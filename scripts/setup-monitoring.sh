#!/usr/bin/env bash
# setup-monitoring.sh — Install Prometheus, Grafana, Alertmanager, and redis_exporter
# on a DigitalOcean Ubuntu droplet.  Run once after the RQ migration is deployed.
#
# Usage:  sudo bash scripts/setup-monitoring.sh
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/canyougrab-repo}"

# ── Versions ──────────────────────────────────────────────────────
PROMETHEUS_VERSION="2.53.4"
ALERTMANAGER_VERSION="0.28.1"
REDIS_EXPORTER_VERSION="1.67.0"

ARCH="linux-amd64"

echo "==> Installing monitoring stack"

# ── 1. Prometheus ─────────────────────────────────────────────────
if [ ! -f /opt/prometheus/prometheus ]; then
  echo "  -> Installing Prometheus ${PROMETHEUS_VERSION}"
  cd /tmp
  curl -sSLO "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.${ARCH}.tar.gz"
  tar xzf "prometheus-${PROMETHEUS_VERSION}.${ARCH}.tar.gz"
  mkdir -p /opt/prometheus/data
  cp "prometheus-${PROMETHEUS_VERSION}.${ARCH}/prometheus" /opt/prometheus/
  cp "prometheus-${PROMETHEUS_VERSION}.${ARCH}/promtool" /opt/prometheus/
  rm -rf "prometheus-${PROMETHEUS_VERSION}.${ARCH}" "prometheus-${PROMETHEUS_VERSION}.${ARCH}.tar.gz"
else
  echo "  -> Prometheus already installed"
fi

# Copy config
cp "$REPO_DIR/config/prometheus/prometheus.yml" /opt/prometheus/prometheus.yml
cp "$REPO_DIR/config/prometheus/alert-rules.yml" /opt/prometheus/alert-rules.yml

# Create user if needed
id -u prometheus &>/dev/null || useradd --system --no-create-home prometheus
chown -R prometheus:prometheus /opt/prometheus

# Install systemd unit
cp "$REPO_DIR/config/systemd/prometheus.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable prometheus
systemctl restart prometheus
echo "  -> Prometheus running on :9090"

# ── 2. Alertmanager ───────────────────────────────────────────────
if [ ! -f /opt/alertmanager/alertmanager ]; then
  echo "  -> Installing Alertmanager ${ALERTMANAGER_VERSION}"
  cd /tmp
  curl -sSLO "https://github.com/prometheus/alertmanager/releases/download/v${ALERTMANAGER_VERSION}/alertmanager-${ALERTMANAGER_VERSION}.${ARCH}.tar.gz"
  tar xzf "alertmanager-${ALERTMANAGER_VERSION}.${ARCH}.tar.gz"
  mkdir -p /opt/alertmanager/data
  cp "alertmanager-${ALERTMANAGER_VERSION}.${ARCH}/alertmanager" /opt/alertmanager/
  rm -rf "alertmanager-${ALERTMANAGER_VERSION}.${ARCH}" "alertmanager-${ALERTMANAGER_VERSION}.${ARCH}.tar.gz"
else
  echo "  -> Alertmanager already installed"
fi

# Copy config — IMPORTANT: edit the webhook URL before running this!
cp "$REPO_DIR/config/prometheus/alertmanager.yml" /opt/alertmanager/alertmanager.yml
chown -R prometheus:prometheus /opt/alertmanager

cp "$REPO_DIR/config/systemd/alertmanager.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable alertmanager
systemctl restart alertmanager
echo "  -> Alertmanager running on :9093"

# ── 3. redis_exporter ────────────────────────────────────────────
if [ ! -f /opt/redis_exporter/redis_exporter ]; then
  echo "  -> Installing redis_exporter ${REDIS_EXPORTER_VERSION}"
  cd /tmp
  curl -sSLO "https://github.com/oliver006/redis_exporter/releases/download/v${REDIS_EXPORTER_VERSION}/redis_exporter-v${REDIS_EXPORTER_VERSION}.${ARCH}.tar.gz"
  tar xzf "redis_exporter-v${REDIS_EXPORTER_VERSION}.${ARCH}.tar.gz"
  mkdir -p /opt/redis_exporter
  cp "redis_exporter-v${REDIS_EXPORTER_VERSION}.${ARCH}/redis_exporter" /opt/redis_exporter/
  rm -rf "redis_exporter-v${REDIS_EXPORTER_VERSION}.${ARCH}" "redis_exporter-v${REDIS_EXPORTER_VERSION}.${ARCH}.tar.gz"
else
  echo "  -> redis_exporter already installed"
fi

id -u redis_exporter &>/dev/null || useradd --system --no-create-home redis_exporter

cp "$REPO_DIR/config/systemd/redis-exporter.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable redis-exporter
systemctl restart redis-exporter
echo "  -> redis_exporter running on :9121"

# ── 4. Grafana ────────────────────────────────────────────────────
if ! dpkg -l grafana &>/dev/null; then
  echo "  -> Installing Grafana via apt"
  apt-get install -y apt-transport-https software-properties-common
  mkdir -p /etc/apt/keyrings/
  curl -sSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
  apt-get update
  apt-get install -y grafana
else
  echo "  -> Grafana already installed"
fi

# Provision datasource and dashboards
mkdir -p /etc/grafana/provisioning/datasources
mkdir -p /etc/grafana/provisioning/dashboards
mkdir -p /etc/grafana/dashboards

cp "$REPO_DIR/config/grafana/provisioning/datasources/prometheus.yml" /etc/grafana/provisioning/datasources/
cp "$REPO_DIR/config/grafana/provisioning/dashboards/default.yml" /etc/grafana/provisioning/dashboards/
cp "$REPO_DIR/config/grafana/dashboards/canyougrab-queue.json" /etc/grafana/dashboards/

systemctl daemon-reload
systemctl enable grafana-server
systemctl restart grafana-server
echo "  -> Grafana running on :3000 (default login: admin/admin)"

# ── 5. RQ metrics exporter ───────────────────────────────────────
cp "$REPO_DIR/config/systemd/rq-metrics.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable rq-metrics
systemctl restart rq-metrics
echo "  -> RQ metrics exporter running on :9122"

# ── 6. Autoscaler (optional — enable manually) ───────────────────
cp "$REPO_DIR/config/systemd/canyougrab-autoscaler.service" /etc/systemd/system/
systemctl daemon-reload
# NOT enabled by default — requires DO_API_TOKEN and snapshot config
echo "  -> Autoscaler service installed (not enabled — configure env vars then: systemctl enable --now canyougrab-autoscaler)"

echo ""
echo "==> Monitoring stack installed!"
echo ""
echo "Next steps:"
echo "  1. Edit /opt/alertmanager/alertmanager.yml — replace SLACK_WEBHOOK_URL_HERE with your Slack webhook"
echo "  2. Restart Alertmanager: systemctl restart alertmanager"
echo "  3. Open Grafana at http://<droplet-ip>:3000 (admin/admin)"
echo "  4. For autoscaler: set DO_API_TOKEN and DO_WORKER_SNAPSHOT_ID in /etc/canyougrab/env"
echo "     then: systemctl enable --now canyougrab-autoscaler"
echo ""
echo "Verify:"
echo "  curl -s localhost:9090/-/healthy     # Prometheus"
echo "  curl -s localhost:9093/-/healthy     # Alertmanager"
echo "  curl -s localhost:9121/metrics | head # redis_exporter"
echo "  curl -s localhost:9122/metrics | head # RQ metrics"
