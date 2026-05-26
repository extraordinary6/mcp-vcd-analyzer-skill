# VCD Analyzer × LangChain

LangChain `BaseTool` wrappers for the four VCD Analyzer Skills.

## Files

| File | Purpose |
|------|---------|
| `langchain_tools.py` | `build_tools()` returns 4 `BaseTool` instances |
| `langchain_example.py` | End-to-end agent demo (`create_tool_calling_agent`) |

## Quick Start

```bash
pip install langchain langchain-openai
export OPENAI_API_KEY=sk-...
python vcd_integrations/langchain_example.py path/to/sim.vcd
```

The example creates a `tool-calling` agent with the four tools registered,
and asks it to analyze the VCD file end-to-end.

## Using the Tools Directly

```python
from vcd_integrations.langchain_tools import build_tools

tools = build_tools()
# tools is a list of:
#   vcd_protocol_decode, vcd_fsm_trace, vcd_causality, vcd_anomaly_detect

# Each tool can also be invoked manually:
result_json = tools[3]._run(file="sim.vcd", glitch_threshold="2ns")
```

## Pydantic Schemas

The tools use Pydantic v1 schemas (via `langchain_core.pydantic_v1` if
available, falling back to `pydantic.v1` or plain pydantic). This matches
how most LangChain releases expect tool args today.

Each schema mirrors the Skill's manifest entry:

```python
class CausalityInput(BaseModel):
    file: str
    effect: str
    at: str
    window: Optional[str] = None
```

## Tool Output Format

All tools return the standardized VCD Analyzer envelope as a **JSON string**
(not a parsed dict — LangChain Tools must return strings or messages). Agents
generally parse the envelope themselves via the model.

To parse on the Python side, wrap the call:

```python
import json
from vcd_integrations.langchain_tools import build_tools

tools = {t.name: t for t in build_tools()}
envelope = json.loads(tools['vcd_causality']._run(
    file='sim.vcd', effect='error_flag', at='17.5us'
))
if envelope['status'] == 'success':
    causes = envelope['result']['potential_causes']
```

## Compatibility

Tested with:

- `langchain-core` >= 0.1
- `langchain` >= 0.1
- `langchain-openai` >= 0.0.5

The `build_tools()` function lazily imports `langchain_core.tools` so you
can `import vcd_integrations.langchain_tools` without LangChain installed
(useful for tests that only check the Pydantic schemas).
