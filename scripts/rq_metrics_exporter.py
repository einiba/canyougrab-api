#!/usr/bin/env python3
"""
Prometheus exporter for RQ queue metrics — multi-environment.

Watches multiple RQ queues (dev, prod, etc.) on the same Valkey instance
and exposes per-environment metrics with an `environment` label.

Environment config via RQ_ENVIRONMENTS (comma-separated):
    RQ_ENVIRONMENTS=dev:queue:jobs:dev,prod:queue:jobs:prod

Each entry is "name:queue_name". Metrics get labeled {environment="name"}.

Run as a systemd service on the admin server:
    python scripts/rq_metrics_exporter.py
"""

import os
import sys
import time
import logging

import redis
from rq import Queue, Worker
from rq.job import JobStatus
from prometheus_client import start_http_server, Gauge, Histogram, Info

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [rq-metrics] %(message)s',
)
logger = logging.getLogger(__name__)

METRICS_PORT = int(os.environ.get('RQ_METRICS_PORT', '9122'))
SCRAPE_INTERVAL = int(os.environ.get('RQ_METRICS_INTERVAL', '15'))

# Parse environment config: "dev:queue:jobs:dev,prod:queue:jobs:prod"
# Falls back to single-environment mode for backward compatibility.
def parse_environments():
    env_str = os.environ.get('RQ_ENVIRONMENTS', '')
    if env_str:
        envs = {}
        for entry in env_str.split(','):
            entry = entry.strip()
            # Format: "name:queue_name" — split on first colon only
            parts = entry.split(':', 1)
            if len(parts) == 2:
                envs[parts[0]] = parts[1]
            else:
                logger.warning('Invalid environment entry: %s (expected name:queue_name)', entry)
        return envs

    # Backward compat: single queue from VALKEY_QUEUE_NAME
    queue_name = os.environ.get('VALKEY_QUEUE_NAME', 'canyougrab-jobs')
    env_name = os.environ.get('ENVIRONMENT', 'default')
    return {env_name: queue_name}


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


def collect_metrics(conn: redis.Redis, queues: dict[str, Queue]):
    """Read RQ state for each environment and update Prometheus gauges."""
    # Get all workers once (they're shared across the connection)
    try:
        all_workers = Worker.all(connection=conn)
    except redis.ConnectionError:
        logger.warning('Lost Valkey connection, will retry next cycle')
        return
    except Exception:
        logger.exception('Error fetching workers')
        return

    # Build a map of queue_name -> list of workers listening on that queue
    workers_by_queue: dict[str, list[Worker]] = {}
    for w in all_workers:
        for q in w.queues:
            workers_by_queue.setdefault(q.name, []).append(w)

    for env_name, queue in queues.items():
        try:
            queue_depth.labels(environment=env_name).set(queue.count)

            env_workers = workers_by_queue.get(queue.name, [])
            workers_active.labels(environment=env_name).set(len(env_workers))
            workers_busy.labels(environment=env_name).set(
                sum(1 for w in env_workers if w.get_state() == 'busy')
            )

            failed_jobs.labels(environment=env_name).set(len(queue.failed_job_registry))
            scheduled_jobs.labels(environment=env_name).set(len(queue.scheduled_job_registry))
            started_jobs.labels(environment=env_name).set(len(queue.started_job_registry))

            # Drain processing times (keyed per environment)
            metrics_key = f'metrics:processing_times:{env_name}'
            # Also drain legacy key for backward compat
            for key in [metrics_key, 'metrics:processing_times']:
                while True:
                    val = conn.rpop(key)
                    if val is None:
                        break
                    try:
                        processing_time.labels(environment=env_name).observe(float(val))
                    except (ValueError, TypeError):
                        pass

        except Exception:
            logger.exception('Error collecting metrics for environment %s', env_name)


def main():
    conn = build_connection()
    conn.ping()
    logger.info('Valkey connected')

    environments = parse_environments()
    queues = {}
    for env_name, queue_name in environments.items():
        queues[env_name] = Queue(queue_name, connection=conn)
        logger.info('Watching environment %s → queue %s', env_name, queue_name)

    env_names = ','.join(environments.keys())
    exporter_info.info({'environments': env_names})

    start_http_server(METRICS_PORT, addr='127.0.0.1')
    logger.info(
        'Serving metrics on 127.0.0.1:%d/metrics (interval=%ds, environments=%s)',
        METRICS_PORT, SCRAPE_INTERVAL, env_names,
    )

    while True:
        collect_metrics(conn, queues)
        time.sleep(SCRAPE_INTERVAL)


if __name__ == '__main__':
    main()
