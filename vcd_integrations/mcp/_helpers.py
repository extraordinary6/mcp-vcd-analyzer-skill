"""Internal helpers shared by the MCP server and its test suite.

This module deliberately has NO third-party imports so it can be loaded
without `mcp` installed. The MCP server (which requires `mcp`) imports
these helpers from here.
"""

import json
import os
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


def load_manifest():
    """Load vcd_skill_manifest.json from the repo root."""
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_cli_args(command, arguments, python_executable=None):
    """Translate JSON arguments into a vcd_analyzer.py CLI command vector.

    Pure function (no I/O), safe to unit test without `mcp` installed.
    """
    cli = [python_executable or sys.executable, str(VCD_ANALYZER), command]
    if arguments.get('file'):
        cli.append(arguments['file'])
    for key, flag in _FLAG_MAP.items():
        val = arguments.get(key)
        if val is not None:
            cli.append(flag)
            cli.append(str(val))
    cli.append('--json')
    return cli


def manifest_to_tool_metadata(manifest):
    """Translate manifest capabilities into the (name, description, input_schema)
    tuples needed to register MCP Tools. Pure function — no MCP dependency.
    """
    tools = []
    for cap in manifest['capabilities']:
        tools.append({
            'name': 'vcd_' + cap['skill'],
            'description': '{}\n\nReturns the standardized VCD Analyzer envelope (status, skill, execution_time_ms, input, result, metadata, suggestions).'.format(cap['description']),
            'input_schema': cap['input_schema'],
            'command': cap['command'],
            'skill': cap['skill'],
        })
    return tools
