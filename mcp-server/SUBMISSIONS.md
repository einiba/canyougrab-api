# Community Directory Submission Templates

Ready-to-submit content for each MCP community directory.

---

## 1. MCP.so — GitHub Issue

**Title:** Add CanYouGrab.it — Domain Availability Checking

**Body:**

### Server Name
CanYouGrab.it

### Description
Real-time, confidence-scored domain availability checking for AI agents. Solves a known AI weakness — LLMs cannot check domain availability and consistently hallucinate answers. CanYouGrab.it provides live DNS + WHOIS lookups with confidence scoring (high/medium/low) so agents know how reliable each result is.

### Tools
- **check_domains** — Check availability of up to 100 domains with confidence scoring
- **get_domain_info** — WHOIS/RDAP lookup for registered domains (registrar, dates, nameservers)
- **check_usage** — Check API quota and billing status

### Transport
- STDIO (PyPI: `mcp-server-canyougrab`)
- Streamable HTTP (`https://api.canyougrab.it/mcp`)

### Links
- Website: https://canyougrab.it
- PyPI: https://pypi.org/project/mcp-server-canyougrab/
- GitHub: https://github.com/einiba/canyougrab-api
- Privacy Policy: https://canyougrab.it/privacy

---

## 2. Smithery — GitHub PR

**Title:** Add CanYouGrab.it domain availability server

**Description for smithery.yaml or PR body:**

```yaml
name: canyougrab
description: Real-time domain availability checking with confidence scoring via DNS + WHOIS
author: einiba
homepage: https://canyougrab.it
repository: https://github.com/einiba/canyougrab-api
license: MIT
categories:
  - utilities
  - developer-tools
tools:
  - check_domains
  - get_domain_info
  - check_usage
install:
  pypi: mcp-server-canyougrab
  remote: https://api.canyougrab.it/mcp
```

---

## 3. Glama.ai — Submission Form

**Server Name:** CanYouGrab.it
**Description:** Real-time, confidence-scored domain availability checking for AI agents. Live DNS + WHOIS lookups with high/medium/low confidence ratings. Checks up to 100 domains per request. All tools are read-only and safe.
**Repository URL:** https://github.com/einiba/canyougrab-api
**Package:** mcp-server-canyougrab (PyPI)
**Remote URL:** https://api.canyougrab.it/mcp

---

## 4. PulseMCP — Submission Form

**Server Name:** CanYouGrab.it
**Short Description:** Confidence-scored domain availability checking via live DNS + WHOIS
**Long Description:** AI assistants cannot check domain availability — they guess, and they're usually wrong. CanYouGrab.it fixes this with real-time DNS + WHOIS lookups that include confidence scoring. Every result is rated high/medium/low so the agent knows how reliable the answer is. Supports bulk checking (up to 100 domains), WHOIS lookups, and quota monitoring. All tools are read-only with correct MCP safety annotations.
**Category:** Developer Tools / Utilities
**Website:** https://canyougrab.it
**Repository:** https://github.com/einiba/canyougrab-api
**Install:** `uvx mcp-server-canyougrab` or remote at `https://api.canyougrab.it/mcp`

---

## 5. Awesome MCP Servers — GitHub PR

**Section:** Utilities / Domain Tools

**Entry to add:**

```markdown
- [CanYouGrab.it](https://github.com/einiba/canyougrab-api) - Real-time domain availability checking with confidence scoring via DNS + WHOIS. Bulk check up to 100 domains, WHOIS lookups, quota monitoring. [PyPI](https://pypi.org/project/mcp-server-canyougrab/)
```

---

## 6. Official MCP Servers Repo (Community Section) — GitHub PR

**File to edit:** `README.md` (community servers section)

**Entry:**

```markdown
- **[CanYouGrab.it](https://github.com/einiba/canyougrab-api)** - Confidence-scored domain availability checking via live DNS + WHOIS lookups
```

---

## 7. Docker MCP Catalog — Submission Form

**Name:** CanYouGrab.it
**Description:** Confidence-scored domain availability checking for AI agents via live DNS + WHOIS. Bulk domain checks, WHOIS lookups, and usage monitoring.
**Image:** N/A (PyPI package: mcp-server-canyougrab, or remote endpoint)
**Repository:** https://github.com/einiba/canyougrab-api
**Documentation:** https://github.com/einiba/canyougrab-api/blob/main/mcp-server/README.md

---

## 8. Anthropic Connectors Directory — Google Form

**Form URL:** https://docs.google.com/forms/d/e/1FAIpQLSeafJF2NDI7oYx1r8o0ycivCSVLNq92Mpc1FPxMKSw1CzDkqA/viewform

**Server Name:** CanYouGrab.it
**Description:** Real-time domain availability checking with confidence scoring for Claude. Fills a known AI weakness — LLMs cannot check domain availability and consistently hallucinate answers. All tools are read-only with correct safety annotations (readOnlyHint: true, destructiveHint: false). Built on the MCP SDK with Streamable HTTP transport.
**Remote URL:** https://api.canyougrab.it/mcp
**Auth:** OAuth 2.0 via Auth0 (authorization code flow)
**Tools:** check_domains, get_domain_info, check_usage
**Privacy Policy:** https://canyougrab.it/privacy
**Support:** support@canyougrab.it + https://github.com/einiba/canyougrab-api/issues
**Test Account:** (provision before submitting — see test account script)

**Example Prompts:**
1. "Is coolstartup.com available?"
2. "Check if grabify.io, grabify.co, grabify.dev, and grabify.app are available"
3. "Who owns example.com and when does it expire?"
4. "How many domain lookups do I have left this month?"
