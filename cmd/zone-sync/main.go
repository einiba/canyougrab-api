// Zone file sync — downloads from ICANN CZDS once daily and uploads to DO Spaces.
// All downstream consumers (bloom builder, parking scanner) read from Spaces.
//
// Usage:
//   /app/zone-sync [tld ...]           # sync specific TLDs
//   /app/zone-sync                      # sync all supported TLDs
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	spaces "github.com/ericismaking/canyougrab-api/internal/spaces"
)

var supportedTLDs = []string{"com", "net", "org", "xyz", "info", "top", "online", "store", "shop"}

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
	if result.AccessToken == "" {
		return "", fmt.Errorf("empty token")
	}
	return result.AccessToken, nil
}

func downloadFromCZDS(tld, token, destPath string) (int64, error) {
	req, _ := http.NewRequest("GET", fmt.Sprintf("https://czds-download-api.icann.org/czds/downloads/%s.zone", tld), nil)
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := (&http.Client{Timeout: 45 * time.Minute}).Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return 0, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(destPath)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	return io.Copy(f, resp.Body)
}

func main() {
	log.SetFlags(log.Ldate | log.Ltime)
	log.Printf("zone-sync starting")

	czdsUser := os.Getenv("CZDS_USERNAME")
	czdsPass := os.Getenv("CZDS_PASSWORD")
	if czdsUser == "" || czdsPass == "" {
		log.Fatal("CZDS_USERNAME and CZDS_PASSWORD must be set")
	}

	tlds := supportedTLDs
	if len(os.Args) > 1 {
		tlds = os.Args[1:]
	}

	// Init Spaces client
	spacesClient, err := spaces.NewClient()
	if err != nil {
		log.Fatalf("Spaces client: %v", err)
	}

	// Auth to CZDS
	log.Printf("Authenticating to CZDS...")
	token, err := czdsAuthenticate(czdsUser, czdsPass)
	if err != nil {
		log.Fatalf("CZDS auth failed: %v", err)
	}

	date := time.Now().UTC().Format("2006-01-02")
	var synced, failed int

	for _, tld := range tlds {
		localPath := fmt.Sprintf("/tmp/%s.zone.gz", tld)
		archiveKey := fmt.Sprintf("archive/%s/%s.zone.gz", date, tld)
		latestKey := fmt.Sprintf("latest/%s.zone.gz", tld)

		// Download from CZDS
		log.Printf("Downloading .%s from CZDS...", tld)
		t0 := time.Now()
		size, err := downloadFromCZDS(tld, token, localPath)
		if err != nil {
			log.Printf("FAILED .%s download: %v", tld, err)
			failed++
			continue
		}
		log.Printf(".%s: downloaded %.1f MB in %s", tld, float64(size)/1024/1024, time.Since(t0).Round(time.Second))

		// Upload to Spaces (archive)
		if err := spacesClient.Upload(localPath, archiveKey); err != nil {
			log.Printf("FAILED .%s archive upload: %v", tld, err)
			os.Remove(localPath)
			failed++
			continue
		}

		// Copy to latest/
		if err := spacesClient.CopyKey(archiveKey, latestKey); err != nil {
			log.Printf("FAILED .%s latest copy: %v", tld, err)
			// Archive succeeded, so this isn't a total failure
		}

		os.Remove(localPath)
		synced++
		log.Printf(".%s: synced to Spaces (archive/%s + latest)", tld, date)
	}

	log.Printf("DONE: %d synced, %d failed out of %d TLDs", synced, failed, len(tlds))
	if failed > 0 {
		os.Exit(1)
	}
}
