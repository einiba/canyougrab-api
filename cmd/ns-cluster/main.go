// NS cluster analysis — discovers parking/marketplace services from zone file NS records.
//
// Parses ICANN CZDS zone files, extracts NS records, and clusters domains by
// their nameserver's base domain.  Reports unrecognized NS clusters (patterns
// not in our enrichment provider database) with domain samples for investigation.
//
// Usage:
//   CZDS_USERNAME=... CZDS_PASSWORD=... go run cmd/ns-cluster/main.go [tld ...]
//   CZDS_USERNAME=... CZDS_PASSWORD=... go run cmd/ns-cluster/main.go com net org
//
// Output: JSON report to stdout.
package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

const minClusterSize = 1000 // only report NS base domains serving 1000+ domains
const maxSamples = 5        // sample domains per cluster

// Known NS base domains from enrichment.py _NS_PROVIDERS.
// category: dns_hosting, registrar, for_sale, parking, self_hosted
var knownProviders = map[string]struct{ Name, Category string }{
	// DNS hosting
	"google.com":          {"Google", "self_hosted"},
	"googledomains.com":   {"Google Domains", "dns_hosting"},
	"cloudflare.com":      {"Cloudflare", "dns_hosting"},
	"awsdns-":             {"AWS Route 53", "dns_hosting"}, // prefix match
	"azure-dns.com":       {"Azure DNS", "dns_hosting"},
	"squarespace.com":     {"Squarespace", "dns_hosting"},
	"wixdns.net":          {"Wix", "dns_hosting"},
	"myshopify.com":       {"Shopify", "dns_hosting"},
	"vercel-dns.com":      {"Vercel", "dns_hosting"},
	"nsone.net":           {"NS1 / Netlify", "dns_hosting"},
	"hostinger.com":       {"Hostinger", "dns_hosting"},
	"digitalocean.com":    {"DigitalOcean", "dns_hosting"},
	"dnsimple.com":        {"DNSimple", "dns_hosting"},
	"hover.com":           {"Hover", "dns_hosting"},
	"linode.com":          {"Linode", "dns_hosting"},
	"dnsmadeeasy.com":     {"DNS Made Easy", "dns_hosting"},
	"ultradns.com":        {"UltraDNS", "dns_hosting"},
	"afraid.org":          {"FreeDNS", "dns_hosting"},
	// Registrar
	"domaincontrol.com":   {"GoDaddy", "registrar"},
	"registrar-servers.com": {"Namecheap", "registrar"},
	"dreamhost.com":       {"DreamHost", "registrar"},
	"name-services.com":   {"Enom", "registrar"},
	"porkbun.com":         {"Porkbun", "registrar"},
	"dynadot.com":         {"Dynadot", "registrar"},
	"namebrightdns.com":   {"NameBright", "registrar"},
	"1und1.de":            {"IONOS", "registrar"},
	"ui-dns.com":          {"IONOS", "registrar"},
	"gandi.net":           {"Gandi", "registrar"},
	"ovh.net":             {"OVH", "registrar"},
	"epik.com":            {"Epik", "registrar"},
	"opensrs.net":         {"OpenSRS / Tucows", "registrar"},
	// Marketplace (for_sale)
	"dan.com":             {"Dan.com", "for_sale"},
	"undeveloped.com":     {"Dan.com", "for_sale"},
	"park.do":             {"Dan.com", "for_sale"},
	"afternic.com":        {"Afternic", "for_sale"},
	"eftydns.com":         {"Efty", "for_sale"},
	"squadhelp.com":       {"Squadhelp", "for_sale"},
	"hugedomains.com":     {"HugeDomains", "for_sale"},
	"domainmarket.com":    {"DomainMarket", "for_sale"},
	"brandshelter.com":    {"BrandShelter", "for_sale"},
	"sav.com":             {"Sav.com", "for_sale"},
	"uniregistry.net":     {"Uniregistry", "for_sale"},
	"namefind.com":        {"NameFind", "for_sale"},
	"buydomains.com":      {"BuyDomains", "for_sale"},
	"domainprofi.de":      {"DomainProfi", "for_sale"},
	// Parking
	"sedoparking.com":     {"Sedo", "parking"},
	"parkingcrew.net":     {"ParkingCrew", "parking"},
	"above.com":           {"Above.com", "parking"},
	"bodis.com":           {"Bodis", "parking"},
	"cashparking.com":     {"GoDaddy CashParking", "parking"},
	"smartname.com":       {"GoDaddy CashParking", "parking"},
	"parklogic.com":       {"ParkLogic", "parking"},
	"voodoo.com":          {"Voodoo", "parking"},
	"dsredirection.com":   {"DS Redirection", "parking"},
	"domainnamesales.com": {"Domain Name Sales", "parking"},
	"domainparkingserver.net": {"DomainParkingServer", "parking"},
	"parkpage.com":        {"ParkPage", "parking"},
	"ztomy.com":           {"Ztomy", "parking"},
	"realtime.at":         {"Realtime", "parking"},
	"dopa.com":            {"DOPA", "parking"},
	"rookdns.com":         {"RookDNS", "parking"},
	"itidns.com":          {"ITI DNS", "parking"},
	"trafficz.com":        {"Trafficz", "parking"},
	"namedrive.com":       {"NameDrive", "parking"},
	"skenzo.com":          {"Skenzo", "parking"},
	"tonic.to":            {"Tonic", "parking"},
}

