"""
Hosted-LLM proxy for anonymous name generation.

Talks to a self-hosted OpenAI-compatible endpoint (default: llm.canyougrab.it)
fronted via Cloudflare Tunnel. The model is fine-tuned for business name
generation and returns a JSON array of name bases.

Capacity model:
- Single home machine, 4 concurrent slots.
- An asyncio.Semaphore acts as the queue. Requests beyond capacity wait up to
  HOME_LLM_QUEUE_TIMEOUT seconds; if the wait exceeds that, we raise
  HostedQueueFullError and the FE surfaces "high demand, try again or BYOK".
- Circuit breaker: HOME_LLM_BREAKER_THRESHOLD consecutive failures opens the
  breaker for HOME_LLM_BREAKER_COOLDOWN seconds. While open, generate_bases
  raises HostedUnavailableError immediately so the FE falls back gracefully.

Per-visitor and per-IP daily quotas are enforced upstream in name_gen.py;
this module is purely the model-call boundary.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
BASE_URL = os.environ.get('HOME_LLM_BASE_URL', 'https://llm.canyougrab.it/v1').rstrip('/')
API_KEY = os.environ.get('HOME_LLM_API_KEY', '')
MODEL = os.environ.get('HOME_LLM_MODEL', 'business-name-gen')
CONCURRENCY = int(os.environ.get('HOME_LLM_CONCURRENCY', '4'))
QUEUE_TIMEOUT_S = float(os.environ.get('HOME_LLM_QUEUE_TIMEOUT', '10'))
REQUEST_TIMEOUT_S = float(os.environ.get('HOME_LLM_REQUEST_TIMEOUT', '20'))
BREAKER_THRESHOLD = int(os.environ.get('HOME_LLM_BREAKER_THRESHOLD', '3'))
BREAKER_COOLDOWN_S = float(os.environ.get('HOME_LLM_BREAKER_COOLDOWN', '60'))


# ── Errors surfaced to callers ─────────────────────────────────────────────

class HostedUnavailableError(Exception):
    """The hosted LLM is configured-off or the circuit breaker is open."""


class HostedQueueFullError(Exception):
    """The semaphore couldn't be acquired within QUEUE_TIMEOUT_S."""


# ── Internal state ─────────────────────────────────────────────────────────

_semaphore: Optional[asyncio.Semaphore] = None
_breaker_consecutive_failures = 0
_breaker_open_until = 0.0  # epoch seconds; 0 == closed


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy-init so the semaphore binds to the running event loop."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(CONCURRENCY)
    return _semaphore


def is_configured() -> bool:
    return bool(API_KEY)


def _breaker_is_open() -> bool:
    if _breaker_open_until == 0.0:
        return False
    if time.monotonic() < _breaker_open_until:
        return True
    # Half-open: cooldown elapsed, allow one probe.
    return False


def _on_success() -> None:
    global _breaker_consecutive_failures, _breaker_open_until
    _breaker_consecutive_failures = 0
    _breaker_open_until = 0.0


def _on_failure() -> None:
    global _breaker_consecutive_failures, _breaker_open_until
    _breaker_consecutive_failures += 1
    if _breaker_consecutive_failures >= BREAKER_THRESHOLD:
        _breaker_open_until = time.monotonic() + BREAKER_COOLDOWN_S
        logger.warning(
            'Hosted LLM circuit breaker OPEN for %.0fs after %d failures',
            BREAKER_COOLDOWN_S, _breaker_consecutive_failures,
        )


# ── Prompt ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a startup-name generator. Given a business description, return ONLY "
    "a JSON array of brandable name BASES (no TLDs, no commentary). "
    "Constraints per name: lowercase a-z0-9, 4-18 characters, no spaces, no offensive or "
    "trademark-likely names, mix invented words / evocative metaphors / compound words. "
    "Avoid generic words like app, platform, startup, company."
)


def _user_prompt(description: str, styles: list[str], tld_pref: str, count: int) -> str:
    style_str = ', '.join(s for s in styles if isinstance(s, str)) or 'modern'
    return (
        f'Generate {count} name BASES for this business:\n\n'
        f'{description.strip()[:1000]}\n\n'
        f'Preferred style: {style_str}\n'
        f'Preferred extension type: {tld_pref}\n\n'
        'Return ONLY a JSON array of strings, e.g. ["frondly", "treekit", "leafgraph"].'
    )


# ── Public API ──────────────────────────────────────────────────────────────

async def generate_bases(
    description: str,
    styles: list[str],
    tld_pref: str,
    count: int = 18,
) -> list[str]:
    """Generate brandable name bases via the hosted LLM. Returns a list of
    cleaned base strings (max `count`). Raises HostedUnavailableError if the
    endpoint is not configured or the circuit is open, or HostedQueueFullError
    if all 4 concurrency slots are taken longer than QUEUE_TIMEOUT_S.
    """
    if not is_configured():
        raise HostedUnavailableError('HOME_LLM_API_KEY not set')
    if _breaker_is_open():
        raise HostedUnavailableError('hosted LLM circuit breaker is open')

    sem = _get_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=QUEUE_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HostedQueueFullError(f'all {CONCURRENCY} slots busy for >{QUEUE_TIMEOUT_S:.0f}s')

    try:
        bases = await _call(description, styles, tld_pref, count)
        _on_success()
        return bases
    except (HostedUnavailableError, HostedQueueFullError):
        raise
    except Exception:
        _on_failure()
        raise
    finally:
        sem.release()


async def _call(description: str, styles: list[str], tld_pref: str, count: int) -> list[str]:
    body = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {'role': 'user', 'content': _user_prompt(description, styles, tld_pref, count)},
        ],
        'max_tokens': 512,
        'temperature': 0.9,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        resp = await client.post(
            f'{BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    text = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
    try:
        bases = json.loads(text)
    except json.JSONDecodeError:
        # Some models leak prose around the array — best-effort grab the first [...].
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if not m:
            raise ValueError(f'hosted LLM returned non-JSON: {text[:120]!r}')
        bases = json.loads(m.group(0))

    if not isinstance(bases, list):
        raise ValueError('hosted LLM did not return a list')

    cleaned: list[str] = []
    seen: set[str] = set()
    for b in bases:
        if not isinstance(b, str):
            continue
        c = re.sub(r'[^a-z0-9]', '', b.lower())[:20]
        if 3 <= len(c) <= 20 and c not in seen:
            seen.add(c)
            cleaned.append(c)
        if len(cleaned) >= count:
            break
    return cleaned
