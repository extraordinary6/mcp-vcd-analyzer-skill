# Skill Envelope — Standardized JSON Schema

Phase 2 standardizes the JSON output across every VCD Analyzer Skill so any AI
Agent (Claude Code, GPT-4 function calling, LangChain, MCP) can consume
responses uniformly.

## Envelope Shape

### Success Response

Every Skill emits this envelope on success when `--json` is passed:

```json
{
  "status": "success",
  "skill": "<skill_name>",
  "execution_time_ms": 7,
  "input": { /* echo of input args */ },
  "result": { /* skill-specific payload */ },
  "metadata": {
    "vcd_file_size": 2503,
    "analyzer_version": "1.3.9",
    "signals_matched": 23,
    "time_range_analyzed": ["0s", "863ns"]
  },
  "suggestions": [
    "High correlation with fifo_full (92%), likely root cause"
  ]
}
```

### Error Response

On a recoverable error, the Skill emits:

```json
{
  "status": "error",
  "skill": "protocol_decode",
  "execution_time_ms": 0,
  "input": {
    "file": "sim.vcd",
    "protocol": "i2c",
    "signals": "*"
  },
  "error": {
    "code": "INVALID_PROTOCOL",
    "message": "Unsupported protocol: i2c",
    "details": {
      "supported": ["axi4", "apb", "uart", "spi"]
    }
  },
  "suggestions": []
}
```

The process exits with non-zero status so Agent runtimes can detect failure
without parsing JSON.

## Top-Level Fields

| Field | Type | Purpose |
|-------|------|---------|
| `status` | `"success"` \| `"error"` | First branch point for Agents |
| `skill` | string | Stable Skill identifier (e.g. `protocol_decode`) |
| `execution_time_ms` | int | Wall-clock duration of the analysis |
| `input` | object | Echo of resolved input parameters |
| `result` | object | Skill-specific result (success only) |
| `metadata` | object | Common context across Skills (success only) |
| `error` | object | Structured error (error only) |
| `suggestions` | string[] | Agent-facing next-step hints |

## Metadata Fields

Every successful response carries:

| Field | Type | Description |
|-------|------|-------------|
| `vcd_file_size` | int (bytes) | VCD file size, useful for sanity checks |
| `analyzer_version` | string | Version of `vcd_analyzer.py` that produced the response |
| `signals_matched` | int | How many signals the Skill ended up analyzing |
| `time_range_analyzed` | `[start, end]` | Effective window the Skill ran on |

## Error Codes

Stable error codes the Skill layer can emit:

| Code | Meaning |
|------|---------|
| `FILE_NOT_FOUND` | VCD file does not exist or cannot be opened |
| `PARSE_ERROR` | VCD format is invalid or corrupt |
| `INVALID_PROTOCOL` | Requested protocol is not supported |
| `SIGNAL_NOT_FOUND` | Signal pattern matched zero or multiple signals when one was required |
| `INVALID_TIME_RANGE` | Time argument failed to parse or end < begin |
| `INVALID_ARGUMENT` | A CLI argument is malformed (filter pattern, condition, etc.) |
| `INSUFFICIENT_DATA` | The VCD does not have enough events to complete the analysis |
| `RESOURCE_LIMIT` | Input exceeds configured resource limits |
| `INTERNAL_ERROR` | Unhandled exception inside the Skill (please report) |

Agents should branch on `error.code`, not on the human-readable `error.message`.

## Skill Discovery

Two CLI flags expose the manifest:

```bash
# Full manifest
python vcd_analyzer.py --skill-manifest

# A single capability block
python vcd_analyzer.py --skill-info fsm_trace
```

The manifest file lives at `vcd_skill_manifest.json` and is also fetched at
runtime by these flags. It describes:

- All Skills, their commands, input schemas, and expected result shapes
- All error codes
- Example CLI invocations

Use the manifest to:

- Generate Function Calling schemas for GPT-4 / Claude / Gemini
- Build LangChain Tool subclasses from a single source of truth
- Validate Agent-generated calls before executing them

## Backward Compatibility

The envelope is additive. Existing JSON consumers that read
`result.transactions`, `result.statistics`, `input.protocol`, etc. continue to
work — these fields are inside `result` / `input` unchanged.

New fields (`execution_time_ms`, `metadata`, top-level `status`) are layered
around the existing payload so callers that ignored them previously still
parse correctly today.

## Example: Branching on Error in an Agent

```python
import json, subprocess

result = subprocess.run(
    ["python", "vcd_analyzer.py", "protocol-decode",
     "sim.vcd", "--protocol", "axi4", "--signals", "*", "--json"],
    capture_output=True, text=True
)
envelope = json.loads(result.stdout)

if envelope["status"] == "error":
    code = envelope["error"]["code"]
    if code == "SIGNAL_NOT_FOUND":
        # Show user the available signals and ask them to refine
        ...
    elif code == "INVALID_PROTOCOL":
        # Suggest from envelope["error"]["details"]["supported"]
        ...
else:
    txns = envelope["result"]["transactions"]
    ...
```

## See Also

- [`vcd_skill_manifest.json`](../vcd_skill_manifest.json) — machine-readable manifest
- [`protocol_decode.md`](protocol_decode.md) — AXI4 docs
- [`protocol_decode_apb_uart_spi.md`](protocol_decode_apb_uart_spi.md) — APB/UART/SPI docs
- [`fsm_trace.md`](fsm_trace.md) — FSM tracer docs
- [`causality.md`](causality.md) — causality analyzer docs
- [`anomaly_detect.md`](anomaly_detect.md) — anomaly detector docs
