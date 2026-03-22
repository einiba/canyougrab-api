#!/usr/bin/env python3
"""
DigitalOcean worker auto-scaler for the canyougrab-api job queue.

Periodically checks RQ queue depth and scales worker droplets up/down
via the DigitalOcean API.  Runs as a systemd service on the API host.

Usage:
    python scripts/autoscaler.py            # production mode
    python scripts/autoscaler.py --dry-run  # log decisions without calling DO API
"""

import os
import sys
import time
import json
import signal
import logging
import argparse
from datetime import datetime, timezone

import redis
import httpx
from rq import Queue

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [autoscaler] %(message)s',
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────

DO_API_TOKEN = os.environ.get('DO_API_TOKEN', '')
DO_API_BASE = 'https://api.digitalocean.com/v2'

# Queue settings
QUEUE_NAME = os.environ.get('VALKEY_QUEUE_NAME', 'canyougrab-jobs')

# Scaling thresholds
MIN_WORKERS = int(os.environ.get('AUTOSCALER_MIN_WORKERS', '1'))
MAX_WORKERS = int(os.environ.get('AUTOSCALER_MAX_WORKERS', '5'))
SCALE_UP_THRESHOLD = int(os.environ.get('AUTOSCALER_SCALE_UP_THRESHOLD', '50'))
SCALE_DOWN_IDLE_MINUTES = int(os.environ.get('AUTOSCALER_SCALE_DOWN_IDLE_MINUTES', '10'))
COOLDOWN_MINUTES = int(os.environ.get('AUTOSCALER_COOLDOWN_MINUTES', '5'))
CHECK_INTERVAL = int(os.environ.get('AUTOSCALER_CHECK_INTERVAL', '60'))

# Scale-up requires consecutive checks above threshold to avoid reacting to spikes
SCALE_UP_CONSECUTIVE_CHECKS = 2

# Droplet creation settings
DO_WORKER_SNAPSHOT_ID = os.environ.get('DO_WORKER_SNAPSHOT_ID', '')
DO_WORKER_REGION = os.environ.get('DO_WORKER_REGION', 'nyc3')
DO_WORKER_SIZE = os.environ.get('DO_WORKER_SIZE', 's-1vcpu-1gb')
DO_VPC_UUID = os.environ.get('DO_VPC_UUID', '')
DO_SSH_KEY_IDS = os.environ.get('DO_SSH_KEY_IDS', '')  # comma-separated
DO_WORKER_TAG = os.environ.get('DO_WORKER_TAG', 'canyougrab-worker')

# ── State ─────────────────────────────────────────────────────────

running = True
last_scale_action_at = None  # datetime of last scale up/down
consecutive_above_threshold = 0
idle_since = None  # datetime when queue first became empty


def handle_signal(signum, _frame):
    global running
    logger.info('Received signal %d, shutting down...', signum)
    running = False


# ── Valkey connection ─────────────────────────────────────────────

def build_valkey_connection() -> redis.Redis:
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = os.environ.get('VALKEY_PORT', '25061')
    user = os.environ.get('VALKEY_USERNAME', 'default')
    pw = os.environ.get('VALKEY_PASSWORD', '')
    url = f'rediss://{user}:{pw}@{host}:{port}'
    return redis.from_url(url, decode_responses=False)


# ── DigitalOcean API helpers ──────────────────────────────────────

def do_headers() -> dict:
    return {
        'Authorization': f'Bearer {DO_API_TOKEN}',
        'Content-Type': 'application/json',
    }


