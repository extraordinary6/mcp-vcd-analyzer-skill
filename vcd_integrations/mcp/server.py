#!/usr/bin/env python3
"""VCD Analyzer MCP Server.

Exposes the four VCD Analyzer Skills (protocol-decode, fsm-trace, causality,
anomaly-detect) as MCP Tools so MCP-compatible clients (Claude Desktop,
Claude Code, Continue, etc.) can call them as first-class tools.

The server runs the existing CLI as a subprocess and forwards the JSON
envelope unchanged, so the implementation contract stays in one place:
vcd_analyzer.py + vcd_skill_manifest.json.

Run directly:
    python vcd_integrations/mcp/server.py

Or wire into Claude Desktop via claude_desktop_config.json:
    {
      "mcpServers": {
        "vcd-analyzer": {
          "command": "python",
          "args": ["/abs/path/to/vcd_integrations/mcp/server.py"]
        }
      }
    }

Requires: pip install mcp
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# Pure-Python helpers (no mcp dependency)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import (  # noqa: E402
    load_manifest,
    build_cli_args,
    manifest_to_tool_metadata,
    VCD_ANALYZER,
    MANIFEST_PATH,
)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError as e:
    sys.stderr.write(
        "Error: 'mcp' package not installed. Install with: pip install mcp\n"
        "Original error: {}\n".format(e)
    )
    sys.exit(1)


def _manifest_to_mcp_tools(manifest):
    """Adapt the pure-Python tool metadata into MCP Tool objects."""
    tools = []
    for meta in manifest_to_tool_metadata(manifest):
        tools.append(Tool(
            name=meta['name'],
            description=meta['description'],
            inputSchema=meta['input_schema'],
        ))
    return tools


async def _invoke_skill(skill_name, command, arguments):
    """Run vcd_analyzer.py and return the raw JSON envelope string."""
    cli = build_cli_args(command, arguments)

    proc = await asyncio.create_subprocess_exec(
        *cli,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if stdout:
        return stdout.decode('utf-8', errors='replace')

    return json.dumps({
        'status': 'error',
        'skill': skill_name,
        'error': {
            'code': 'INTERNAL_ERROR',
            'message': (stderr.decode('utf-8', errors='replace').strip()
                        or 'vcd_analyzer produced no output'),
            'details': {'exit_code': proc.returncode},
        },
        'suggestions': [],
    })


def main():
    manifest = load_manifest()
    server = Server('vcd-analyzer')

    tools = _manifest_to_mcp_tools(manifest)
    metadata = manifest_to_tool_metadata(manifest)
    name_to_command = {m['name']: m['command'] for m in metadata}
    name_to_skill = {m['name']: m['skill'] for m in metadata}

    @server.list_tools()
    async def list_tools():
        return tools

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in name_to_command:
            return [TextContent(type='text', text=json.dumps({
                'status': 'error',
                'skill': name,
                'error': {
                    'code': 'INVALID_ARGUMENT',
                    'message': 'Unknown tool: {}'.format(name),
                    'details': {'available': list(name_to_command.keys())},
                },
                'suggestions': [],
            }))]

        command = name_to_command[name]
        skill_name = name_to_skill[name]
        envelope_text = await _invoke_skill(skill_name, command, arguments or {})
        return [TextContent(type='text', text=envelope_text)]

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream,
                              server.create_initialization_options())

    asyncio.run(_run())


if __name__ == '__main__':
    main()
