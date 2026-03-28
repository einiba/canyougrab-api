// Parking detection from zone files + reverse IP discovery.
//
// Pass 1 (default): Parse NS records from CZDS zone files, match against
// known parking/marketplace patterns, write matches to Valkey. No DNS needed.
//
// Pass 2 (--reverse): Resolve A records for domains already flagged as parked
// (from Pass 1), cluster by /24 IP to discover unknown parking infrastructure.
// Rate-limited to ~80 queries/sec to avoid upstream blocking.
//
// Pass 3 (--discover): Cluster all NS base domains from zone files, report
// unknown patterns with 100+ domains. For finding new parking services.
//
// Usage:
//   /app/parking-scanner [tld ...]              # Pass 1: zone file NS scan
//   /app/parking-scanner --discover [tld ...]   # NS discovery mode
//   /app/parking-scanner --reverse              # Pass 2: reverse IP for parked domains
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
	valkeyTTL        = 25 * time.Hour // zone files refresh daily
	pipelineBatch    = 1000
	progressInterval = 50_000
	discoverMinCount = 100
	discoverSamples  = 5
	reverseRate      = 80 // queries/sec for reverse IP mode
)

// ── Known NS providers (from enrichment.py) ──────────────────────────────

type providerInfo struct {
	Name     string
	Category string // "for_sale", "parking", "dns_hosting", "registrar", "self_hosted"
}

var knownProviders = map[string]providerInfo{
	// Marketplace (for_sale)
	"dan.com": {"Dan.com", "for_sale"}, "undeveloped.com": {"Dan.com", "for_sale"},
	"park.do": {"Dan.com", "for_sale"}, "afternic.com": {"Afternic", "for_sale"},
	"eftydns.com": {"Efty", "for_sale"}, "squadhelp.com": {"Squadhelp", "for_sale"},
	"hugedomains.com": {"HugeDomains", "for_sale"}, "domainmarket.com": {"DomainMarket", "for_sale"},
	"brandshelter.com": {"BrandShelter", "for_sale"}, "sav.com": {"Sav.com", "for_sale"},
	"uniregistry.net": {"Uniregistry", "for_sale"}, "namefind.com": {"NameFind", "for_sale"},
	"buydomains.com": {"BuyDomains", "for_sale"}, "domainprofi.de": {"DomainProfi", "for_sale"},
	// Parking
	"sedoparking.com": {"Sedo", "parking"}, "parkingcrew.net": {"ParkingCrew", "parking"},
	"above.com": {"Above.com", "parking"}, "bodis.com": {"Bodis", "parking"},
	"cashparking.com": {"GoDaddy CashParking", "parking"}, "smartname.com": {"GoDaddy CashParking", "parking"},
	"parklogic.com": {"ParkLogic", "parking"}, "voodoo.com": {"Voodoo", "parking"},
	"dsredirection.com": {"DS Redirection", "parking"}, "domainnamesales.com": {"Domain Name Sales", "parking"},
	"domainparkingserver.net": {"DomainParkingServer", "parking"}, "parkpage.com": {"ParkPage", "parking"},
	"ztomy.com": {"Ztomy", "parking"}, "realtime.at": {"Realtime", "parking"},
	"dopa.com": {"DOPA", "parking"}, "rookdns.com": {"RookDNS", "parking"},
	"itidns.com": {"ITI DNS", "parking"}, "trafficz.com": {"Trafficz", "parking"},
	"namedrive.com": {"NameDrive", "parking"}, "skenzo.com": {"Skenzo", "parking"},
	"tonic.to": {"Tonic", "parking"},
	// DNS hosting (detected but not written to Valkey)
	"google.com": {"Google", "self_hosted"}, "googledomains.com": {"Google Domains", "dns_hosting"},
	"cloudflare.com": {"Cloudflare", "dns_hosting"}, "squarespace.com": {"Squarespace", "dns_hosting"},
	"wixdns.net": {"Wix", "dns_hosting"}, "myshopify.com": {"Shopify", "dns_hosting"},
	"vercel-dns.com": {"Vercel", "dns_hosting"}, "nsone.net": {"NS1 / Netlify", "dns_hosting"},
	"hostinger.com": {"Hostinger", "dns_hosting"}, "digitalocean.com": {"DigitalOcean", "dns_hosting"},
	// Registrar
	"domaincontrol.com": {"GoDaddy", "registrar"}, "registrar-servers.com": {"Namecheap", "registrar"},
	"porkbun.com": {"Porkbun", "registrar"}, "dynadot.com": {"Dynadot", "registrar"},
	"namebrightdns.com": {"NameBright", "registrar"}, "gandi.net": {"Gandi", "registrar"},
	"ovh.net": {"OVH", "registrar"}, "opensrs.net": {"OpenSRS / Tucows", "registrar"},
}

