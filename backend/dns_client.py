"""
DNS-based domain availability checking via dedicated Unbound resolver.
"""

import os
import socket
import logging

import dns.resolver
import dns.rdatatype
import dns.exception

logger = logging.getLogger(__name__)

DNS_RESOLVER_HOSTNAME = os.environ.get('DNS_RESOLVER_HOSTNAME', 'unbound.canyougrab.internal')
DNS_RESOLVER_PORT = int(os.environ.get('DNS_RESOLVER_PORT', '53'))
DNS_QUERY_TIMEOUT = float(os.environ.get('DNS_QUERY_TIMEOUT', '5.0'))


def create_resolver() -> dns.resolver.Resolver:
    """Create a DNS resolver configured to use the Unbound instance.
    Resolves the VPC internal hostname to an IP at startup."""
    resolver_ip = socket.gethostbyname(DNS_RESOLVER_HOSTNAME)
    logger.info('Resolved %s to %s', DNS_RESOLVER_HOSTNAME, resolver_ip)
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [resolver_ip]
    resolver.port = DNS_RESOLVER_PORT
    resolver.timeout = DNS_QUERY_TIMEOUT
    resolver.lifetime = DNS_QUERY_TIMEOUT
    return resolver


def check_domain_dns(domain: str, resolver: dns.resolver.Resolver) -> dict:
    """Check domain availability via DNS NS query to Unbound.

    Returns:
        {"domain": "example.com", "available": False, "tld": "com"} — registered
        {"domain": "example.com", "available": True} — probably available
        {"domain": "example.com", "available": True, "error": "..."} — validation error
        {"domain": "example.com", "available": None, "error": "..."} — DNS failure
    """
    domain = domain.lower().strip().rstrip('.')
    if not domain or '..' in domain:
        return {"domain": domain, "available": True, "error": "invalid domain"}

    parts = domain.split('.')
    if len(parts) < 2:
        return {"domain": domain, "available": True, "error": "need at least sld.tld"}

    tld = parts[-1]

    try:
        resolver.resolve(domain, 'NS')
        # NOERROR with NS records — domain is registered
        return {"domain": domain, "available": False, "tld": tld}

    except dns.resolver.NXDOMAIN:
        # Domain does not exist in zone — probably available
        return {"domain": domain, "available": True}

    except dns.resolver.NoAnswer:
        # Domain exists but has no NS records (registered but parked/undelegated)
        return {"domain": domain, "available": False, "tld": tld}

    except dns.resolver.NoNameservers:
        # All nameservers failed (SERVFAIL) — ambiguous, do not claim available
        logger.warning('SERVFAIL for domain %s', domain)
        return {"domain": domain, "available": None, "error": "dns_servfail"}

    except dns.exception.Timeout:
        logger.warning('DNS timeout for domain %s', domain)
        return {"domain": domain, "available": None, "error": "dns_timeout"}

    except Exception as e:
        logger.exception('Unexpected DNS error for domain %s', domain)
        return {"domain": domain, "available": None, "error": str(e)}
