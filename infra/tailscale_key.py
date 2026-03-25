"""
Tailscale auth key for server droplets.

Creates a reusable ephemeral pre-authorized key that all stacks use.
Ephemeral means destroyed droplets auto-deregister from the tailnet.
"""

import pulumi
import pulumi_tailscale as tailscale

server_key = tailscale.TailnetKey("server-key",
    reusable=True,
    ephemeral=True,
    preauthorized=True,
    expiry=7776000,  # 90 days
    description="Pulumi-managed key for canyougrab server droplets",
    # TODO: add tags=["tag:server"] once ACL policy defines tag:server
)
