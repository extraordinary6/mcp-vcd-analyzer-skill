#!/usr/bin/env python3
"""Flask REST API exposing the VCD Analyzer Skills.

For Agents that can't easily call CLIs or load Python modules (e.g. cloud
agents, language runtimes other than Python), a thin HTTP layer is the
easiest integration path.

Endpoints:
    GET  /api/v1/skills                    -> the full Skill manifest
    GET  /api/v1/skills/<name>             -> one capability entry
    POST /api/v1/skills/<name>             -> execute the Skill
                                              body: JSON matching input_schema
    POST /api/v1/protocol-decode           -> shortcut for vcd_protocol_decode
    POST /api/v1/fsm-trace                 -> shortcut for vcd_fsm_trace
    POST /api/v1/causality                 -> shortcut for vcd_causality
    POST /api/v1/anomaly-detect            -> shortcut for vcd_anomaly_detect

All POST endpoints return the standardized VCD envelope JSON (HTTP 200 even
for status=error, since the envelope carries success/error itself).
HTTP-level errors (400/404/500) are reserved for transport/routing issues.

Run:
    pip install flask
    python vcd_integrations/rest_api/server.py [--host 0.0.0.0 --port 5000]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from flask import Flask, jsonify, request
except ImportError:
    sys.stderr.write("Error: Flask not installed. Run: pip install flask\n")
    sys.exit(1)


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
    'condition': '--condition',
    'show': '--show',
    'changed': '--changed',
}


def _load_manifest():
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _resolve_command(skill_or_command):
    """Accept either a skill name (e.g. 'protocol_decode'), CLI command
    (e.g. 'protocol-decode'), or MCP-style name ('vcd_protocol_decode')."""
    manifest = _load_manifest()
    candidate = skill_or_command.lstrip('-')
    if candidate.startswith('vcd_'):
        candidate = candidate[4:]
    for cap in manifest['capabilities']:
        if candidate in (cap['skill'], cap['command'],
                         cap['skill'].replace('_', '-'),
                         cap['command'].replace('-', '_')):
            return cap['command']
    return None


def _run_skill(command, payload):
    """Execute the CLI, return parsed envelope dict."""
    cli = [sys.executable, str(VCD_ANALYZER), command]
    if payload.get('file'):
        cli.append(payload['file'])
    for key, flag in _FLAG_MAP.items():
        val = payload.get(key)
        if val is not None:
            cli.append(flag)
            cli.append(str(val))
    cli.append('--json')

    result = subprocess.run(cli, capture_output=True, text=True)
    if result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return {
                'status': 'error',
                'skill': command.replace('-', '_'),
                'error': {
                    'code': 'INTERNAL_ERROR',
                    'message': 'invalid JSON from vcd_analyzer: {}'.format(e),
                    'details': {'stdout_head': result.stdout[:500]},
                },
                'suggestions': [],
            }
    return {
        'status': 'error',
        'skill': command.replace('-', '_'),
        'error': {
            'code': 'INTERNAL_ERROR',
            'message': (result.stderr.strip() or 'no output from vcd_analyzer'),
            'details': {'exit_code': result.returncode},
        },
        'suggestions': [],
    }


def create_app():
    app = Flask(__name__)

    @app.route('/api/v1/skills', methods=['GET'])
    def list_skills():
        return jsonify(_load_manifest())

    @app.route('/api/v1/skills/<name>', methods=['GET'])
    def get_skill(name):
        manifest = _load_manifest()
        candidate = name
        if candidate.startswith('vcd_'):
            candidate = candidate[4:]
        for cap in manifest['capabilities']:
            if candidate in (cap['skill'], cap['command']):
                return jsonify(cap)
        return jsonify({
            'error': 'unknown skill',
            'available': [c['skill'] for c in manifest['capabilities']],
        }), 404

    @app.route('/api/v1/skills/<name>', methods=['POST'])
    def execute_skill(name):
        command = _resolve_command(name)
        if not command:
            return jsonify({'error': 'unknown skill', 'name': name}), 404
        payload = request.get_json(silent=True) or {}
        envelope = _run_skill(command, payload)
        return jsonify(envelope)

    # Convenience shortcuts (also kept stable for clients that pre-date the
    # generic /skills/<name> route). One shortcut per Skill in the manifest.
    manifest = _load_manifest()
    for cap in manifest['capabilities']:
        command = cap['command']
        def _make(command=command):
            def handler():
                payload = request.get_json(silent=True) or {}
                return jsonify(_run_skill(command, payload))
            handler.__name__ = 'shortcut_' + command.replace('-', '_')
            return handler
        app.add_url_rule('/api/v1/' + command,
                         endpoint='shortcut_' + command,
                         view_func=_make(),
                         methods=['POST'])

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({'status': 'ok', 'vcd_analyzer': str(VCD_ANALYZER)})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
