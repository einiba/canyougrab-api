"""
DNS-based domain availability checking with capacity-aware multi-resolver routing.

Distributes queries across multiple DNS resolvers (Google, Cloudflare, Quad9,
OpenDNS, Unbound) proportional to each server's observed capacity.
On SERVFAIL, retries with a different server.
"""

import os
import random
import socket
import logging
import time
import threading
from dataclasses import dataclass, field

import dns.resolver
import dns.rdatatype
import dns.exception

logger = logging.getLogger(__name__)

# Legacy env vars — Unbound is now one of many resolvers, loaded from DB
DNS_RESOLVER_HOSTNAME = os.environ.get('DNS_RESOLVER_HOSTNAME', 'unbound.canyougrab.internal')
DNS_RESOLVER_PORT = int(os.environ.get('DNS_RESOLVER_PORT', '53'))
DNS_QUERY_TIMEOUT = float(os.environ.get('DNS_QUERY_TIMEOUT', '5.0'))

# Feature flag: set to "true" to enable multi-resolver routing
MULTI_RESOLVER_ENABLED = os.environ.get('MULTI_RESOLVER', 'true').lower() == 'true'

# How often to refresh server list and recalculate caps (seconds)
REFRESH_INTERVAL = 30


@dataclass
class NameserverEntry:
    id: int
    name: str
    address: str
    port: int = 53
    provider: str = ''
    estimated_cap_qps: float = 1.0
    enabled: bool = True
    resolver: dns.resolver.Resolver = field(default=None, repr=False)


