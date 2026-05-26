"""LangChain Tool wrappers for VCD Analyzer Skills.

Each Skill is exposed as a `BaseTool` subclass with a Pydantic-typed
`args_schema`, so LangChain Agents can call them with structured arguments
and get back the standardized VCD envelope.

The LangChain dependency is imported lazily inside `build_tools()`. If you
don't need the Agent integration, you can still import this module to
introspect signatures, schemas, etc., without `pip install langchain`.

Typical usage:

    from langchain_openai import ChatOpenAI
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate
    from vcd_integrations.langchain_tools import build_tools

    tools = build_tools()
    llm = ChatOpenAI(model="gpt-4o-mini")
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a VCD debug assistant. Use the tools to analyze waveforms."),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    executor.invoke({"input": "Analyze sim.vcd for AXI4 issues"})
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Pydantic is required by LangChain at runtime, but we still guard the
# import so this module can be loaded for schema introspection / docs /
# tooling without pydantic installed. If the import fails, the schema
# classes below remain undefined and `build_tools()` raises a clear
# ImportError pointing the user at the install command.
_PYDANTIC_AVAILABLE = True
_PYDANTIC_IMPORT_ERROR = None
try:
    from langchain_core.pydantic_v1 import BaseModel, Field  # pydantic v1 shim
except ImportError:
    try:
        from pydantic.v1 import BaseModel, Field  # pydantic v2 with v1 shim
    except ImportError:
        try:
            from pydantic import BaseModel, Field  # plain pydantic (any version)
        except ImportError as e:
            _PYDANTIC_AVAILABLE = False
            _PYDANTIC_IMPORT_ERROR = e
            BaseModel = None  # type: ignore[assignment]
            Field = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
VCD_ANALYZER = REPO_ROOT / 'vcd_analyzer.py'


# ----- Pydantic input schemas mirror the manifest input_schema for each Skill -----
# Defined only when pydantic is importable; otherwise these names remain absent
# and the build_tools() factory raises a clear ImportError.

if _PYDANTIC_AVAILABLE:

    class ProtocolDecodeInput(BaseModel):
        file: str = Field(description="Path to VCD file")
        protocol: str = Field(description="Protocol type: axi4, apb, uart, or spi")
        signals: Optional[str] = Field(default=None,
            description="Signal pattern (substring/glob), e.g. 'm_axi_*' or 's_apb_*'")
        begin: Optional[str] = Field(default=None, description="Start time, e.g. 100ns")
        end: Optional[str] = Field(default=None, description="End time")


    class FSMTraceInput(BaseModel):
        file: str = Field(description="Path to VCD file")
        state: str = Field(description="Signal pattern resolving to exactly one state signal")
        stuck_threshold: Optional[str] = Field(default=None,
            description="Duration above which a state is reported stuck (default 100us)")
        begin: Optional[str] = Field(default=None)
        end: Optional[str] = Field(default=None)


    class CausalityInput(BaseModel):
        file: str = Field(description="Path to VCD file")
        effect: str = Field(description="Effect signal pattern (exactly one match)")
        at: str = Field(description="Time when the effect was observed, e.g. '17.5us'")
        window: Optional[str] = Field(default=None,
            description="Search window before --at (default 100ns)")


    class AnomalyDetectInput(BaseModel):
        file: str = Field(description="Path to VCD file")
        filter: Optional[str] = Field(default=None,
            description="Restrict analysis to matching signals")
        begin: Optional[str] = Field(default=None)
        end: Optional[str] = Field(default=None)
        stuck_threshold: Optional[str] = Field(default=None,
            description="Default: 50%% of analysis window or 100us")
        glitch_threshold: Optional[str] = Field(default=None,
            description="Default: 5ns")


# ----- Shared runner: build CLI, run, parse envelope -----

_FLAG_MAP = {
    'protocol': '--protocol',
    'signals': '--signals',
    'begin': '--begin',
    'end': '--end',
    'state': '--state',
    'stuck_threshold': '--stuck-threshold',
    'glitch_threshold': '--glitch-threshold',
    'effect': '--effect',
    'at': '--at',
    'window': '--window',
    'filter': '--filter',
}


def _run_skill(command, args_dict):
    """Run a vcd_analyzer subcommand and return the envelope JSON string.

    Returns a string (not a dict) because LangChain Tools must return string
    or message content; the model receives the JSON and can parse it itself.
    """
    import subprocess

    cli = [sys.executable, str(VCD_ANALYZER), command]
    if 'file' in args_dict and args_dict['file']:
        cli.append(args_dict['file'])
    for key, flag in _FLAG_MAP.items():
        val = args_dict.get(key)
        if val is not None:
            cli.append(flag)
            cli.append(str(val))
    cli.append('--json')

    result = subprocess.run(cli, capture_output=True, text=True)
    if result.stdout:
        return result.stdout
    # Synthesize a valid error envelope when the CLI emits nothing
    return json.dumps({
        'status': 'error',
        'skill': command.replace('-', '_'),
        'error': {
            'code': 'INTERNAL_ERROR',
            'message': result.stderr.strip() or 'no output from vcd_analyzer',
            'details': {'exit_code': result.returncode},
        },
        'suggestions': [],
    })


# ----- BaseTool factory (lazy import so plain `import vcd_integrations.langchain_tools` works) -----

def build_tools():
    """Return a list of LangChain BaseTool instances, one per Skill.

    Lazy-imports langchain_core so module import doesn't require LangChain
    to be installed.
    """
    if not _PYDANTIC_AVAILABLE:
        raise ImportError(
            "pydantic is required to build LangChain tools. "
            "Install with: pip install pydantic langchain-core "
            "(original import error: {})".format(_PYDANTIC_IMPORT_ERROR)
        )
    try:
        from langchain_core.tools import BaseTool
    except ImportError as e:
        raise ImportError(
            "LangChain is required to build tools. Install with: "
            "pip install langchain-core (or langchain)"
        ) from e

    class ProtocolDecodeTool(BaseTool):
        name: str = "vcd_protocol_decode"
        description: str = (
            "Decode AXI4 / APB / UART / SPI transactions from a VCD waveform. "
            "Returns transactions, protocol violations, and statistics in the "
            "VCD Analyzer envelope (status, result, suggestions, ...)."
        )
        args_schema: type = ProtocolDecodeInput

        def _run(self, file, protocol, signals=None, begin=None, end=None):
            return _run_skill('protocol-decode', {
                'file': file, 'protocol': protocol, 'signals': signals,
                'begin': begin, 'end': end,
            })

    class FSMTraceTool(BaseTool):
        name: str = "vcd_fsm_trace"
        description: str = (
            "Extract state machine transitions from a state signal. Detects "
            "stuck states and reports per-state duration statistics."
        )
        args_schema: type = FSMTraceInput

        def _run(self, file, state, stuck_threshold=None, begin=None, end=None):
            return _run_skill('fsm-trace', {
                'file': file, 'state': state,
                'stuck_threshold': stuck_threshold,
                'begin': begin, 'end': end,
            })

    class CausalityTool(BaseTool):
        name: str = "vcd_causality"
        description: str = (
            "Find potential root causes for a signal change at a specific time. "
            "Ranks candidates by temporal proximity and historical correlation."
        )
        args_schema: type = CausalityInput

        def _run(self, file, effect, at, window=None):
            return _run_skill('causality', {
                'file': file, 'effect': effect, 'at': at, 'window': window,
            })

    class AnomalyDetectTool(BaseTool):
        name: str = "vcd_anomaly_detect"
        description: str = (
            "Scan the waveform for common anomalies: stuck signals, glitches, "
            "metastability (x/z values), and bus contention."
        )
        args_schema: type = AnomalyDetectInput

        def _run(self, file, filter=None, begin=None, end=None,
                  stuck_threshold=None, glitch_threshold=None):
            return _run_skill('anomaly-detect', {
                'file': file, 'filter': filter,
                'begin': begin, 'end': end,
                'stuck_threshold': stuck_threshold,
                'glitch_threshold': glitch_threshold,
            })

    return [
        ProtocolDecodeTool(),
        FSMTraceTool(),
        CausalityTool(),
        AnomalyDetectTool(),
    ]
