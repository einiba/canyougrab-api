// Validates zone-file NS parking detection by HTTP-probing a sample of
// detected domains and comparing NS-based classification with actual content.
//
// Usage:
//   /app/parking-validator                        # 10K random samples
//   /app/parking-validator --count=1000           # 1K smoke test
//   /app/parking-validator --category=for_sale    # only marketplace domains
//   /app/parking-validator --rate=100             # 100 probes/sec
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	defaultCount    = 10000
	defaultRate     = 50 // probes/sec
	progressEvery   = 500
	probeTimeout    = 5 * time.Second
	maxFalseSamples = 20 // max false positive examples in report
)

var (
	whoisHost = getenv("WHOIS_HOSTNAME", "rust-whois-rdap.canyougrab.svc.cluster.local")
	whoisPort = getenv("WHOIS_PORT", "3000")
)

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
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

// ── Domain info from Valkey ──────────────────────────────────────────────

type domainInfo struct {
	Domain   string
	Category string // "parking" or "for_sale"
	Provider string
}

func collectDomains(rdb *redis.Client, filterCategory string, targetCount int) []domainInfo {
	ctx := context.Background()
	var domains []domainInfo
	seen := make(map[string]bool)
	attempts := 0
	maxAttempts := targetCount * 5 // give up after 5x attempts

	log.Printf("Sampling %d domains via RANDOMKEY...", targetCount)

	for len(domains) < targetCount && attempts < maxAttempts {
		attempts++

		key, err := rdb.RandomKey(ctx).Result()
		if err != nil {
			continue
		}
		if !strings.HasPrefix(key, "dom:") || seen[key] {
			continue
		}
		seen[key] = true

		// Pipeline: get both fields in one round trip
		pipe := rdb.Pipeline()
		catCmd := pipe.HGet(ctx, key, "parking_category")
		provCmd := pipe.HGet(ctx, key, "parking_provider")
		pipe.Exec(ctx)

		cat, err := catCmd.Result()
		if err != nil || cat == "" {
			continue
		}
		if filterCategory != "" && cat != filterCategory {
			continue
		}

		provider, _ := provCmd.Result()
		domain := strings.TrimPrefix(key, "dom:")

		domains = append(domains, domainInfo{
			Domain:   domain,
			Category: cat,
			Provider: provider,
		})

		if len(domains)%1000 == 0 {
			log.Printf("Collected %d/%d domains (%d attempts)", len(domains), targetCount, attempts)
		}
	}

	log.Printf("Collected %d domains in %d attempts", len(domains), attempts)
	return domains
}

// ── Probe ────────────────────────────────────────────────────────────────

type probeResult struct {
	ForSale  *bool    `json:"for_sale"`
	Platform string   `json:"platform"`
	Signals  []string `json:"signals"`
	ProbeMs  int      `json:"probe_time_ms"`
}

var httpClient = &http.Client{Timeout: probeTimeout}

func probeDomain(domain string) (*probeResult, error) {
	u := fmt.Sprintf("http://%s:%s/probe/%s", whoisHost, whoisPort, domain)
	resp, err := httpClient.Get(u)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	var pr probeResult
	if err := json.Unmarshal(body, &pr); err != nil {
		return nil, err
	}
	return &pr, nil
}

// ── Classification ───────────────────────────────────────────────────────

type classification struct {
	Domain      string   `json:"domain"`
	NSCategory  string   `json:"ns_category"`
	NSProvider  string   `json:"ns_provider"`
	ProbeResult string   `json:"probe_result"` // "sale", "not_sale", "inconclusive", "error"
	Platform    string   `json:"platform,omitempty"`
	Signals     []string `json:"signals,omitempty"`
	Label       string   `json:"label"` // "true_positive_sale", "true_positive_parking", "false_positive", "upgrade", "inconclusive"
}

func classify(d domainInfo, pr *probeResult, probeErr error) classification {
	c := classification{
		Domain:     d.Domain,
		NSCategory: d.Category,
		NSProvider: d.Provider,
	}

	if probeErr != nil {
		c.ProbeResult = "error"
		c.Label = "inconclusive"
		return c
	}

	if pr.ForSale == nil {
		c.ProbeResult = "inconclusive"
		c.Label = "inconclusive"
		c.Signals = pr.Signals
		return c
	}

	c.Platform = pr.Platform
	c.Signals = pr.Signals

	if *pr.ForSale {
		c.ProbeResult = "sale"
		if d.Category == "for_sale" {
			c.Label = "true_positive_sale"
		} else {
			c.Label = "upgrade" // NS said parking, probe says for sale
		}
	} else {
		c.ProbeResult = "not_sale"
		if d.Category == "parking" {
			c.Label = "true_positive_parking"
		} else {
			c.Label = "false_positive" // NS said for_sale, probe says not
		}
	}
	return c
}

// ── Report ───────────────────────────────────────────────────────────────

type providerStats struct {
	Sampled       int `json:"sampled"`
	Confirmed     int `json:"confirmed"`
	FalsePositive int `json:"false_positive"`
	Upgraded      int `json:"upgraded"`
	Inconclusive  int `json:"inconclusive"`
}

