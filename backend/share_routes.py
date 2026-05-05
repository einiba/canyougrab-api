"""
Share routes for /results/:id pages.

Bots (Twitter, Slack, Discord, iMessage, LinkedIn) don't run JS, so they can't
read OG meta tags injected by the SPA. Instead they fetch the URL and look at
the raw HTML. This module serves a meta-only HTML shim at /share/{id} that
includes OG tags pulled from the saved list, plus a redirect for human browsers.

The OG image is a dynamic SVG generated on the fly from the saved description
and availability count — no Pillow dependency required.
"""

import html
import logging
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from name_gen import get_saved_list

logger = logging.getLogger(__name__)
router = APIRouter(tags=['Share'])

MARKETING_BASE = os.environ.get('MARKETING_BASE_URL', 'https://canyougrab.it')


def _truncate(text: str, n: int) -> str:
    text = (text or '').strip()
    if len(text) <= n:
        return text
    return text[:n - 1].rstrip() + '…'


@router.get('/share/{share_id}', response_class=HTMLResponse)
def share_page(share_id: str):
    """HTML shim served at /share/{id}. Bots see OG meta tags; humans redirect
    to the SPA at canyougrab.it/results/{id}."""
    data = get_saved_list(share_id)
    spa_url = f'{MARKETING_BASE}/results/{share_id}'

    if not data:
        title = 'Domain ideas — canyougrab.it'
        description = 'A canyougrab.it domain-name list.'
    else:
        desc = data.get('description') or ''
        results = (data.get('payload') or {}).get('results') or []
        available = sum(1 for r in results if r.get('available') is True)
        title = _truncate(f'Domain ideas for: {desc}', 90) if desc else 'Domain ideas'
        description = (
            f'{available} available domains found among {len(results)} suggestions. '
            'Generated with live DNS + WHOIS lookups.'
        )

    # Build OG image URL — same backend, dynamic SVG.
    og_image = f'{MARKETING_BASE.rstrip("/")}'  # placeholder; real value built below
    # The image is served by THIS backend, not the marketing site.
    # It's safe to reference an absolute URL on api.canyougrab.it.
    og_image_url = f'/og/results/{share_id}.svg'  # relative — bots resolve from request host

    safe_title = html.escape(title, quote=True)
    safe_desc = html.escape(description, quote=True)
    safe_url = html.escape(spa_url, quote=True)
    safe_img = html.escape(og_image_url, quote=True)

    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<meta name="description" content="{safe_desc}">
<meta property="og:type" content="website">
<meta property="og:title" content="{safe_title}">
<meta property="og:description" content="{safe_desc}">
<meta property="og:url" content="{safe_url}">
<meta property="og:image" content="{safe_img}">
<meta property="og:site_name" content="canyougrab.it">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{safe_title}">
<meta name="twitter:description" content="{safe_desc}">
<meta name="twitter:image" content="{safe_img}">
<link rel="canonical" href="{safe_url}">
<meta http-equiv="refresh" content="0; url={safe_url}">
<style>body{{font-family:system-ui,sans-serif;background:#0a0b0d;color:#e8eaed;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center;padding:1rem}}a{{color:#00d4aa}}</style>
</head>
<body>
<div>
<p>Opening list&hellip;</p>
<p>If nothing happens, <a href="{safe_url}">click here</a>.</p>
</div>
<script>window.location.replace({safe_url!r});</script>
</body>
</html>
"""
    return HTMLResponse(content=body, status_code=200, headers={
        'Cache-Control': 'public, max-age=300',
    })


@router.get('/og/results/{share_id}.svg')
def og_image(share_id: str):
    """Dynamic OG image (1200x630) generated as SVG. Includes the description
    and availability count. No Pillow dependency required."""
    data = get_saved_list(share_id)
    if not data:
        desc = 'Find a domain that’s actually available'
        avail_text = ''
    else:
        desc = (data.get('description') or '').strip()
        results = (data.get('payload') or {}).get('results') or []
        available = sum(1 for r in results if r.get('available') is True)
        avail_text = f'{available} of {len(results)} available'

    # SVG text wraps poorly natively; we manually wrap by splitting into ~24-char lines.
    desc_text = _truncate(desc, 180)
    lines = _wrap(desc_text, 30)[:5]
    line_dy = 50
    line_y0 = 230 if len(lines) <= 3 else 210

    line_tspans = '\n'.join(
        f'<tspan x="80" dy="{0 if i == 0 else line_dy}">{html.escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )

    safe_avail = html.escape(avail_text)

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0a0b0d"/>
      <stop offset="100%" stop-color="#12141a"/>
    </linearGradient>
    <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#00d4aa"/>
      <stop offset="100%" stop-color="#00e6b8"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <circle cx="1080" cy="120" r="220" fill="#00d4aa" opacity="0.06"/>
  <circle cx="80" cy="540" r="180" fill="#00d4aa" opacity="0.04"/>

  <!-- brand -->
  <text x="80" y="100" font-family="Inter, system-ui, sans-serif" font-size="32" font-weight="600" fill="#e8eaed">
    canyougrab<tspan fill="#00d4aa">.it</tspan>
  </text>

  <!-- label -->
  <text x="80" y="160" font-family="Inter, system-ui, sans-serif" font-size="22" font-weight="500" fill="url(#accent)" letter-spacing="2">
    DOMAIN IDEAS FOR
  </text>

  <!-- description (wrapped) -->
  <text x="80" y="{line_y0}" font-family="Inter, system-ui, sans-serif" font-size="42" font-weight="600" fill="#e8eaed">
    {line_tspans}
  </text>

  <!-- availability footer -->
  <text x="80" y="560" font-family="JetBrains Mono, ui-monospace, monospace" font-size="28" fill="#8b8f98">
    {safe_avail}
  </text>
  <text x="80" y="595" font-family="Inter, system-ui, sans-serif" font-size="22" fill="#8b8f98">
    Generated live from DNS + WHOIS
  </text>
</svg>
"""
    return Response(
        content=svg,
        media_type='image/svg+xml',
        headers={'Cache-Control': 'public, max-age=600'},
    )


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word-wrap. Doesn't try to be clever about hyphenation."""
    if not text:
        return [text]
    out: list[str] = []
    current = ''
    for word in text.split():
        candidate = f'{current} {word}'.strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                out.append(current)
            current = word
    if current:
        out.append(current)
    return out or [text]
