"""
Tailscale auth key for server droplets.

Creates a reusable pre-authorized key that all stacks use.
Non-ephemeral so nodes survive power cycles and reboots without
needing to re-authenticate. Stale nodes from destroyed droplets
are cleaned up by the pre_deploy_cleanup command in each stack.
"""

import pulumi
import pulumi_tailscale as tailscale

server_key = tailscale.TailnetKey("server-key",
    reusable=True,
    ephemeral=False,  # survives power cycles — cleanup handles stale nodes
    preauthorized=True,
    expiry=7776000,  # 90 days
    description="Pulumi-managed key for canyougrab server droplets",
    # TODO: add tags=["tag:server"] once ACL policy defines tag:server
)
