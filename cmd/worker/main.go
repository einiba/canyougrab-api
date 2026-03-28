// Go replacement for the Python RQ workers.
// Reads job keys directly from a Valkey list via BLPOP,
// processes domains (cache → bloom → DNS → WHOIS), writes results back.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/zeebo/xxh3"
)

// ── Config ────────────────────────────────────────────────────────────────

const (
	numHashes       = 7
	blpopTimeout    = 5 * time.Second
	dnsTimeout      = 5 * time.Second
	whoisTimeout    = 8 * time.Second
	maxConcurrency  = 25
	maxRetries      = 2
	jobTTL          = 600 // seconds
)

var (
	queueName   = getenv("VALKEY_QUEUE_NAME", "queue:rdap:prod")
	dnsHost     = getenv("DNS_RESOLVER_HOSTNAME", "unbound.canyougrab.svc.cluster.local")
	dnsPort     = getenv("DNS_RESOLVER_PORT", "53")
	whoisHost   = getenv("WHOIS_HOSTNAME", "rust-whois-rdap.canyougrab.svc.cluster.local")
	whoisPort   = getenv("WHOIS_PORT", "3000")
	concurrency = envInt("BATCH_CONCURRENCY", maxConcurrency)
)

func buildValkeyURL() string {
	host := getenv("VALKEY_HOST", "localhost")
	port := getenv("VALKEY_PORT", "25061")
	user := getenv("VALKEY_USERNAME", "default")
	pw   := os.Getenv("VALKEY_PASSWORD")
	return fmt.Sprintf("rediss://%s:%s@%s:%s", user, pw, host, port)
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	var n int
	fmt.Sscan(v, &n)
	if n <= 0 {
		return fallback
	}
	return n
}

// ── Valkey client ─────────────────────────────────────────────────────────

func newValkeyClient() *redis.Client {
	opts, err := redis.ParseURL(buildValkeyURL())
	if err != nil {
		log.Fatalf("invalid valkey URL: %v", err)
	}
	return redis.NewClient(opts)
}

// ── Bloom filter check ────────────────────────────────────────────────────

func bloomPositions(domain string) [numHashes]uint64 {
	sum := xxh3.Hash128([]byte(domain))
	h1, h2 := sum.Lo, sum.Hi
	var pos [numHashes]uint64
	for i := uint64(0); i < numHashes; i++ {
		pos[i] = h1 + i*h2
	}
	return pos
}

// checkBloom returns true if the domain is definitely in the bloom filter.
// Returns false if not found, or if the filter key doesn't exist (TLD not indexed).
func checkBloom(ctx context.Context, rdb *redis.Client, domain, tld string) bool {
	sld := strings.TrimSuffix(domain, "."+tld)
	key := "zone:bloom:" + tld
	positions := bloomPositions(sld)

	// Check filter size from meta key first
	metaKey := "zone:meta:" + tld
	filterSize, err := rdb.HGet(ctx, metaKey, "filter_size").Int64()
	if err != nil || filterSize == 0 {
		return false // TLD not indexed
	}

	pipe := rdb.Pipeline()
	cmds := make([]*redis.IntCmd, numHashes)
	for i, p := range positions {
		bit := p % uint64(filterSize)
		cmds[i] = pipe.GetBit(ctx, key, int64(bit))
	}
	if _, err := pipe.Exec(ctx); err != nil {
		return false
	}
	for _, cmd := range cmds {
		if cmd.Val() == 0 {
			return false
		}
	}
	return true
}

// ── Domain cache ──────────────────────────────────────────────────────────

type CachedResult struct {
	Available  string `json:"available"` // "true"/"false"/"null"
	Confidence string `json:"confidence"`
	Source     string `json:"original_source"`
	TLD        string `json:"tld"`
	CachedAt   string `json:"cached_at"`
}