class ResolverPool:
    """Thread-safe pool of DNS resolvers with weighted selection."""

    def __init__(self):
        self._servers: list[NameserverEntry] = []
        self._lock = threading.Lock()
        self._last_refresh = 0
        self._valkey = None
        self._initialized = False

    def initialize(self, valkey_client=None):
        """Load servers from database. Call once at worker startup."""
        self._valkey = valkey_client
        self._refresh_servers()
        self._initialized = True
        logger.info('ResolverPool initialized with %d servers', len(self._servers))

    def _refresh_servers(self):
        """Load nameserver list from PostgreSQL + latest caps from Valkey."""
        try:
            from queries import get_db_conn
            conn = get_db_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, address, port, provider, estimated_cap_qps, enabled
                    FROM nameservers
                    WHERE enabled = TRUE AND disabled_at IS NULL
                    ORDER BY estimated_cap_qps DESC
                """)
                rows = cur.fetchall()
            conn.close()

            servers = []
            for row in rows:
                entry = NameserverEntry(
                    id=row[0], name=row[1], address=row[2],
                    port=row[3] or 53, provider=row[4] or '',
                    estimated_cap_qps=row[5] or 1.0, enabled=row[6],
                )
                # Create a resolver for this server
                r = dns.resolver.Resolver(configure=False)
                r.nameservers = [entry.address]
                r.port = entry.port
                r.timeout = DNS_QUERY_TIMEOUT
                r.lifetime = DNS_QUERY_TIMEOUT
                entry.resolver = r
                servers.append(entry)

            # Also update caps from Valkey if available (hot data)
            if self._valkey:
                for s in servers:
                    cap = self._valkey.get(f'ns:cap:{s.id}')
                    if cap:
                        s.estimated_cap_qps = float(cap)

            with self._lock:
                self._servers = servers
                self._last_refresh = time.monotonic()

            logger.info('Refreshed %d nameservers: %s',
                        len(servers),
                        ', '.join(f'{s.name}({s.estimated_cap_qps:.1f})' for s in servers))

        except Exception as e:
            logger.warning('Failed to refresh nameservers from DB: %s', e)
            if not self._servers:
                # Fallback: create Unbound-only pool
                self._init_fallback()

    def _init_fallback(self):
        """Fallback if DB is unreachable — use Unbound only."""
        try:
            ip = socket.gethostbyname(DNS_RESOLVER_HOSTNAME)
        except Exception:
            ip = DNS_RESOLVER_HOSTNAME

        r = dns.resolver.Resolver(configure=False)
        r.nameservers = [ip]
        r.port = DNS_RESOLVER_PORT
        r.timeout = DNS_QUERY_TIMEOUT
        r.lifetime = DNS_QUERY_TIMEOUT

        entry = NameserverEntry(
            id=0, name='Unbound (fallback)', address=ip,
            port=DNS_RESOLVER_PORT, provider='self',
            estimated_cap_qps=1.0, resolver=r,
        )
        with self._lock:
            self._servers = [entry]
            self._last_refresh = time.monotonic()
        logger.warning('Using fallback Unbound-only resolver at %s', ip)

    def _maybe_refresh(self):
        """Refresh server list if stale."""
        if time.monotonic() - self._last_refresh > REFRESH_INTERVAL:
            self._refresh_servers()

    def select(self, exclude_id: int = None) -> NameserverEntry | None:
        """Select a nameserver weighted by estimated capacity.
        Optionally exclude a server (for retry with different server)."""
        self._maybe_refresh()

        with self._lock:
            candidates = [s for s in self._servers if s.enabled]
            if exclude_id is not None:
                candidates = [s for s in candidates if s.id != exclude_id]
            if not candidates:
                return None

        total = sum(s.estimated_cap_qps for s in candidates)
        if total <= 0:
            return random.choice(candidates)

        weights = [s.estimated_cap_qps / total for s in candidates]
        return random.choices(candidates, weights=weights, k=1)[0]

    def record_result(self, server_id: int, success: bool):
        """Record a query result. Updates Valkey counters."""
        if not self._valkey or server_id == 0:
            return

        try:
            pipe = self._valkey.pipeline(transaction=False)
            if success:
                key = f'ns:stats:{server_id}:success'
                pipe.incr(key)
                pipe.expire(key, 180)  # 3 min TTL
            else:
                key = f'ns:stats:{server_id}:error'
                pipe.incr(key)
                pipe.expire(key, 180)
            pipe.execute()
        except Exception:
            pass  # Best effort — don't fail the lookup

    def recalculate_caps(self):
        """Recalculate estimated caps from Valkey stats. Called by background task."""
        if not self._valkey:
            return

        try:
            from queries import get_db_conn
            conn = get_db_conn()

            with self._lock:
                servers = list(self._servers)

            for s in servers:
                success = int(self._valkey.get(f'ns:stats:{s.id}:success') or 0)
                error = int(self._valkey.get(f'ns:stats:{s.id}:error') or 0)
                total = success + error

                if total < 5:
                    continue  # Not enough data

                # Only adjust caps downward for servers that had errors recently.
                # Error-free servers with high volume get a small cap increase.
                if error == 0:
                    new_cap = s.estimated_cap_qps
                    if success > 50:
                        # Proven under load with zero errors — increase cap
                        new_cap = min(s.estimated_cap_qps * 1.1, 500)
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE nameservers SET
                                estimated_cap_qps = %s,
                                success_count_3m = %s,
                                last_success_at = NOW(),
                                updated_at = NOW()
                            WHERE id = %s
                        """, (new_cap, success, s.id))
                    conn.commit()
                    if new_cap != s.estimated_cap_qps:
                        self._valkey.set(f'ns:cap:{s.id}', str(new_cap), ex=300)
                        s.estimated_cap_qps = new_cap
                    continue

                error_rate = error / total

                if error_rate > 0.5:
                    new_cap = max(0.1, s.estimated_cap_qps * 0.5)
                elif error_rate > 0.1:
                    new_cap = s.estimated_cap_qps * (1 - error_rate)
                else:
                    # Errors present but low rate — slight reduction
                    new_cap = s.estimated_cap_qps * 0.95

                # Servers recovering (error rate dropping) get a boost
                if error_rate < 0.05 and s.estimated_cap_qps < 1.0:
                    new_cap = min(s.estimated_cap_qps * 1.3, 500)

                # Update DB
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE nameservers SET
                            estimated_cap_qps = %s,
                            success_count_3m = %s,
                            error_count_3m = %s,
                            last_error_at = NOW(),
                            last_success_at = CASE WHEN %s > 0 THEN NOW() ELSE last_success_at END,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (new_cap, success, error, success, s.id))
                conn.commit()

                # Update Valkey hot cache
                self._valkey.set(f'ns:cap:{s.id}', str(new_cap), ex=300)
                s.estimated_cap_qps = new_cap

                logger.info('Nameserver %s: cap=%.1f (success=%d error=%d rate=%.1f%%)',
                            s.name, new_cap, success, error, error_rate * 100)

            conn.close()

            # Only decay counters for servers that had errors — clean servers
            # keep their counters until Valkey TTL (3 min) expires naturally
            for s in servers:
                error_count = int(self._valkey.get(f'ns:stats:{s.id}:error') or 0)
                if error_count > 0:
                    for suffix in ('success', 'error'):
                        key = f'ns:stats:{s.id}:{suffix}'
                        val = int(self._valkey.get(key) or 0)
                        if val > 0:
                            self._valkey.set(key, str(val // 2), ex=180)

        except Exception as e:
            logger.warning('Failed to recalculate nameserver caps: %s', e)

    @property
    def server_count(self) -> int:
        with self._lock:
            return len(self._servers)


# Module-level singleton
_pool = ResolverPool()


def get_resolver_pool() -> ResolverPool:
    return _pool


def create_resolver() -> dns.resolver.Resolver:
    """Legacy compatibility: returns a single Unbound resolver.
    Used by worker startup validation and health checks."""
    try:
        resolver_ip = socket.gethostbyname(DNS_RESOLVER_HOSTNAME)
    except Exception:
        resolver_ip = DNS_RESOLVER_HOSTNAME
    logger.info('Resolved %s to %s', DNS_RESOLVER_HOSTNAME, resolver_ip)
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [resolver_ip]
    resolver.port = DNS_RESOLVER_PORT
    resolver.timeout = DNS_QUERY_TIMEOUT
    resolver.lifetime = DNS_QUERY_TIMEOUT
    return resolver


def _do_query(domain: str, tld: str, server: NameserverEntry) -> dict:
    """Execute a single DNS NS query against a specific server."""
    try:
        server.resolver.resolve(domain, 'NS')
        return {"domain": domain, "available": False, "tld": tld,
                "dns_status": "noerror_ns", "dns_server": server.name}

    except dns.resolver.NXDOMAIN:
        return {"domain": domain, "available": True, "tld": tld,
                "dns_status": "nxdomain", "dns_server": server.name}

    except dns.resolver.NoAnswer:
        return {"domain": domain, "available": False, "tld": tld,
                "dns_status": "noanswer", "dns_server": server.name}

    except (dns.resolver.NoNameservers, dns.exception.Timeout) as e:
        status = "servfail" if isinstance(e, dns.resolver.NoNameservers) else "timeout"
        return {"domain": domain, "available": None, "tld": tld,
                "error": f"dns_{status}", "dns_status": status,
                "dns_server": server.name, "_failed": True}

    except Exception as e:
        return {"domain": domain, "available": None, "tld": tld,
                "error": str(e), "dns_server": server.name, "_failed": True}


def check_domain_dns(domain: str, resolver: dns.resolver.Resolver = None) -> dict:
    """Check domain availability via DNS NS query.

    If multi-resolver is enabled, ignores the `resolver` parameter and
    selects from the pool. On failure, retries with a different server.
    Falls back to the provided resolver if pool is empty.
    """
    domain = domain.lower().strip().rstrip('.')
    if not domain or '..' in domain:
        return {"domain": domain, "available": True, "error": "invalid domain"}

    parts = domain.split('.')
    if len(parts) < 2:
        return {"domain": domain, "available": True, "error": "need at least sld.tld"}

    tld = parts[-1]
    pool = get_resolver_pool()

    # Multi-resolver path
    if MULTI_RESOLVER_ENABLED and pool._initialized and pool.server_count > 0:
        # First attempt
        server = pool.select()
        if server is None:
            # No servers available — fall through to legacy
            pass
        else:
            result = _do_query(domain, tld, server)
            failed = result.pop('_failed', False)

            if not failed:
                pool.record_result(server.id, success=True)
                return result

            # First attempt failed — record and retry with different server
            pool.record_result(server.id, success=False)
            logger.debug('DNS %s failed on %s, retrying on different server', domain, server.name)

            server2 = pool.select(exclude_id=server.id)
            if server2 is not None:
                time.sleep(0.1)  # Brief pause before retry
                result2 = _do_query(domain, tld, server2)
                failed2 = result2.pop('_failed', False)

                if not failed2:
                    pool.record_result(server2.id, success=True)
                    result2['dns_status'] = result2['dns_status'] + '_retry'
                    return result2

                pool.record_result(server2.id, success=False)

            # Both attempts failed
            logger.warning('SERVFAIL for %s (tried %s + %s)',
                           domain, server.name, server2.name if server2 else 'none')
            return result  # Return first failure result

    # Legacy single-resolver path (fallback)
    if resolver is None:
        resolver = create_resolver()

    try:
        resolver.resolve(domain, 'NS')
        return {"domain": domain, "available": False, "tld": tld, "dns_status": "noerror_ns"}
    except dns.resolver.NXDOMAIN:
        return {"domain": domain, "available": True, "tld": tld, "dns_status": "nxdomain"}
    except dns.resolver.NoAnswer:
        return {"domain": domain, "available": False, "tld": tld, "dns_status": "noanswer"}
    except dns.resolver.NoNameservers:
        logger.warning('SERVFAIL for domain %s (legacy resolver)', domain)
        return {"domain": domain, "available": None, "error": "dns_servfail", "dns_status": "servfail"}
    except dns.exception.Timeout:
        logger.warning('DNS timeout for domain %s (legacy resolver)', domain)
        return {"domain": domain, "available": None, "error": "dns_timeout", "dns_status": "timeout"}
    except Exception as e:
        logger.exception('Unexpected DNS error for domain %s', domain)
        return {"domain": domain, "available": None, "error": str(e)}
