"""
Anonymous name generation: business description -> brandable name candidates ->
live availability check, gated by trial tier (curious/trying/engaged).

Identity is multi-signal: visitor_id (client cookie), fingerprint (FingerprintJS
visitorId), and salted IP hash. Usage is aggregated by MAX across all three so
that defeating any single signal does not reset the counter.

Generation uses Anthropic Claude when ANTHROPIC_API_KEY is set, otherwise falls
back to a deterministic rule-based generator so the endpoint never hard-fails.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from queries import get_db_conn
from valkey_client import create_split_job, get_job_status, get_job_results

logger = logging.getLogger(__name__)

# ── Trial tier limits ──────────────────────────────────────────────────────
CURIOUS_LIMIT = 5
TRYING_LIMIT = 10
FULL_RESULT_COUNT = 36
ENGAGED_VISIBLE_COUNT = 3
ENGAGED_COOLDOWN_MS = 30_000
GLOBAL_COOLDOWN_MS = 5_000   # min spacing between any two generations
ROLLING_WINDOW_DAYS = 7

# BYOK callers don't burn our LLM budget — but they still hit the availability
# engine, so we cap them separately, more permissively. Tunable.
BYOK_DAILY_LIMIT = 50

# Hosted-LLM anon caps (cost-protection layer in front of the home machine).
# Per-visitor cap drives signup conversion; per-IP cap defends against scripted
# abuse that rotates visitor_ids. Adjust via env without redeploying code.
HOSTED_VISITOR_DAILY_LIMIT = int(os.environ.get('HOME_LLM_VISITOR_DAILY', '5'))
HOSTED_IP_DAILY_LIMIT = int(os.environ.get('HOME_LLM_IP_DAILY', '20'))

# Anthropic config (optional, legacy fallback before HOME_LLM)
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.environ.get('NAMEGEN_MODEL', 'claude-haiku-4-5')

TLD_BUCKETS: dict[str, list[str]] = {
    'com_only': ['com'],
    'tech':     ['io', 'dev', 'ai', 'app'],
    'global':   ['co', 'net', 'org', 'com'],
    'any':      ['com', 'io', 'co', 'ai', 'dev', 'app', 'xyz'],
}

PORTAL_URL = os.environ.get('PORTAL_URL', 'https://portal.canyougrab.it')
SIGNUP_URL = f'{PORTAL_URL}/signup'


# ── IP hashing with daily salt rotation ────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def get_or_create_daily_salt() -> str:
    today = _today()
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT salt FROM anon_ip_salt WHERE salt_date = %s', (today,))
            row = cur.fetchone()
            if row:
                return row[0]
            new_salt = secrets.token_hex(32)
            cur.execute(
                'INSERT INTO anon_ip_salt (salt_date, salt) VALUES (%s, %s) '
                'ON CONFLICT (salt_date) DO NOTHING RETURNING salt',
                (today, new_salt),
            )
            ret = cur.fetchone()
            conn.commit()
            if ret:
                return ret[0]
            cur.execute('SELECT salt FROM anon_ip_salt WHERE salt_date = %s', (today,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def hash_ip(ip: str) -> Optional[str]:
    if not ip:
        return None
    salt = get_or_create_daily_salt()
    return hashlib.sha256(f'{salt}:{ip}'.encode()).hexdigest()


# ── Visitor identity & tier aggregation ────────────────────────────────────

def aggregate_usage(visitor_id: str, fingerprint: Optional[str], ip_hash: Optional[str]) -> dict:
    """Return {count, last_at} aggregated across (visitor_id OR fingerprint OR ip_hash)
    within the rolling window. Count is the highest of the three lookups."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            counts = []
            last_at = None

            def _q(field: str, value: str):
                cur.execute(
                    f'SELECT COUNT(*), MAX(created_at) FROM anon_name_gen_usage '
                    f"WHERE {field} = %s AND created_at > NOW() - INTERVAL '%s days'",
                    (value, ROLLING_WINDOW_DAYS),
                )
                return cur.fetchone()

            for field, value in (
                ('visitor_id', visitor_id),
                ('fingerprint', fingerprint),
                ('ip_hash', ip_hash),
            ):
                if not value:
                    continue
                c, m = _q(field, value)
                counts.append(c or 0)
                if m and (last_at is None or m > last_at):
                    last_at = m

            return {'count': max(counts) if counts else 0, 'last_at': last_at}
    finally:
        conn.close()


def record_usage(visitor_id: str, fingerprint: Optional[str], ip_hash: Optional[str]) -> None:
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO anon_name_gen_usage (visitor_id, fingerprint, ip_hash) '
                'VALUES (%s, %s, %s)',
                (visitor_id, fingerprint, ip_hash),
            )
            conn.commit()
    finally:
        conn.close()


