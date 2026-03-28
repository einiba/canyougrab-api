// Weekly parking IP scanner — resolves A records for zone file domains and
// flags those pointing to known parking service IPs.
//
// Reuses CZDS auth + zone file download from the bloom builder.
// Resolves A records via Unbound (cluster-local DNS resolver).
// Writes matches to Valkey dom:{domain} hashes for enrichment.
//
// Usage:
//   CZDS_USERNAME=... CZDS_PASSWORD=... /app/parking-scanner [tld ...]
//   CZDS_USERNAME=... CZDS_PASSWORD=... /app/parking-scanner com net org
package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	resolverConcurrency = 500  // reduced from 5000 — nodes are memory-constrained
	resolverTimeout     = 2 * time.Second
	valkeyTTL           = 8 * 24 * time.Hour // 8 days (weekly refresh + buffer)
	pipelineBatchSize   = 500
)

var (
	dnsHost = getenv("DNS_RESOLVER_HOSTNAME", "unbound.canyougrab.svc.cluster.local")
	dnsPort = getenv("DNS_RESOLVER_PORT", "53")
)

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// ── Parking IP CIDRs (from MISP + TMA22) ────────────────────────────────

type parkingCIDR struct {
	network *net.IPNet
	service string
}

// Known parking service IP ranges with service attribution.
// Source: https://github.com/MISP/misp-warninglists/blob/main/lists/parking-domain/list.json
// + TMA22 parking_services.json for service names.
var parkingCIDRs []parkingCIDR