func nsBaseDomain(ns string) string {
	ns = strings.TrimRight(strings.ToLower(ns), ".")
	parts := strings.Split(ns, ".")
	if len(parts) < 2 {
		return ns
	}
	return strings.Join(parts[len(parts)-2:], ".")
}

func lookupProvider(nsBase string) (providerInfo, bool) {
	if info, ok := knownProviders[nsBase]; ok {
		return info, true
	}
	return providerInfo{}, false
}

func isParkingOrSale(cat string) bool {
	return cat == "parking" || cat == "for_sale"
}

// ── Helpers ──────────────────────────────────────────────────────────────

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// ── CZDS auth + download ─────────────────────────────────────────────────

func czdsAuthenticate(username, password string) (string, error) {
	payload, _ := json.Marshal(map[string]string{"username": username, "password": password})
	resp, err := http.Post("https://account-api.icann.org/api/authenticate", "application/json", bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	var result struct{ AccessToken string `json:"accessToken"` }
	json.NewDecoder(resp.Body).Decode(&result)
	return result.AccessToken, nil
}

func downloadZoneFile(tld, token, destPath string) error {
	req, _ := http.NewRequest("GET", fmt.Sprintf("https://czds-download-api.icann.org/czds/downloads/%s.zone", tld), nil)
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := (&http.Client{Timeout: 30 * time.Minute}).Do(req)
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

// ── Zone file NS streaming ───────────────────────────────────────────────

// domainNS holds a domain and its collected nameservers from the zone file.
type domainNS struct {
	domain      string
	nameservers []string
}

// streamNSRecords parses NS records from a gzipped zone file and emits
// (domain, []nameservers) pairs grouped by domain. Zone files are sorted
// by domain name, so we accumulate NS records until the domain changes.
func streamNSRecords(zonePath, tld string, out chan<- domainNS) error {
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
	scanner := bufio.NewScanner(gz)
	scanner.Buffer(make([]byte, 4*1024*1024), 4*1024*1024)

	var currentSLD string
	var currentNS []string
	count := 0
	lineCount := 0
	nsLineCount := 0
	debugSamples := 0

	flush := func() {
		if currentSLD != "" && len(currentNS) > 0 {
			count++
			out <- domainNS{domain: currentSLD + "." + tld, nameservers: currentNS}
		}
		currentNS = nil
	}

	for scanner.Scan() {
		line := scanner.Bytes()
		lineCount++

		// Log first 5 non-comment lines for format debugging
		if lineCount <= 200 && len(line) > 0 && line[0] != ';' && line[0] != '$' {
			if debugSamples < 5 {
				debugSamples++
				log.Printf(".%s DEBUG line %d: %q", tld, lineCount, string(line[:min(len(line), 200)]))
			}
		}

		if len(line) == 0 || line[0] == ';' || line[0] == '$' || line[0] == ' ' || line[0] == '\t' {
			continue
		}

		// Tokenize into fields (handles any whitespace: tabs, spaces, mixed)
		fields := bytes.Fields(line)
		if len(fields) < 4 {
			continue
		}

		// Find "NS" field — it's typically field[2] or field[3] depending on
		// whether TTL is present: "domain TTL IN NS target" or "domain IN NS target"
		nsIdx := -1
		for i := 1; i < len(fields)-1; i++ {
			if bytes.Equal(fields[i], []byte("NS")) {
				nsIdx = i
				break
			}
		}
		if nsIdx < 0 || nsIdx >= len(fields)-1 {
			if nsLineCount == 0 && lineCount%1000000 == 0 {
				log.Printf(".%s: %dM lines scanned, still 0 NS records found", tld, lineCount/1000000)
			}
			continue
		}
		nsLineCount++

		// First field is the domain
		domainBytes := fields[0]
		if !bytes.HasSuffix(domainBytes, []byte(suffix)) {
			continue
		}
		sld := strings.ToLower(string(domainBytes[:len(domainBytes)-len(suffix)]))
		if strings.ContainsRune(sld, '.') {
			continue // skip subdomains
		}

		// Field after "NS" is the nameserver target
		nsHost := strings.TrimRight(strings.ToLower(string(fields[nsIdx+1])), ".")

		// Group by domain
		if sld != currentSLD {
			flush()
			currentSLD = sld
		}
		currentNS = append(currentNS, nsHost)
	}
	flush()

	log.Printf(".%s: streamed %d domains with NS records", tld, count)
	return scanner.Err()
}

// ── Pass 1: Zone file NS scan ────────────────────────────────────────────

func scanZoneNS(tld, zonePath string, rdb *redis.Client) (parked, forSale, total int64, elapsed time.Duration) {
	start := time.Now()
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

	ch := make(chan domainNS, 10000)
	go func() {
		if err := streamNSRecords(zonePath, tld, ch); err != nil {
			log.Printf(".%s NS stream error: %v", tld, err)
		}
		close(ch)
	}()

	now := time.Now().UTC().Format(time.RFC3339Nano)

	for rec := range ch {
		n := atomic.AddInt64(&total, 1)
		if n%int64(progressInterval) == 0 {
			log.Printf(".%s scan: %dk domains, %d parked, %d for_sale",
				tld, n/1000, atomic.LoadInt64(&parked), atomic.LoadInt64(&forSale))
		}

		// Match first NS against known providers
		var matched *providerInfo
		for _, ns := range rec.nameservers {
			base := nsBaseDomain(ns)
			if info, ok := lookupProvider(base); ok {
				matched = &info
				break
			}
		}

		// Only write parking + marketplace to Valkey
		if matched != nil && isParkingOrSale(matched.Category) {
			if matched.Category == "parking" {
				atomic.AddInt64(&parked, 1)
			} else {
				atomic.AddInt64(&forSale, 1)
			}

			nsJSON, _ := json.Marshal(rec.nameservers)
			pipeMu.Lock()
			pipe.HSet(ctx, "dom:"+rec.domain, map[string]interface{}{
				"available":       "false",
				"confidence":      "high",
				"original_source": "zone",
				"tld":             tld,
				"cached_at":       now,
				"nameservers":     string(nsJSON),
				"parking_provider": matched.Name,
				"parking_category": matched.Category,
			})
			pipe.Expire(ctx, "dom:"+rec.domain, valkeyTTL)
			pipeCount++
			if pipeCount >= pipelineBatch {
				flushPipe()
			}
			pipeMu.Unlock()
		}
	}

	pipeMu.Lock()
	flushPipe()
	pipeMu.Unlock()

	return parked, forSale, total, time.Since(start)
}

// ── NS Discovery mode ────────────────────────────────────────────────────

type nsCluster struct {
	count   int
	samples []string
}

type clusterEntry struct {
	NSBase   string   `json:"ns_base"`
	Count    int      `json:"count"`
	Samples  []string `json:"samples"`
	Provider string   `json:"provider,omitempty"`
	Category string   `json:"category,omitempty"`
}

type discoveryReport struct {
	TLD     string         `json:"tld"`
	Unknown []clusterEntry `json:"unknown"`
	Known   []clusterEntry `json:"known"`
}

func discoverZoneNS(tld, zonePath string) discoveryReport {
	clusters := make(map[string]*nsCluster)

	ch := make(chan domainNS, 10000)
	var total int64
	go func() {
		if err := streamNSRecords(zonePath, tld, ch); err != nil {
			log.Printf(".%s NS stream error: %v", tld, err)
		}
		close(ch)
	}()

	for rec := range ch {
		n := atomic.AddInt64(&total, 1)
		if n%int64(progressInterval) == 0 {
			log.Printf(".%s discover: %dk domains, %d clusters", tld, n/1000, len(clusters))
		}
		for _, ns := range rec.nameservers {
			base := nsBaseDomain(ns)
			c := clusters[base]
			if c == nil {
				c = &nsCluster{}
				clusters[base] = c
			}
			c.count++
			if len(c.samples) < discoverSamples {
				c.samples = append(c.samples, rec.domain)
			}
			break // one NS per domain is enough for clustering
		}
	}

	var unknown, known []clusterEntry
	for base, c := range clusters {
		if c.count < discoverMinCount {
			continue
		}
		entry := clusterEntry{NSBase: base, Count: c.count, Samples: c.samples}
		if info, ok := lookupProvider(base); ok {
			entry.Provider = info.Name
			entry.Category = info.Category
			known = append(known, entry)
		} else {
			unknown = append(unknown, entry)
		}
	}
	sort.Slice(unknown, func(i, j int) bool { return unknown[i].Count > unknown[j].Count })
	sort.Slice(known, func(i, j int) bool { return known[i].Count > known[j].Count })

	log.Printf(".%s: %d domains, %d unknown clusters, %d known",
		tld, total, len(unknown), len(known))
	return discoveryReport{TLD: tld, Unknown: unknown, Known: known}
}

// ── Pass 2: Reverse IP discovery ─────────────────────────────────────────

// MISP parking CIDRs (attributed)
type parkingCIDR struct {
	network *net.IPNet
	service string
}

var parkingCIDRs []parkingCIDR

func init() {
	entries := []struct{ cidr, service string }{
		{"199.59.240.0/22", "Bodis"}, {"185.53.176.0/22", "ParkingCrew"},
		{"91.195.240.0/23", "Sedo"}, {"64.190.62.0/23", "Sedo"},
		{"204.11.56.0/23", "Above.com"}, {"66.81.199.0/24", "Above.com"},
		{"34.102.136.180/32", "GoDaddy"}, {"34.98.99.30/32", "GoDaddy"},
		{"35.186.238.101/32", "GoDaddy CashParking"},
		{"3.33.130.190/32", "GoDaddy"}, {"15.197.148.33/32", "GoDaddy"},
		{"208.91.196.0/23", "DomainSponsor"},
		{"52.58.78.16/32", "Dan.com"}, {"3.64.163.50/32", "Dan.com"},
		{"209.99.64.0/24", "Afternic"}, {"209.99.40.222/32", "Afternic"},
		{"75.2.115.196/32", "HugeDomains"}, {"75.2.18.233/32", "HugeDomains"},
		{"75.2.26.18/32", "HugeDomains"}, {"75.2.37.224/32", "HugeDomains"},
		{"76.223.65.111/32", "HugeDomains"}, {"99.83.154.118/32", "HugeDomains"},
	}
	for _, e := range entries {
		_, network, err := net.ParseCIDR(e.cidr)
		if err != nil {
			log.Fatalf("bad CIDR %q: %v", e.cidr, err)
		}
		parkingCIDRs = append(parkingCIDRs, parkingCIDR{network: network, service: e.service})
	}
}

func matchParkingIP(ip net.IP) (string, bool) {
	for _, p := range parkingCIDRs {
		if p.network.Contains(ip) {
			return p.service, true
		}
	}
	return "", false
}

func ipTo24(ip net.IP) string {
	v4 := ip.To4()
	if v4 == nil {
		return ""
	}
	return fmt.Sprintf("%d.%d.%d.0/24", v4[0], v4[1], v4[2])
}

func reverseIPDiscovery(rdb *redis.Client) {
	dnsHost := getenv("DNS_RESOLVER_HOSTNAME", "unbound.canyougrab.svc.cluster.local")
	dnsPort := getenv("DNS_RESOLVER_PORT", "53")
	resolver := &net.Resolver{
		PreferGo: true,
		Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
			return (&net.Dialer{Timeout: 2 * time.Second}).DialContext(ctx, "udp", net.JoinHostPort(dnsHost, dnsPort))
		},
	}

	ctx := context.Background()
	ticker := time.NewTicker(time.Second / reverseRate)
	defer ticker.Stop()

	// Scan Valkey for domains flagged as parked by zone scan
	clusters := make(map[string]*nsCluster)
	var total, resolved, matched int64

	iter := rdb.Scan(ctx, 0, "dom:*", 10000).Iterator()
	for iter.Next(ctx) {
		key := iter.Val()
		src, _ := rdb.HGet(ctx, key, "original_source").Result()
		if src != "zone" {
			continue
		}
		cat, _ := rdb.HGet(ctx, key, "parking_category").Result()
		if cat != "parking" && cat != "for_sale" {
			continue
		}

		domain := strings.TrimPrefix(key, "dom:")
		total++
		if total%10000 == 0 {
			log.Printf("reverse: scanned %dk parked domains, resolved %d, %d IP clusters",
				total/1000, resolved, len(clusters))
		}

		// Rate-limited DNS resolution
		<-ticker.C
		lookupCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
		addrs, err := resolver.LookupIPAddr(lookupCtx, domain)
		cancel()
		if err != nil {
			continue
		}
		resolved++

		for _, addr := range addrs {
			ip := addr.IP.To4()
			if ip == nil {
				continue
			}

			// Check known parking CIDRs
			if service, ok := matchParkingIP(ip); ok {
				rdb.HSet(ctx, key, "parked_by_ip", "true", "parking_ip_service", service)
				matched++
			}

			// Cluster by /24
			prefix := ipTo24(ip)
			if prefix == "" {
				continue
			}
			c := clusters[prefix]
			if c == nil {
				c = &nsCluster{}
				clusters[prefix] = c
			}
			c.count++
			if len(c.samples) < discoverSamples {
				c.samples = append(c.samples, domain)
			}
			break // one IP per domain
		}
	}

	// Report unknown /24 clusters
	log.Printf("reverse: done — %d parked domains, %d resolved, %d IP-matched", total, resolved, matched)

	type ipEntry struct {
		IP      string   `json:"ip"`
		Count   int      `json:"count"`
		Samples []string `json:"samples"`
		Known   string   `json:"known,omitempty"`
	}
	var unknown, known []ipEntry
	for prefix, c := range clusters {
		if c.count < discoverMinCount {
			continue
		}
		_, network, _ := net.ParseCIDR(prefix)
		repIP := make(net.IP, 4)
		copy(repIP, network.IP.To4())
		repIP[3] = 1
		service, isKnown := matchParkingIP(repIP)
		entry := ipEntry{IP: prefix, Count: c.count, Samples: c.samples}
		if isKnown {
			entry.Known = service
			known = append(known, entry)
		} else {
			unknown = append(unknown, entry)
		}
	}
	sort.Slice(unknown, func(i, j int) bool { return unknown[i].Count > unknown[j].Count })

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(map[string]interface{}{
		"unknown_ip_clusters": unknown,
		"known_ip_clusters":   known,
		"stats": map[string]int64{
			"parked_domains": total, "resolved": resolved, "ip_matched": matched,
		},
	})
}

// ── Main ─────────────────────────────────────────────────────────────────

var supportedTLDs = []string{"com", "net", "org", "xyz", "info", "top", "online", "store", "shop"}

func main() {
	log.SetFlags(log.Ldate | log.Ltime)

	// Parse flags
	discoverMode := false
	reverseMode := false
	var tlds []string
	for _, arg := range os.Args[1:] {
		switch arg {
		case "--discover":
			discoverMode = true
		case "--reverse":
			reverseMode = true
		default:
			tlds = append(tlds, arg)
		}
	}
	if len(tlds) == 0 {
		tlds = supportedTLDs
	}

	// Reverse mode doesn't need CZDS — reads from Valkey
	if reverseMode {
		log.Printf("parking-scanner: REVERSE IP DISCOVERY MODE")
		rdb := newValkeyClient()
		if err := rdb.Ping(context.Background()).Err(); err != nil {
			log.Fatalf("Valkey ping failed: %v", err)
		}
		reverseIPDiscovery(rdb)
		return
	}

	// Forward modes need CZDS
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

	if discoverMode {
		log.Printf("parking-scanner: NS DISCOVERY MODE")
		var reports []discoveryReport
		for _, tld := range tlds {
			zonePath := fmt.Sprintf("/tmp/%s.zone.gz", tld)
			log.Printf("Downloading .%s zone file...", tld)
			if err := downloadZoneFile(tld, token, zonePath); err != nil {
				log.Printf("Failed: %v (skipping)", err)
				continue
			}
			reports = append(reports, discoverZoneNS(tld, zonePath))
			os.Remove(zonePath)
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(reports)
		return
	}

	// Default: Pass 1 — zone file NS scan
	log.Printf("parking-scanner: ZONE FILE NS SCAN")
	rdb := newValkeyClient()
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		log.Fatalf("Valkey ping failed: %v", err)
	}

	var totalParked, totalForSale, totalDomains int64
	for _, tld := range tlds {
		zonePath := fmt.Sprintf("/tmp/%s.zone.gz", tld)
		log.Printf("Downloading .%s zone file...", tld)
		if err := downloadZoneFile(tld, token, zonePath); err != nil {
			log.Printf("Failed: %v (skipping)", err)
			continue
		}
		p, fs, t, elapsed := scanZoneNS(tld, zonePath, rdb)
		totalParked += p
		totalForSale += fs
		totalDomains += t
		log.Printf(".%s: %d parked + %d for_sale out of %d domains in %s",
			tld, p, fs, t, elapsed.Round(time.Second))
		os.Remove(zonePath)
	}
	log.Printf("DONE: %d parked + %d for_sale across %d domains",
		totalParked, totalForSale, totalDomains)
}
// cache bust 1774725063
