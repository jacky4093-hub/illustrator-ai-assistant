"""Illustrator connection layer (phase 1: interface + script generation only).

Nothing in this package launches Illustrator, installs an MCP server or
claims that an .ai file was produced. See docs/illustrator-mcp-evaluation.md.
"""

from .bridge import BridgeStatus, IllustratorBridge, ScriptExportBridge, get_bridge  # noqa: F401
from .jsx import build_jsx  # noqa: F401
