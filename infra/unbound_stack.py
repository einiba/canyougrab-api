"""
Unbound DNS Resolver Stack — Pulumi Module

Manages the dedicated DNS resolver for domain availability checking.
Currently shared between dev and prod (can be split later).

Service discovery: unbound.canyougrab.it → VPC private IP via CF DNS.
"""

import pulumi
import pulumi_digitalocean as do
import pulumi_cloudflare as cf
import pulumi_command as command
from pathlib import Path
from shared import CF_ZONE_ID, VPC_ID_OLD, VPC_ID_NEW, VPC_CIDR_OLD, VPC_CIDR_NEW

stack = pulumi.get_stack()
config = pulumi.Config()

droplet_size = config.get("droplet_size") or "s-1vcpu-2gb"
region = config.get("region") or "nyc3"
ssh_key_fingerprint = config.require("ssh_key_fingerprint")

is_dev = stack.startswith("dev")
vpc_id = config.get("vpc_id") or (VPC_ID_NEW if is_dev else VPC_ID_OLD)
vpc_cidr = VPC_CIDR_NEW if is_dev else VPC_CIDR_OLD
UNBOUND_HOSTNAME = "dev-unbound.canyougrab.it" if is_dev else "unbound.canyougrab.it"
droplet_name = "dev-unbound.canyougrab.it" if is_dev else "unbound.canyougrab.it"

# ---------------------------------------------------------------------------
# Unbound config — bind to 0.0.0.0, ACL limits to VPC
# ---------------------------------------------------------------------------
UNBOUND_CONF = f"""server:
    # Bind all interfaces — ACL restricts to VPC only
    interface: 0.0.0.0
    port: 53
    do-ip4: yes
    do-ip6: yes
    do-udp: yes
    do-tcp: yes

    # Access control — localhost and VPC subnet only
    access-control: 127.0.0.0/8 allow
    access-control: {vpc_cidr} allow
    access-control: 0.0.0.0/0 refuse

    # Performance
    num-threads: 1
    msg-cache-slabs: 2
    rrset-cache-slabs: 2
    infra-cache-slabs: 2
    key-cache-slabs: 2

    # Cache sizing (optimized for 2GB droplet)
    msg-cache-size: 400m
    rrset-cache-size: 800m

    # TTL overrides for domain availability checking
    cache-max-ttl: 604800
    cache-max-negative-ttl: 300
    cache-min-ttl: 60

    # Prefetch popular entries before expiry
    prefetch: yes

    # Serve expired data while refreshing in background
    serve-expired: yes
    serve-expired-ttl: 86400
    serve-expired-client-timeout: 1800

    # Security
    hide-identity: yes
    hide-version: yes
    harden-glue: yes
    harden-dnssec-stripped: yes
    harden-referral-path: yes
    use-caps-for-id: yes
    qname-minimisation: yes
    minimal-responses: yes

    # Infra cache
    infra-cache-numhosts: 10000
    infra-host-ttl: 900

    # Logging
    verbosity: 1
    log-queries: no
    log-replies: no
    logfile: ""
    use-syslog: yes

    # Root hints and DNSSEC
    root-hints: /usr/share/dns/root.hints
    auto-trust-anchor-file: "/var/lib/unbound/root.key"

remote-control:
    control-enable: yes
    control-interface: 127.0.0.1
    control-port: 8953
    server-key-file: "/etc/unbound/unbound_server.key"
    server-cert-file: "/etc/unbound/unbound_server.pem"
    control-key-file: "/etc/unbound/unbound_control.key"
    control-cert-file: "/etc/unbound/unbound_control.pem"
"""