func checkCache(ctx context.Context, rdb *redis.Client, domain string) map[string]interface{} {
	data, err := rdb.HGetAll(ctx, "dom:"+domain).Result()
	if err != nil || len(data) == 0 {
		return nil
	}
	avail := data["available"]
	var available interface{}
	switch avail {
	case "true":
		available = true
	case "false":
		available = false
	default:
		available = nil
	}

	result := map[string]interface{}{
		"domain":            domain,
		"available":         available,
		"confidence":        data["confidence"],
		"tld":               data["tld"],
		"source":            "cache",
		"checked_at":        data["cached_at"],
		"cache_age_seconds": cacheAge(data["cached_at"]),
		"registration":      nil,
	}
	if e, ok := data["error"]; ok && e != "" {
		result["error"] = e
	}
	if ns, ok := data["nameservers"]; ok && ns != "" {
		var nsArr []string
		if err := json.Unmarshal([]byte(ns), &nsArr); err == nil {
			result["nameservers"] = nsArr
		}
	}
	return result
}

func cacheAge(cachedAt string) int {
	if cachedAt == "" {
		return 0
	}
	t, err := time.Parse(time.RFC3339Nano, cachedAt)
	if err != nil {
		return 0
	}
	return int(time.Since(t).Seconds())
}

func writeCacheResult(ctx context.Context, rdb *redis.Client, domain string, result map[string]interface{}) {
	confidence, _ := result["confidence"].(string)
	if confidence != "high" {
		return
	}
	available := result["available"]

	var availStr string
	switch v := available.(type) {
	case bool:
		if v {
			availStr = "true"
		} else {
			availStr = "false"
		}
	default:
		availStr = "null"
	}

	checkedAt, _ := result["checked_at"].(string)
	if checkedAt == "" {
		checkedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	tld, _ := result["tld"].(string)
	source, _ := result["source"].(string)

	mapping := map[string]interface{}{
		"available":       availStr,
		"cached_at":       checkedAt,
		"confidence":      confidence,
		"original_source": source,
		"tld":             tld,
	}

	// Store nameservers if present
	if ns, ok := result["nameservers"]; ok {
		if nsJSON, err := json.Marshal(ns); err == nil {
			mapping["nameservers"] = string(nsJSON)
		}
	}

	// Determine TTL
	var ttl time.Duration
	if available == true {
		ttl = 3 * 24 * time.Hour // available domains: 3 days
	} else {
		ttl = 6 * time.Hour // registered: 6h default
	}

	pipe := rdb.Pipeline()
	pipe.HSet(ctx, "dom:"+domain, mapping)
	pipe.Expire(ctx, "dom:"+domain, ttl)
	pipe.Exec(ctx) //nolint
}

// ── DNS lookup ────────────────────────────────────────────────────────────

var dnsResolver = &net.Resolver{
	PreferGo: true,
	Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
		d := net.Dialer{Timeout: dnsTimeout}
		return d.DialContext(ctx, "udp", net.JoinHostPort(dnsHost, dnsPort))
	},
}

func checkDNS(domain, tld string) map[string]interface{} {
	ctx, cancel := context.WithTimeout(context.Background(), dnsTimeout)
	defer cancel()

	nss, err := dnsResolver.LookupNS(ctx, domain)
	now := time.Now().UTC().Format(time.RFC3339Nano)

	if err == nil {
		nsNames := make([]string, 0, len(nss))
		for _, ns := range nss {
			nsNames = append(nsNames, strings.TrimRight(ns.Host, "."))
		}
		return map[string]interface{}{
			"domain": domain, "available": false, "tld": tld,
			"confidence": "high", "source": "dns",
			"checked_at": now, "cache_age_seconds": 0,
			"dns_status": "noerror_ns", "registration": nil,
			"nameservers": nsNames,
		}
	}

	dnsErr, _ := err.(*net.DNSError)
	if dnsErr != nil && dnsErr.IsNotFound {
		return map[string]interface{}{
			"domain": domain, "available": true, "tld": tld,
			"confidence": "medium", "source": "dns",
			"checked_at": now, "cache_age_seconds": 0,
			"dns_status": "nxdomain", "registration": nil,
		}
	}
	if dnsErr != nil && dnsErr.IsTimeout {
		return map[string]interface{}{
			"domain": domain, "available": nil, "tld": tld,
			"confidence": "low", "source": "dns",
			"checked_at": now, "cache_age_seconds": 0,
			"error": "dns_timeout", "dns_status": "timeout",
		}
	}

	// NoAnswer / other — treat as registered (conservative)
	return map[string]interface{}{
		"domain": domain, "available": false, "tld": tld,
		"confidence": "high", "source": "dns",
		"checked_at": now, "cache_age_seconds": 0,
		"dns_status": "noanswer", "registration": nil,
	}
}

