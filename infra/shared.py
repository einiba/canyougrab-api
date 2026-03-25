"""Shared configuration for all Pulumi stacks."""

import pulumi
from pathlib import Path

# Cloudflare zone
CF_ZONE_ID = "2f18d0a54e25e07c73667df397bd1c5f"

# VPC
VPC_ID = "159def95-d05a-4ab9-9618-b670ceada0bb"
VPC_CIDR = "10.108.0.0/20"

# VPC-internal services
UNBOUND_IP = "10.108.0.5"  # static — unbound is not managed by Pulumi yet
RUST_WHOIS_IP = "10.108.0.8"  # legacy — use RUST_WHOIS_HOSTNAME for new deploys
RUST_WHOIS_HOSTNAME = "rust-whois.canyougrab.it"  # CF DNS → VPC private IP

# Repo root (one level up from infra/)
REPO_ROOT = Path(__file__).parent.parent

# Common paths
DEPLOY_KEY_PATH = REPO_ROOT / "config" / "env" / "github-deploy-key"
SSL_CERT_PATH = REPO_ROOT / "config" / "env" / "cloudflare-origin-cert.pem"
SSL_KEY_PATH = REPO_ROOT / "config" / "env" / "cloudflare-origin-key.pem"
