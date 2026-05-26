"""OpenAI Function-Calling executor for VCD Analyzer.

Bridges the JSON arguments produced by an LLM's `function_call` into
`vcd_analyzer.py` CLI invocations and returns the parsed envelope.

Typical usage from your agent loop:

    from vcd_integrations.openai.executor import execute_function_call

    # ...inside the agent loop, after the LLM emits a tool/function call...
    name = tool_call.function.name          # e.g. "vcd_causality"
    args = json.loads(tool_call.function.arguments)
    envelope = execute_function_call(name, args)
    # envelope is the standardized VCD Analyzer JSON envelope (dict)

The executor does not import the openai package — it has no opinion about
which SDK or version you use. It only takes (name, args dict) and returns
the parsed envelope dict. This keeps the integration usable from any
LLM SDK that supports function calling.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VCD_ANALYZER = REPO_ROOT / 'vcd_analyzer.py'
MANIFEST_PATH = REPO_ROOT / 'vcd_skill_manifest.json'


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


def _load_manifest():
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _name_to_command(name):
    """Look up the CLI subcommand for an OpenAI function name (vcd_<skill>)."""
    manifest = _load_manifest()
    for cap in manifest['capabilities']:
        if 'vcd_' + cap['skill'] == name:
            return cap['command']
    return None


def execute_function_call(name, arguments, python_executable=None, timeout=120):
    """Execute one VCD Analyzer Skill identified by a function-calling name.

    Args:
        name: function name as emitted by the LLM (e.g. 'vcd_causality')
        arguments: dict of JSON arguments from the LLM
        python_executable: override sys.executable if you need a specific interpreter
        timeout: subprocess timeout in seconds

    Returns:
        parsed envelope dict, including error envelopes (status="error") if
        the underlying invocation failed.
    """
    command = _name_to_command(name)
    if command is None:
        return {
            'status': 'error',
            'skill': name,
            'error': {
                'code': 'INVALID_ARGUMENT',
                'message': 'Unknown function name: {}'.format(name),
                'details': {},
            },
            'suggestions': [],
        }

    cli = [python_executable or sys.executable, str(VCD_ANALYZER), command]
    if 'file' in arguments:
        cli.append(arguments['file'])
    for key, flag in _FLAG_MAP.items():
        if arguments.get(key) is not None:
            cli.append(flag)
            cli.append(str(arguments[key]))
    cli.append('--json')

    try:
        result = subprocess.run(cli, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            'status': 'error',
            'skill': name.removeprefix('vcd_'),
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': 'vcd_analyzer.py timed out after {}s'.format(timeout),
                'details': {'cli': cli},
            },
            'suggestions': [],
        }

    if result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return {
                'status': 'error',
                'skill': name.removeprefix('vcd_'),
                'error': {
                    'code': 'INTERNAL_ERROR',
                    'message': 'failed to parse vcd_analyzer output: {}'.format(e),
                    'details': {'stdout_head': result.stdout[:500]},
                },
                'suggestions': [],
            }

    return {
        'status': 'error',
        'skill': name.removeprefix('vcd_'),
        'error': {
            'code': 'INTERNAL_ERROR',
            'message': (result.stderr.strip() or 'vcd_analyzer produced no output'),
            'details': {'exit_code': result.returncode},
        },
        'suggestions': [],
    }
