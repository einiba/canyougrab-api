"""Shared configuration for all Pulumi stacks."""

import pulumi
from pathlib import Path

# Cloudflare zone
CF_ZONE_ID = "2f18d0a54e25e07c73667df397bd1c5f"

# VPCs (old account default, new account dev)
VPC_ID_OLD = "159def95-d05a-4ab9-9618-b670ceada0bb"
VPC_ID_NEW = "0c9c085d-eaeb-417d-9a51-98ee015f2b21"
VPC_CIDR_OLD = "10.108.0.0/20"
VPC_CIDR_NEW = "10.120.0.0/20"

# Service hostnames — CF DNS → VPC private IPs
# Prod uses the shared instances (old account)
UNBOUND_HOSTNAME = "unbound.canyougrab.it"
RUST_WHOIS_HOSTNAME = "rust-whois.canyougrab.it"
# Dev uses dedicated instances (new account)
DEV_UNBOUND_HOSTNAME = "dev-unbound.canyougrab.it"
DEV_RUST_WHOIS_HOSTNAME = "dev-rust-whois.canyougrab.it"

# Legacy IPs (for reference only)
UNBOUND_IP = "10.108.0.5"
RUST_WHOIS_IP = "10.108.0.8"

# Repo root (one level up from infra/)
REPO_ROOT = Path(__file__).parent.parent

# Common paths
DEPLOY_KEY_PATH = REPO_ROOT / "config" / "env" / "github-deploy-key"
SSL_CERT_PATH = REPO_ROOT / "config" / "env" / "cloudflare-origin-cert.pem"
SSL_KEY_PATH = REPO_ROOT / "config" / "env" / "cloudflare-origin-key.pem"
