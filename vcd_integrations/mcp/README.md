# VCD Analyzer MCP Server

Expose the four VCD Analyzer Skills as **Model Context Protocol** Tools so
any MCP-compatible client (Claude Desktop, Claude Code, Continue, Cline, etc.)
can call them as first-class tools.

## What This Provides

The MCP server registers four tools, one per Skill:

| MCP Tool | Underlying CLI | Purpose |
|----------|----------------|---------|
| `vcd_protocol_decode` | `protocol-decode` | Decode AXI4 / APB / UART / SPI traffic |
| `vcd_fsm_trace` | `fsm-trace` | Extract state transitions and detect stuck states |
| `vcd_causality` | `causality` | Find root causes for a signal change |
| `vcd_anomaly_detect` | `anomaly-detect` | Detect glitches, metastability, stuck signals, bus contention |

Each tool returns the standardized VCD Analyzer envelope (`status`, `skill`,
`execution_time_ms`, `input`, `result`, `metadata`, `suggestions`).

## Install

```bash
pip install mcp
```

Then make sure `vcd_analyzer.py` is reachable — the server uses the path
relative to its own file, so you can keep this repository structure intact.

## Configure Claude Desktop

Edit Claude Desktop's config file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add (substituting the absolute path):

```json
{
  "mcpServers": {
    "vcd-analyzer": {
      "command": "python",
      "args": ["/abs/path/to/vcd_integrations/mcp/server.py"]
    }
  }
}
```

A template lives at `claude_desktop_config.example.json` in this directory.

Restart Claude Desktop and the tools become available automatically — they
will appear in the tool picker as `vcd_protocol_decode`, `vcd_fsm_trace`,
`vcd_causality`, `vcd_anomaly_detect`.

## How It Works

1. On startup, the server reads `vcd_skill_manifest.json` (the single source
   of truth) and registers one MCP Tool per capability.
2. When the client calls a tool, the server translates the JSON arguments
   into CLI flags and runs `python vcd_analyzer.py <command> --json`.
3. The CLI output (a standardized envelope) is returned to the client
   verbatim.

This design keeps the implementation contract in **one place** — adding a new
Skill to the manifest automatically exposes it as an MCP Tool.

## Try Without Claude Desktop

You can invoke the server with the official MCP CLI inspector
(`pip install mcp[cli]`):

```bash
mcp dev vcd_integrations/mcp/server.py
```

This opens an interactive UI where you can list tools and call them with
JSON arguments.

## Smoke Test (Pure CLI Path)

If you only want to verify the CLI-mapping logic works without an MCP
runtime, use the manifest-derived helper from the test suite:

```bash
python verify/test_mcp_integration.py
```

This builds the same CLI arg vector the server would build and runs it,
checking that the envelope shape is correct.
