"""API Stack — dev or prod API droplet with workers."""

import pulumi
import pulumi_digitalocean as do
import pulumi_cloudflare as cf
import pulumi_command as command
import base64
from pathlib import Path
from shared import (
    CF_ZONE_ID,
    VPC_ID_OLD, VPC_ID_NEW, VPC_CIDR_OLD, VPC_CIDR_NEW,
    UNBOUND_HOSTNAME, RUST_WHOIS_HOSTNAME,
    DEV_UNBOUND_HOSTNAME, DEV_RUST_WHOIS_HOSTNAME,
    REPO_ROOT, DEPLOY_KEY_PATH, SSL_CERT_PATH, SSL_KEY_PATH,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
stack = pulumi.get_stack()
config = pulumi.Config()

droplet_size = config.get("droplet_size") or "s-1vcpu-1gb"
region = config.get("region") or "nyc3"
git_ref = config.get("git_ref") or ("dev" if stack == "dev" else "main")
worker_count = int(config.get("worker_count") or "3")
batch_concurrency = int(config.get("batch_concurrency") or "10")

api_hostname = f"{'dev-' if stack == 'dev' else ''}api.canyougrab.it"
portal_hostname = f"{'dev-' if stack == 'dev' else ''}portal.canyougrab.it"
portal_url = config.get("portal_url") or f"https://{portal_hostname}"

postgres_password = config.require_secret("postgres_password")
valkey_password = config.require_secret("valkey_password")
stripe_secret_key = config.require_secret("stripe_secret_key")
stripe_webhook_secret = config.require_secret("stripe_webhook_secret")

cf_api_token = pulumi.Config("cloudflare").require_secret("apiToken")

postgres_host = config.get("postgres_host") or "private-db-postgresql-nyc3-canyougrab-it-do-user-34383636-0.g.db.ondigitalocean.com"
postgres_port = config.get("postgres_port") or "25060"
postgres_db = config.get("postgres_db") or "canyougrabit"
postgres_user = config.get("postgres_user") or "canyougrabit"
valkey_host = config.get("valkey_host") or "private-db-valkey-nyc3-queue-do-user-34383636-0.g.db.ondigitalocean.com"
valkey_port = config.get("valkey_port") or "25061"
auth0_domain = config.get("auth0_domain") or "login.canyougrab.it"
auth0_audience = config.get("auth0_audience") or "https://api.canyougrab.it"

ssh_key_fingerprint = config.require("ssh_key_fingerprint")

# Per-environment VPC and service hostnames
if stack == "dev":
    vpc_id = config.get("vpc_id") or VPC_ID_NEW
    vpc_cidr = VPC_CIDR_NEW
    unbound_hostname = DEV_UNBOUND_HOSTNAME
    whois_hostname = DEV_RUST_WHOIS_HOSTNAME
else:
    vpc_id = config.get("vpc_id") or VPC_ID_OLD
    vpc_cidr = VPC_CIDR_OLD
    unbound_hostname = UNBOUND_HOSTNAME
    whois_hostname = RUST_WHOIS_HOSTNAME

# ---------------------------------------------------------------------------
# Read local files
# ---------------------------------------------------------------------------
deploy_key_b64_str = base64.b64encode(DEPLOY_KEY_PATH.read_bytes()).decode()
ssl_cert = SSL_CERT_PATH.read_text() if SSL_CERT_PATH.exists() else ""
ssl_key = SSL_KEY_PATH.read_text() if SSL_KEY_PATH.exists() else ""


# ---------------------------------------------------------------------------
# Cloud-init user_data builder
# ---------------------------------------------------------------------------
def build_env_file(pg_pass: str, vk_pass: str, stripe_key: str, stripe_wh: str) -> str:
    return f"""POSTGRES_HOST={postgres_host}
POSTGRES_PORT={postgres_port}
POSTGRES_DB={postgres_db}
POSTGRES_USER={postgres_user}
POSTGRES_PASSWORD={pg_pass}
POSTGRES_SSLMODE=require
VALKEY_HOST={valkey_host}
VALKEY_PORT={valkey_port}
VALKEY_PASSWORD={vk_pass}
VALKEY_USERNAME=default
VALKEY_QUEUE_NAME=queue:jobs:{stack}
WHOIS_HOSTNAME={whois_hostname}
DNS_RESOLVER_HOSTNAME={unbound_hostname}
STRIPE_SECRET_KEY={stripe_key}
STRIPE_WEBHOOK_SECRET={stripe_wh}
AUTH0_DOMAIN={auth0_domain}
AUTH0_AUDIENCE={auth0_audience}
PORTAL_URL={portal_url}
BATCH_CONCURRENCY={batch_concurrency}"""


def build_user_data(
    env_file_content: str,
    cf_token: str,
) -> str:
    """Cloud-init script that fully bootstraps a droplet.

    SSL strategy:
      - API: Let's Encrypt via certbot + cloudflare DNS-01 (works before DNS switch)
      - Portal: Cloudflare origin cert (portal is CF-proxied)
    """
    return f"""#!/bin/bash
set -e
exec > /var/log/canyougrab-provision.log 2>&1
echo "=== canyougrab provision started at $(date -u) ==="

# --- SSH hardening ---
sed -i 's/^#\\?MaxStartups.*/MaxStartups 50:30:200/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 50:30:200' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# --- VPC services use CF DNS for service discovery (no /etc/hosts needed) ---
# unbound.canyougrab.it and rust-whois.canyougrab.it resolve to VPC IPs via CF DNS

# --- System packages ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx git curl rsync certbot python3-certbot-dns-cloudflare

# --- Node exporter ---
cd /tmp
curl -sLO https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz
tar xf node_exporter-1.8.2.linux-amd64.tar.gz
mv node_exporter-1.8.2.linux-amd64/node_exporter /usr/local/bin/
useradd -rs /bin/false node_exporter 2>/dev/null || true
cat > /etc/systemd/system/node_exporter.service <<'NODEEXP'
[Unit]
Description=Node Exporter
After=network.target
[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter
[Install]
WantedBy=multi-user.target
NODEEXP
systemctl daemon-reload
systemctl enable --now node_exporter

# --- GitHub deploy key ---
mkdir -p /root/.ssh
echo '{deploy_key_b64_str}' | base64 -d > /root/.ssh/canyougrab-deploy
chmod 600 /root/.ssh/canyougrab-deploy
cat > /root/.ssh/config <<'SSHCONF'
Host github.com
    IdentityFile /root/.ssh/canyougrab-deploy
    StrictHostKeyChecking no
SSHCONF

# --- Clone repo ---
git clone git@github.com:ericismaking/canyougrab-api.git /opt/canyougrab-repo
cd /opt/canyougrab-repo
git checkout {git_ref}

# --- Virtualenv and directories ---
python3 -m venv /opt/canyougrab/venv
mkdir -p /opt/canyougrab/{{api,portal,scripts}}

# --- Write env file (secrets from Pulumi) ---
cat > /opt/canyougrab/app.env <<'ENVFILE'
{env_file_content}
ENVFILE
grep '^POSTGRES_' /opt/canyougrab/app.env > /opt/canyougrab/database.env
grep -E '^(VALKEY_|WHOIS_)' /opt/canyougrab/app.env > /opt/canyougrab/valkey.env
grep -E '^(STRIPE_|AUTH0_|PORTAL_|BATCH_)' /opt/canyougrab/app.env > /opt/canyougrab/stripe.env

# --- SSL: Cloudflare origin cert (for portal — CF-proxied) ---
cat > /etc/ssl/cloudflare-origin-cert.pem <<'SSLCERT'
{ssl_cert}
SSLCERT
cat > /etc/ssl/cloudflare-origin-key.pem <<'SSLKEY'
{ssl_key}
SSLKEY
chmod 600 /etc/ssl/cloudflare-origin-key.pem

# --- SSL: Let's Encrypt for API (DNS-01 via Cloudflare) ---
# This works BEFORE DNS points to this droplet — proves ownership via DNS TXT record
mkdir -p /etc/letsencrypt
cat > /etc/letsencrypt/cloudflare.ini <<CFINI
dns_cloudflare_api_token = {cf_token}
CFINI
chmod 600 /etc/letsencrypt/cloudflare.ini

# Force fresh ACME account registration (each droplet is ephemeral)
rm -rf /etc/letsencrypt/accounts

# Certbot may fail (rate limits, DNS propagation) — don't abort the whole bootstrap
if certbot certonly \\
    --dns-cloudflare \\
    --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \\
    --dns-cloudflare-propagation-seconds 30 \\
    -d {api_hostname} \\
    --non-interactive \\
    --agree-tos \\
    --email eric.cocozza@canyougrab.it \\
    --cert-name api \\
    --force-renewal; then
    echo "=== Let's Encrypt cert obtained ==="
    LE_CERT_OK=true
else
    echo "=== WARNING: certbot failed, falling back to Cloudflare origin cert ==="
    LE_CERT_OK=false
fi

# Set up auto-renewal timer
systemctl enable certbot.timer 2>/dev/null || true

# --- Nginx: API ---
# Use LE cert if available, fall back to Cloudflare origin cert
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-enabled/api.conf <<'NGINXAPI'
server {{
    listen 80;
    server_name {api_hostname};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    server_name {api_hostname};

    ssl_certificate __SSL_CERT__;
    ssl_certificate_key __SSL_KEY__;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location /mcp {{
        if ($request_method = 'OPTIONS') {{
            add_header 'Access-Control-Allow-Origin' '*' always;
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS' always;
            add_header 'Access-Control-Allow-Headers' 'authorization, content-type, accept, mcp-session-id' always;
            add_header 'Access-Control-Max-Age' 86400 always;
            return 204;
        }}
        add_header 'Access-Control-Allow-Origin' '*' always;
        add_header 'Access-Control-Allow-Headers' 'authorization, content-type, accept, mcp-session-id' always;
        add_header 'Access-Control-Expose-Headers' 'mcp-session-id' always;

        proxy_pass http://127.0.0.1:8001/mcp;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_buffering off;
        proxy_cache off;
    }}

    location / {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
NGINXAPI

# Patch SSL cert paths based on whether LE succeeded
if [ "$LE_CERT_OK" = true ]; then
    sed -i 's|__SSL_CERT__|/etc/letsencrypt/live/api/fullchain.pem|' /etc/nginx/sites-enabled/api.conf
    sed -i 's|__SSL_KEY__|/etc/letsencrypt/live/api/privkey.pem|' /etc/nginx/sites-enabled/api.conf
else
    sed -i 's|__SSL_CERT__|/etc/ssl/cloudflare-origin-cert.pem|' /etc/nginx/sites-enabled/api.conf
    sed -i 's|__SSL_KEY__|/etc/ssl/cloudflare-origin-key.pem|' /etc/nginx/sites-enabled/api.conf
fi

# --- Nginx: Portal (Cloudflare origin cert, CF-proxied) ---
cat > /etc/nginx/sites-enabled/portal.conf <<'NGINXPORTAL'
server {{
    listen 80;
    server_name {portal_hostname};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    server_name {portal_hostname};

    ssl_certificate /etc/ssl/cloudflare-origin-cert.pem;
    ssl_certificate_key /etc/ssl/cloudflare-origin-key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    root /opt/canyougrab/portal;
    index index.html;

    location / {{
        try_files $uri $uri.html /index.html;
    }}

    location /assets/ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}
}}
NGINXPORTAL

nginx -t && systemctl reload nginx

# --- Deploy app (rsync code, pip install, systemd units) ---
CANYOUGRAB_ENV={stack} bash config/deploy/deploy-app.sh {git_ref}

echo "=== canyougrab provision completed at $(date -u) ==="
touch /opt/canyougrab/.provision-complete
"""


# ---------------------------------------------------------------------------
# API Droplet
# ---------------------------------------------------------------------------
user_data = pulumi.Output.all(
    postgres_password, valkey_password, stripe_secret_key, stripe_webhook_secret,
    cf_api_token,
).apply(lambda s: build_user_data(
    env_file_content=build_env_file(s[0], s[1], s[2], s[3]),
    cf_token=s[4],
))

api_droplet = do.Droplet(
    f"{stack}-api",
    name=f"{'api-dev' if stack == 'dev' else 'api'}.canyougrab.it",
    image="ubuntu-24-04-x64",
    region=region,
    size=droplet_size,
    vpc_uuid=vpc_id,
    ssh_keys=[ssh_key_fingerprint],
    monitoring=True,
    tags=[f"canyougrab-api-{stack}"],
    user_data=user_data,
    # True blue-green: create new droplet BEFORE destroying old one.
    # DNS switches after the new droplet is up, then old one is deleted.
    opts=pulumi.ResourceOptions(delete_before_replace=False),
)

# ---------------------------------------------------------------------------
# Cloudflare DNS Records (source of truth for all DNS)
# ---------------------------------------------------------------------------
# API: DNS-only (direct to droplet, Let's Encrypt cert)
cf_api_dns = cf.DnsRecord(
    f"{stack}-cf-api-dns",
    zone_id=CF_ZONE_ID,
    name=api_hostname,
    type="A",
    content=api_droplet.ipv4_address,
    proxied=False,
    ttl=60,
)

# Portal: CF-proxied (Cloudflare handles public SSL, origin cert on server)
cf_portal_dns = cf.DnsRecord(
    f"{stack}-cf-portal-dns",
    zone_id=CF_ZONE_ID,
    name=portal_hostname,
    type="A",
    content=api_droplet.ipv4_address,
    proxied=True,
    ttl=1,  # auto when proxied
)

# ---------------------------------------------------------------------------
# DigitalOcean Firewall
# ---------------------------------------------------------------------------
api_firewall = do.Firewall(
    f"{stack}-api-firewall",
    name=f"canyougrab-{stack}-api-fw",
    droplet_ids=[api_droplet.id],
    inbound_rules=[
        # HTTP/HTTPS (public)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="80",
            source_addresses=["0.0.0.0/0", "::/0"]),
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="443",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # SSH
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="22",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # Node exporter (VPC only)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="9100",
            source_addresses=[vpc_cidr]),
    ],
    outbound_rules=[
        do.FirewallOutboundRuleArgs(
            protocol="tcp", port_range="1-65535",
            destination_addresses=["0.0.0.0/0", "::/0"]),
        do.FirewallOutboundRuleArgs(
            protocol="udp", port_range="1-65535",
            destination_addresses=["0.0.0.0/0", "::/0"]),
        do.FirewallOutboundRuleArgs(
            protocol="icmp",
            destination_addresses=["0.0.0.0/0", "::/0"]),
    ],
)

# ---------------------------------------------------------------------------
# Health check (polls public HTTPS endpoint — no SSH needed)
# ---------------------------------------------------------------------------
health_check = command.local.Command(
    f"{stack}-api-health-check",
    create=api_droplet.ipv4_address.apply(
        lambda ip: (
            f"for i in $(seq 1 60); do "
            f"if curl -sf --max-time 5 --resolve {api_hostname}:443:{ip} "
            f"https://{api_hostname}/health 2>/dev/null; then exit 0; fi; "
            f"sleep 5; done; "
            f"echo 'TIMEOUT: health check failed after 5 minutes'; exit 1"
        )
    ),
    opts=pulumi.ResourceOptions(depends_on=[api_droplet, cf_api_dns]),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
pulumi.export("droplet_id", api_droplet.id)
pulumi.export("droplet_name", api_droplet.name)
pulumi.export("public_ip", api_droplet.ipv4_address)
pulumi.export("private_ip", api_droplet.ipv4_address_private)
pulumi.export("api_hostname", api_hostname)
pulumi.export("portal_hostname", portal_hostname)
pulumi.export("api_url", f"https://{api_hostname}")
pulumi.export("portal_url", f"https://{portal_hostname}")
pulumi.export("health_check", health_check.stdout)
