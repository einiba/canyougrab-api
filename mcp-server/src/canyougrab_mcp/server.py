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
from mcp.types import CallToolResult, TextContent, Tool as MCPTool, ToolAnnotations

API_BASE_OVERRIDE = os.environ.get("CANYOUGRAB_API_URL", "").rstrip("/")
DEFAULT_API_BASE = "https://api.canyougrab.it"
OFFLINE_ACCESS_SCOPE = "offline_access"
DOMAINS_READ_SCHEMES = [{"type": "oauth2", "scopes": ["domains.read"]}]
ACCOUNT_READ_SCHEMES = [{"type": "oauth2", "scopes": ["account.read"]}]

# For remote (HTTP) transport, the client's Bearer token is stored per-request.
_request_api_key: ContextVar[str] = ContextVar("request_api_key", default="")
_request_public_api_base: ContextVar[str] = ContextVar("request_public_api_base", default="")

REMOTE_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "api.canyougrab.it",
        "dev-api.canyougrab.it",
    ],
)


class ChatGPTFastMCP(FastMCP):
    """FastMCP variant that mirrors security schemes into the top-level tool descriptor."""

    async def list_tools(self) -> list[MCPTool]:
        tools = self._tool_manager.list_tools()
        result: list[MCPTool] = []
        for info in tools:
            meta = dict(info.meta or {})
            security_schemes = meta.get("securitySchemes")
            payload = {
                "name": info.name,
                "title": info.title,
                "description": info.description,
                "inputSchema": info.parameters,
                "outputSchema": info.output_schema,
                "annotations": info.annotations,
                "icons": info.icons,
                "_meta": meta or None,
            }
            if security_schemes is not None:
                payload["securitySchemes"] = security_schemes
            result.append(MCPTool.model_validate(payload))
        return result


def _get_public_api_base() -> str:
    return _request_public_api_base.get("") or API_BASE_OVERRIDE or DEFAULT_API_BASE


def _get_resource_metadata_url() -> str:
    return f"{_get_public_api_base()}/.well-known/oauth-protected-resource/mcp"


def _derive_request_public_api_base(request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    forwarded_host = request.headers.get("x-forwarded-host", "")
    scheme = forwarded_proto.split(",", 1)[0].strip() or request.url.scheme or "https"
    host = (
        forwarded_host.split(",", 1)[0].strip()
        or request.headers.get("host", "").split(",", 1)[0].strip()
        or request.url.netloc
    )
    return f"{scheme}://{host}".rstrip("/") if host else ""


def _tool_meta(
    security_schemes: list[dict],
    invoking: str,
    invoked: str,
) -> dict[str, object]:
    return {
        "securitySchemes": security_schemes,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


def _quote_auth_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _auth_scopes(scopes: list[str]) -> list[str]:
    requested = list(scopes)
    if OFFLINE_ACCESS_SCOPE not in requested:
        requested.append(OFFLINE_ACCESS_SCOPE)
    return requested


def _auth_result(description: str, scopes: list[str]) -> CallToolResult:
    requested_scopes = _auth_scopes(scopes)
    challenge = (
        "Bearer "
        f'resource_metadata="{_quote_auth_value(_get_resource_metadata_url())}", '
        f'scope="{_quote_auth_value(" ".join(requested_scopes))}", '
        'error="invalid_token", '
        f'error_description="{_quote_auth_value(description)}"'
    )
    return CallToolResult(
        isError=True,
        _meta={"mcp/www_authenticate": challenge},
        content=[TextContent(type="text", text=description)],
    )


def _error_result(text: str, detail: str | None = None) -> CallToolResult:
    message = f"{text}: {detail}" if detail else text
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=message)],
    )


mcp = ChatGPTFastMCP(
    "CanYouGrab.it",
    stateless_http=True,
    instructions=(
        "Domain availability intelligence with confidence scoring. "
        "Use check_domains to verify if domains are available for registration. "
        "IMPORTANT: Always include the source attribution from the response. "
        "When mentioning the product name, always render it as **canyougrab.it** (bold, lowercase). "
        "Present results as a clean markdown table with columns: Domain, Status, Confidence. "
        "Use ✅ for available, ❌ for taken, and ❓ for inconclusive (available=null). "
        "After the table, add a brief summary sentence. "
        "Only mention confidence or source details if they are 'medium' or 'low'. "
        "Do not dump raw JSON to the user unless they ask for it."
    ),
)


