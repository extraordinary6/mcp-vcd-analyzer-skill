# VCD Analyzer × OpenAI Function Calling

Use the VCD Analyzer Skills with OpenAI's function-calling API
(`gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`, etc.).

## Files

| File | Purpose |
|------|---------|
| `functions.json` | Tool schema for OpenAI `tools=[...]` parameter |
| `generate_functions.py` | Regenerates `functions.json` from `vcd_skill_manifest.json` |
| `executor.py` | Runs a single function call against `vcd_analyzer.py` and returns the JSON envelope |
| `example.py` | End-to-end agent loop using `gpt-4o-mini` |

## Quick Start

```bash
pip install openai
export OPENAI_API_KEY=sk-...
python vcd_integrations/openai/example.py path/to/sim.vcd
```

The example will:

1. Send the user prompt + tool schemas to the model.
2. When the model emits `function_call`s, hand them to `executor.execute_function_call`.
3. Send the JSON envelope back as `role: tool`.
4. Loop until the model produces a final natural-language answer.

## Using the Executor Directly

The executor is SDK-agnostic — it accepts the tool name and JSON arguments
the LLM emitted and returns the parsed envelope dict:

```python
from vcd_integrations.openai.executor import execute_function_call

envelope = execute_function_call(
    'vcd_causality',
    {'file': 'sim.vcd', 'effect': 'error_flag', 'at': '17.5us', 'window': '100ns'}
)

if envelope['status'] == 'success':
    causes = envelope['result']['potential_causes']
else:
    code = envelope['error']['code']
```

## Keeping the Schema in Sync

`functions.json` is generated from `vcd_skill_manifest.json`. Whenever you
add or modify a Skill, regenerate the tool schema:

```bash
python vcd_integrations/openai/generate_functions.py > vcd_integrations/openai/functions.json
```

The CI workflow includes this regeneration step to detect drift.

## Compatibility

`functions.json` follows the OpenAI `tools=[{"type": "function", "function": {...}}]`
format. The same `function.parameters` JSON Schema also works for:

- Anthropic Messages API (`tools=`)
- Google Gemini function calling
- Mistral function calling
- Ollama / llama.cpp tool-use modes
- Most LangChain / LiteLLM wrappers

For non-OpenAI clients, unwrap the schemas (just take each
`function.parameters` block) or read directly from `vcd_skill_manifest.json`.

## Cost / Latency Tips

- Use `gpt-4o-mini` for routine waveform debug — it handles the four Skills
  comfortably and is ~10x cheaper than `gpt-4o`.
- Bound the agent loop (the example uses 6 turns). Real debug sessions rarely
  need more than 3-4 tool calls.
- The envelope's `metadata.execution_time_ms` lets you log per-Skill latency
  to spot hot calls — `causality` is the most expensive because it computes
  historical correlations across the whole waveform.
