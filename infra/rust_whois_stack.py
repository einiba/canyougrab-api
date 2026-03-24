"""
Rust-WHOIS Stack — Pulumi Module

Manages the WHOIS/RDAP lookup service droplet.
Currently shared between dev and prod (can be split later).

The binary is pre-built via GitHub Actions CI and downloaded from
a GitHub Release — no Rust toolchain needed on the server.
"""

import pulumi
import pulumi_digitalocean as do
import pulumi_cloudflare as cf
import pulumi_command as command
from pathlib import Path
from shared import (
    VPC_ID, VPC_CIDR,
    UNBOUND_IP, RUST_WHOIS_IP,
    DEPLOY_KEY_PATH, SSL_CERT_PATH, SSL_KEY_PATH,
)
import base64

config = pulumi.Config()

# Config
droplet_size = config.get("droplet_size") or "s-1vcpu-1gb"
region = config.get("region") or "nyc3"
ssh_key_fingerprint = config.require("ssh_key_fingerprint")
release_version = config.get("release_version") or "v0.2.2"
repo_owner = config.get("repo_owner") or "einiba"
repo_name = config.get("repo_name") or "canyougrab-whois-engine"
gh_token = config.get_secret("gh_token")  # needed for private repo releases

# Service config
bind_ip = RUST_WHOIS_IP  # VPC-internal only
bind_port = "3000"
concurrent_queries = config.get("concurrent_queries") or "50"
cache_ttl = config.get("cache_ttl") or "3600"
cache_max = config.get("cache_max") or "10000"
whois_timeout = config.get("whois_timeout") or "10"

# Deploy key for private repo release download
deploy_key_b64 = base64.b64encode(DEPLOY_KEY_PATH.read_bytes()).decode()


def build_user_data(token: str) -> str:
    """Cloud-init for rust-whois. Downloads pre-built binary from GitHub Release."""

    # Build the download command — use gh token for private repos
    if token:
        download_cmd = (
            f"curl -sL -H 'Authorization: token {token}' "
            f"-H 'Accept: application/octet-stream' "
            f"$(curl -sf -H 'Authorization: token {token}' "
            f"'https://api.github.com/repos/{repo_owner}/{repo_name}/releases/tags/{release_version}' "
            f"| python3 -c \"import sys,json; assets=json.load(sys.stdin)['assets']; "
            f"print([a['url'] for a in assets if a['name']=='whois-service'][0])\") "
            f"-o /opt/rust-whois/whois-service"
        )
    else:
        download_cmd = (
            f"curl -sL 'https://github.com/{repo_owner}/{repo_name}/releases/download/"
            f"{release_version}/whois-service' -o /opt/rust-whois/whois-service"
        )

    return f"""#!/bin/bash
set -e
exec > /var/log/canyougrab-provision.log 2>&1
echo "=== rust-whois provision started at $(date -u) ==="

# --- SSH hardening ---
sed -i 's/^#\\?MaxStartups.*/MaxStartups 50:30:200/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 50:30:200' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# --- VPC internal hostnames ---
echo '{UNBOUND_IP} unbound.canyougrab.internal' >> /etc/hosts
echo '{RUST_WHOIS_IP} rust-whois.canyougrab.internal' >> /etc/hosts

# --- System packages ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl python3 jq

# --- Node exporter ---
cd /tmp
curl -sLO https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz
tar xf node_exporter-1.8.2.linux-amd64.tar.gz
mv node_exporter-1.8.2.linux-amd64/node_exporter /usr/local/bin/
useradd -rs /bin/false node_exporter 2>/dev/null || true
cat > /etc/systemd/system/node_exporter.service <<'EOF'
[Unit]
Description=Node Exporter
After=network.target
[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter
[Install]
WantedBy=multi-user.target
EOF

# --- Create whois user and directories ---
useradd -rs /bin/false whois 2>/dev/null || true
mkdir -p /opt/rust-whois
chown whois:whois /opt/rust-whois

# --- Download pre-built binary from GitHub Release ---
echo "Downloading whois-service {release_version}..."
{download_cmd}
chmod +x /opt/rust-whois/whois-service
chown whois:whois /opt/rust-whois/whois-service

# Verify binary
/opt/rust-whois/whois-service --version 2>/dev/null || echo "(no --version flag, continuing)"

# --- Write env file ---
cat > /opt/rust-whois/rust-whois.env <<'ENVFILE'
HOST={bind_ip}
PORT={bind_port}
CACHE_TTL_SECONDS={cache_ttl}
CACHE_MAX_ENTRIES={cache_max}
WHOIS_TIMEOUT_SECONDS={whois_timeout}
CONCURRENT_WHOIS_QUERIES={concurrent_queries}
RUST_LOG=whois_service=info
ENVFILE
chown whois:whois /opt/rust-whois/rust-whois.env
chmod 600 /opt/rust-whois/rust-whois.env

# --- Systemd service ---
cat > /etc/systemd/system/rust-whois.service <<'EOF'
[Unit]
Description=rust-whois RDAP/WHOIS lookup service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=whois
Group=whois
WorkingDirectory=/opt/rust-whois
ExecStart=/opt/rust-whois/whois-service
EnvironmentFile=/opt/rust-whois/rust-whois.env
Restart=on-failure
RestartSec=5

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadOnlyPaths=/
ReadWritePaths=/opt/rust-whois

LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# --- Enable and start ---
systemctl daemon-reload
systemctl enable --now node_exporter rust-whois

# --- Health check ---
sleep 3
curl -sf http://{bind_ip}:{bind_port}/health || echo "WARNING: health check failed"

echo "=== rust-whois provision completed at $(date -u) ==="
touch /opt/canyougrab/.provision-complete
"""


