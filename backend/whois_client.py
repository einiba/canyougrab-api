"""
HTTP client for the self-hosted rust-whois RDAP/WHOIS service.
Queries the VPC-internal rust-whois instance for structured domain data.
"""

import os
import socket
import logging

import httpx

logger = logging.getLogger(__name__)

WHOIS_HOSTNAME = os.environ.get('WHOIS_HOSTNAME', 'rust-whois.canyougrab.internal')
WHOIS_PORT = int(os.environ.get('WHOIS_PORT', '3000'))
WHOIS_TIMEOUT = float(os.environ.get('WHOIS_TIMEOUT', '10.0'))

_base_url: str | None = None


def _get_base_url() -> str:
    """Resolve the VPC hostname to an IP once and build the base URL."""
    global _base_url
    if _base_url is None:
        ip = socket.gethostbyname(WHOIS_HOSTNAME)
        logger.info('Resolved %s to %s', WHOIS_HOSTNAME, ip)
        _base_url = f'http://{ip}:{WHOIS_PORT}'
    return _base_url


def check_domain_whois(domain: str) -> dict | None:
    """Query rust-whois for structured WHOIS/RDAP data.

    Returns a dict with parsed fields on success, or None on any failure.
    Failures are logged but never raised — WHOIS is a best-effort enrichment.

    Returned dict (on success):
        {
            "registrar": str | None,
            "creation_date": str | None,      # ISO 8601
            "expiration_date": str | None,     # ISO 8601
            "updated_date": str | None,        # ISO 8601
            "name_servers": list[str] | None,
            "status": list[str] | None,
            "whois_server": str | None,
            "query_time_ms": int,
        }
    """
    try:
        url = f'{_get_base_url()}/whois/{domain}'
        resp = httpx.get(url, timeout=WHOIS_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        logger.warning('WHOIS timeout for %s', domain)
        return None
    except httpx.ConnectError:
        logger.warning('WHOIS connection refused for %s', domain)
        return None
    except Exception as e:
        logger.warning('WHOIS error for %s: %s', domain, e)
        return None

    parsed = data.get('parsed_data') or {}

    return {
        'registrar': parsed.get('registrar'),
        'creation_date': parsed.get('creation_date'),
        'expiration_date': parsed.get('expiration_date'),
        'updated_date': parsed.get('updated_date'),
        'name_servers': parsed.get('name_servers'),
        'status': parsed.get('status'),
        'whois_server': data.get('whois_server'),
        'query_time_ms': data.get('query_time_ms'),
    }
