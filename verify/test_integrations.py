"""Tests for Phase 3 Agent adapters.

These tests intentionally avoid requiring the third-party packages used by
each adapter at runtime (`mcp`, `openai`, `langchain`, `flask`). Instead, we:

  * verify pure-Python helpers (manifest -> tool metadata translation)
  * verify the subprocess executors that call vcd_analyzer.py
  * verify static artifacts (functions.json) stay in sync with the manifest
  * skip the integration parts that actually need the third-party SDKs

This keeps CI lightweight while still exercising every adapter's
"glue logic" — the part most likely to break in practice.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'vcd_integrations' / 'mcp'))

FIXTURES_DIR = REPO_ROOT / 'verify' / 'fixtures'
MANIFEST_PATH = REPO_ROOT / 'vcd_skill_manifest.json'
VCD_ANALYZER = REPO_ROOT / 'vcd_analyzer.py'


def _load_manifest():
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# ----- MCP helpers (no `mcp` package needed) -----

class MCPHelpersTests(unittest.TestCase):
    def test_manifest_to_tool_metadata(self):
        from _helpers import manifest_to_tool_metadata
        manifest = _load_manifest()
        tools = manifest_to_tool_metadata(manifest)
        self.assertEqual(len(tools), 4)
        names = {t['name'] for t in tools}
        self.assertEqual(names, {
            'vcd_protocol_decode', 'vcd_fsm_trace',
            'vcd_causality', 'vcd_anomaly_detect',
        })
        # Each tool entry carries the manifest's input_schema verbatim
        for t in tools:
            self.assertIn('input_schema', t)
            self.assertIn('command', t)
            self.assertIn('skill', t)

    def test_build_cli_args_protocol_decode(self):
        from _helpers import build_cli_args
        cli = build_cli_args('protocol-decode', {
            'file': 'sim.vcd', 'protocol': 'axi4', 'signals': 'm_axi_*'
        })
        # Must contain the positional file, protocol/signals flags, and --json
        self.assertIn('protocol-decode', cli)
        self.assertIn('sim.vcd', cli)
        self.assertIn('--protocol', cli)
        self.assertIn('axi4', cli)
        self.assertIn('--signals', cli)
        self.assertIn('m_axi_*', cli)
        self.assertIn('--json', cli)

    def test_build_cli_args_omits_none(self):
        from _helpers import build_cli_args
        cli = build_cli_args('fsm-trace', {
            'file': 'sim.vcd', 'state': 'fsm_state',
            'stuck_threshold': None,  # None is dropped
        })
        # --stuck-threshold should NOT appear when the value is None
        self.assertNotIn('--stuck-threshold', cli)
        self.assertIn('fsm-trace', cli)
        self.assertIn('--state', cli)
        self.assertIn('fsm_state', cli)


# ----- OpenAI functions.json sync with manifest -----

class OpenAIArtifactsTests(unittest.TestCase):
    def test_functions_json_in_sync_with_manifest(self):
        """vcd_integrations/openai/functions.json must match what
        generate_functions.py would produce from the current manifest."""
        from vcd_integrations.openai.generate_functions import manifest_to_openai_tools

        manifest = _load_manifest()
        expected = manifest_to_openai_tools(manifest)

        functions_path = REPO_ROOT / 'vcd_integrations' / 'openai' / 'functions.json'
        with open(functions_path, 'r', encoding='utf-8') as f:
            on_disk = json.load(f)

        self.assertEqual(on_disk, expected,
                         "functions.json is out of sync with manifest; "
                         "regenerate with: python vcd_integrations/openai/generate_functions.py "
                         "> vcd_integrations/openai/functions.json")

    def test_openai_tool_count_and_shape(self):
        functions_path = REPO_ROOT / 'vcd_integrations' / 'openai' / 'functions.json'
        with open(functions_path, 'r', encoding='utf-8') as f:
            tools = json.load(f)
        self.assertEqual(len(tools), 4)
        for tool in tools:
            self.assertEqual(tool['type'], 'function')
            f = tool['function']
            self.assertIn('name', f)
            self.assertTrue(f['name'].startswith('vcd_'))
            self.assertIn('description', f)
            self.assertIn('parameters', f)
            self.assertEqual(f['parameters'].get('type'), 'object')


# ----- OpenAI executor (subprocess-based, no `openai` package needed) -----

class OpenAIExecutorTests(unittest.TestCase):
    def test_execute_protocol_decode_returns_envelope(self):
        from vcd_integrations.openai.executor import execute_function_call

        envelope = execute_function_call('vcd_protocol_decode', {
            'file': str(FIXTURES_DIR / 'axi4_basic.vcd'),
            'protocol': 'axi4',
            'signals': '*m_axi*',
        })
        self.assertEqual(envelope['status'], 'success')
        self.assertEqual(envelope['skill'], 'protocol_decode')
        self.assertIn('result', envelope)
        self.assertIn('metadata', envelope)
        self.assertIn('execution_time_ms', envelope)

    def test_execute_unknown_function_returns_error_envelope(self):
        from vcd_integrations.openai.executor import execute_function_call
        envelope = execute_function_call('vcd_nonexistent', {})
        self.assertEqual(envelope['status'], 'error')
        self.assertEqual(envelope['error']['code'], 'INVALID_ARGUMENT')

    def test_execute_propagates_skill_error_envelope(self):
        """When the Skill itself emits a structured error, the executor must
        return that exact envelope unchanged."""
        from vcd_integrations.openai.executor import execute_function_call
        envelope = execute_function_call('vcd_protocol_decode', {
            'file': str(FIXTURES_DIR / 'axi4_basic.vcd'),
            'protocol': 'i2c',  # unsupported
        })
        self.assertEqual(envelope['status'], 'error')
        self.assertEqual(envelope['error']['code'], 'INVALID_PROTOCOL')
        # The error details list the supported protocols — propagated unchanged
        self.assertIn('axi4', envelope['error']['details']['supported'])


# ----- LangChain Pydantic schemas (pydantic is broadly available) -----

class LangChainSchemaTests(unittest.TestCase):
    def test_input_schemas_have_required_fields(self):
        from vcd_integrations.langchain_tools import (
            ProtocolDecodeInput, FSMTraceInput, CausalityInput, AnomalyDetectInput,
        )
        # Pydantic v1 and v2 both expose .schema(); v2 also has .model_json_schema()
        for cls, expected_required in [
            (ProtocolDecodeInput, {'file', 'protocol'}),
            (FSMTraceInput, {'file', 'state'}),
            (CausalityInput, {'file', 'effect', 'at'}),
            (AnomalyDetectInput, {'file'}),
        ]:
            schema = cls.schema() if hasattr(cls, 'schema') else cls.model_json_schema()
            required = set(schema.get('required', []))
            self.assertTrue(expected_required.issubset(required),
                             "{}: required={} missing some of {}".format(
                                 cls.__name__, required, expected_required))


# ----- REST API logic (Flask is only needed for the HTTP routes themselves) -----

class RestApiLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import flask  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("flask not installed; REST API tests skipped")

    def test_resolve_command_accepts_multiple_aliases(self):
        from vcd_integrations.rest_api.server import _resolve_command
        self.assertEqual(_resolve_command('protocol_decode'), 'protocol-decode')
        self.assertEqual(_resolve_command('protocol-decode'), 'protocol-decode')
        self.assertEqual(_resolve_command('vcd_protocol_decode'), 'protocol-decode')
        self.assertIsNone(_resolve_command('bogus_skill_name'))

    def test_run_skill_returns_envelope(self):
        from vcd_integrations.rest_api.server import _run_skill
        envelope = _run_skill('protocol-decode', {
            'file': str(FIXTURES_DIR / 'axi4_basic.vcd'),
            'protocol': 'axi4',
            'signals': '*m_axi*',
        })
        self.assertEqual(envelope['status'], 'success')
        self.assertEqual(envelope['skill'], 'protocol_decode')

    def test_flask_endpoints_dispatch_correctly(self):
        from vcd_integrations.rest_api.server import create_app
        app = create_app()
        client = app.test_client()

        # GET /api/v1/skills returns the manifest
        resp = client.get('/api/v1/skills')
        self.assertEqual(resp.status_code, 200)
        manifest = resp.get_json()
        self.assertEqual(manifest['name'], 'vcd_analyzer')

        # GET /api/v1/skills/<name>
        resp = client.get('/api/v1/skills/fsm_trace')
        self.assertEqual(resp.status_code, 200)
        cap = resp.get_json()
        self.assertEqual(cap['skill'], 'fsm_trace')

        # GET unknown skill -> 404
        resp = client.get('/api/v1/skills/no_such_skill')
        self.assertEqual(resp.status_code, 404)

        # POST shortcut endpoint
        resp = client.post('/api/v1/protocol-decode', json={
            'file': str(FIXTURES_DIR / 'axi4_basic.vcd'),
            'protocol': 'axi4',
            'signals': '*m_axi*',
        })
        self.assertEqual(resp.status_code, 200)
        envelope = resp.get_json()
        self.assertEqual(envelope['status'], 'success')


if __name__ == '__main__':
    unittest.main(verbosity=2)