# ---------------------------------------------------------------------------
# Droplet
# ---------------------------------------------------------------------------
user_data = pulumi.Output.all(
    gh_token or pulumi.Output.from_input(""),
).apply(lambda s: build_user_data(s[0]))

whois_droplet = do.Droplet(
    "rust-whois",
    name="canyougrab-rust-whois",
    image="ubuntu-24-04-x64",
    region=region,
    size=droplet_size,
    vpc_uuid=VPC_ID,
    ssh_keys=[ssh_key_fingerprint],
    monitoring=True,
    tags=["canyougrab-rust-whois"],
    user_data=user_data,
)

# ---------------------------------------------------------------------------
# Firewall — VPC-only access (no public exposure)
# ---------------------------------------------------------------------------
whois_firewall = do.Firewall(
    "rust-whois-firewall",
    name="canyougrab-rust-whois-fw",
    droplet_ids=[whois_droplet.id],
    inbound_rules=[
        # WHOIS service (VPC only — API droplets connect here)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="3000",
            source_addresses=[VPC_CIDR]),
        # SSH
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="22",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # Node exporter (VPC only)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="9100",
            source_addresses=[VPC_CIDR]),
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
# Health check
# ---------------------------------------------------------------------------
health_check = command.remote.Command(
    "rust-whois-health-check",
    connection=command.remote.ConnectionArgs(
        host=whois_droplet.ipv4_address,
        user="root",
        private_key=Path.home().joinpath(".ssh/id_ed25519").read_text(),
    ),
    create=" ".join([
        "for i in $(seq 1 60); do",
        "test -f /opt/canyougrab/.provision-complete && break;",
        "sleep 10; done;",
        f"curl -sf http://{bind_ip}:{bind_port}/health",
    ]),
    opts=pulumi.ResourceOptions(depends_on=[whois_droplet]),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
pulumi.export("droplet_id", whois_droplet.id)
pulumi.export("droplet_name", whois_droplet.name)
pulumi.export("public_ip", whois_droplet.ipv4_address)
pulumi.export("private_ip", whois_droplet.ipv4_address_private)
pulumi.export("service_url", f"http://{bind_ip}:{bind_port}")
pulumi.export("release_version", release_version)
pulumi.export("health_check", health_check.stdout)