// ── WHOIS lookup ──────────────────────────────────────────────────────────

var whoisHTTP = &http.Client{Timeout: whoisTimeout}

type whoisResponse struct {
	ParsedData  map[string]interface{} `json:"parsed_data"`
	WhoisServer string                 `json:"whois_server"`
	LookupSource string                `json:"lookup_source"`
	QueryTimeMs  int                    `json:"query_time_ms"`
}

func checkWHOIS(domain string) map[string]interface{} {
	url := fmt.Sprintf("http://%s:%s/whois/%s", whoisHost, whoisPort, domain)
	resp, err := whoisHTTP.Get(url)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil
	}

	if resp.StatusCode == 404 {
		return map[string]interface{}{"lookup_source": "rdap_domain_not_found"}
	}
	if resp.StatusCode == 429 {
		return map[string]interface{}{"lookup_source": "rdap_rate_limited"}
	}
	if resp.StatusCode != 200 {
		return nil
	}

	var wr whoisResponse
	if err := json.Unmarshal(body, &wr); err != nil {
		return nil
	}
	parsed := wr.ParsedData
	if parsed == nil {
		parsed = map[string]interface{}{}
	}
	return map[string]interface{}{
		"registrar":       parsed["registrar"],
		"creation_date":   parsed["creation_date"],
		"expiration_date": parsed["expiration_date"],
		"updated_date":    parsed["updated_date"],
		"name_servers":    parsed["name_servers"],
		"status":          parsed["status"],
		"whois_server":    wr.WhoisServer,
		"lookup_source":   wr.LookupSource,
		"query_time_ms":   wr.QueryTimeMs,
	}
}

// ── TOS coverage check ───────────────────────────────────────────────────

const tosCoveredKey = "tos:covered_tlds"
const brandTLDsKey = "tos:brand_tlds"

// isTLDCovered returns true if the TLD's registry operator is listed in
// our Terms of Service.  Only covered TLDs may receive RDAP/WHOIS queries;
// uncovered TLDs get DNS-only results.
func isTLDCovered(ctx context.Context, rdb *redis.Client, tld string) bool {
	ok, err := rdb.SIsMember(ctx, tosCoveredKey, tld).Result()
	if err != nil {
		log.Printf("TOS coverage check error for .%s: %v (failing open)", tld, err)
		return true
	}
	if !ok {
		log.Printf("TOS gate: .%s not covered — skipping RDAP/WHOIS", tld)
	}
	return ok
}

// isBrandTLD returns true if the TLD is a brand/closed TLD that does not
// allow public domain registrations (e.g., .nike, .apple, .google).
func isBrandTLD(ctx context.Context, rdb *redis.Client, tld string) bool {
	ok, err := rdb.SIsMember(ctx, brandTLDsKey, tld).Result()
	if err != nil {
		return false // fail open — don't block lookups on Valkey error
	}
	return ok
}

// ── Domain pipeline ───────────────────────────────────────────────────────

