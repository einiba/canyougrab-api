"""
MCP server for CanYouGrab.it — confidence-scored domain availability checking.

Exposes domain availability tools to AI agents via the Model Context Protocol.
Supports Claude, ChatGPT, Gemini, Copilot, and any MCP-compatible client.
"""

from canyougrab_mcp.server import main

__all__ = ["main"]