func init() {
	type entry struct {
		cidr    string
		service string
	}
	// Major ranges with known service attribution
	entries := []entry{
		// Bodis
		{"199.59.240.0/22", "Bodis"},
		{"199.59.243.160/27", "Bodis"},
		{"199.59.243.192/27", "Bodis"},
		{"199.59.243.224/29", "Bodis"},
		// ParkingCrew
		{"185.53.176.0/22", "ParkingCrew"},
		// Sedo
		{"91.195.240.0/23", "Sedo"},
		{"91.195.240.80/28", "Sedo"},
		{"64.190.62.0/23", "Sedo"},
		// Above.com / Trellian
		{"204.11.56.0/23", "Above.com"},
		{"66.81.199.0/24", "Above.com"},
		// GoDaddy free parking / CashParking
		{"34.102.136.180/32", "GoDaddy"},
		{"34.98.99.30/32", "GoDaddy"},
		{"35.186.238.101/32", "GoDaddy CashParking"},
		{"3.33.130.190/32", "GoDaddy"},
		{"15.197.148.33/32", "GoDaddy"},
		// DomainSponsor / Oversee
		{"208.91.196.0/23", "DomainSponsor"},
		{"208.91.196.46/32", "DomainSponsor"},
		{"208.91.197.46/32", "DomainSponsor"},
		{"208.91.197.91/32", "DomainSponsor"},
		// Dan.com
		{"52.58.78.16/32", "Dan.com"},
		{"3.64.163.50/32", "Dan.com"},
		// Afternic/NameFind
		{"209.99.64.0/24", "Afternic"},
		{"209.99.40.222/32", "Afternic"},
		// HugeDomains
		{"75.2.115.196/32", "HugeDomains"},
		{"75.2.18.233/32", "HugeDomains"},
		{"75.2.26.18/32", "HugeDomains"},
		{"75.2.37.224/32", "HugeDomains"},
		{"76.223.65.111/32", "HugeDomains"},
		{"99.83.154.118/32", "HugeDomains"},
		// Remaining MISP entries (service unknown — generic "parking")
		{"103.120.80.111/32", "parking"},
		{"103.139.0.32/32", "parking"},
		{"103.224.182.0/23", "parking"},
		{"103.224.212.0/23", "parking"},
		{"104.26.6.37/32", "parking"},
		{"104.26.7.37/32", "parking"},
		{"119.28.128.52/32", "parking"},
		{"121.254.178.252/32", "parking"},
		{"13.225.34.0/24", "parking"},
		{"13.227.219.0/24", "parking"},
		{"13.248.216.40/32", "parking"},
		{"135.148.9.101/32", "parking"},
		{"141.8.224.195/32", "parking"},
		{"158.247.7.206/32", "parking"},
		{"158.69.201.47/32", "parking"},
		{"159.89.244.183/32", "parking"},
		{"164.90.244.158/32", "parking"},
		{"172.67.70.191/32", "parking"},
		{"18.164.52.0/24", "parking"},
		{"185.134.245.113/32", "parking"},
		{"188.93.95.11/32", "parking"},
		{"192.185.0.218/32", "parking"},
		{"192.64.147.0/24", "parking"},
		{"194.58.112.165/32", "parking"},
		{"194.58.112.174/32", "parking"},
		{"198.54.117.192/26", "parking"},
		{"199.191.50.0/24", "parking"},
		{"199.58.179.10/32", "parking"},
		{"2.57.90.16/32", "parking"},
		{"207.148.248.143/32", "parking"},
		{"207.148.248.145/32", "parking"},
		{"213.145.228.16/32", "parking"},
		{"213.171.195.105/32", "parking"},
		{"216.40.34.41/32", "parking"},
		{"217.160.141.142/32", "parking"},
		{"217.160.95.94/32", "parking"},
		{"217.26.48.101/32", "parking"},
		{"217.70.184.38/32", "parking"},
		{"217.70.184.50/32", "parking"},
		{"3.139.159.151/32", "parking"},
		{"3.234.55.179/32", "parking"},
		{"31.186.11.254/32", "parking"},
		{"31.31.205.163/32", "parking"},
		{"34.102.221.37/32", "parking"},
		{"35.227.197.36/32", "parking"},
		{"37.97.254.27/32", "parking"},
		{"43.128.56.249/32", "parking"},
		{"45.79.222.138/32", "parking"},
		{"45.88.202.115/32", "parking"},
		{"46.28.105.2/32", "parking"},
		{"46.30.211.38/32", "parking"},
		{"46.4.13.97/32", "parking"},
		{"46.8.8.100/32", "parking"},
		{"47.91.170.222/32", "parking"},
		{"5.9.161.60/32", "parking"},
		{"50.28.32.8/32", "parking"},
		{"52.128.23.153/32", "parking"},
		{"52.222.139.0/24", "parking"},
		{"52.222.149.0/24", "parking"},
		{"52.222.158.0/24", "parking"},
		{"52.222.174.0/24", "parking"},
		{"52.60.87.163/32", "parking"},
		{"52.84.174.0/24", "parking"},
		{"62.149.128.40/32", "parking"},
		{"64.70.19.203/32", "parking"},
		{"64.70.19.98/32", "parking"},
		{"74.220.199.14/32", "parking"},
		{"74.220.199.15/32", "parking"},
		{"74.220.199.6/32", "parking"},
		{"74.220.199.8/32", "parking"},
		{"74.220.199.9/32", "parking"},
		{"78.47.145.38/32", "parking"},
		{"81.2.194.128/32", "parking"},
		{"88.198.29.97/32", "parking"},
		{"91.184.0.100/32", "parking"},
		{"93.191.168.52/32", "parking"},
		{"94.136.40.51/32", "parking"},
		{"95.217.58.108/32", "parking"},
		{"98.124.204.16/32", "parking"},
	}

	for _, e := range entries {
		_, network, err := net.ParseCIDR(e.cidr)
		if err != nil {
			log.Fatalf("bad CIDR %q: %v", e.cidr, err)
		}
		parkingCIDRs = append(parkingCIDRs, parkingCIDR{network: network, service: e.service})
	}
	log.Printf("Loaded %d parking CIDRs", len(parkingCIDRs))
}

func matchParkingIP(ip net.IP) (string, bool) {
	for _, p := range parkingCIDRs {
		if p.network.Contains(ip) {
			return p.service, true
		}
	}
	return "", false
}

// ── DNS resolver (via Unbound) ───────────────────────────────────────────

var resolver = &net.Resolver{
	PreferGo: true,
	Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
		d := net.Dialer{Timeout: resolverTimeout}
		return d.DialContext(ctx, "udp", net.JoinHostPort(dnsHost, dnsPort))
	},
}