func checkDomain(ctx context.Context, rdb *redis.Client, domain string) map[string]interface{} {
	domain = strings.ToLower(strings.TrimRight(strings.TrimSpace(domain), "."))
	parts := strings.Split(domain, ".")
	if len(parts) < 2 {
		return map[string]interface{}{
			"domain": domain, "available": nil, "confidence": "low",
			"checked_at": time.Now().UTC().Format(time.RFC3339Nano),
			"error": "invalid domain",
		}
	}
	tld := parts[len(parts)-1]
	now := time.Now().UTC().Format(time.RFC3339Nano)

	// 0. Brand TLD gate — reject domains on closed/brand TLDs immediately
	if isBrandTLD(ctx, rdb, tld) {
		return map[string]interface{}{
			"domain": domain, "available": nil, "tld": tld,
			"confidence": "high", "source": "registry",
			"checked_at": now, "cache_age_seconds": 0,
			"error": "brand_tld", "registration": nil,
		}
	}

	// 1. Cache
	if cached := checkCache(ctx, rdb, domain); cached != nil {
		return cached
	}

	// 2. Bloom filter — fast-path for registered domains
	if checkBloom(ctx, rdb, domain, tld) {
		result := map[string]interface{}{
			"domain": domain, "available": false, "tld": tld,
			"confidence": "high", "source": "bloom",
			"checked_at": now, "cache_age_seconds": 0, "registration": nil,
		}
		// Quick NS lookup for enrichment — Unbound has it cached from zone file
		nsCtx, nsCancel := context.WithTimeout(context.Background(), 2*time.Second)
		if nss, err := dnsResolver.LookupNS(nsCtx, domain); err == nil {
			nsNames := make([]string, 0, len(nss))
			for _, ns := range nss {
				nsNames = append(nsNames, strings.TrimRight(ns.Host, "."))
			}
			result["nameservers"] = nsNames
		}
		nsCancel()
		writeCacheResult(ctx, rdb, domain, result)
		return result
	}

	// 3. DNS
	dnsResult := checkDNS(domain, tld)
	available, _ := dnsResult["available"]

	// Domain is registered — no need for WHOIS
	if available == false {
		writeCacheResult(ctx, rdb, domain, dnsResult)
		return dnsResult
	}

	// DNS error — return as-is (low confidence, don't cache)
	if available == nil {
		return dnsResult
	}

	// 4. TOS coverage gate — only query RDAP/WHOIS for operators listed in our TOS
	if !isTLDCovered(ctx, rdb, tld) {
		// Return DNS result as-is (medium confidence, no WHOIS verification)
		return dnsResult
	}

	// 5. WHOIS — DNS says NXDOMAIN, verify with WHOIS/RDAP
	whoisData := checkWHOIS(domain)
	if whoisData == nil {
		// WHOIS unavailable — return DNS result with medium confidence
		return dnsResult
	}

	lookupSource, _ := whoisData["lookup_source"].(string)

	if lookupSource == "rdap_domain_not_found" {
		result := map[string]interface{}{
			"domain": domain, "available": true, "tld": tld,
			"confidence": "high", "source": "rdap",
			"checked_at": now, "cache_age_seconds": 0, "registration": nil,
		}
		writeCacheResult(ctx, rdb, domain, result)
		return result
	}

	if lookupSource == "rdap_rate_limited" {
		return dnsResult // fall back to medium-confidence DNS result
	}

	// Check expiration_date — only treat as registered if we have one.
	// WHOIS returns HTTP 200 even for "no data found" responses; expiration_date
	// is the reliable signal that a domain is actually registered (same logic as Python lookup.py).
	if whoisData["expiration_date"] == nil {
		result := map[string]interface{}{
			"domain": domain, "available": true, "tld": tld,
			"confidence": "high", "source": "whois",
			"checked_at": now, "cache_age_seconds": 0, "registration": nil,
		}
		writeCacheResult(ctx, rdb, domain, result)
		return result
	}

	// WHOIS found registration data
	reg := map[string]interface{}{}
	if v := whoisData["creation_date"]; v != nil {
		reg["created_at"] = v
	}
	if v := whoisData["expiration_date"]; v != nil {
		reg["expires_at"] = v
	}
	if v := whoisData["updated_date"]; v != nil {
		reg["updated_at"] = v
	}
	if v := whoisData["registrar"]; v != nil {
		reg["registrar"] = v
	}
	if v := whoisData["name_servers"]; v != nil {
		reg["name_servers"] = v
	}

	result := map[string]interface{}{
		"domain": domain, "available": false, "tld": tld,
		"confidence": "high", "source": "whois",
		"checked_at": now, "cache_age_seconds": 0,
		"registration": reg,
	}
	writeCacheResult(ctx, rdb, domain, result)
	return result
}

// ── Sub-job merge (Lua) ──────────────────────────────────────────────────

// completeSubJobLua atomically marks a sub-job completed and checks whether
// all sibling sub-jobs are also done.  Returns 1 when the caller should
// merge results into the parent job, 0 otherwise.
var completeSubJobLua = redis.NewScript(`
-- KEYS[1] = sub-job key
-- KEYS[2] = parent job key
-- ARGV[1] = results JSON
-- ARGV[2] = completed_at
-- ARGV[3] = queued_at   (may be empty)
-- ARGV[4] = JOB_TTL

-- Mark sub-job completed
redis.call('HSET', KEYS[1], 'status', 'completed',
           'results', ARGV[1], 'completed_at', ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))

if ARGV[3] ~= '' then
    redis.call('HSET', KEYS[1], 'queued_at', ARGV[3])
end

-- Check if all sub-jobs are done
local sub_jobs_json = redis.call('HGET', KEYS[2], 'sub_jobs')
if not sub_jobs_json then return 0 end

local sub_jobs = cjson.decode(sub_jobs_json)
for _, sj_key in ipairs(sub_jobs) do
    local sj_status = redis.call('HGET', sj_key, 'status')
    if sj_status ~= 'completed' then
        return 0
    end
end

return 1
`)

