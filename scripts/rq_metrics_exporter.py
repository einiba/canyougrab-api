#!/usr/bin/env python3
"""
Prometheus exporter for Valkey job queue metrics.

Queues are plain Valkey lists (RPUSH/BLPOP) — not RQ queues.
Queue depth is measured with LLEN on each list.
Worker counts are not tracked here (use K8s metrics instead).

Configure via:
    RQ_QUEUE_ENVIRONMENTS=prod:queue:jobs:prod,prod-rdap:queue:rdap:prod,prod-whois:queue:whois:prod
"""

import os
import time
import logging

import redis
from prometheus_client import start_http_server, Gauge, Histogram, Info

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [rq-metrics] %(message)s',
)
logger = logging.getLogger(__name__)

METRICS_PORT = int(os.environ.get('RQ_METRICS_PORT', '9122'))
SCRAPE_INTERVAL = int(os.environ.get('RQ_METRICS_INTERVAL', '15'))


def parse_queue_environments():
    """Parse RQ_QUEUE_ENVIRONMENTS into {env_name: queue_list_key} map."""
    env_str = os.environ.get('RQ_QUEUE_ENVIRONMENTS', '')
    if not env_str:
        queue_name = os.environ.get('VALKEY_QUEUE_NAME', 'queue:jobs:prod')
        return {'default': queue_name}
    mapping = {}
    for entry in env_str.split(','):
        parts = entry.strip().split(':', 1)
        if len(parts) == 2:
            mapping[parts[0]] = parts[1]
    return mapping


# Prometheus metrics
queue_depth = Gauge('canyougrab_queue_depth', 'Pending jobs in queue list', ['environment'])
processing_time = Histogram(
    'canyougrab_processing_time_ms',
    'Job processing time in milliseconds',
    ['environment'],
    buckets=[100, 250, 500, 1000, 2500, 5000, 10000, 25000, 45000, 60000, 90000, 120000],
)
exporter_info = Info('canyougrab_rq_exporter', 'Queue metrics exporter metadata')


def build_connection() -> redis.Redis:
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = os.environ.get('VALKEY_PORT', '25061')
    user = os.environ.get('VALKEY_USERNAME', 'default')
    pw = os.environ.get('VALKEY_PASSWORD', '')
    url = f'rediss://{user}:{pw}@{host}:{port}'
    return redis.from_url(url, decode_responses=False)


def collect_metrics(conn: redis.Redis, queue_envs: dict[str, str]):
    """Read queue depths via LLEN and drain processing_times list."""
    total_depth = 0

    for env_name, list_key in queue_envs.items():
        try:
            depth = conn.llen(list_key)
            queue_depth.labels(environment=env_name).set(depth)
            total_depth += depth
        except Exception:
            logger.exception('Error collecting queue depth for %s (%s)', env_name, list_key)

    queue_depth.labels(environment='all').set(total_depth)

    # Drain processing times pushed by workers
    while True:
        val = conn.rpop('metrics:processing_times')
        if val is None:
            break
        try:
            processing_time.labels(environment='all').observe(float(val))
        except (ValueError, TypeError):
            pass


def main():
    conn = build_connection()
    conn.ping()
    logger.info('Valkey connected')

    queue_envs = parse_queue_environments()
    for env_name, list_key in queue_envs.items():
        logger.info('Watching list %s → environment %s', list_key, env_name)

    exporter_info.info({
        'queues': ','.join(f'{k}={v}' for k, v in queue_envs.items()),
    })

    start_http_server(METRICS_PORT, addr='0.0.0.0')
    logger.info('Serving metrics on 0.0.0.0:%d/metrics (interval=%ds)', METRICS_PORT, SCRAPE_INTERVAL)

    while True:
        collect_metrics(conn, queue_envs)
        time.sleep(SCRAPE_INTERVAL)


if __name__ == '__main__':
    main()
