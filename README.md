<p align="center">
  <h1 align="center">mcp-vcd-analyzer-skill</h1>
  <p align="center">
    A waveform analysis toolkit exposed as <b>AI Agent Skills</b> &mdash;
    decode protocols, trace state machines, locate root causes, and surface anomalies
    from Verilog VCD dumps without ever opening a waveform viewer.
  </p>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-2.0.0-3366cc?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.9+-3366cc?style=flat-square&logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-3366cc?style=flat-square">
  <img alt="Tests" src="https://img.shields.io/badge/tests-110/110%20passed-22aa55?style=flat-square">
  <img alt="Skills" src="https://img.shields.io/badge/skills-11-7a3eb6?style=flat-square">
  <img alt="Adapters" src="https://img.shields.io/badge/adapters-MCP%20%C2%B7%20OpenAI%20%C2%B7%20LangChain%20%C2%B7%20REST-7a3eb6?style=flat-square">
</p>

---

## What is this?

VCD Analyzer turns a Verilog `.vcd` dump into something an **AI Agent** can
*reason about*. Every command speaks a standardized JSON envelope, and the
project ships four adapters so the same Skill works from Claude Desktop
(MCP), GPT-4 (Function Calling), LangChain, or any HTTP client.

```text
   Agent (Claude / GPT / Cursor / LangGraph / your script)
        │
        ▼  one of: MCP · OpenAI Function · LangChain · REST · raw CLI
   VCD Analyzer Skill Interface  ──  vcd_skill_manifest.json
        │
        ▼
   vcd_analyzer.py        single file, stdlib only, zero install
        │
        ▼
   VCD waveform on disk
```

The core engine is still a **single 4.6k-line Python file with no external
dependencies** &mdash; you can drop it on any machine that has Python 3.9+,
and the Skill envelope works whether the caller is an LLM or a human at a
terminal.

## The 11 Skills

| Skill | What it answers | CLI command |
|:------|:----------------|:------------|
| `info` | What's in this file? (timescale, scopes, signal count, time span) | `info` |
| `list` | Which signals match this pattern? | `list` |
| `dump` | What changed between T1 and T2? | `dump` |
| `summary` | Per-signal stats over a window: active/static, edges, change counts | `summary` |
| `snapshot` | What were all signal values at exactly time T? | `snapshot` |
| `compare` | What changed between T1 and T2? (diff of two snapshots) | `compare` |
| `search` | When did `valid=1 && ready=1` hold? When did `state` actually change? | `search` |
| `protocol_decode` | Decode AXI4 / APB / UART / SPI transactions + protocol violations | `protocol-decode` |
| `fsm_trace` | Extract state-machine transitions, detect stuck states | `fsm-trace` |
| `causality` | What other signals likely caused this one to change? | `causality` |
| `anomaly_detect` | Find stuck signals, glitches, metastability, bus contention | `anomaly-detect` |

The first seven are **basic queries** &mdash; the equivalent of `grep` or
`jq` on a waveform. The last four are **higher-level analyses** that
understand bus protocols, state machines, and timing relationships.

## Quick start

### As an AI Agent Skill

**Discover the catalogue**

```bash
python vcd_analyzer.py --skill-manifest        # full manifest as JSON
python vcd_analyzer.py --skill-info causality  # one capability entry
```

**Call a Skill (returns a standardized envelope)**

```bash
python vcd_analyzer.py causality sim.vcd \
       --effect error_flag --at 17.5us --window 100ns --json
```

```json
{
  "status": "success",
  "skill": "causality",
  "execution_time_ms": 14,
  "input": { "file": "sim.vcd", "effect": "error_flag", "at": "17.5us" },
  "result": {
    "effect": { "signal": "error_flag", "value": "1" },
    "potential_causes": [
      { "signal": "fifo_full", "delta_ns": 30, "correlation": 0.95,
        "confidence": "high" }
    ],
    "causal_chain": [ ... ]
  },
  "metadata": { "vcd_file_size": 12500000, "signals_matched": 8,
                "analyzer_version": "2.0.0",
                "time_range_analyzed": ["17.4us", "17.5us"] },
  "suggestions": [
    "High correlation with fifo_full (95%), likely root cause",
    "Use fsm-trace to confirm the controller handled fifo_full correctly"
  ]
}
```

### As a human at a terminal

```bash
# What's in this file?
python vcd_analyzer.py info sim.vcd

# When was valid=1 AND ready=1 at the same time?
python vcd_analyzer.py search sim.vcd --condition "valid=1,ready=1" --show data

# Decode 100 us of AXI4 traffic and flag protocol violations
python vcd_analyzer.py protocol-decode sim.vcd \
       --protocol axi4 --signals "m_axi_*" --begin 17us --end 18us

# Find anomalies (stuck signals, glitches, x/z values) in a window
python vcd_analyzer.py anomaly-detect sim.vcd --filter axi --begin 0 --end 1ms
```

All commands accept `--begin` / `--end` with unit suffixes (`fs`, `ps`,
`ns`, `us`, `ms`, `s`), and `--json` for the structured envelope.

## AI Agent Integration

Four adapters ship out of the box. Pick the one that matches your stack.

