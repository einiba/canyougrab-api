-- NS records now live in Valkey dom:{domain} hashes (populated lazily by Go worker).
-- WHOIS registration data is also cached in dom:{domain} hashes.
-- These Postgres tables are no longer read in the hot path.
DROP TABLE IF EXISTS zone_ns_snapshots;
DROP TABLE IF EXISTS domain_whois_enrichment;
