# Illustrator connection: MCP / automation evaluation (phase 1)

Status: **research only**. Nothing listed here was installed, cloned or executed.
Every candidate needs a human-approved source review before it is used.

## 1. How Illustrator can be automated on Windows

| Route | Mechanism | Notes |
|---|---|---|
| COM automation | `Illustrator.Application` COM object; `DoJavaScript` / `DoJavaScriptFile` runs ExtendScript | Windows-only. Needs no Illustrator plugin. Callable from PowerShell, Python (`pywin32`) or Node. **NOT VERIFIED** in this project. |
| ExtendScript file | Run a `.jsx` from *File > Scripts > Other Script…* | Manual and simplest. This is what phase 1 generates (`illustrator_build.jsx`). **NOT VERIFIED.** |
| CEP panel | HTML/JS panel inside Illustrator, bridges to ExtendScript (e.g. over WebSocket) | Requires installing an extension and enabling debug mode for unsigned panels, which changes the user environment. |
| UXP | Adobe's newer plugin platform | Community sources (Feb/May 2026) say Illustrator UXP is **not** open to third-party developers. This has not been confirmed in official Adobe documentation. |
| macOS | AppleScript `do javascript` | Useful for development on the iMac only. **Not evidence for Windows behaviour.** |

## 2. Community Illustrator MCP servers found (unreviewed)

The information below comes from search-result listing pages. We have not read any
repository source code.

| Project | Claimed platform | Mechanism (as listed) | Review status |
|---|---|---|---|
| krVatsal/illustrator-mcp | Windows + macOS | MCP server sends ExtendScript to Illustrator | NOT REVIEWED |
| VoidChecksum/adobe-mcp (PyPI `adobe-mcp`) | Windows 10/11 only | PowerShell COM + ExtendScript | NOT REVIEWED |
| gherardo200-glitch/illustrator-mcp | Windows + macOS | COM `DoJavaScript` (Win) / AppleScript (mac) | NOT REVIEWED |
| Gokul2/illustrator-mcp-… | Windows + macOS | ExtendScript + screenshot capture | NOT REVIEWED |
| jinkeda/Illustrator_MCP | Windows + macOS | CEP panel + WebSocket | NOT REVIEWED |
| ie3jp/illustrator-mcp-server | macOS only | osascript | Not usable on Windows |
| matrayu/adobe-mcp | multi-app | Requires UXP Developer Tools | Unclear for Illustrator |

No official Adobe Illustrator MCP server was found.

## 3. Review checklist (required before any install, needs human approval)

1. **Source**: read the entire server source. Look for arbitrary-script execution
   exposed without limits, file-system writes outside an output folder, network calls,
   telemetry, and shell spawning.
2. **Execution surface**: an MCP tool that runs *arbitrary* ExtendScript can read and
   write any file the user can. Prefer a server with a narrow toolset, or wrap it.
3. **Dependencies**: list them, pin versions, check maintenance and known advisories.
   Avoid `postinstall` scripts.
4. **Licence**: it must be explicit and compatible (MIT/Apache-2.0 preferred). No licence
   means no use.
5. **Version compatibility**: Illustrator version (CC 2024/2025/2026), Windows 10 vs 11,
   Python/Node versions.
6. **Network**: the server must bind to localhost only. No outbound uploads of artwork.
7. **Reproducibility**: run it in a test Windows profile with a throwaway document first.

## 4. Project design: a narrow, cross-platform bridge

`tools/vectorfidelity/illustrator/bridge.py` defines `IllustratorBridge`:

- `probe()` reports availability with no side effects.
- `build(jsx_text, jsx_path)` delivers the build script and returns a `BridgeStatus`
  (`verification`: PASS / FAIL / NOT VERIFIED).

Backends:

| Backend | Phase 1 | Plan |
|---|---|---|
| `script-export` | Implemented. Writes the `.jsx` only. | Manual run by a human on Windows. |
| `windows-com` | Design only | Python `pywin32` → `Illustrator.Application.DoJavaScriptFile(path)`. Runs only our generated script, never free-form code. |
| `mcp` | Design only | A reviewed MCP server, restricted to "run this generated file" plus "export". |
| `macos-applescript` | Design only | Dev convenience only, not Windows evidence. |

The generated script does only the following: create an RGB document of the source
size, draw a background rectangle and compound paths with RGB fills, `saveAs` `.ai`,
and `exportFile` SVG and PNG24.

Points to verify on Windows:

- the y-axis mapping (artboard `[0,H,W,0]`, `y = H − svgY`);
- `evenodd` on compound paths;
- forward-slash Windows paths in `File()`;
- performance with thousands of points.

## Sources

- [krVatsal/illustrator-mcp](https://github.com/krVatsal/illustrator-mcp)
- [adobe-mcp on PyPI](https://pypi.org/project/adobe-mcp/)
- [Illustrator MCP (jinkeda) on Glama](https://glama.ai/mcp/servers/jinkeda/Illustrator_MCP)
- [Gokul2 Illustrator MCP on Glama](https://glama.ai/mcp/servers/Gokul2/illustrator-mcp-Gokul-Dhatkshina-Moorthy-)
- [Adobe community: is UXP available for Illustrator in 2026](https://community.adobe.com/questions-652/clarification-needed-is-uxp-publicly-available-for-illustrator-in-2026-1548811)
- [Mapsoft: UXP for Illustrator status](https://mapsoft.com/posts/illustrator-uxp-status.html)