// isSubJob returns true when the key looks like "job:rdap:..." or "job:whois:...".
func isSubJob(jobKey string) bool {
	return strings.HasPrefix(jobKey, "job:rdap:") || strings.HasPrefix(jobKey, "job:whois:")
}

// mergeSubJobs reads all sibling sub-jobs and writes the combined result
// array (in original order) into the parent job hash.
func mergeSubJobs(ctx context.Context, rdb *redis.Client, parentKey, createdAt string) {
	subJobsJSON, err := rdb.HGet(ctx, parentKey, "sub_jobs").Result()
	if err != nil {
		log.Printf("merge: no sub_jobs on %s: %v", parentKey, err)
		return
	}
	var subJobKeys []string
	if err := json.Unmarshal([]byte(subJobsJSON), &subJobKeys); err != nil {
		log.Printf("merge: bad sub_jobs JSON on %s: %v", parentKey, err)
		return
	}

	domainCountStr, _ := rdb.HGet(ctx, parentKey, "domain_count").Result()
	var domainCount int
	fmt.Sscan(domainCountStr, &domainCount)
	if domainCount == 0 {
		domainCount = 100 // safety fallback
	}

	merged := make([]interface{}, domainCount)
	hasPartialError := false

	for _, sjKey := range subJobKeys {
		sjData, err := rdb.HGetAll(ctx, sjKey).Result()
		if err != nil || sjData["status"] != "completed" {
			hasPartialError = true
			continue
		}
		var sjResults []json.RawMessage
		if err := json.Unmarshal([]byte(sjData["results"]), &sjResults); err != nil {
			hasPartialError = true
			continue
		}
		var sjIndices []int
		if err := json.Unmarshal([]byte(sjData["indices"]), &sjIndices); err != nil {
			hasPartialError = true
			continue
		}
		for i, idx := range sjIndices {
			if idx >= 0 && idx < domainCount && i < len(sjResults) {
				merged[idx] = sjResults[i]
			}
		}
	}

	// Fill gaps with error placeholders
	for i := range merged {
		if merged[i] == nil {
			merged[i] = map[string]interface{}{
				"domain": "unknown", "available": nil,
				"confidence": "low", "error": "sub-job failed or timed out",
				"source": "error",
			}
			hasPartialError = true
		}
	}

	now := time.Now().UTC()
	nowISO := now.Format(time.RFC3339Nano)
	mergedJSON, _ := json.Marshal(merged)

	mapping := map[string]interface{}{
		"status":       "completed",
		"results":      string(mergedJSON),
		"completed_at": nowISO,
	}
	if hasPartialError {
		mapping["partial"] = "true"
	}
	if createdAt != "" {
		if t, err := time.Parse(time.RFC3339Nano, createdAt); err == nil {
			mapping["response_time_ms"] = int(now.Sub(t).Milliseconds())
		}
	}

	pipe := rdb.Pipeline()
	pipe.HSet(ctx, parentKey, mapping)
	pipe.Expire(ctx, parentKey, jobTTL*time.Second)
	pipe.Exec(ctx) //nolint

	log.Printf("merged %d results into parent %s (partial=%v)", domainCount, parentKey, hasPartialError)
}

// ── Job processing ────────────────────────────────────────────────────────

