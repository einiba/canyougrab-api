-- DNS nameserver pool for capacity-aware multi-resolver routing.
-- Each server's estimated_cap_qps is updated based on observed error rates.
-- Workers route queries proportionally to capacity.

CREATE TABLE IF NOT EXISTS nameservers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL UNIQUE,
    port INTEGER DEFAULT 53,
    provider TEXT,
    protocol TEXT DEFAULT 'udp',

    -- Capacity tracking
    estimated_cap_qps REAL DEFAULT 1.0,
    success_count_3m INTEGER DEFAULT 0,
    error_count_3m INTEGER DEFAULT 0,
    last_error_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,

    -- State
    enabled BOOLEAN DEFAULT TRUE,
    disabled_at TIMESTAMPTZ,
    disabled_reason TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed with known high-capacity public DNS servers (all start at cap=1)
INSERT INTO nameservers (name, address, provider) VALUES
    ('Google Primary',      '8.8.8.8',          'google'),
    ('Google Secondary',    '8.8.4.4',          'google'),
    ('Cloudflare Primary',  '1.1.1.1',          'cloudflare'),
    ('Cloudflare Secondary','1.0.0.1',          'cloudflare'),
    ('Quad9 Primary',       '9.9.9.9',          'quad9'),
    ('Quad9 Secondary',     '149.112.112.112',  'quad9'),
    ('OpenDNS Primary',     '208.67.222.222',   'opendns'),
    ('OpenDNS Secondary',   '208.67.220.220',   'opendns')
ON CONFLICT (address) DO NOTHING;
