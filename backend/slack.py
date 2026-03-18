"""
Slack webhook notifications for canyougrab.it events.
"""

import logging
import os
import threading
import urllib.request
import json

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.environ.get("SLACK_NEW_USER_WEBHOOK_URL", "")


def _post_webhook(url: str, payload: dict) -> None:
    """Fire-and-forget POST to a Slack webhook. Runs in a thread to avoid blocking."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                logger.warning("Slack webhook returned %s", resp.status)
    except Exception as e:
        logger.warning("Slack webhook failed: %s", e)


def notify_new_user(email: str, name: str = "", auth_provider: str = "") -> None:
    """Send a 'New User Created' message to the #new-users Slack channel."""
    if not SLACK_WEBHOOK_URL:
        logger.debug("SLACK_NEW_USER_WEBHOOK_URL not set, skipping notification")
        return

    display = name or email or "unknown"
    provider = auth_provider or "unknown"
    text = f":wave: *New User Created*\n• Email: `{email}`\n• Name: {display}\n• Provider: {provider}"

    threading.Thread(
        target=_post_webhook,
        args=(SLACK_WEBHOOK_URL, {"text": text}),
        daemon=True,
    ).start()