func processJob(ctx context.Context, rdb *redis.Client, jobKey string) error {
	// Claim
	pipe := rdb.Pipeline()
	pipe.HSet(ctx, jobKey, "status", "processing")
	domainsCmd := pipe.HGet(ctx, jobKey, "domains")
	createdAtCmd := pipe.HGet(ctx, jobKey, "created_at")
	parentJobCmd := pipe.HGet(ctx, jobKey, "parent_job")
	if _, err := pipe.Exec(ctx); err != nil && err != redis.Nil {
		return fmt.Errorf("claim %s: %w", jobKey, err)
	}

	domainsJSON, err := domainsCmd.Result()
	if err != nil {
		return fmt.Errorf("no domains for %s: %w", jobKey, err)
	}
	createdAt, _ := createdAtCmd.Result()
	parentKey, _ := parentJobCmd.Result()

	var domains []string
	if err := json.Unmarshal([]byte(domainsJSON), &domains); err != nil {
		return fmt.Errorf("parse domains: %w", err)
	}

	log.Printf("processing %s (%d domains)", jobKey, len(domains))

	// Process concurrently
	results := make([]map[string]interface{}, len(domains))
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup
	for i, domain := range domains {
		wg.Add(1)
		sem <- struct{}{}
		go func(idx int, d string) {
			defer wg.Done()
			defer func() { <-sem }()
			results[idx] = checkDomain(ctx, rdb, d)
		}(i, domain)
	}
	wg.Wait()

	// Complete
	now := time.Now().UTC()
	nowISO := now.Format(time.RFC3339Nano)
	resultsJSON, _ := json.Marshal(results)

	// Sub-job: use Lua script for atomic completion + sibling check
	if isSubJob(jobKey) && parentKey != "" {
		allDone, err := completeSubJobLua.Run(ctx, rdb,
			[]string{jobKey, parentKey},
			string(resultsJSON), nowISO, createdAt, fmt.Sprintf("%d", jobTTL),
		).Int()
		if err != nil {
			log.Printf("sub-job Lua error for %s: %v — falling back to direct write", jobKey, err)
			// Fallback: write directly (merge won't trigger but at least sub-job is marked done)
			rdb.HSet(ctx, jobKey, map[string]interface{}{
				"status": "completed", "results": string(resultsJSON), "completed_at": nowISO,
			})
			rdb.Expire(ctx, jobKey, jobTTL*time.Second)
		} else if allDone == 1 {
			mergeSubJobs(ctx, rdb, parentKey, createdAt)
		}
		log.Printf("completed sub-job %s (%d results, all_done=%v)", jobKey, len(results), allDone == 1)
		return nil
	}

	// Regular job (not a sub-job): write results directly
	mapping := map[string]interface{}{
		"status":     "completed",
		"results":    string(resultsJSON),
		"completed_at": nowISO,
	}
	if createdAt != "" {
		mapping["queued_at"] = createdAt
		if t, err := time.Parse(time.RFC3339Nano, createdAt); err == nil {
			mapping["response_time_ms"] = int(now.Sub(t).Milliseconds())
		}
	}

	rdb.HSet(ctx, jobKey, mapping)
	rdb.Expire(ctx, jobKey, jobTTL*time.Second)

	log.Printf("completed %s (%d results)", jobKey, len(results))
	return nil
}

func failJob(ctx context.Context, rdb *redis.Client, jobKey, errMsg string) {
	rdb.HSet(ctx, jobKey, map[string]interface{}{
		"status": "failed",
		"error":  errMsg,
	})
	rdb.Expire(ctx, jobKey, jobTTL*time.Second)
}

// ── Main loop ─────────────────────────────────────────────────────────────

func main() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.Printf("worker starting queue=%s concurrency=%d", queueName, concurrency)

	rdb := newValkeyClient()
	ctx := context.Background()

	// Connectivity checks
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("valkey ping failed: %v", err)
	}
	log.Printf("valkey connected")

	if _, err := dnsResolver.LookupNS(context.Background(), "google.com"); err != nil {
		log.Printf("warning: DNS resolver check failed: %v", err)
	} else {
		log.Printf("DNS resolver connected (%s:%s)", dnsHost, dnsPort)
	}

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGTERM, syscall.SIGINT)
	running := true

	go func() {
		<-quit
		log.Printf("shutting down...")
		running = false
	}()

	log.Printf("listening on %s", queueName)

	for running {
		res, err := rdb.BLPop(ctx, blpopTimeout, queueName).Result()
		if err == redis.Nil {
			continue // timeout, loop again
		}
		if err != nil {
			if running {
				log.Printf("blpop error: %v", err)
				time.Sleep(time.Second)
			}
			continue
		}

		jobKey := res[1] // [list_name, value]

		if err := processJob(ctx, rdb, jobKey); err != nil {
			log.Printf("job %s error: %v", jobKey, err)
			failJob(ctx, rdb, jobKey, err.Error())
		}
	}

	log.Printf("worker stopped")
}
