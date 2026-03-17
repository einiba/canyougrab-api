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

#### Example

```
User: "Check if coolstartup.com and coolstartup.io are available"

→ check_domains(["coolstartup.com", "coolstartup.io"])

{
  "results": [
    {
      "domain": "coolstartup.com",
      "available": false,
      "confidence": "high",
      "source": "dns",
      "registration": null
    },
    {
      "domain": "coolstartup.io",
      "available": true,
      "confidence": "high",
      "source": "whois",
      "registration": null
    }
  ]
}
```

### check_usage

Check your current API usage and remaining quota.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CANYOUGRAB_API_KEY` | Yes | — | Your API key from portal.canyougrab.it |
| `CANYOUGRAB_API_URL` | No | `https://api.canyougrab.it` | API base URL (for testing) |

## License

MIT