def daily_count_visitor(visitor_id: str, fingerprint: Optional[str]) -> int:
    """Generations from this visitor in the last 24h. Aggregates by MAX across
    visitor_id and fingerprint so clearing cookies alone doesn't reset."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            counts: list[int] = []
            for field, value in (('visitor_id', visitor_id), ('fingerprint', fingerprint)):
                if not value:
                    continue
                cur.execute(
                    f'SELECT COUNT(*) FROM anon_name_gen_usage '
                    f"WHERE {field} = %s AND created_at > NOW() - INTERVAL '24 hours'",
                    (value,),
                )
                counts.append(cur.fetchone()[0] or 0)
            return max(counts) if counts else 0
    finally:
        conn.close()


def daily_count_ip(ip_hash: Optional[str]) -> int:
    """Generations from this IP-hash in the last 24h. Used to gate scripted
    abuse that rotates visitor_ids."""
    if not ip_hash:
        return 0
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM anon_name_gen_usage '
                "WHERE ip_hash = %s AND created_at > NOW() - INTERVAL '24 hours'",
                (ip_hash,),
            )
            return cur.fetchone()[0] or 0
    finally:
        conn.close()


# ── Saved generation lists (claim-on-signup) ───────────────────────────────

def save_generation_list(visitor_id: str, description: str, payload: dict) -> Optional[str]:
    """Persist a generation result so it can be attached to an account when
    the visitor signs up. Returns the new list id, or None on failure."""
    if not visitor_id or not description:
        return None
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO name_generation_lists (visitor_id, description, payload) '
                'VALUES (%s, %s, %s) RETURNING id',
                (visitor_id, description, json.dumps(payload)),
            )
            row = cur.fetchone()
            conn.commit()
            return str(row[0]) if row else None
    except Exception as e:
        logger.warning('Failed to save generation list: %s', e)
        conn.rollback()
        return None
    finally:
        conn.close()


def claim_anon_lists(visitor_id: str, user_sub: str) -> int:
    """Attach all unclaimed anon lists for `visitor_id` to `user_sub`.
    Returns count of rows claimed. Idempotent: only touches NULL user_sub rows.
    """
    if not visitor_id or not user_sub:
        return 0
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE name_generation_lists '
                'SET user_sub = %s, claimed_at = NOW() '
                'WHERE visitor_id = %s AND user_sub IS NULL',
                (user_sub, visitor_id),
            )
            n = cur.rowcount
            conn.commit()
            if n:
                logger.info('Claimed %d anon lists for user_sub=%s visitor=%s', n, user_sub, visitor_id[:8])
            return n
    except Exception as e:
        logger.error('Failed to claim anon lists: %s', e)
        conn.rollback()
        return 0
    finally:
        conn.close()


def get_saved_list(share_id: str) -> Optional[dict]:
    """Public read of a saved generation list. No auth — these are shareable.
    Contents are public-safe (suggested names + availability), no PII.
    Strips per-viewer fields like `locked` so shared viewers see everything."""
    if not share_id:
        return None
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT description, payload, created_at '
                'FROM name_generation_lists WHERE id::text = %s',
                (share_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[1] or {}
            results = payload.get('results') or []
            for r in results:
                r.pop('locked', None)
            payload['results'] = results
            return {
                'description': row[0],
                'payload': payload,
                'created_at': row[2].isoformat() if row[2] else None,
            }
    finally:
        conn.close()


def list_user_generations(user_sub: str, limit: int = 50) -> list[dict]:
    """Return saved lists owned by `user_sub`, newest first."""
    if not user_sub:
        return []
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id, description, payload, created_at, claimed_at '
                'FROM name_generation_lists '
                'WHERE user_sub = %s '
                'ORDER BY created_at DESC LIMIT %s',
                (user_sub, limit),
            )
            return [
                {
                    'id': str(r[0]),
                    'description': r[1],
                    'payload': r[2],
                    'created_at': r[3].isoformat() if r[3] else None,
                    'claimed_at': r[4].isoformat() if r[4] else None,
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def tier_for_count(count: int) -> str:
    if count <= CURIOUS_LIMIT:
        return 'curious'
    if count <= TRYING_LIMIT:
        return 'trying'
    return 'engaged'


def cooldown_remaining_ms(tier: str, last_at: Optional[datetime]) -> int:
    if last_at is None:
        return 0
    elapsed_ms = (datetime.now(timezone.utc) - last_at).total_seconds() * 1000
    floor = ENGAGED_COOLDOWN_MS if tier == 'engaged' else GLOBAL_COOLDOWN_MS
    return max(0, int(floor - elapsed_ms))


# ── Brandable name generation ──────────────────────────────────────────────

LLM_PROMPT = """You are a brand-naming expert. Generate {count} candidate brand names for this business:

