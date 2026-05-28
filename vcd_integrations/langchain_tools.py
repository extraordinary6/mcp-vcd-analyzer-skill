"""LangChain Tool wrappers for VCD Analyzer Skills.

Tools are derived dynamically from `vcd_skill_manifest.json` so adding a new
Skill only requires updating the manifest — no code changes here. Each
Skill becomes a `BaseTool` subclass with a Pydantic args_schema built from
its `input_schema`, returning the standardized VCD envelope as JSON text.

The LangChain dependency is imported lazily inside `build_tools()`. If you
don't need the Agent integration, you can still import this module to
introspect schemas without `pip install langchain`. The four legacy schema
classes (`ProtocolDecodeInput`, `FSMTraceInput`, `CausalityInput`,
`AnomalyDetectInput`) are still exported by name as aliases for
back-compat — older imports keep working.

Typical usage:

    from langchain_openai import ChatOpenAI
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate
    from vcd_integrations.langchain_tools import build_tools

    tools = build_tools()  # returns one BaseTool per Skill in the manifest
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
    from langchain_core.pydantic_v1 import BaseModel, Field, create_model  # pydantic v1 shim
except ImportError:
    try:
        from pydantic.v1 import BaseModel, Field, create_model  # pydantic v2 with v1 shim
    except ImportError:
        try:
            from pydantic import BaseModel, Field, create_model  # plain pydantic (any version)
        except ImportError as e:
            _PYDANTIC_AVAILABLE = False
            _PYDANTIC_IMPORT_ERROR = e
            BaseModel = None  # type: ignore[assignment]
            Field = None  # type: ignore[assignment]
            create_model = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
VCD_ANALYZER = REPO_ROOT / 'vcd_analyzer.py'
MANIFEST_PATH = REPO_ROOT / 'vcd_skill_manifest.json'


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
    'condition': '--condition',
    'show': '--show',
    'changed': '--changed',
}


def _load_manifest():
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


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


# ----- Pydantic schema builder from manifest input_schema -----

_TYPE_MAP = {
    'string': str,
    'integer': int,
    'number': float,
    'boolean': bool,
}


def _schema_class_name(skill):
    """Convert 'protocol_decode' -> 'ProtocolDecodeInput'."""
    return ''.join(part.capitalize() for part in skill.split('_')) + 'Input'


def _build_pydantic_model(skill, input_schema):
    """Build a Pydantic BaseModel subclass from a JSON-Schema-like input_schema.

    Required fields keep their plain types; optional fields are wrapped in
    Optional[...] with default=None so LangChain won't reject missing args.
    """
    if not _PYDANTIC_AVAILABLE:
        return None
    props = input_schema.get('properties', {})
    required = set(input_schema.get('required', []))
    fields = {}
    for name, spec in props.items():
        py_type = _TYPE_MAP.get(spec.get('type', 'string'), str)
        description = spec.get('description', '')
        if name in required:
            fields[name] = (py_type, Field(description=description))
        else:
            fields[name] = (Optional[py_type],
                            Field(default=None, description=description))
    return create_model(_schema_class_name(skill), **fields)


# ----- Pre-built schemas for the four legacy Skills (back-compat exports) -----
# These are aliases that older code may import by name. The dynamic builder
# below produces the same classes for every Skill in the manifest.

if _PYDANTIC_AVAILABLE:
    _manifest = _load_manifest()
    _legacy_skills = {
        'protocol_decode': 'ProtocolDecodeInput',
        'fsm_trace': 'FSMTraceInput',
        'causality': 'CausalityInput',
        'anomaly_detect': 'AnomalyDetectInput',
    }
    for _cap in _manifest['capabilities']:
        if _cap['skill'] in _legacy_skills:
            _model = _build_pydantic_model(_cap['skill'], _cap['input_schema'])
            # Rename to the legacy class name for back-compat
            _model.__name__ = _legacy_skills[_cap['skill']]
            globals()[_legacy_skills[_cap['skill']]] = _model
    del _manifest, _cap, _model


# ----- BaseTool factory (lazy import so plain `import vcd_integrations.langchain_tools` works) -----

def _make_tool_class(BaseTool, skill, command, description, args_model):
    """Build a BaseTool subclass for one Skill.

    The skill's argument keys come from the Pydantic model fields; we pass
    them through unchanged to `_run_skill`, which drops any with value=None.
    """
    tool_name = 'vcd_' + skill

    class _Tool(BaseTool):
        name: str = tool_name
        description: str = description
        args_schema: type = args_model

        def _run(self, **kwargs):
            return _run_skill(command, kwargs)

    _Tool.__name__ = ''.join(part.capitalize() for part in skill.split('_')) + 'Tool'
    return _Tool


def build_tools():
    """Return one LangChain BaseTool instance per Skill in the manifest.

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

    manifest = _load_manifest()
    tools = []
    for cap in manifest['capabilities']:
        args_model = _build_pydantic_model(cap['skill'], cap['input_schema'])
        description = cap['description'] + (
            '\n\nReturns the standardized VCD Analyzer envelope: '
            '{status, skill, execution_time_ms, input, result, '
            'metadata, suggestions}.')
        ToolCls = _make_tool_class(BaseTool, cap['skill'], cap['command'],
                                    description, args_model)
        tools.append(ToolCls())
    return tools
