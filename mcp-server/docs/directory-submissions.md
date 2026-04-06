# Community Directory Submissions

Pre-written descriptions and PR templates for each directory listing.

---

## Short Description (under 100 chars)

Confidence-scored domain availability checking for AI agents via MCP.

## Standard Description (under 300 chars)

Real-time domain availability checking with confidence scoring. Check if domains are available for registration using live DNS and WHOIS lookups. Each result includes availability, confidence level (high/medium/low), data source, and registration details. Read-only, non-destructive.

## Long Description

CanYouGrab.it provides real-time domain availability checking that AI assistants can't do natively. Unlike binary yes/no checkers, every result includes a confidence score (high/medium/low), data source transparency, and registration details for taken domains.

**Tools:**
- `check_domains` — Check up to 100 domains per request with confidence scoring
- `check_usage` — View API usage and remaining quota

**Install:**
```bash
uvx mcp-server-canyougrab
```

**Remote:**
```
https://api.canyougrab.it/mcp
```

---

## 1. MCP.so

**URL:** https://mcp.so
**Method:** GitHub issue or submission form

**Title:** CanYouGrab.it — Domain Availability Checker
**Description:** Confidence-scored domain availability checking for AI agents. Check if domains are available for registration with real-time DNS and WHOIS lookups. Returns availability, confidence level, data source, and registration details.
**Category:** Developer Tools
**Package:** `mcp-server-canyougrab` (PyPI)
**Remote URL:** `https://api.canyougrab.it/mcp`

---

## 2. Smithery

**URL:** https://smithery.ai
**Method:** GitHub PR

**PR Title:** Add CanYouGrab.it domain availability checker

**server.yaml:**
```yaml
name: canyougrab
displayName: CanYouGrab.it
description: Confidence-scored domain availability checking with real-time DNS and WHOIS lookups
icon: https://canyougrab.it/favicon.svg
sourceUrl: https://github.com/einiba/canyougrab-api
package:
  name: mcp-server-canyougrab
  registry: pypi
  runtime: python
  command: uvx mcp-server-canyougrab
remote:
  url: https://api.canyougrab.it/mcp
  transport: streamable-http
```

---

## 3. Glama.ai

**URL:** https://glama.ai/mcp/servers
**Method:** Submission form

**Server Name:** CanYouGrab.it
**Description:** Real-time domain availability checking with confidence scoring. Each result includes availability status, confidence level (high/medium/low), data source (dns/whois/cache), and registration details for taken domains.
**GitHub URL:** https://github.com/einiba/canyougrab-api
**PyPI Package:** mcp-server-canyougrab
**Remote Endpoint:** https://api.canyougrab.it/mcp

---

## 4. PulseMCP

**URL:** https://pulsemcp.com
**Method:** Submission form

**Title:** CanYouGrab.it
**URL:** https://github.com/einiba/canyougrab-api
**Description:** Domain availability checking with confidence scoring for AI agents. Returns real-time DNS + WHOIS results with high/medium/low confidence levels.
**Category:** Utilities

---

## 5. Awesome MCP Servers

**URL:** https://github.com/punkpeye/awesome-mcp-servers
**Method:** GitHub PR

**PR Title:** Add CanYouGrab.it domain availability checker

**Entry to add (under appropriate category, e.g., "Web & Search" or "Utilities"):**
```markdown
- [CanYouGrab.it](https://github.com/einiba/canyougrab-api) - Confidence-scored domain availability checking with real-time DNS and WHOIS lookups.
```

---

## 6. Official MCP Servers (Community Section)

**URL:** https://github.com/modelcontextprotocol/servers
**Method:** GitHub PR to community servers list

**PR Title:** Add CanYouGrab.it — domain availability checking

**Entry:**
```markdown
- **[CanYouGrab.it](https://github.com/einiba/canyougrab-api)** - Real-time domain availability checking with confidence scoring. Returns availability, confidence level, data source, and registration details via DNS + WHOIS lookups.
```

**PR Description:**
```markdown
## Summary

Adds CanYouGrab.it to the community MCP servers list.

CanYouGrab.it provides real-time domain availability checking — something LLMs can't do natively. Each result includes a confidence score (high/medium/low) so the AI agent can communicate certainty to the user.

- **Package:** `mcp-server-canyougrab` on PyPI
- **Remote:** `https://api.canyougrab.it/mcp` (Streamable HTTP)
- **Tools:** `check_domains`, `check_usage`
- **Auth:** OAuth 2.0 (remote) or API key (STDIO)
- **Safety:** All tools annotated with `readOnlyHint: true`, `destructiveHint: false`
```

---

## 7. Docker MCP Catalog

**URL:** https://docker.com
**Method:** Submission form (when available)

**Server Name:** mcp-server-canyougrab
**Description:** Confidence-scored domain availability checking for AI agents. Real-time DNS and WHOIS lookups with high/medium/low confidence levels.
**Source:** https://github.com/einiba/canyougrab-api
**Package:** PyPI: mcp-server-canyougrab

---

## 8. MCP Registry (Anthropic)

**URL:** https://registry.modelcontextprotocol.io
**Method:** When open for submissions

The `server.json` file in the repo root is already formatted for the MCP registry schema (2025-12-11):

```json
{
  "name": "io.github.einiba/canyougrab",
  "title": "CanYouGrab.it",
  "description": "Confidence-scored domain availability checking for AI agents via CanYouGrab.it",
  "version": "1.0.2",
  "packages": [{"registryType": "pypi", "identifier": "mcp-server-canyougrab"}],
  "remotes": [{"type": "streamable-http", "url": "https://api.canyougrab.it/mcp"}]
}
```

---

## Submission Order

1. Awesome MCP Servers (highest visibility, PR-based)
2. Official MCP Servers repo (most credible, PR-based)
3. MCP.so (large catalog)
4. Smithery (developer-focused)
5. Glama.ai (growing catalog)
6. PulseMCP (discovery)
7. Docker MCP Catalog (when available)
8. MCP Registry (when open)

Submit to GitHub PR-based directories first — accepted PRs create permanent backlinks that help with the form-based submissions.
