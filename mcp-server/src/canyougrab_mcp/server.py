"""
CanYouGrab.it MCP Server.

Provides confidence-scored domain availability checking for AI agents.
Each result includes availability status, confidence level (high/medium/low),
data source, cache freshness, and registration details when available.

Two modes:
  - STDIO (default): API key from CANYOUGRAB_API_KEY env var.
    For Claude Desktop, VS Code, Cursor, etc.
  - Streamable HTTP (--streamable-http): API key from client's Authorization header.
    For Claude API, ChatGPT, and other remote MCP clients.
"""

import contextlib
import os
import sys
from contextvars import ContextVar

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

API_BASE = os.environ.get("CANYOUGRAB_API_URL", "https://api.canyougrab.it")

# For remote (HTTP) transport, the client's Bearer token is stored per-request.
_request_api_key: ContextVar[str] = ContextVar("request_api_key", default="")

REMOTE_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "127.0.0.1:*", "localhost:*", "[::1]:*",
        "api.canyougrab.it", "dev-api.canyougrab.it",
    ],
)

mcp = FastMCP(
    "CanYouGrab.it",
    stateless_http=True,
    instructions=(
        "Domain availability intelligence with confidence scoring. "
        "Use check_domains to verify if domains are available for registration. "
        "Results include a confidence level: 'high' means verified by DNS+WHOIS, "
        "'medium' means DNS-only (WHOIS unavailable), 'low' means lookup failed. "
        "When available is null, the lookup could not determine availability. "
        "Check up to 100 domains per request."
    ),
)


def _get_api_key() -> str:
    """Get API key from context (remote) or environment (STDIO)."""
    key = _request_api_key.get("") or os.environ.get("CANYOUGRAB_API_KEY", "")
    if not key:
        raise ValueError(
            "CANYOUGRAB_API_KEY environment variable is required. "
            "Get your API key at https://portal.canyougrab.it"
        )
    return key


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def check_domains(domains: list[str]) -> dict:
    """Check domain name availability with confidence scoring.

    Returns availability status, confidence level, data source, and
    registration details for each domain. Checks up to 100 domains
    per request.

    Confidence levels:
    - high: Verified by DNS + WHOIS, or fresh DNS with active nameservers.
    - medium: DNS only (WHOIS was unavailable or timed out).
    - low: DNS failure, timeout, or stale cache.

    Args:
        domains: List of domain names to check (e.g. ["example.com", "myapp.io"]).
                 Maximum 100 domains per request.
    """
    if not domains:
        return {"error": "Provide at least one domain to check"}
    if len(domains) > 100:
        return {"error": "Maximum 100 domains per request"}

    api_key = _get_api_key()

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{API_BASE}/api/check/bulk",
            json={"domains": domains},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code == 429:
        data = resp.json()
        return {"error": "Rate limit exceeded", "detail": data.get("message", "")}
    if resp.status_code == 401:
        return {"error": "Invalid API key. Get yours at https://portal.canyougrab.it"}
    if resp.status_code != 200:
        return {"error": f"API error (HTTP {resp.status_code})", "detail": resp.text}

    return resp.json()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def check_usage() -> dict:
    """Check your CanYouGrab.it API usage for the current billing period.

    Returns lookup counts and remaining quota.
    """
    api_key = _get_api_key()

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{API_BASE}/api/account/usage",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    if resp.status_code != 200:
        return {"error": f"API error (HTTP {resp.status_code})"}

    return resp.json()


def _create_remote_app():
    """Create a Starlette app for remote MCP with auth forwarding."""
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount

    class AuthForwardMiddleware(BaseHTTPMiddleware):
        """Extract Bearer token from request and store in contextvar."""
        async def dispatch(self, request: Request, call_next):
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
                tok = _request_api_key.set(token)
                try:
                    return await call_next(request)
                finally:
                    _request_api_key.reset(tok)
            return await call_next(request)

    # Configure MCP transport security to allow proxied hosts
    mcp.settings.transport_security = REMOTE_SECURITY

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[Mount("/", app=mcp.streamable_http_app())],
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=["*"]),
            Middleware(AuthForwardMiddleware),
        ],
        lifespan=lifespan,
    )


def main():
    if "--streamable-http" in sys.argv:
        import uvicorn
        app = _create_remote_app()
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8001"))
        uvicorn.run(
            app, host=host, port=port,
            proxy_headers=True,
            forwarded_allow_ips="*",
            server_header=False,
        )
    elif "--sse" in sys.argv:
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
