"""
Tailscale lifecycle helpers for blue-green deploys.

- pre_deploy_cleanup: removes stale nodes before creating a new droplet
- post_deploy_approve_routes: approves VPC subnet routes after deploy
"""

import pulumi
import pulumi_command as command
from pathlib import Path

config = pulumi.Config("tailscale")
ts_api_key = config.require_secret("apiKey")

SCRIPTS_DIR = Path(__file__).parent / "scripts"


def pre_deploy_cleanup(hostname: str) -> command.local.Command:
    """Remove stale Tailscale nodes matching hostname before deploy."""
    return command.local.Command(
        f"ts-cleanup-{hostname}",
        create=ts_api_key.apply(
            lambda key: f"TS_API_KEY={key} bash {SCRIPTS_DIR}/ts-cleanup.sh {hostname}"
        ),
    )


def post_deploy_approve_routes(
    hostname: str,
    depends_on: list,
) -> command.local.Command:
    """Approve advertised routes after deploy."""
    return command.local.Command(
        f"ts-routes-{hostname}",
        create=ts_api_key.apply(
            lambda key: f"TS_API_KEY={key} bash {SCRIPTS_DIR}/ts-approve-routes.sh {hostname}"
        ),
        opts=pulumi.ResourceOptions(depends_on=depends_on),
    )
