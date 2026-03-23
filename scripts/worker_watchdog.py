#!/usr/bin/env python3
"""
Worker watchdog — runs every 60s via systemd timer.

Three checks:
  1. Headcount: ensure WATCHDOG_WORKER_COUNT worker units are active
  2. Heartbeat: kill workers whose RQ heartbeat is stale (>5 min)
  3. Stuck jobs: fail jobs stuck in 'processing' longer than JOB_TIMEOUT
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta

import redis
from rq import Worker as RQWorker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [watchdog] %(message)s',
)
logger = logging.getLogger(__name__)

TARGET_WORKERS = int(os.environ.get('WATCHDOG_WORKER_COUNT', '3'))
HEARTBEAT_TIMEOUT = timedelta(minutes=5)
JOB_TIMEOUT = int(os.environ.get('JOB_TIMEOUT', '120'))  # seconds
SERVICE_TEMPLATE = 'canyougrab-worker@{}.service'


def _build_valkey_url() -> str:
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = os.environ.get('VALKEY_PORT', '25061')
    user = os.environ.get('VALKEY_USERNAME', 'default')
    pw = os.environ.get('VALKEY_PASSWORD', '')
    return f'rediss://{user}:{pw}@{host}:{port}'


def get_connections():
    """Return (app_conn, rq_conn) pair."""
    url = _build_valkey_url()
    app = redis.from_url(url, decode_responses=True)
    rq = redis.from_url(url, decode_responses=False)
    return app, rq


def systemctl(action: str, unit: str) -> bool:
    """Run a systemctl command. Returns True on success."""
    try:
        result = subprocess.run(
            ['systemctl', action, unit],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning('systemctl %s %s failed: %s', action, unit, result.stderr.strip())
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error('systemctl %s %s timed out', action, unit)
        return False


def is_unit_active(unit: str) -> bool:
    """Check if a systemd unit is active."""
    result = subprocess.run(
        ['systemctl', 'is-active', '--quiet', unit],
        capture_output=True, timeout=10,
    )
    return result.returncode == 0


def check_headcount() -> int:
    """Ensure TARGET_WORKERS worker units are active. Returns count of running workers."""
    running = 0
    for i in range(1, TARGET_WORKERS + 1):
        unit = SERVICE_TEMPLATE.format(i)
        if is_unit_active(unit):
            running += 1
        else:
            logger.warning('Worker %s is not active, restarting', unit)
            systemctl('restart', unit)
            if is_unit_active(unit):
                running += 1
                logger.info('Worker %s restarted successfully', unit)
            else:
                logger.error('Worker %s failed to restart', unit)
    return running


def check_heartbeats(rq_conn) -> int:
    """Check RQ worker heartbeats. Kill workers with stale heartbeats.
    Returns number of healthy workers found."""
    now = datetime.now(timezone.utc)
    workers = RQWorker.all(connection=rq_conn)
    healthy = 0
    stale_pids = []

    for w in workers:
        heartbeat = w.last_heartbeat
        if heartbeat is None:
            logger.warning('Worker %s has no heartbeat, pid=%s', w.name, w.pid)
            stale_pids.append(w.pid)
            continue

        # RQ stores heartbeat as a naive UTC datetime
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)

        age = now - heartbeat
        if age > HEARTBEAT_TIMEOUT:
            logger.warning(
                'Worker %s heartbeat stale (%s ago), pid=%s — killing',
                w.name, age, w.pid,
            )
            stale_pids.append(w.pid)
        else:
            healthy += 1

    # Kill stale workers — systemd will respawn them
    for pid in stale_pids:
        if pid:
            try:
                os.kill(pid, 9)  # SIGKILL
                logger.info('Sent SIGKILL to pid %s', pid)
            except ProcessLookupError:
                logger.info('pid %s already gone', pid)
            except PermissionError:
                logger.error('No permission to kill pid %s', pid)

    return healthy


def check_stuck_jobs(app_conn) -> int:
    """Find and fail jobs stuck in 'processing' state beyond JOB_TIMEOUT.
    Returns number of stuck jobs found."""
    now = datetime.now(timezone.utc)
    cursor = 0
    stuck = 0

    while True:
        cursor, keys = app_conn.scan(cursor, match='job:*', count=200)
        for key in keys:
            pipe = app_conn.pipeline(transaction=False)
            pipe.hget(key, 'status')
            pipe.hget(key, 'created_at')
            status, created_at = pipe.execute()

            if status != 'processing':
                continue

            if not created_at:
                continue

            try:
                created_dt = datetime.fromisoformat(created_at)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_secs = (now - created_dt).total_seconds()
            except (ValueError, TypeError):
                continue

            # Give 2x the job timeout as buffer (RQ has its own timeout + retries)
            if age_secs > JOB_TIMEOUT * 2:
                job_id = key.split(':', 1)[1] if ':' in key else key
                logger.warning(
                    'Job %s stuck in processing for %.0fs (limit %ds), failing it',
                    job_id[:8], age_secs, JOB_TIMEOUT * 2,
                )
                app_conn.hset(key, mapping={
                    'status': 'failed',
                    'error': f'Watchdog: stuck in processing for {int(age_secs)}s',
                })
                app_conn.expire(key, 3600)
                stuck += 1

        if cursor == 0:
            break

    return stuck


def main():
    logger.info('Watchdog running (target_workers=%d)', TARGET_WORKERS)

    app_conn, rq_conn = get_connections()

    # Verify Valkey connectivity
    try:
        app_conn.ping()
    except Exception as e:
        logger.error('Cannot connect to Valkey: %s', e)
        sys.exit(1)

    # 1. Headcount — ensure all worker units are running
    running = check_headcount()
    logger.info('Headcount: %d/%d workers running', running, TARGET_WORKERS)

    # 2. Heartbeat — check RQ worker heartbeats
    healthy = check_heartbeats(rq_conn)
    logger.info('Heartbeat: %d healthy RQ workers', healthy)

    # 3. Stuck jobs — fail jobs that exceeded timeout
    stuck = check_stuck_jobs(app_conn)
    if stuck:
        logger.warning('Stuck jobs: failed %d stuck jobs', stuck)
    else:
        logger.info('Stuck jobs: none found')

    logger.info('Watchdog complete')


if __name__ == '__main__':
    main()