def build_user_data(tailscale_auth_key: str) -> str:
    return f"""#!/bin/bash
set -e
exec > /var/log/canyougrab-provision.log 2>&1
echo "=== unbound provision started at $(date -u) ==="

# --- Tailscale (FIRST — enables SSH debug access if provisioning fails) ---
curl -fsSL https://tailscale.com/install.sh | sh
echo 'net.ipv4.ip_forward = 1' >> /etc/sysctl.d/99-tailscale.conf
sysctl -p /etc/sysctl.d/99-tailscale.conf
tailscale up --auth-key={tailscale_auth_key} --ssh --hostname={droplet_name.replace('.canyougrab.it', '')} --advertise-routes={vpc_cidr}
echo "=== Tailscale connected (advertising VPC routes) ==="

# --- SSH hardening ---
sed -i 's/^#\\?MaxStartups.*/MaxStartups 50:30:200/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 50:30:200' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# --- Disable systemd-resolved stub listener (conflicts with unbound on port 53) ---
mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/no-stub.conf <<'RESOLVED'
[Resolve]
DNSStubListener=no
RESOLVED
systemctl restart systemd-resolved

# --- System packages ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq unbound curl dns-root-data

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

# --- Unbound config ---
cat > /etc/unbound/unbound.conf <<'UNBOUNDCFG'
{UNBOUND_CONF}
UNBOUNDCFG

# Generate control keys
unbound-control-setup 2>/dev/null || true

# --- Enable and start ---
systemctl daemon-reload
systemctl enable --now node_exporter unbound

# --- Re-disable systemd-resolved stub (apt-get install unbound may re-enable it) ---
systemctl restart systemd-resolved 2>/dev/null || true
systemctl restart unbound

# --- Verify ---
sleep 2
if dig @0.0.0.0 google.com NS +short | grep -q 'google'; then
    echo "=== Unbound DNS resolution verified ==="
else
    echo "=== WARNING: Unbound DNS test failed ==="
fi

echo "=== unbound provision completed at $(date -u) ==="
touch /opt/canyougrab/.provision-complete
"""


# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------
from tailscale_key import server_key

# ---------------------------------------------------------------------------
# Droplet
# ---------------------------------------------------------------------------
user_data = server_key.key.apply(lambda key: build_user_data(tailscale_auth_key=key))

unbound_droplet = do.Droplet(
    f"{stack}-droplet",
    name=droplet_name,
    image="ubuntu-24-04-x64",
    region=region,
    size=droplet_size,
    vpc_uuid=vpc_id,
    ssh_keys=[ssh_key_fingerprint],
    monitoring=True,
    tags=["canyougrab-unbound"],
    user_data=user_data,
)

# ---------------------------------------------------------------------------
# CF DNS — unbound.canyougrab.it → VPC private IP
# ---------------------------------------------------------------------------
cf_unbound_dns = cf.DnsRecord(
    f"{stack}-dns",
    zone_id=CF_ZONE_ID,
    name=UNBOUND_HOSTNAME,
    type="A",
    content=unbound_droplet.ipv4_address_private,
    proxied=False,
    ttl=60,
)

# ---------------------------------------------------------------------------
# Firewall — DNS on VPC only, SSH public
# ---------------------------------------------------------------------------
unbound_firewall = do.Firewall(
    f"{stack}-firewall",
    name=f"canyougrab-{stack}-fw",
    droplet_ids=[unbound_droplet.id],
    inbound_rules=[
        # Tailscale direct connections (UDP 41641)
        do.FirewallInboundRuleArgs(
            protocol="udp", port_range="41641",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # VPC internal (DNS, node exporter, inter-service)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="1-65535",
            source_addresses=[vpc_cidr]),
        do.FirewallInboundRuleArgs(
            protocol="udp", port_range="1-65535",
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
# Health check — SSH in, test DNS resolution
# ---------------------------------------------------------------------------
health_check = command.local.Command(
    f"{stack}-health-check",
    create=unbound_droplet.ipv4_address.apply(
        lambda ip: (
            f"for i in $(seq 1 60); do "
            f"if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
            f"-i ~/.ssh/id_ed25519 root@{ip} "
            f"'dig @127.0.0.1 google.com NS +short' 2>/dev/null | grep -q google; then exit 0; fi; "
            f"sleep 10; done; "
            f"echo 'TIMEOUT: unbound health check failed after 10 minutes'; exit 1"
        )
    ),
    opts=pulumi.ResourceOptions(depends_on=[unbound_droplet]),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
pulumi.export("droplet_id", unbound_droplet.id)
pulumi.export("droplet_name", unbound_droplet.name)
pulumi.export("public_ip", unbound_droplet.ipv4_address)
pulumi.export("private_ip", unbound_droplet.ipv4_address_private)
pulumi.export("dns_hostname", UNBOUND_HOSTNAME)
pulumi.export("health_check", health_check.stdout)
