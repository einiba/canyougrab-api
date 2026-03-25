"""
Admin/Monitoring Stack — Pulumi Module

Manages the shared monitoring infrastructure:
  - Prometheus + Alertmanager
  - Grafana
  - Redis/Valkey exporter
  - RQ metrics exporter
  - Node exporter
  - Nginx reverse proxy

This is a singleton — one admin server monitors all environments.
"""

import pulumi
import pulumi_digitalocean as do
import pulumi_cloudflare as cf
import pulumi_command as command
import base64
from pathlib import Path
from shared import (
    CF_ZONE_ID, VPC_ID_OLD, VPC_CIDR_OLD,
    REPO_ROOT, DEPLOY_KEY_PATH, SSL_CERT_PATH, SSL_KEY_PATH,
)

config = pulumi.Config()

# Admin-specific config
droplet_size = config.get("droplet_size") or "s-2vcpu-2gb"
region = config.get("region") or "nyc3"
ssh_key_fingerprint = config.require("ssh_key_fingerprint")

# Secrets
valkey_password = config.require_secret("valkey_password")
do_api_token = pulumi.Config("digitalocean").require_secret("token")
slack_webhook_url = config.get_secret("slack_webhook_url") or pulumi.Output.from_input("")

# Read local files
deploy_key_b64 = base64.b64encode(DEPLOY_KEY_PATH.read_bytes()).decode()
ssl_cert = SSL_CERT_PATH.read_text() if SSL_CERT_PATH.exists() else ""
ssl_key = SSL_KEY_PATH.read_text() if SSL_KEY_PATH.exists() else ""

# Read config files from repo
prometheus_yml = (REPO_ROOT / "config" / "prometheus" / "prometheus.yml").read_text()
alert_rules_yml = (REPO_ROOT / "config" / "prometheus" / "alert-rules.yml").read_text()

# Grafana provisioning
grafana_datasources = (REPO_ROOT / "config" / "grafana" / "provisioning" / "datasources" / "prometheus.yml").read_text()
grafana_dashboards_prov = (REPO_ROOT / "config" / "grafana" / "provisioning" / "dashboards" / "default.yml").read_text()

# Grafana dashboard JSON files
dashboards_dir = REPO_ROOT / "config" / "grafana" / "dashboards"
dashboard_files = {}
if dashboards_dir.exists():
    for f in dashboards_dir.glob("*.json"):
        dashboard_files[f.name] = f.read_text()