type report struct {
	TotalSampled   int                      `json:"total_sampled"`
	ProbeSucceeded int                      `json:"probe_succeeded"`
	ProbeFailed    int                      `json:"probe_failed"`
	Results        map[string]int           `json:"results"`
	Accuracy       map[string]float64       `json:"accuracy"`
	ByProvider     map[string]providerStats `json:"by_provider"`
	FalsePositives []classification         `json:"sample_false_positives,omitempty"`
	Upgrades       []classification         `json:"sample_upgrades,omitempty"`
}

// ── Main ─────────────────────────────────────────────────────────────────

func main() {
	log.SetFlags(log.Ldate | log.Ltime)

	count := defaultCount
	rate := defaultRate
	filterCategory := ""

	for _, arg := range os.Args[1:] {
		if strings.HasPrefix(arg, "--count=") {
			count, _ = strconv.Atoi(strings.TrimPrefix(arg, "--count="))
		} else if strings.HasPrefix(arg, "--rate=") {
			rate, _ = strconv.Atoi(strings.TrimPrefix(arg, "--rate="))
		} else if strings.HasPrefix(arg, "--category=") {
			filterCategory = strings.TrimPrefix(arg, "--category=")
		}
	}

	log.Printf("parking-validator: count=%d rate=%d/sec category=%q", count, rate, filterCategory)

	rdb := newValkeyClient()
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		log.Fatalf("Valkey: %v", err)
	}

	// Step 1: Sample domains directly (RANDOMKEY is fast, no full scan)
	sample := collectDomains(rdb, filterCategory, count)
	if len(sample) == 0 {
		log.Fatal("No domains found with parking_category field")
	}
	count = len(sample)

	// Step 3: Probe at controlled rate
	ticker := time.NewTicker(time.Second / time.Duration(rate))
	defer ticker.Stop()

	var (
		results      []classification
		mu           sync.Mutex
		probed       int64
		probeFailed  int64
		wg           sync.WaitGroup
		sem          = make(chan struct{}, rate*2) // allow some buffering
	)

	start := time.Now()

	for _, d := range sample {
		<-ticker.C
		wg.Add(1)
		sem <- struct{}{}
		go func(d domainInfo) {
			defer wg.Done()
			defer func() { <-sem }()

			pr, err := probeDomain(d.Domain)
			c := classify(d, pr, err)

			mu.Lock()
			results = append(results, c)
			mu.Unlock()

			n := atomic.AddInt64(&probed, 1)
			if err != nil {
				atomic.AddInt64(&probeFailed, 1)
			}
			if n%int64(progressEvery) == 0 {
				elapsed := time.Since(start).Seconds()
				log.Printf("Progress: %d/%d probed (%.0f/sec), %d failed", n, count, float64(n)/elapsed, atomic.LoadInt64(&probeFailed))
			}
		}(d)
	}
	wg.Wait()

	elapsed := time.Since(start)
	log.Printf("Probing complete: %d in %s (%.0f/sec)", len(results), elapsed.Round(time.Second), float64(len(results))/elapsed.Seconds())

	// Step 4: Build report
	labelCounts := map[string]int{}
	byProvider := map[string]*providerStats{}
	var falsePositives, upgrades []classification

	for _, c := range results {
		labelCounts[c.Label]++

		p := byProvider[c.NSProvider]
		if p == nil {
			p = &providerStats{}
			byProvider[c.NSProvider] = p
		}
		p.Sampled++
		switch c.Label {
		case "true_positive_sale", "true_positive_parking":
			p.Confirmed++
		case "false_positive":
			p.FalsePositive++
			if len(falsePositives) < maxFalseSamples {
				falsePositives = append(falsePositives, c)
			}
		case "upgrade":
			p.Upgraded++
			if len(upgrades) < maxFalseSamples {
				upgrades = append(upgrades, c)
			}
		case "inconclusive":
			p.Inconclusive++
		}
	}

	total := float64(len(results))
	confirmed := float64(labelCounts["true_positive_sale"] + labelCounts["true_positive_parking"])
	fp := float64(labelCounts["false_positive"])

	// Sort providers by sample count
	providerReport := map[string]providerStats{}
	for k, v := range byProvider {
		providerReport[k] = *v
	}

	r := report{
		TotalSampled:   len(results),
		ProbeSucceeded: len(results) - int(probeFailed),
		ProbeFailed:    int(probeFailed),
		Results:        labelCounts,
		Accuracy: map[string]float64{
			"confirmed_rate":    confirmed / total,
			"false_positive_rate": fp / total,
			"inconclusive_rate": float64(labelCounts["inconclusive"]) / total,
			"upgrade_rate":      float64(labelCounts["upgrade"]) / total,
		},
		ByProvider:     providerReport,
		FalsePositives: falsePositives,
		Upgrades:       upgrades,
	}

	// Sort provider keys for consistent output
	_ = sort.Search(0, func(i int) bool { return false })

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(r)
}
