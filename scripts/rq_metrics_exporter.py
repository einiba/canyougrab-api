#!/usr/bin/env python3
"""
Prometheus exporter for RQ queue metrics — multi-environment.

All workers share the same Valkey queue, so we distinguish environments
by worker hostname. Configure via RQ_HOST_ENVIRONMENTS:

    RQ_HOST_ENVIRONMENTS=dev:canyougrab-dev,dev:canyougrab-dev-green,prod:api

Each entry is "env_name:hostname_prefix". Workers whose hostname starts
with the prefix are counted under that environment. Unmatched workers
go under "unknown".

Run as a systemd service on the admin server:
    python scripts/rq_metrics_exporter.py
"""

import os
import time
import logging

import redis
from rq import Queue, Worker
from prometheus_client import start_http_server, Gauge, Histogram, Info

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [rq-metrics] %(message)s',
)
logger = logging.getLogger(__name__)

METRICS_PORT = int(os.environ.get('RQ_METRICS_PORT', '9122'))
SCRAPE_INTERVAL = int(os.environ.get('RQ_METRICS_INTERVAL', '15'))
QUEUE_NAME = os.environ.get('VALKEY_QUEUE_NAME', 'canyougrab-jobs')


def parse_host_environments():
    """Parse RQ_HOST_ENVIRONMENTS into {hostname_prefix: env_name} map."""
    env_str = os.environ.get('RQ_HOST_ENVIRONMENTS', '')
    if not env_str:
        return {}
    mapping = {}
    for entry in env_str.split(','):
        entry = entry.strip()
        parts = entry.split(':', 1)
        if len(parts) == 2:
            env_name, host_prefix = parts
            mapping[host_prefix] = env_name
        else:
            logger.warning('Invalid host mapping: %s (expected env:hostname)', entry)
    return mapping


def classify_worker(hostname: str, host_map: dict[str, str]) -> str:
    """Return environment name for a worker based on its hostname."""
    # Try exact match first, then prefix match (longest prefix wins)
    if hostname in host_map:
        return host_map[hostname]
    best_match = ''
    best_env = 'unknown'
    for prefix, env_name in host_map.items():
        if hostname.startswith(prefix) and len(prefix) > len(best_match):
            best_match = prefix
            best_env = env_name
    return best_env


# Prometheus metrics — all labeled with environment
queue_depth = Gauge('canyougrab_queue_depth', 'Pending jobs in RQ queue', ['environment'])
workers_active = Gauge('canyougrab_workers_active', 'Active RQ workers', ['environment'])
workers_busy = Gauge('canyougrab_workers_busy', 'Workers currently processing a job', ['environment'])
failed_jobs = Gauge('canyougrab_failed_jobs_total', 'Jobs in the failed job registry', ['environment'])
scheduled_jobs = Gauge('canyougrab_scheduled_jobs', 'Jobs in the scheduled registry', ['environment'])
started_jobs = Gauge('canyougrab_started_jobs', 'Currently executing jobs', ['environment'])
processing_time = Histogram(
    'canyougrab_processing_time_ms',
    'Job processing time in milliseconds',
    ['environment'],
    buckets=[100, 250, 500, 1000, 2500, 5000, 10000, 25000, 45000, 60000, 90000, 120000],
)
exporter_info = Info('canyougrab_rq_exporter', 'RQ metrics exporter metadata')


def build_connection() -> redis.Redis:
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = os.environ.get('VALKEY_PORT', '25061')
    user = os.environ.get('VALKEY_USERNAME', 'default')
    pw = os.environ.get('VALKEY_PASSWORD', '')
    url = f'rediss://{user}:{pw}@{host}:{port}'
    return redis.from_url(url, decode_responses=False)


def collect_metrics(conn: redis.Redis, queue: Queue, host_map: dict[str, str], known_envs: set[str]):
    """Read RQ state and update Prometheus gauges, grouped by environment."""
    try:
        all_workers = Worker.all(connection=conn)
    except redis.ConnectionError:
        logger.warning('Lost Valkey connection, will retry next cycle')
        return
    except Exception:
        logger.exception('Error fetching workers')
        return

    # Count workers per environment
    env_active: dict[str, int] = {e: 0 for e in known_envs}
    env_busy: dict[str, int] = {e: 0 for e in known_envs}

    for w in all_workers:
        env = classify_worker(w.hostname, host_map)
        env_active[env] = env_active.get(env, 0) + 1
        if w.get_state() == 'busy':
            env_busy[env] = env_busy.get(env, 0) + 1

    # Update worker gauges
    for env in set(list(env_active.keys()) + list(known_envs)):
        workers_active.labels(environment=env).set(env_active.get(env, 0))
        workers_busy.labels(environment=env).set(env_busy.get(env, 0))

    # Queue-level metrics (shared queue, report under each known env)
    # Since all environments share one queue, report queue depth once as "all"
    # and also per-env so the dashboard variable works
    depth = queue.count
    failed_count = len(queue.failed_job_registry)
    scheduled_count = len(queue.scheduled_job_registry)
    started_count = len(queue.started_job_registry)

    for env in known_envs:
        queue_depth.labels(environment=env).set(depth)
        failed_jobs.labels(environment=env).set(failed_count)
        scheduled_jobs.labels(environment=env).set(scheduled_count)
        started_jobs.labels(environment=env).set(started_count)

    # Drain processing times (no per-env breakdown available yet)
    while True:
        val = conn.rpop('metrics:processing_times')
        if val is None:
            break
        try:
            # Report under "all" since we can't attribute to a specific env
            processing_time.labels(environment='all').observe(float(val))
        except (ValueError, TypeError):
            pass


def main():
    conn = build_connection()
    conn.ping()
    logger.info('Valkey connected')

    queue = Queue(QUEUE_NAME, connection=conn)
    host_map = parse_host_environments()

    # Determine known environments from config
    known_envs = set(host_map.values()) if host_map else {'default'}
    known_envs.add('all')  # Always have an "all" bucket for aggregate metrics

    logger.info('Host-to-environment mapping: %s', host_map)
    logger.info('Known environments: %s', known_envs)

    exporter_info.info({
        'queue_name': QUEUE_NAME,
        'environments': ','.join(sorted(known_envs)),
    })

    start_http_server(METRICS_PORT, addr='127.0.0.1')
    logger.info(
        'Serving metrics on 127.0.0.1:%d/metrics (interval=%ds)',
        METRICS_PORT, SCRAPE_INTERVAL,
    )

    while True:
        collect_metrics(conn, queue, host_map, known_envs)
        time.sleep(SCRAPE_INTERVAL)


if __name__ == '__main__':
    main()