def build_admin_user_data(
    valkey_pw: str,
    do_token: str,
    slack_url: str,
    tailscale_auth_key: str,
) -> str:
    """Cloud-init script for the admin/monitoring server."""

    # Build dashboard file writes
    dashboard_writes = ""
    for name, content in dashboard_files.items():
        # Escape single quotes in JSON for heredoc safety
        escaped = content.replace("'", "'\\''")
        dashboard_writes += f"""
cat > /etc/grafana/dashboards/{name} <<'DASHBOARD_{name.upper().replace('.','_').replace('-','_')}'
{content}
DASHBOARD_{name.upper().replace('.','_').replace('-','_')}
"""

    return f"""#!/bin/bash
set -e
exec > /var/log/canyougrab-provision.log 2>&1
echo "=== admin provision started at $(date -u) ==="

# --- Tailscale (FIRST — enables SSH debug access if provisioning fails) ---
curl -fsSL https://tailscale.com/install.sh | sh
echo 'net.ipv4.ip_forward = 1' >> /etc/sysctl.d/99-tailscale.conf
sysctl -p /etc/sysctl.d/99-tailscale.conf
tailscale up --auth-key={tailscale_auth_key} --ssh --hostname=admin --advertise-routes=10.108.0.0/20
echo "=== Tailscale connected (advertising VPC routes) ==="

# --- SSH hardening ---
sed -i 's/^#\\?MaxStartups.*/MaxStartups 50:30:200/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 50:30:200' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# No VPC hosts entries needed — admin uses CF DNS for service discovery

# --- System packages ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx git curl python3 python3-venv python3-pip apt-transport-https software-properties-common

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

# --- Prometheus ---
useradd -rs /bin/false prometheus 2>/dev/null || true
mkdir -p /opt/prometheus/data
cd /tmp
curl -sLO https://github.com/prometheus/prometheus/releases/download/v2.53.0/prometheus-2.53.0.linux-amd64.tar.gz
tar xf prometheus-2.53.0.linux-amd64.tar.gz
mv prometheus-2.53.0.linux-amd64/prometheus /opt/prometheus/
mv prometheus-2.53.0.linux-amd64/promtool /opt/prometheus/
chown -R prometheus:prometheus /opt/prometheus

# Write Prometheus config
cat > /opt/prometheus/prometheus.yml <<'PROMCFG'
{prometheus_yml}
PROMCFG

# Write alert rules
cat > /opt/prometheus/alert-rules.yml <<'ALERTRULES'
{alert_rules_yml}
ALERTRULES

# Write DO token for service discovery
mkdir -p /etc/prometheus
echo -n '{do_token}' > /etc/prometheus/do_token
chmod 600 /etc/prometheus/do_token
chown prometheus:prometheus /etc/prometheus/do_token

# Prometheus systemd
cat > /etc/systemd/system/prometheus.service <<'EOF'
[Unit]
Description=Prometheus Monitoring
After=network.target
[Service]
Type=simple
User=prometheus
ExecStart=/opt/prometheus/prometheus \\
    --config.file=/opt/prometheus/prometheus.yml \\
    --storage.tsdb.path=/opt/prometheus/data \\
    --storage.tsdb.retention.time=30d \\
    --web.listen-address=127.0.0.1:9090
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# --- Alertmanager ---
cd /tmp
curl -sLO https://github.com/prometheus/alertmanager/releases/download/v0.27.0/alertmanager-0.27.0.linux-amd64.tar.gz
tar xf alertmanager-0.27.0.linux-amd64.tar.gz
mkdir -p /opt/alertmanager/data
mv alertmanager-0.27.0.linux-amd64/alertmanager /opt/alertmanager/
mv alertmanager-0.27.0.linux-amd64/amtool /opt/alertmanager/
chown -R prometheus:prometheus /opt/alertmanager

cat > /opt/alertmanager/alertmanager.yml <<AMCFG
global:
  resolve_timeout: 5m
route:
  receiver: slack-alerts
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 1h
receivers:
  - name: slack-alerts
    slack_configs:
      - api_url: '{slack_url}'
        channel: '#ops-alerts'
        send_resolved: true
AMCFG

cat > /etc/systemd/system/alertmanager.service <<'EOF'
[Unit]
Description=Prometheus Alertmanager
After=network.target
[Service]
Type=simple
User=prometheus
ExecStart=/opt/alertmanager/alertmanager \\
    --config.file=/opt/alertmanager/alertmanager.yml \\
    --storage.path=/opt/alertmanager/data \\
    --web.listen-address=127.0.0.1:9093
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# --- Redis exporter ---
cd /tmp
curl -sLO https://github.com/oliver006/redis_exporter/releases/download/v1.63.0/redis_exporter-v1.63.0.linux-amd64.tar.gz
tar xf redis_exporter-v1.63.0.linux-amd64.tar.gz
mkdir -p /opt/redis_exporter
mv redis_exporter-v1.63.0.linux-amd64/redis_exporter /opt/redis_exporter/
useradd -rs /bin/false redis_exporter 2>/dev/null || true

mkdir -p /etc/canyougrab
cat > /etc/canyougrab/env <<REDISENV
VALKEY_HOST=private-db-valkey-nyc3-queue-do-user-34383636-0.g.db.ondigitalocean.com
VALKEY_PORT=25061
VALKEY_USERNAME=default
VALKEY_PASSWORD={valkey_pw}
REDISENV
chmod 600 /etc/canyougrab/env

cat > /etc/systemd/system/redis-exporter.service <<'EOF'
[Unit]
Description=Redis/Valkey Prometheus Exporter
After=network.target
[Service]
Type=simple
User=redis_exporter
EnvironmentFile=/etc/canyougrab/env
ExecStart=/opt/redis_exporter/redis_exporter \\
    --redis.addr=rediss://${{VALKEY_USERNAME}}:${{VALKEY_PASSWORD}}@${{VALKEY_HOST}}:${{VALKEY_PORT}} \\
    --check-keys=rq:queue:*
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# --- Grafana ---
curl -sf https://apt.grafana.com/gpg.key | gpg --dearmor -o /usr/share/keyrings/grafana-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/grafana-archive-keyring.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update -qq
apt-get install -y -qq grafana

# Grafana provisioning
mkdir -p /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards /etc/grafana/dashboards

cat > /etc/grafana/provisioning/datasources/prometheus.yml <<'GFDS'
{grafana_datasources}
GFDS

cat > /etc/grafana/provisioning/dashboards/default.yml <<'GFDP'
{grafana_dashboards_prov}
GFDP

# Write dashboard JSON files
{dashboard_writes}

# --- GitHub deploy key (for pulling repo updates) ---
mkdir -p /root/.ssh
echo '{deploy_key_b64}' | base64 -d > /root/.ssh/canyougrab-deploy
chmod 600 /root/.ssh/canyougrab-deploy
cat > /root/.ssh/config <<'SSHCONF'
Host github.com
    IdentityFile /root/.ssh/canyougrab-deploy
    StrictHostKeyChecking no
SSHCONF

# --- Clone repo ---
git clone git@github.com:ericismaking/canyougrab-api.git /opt/canyougrab-repo
cd /opt/canyougrab-repo
git checkout dev

# --- RQ metrics exporter (Python) ---
python3 -m venv /opt/canyougrab-admin/venv
/opt/canyougrab-admin/venv/bin/pip install -q redis rq prometheus_client

cat > /opt/canyougrab-admin/valkey.env <<VKENV
VALKEY_HOST=private-db-valkey-nyc3-queue-do-user-34383636-0.g.db.ondigitalocean.com
VALKEY_PORT=25061
VALKEY_USERNAME=default
VALKEY_PASSWORD={valkey_pw}
VKENV

cat > /etc/systemd/system/rq-metrics.service <<'EOF'
[Unit]
Description=CanYouGrab RQ Metrics Exporter (multi-environment)
After=network.target
[Service]
Type=simple
User=root
EnvironmentFile=/opt/canyougrab-admin/valkey.env
Environment=RQ_METRICS_PORT=9122
Environment=RQ_QUEUE_ENVIRONMENTS=dev:queue:jobs:dev,prod:queue:jobs:prod
Environment=RQ_HOST_ENVIRONMENTS=dev:dev,prod:prod
ExecStart=/opt/canyougrab-admin/venv/bin/python /opt/canyougrab-repo/scripts/rq_metrics_exporter.py
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# --- SSL certs (Cloudflare origin cert — admin is CF-proxied) ---
cat > /etc/ssl/cloudflare-origin-cert.pem <<'SSLCERT'
{ssl_cert}
SSLCERT
cat > /etc/ssl/cloudflare-origin-key.pem <<'SSLKEY'
{ssl_key}
SSLKEY
chmod 600 /etc/ssl/cloudflare-origin-key.pem

# --- Nginx ---
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-enabled/admin <<'NGINXADMIN'
server {{
    listen 80;
    server_name admin.canyougrab.it dev-admin.canyougrab.it;
    return 301 https://$host$request_uri;
}}
server {{
    listen 443 ssl;
    server_name admin.canyougrab.it dev-admin.canyougrab.it;
    ssl_certificate /etc/ssl/cloudflare-origin-cert.pem;
    ssl_certificate_key /etc/ssl/cloudflare-origin-key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {{
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
    location /api/live/ {{
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }}
    location /prometheus/ {{
        proxy_pass http://127.0.0.1:9090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
    location /alertmanager/ {{
        proxy_pass http://127.0.0.1:9093;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
NGINXADMIN
nginx -t && systemctl reload nginx

# --- Enable and start all services ---
systemctl daemon-reload
systemctl enable --now node_exporter prometheus alertmanager redis-exporter rq-metrics grafana-server nginx

echo "=== admin provision completed at $(date -u) ==="
mkdir -p /opt/canyougrab && touch /opt/canyougrab/.provision-complete
"""