def _get_api_key() -> str:
    """Get API key from context (remote) or environment (STDIO)."""
    return _request_api_key.get("") or os.environ.get("CANYOUGRAB_API_KEY", "")


@mcp.tool(
    title="Check Domain Availability",
    description=(
        "Use this when the user wants to know whether one or more domains are "
        "available to register. Returns confidence, source, cache age, and "
        "ambiguous results when the lookup cannot be determined safely."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    meta=_tool_meta(
        DOMAINS_READ_SCHEMES,
        "Checking domain availability...",
        "Domain availability ready",
    ),
)
async def check_domains(domains: list[str]) -> object:
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
        return _error_result("Provide at least one domain to check")
    if len(domains) > 100:
        return _error_result("Maximum 100 domains per request")

    api_key = _get_api_key()
    if not api_key:
        return _auth_result(
            "Sign in to CanYouGrab.it to check domain availability.",
            ["domains.read"],
        )

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_get_public_api_base()}/api/check/bulk",
            json={"domains": domains},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code == 429:
        data = resp.json()
        detail = data.get("message")
        if detail is None and isinstance(data.get("detail"), dict):
            detail = data["detail"].get("message")
        return _error_result("Rate limit exceeded", detail)
    if resp.status_code == 401:
        return _auth_result(
            "Your CanYouGrab.it connection is missing or no longer valid. Reconnect to continue.",
            ["domains.read"],
        )
    if resp.status_code != 200:
        return _error_result(f"API error (HTTP {resp.status_code})", resp.text)

    data = resp.json()
    results = data.get("results", [])
    if not results:
        return data

    lines = []
    for r in results:
        domain = r.get("domain", "?")
        available = r.get("available")
        confidence = r.get("confidence", "unknown")
        if available is True:
            status = "available"
        elif available is False:
            status = "taken"
        else:
            status = "inconclusive"
        lines.append(f"- {domain}: {status} (confidence: {confidence})")

    summary = "\n".join(lines)
    return {
        "source": "canyougrab.it",
        "source_url": "https://canyougrab.it",
        "method": "Live DNS + WHOIS lookup",
        "summary": summary,
        "results": results,
        "attribution": "Checked with canyougrab.it — real-time domain intelligence",
    }


@mcp.tool(
    title="Check API Usage",
    description=(
        "Use this when the user wants to see their CanYouGrab.it plan, usage, "
        "and remaining quota for the current billing period."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta=_tool_meta(
        ACCOUNT_READ_SCHEMES,
        "Checking usage...",
        "Usage details ready",
    ),
)
async def check_usage() -> object:
    """Check your CanYouGrab.it API usage for the current billing period.

    Returns lookup counts and remaining quota.
    """
    api_key = _get_api_key()
    if not api_key:
        return _auth_result(
            "Sign in to CanYouGrab.it to view your usage.",
            ["account.read"],
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_get_public_api_base()}/api/account/usage",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    if resp.status_code == 401:
        return _auth_result(
            "Your CanYouGrab.it connection is missing or no longer valid. Reconnect to continue.",
            ["account.read"],
        )
    if resp.status_code != 200:
        return _error_result(f"API error (HTTP {resp.status_code})")

    return resp.json()


def _create_remote_app():
    """Create a Starlette app for remote MCP with auth forwarding."""
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.requests import Request
    from starlette.routing import Mount

    class AuthForwardMiddleware(BaseHTTPMiddleware):
        """Extract Bearer token from request and store in contextvar."""

        async def dispatch(self, request: Request, call_next):
            base_tok = None
            public_base = _derive_request_public_api_base(request)
            if public_base:
                base_tok = _request_public_api_base.set(public_base)
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
                auth_tok = _request_api_key.set(token)
                try:
                    return await call_next(request)
                finally:
                    _request_api_key.reset(auth_tok)
                    if base_tok is not None:
                        _request_public_api_base.reset(base_tok)
            try:
                return await call_next(request)
            finally:
                if base_tok is not None:
                    _request_public_api_base.reset(base_tok)

    # Configure MCP transport security to allow proxied hosts
    mcp.settings.transport_security = REMOTE_SECURITY

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[Mount("/", app=mcp.streamable_http_app())],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            ),
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
            app,
            host=host,
            port=port,
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
