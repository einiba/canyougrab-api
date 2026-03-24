"""
CanYouGrab.it Infrastructure — Pulumi Program

Stacks:
  - dev:   API droplet for dev-api.canyougrab.it
  - prod:  API droplet for api.canyougrab.it
  - admin: Monitoring stack (Prometheus, Grafana, Alertmanager)

Usage:
    pulumi stack select dev && pulumi up
    pulumi stack select prod && pulumi up
    pulumi stack select admin && pulumi up
"""

import pulumi

stack = pulumi.get_stack()

if stack in ("dev", "prod"):
    import api_stack  # noqa: F401
elif stack == "admin":
    import admin_stack  # noqa: F401
elif stack == "rust-whois":
    import rust_whois_stack  # noqa: F401
else:
    raise ValueError(f"Unknown stack: {stack}. Expected: dev, prod, admin, or rust-whois")
