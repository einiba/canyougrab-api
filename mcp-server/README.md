# CanYouGrab.it MCP Server

Confidence-scored domain availability checking for AI agents. Works with Claude, ChatGPT, Gemini, Copilot, and any MCP-compatible client.

Unlike other domain checkers that return binary yes/no, CanYouGrab.it tells you **how confident** the result is — so your AI agent can make smarter decisions.

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

Connect to the remote endpoint:

```
https://api.canyougrab.it/mcp
```

## Get an API Key

Sign up at [portal.canyougrab.it](https://portal.canyougrab.it) to get your API key.

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

**Example 1: Check a single domain**

```
User: "Is mybrand.com available?"

→ check_domains(["mybrand.com"])

{
  "results": [
    {
      "domain": "mybrand.com",
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

The domain is taken (available: false) with high confidence. Registration details show the registrar and expiry date.

**Example 2: Bulk check multiple domains across TLDs**

```
User: "Check if coolstartup.com, coolstartup.io, and coolstartup.dev are available"

→ check_domains(["coolstartup.com", "coolstartup.io", "coolstartup.dev"])

{
  "results": [
    {
      "domain": "coolstartup.com",
      "available": false,
      "confidence": "high",
      "source": "dns",
      "checked_at": "2026-03-17T12:00:00Z",
      "cache_age_seconds": 0,
      "registration": null
    },
    {
      "domain": "coolstartup.io",
      "available": true,
      "confidence": "high",
      "source": "whois",
      "checked_at": "2026-03-17T12:00:00Z",
      "cache_age_seconds": 0,
      "registration": null
    },
    {
      "domain": "coolstartup.dev",
      "available": true,
      "confidence": "high",
      "source": "dns",
      "checked_at": "2026-03-17T12:00:00Z",
      "cache_age_seconds": 0,
      "registration": null
    }
  ]
}
```

Two of the three domains are available with high confidence. The agent can recommend registering the .io or .dev variants.

**Example 3: Handle ambiguous results**

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

When available is null with low confidence, the lookup was inconclusive (e.g., DNS timeout or WHOIS failure). The agent should inform the user that availability could not be determined and suggest trying again later.

### check_usage

Check your current API usage and remaining quota.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CANYOUGRAB_API_KEY` | Yes | — | Your API key from portal.canyougrab.it |
| `CANYOUGRAB_API_URL` | No | `https://api.canyougrab.it` | API base URL (for testing) |

## Privacy Policy

See our privacy policy: [https://canyougrab.it/privacy](https://canyougrab.it/privacy)

**Summary**: We collect only the domain names submitted for checks and request metadata (timestamps, API key identifier) for billing and rate limiting. We do not receive or store your AI assistant conversations. Query logs are retained for 30 days. We do not sell your data.

## Support

- Email: [hello@canyougrab.it](mailto:hello@canyougrab.it)
- GitHub Issues: [github.com/ericismaking/canyougrab-api/issues](https://github.com/ericismaking/canyougrab-api/issues)

## MCP Registry

mcp-name: io.github.ericismaking/canyougrab

## License

MIT
