# Phase 3: AI Agent Adapters

Phase 3 ships **four adapters** that expose the VCD Analyzer Skills to
different AI Agent ecosystems. Every adapter delegates the heavy lifting
to `vcd_analyzer.py` and returns the standardized Skill envelope from
[Phase 2](skill_envelope.md), so the adapters stay extremely thin.

## What's in `vcd_integrations/`

```
vcd_integrations/
├── mcp/
│   ├── server.py                     MCP Server (stdio transport)
│   ├── _helpers.py                   pure-Python helpers (no mcp dep)
│   ├── claude_desktop_config.example.json
│   └── README.md
├── openai/
│   ├── functions.json                generated tool schemas
│   ├── generate_functions.py         regen from manifest
│   ├── executor.py                   bridge function_call -> CLI
│   ├── example.py                    end-to-end agent loop
│   └── README.md
├── langchain_tools.py                BaseTool wrappers
├── langchain_example.py              create_tool_calling_agent demo
├── langchain_README.md
└── rest_api/
    ├── server.py                     Flask HTTP/JSON layer
    ├── requirements.txt
    └── README.md
```

## Choosing an Adapter

| Adapter | Best For | Requires |
|---------|----------|----------|
| **MCP Server** | Claude Desktop, Claude Code, MCP-aware editors (Continue, Cline) | `pip install mcp` |
| **OpenAI Functions** | GPT-4o / Claude (via Anthropic API) / Gemini / Mistral / any function-calling LLM | None for schemas; `pip install openai` for the example |
| **LangChain Tools** | LangChain agents (Agent Executor, LangGraph, etc.) | `pip install langchain langchain-openai` |
| **REST API** | Cross-language clients, cloud agents, services that can't import Python | `pip install flask` |

All four call the same underlying Skills, so you can mix and match.

## Single Source of Truth: `vcd_skill_manifest.json`

The manifest declares the four Skills' input schemas, output shapes, and
error codes. Every adapter consumes it:

- `mcp/_helpers.py` — `manifest_to_tool_metadata()` builds MCP `Tool` objects
- `openai/generate_functions.py` — emits `functions.json` from the manifest
- `langchain_tools.py` — Pydantic schemas mirror manifest entries
- `rest_api/server.py` — `GET /api/v1/skills` serves the manifest verbatim

Adding a new Skill means:

1. Implement the Skill in `vcd_analyzer.py` (Phase 1 pattern).
2. Add a `capabilities` entry to `vcd_skill_manifest.json`.
3. Regenerate `vcd_integrations/openai/functions.json`.
4. Optionally add a Pydantic schema + `BaseTool` subclass in `langchain_tools.py`.

The MCP server and REST API pick up new Skills automatically because they
read the manifest at runtime.

## Envelope Contract

All four adapters preserve the standardized envelope:

```json
{
  "status": "success" | "error",
  "skill": "<name>",
  "execution_time_ms": 7,
  "input": { /* echo */ },
  "result": { /* payload */ },          // success only
  "metadata": { ... },                   // success only
  "error": { code, message, details },   // error only
  "suggestions": [...]
}
```

This means an Agent's downstream parsing code (or LangChain output parser,
or REST consumer) is the same regardless of which adapter sits in front.

## Testing

`verify/test_integrations.py` covers the adapters without requiring the
heavyweight third-party SDKs:

- **MCP**: tests pure-Python helpers (`manifest_to_tool_metadata`,
  `build_cli_args`). Skips the full server because that needs `mcp`.
- **OpenAI**: verifies `functions.json` stays in sync with the manifest;
  exercises the executor against real VCD fixtures via subprocess.
- **LangChain**: validates Pydantic schemas (`pydantic` is broadly available).
- **REST API**: skipped if `flask` isn't installed; otherwise exercises the
  Flask test client against the live endpoints.

The CI workflow installs Flask but deliberately skips `mcp`, `openai`, and
`langchain` to keep the matrix lightweight.

## Quick Start by Adapter

### MCP (Claude Desktop)

```bash
pip install mcp
# Add to claude_desktop_config.json (see vcd_integrations/mcp/README.md)
```

### OpenAI Function Calling

```bash
pip install openai
export OPENAI_API_KEY=sk-...
python vcd_integrations/openai/example.py sim.vcd
```

### LangChain

```bash
pip install langchain langchain-openai
export OPENAI_API_KEY=sk-...
python vcd_integrations/langchain_example.py sim.vcd
```

### REST API

```bash
pip install flask
python vcd_integrations/rest_api/server.py --port 5000
curl -X POST http://localhost:5000/api/v1/protocol-decode \
  -H 'Content-Type: application/json' \
  -d '{"file": "sim.vcd", "protocol": "axi4", "signals": "m_axi_*"}'
```

## Beyond Phase 3

The remaining work in [`plan.md`](../plan.md) is Phase 4 — documentation
polish and end-to-end use case examples. The adapter implementations are
expected to be stable; new Skills should slot in via the manifest without
touching the adapters.
