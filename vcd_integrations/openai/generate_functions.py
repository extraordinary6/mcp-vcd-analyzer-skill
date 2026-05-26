#!/usr/bin/env python3
"""Generate OpenAI Function Calling definitions from vcd_skill_manifest.json.

The manifest is the single source of truth for what Skills exist and what
their inputs look like. This script translates the manifest into the JSON
schema format expected by OpenAI's `tools=` / `functions=` parameters
(also compatible with Anthropic's `tools=` API and most function-calling
LLM SDKs).

Usage:
    python vcd_integrations/openai/generate_functions.py > functions.json

Re-run whenever the manifest changes so functions.json stays in sync.
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / 'vcd_skill_manifest.json'


def manifest_to_openai_tools(manifest):
    """Translate VCD skill manifest to OpenAI tools[] schema.

    Returns a list of {type: "function", function: {name, description,
    parameters}} entries, ready to pass to the OpenAI client as tools=...
    """
    tools = []
    for cap in manifest['capabilities']:
        tools.append({
            'type': 'function',
            'function': {
                'name': 'vcd_' + cap['skill'],
                'description': cap['description'] + (
                    '\n\nReturns the standardized VCD Analyzer envelope: '
                    '{status, skill, execution_time_ms, input, result, '
                    'metadata, suggestions}. On error, the envelope has '
                    'status="error" and error={code, message, details}.'),
                'parameters': cap['input_schema'],
            },
        })
    return tools


def main():
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    tools = manifest_to_openai_tools(manifest)
    json.dump(tools, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