# ---------------------------------------------------------------------------
# Admin Droplet
# ---------------------------------------------------------------------------
from tailscale_key import server_key

user_data = pulumi.Output.all(
    valkey_password, do_api_token, slack_webhook_url, server_key.key,
).apply(lambda s: build_admin_user_data(s[0], s[1], s[2], tailscale_auth_key=s[3]))

admin_droplet = do.Droplet(
    "admin",
    name="admin.canyougrab.it",
    image="ubuntu-24-04-x64",
    region=region,
    size=droplet_size,
    vpc_uuid=VPC_ID_OLD,
    ssh_keys=[ssh_key_fingerprint],
    monitoring=True,
    tags=["canyougrab-admin"],
    user_data=user_data,
)

# ---------------------------------------------------------------------------
# Cloudflare DNS (both CF-proxied)
# ---------------------------------------------------------------------------
cf_admin_dns = cf.DnsRecord(
    "admin-dns",
    zone_id=CF_ZONE_ID,
    name="admin.canyougrab.it",
    type="A",
    content=admin_droplet.ipv4_address,
    proxied=True,
    ttl=1,
)

cf_dev_admin_dns = cf.DnsRecord(
    "dev-admin-dns",
    zone_id=CF_ZONE_ID,
    name="dev-admin.canyougrab.it",
    type="A",
    content=admin_droplet.ipv4_address,
    proxied=True,
    ttl=1,
)

# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------
admin_firewall = do.Firewall(
    "admin-firewall",
    name="canyougrab-admin-fw",
    droplet_ids=[admin_droplet.id],
    inbound_rules=[
        # HTTPS (public — Grafana via CF proxy)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="443",
            source_addresses=["0.0.0.0/0", "::/0"]),
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="80",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # Tailscale direct connections (UDP 41641)
        do.FirewallInboundRuleArgs(
            protocol="udp", port_range="41641",
            source_addresses=["0.0.0.0/0", "::/0"]),
        # VPC internal (node exporter, Prometheus scraping)
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range="1-65535",
            source_addresses=[VPC_CIDR_OLD]),
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
health_check = command.local.Command(
    "admin-health-check",
    create=admin_droplet.ipv4_address.apply(
        lambda ip: (
            f"for i in $(seq 1 90); do "
            f"if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@admin "
            f"'curl -sf http://127.0.0.1:3000/api/health' 2>/dev/null; then exit 0; fi; "
            f"sleep 10; done; "
            f"echo 'TIMEOUT: admin health check failed after 15 minutes'; exit 1"
        )
    ),
    opts=pulumi.ResourceOptions(depends_on=[admin_droplet, cf_admin_dns]),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
pulumi.export("droplet_id", admin_droplet.id)
pulumi.export("droplet_name", admin_droplet.name)
pulumi.export("public_ip", admin_droplet.ipv4_address)
pulumi.export("private_ip", admin_droplet.ipv4_address_private)
pulumi.export("grafana_url", "https://admin.canyougrab.it")
pulumi.export("prometheus_url", "https://admin.canyougrab.it/prometheus")
pulumi.export("health_check", health_check.stdout)
