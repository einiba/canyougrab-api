# Backlog: Rapid7 Forward DNS for Parking IP Detection

## Summary

Download weekly Rapid7 FDNS A-record dataset and cross-reference with known parking IP ranges to detect parked domains that use generic registrar nameservers (invisible to NS-based detection).

## Data Source

- **URL:** https://opendata.rapid7.com/sonar.fdns_v2/
- **Format:** Gzip-compressed JSON, one record per line
- **Frequency:** Weekly snapshots
- **Cost:** Free (account registration required)
- **Size:** Tens of GB compressed per record type

## IP Sources to Cross-Reference

- MISP Parking Domain IP list: 119 CIDRs
  https://github.com/MISP/misp-warninglists/blob/main/lists/parking-domain/list.json
- TMA22 parking services: IP ranges per service in `parking_services.json`
  https://github.com/tma22-parking/tma22-parking.github.io

## Implementation

1. Weekly batch job (K8s CronJob) downloads the A-record FDNS file
2. Stream-process: for each domain → A record, check if IP falls in any parking CIDR
3. Write matched domains to Valkey with `parked_by_ip: true` flag
4. Enrichment reads this flag as an additional parking signal

## Why It Matters

Some domains use GoDaddy/Namecheap default nameservers (which we classify as `registrar`) but their A record points to a Sedo/Bodis/ParkingCrew IP. Our NS-based detection misses these. The TMA22 paper found this is a meaningful percentage of parked domains.

## Priority

Low — NS-based detection covers the majority. This fills edge cases. Implement after the NS clustering tool has been run and we've expanded the provider list.