def list_worker_droplets() -> list[dict]:
    """List all droplets tagged as workers."""
    resp = httpx.get(
        f'{DO_API_BASE}/droplets',
        headers=do_headers(),
        params={'tag_name': DO_WORKER_TAG, 'per_page': 100},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get('droplets', [])


def create_worker_droplet(dry_run: bool) -> dict | None:
    """Create a new worker droplet from snapshot."""
    name = f'canyougrab-worker-{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")}'

    ssh_keys = [k.strip() for k in DO_SSH_KEY_IDS.split(',') if k.strip()]

    payload = {
        'name': name,
        'region': DO_WORKER_REGION,
        'size': DO_WORKER_SIZE,
        'image': DO_WORKER_SNAPSHOT_ID,
        'tags': [DO_WORKER_TAG],
        'monitoring': True,
    }
    if DO_VPC_UUID:
        payload['vpc_uuid'] = DO_VPC_UUID
    if ssh_keys:
        payload['ssh_keys'] = ssh_keys

    if dry_run:
        logger.info('[DRY RUN] Would create droplet: %s', json.dumps(payload, indent=2))
        return None

    resp = httpx.post(
        f'{DO_API_BASE}/droplets',
        headers=do_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    droplet = resp.json().get('droplet', {})
    logger.info('Created worker droplet: id=%s name=%s', droplet.get('id'), droplet.get('name'))
    return droplet


def destroy_worker_droplet(droplet_id: int, droplet_name: str, dry_run: bool):
    """Destroy a worker droplet."""
    if dry_run:
        logger.info('[DRY RUN] Would destroy droplet: id=%s name=%s', droplet_id, droplet_name)
        return

    resp = httpx.delete(
        f'{DO_API_BASE}/droplets/{droplet_id}',
        headers=do_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    logger.info('Destroyed worker droplet: id=%s name=%s', droplet_id, droplet_name)


# ── Scaling logic ─────────────────────────────────────────────────

def cooldown_active() -> bool:
    if last_scale_action_at is None:
        return False
    elapsed = (datetime.now(timezone.utc) - last_scale_action_at).total_seconds()
    return elapsed < (COOLDOWN_MINUTES * 60)


def check_and_scale(queue: Queue, dry_run: bool):
    """Core scaling loop iteration."""
    global consecutive_above_threshold, idle_since, last_scale_action_at

    depth = queue.count

    # Track consecutive checks above threshold
    if depth > SCALE_UP_THRESHOLD:
        consecutive_above_threshold += 1
        idle_since = None
    elif depth == 0:
        consecutive_above_threshold = 0
        if idle_since is None:
            idle_since = datetime.now(timezone.utc)
    else:
        consecutive_above_threshold = 0
        idle_since = None

    try:
        workers = list_worker_droplets()
    except Exception:
        logger.exception('Failed to list worker droplets')
        return

    current_count = len(workers)
    logger.info('Queue depth=%d, workers=%d, consecutive_above=%d, idle_since=%s',
                depth, current_count, consecutive_above_threshold,
                idle_since.isoformat() if idle_since else 'N/A')

    # ── Scale up ──────────────────────────────────────────────
    if (consecutive_above_threshold >= SCALE_UP_CONSECUTIVE_CHECKS
            and current_count < MAX_WORKERS
            and not cooldown_active()):
        logger.info('SCALE UP: depth=%d > %d for %d checks, workers=%d/%d',
                     depth, SCALE_UP_THRESHOLD, consecutive_above_threshold,
                     current_count, MAX_WORKERS)
        try:
            create_worker_droplet(dry_run)
            last_scale_action_at = datetime.now(timezone.utc)
            consecutive_above_threshold = 0
        except Exception:
            logger.exception('Failed to create worker droplet')

    # ── Scale down ────────────────────────────────────────────
    elif (idle_since is not None
          and current_count > MIN_WORKERS
          and not cooldown_active()):
        idle_minutes = (datetime.now(timezone.utc) - idle_since).total_seconds() / 60
        if idle_minutes >= SCALE_DOWN_IDLE_MINUTES:
            # Pick the most recently created extra worker to destroy
            extras = sorted(workers, key=lambda d: d.get('created_at', ''), reverse=True)
            target = extras[0]
            logger.info('SCALE DOWN: queue empty for %.1f min, destroying %s (id=%s)',
                         idle_minutes, target.get('name'), target.get('id'))
            try:
                destroy_worker_droplet(target['id'], target.get('name', ''), dry_run)
                last_scale_action_at = datetime.now(timezone.utc)
                idle_since = datetime.now(timezone.utc)  # reset to give next check a fresh window
            except Exception:
                logger.exception('Failed to destroy worker droplet')


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='CanYouGrab worker auto-scaler')
    parser.add_argument('--dry-run', action='store_true', help='Log decisions without calling DO API')
    args = parser.parse_args()

    if not DO_API_TOKEN and not args.dry_run:
        logger.error('DO_API_TOKEN is required (or use --dry-run)')
        sys.exit(1)

    if not DO_WORKER_SNAPSHOT_ID:
        logger.warning('DO_WORKER_SNAPSHOT_ID not set — scale-up will fail')

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    conn = build_valkey_connection()
    conn.ping()
    logger.info('Valkey connected')

    queue = Queue(QUEUE_NAME, connection=conn)

    logger.info('Autoscaler started (dry_run=%s, min=%d, max=%d, '
                'up_threshold=%d, down_idle=%dm, cooldown=%dm, interval=%ds)',
                args.dry_run, MIN_WORKERS, MAX_WORKERS,
                SCALE_UP_THRESHOLD, SCALE_DOWN_IDLE_MINUTES,
                COOLDOWN_MINUTES, CHECK_INTERVAL)

    while running:
        try:
            check_and_scale(queue, args.dry_run)
        except Exception:
            logger.exception('Unexpected error in autoscaler loop')
        time.sleep(CHECK_INTERVAL)

    logger.info('Autoscaler shutting down')


if __name__ == '__main__':
    main()
