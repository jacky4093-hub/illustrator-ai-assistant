"""Illustrator connection layer.

Default (``get_bridge``): script generation only, Illustrator is never
contacted. Opt-in (``get_execution_bridge("windows-com")``): run the
generated script through the Windows COM launcher. No backend claims that
an .ai file is verified. See docs/illustrator-mcp-evaluation.md.
"""

from .bridge import (BridgeStatus, IllustratorBridge, ScriptExportBridge,  # noqa: F401
                     WindowsComBridge, get_bridge, get_execution_bridge)
from .jsx import build_jsx  # noqa: F401
