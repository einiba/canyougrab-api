# Anthropic Connectors Directory — Submission Form Responses

Submission URL: https://docs.google.com/forms/d/e/1FAIpQLSeafJF2NDI7oYx1r8o0ycivCSVLNq92Mpc1FPxMKSw1CzDkqA/viewform

Copy-paste these responses into the form fields.

---

## Server Name

CanYouGrab.it

## Short Description

Confidence-scored domain availability checking for AI agents. Check if domains are available for registration with real-time DNS and WHOIS lookups.

## Long Description

CanYouGrab.it provides real-time domain availability checking that AI assistants can't do natively. Unlike binary yes/no checkers, every result includes a confidence score (high/medium/low), data source transparency, and registration details for taken domains.

The server exposes two tools:
- **check_domains** — Check up to 100 domains per request. Returns availability status, confidence level, source (dns/whois/cache), cache age, and registrar details.
- **check_usage** — View your API usage and remaining quota for the current billing period.

All tools are read-only and non-destructive. No user data is modified, no external actions are taken.

## Tools List

### check_domains
- **Title:** Check Domain Availability
- **Description:** Check whether one or more domains are available for registration. Returns confidence, source, cache age, and ambiguous results when the lookup cannot be determined safely.
- **Parameters:** `domains` (list of strings, max 100)
- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: true`

### check_usage
- **Title:** Check API Usage
- **Description:** View your CanYouGrab.it plan, usage, and remaining quota for the current billing period.
- **Parameters:** None
- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false`

## Example Prompts

### Example 1: Quick availability check
**User prompt:** "Is coolstartup.com available?"

**What happens:** The server queries DNS and WHOIS to check real-time availability. Returns whether the domain is registered or available, along with confidence level and source.

### Example 2: Bulk comparison across TLDs
**User prompt:** "Check if any of these are available: grabify.io, grabify.co, grabify.dev, grabify.app"

**What happens:** The server checks all four domains simultaneously. Returns a structured result for each domain showing availability, confidence, and source.

### Example 3: Handle ambiguous results
**User prompt:** "Can I register example.xyz?"

**What happens:** If DNS or WHOIS lookups fail or time out, the result includes `available: null` with `confidence: low`, letting the agent communicate uncertainty to the user rather than guessing.

### Example 4: Usage monitoring
**User prompt:** "How many domain lookups do I have left this month?"

**What happens:** The server returns the current billing period's lookup count, monthly limit, and remaining quota.

## Remote Endpoint

```
https://api.canyougrab.it/mcp
```

Transport: Streamable HTTP

## Authentication

OAuth 2.0 Authorization Code flow with PKCE.

- Authorization Server Metadata: `https://api.canyougrab.it/.well-known/oauth-authorization-server`
- Protected Resource Metadata: `https://api.canyougrab.it/.well-known/oauth-protected-resource/mcp`
- Scopes: `domains.read`, `account.read`, `offline_access`
- Identity Provider: Auth0 (custom domain: `login.canyougrab.it`)

For local STDIO mode, users can also authenticate with an API key via the `CANYOUGRAB_API_KEY` environment variable.

## PyPI Package

```
pip install mcp-server-canyougrab
```

or run directly:

```
uvx mcp-server-canyougrab
```

## Privacy Policy URL

https://canyougrab.it/privacy

## Support Channel

- Email: hello@canyougrab.it
- GitHub Issues: https://github.com/einiba/canyougrab-api/issues

## Test Account

Contact hello@canyougrab.it for a test account with pre-loaded usage data. Test accounts include:
- API key with `domains.read` and `account.read` scopes
- Pre-existing usage history for quota/usage tool testing
- No MFA required

## MCP SDK Version

`mcp>=1.1.3` (Python SDK)

## Token Efficiency

Domain availability responses are under 500 tokens per result. Bulk checks of 10 domains return under 2,000 tokens total. Well under the 25,000 token limit.

## CORS

Configured with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` to support all Claude client origins.
