#!/usr/bin/env python3
"""
Prometheus exporter for RQ queue metrics on the canyougrab-api worker queue.
Exposes queue depth, active worker count, and failed job count on :9122/metrics.

Run as a systemd service alongside the API and worker:
    python scripts/rq_metrics_exporter.py
"""

import os
import sys
import time
import logging

import redis
from rq import Queue, Worker
from rq.job import JobStatus
from prometheus_client import start_http_server, Gauge, Info

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [rq-metrics] %(message)s',
)
logger = logging.getLogger(__name__)

METRICS_PORT = int(os.environ.get('RQ_METRICS_PORT', '9122'))
SCRAPE_INTERVAL = int(os.environ.get('RQ_METRICS_INTERVAL', '15'))  # seconds
QUEUE_NAME = os.environ.get('VALKEY_QUEUE_NAME', 'canyougrab-jobs')

# Prometheus metrics
queue_depth = Gauge('canyougrab_queue_depth', 'Number of pending jobs in the RQ queue')
workers_active = Gauge('canyougrab_workers_active', 'Number of active RQ workers')
workers_busy = Gauge('canyougrab_workers_busy', 'Number of RQ workers currently processing a job')
failed_jobs = Gauge('canyougrab_failed_jobs_total', 'Number of jobs in the failed job registry')
scheduled_jobs = Gauge('canyougrab_scheduled_jobs', 'Number of jobs in the scheduled registry')
started_jobs = Gauge('canyougrab_started_jobs', 'Number of currently executing jobs')
exporter_info = Info('canyougrab_rq_exporter', 'RQ metrics exporter metadata')


def build_connection() -> redis.Redis:
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = os.environ.get('VALKEY_PORT', '25061')
    user = os.environ.get('VALKEY_USERNAME', 'default')
    pw = os.environ.get('VALKEY_PASSWORD', '')
    url = f'rediss://{user}:{pw}@{host}:{port}'
    return redis.from_url(url, decode_responses=False)


def collect_metrics(conn: redis.Redis, queue: Queue):
    """Read RQ state and update Prometheus gauges."""
    try:
        queue_depth.set(queue.count)

        all_workers = Worker.all(connection=conn)
        workers_active.set(len(all_workers))
        workers_busy.set(sum(1 for w in all_workers if w.get_state() == 'busy'))

        failed_registry = queue.failed_job_registry
        failed_jobs.set(len(failed_registry))

        scheduled_registry = queue.scheduled_job_registry
        scheduled_jobs.set(len(scheduled_registry))

        started_registry = queue.started_job_registry
        started_jobs.set(len(started_registry))

    except redis.ConnectionError:
        logger.warning('Lost Valkey connection, will retry next cycle')
    except Exception:
        logger.exception('Error collecting RQ metrics')


def main():
    conn = build_connection()

    # Verify connectivity
    conn.ping()
    logger.info('Valkey connected')

    queue = Queue(QUEUE_NAME, connection=conn)
    exporter_info.info({'queue_name': QUEUE_NAME})

    start_http_server(METRICS_PORT, addr='127.0.0.1')
    logger.info('Serving Prometheus metrics on 127.0.0.1:%d/metrics (interval=%ds)', METRICS_PORT, SCRAPE_INTERVAL)

    while True:
        collect_metrics(conn, queue)
        time.sleep(SCRAPE_INTERVAL)


if __name__ == '__main__':
    main()
