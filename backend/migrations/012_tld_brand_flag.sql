-- Mark brand/closed TLDs that do not allow public domain registrations.
-- Domains under these TLDs should return available=null, error=brand_tld.
ALTER TABLE tld_registry ADD COLUMN IF NOT EXISTS is_brand BOOLEAN NOT NULL DEFAULT FALSE;