func lookupA(domain string) []net.IP {
	ctx, cancel := context.WithTimeout(context.Background(), resolverTimeout)
	defer cancel()
	addrs, err := resolver.LookupIPAddr(ctx, domain)
	if err != nil {
		return nil
	}
	ips := make([]net.IP, 0, len(addrs))
	for _, a := range addrs {
		if a.IP.To4() != nil { // IPv4 only
			ips = append(ips, a.IP)
		}
	}
	return ips
}

// ── CZDS auth + download (same as bloom-builder) ─────────────────────────

func czdsAuthenticate(username, password string) (string, error) {
	payload, _ := json.Marshal(map[string]string{
		"username": username,
		"password": password,
	})
	resp, err := http.Post(
		"https://account-api.icann.org/api/authenticate",
		"application/json",
		bytes.NewReader(payload),
	)
	if err != nil {
		return "", fmt.Errorf("czds auth: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("czds auth: HTTP %d", resp.StatusCode)
	}
	var result struct {
		AccessToken string `json:"accessToken"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("czds auth decode: %w", err)
	}
	return result.AccessToken, nil
}

func downloadZoneFile(tld, token, destPath string) error {
	zoneURL := fmt.Sprintf("https://czds-download-api.icann.org/czds/downloads/%s.zone", tld)
	req, _ := http.NewRequest("GET", zoneURL, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	client := &http.Client{Timeout: 30 * time.Minute}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

// ── Zone file streaming ──────────────────────────────────────────────────

// streamDomains sends unique FQDNs from a gzipped zone file to a channel.
// Uses a bloom-like dedup (first-seen map limited to 1M entries with eviction)
// to avoid loading all 150M domains into memory.
func streamDomains(zonePath, tld string, out chan<- string) error {
	f, err := os.Open(zonePath)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()

	suffix := "." + tld + "."
	var lastSLD string // simple dedup: zone files group records by domain
	count := 0

	scanner := bufio.NewScanner(gz)
	scanner.Buffer(make([]byte, 4*1024*1024), 4*1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 || line[0] == ';' || line[0] == '$' || line[0] == ' ' || line[0] == '\t' {
			continue
		}
		spaceIdx := bytes.IndexAny(line, " \t")
		if spaceIdx <= 0 {
			continue
		}
		domain := string(line[:spaceIdx])
		if !strings.HasSuffix(domain, suffix) {
			continue
		}
		sld := domain[:len(domain)-len(suffix)]
		if strings.ContainsRune(sld, '.') {
			continue
		}
		sld = strings.ToLower(sld)

		// Zone files are sorted — consecutive lines for the same domain.
		// Skip duplicates by comparing to the last SLD we emitted.
		if sld == lastSLD {
			continue
		}
		lastSLD = sld
		count++
		out <- sld + "." + tld
	}

	log.Printf(".%s: streamed %d unique domains", tld, count)
	return scanner.Err()
}

// ── Valkey client ────────────────────────────────────────────────────────

func newValkeyClient() *redis.Client {
	valkeyURL := os.Getenv("VALKEY_URL")
	if valkeyURL == "" {
		host := os.Getenv("VALKEY_HOST")
		port := getenv("VALKEY_PORT", "25061")
		user := getenv("VALKEY_USERNAME", "default")
		pass := os.Getenv("VALKEY_PASSWORD")
		valkeyURL = fmt.Sprintf("rediss://%s:%s@%s:%s", url.QueryEscape(user), url.QueryEscape(pass), host, port)
	}
	opts, err := redis.ParseURL(valkeyURL)
	if err != nil {
		log.Fatalf("invalid valkey URL: %v", err)
	}
	return redis.NewClient(opts)
}

// ── Main scan logic ──────────────────────────────────────────────────────

func scanTLD(tld, zonePath string, rdb *redis.Client) (int64, int64, time.Duration) {
	start := time.Now()
	var matched, total int64
	sem := make(chan struct{}, resolverConcurrency)
	var wg sync.WaitGroup

	ctx := context.Background()
	pipe := rdb.Pipeline()
	var pipeMu sync.Mutex
	pipeCount := 0

	flushPipe := func() {
		if pipeCount > 0 {
			pipe.Exec(ctx)
			pipe = rdb.Pipeline()
			pipeCount = 0
		}
	}

	domainCh := make(chan string, 10000)
	go func() {
		if err := streamDomains(zonePath, tld, domainCh); err != nil {
			log.Printf(".%s stream error: %v", tld, err)
		}
		close(domainCh)
	}()

	for domain := range domainCh {
		atomic.AddInt64(&total, 1)
		wg.Add(1)
		sem <- struct{}{}
		go func(d string) {
			defer wg.Done()
			defer func() { <-sem }()

			ips := lookupA(d)
			for _, ip := range ips {
				service, ok := matchParkingIP(ip)
				if ok {
					atomic.AddInt64(&matched, 1)
					pipeMu.Lock()
					pipe.HSet(ctx, "dom:"+d, "parked_by_ip", "true", "parking_ip_service", service)
					pipe.Expire(ctx, "dom:"+d, valkeyTTL)
					pipeCount++
					if pipeCount >= pipelineBatchSize {
						flushPipe()
					}
					pipeMu.Unlock()
					break
				}
			}
		}(domain)
	}

	wg.Wait()
	pipeMu.Lock()
	flushPipe()
	pipeMu.Unlock()

	return matched, total, time.Since(start)
}

// ── Discovery mode — find unknown parking IPs ────────────────────────────

type ipCluster struct {
	count   int
	samples []string
}

const (
	discoveryMinDomains = 100 // only report IPs hosting 100+ domains
	discoverySamples    = 5   // sample domains per IP
)

// ipTo24 extracts the /24 prefix from an IPv4 address: "1.2.3.4" → "1.2.3.0/24"
func ipTo24(ip net.IP) string {
	v4 := ip.To4()
	if v4 == nil {
		return ""
	}
	return fmt.Sprintf("%d.%d.%d.0/24", v4[0], v4[1], v4[2])
}

// discoverTLD resolves A records for all domains (streamed) and clusters by /24 CIDR.
// Aggregating by /24 instead of per-IP keeps memory bounded (~200K unique /24s typical).
func discoverTLD(tld, zonePath string) (map[string]*ipCluster, int64) {
	clusters := make(map[string]*ipCluster)
	var mu sync.Mutex
	var total int64
	sem := make(chan struct{}, resolverConcurrency)
	var wg sync.WaitGroup

	domainCh := make(chan string, 10000)
	go func() {
		if err := streamDomains(zonePath, tld, domainCh); err != nil {
			log.Printf(".%s stream error: %v", tld, err)
		}
		close(domainCh)
	}()

	for domain := range domainCh {
		atomic.AddInt64(&total, 1)
		wg.Add(1)
		sem <- struct{}{}
		go func(d string) {
			defer wg.Done()
			defer func() { <-sem }()

			ips := lookupA(d)
			for _, ip := range ips {
				prefix := ipTo24(ip)
				if prefix == "" {
					continue
				}
				mu.Lock()
				c := clusters[prefix]
				if c == nil {
					c = &ipCluster{}
					clusters[prefix] = c
				}
				c.count++
				if len(c.samples) < discoverySamples {
					c.samples = append(c.samples, d)
				}
				mu.Unlock()
			}
		}(domain)
	}
	wg.Wait()
	return clusters, total
}

type discoveryEntry struct {
	IP      string   `json:"ip"`
	Count   int      `json:"count"`
	Samples []string `json:"samples"`
	Known   string   `json:"known,omitempty"`
	CIDR    string   `json:"cidr,omitempty"`
}

type discoveryReport struct {
	TLD     string           `json:"tld"`
	Unknown []discoveryEntry `json:"unknown"`
	Known   []discoveryEntry `json:"known"`
}

func buildDiscoveryReport(tld string, clusters map[string]*ipCluster) discoveryReport {
	var unknown, known []discoveryEntry

	for prefix, c := range clusters {
		if c.count < discoveryMinDomains {
			continue
		}
		// Check if any IP in this /24 matches a known parking CIDR
		// Use the .1 address as representative
		_, network, _ := net.ParseCIDR(prefix)
		repIP := make(net.IP, 4)
		copy(repIP, network.IP.To4())
		repIP[3] = 1

		service, matched := matchParkingIP(repIP)
		entry := discoveryEntry{
			IP:      prefix,
			Count:   c.count,
			Samples: c.samples,
		}
		if matched {
			entry.Known = service
			entry.CIDR = prefix
			known = append(known, entry)
		} else {
			unknown = append(unknown, entry)
		}
	}

	// Sort by count descending
	sort.Slice(unknown, func(i, j int) bool { return unknown[i].Count > unknown[j].Count })
	sort.Slice(known, func(i, j int) bool { return known[i].Count > known[j].Count })

	return discoveryReport{TLD: tld, Unknown: unknown, Known: known}
}

// ── Supported TLDs ───────────────────────────────────────────────────────

var supportedTLDs = []string{"com", "net", "org", "xyz", "info", "top", "online", "store", "shop"}

func main() {
	log.SetFlags(log.Ldate | log.Ltime)

	// Parse flags
	discoverMode := false
	tlds := supportedTLDs
	var filteredArgs []string
	for _, arg := range os.Args[1:] {
		if arg == "--discover" {
			discoverMode = true
		} else {
			filteredArgs = append(filteredArgs, arg)
		}
	}
	if len(filteredArgs) > 0 {
		tlds = filteredArgs
	}

	if discoverMode {
		log.Printf("parking-scanner starting (DISCOVERY MODE)")
	} else {
		log.Printf("parking-scanner starting")
	}

	czdsUser := os.Getenv("CZDS_USERNAME")
	czdsPass := os.Getenv("CZDS_PASSWORD")
	if czdsUser == "" || czdsPass == "" {
		log.Fatal("CZDS_USERNAME and CZDS_PASSWORD must be set")
	}

	log.Printf("Authenticating to CZDS...")
	token, err := czdsAuthenticate(czdsUser, czdsPass)
	if err != nil {
		log.Fatalf("CZDS auth failed: %v", err)
	}

	var rdb *redis.Client
	if !discoverMode {
		rdb = newValkeyClient()
		if err := rdb.Ping(context.Background()).Err(); err != nil {
			log.Fatalf("Valkey ping failed: %v", err)
		}
		log.Printf("Valkey connected")
	}

	totalMatches := int64(0)
	totalDomains := 0
	var allReports []discoveryReport

	for _, tld := range tlds {
		zonePath := fmt.Sprintf("/tmp/%s.zone.gz", tld)

		log.Printf("Downloading .%s zone file...", tld)
		if err := downloadZoneFile(tld, token, zonePath); err != nil {
			log.Printf("Failed to download .%s: %v (skipping)", tld, err)
			continue
		}

		if discoverMode {
			log.Printf("Discovering IP clusters for .%s (streaming)...", tld)
			clusters, count := discoverTLD(tld, zonePath)
			totalDomains += int(count)
			report := buildDiscoveryReport(tld, clusters)
			allReports = append(allReports, report)
			log.Printf(".%s: %d domains, %d unknown IPs (100+ domains), %d known parking IPs",
				tld, count, len(report.Unknown), len(report.Known))
		} else {
			log.Printf("Scanning .%s for parking IPs (streaming)...", tld)
			matched, count, elapsed := scanTLD(tld, zonePath, rdb)
			totalDomains += int(count)
			totalMatches += matched
			log.Printf(".%s: %d/%d parked (%.1f%%) in %s",
				tld, matched, count,
				float64(matched)/float64(count)*100,
				elapsed.Round(time.Second))
		}

		os.Remove(zonePath)
	}

	if discoverMode {
		// Output JSON report to stdout
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(allReports)
		log.Printf("DONE: discovered IP clusters across %d TLDs (%d total domains)", len(tlds), totalDomains)
	} else {
		log.Printf("DONE: scanned %d domains across %d TLDs, found %d parked by IP",
			totalDomains, len(tlds), totalMatches)
	}
}