| Adapter | For | Install |
|---------|-----|---------|
| **MCP Server** | Claude Desktop · Claude Code · Continue · Cline | `pip install mcp` |
| **OpenAI Function Calling** | GPT-4o · Claude (Anthropic API) · Gemini · any function-calling LLM | (schemas only need stdlib) |
| **LangChain Tools** | LangChain agents · LangGraph | `pip install langchain pydantic` |
| **REST API** | Cross-language clients, cloud agents | `pip install flask` |

All four expose the same 11 Skills with the same envelope contract.

**MCP example** &mdash; add to `claude_desktop_config.json`:

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

**OpenAI Function Calling example** &mdash; the manifest auto-generates the
[`functions.json`](vcd_integrations/openai/functions.json) you pass to the
API; the [executor](vcd_integrations/openai/executor.py) runs the chosen
function and returns the envelope verbatim.

**LangChain example** &mdash; tool classes are derived from the manifest at
import time, so adding a new Skill needs zero LangChain-side code:

```python
from vcd_integrations.langchain_tools import build_tools
tools = build_tools()   # one BaseTool per Skill, 11 total
```

**REST example**:

```bash
python vcd_integrations/rest_api/server.py --port 5000
curl -X POST http://localhost:5000/api/v1/causality \
     -H 'Content-Type: application/json' \
     -d '{"file":"sim.vcd","effect":"error_flag","at":"17.5us"}'
```

Full setup details: [docs/agent_adapters.md](docs/agent_adapters.md).

## The Skill Envelope

Every Skill returns the same shape so Agents (and humans) write the parser
once:

```jsonc
{
  "status": "success" | "error",
  "skill":  "<name>",
  "execution_time_ms": 14,
  "input":  { /* echo of inputs for audit */ },
  "result": { /* Skill-specific payload */ },     // success only
  "metadata": {
    "vcd_file_size": 12500000,
    "analyzer_version": "2.0.0",
    "signals_matched": 8,
    "time_range_analyzed": ["17.4us", "17.5us"]
  },
  "error":  { "code": "...", "message": "...", "details": {} },  // error only
  "suggestions": [ "next-step guidance for the Agent", "..." ]
}
```

Error codes are enumerated in the manifest: `FILE_NOT_FOUND`,
`PARSE_ERROR`, `INVALID_PROTOCOL`, `SIGNAL_NOT_FOUND`,
`INVALID_TIME_RANGE`, `INVALID_ARGUMENT`, `INSUFFICIENT_DATA`,
`RESOURCE_LIMIT`, `INTERNAL_ERROR`.

Full spec: [docs/skill_envelope.md](docs/skill_envelope.md).

## Project layout

```
vcd_analyzer.py                Single-file core engine (stdlib only)
vcd_skill_manifest.json        Single source of truth for the 11 Skills
vcd_integrations/              AI Agent adapters (each opt-in via pip)
├── mcp/                       MCP Server (stdio transport)
├── openai/                    Function-calling schemas + executor
├── langchain_tools.py         Manifest-driven BaseTool factory
└── rest_api/                  Flask HTTP/JSON layer
docs/                          Per-Skill references + integration guides
verify/                        110 tests across helpers, parser, CLI,
                               envelope shape, and all 4 adapters
verify/fixtures/               Sanitized VCD waveforms
verify/samples/                Real-world GitHub VCD samples
version_notes/                 Per-release change logs
plan.md                        Multi-phase upgrade roadmap
```

## Tests

```bash
# Full pytest suite — 110 tests, every command + every adapter
python -m pytest verify/ -v

# Subset: envelope/manifest contract
python verify/test_skill_envelope.py

# Subset: adapter glue (works without mcp / openai / langchain installed)
python verify/test_integrations.py
```

CI runs the matrix on Ubuntu / Windows / macOS &times; Python 3.9-3.12 and
verifies `vcd_integrations/openai/functions.json` stays in sync with the
manifest via diff.

## Documentation

| Doc | What's inside |
|:----|:--------------|
| [docs/skill_envelope.md](docs/skill_envelope.md) | Standardized JSON envelope + error codes |
| [docs/agent_adapters.md](docs/agent_adapters.md) | MCP / OpenAI / LangChain / REST setup |
| [docs/protocol_decode.md](docs/protocol_decode.md) | AXI4 decoder details |
| [docs/protocol_decode_apb_uart_spi.md](docs/protocol_decode_apb_uart_spi.md) | APB / UART / SPI decoders |
| [docs/fsm_trace.md](docs/fsm_trace.md) | State-machine extraction & anomalies |
| [docs/causality.md](docs/causality.md) | Temporal correlation + causal-chain ranking |
| [docs/anomaly_detect.md](docs/anomaly_detect.md) | Stuck / glitch / metastability / bus contention |
| [plan.md](plan.md) | Phase 1-4 roadmap & status |

## Version history

Full per-version notes live in [version_notes/](version_notes/).

| Version | Highlight |
|:--------|:----------|
| `2.0.0` | **All 11 commands unified as AI Agent Skills**: standard envelope, manifest, 4 adapters (MCP / OpenAI / LangChain / REST), manifest-driven adapter glue |
| `1.3.9` | Eliminate duplicated value-change parsing |
| `1.3.8` | Harden input validation & error reporting |
| `1.3.7` | Literal bus-range globs & escaped-scope reporting |
| `1.3.0` | Redesign search around conditions & observations |
| `1.0.0` | Initial public release |

## License

MIT &mdash; see [LICENSE](LICENSE). &copy; 2026 neveltyc

[中文说明](README_zh.md)
