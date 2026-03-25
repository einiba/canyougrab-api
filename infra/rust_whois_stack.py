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
    CF_ZONE_ID, VPC_ID_OLD, VPC_ID_NEW, VPC_CIDR_OLD, VPC_CIDR_NEW,
    DEPLOY_KEY_PATH,
)
import base64

stack = pulumi.get_stack()
config = pulumi.Config()

# Config
droplet_size = config.get("droplet_size") or "s-1vcpu-1gb"
region = config.get("region") or "nyc3"
ssh_key_fingerprint = config.require("ssh_key_fingerprint")
release_version = config.get("release_version") or "v0.2.2"
repo_owner = config.get("repo_owner") or "einiba"
repo_name = config.get("repo_name") or "canyougrab-whois-engine"
gh_token = config.get_secret("gh_token")  # needed for private repo releases

# Per-environment config
is_dev = stack.startswith("dev")
vpc_id = config.get("vpc_id") or (VPC_ID_NEW if is_dev else VPC_ID_OLD)
vpc_cidr = VPC_CIDR_NEW if is_dev else VPC_CIDR_OLD
whois_dns_hostname = "dev-rust-whois.canyougrab.it" if is_dev else "rust-whois.canyougrab.it"
droplet_name = "dev-rust-whois" if is_dev else "canyougrab-rust-whois"

# Service config
bind_ip = "0.0.0.0"
bind_port = "3000"
concurrent_queries = config.get("concurrent_queries") or "50"
cache_ttl = config.get("cache_ttl") or "3600"
cache_max = config.get("cache_max") or "10000"
whois_timeout = config.get("whois_timeout") or "10"

# Deploy key for private repo release download
deploy_key_b64 = base64.b64encode(DEPLOY_KEY_PATH.read_bytes()).decode()


def build_user_data(token: str, tailscale_auth_key: str) -> str:
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

# --- Tailscale (FIRST — enables SSH debug access if provisioning fails) ---
curl -fsSL https://tailscale.com/install.sh | sh
echo 'net.ipv4.ip_forward = 1' >> /etc/sysctl.d/99-tailscale.conf
sysctl -p /etc/sysctl.d/99-tailscale.conf
tailscale up --auth-key={tailscale_auth_key} --ssh --hostname={droplet_name} --advertise-routes={vpc_cidr}
echo "=== Tailscale connected (advertising VPC routes) ==="

# --- SSH hardening ---
sed -i 's/^#\\?MaxStartups.*/MaxStartups 50:30:200/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 50:30:200' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

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
from tailscale_key import server_key

user_data = pulumi.Output.all(
    gh_token or pulumi.Output.from_input(""),
    server_key.key,
).apply(lambda s: build_user_data(token=s[0], tailscale_auth_key=s[1]))

whois_droplet = do.Droplet(
    f"{stack}-droplet",
    name=droplet_name,
    image="ubuntu-24-04-x64",
    region=region,
    size=droplet_size,
    vpc_uuid=vpc_id,
    ssh_keys=[ssh_key_fingerprint],
    monitoring=True,
    tags=[f"canyougrab-{stack}"],
    user_data=user_data,
)

# ---------------------------------------------------------------------------
# Firewall — VPC-only access (no public exposure)
# ---------------------------------------------------------------------------
whois_firewall = do.Firewall(
    f"{stack}-firewall",
    name=f"canyougrab-{stack}-fw",
    droplet_ids=[whois_droplet.id],
    inbound_rules=[
        # Tailscale direct connections (UDP 41641)
        do.FirewallInboundRuleArgs(
            protocol="udp", port_range="41641",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # VPC internal (WHOIS service, node exporter, inter-service)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="1-65535",
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
# Cloudflare DNS — maps rust-whois.canyougrab.it to VPC private IP
# API workers use this hostname instead of a hardcoded IP, so blue-green
# deploys just update the DNS record and workers pick up the new IP.
# ---------------------------------------------------------------------------
cf_whois_dns = cf.DnsRecord(
    f"{stack}-dns",
    zone_id=CF_ZONE_ID,
    name=whois_dns_hostname,
    type="A",
    content=whois_droplet.ipv4_address_private,
    proxied=False,
    ttl=60,
)

# ---------------------------------------------------------------------------
# Health check — SSH to public IP, curl the VPC-bound service
# ---------------------------------------------------------------------------
health_check = command.local.Command(
    f"{stack}-health-check",
    create=pulumi.Output.all(
        whois_droplet.ipv4_address, whois_droplet.ipv4_address_private,
    ).apply(lambda ips: (
        f"for i in $(seq 1 60); do "
        f"if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
        f"-i ~/.ssh/id_ed25519 root@{ips[0]} "
        f"'curl -sf http://127.0.0.1:{bind_port}/health' 2>/dev/null; then exit 0; fi; "
        f"sleep 10; done; "
        f"echo 'TIMEOUT: rust-whois health check failed after 10 minutes'; exit 1"
    )),
    opts=pulumi.ResourceOptions(depends_on=[whois_droplet]),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
pulumi.export("droplet_id", whois_droplet.id)
pulumi.export("droplet_name", whois_droplet.name)
pulumi.export("public_ip", whois_droplet.ipv4_address)
pulumi.export("private_ip", whois_droplet.ipv4_address_private)
pulumi.export("dns_hostname", whois_dns_hostname)
pulumi.export("service_url", pulumi.Output.concat("http://", whois_droplet.ipv4_address_private, f":{bind_port}"))
pulumi.export("release_version", release_version)
pulumi.export("health_check", health_check.stdout)
