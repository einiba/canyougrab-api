# CanYouGrab.it MCP Server

Real-time, confidence-scored domain availability checking for AI agents. Works with Claude, ChatGPT, Gemini, Copilot, and any MCP-compatible client.

AI assistants cannot check domain availability on their own — they guess, and they're usually wrong. CanYouGrab.it solves this by providing live DNS + WHOIS lookups with confidence scoring, so your AI agent knows **how reliable** each result is.

## Features

- **Confidence scoring** — every result is rated high/medium/low so the agent can qualify its answer
- **Bulk checking** — up to 100 domains per request
- **WHOIS enrichment** — registrar, creation date, expiry, nameservers for registered domains
- **Domain info lookup** — detailed RDAP/WHOIS data for any registered domain
- **Read-only and safe** — all tools are non-destructive with correct MCP safety annotations
- **Fast** — responses typically under 2 seconds, cached results under 100ms

## Quick Start

### Claude Desktop / Claude Code

Add to your MCP config (`~/.claude/.mcp.json` or Claude Desktop settings):

```json
{
  "mcpServers": {
    "canyougrab": {
      "command": "uvx",
      "args": ["mcp-server-canyougrab"],
      "env": {
        "CANYOUGRAB_API_KEY": "cyg_your_key_here"
      }
    }
  }
}
```

### VS Code / Cursor

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "canyougrab": {
      "command": "uvx",
      "args": ["mcp-server-canyougrab"],
      "env": {
        "CANYOUGRAB_API_KEY": "cyg_your_key_here"
      }
    }
  }
}
```

### ChatGPT / Remote Clients

Connect to the remote endpoint (OAuth 2.0 authentication):

```
https://api.canyougrab.it/mcp
```

## Get an API Key

Sign up at [portal.canyougrab.it](https://portal.canyougrab.it) to get your API key. A free tier is available for basic lookups.

---

## Tools

### check_domains

Check availability of up to 100 domains per request. Each result includes:

- **available**: `true` (registrable), `false` (taken), or `null` (couldn't determine)
- **confidence**: `high` / `medium` / `low`
- **source**: `dns` / `whois` / `cache`
- **checked_at**: When the data was gathered
- **cache_age_seconds**: How fresh the data is
- **registration**: Registrar, creation date, expiry date (for taken domains)

#### Confidence Levels

| Level | Meaning |
|-------|---------|
| **high** | Verified by DNS + WHOIS, or fresh DNS with active nameservers |
| **medium** | DNS only — WHOIS was unavailable or timed out |
| **low** | DNS failure, timeout, or stale cached data |

#### Examples

**Example 1: Quick availability check**

```
User: "Is coolstartup.com available?"

→ check_domains(["coolstartup.com"])

{
  "results": [
    {
      "domain": "coolstartup.com",
      "available": false,
      "confidence": "high",
      "source": "dns",
      "checked_at": "2026-03-17T12:00:00Z",
      "cache_age_seconds": 0,
      "registration": {
        "registrar": "GoDaddy.com, LLC",
        "created": "2015-06-12",
        "expires": "2027-06-12"
      }
    }
  ]
}
```

The domain is taken with high confidence. Registration details show the registrar and expiry date.

**Example 2: Bulk comparison across TLDs**

```
User: "Check if grabify.io, grabify.co, grabify.dev, and grabify.app are available"

→ check_domains(["grabify.io", "grabify.co", "grabify.dev", "grabify.app"])

{
  "results": [
    { "domain": "grabify.io", "available": false, "confidence": "high", "source": "dns" },
    { "domain": "grabify.co", "available": true, "confidence": "high", "source": "whois" },
    { "domain": "grabify.dev", "available": true, "confidence": "high", "source": "dns" },
    { "domain": "grabify.app", "available": true, "confidence": "high", "source": "dns" }
  ]
}
```

Three of four domains are available. The agent can recommend the best option.

**Example 3: Handling ambiguous results**

```
User: "Can I register example.xyz?"

→ check_domains(["example.xyz"])

{
  "results": [
    {
      "domain": "example.xyz",
      "available": null,
      "confidence": "low",
      "source": "dns",
      "checked_at": "2026-03-17T12:00:00Z",
      "cache_age_seconds": 0,
      "registration": null
    }
  ]
}
```

When `available` is `null` with low confidence, the lookup was inconclusive (DNS timeout, WHOIS failure). The agent should inform the user and suggest trying again.

---

### get_domain_info

Get WHOIS/RDAP information for a registered domain — registrar, dates, nameservers, and status codes.

#### Example

**Example 4: Domain reconnaissance**

```
User: "Who owns example.com and when does it expire?"

→ get_domain_info("example.com")

{
  "domain": "example.com",
  "registrar": "RESERVED-Internet Assigned Numbers Authority",
  "created_date": "1995-08-14T00:00:00Z",
  "expiry_date": "2025-08-13T00:00:00Z",
  "updated_date": "2024-08-14T07:01:38Z",
  "nameservers": ["a.iana-servers.net", "b.iana-servers.net"],
  "status": ["clientDeleteProhibited", "clientTransferProhibited", "clientUpdateProhibited"],
  "source": "rdap"
}
```

Returns structured WHOIS data including registrar, registration dates, nameservers, and EPP status codes.

---

### check_usage

Check your current API usage and remaining quota for the billing period.

#### Example

**Example 5: Monitor your quota**

```
User: "How many domain lookups do I have left this month?"

→ check_usage()

{
  "plan": "starter",
  "lookups_today": 847,
  "lookups_limit": 5000,
  "period": "monthly"
}
```

---

## Safety Annotations

All tools include correct MCP safety annotations:

| Tool | readOnlyHint | destructiveHint | idempotentHint | openWorldHint |
|------|-------------|-----------------|----------------|---------------|
| check_domains | true | false | true | true |
| get_domain_info | true | false | true | false |
| check_usage | true | false | true | false |

All operations are **read-only** — no user data is modified, no external actions are taken.

---

## Negative Test Prompts

These prompts should **not** trigger CanYouGrab.it tools:

- "What's the weather in New York?"
- "Write me a poem about the ocean"
- "How do I cook pasta?"
- "What is the capital of France?"
- "Summarize this article for me"
- "Help me write a Python script"
- "What time is it?"

The tools should only activate for domain-related queries: availability checks, WHOIS lookups, domain name suggestions, and API usage inquiries.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CANYOUGRAB_API_KEY` | Yes (STDIO mode) | — | Your API key from portal.canyougrab.it |
| `CANYOUGRAB_API_URL` | No | `https://api.canyougrab.it` | API base URL override |

## Privacy Policy

See our privacy policy: [https://canyougrab.it/privacy](https://canyougrab.it/privacy)

**Summary**: We collect only the domain names submitted for checks and request metadata (timestamps, API key identifier) for billing and rate limiting. We do not receive or store your AI assistant conversations. Query logs are retained for 30 days. We do not sell your data.

## Support

- Email: [support@canyougrab.it](mailto:support@canyougrab.it)
- GitHub Issues: [github.com/einiba/canyougrab-api/issues](https://github.com/einiba/canyougrab-api/issues)

## MCP Registry

mcp-name: io.github.einiba/canyougrab

## License

MIT