// nsBaseDomain extracts the base domain from a nameserver hostname.
// e.g., "ns1.sedoparking.com." → "sedoparking.com"
func nsBaseDomain(ns string) string {
	ns = strings.TrimRight(strings.ToLower(ns), ".")
	parts := strings.Split(ns, ".")
	if len(parts) < 2 {
		return ns
	}
	return strings.Join(parts[len(parts)-2:], ".")
}

func lookupKnown(nsBase string) (string, string, bool) {
	if info, ok := knownProviders[nsBase]; ok {
		return info.Name, info.Category, true
	}
	return "", "", false
}

type ClusterEntry struct {
	NSBase   string   `json:"ns_base"`
	Count    int      `json:"count"`
	Samples  []string `json:"samples,omitempty"`
	Provider string   `json:"provider,omitempty"`
	Category string   `json:"category,omitempty"`
}

type Report struct {
	TLD     string         `json:"tld"`
	Unknown []ClusterEntry `json:"unknown"`
	Known   []ClusterEntry `json:"known"`
}

// ── CZDS auth (same as bloom-builder) ────────────────────────────────────

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

// ── Zone file NS extraction ─────────────────────────────────────────────

// clusterNSRecords streams a gzip zone file and builds NS base domain → domain count + samples.
func clusterNSRecords(zonePath, tld string) (map[string]*struct {
	count   int
	samples []string
}, error) {
	f, err := os.Open(zonePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	gz, err := gzip.NewReader(f)
	if err != nil {
		return nil, err
	}
	defer gz.Close()

	suffix := "." + tld + "."
	nsToken := []byte("\tNS\t") // tab-NS-tab is the most common zone file format

	clusters := make(map[string]*struct {
		count   int
		samples []string
	})

	scanner := bufio.NewScanner(gz)
	scanner.Buffer(make([]byte, 4*1024*1024), 4*1024*1024)
	lines := 0
	nsLines := 0

	for scanner.Scan() {
		line := scanner.Bytes()
		lines++

		// Skip comments and metadata
		if len(line) == 0 || line[0] == ';' || line[0] == '$' || line[0] == ' ' || line[0] == '\t' {
			continue
		}

		// Quick check: does this line contain an NS record?
		// Zone files use tabs between fields: "domain.\tTTL\tIN\tNS\tnameserver."
		// Some use spaces. Check for both "NS" patterns.
		nsIdx := bytes.Index(line, nsToken)
		if nsIdx < 0 {
			// Try space variant
			nsIdx = bytes.Index(line, []byte(" NS "))
			if nsIdx < 0 {
				continue
			}
		}

		// Extract domain (first field)
		spaceIdx := bytes.IndexAny(line, " \t")
		if spaceIdx <= 0 {
			continue
		}
		domain := string(line[:spaceIdx])

		// Must end with .tld.
		if !strings.HasSuffix(domain, suffix) {
			continue
		}

		// Skip subdomains
		sld := domain[:len(domain)-len(suffix)]
		if strings.ContainsRune(sld, '.') {
			continue
		}

		// Extract nameserver — last whitespace-delimited field on the line
		fields := bytes.Fields(line)
		if len(fields) < 2 {
			continue
		}
		nsHost := strings.TrimRight(string(fields[len(fields)-1]), ".")
		nsBase := nsBaseDomain(nsHost)
		nsLines++

		c := clusters[nsBase]
		if c == nil {
			c = &struct {
				count   int
				samples []string
			}{}
			clusters[nsBase] = c
		}
		c.count++
		fqdn := strings.ToLower(sld + "." + tld)
		if len(c.samples) < maxSamples {
			// Avoid duplicate samples
			dup := false
			for _, s := range c.samples {
				if s == fqdn {
					dup = true
					break
				}
			}
			if !dup {
				c.samples = append(c.samples, fqdn)
			}
		}
	}

	log.Printf(".%s: %d lines, %d NS records, %d unique NS base domains", tld, lines, nsLines, len(clusters))
	return clusters, scanner.Err()
}

func buildReport(tld string, clusters map[string]*struct {
	count   int
	samples []string
}) Report {
	var unknown, known []ClusterEntry

	for nsBase, c := range clusters {
		if c.count < minClusterSize {
			continue
		}
		name, category, isKnown := lookupKnown(nsBase)
		entry := ClusterEntry{
			NSBase:  nsBase,
			Count:   c.count,
			Samples: c.samples,
		}
		if isKnown {
			entry.Provider = name
			entry.Category = category
			known = append(known, entry)
		} else {
			unknown = append(unknown, entry)
		}
	}

	// Sort by count descending
	sort.Slice(unknown, func(i, j int) bool { return unknown[i].Count > unknown[j].Count })
	sort.Slice(known, func(i, j int) bool { return known[i].Count > known[j].Count })

	return Report{TLD: tld, Unknown: unknown, Known: known}
}

func main() {
	log.SetFlags(log.Ldate | log.Ltime)

	czdsUser := os.Getenv("CZDS_USERNAME")
	czdsPass := os.Getenv("CZDS_PASSWORD")
	if czdsUser == "" || czdsPass == "" {
		log.Fatal("CZDS_USERNAME and CZDS_PASSWORD must be set")
	}

	tlds := []string{"com", "net", "org"}
	if len(os.Args) > 1 {
		tlds = os.Args[1:]
	}

	log.Printf("Authenticating to CZDS...")
	token, err := czdsAuthenticate(czdsUser, czdsPass)
	if err != nil {
		log.Fatalf("CZDS auth failed: %v", err)
	}

	var allReports []Report

	for _, tld := range tlds {
		zonePath := fmt.Sprintf("/tmp/%s.zone.gz", tld)

		log.Printf("Downloading .%s zone file...", tld)
		if err := downloadZoneFile(tld, token, zonePath); err != nil {
			log.Printf("Failed to download .%s: %v (skipping)", tld, err)
			continue
		}
		defer os.Remove(zonePath)

		log.Printf("Clustering NS records for .%s...", tld)
		clusters, err := clusterNSRecords(zonePath, tld)
		if err != nil {
			log.Printf("Failed to parse .%s: %v", tld, err)
			continue
		}

		report := buildReport(tld, clusters)
		allReports = append(allReports, report)

		log.Printf(".%s: %d unknown clusters (1000+ domains), %d known",
			tld, len(report.Unknown), len(report.Known))
	}

	// Output JSON to stdout
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(allReports)
}