DESCRIPTION:
{description}

STYLES: {styles}
PREFERRED EXTENSION TYPE: {tld_pref}

Rules:
- Each name is a single lowercase word or two words concatenated, alphanumeric only
- 4-18 characters
- Avoid generic words ("app", "platform", "startup", "company")
- Mix invented words, evocative metaphors, compound words, and direct descriptors
- Avoid offensive or trademark-likely names

Return ONLY a JSON array of strings, no commentary. Example: ["frondly", "treekit", "leafgraph"]"""


async def llm_generate_bases_async(description: str, styles: list[str], tld_pref: str, count: int) -> list[str]:
    """Async path: prefer the hosted home LLM, fall back to Anthropic, then rule-based.

    The hosted LLM is the primary anon-mode generator. Anthropic stays wired as a
    legacy fallback for environments that have ANTHROPIC_API_KEY set but not
    HOME_LLM_API_KEY. Both fall back to rule-based on any failure so the
    endpoint never hard-fails for the visitor.
    """
    # Hosted home-LLM path
    try:
        from hosted_llm import generate_bases as hosted_generate, is_configured
        if is_configured():
            bases = await hosted_generate(description, styles, tld_pref, count)
            if bases:
                return bases
            logger.info('Hosted LLM returned no bases; falling back')
    except Exception as e:
        # HostedQueueFullError, HostedUnavailableError, network, etc.
        logger.warning('Hosted LLM unavailable (%s); falling back', type(e).__name__)

    # Anthropic legacy fallback
    if ANTHROPIC_API_KEY:
        try:
            return _anthropic_generate_bases(description, styles, tld_pref, count)
        except Exception as e:
            logger.warning('Anthropic fallback failed (%s); using rules', e)

    logger.info('No LLM available; using rule-based name generator')
    return rule_based_bases(description, styles, count)


def _anthropic_generate_bases(description: str, styles: list[str], tld_pref: str, count: int) -> list[str]:
    import anthropic  # type: ignore
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        messages=[{
            'role': 'user',
            'content': LLM_PROMPT.format(
                count=count,
                description=description.strip()[:1000],
                styles=', '.join(styles) if styles else 'modern',
                tld_pref=tld_pref,
            ),
        }],
    )
    text = msg.content[0].text.strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
    bases = json.loads(text)
    if not isinstance(bases, list):
        raise ValueError('LLM did not return a list')
    return [_clean(b) for b in bases if isinstance(b, str)][:count]


_STOPWORDS = frozenset({
    'a', 'an', 'and', 'the', 'for', 'to', 'of', 'in', 'on', 'with', 'that',
    'this', 'is', 'it', 'we', 'our', 'i', 'my', 'be', 'by', 'as', 'at', 'or',
    'from', 'but', 'are', 'was', 'were', 'will', 'would', 'can', 'could',
    'should', 'have', 'has', 'had', 'you', 'your', 'they', 'their', 'them',
    'app', 'platform', 'service', 'company', 'business', 'startup',
})


def _clean(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())[:20]


def rule_based_bases(description: str, styles: list[str], count: int) -> list[str]:
    tokens = [
        t for t in re.findall(r'[a-z]+', description.lower())
        if len(t) >= 3 and t not in _STOPWORDS
    ][:8]
    bases: list[str] = []
    seen: set[str] = set()

    def add(b: str):
        b = _clean(b)
        if 3 <= len(b) <= 20 and b not in seen:
            seen.add(b)
            bases.append(b)

    for t in tokens:
        add(t)

    if 'short' in styles:
        for t in tokens:
            add(t[: max(4, int(len(t) * 0.7))])

    if 'playful' in styles:
        for t in tokens:
            add(f'{t}ly')
            add(f'get{t}')

    if 'wordplay' in styles:
        for t in tokens:
            add(t.replace('s', 'z') if t.endswith('s') else f'{t}ify')

    if 'compound' in styles or len(tokens) >= 2:
        for i, a in enumerate(tokens):
            for j, b in enumerate(tokens):
                if i != j:
                    add(f'{a}{b}')

    for t in tokens[:3]:
        for p in ('use', 'try', 'join', 'go'):
            add(f'{p}{t}')
        for s in ('hq', 'labs', 'kit', 'hub'):
            add(f'{t}{s}')

    return bases[:count]


def expand_to_domains(bases: list[str], tld_pref: str, cap: int) -> list[str]:
    tlds = TLD_BUCKETS.get(tld_pref, TLD_BUCKETS['any'])
    out: list[str] = []
    for base in bases:
        for tld in tlds:
            if len(out) >= cap:
                return out
            out.append(f'{base}.{tld}')
        if len(out) >= cap:
            return out
    return out


# ── Domain availability via existing job pipeline ──────────────────────────

POLL_INTERVAL = 0.3
POLL_TIMEOUT = 30.0


async def check_domains_anon(domains: list[str]) -> list[dict]:
    """Run domains through the same job pipeline as /api/check/bulk, but
    without quota tracking. Used only for trial-gated anonymous traffic.
    """
    job_id = str(uuid.uuid4())
    consumer = f'anon:{job_id[:8]}'
    create_split_job(job_id, consumer, domains)

    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        job = get_job_status(job_id)
        if job is None:
            continue
        if job['status'] == 'completed':
            return get_job_results(job_id) or []
        if job['status'] == 'failed':
            logger.warning('Anon domain check failed: %s', job.get('error'))
            return []
    logger.warning('Anon domain check timed out for job %s', job_id[:8])
    return []


# ── Top-level pipeline ─────────────────────────────────────────────────────

async def generate_for_visitor(
    description: str,
    styles: list[str],
    tld_pref: str,
    visitor_id: str,
    fingerprint: Optional[str],
    ip_hash: Optional[str],
) -> dict:
    """Full pipeline. Returns the response dict, or raises a structured error
    on hard limits / cooldowns / hosted-LLM unavailability.
    """
    # Per-IP daily cap protects the hosted LLM from scripted abuse that rotates
    # visitor_ids — checked first, since IP is harder to spoof than a cookie.
    if daily_count_ip(ip_hash) >= HOSTED_IP_DAILY_LIMIT:
        raise HostedDailyCapError('ip', HOSTED_IP_DAILY_LIMIT)
    # Per-visitor daily cap drives signup conversion.
    if daily_count_visitor(visitor_id, fingerprint) >= HOSTED_VISITOR_DAILY_LIMIT:
        raise HostedDailyCapError('visitor', HOSTED_VISITOR_DAILY_LIMIT)

    pre = aggregate_usage(visitor_id, fingerprint, ip_hash)
    pre_tier = tier_for_count(pre['count'])
    cooldown = cooldown_remaining_ms(pre_tier, pre['last_at'])
    if cooldown > 0:
        raise CooldownError(cooldown)

    record_usage(visitor_id, fingerprint, ip_hash)
    new_count = pre['count'] + 1
    tier = tier_for_count(new_count)

    bases = await llm_generate_bases_async(description, styles, tld_pref, count=18)
    domains = expand_to_domains(bases, tld_pref, cap=FULL_RESULT_COUNT)

    raw = await check_domains_anon(domains) if domains else []
    by_domain = {r['domain']: r for r in raw}

    results = []
    for d in domains:
        r = by_domain.get(d, {})
        base = d.rsplit('.', 1)[0]
        tld = d.rsplit('.', 1)[1] if '.' in d else ''
        results.append({
            'domain': d,
            'available': r.get('available'),
            'tld': tld,
            'base': base,
            'rationale': None,
        })

    # Sort: available first, shorter domains first
    results.sort(key=lambda r: (
        0 if r['available'] is True else (1 if r['available'] is None else 2),
        len(r['domain']),
    ))

    visible = ENGAGED_VISIBLE_COUNT if tier == 'engaged' else len(results)
    for i, r in enumerate(results):
        if i >= visible:
            r['locked'] = True

    limit = (
        CURIOUS_LIMIT if tier == 'curious'
        else TRYING_LIMIT if tier == 'trying'
        else TRYING_LIMIT
    )
    cooldown_ms = ENGAGED_COOLDOWN_MS if tier == 'engaged' else 0

    response = {
        'results': results,
        'description': description,
        'tier': tier,
        'generations_used': new_count,
        'generations_limit': limit,
        'signup_url': SIGNUP_URL,
        'cooldown_ms': cooldown_ms,
    }
    list_id = save_generation_list(
        visitor_id=visitor_id,
        description=description,
        payload={
            'styles': styles,
            'tld_pref': tld_pref,
            'results': results,
            'tier': tier,
        },
    )
    if list_id:
        response['list_id'] = list_id
    return response


class CooldownError(Exception):
    def __init__(self, retry_after_ms: int):
        self.retry_after_ms = retry_after_ms
        super().__init__(f'Cooldown active, retry in {retry_after_ms}ms')


class HostedDailyCapError(Exception):
    """Anonymous visitor has hit the hosted-LLM daily cap. The route surfaces
    this as a 429 with `signup_url` so the FE can render its soft paywall.

    `scope` is "visitor" or "ip" — the FE messages slightly differently."""
    def __init__(self, scope: str, daily_limit: int):
        self.scope = scope
        self.daily_limit = daily_limit
        super().__init__(f'Hosted LLM {scope} daily cap reached ({daily_limit})')
