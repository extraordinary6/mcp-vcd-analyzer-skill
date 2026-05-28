"""
Phase 2 tests: standardized Skill envelope, error handling, and Skill manifest.

These tests focus on the *shape* of responses across all Skills rather than
their domain-specific content (which is covered by per-skill test modules).
"""

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
VCD_ANALYZER = REPO_ROOT / 'vcd_analyzer.py'


def _run(cmd, expect_failure=False):
    """Run a CLI invocation and return its parsed JSON output (or stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if not expect_failure and result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed unexpectedly: {' '.join(cmd)}")
    if result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout
    return None


def _success_envelope_shape(envelope):
    """Assert the envelope has every standardized success-mode field."""
    assert envelope['status'] == 'success', envelope
    assert 'skill' in envelope and isinstance(envelope['skill'], str)
    assert 'execution_time_ms' in envelope
    assert isinstance(envelope['execution_time_ms'], int)
    assert envelope['execution_time_ms'] >= 0
    assert 'input' in envelope
    assert 'result' in envelope
    assert 'metadata' in envelope
    assert 'suggestions' in envelope and isinstance(envelope['suggestions'], list)

    # Metadata structure
    md = envelope['metadata']
    assert 'vcd_file_size' in md
    assert 'analyzer_version' in md
    assert 'signals_matched' in md
    assert 'time_range_analyzed' in md
    assert isinstance(md['time_range_analyzed'], list)
    assert len(md['time_range_analyzed']) == 2


def _error_envelope_shape(envelope, expected_code=None):
    """Assert the envelope has every standardized error-mode field."""
    assert envelope['status'] == 'error', envelope
    assert 'skill' in envelope
    assert 'execution_time_ms' in envelope
    assert 'input' in envelope
    assert 'error' in envelope
    err = envelope['error']
    assert 'code' in err and 'message' in err and 'details' in err
    if expected_code is not None:
        assert err['code'] == expected_code, \
            f"Expected error code {expected_code}, got {err['code']}"


# ----- Standardized envelope tests for each Skill -----

def test_protocol_decode_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'protocol-decode',
        str(FIXTURES_DIR / 'axi4_basic.vcd'),
        '--protocol', 'axi4', '--signals', '*m_axi*', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'protocol_decode'
    print("[PASS] protocol-decode emits standardized success envelope")


def test_fsm_trace_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'fsm-trace',
        str(FIXTURES_DIR / 'fsm_basic.vcd'),
        '--state', 'state', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'fsm_trace'
    print("[PASS] fsm-trace emits standardized success envelope")


def test_causality_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'causality',
        str(FIXTURES_DIR / 'causality_basic.vcd'),
        '--effect', 'error_flag', '--at', '405ns', '--window', '50ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'causality'
    print("[PASS] causality emits standardized success envelope")


def test_anomaly_detect_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'anomaly-detect',
        str(FIXTURES_DIR / 'anomaly_basic.vcd'),
        '--stuck-threshold', '300ns', '--glitch-threshold', '5ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'anomaly_detect'
    print("[PASS] anomaly-detect emits standardized success envelope")


# ----- Error envelope tests -----

def test_invalid_protocol_error():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'protocol-decode',
        str(FIXTURES_DIR / 'axi4_basic.vcd'),
        '--protocol', 'i2c', '--signals', '*', '--json',
    ], expect_failure=True)
    _error_envelope_shape(envelope, expected_code='INVALID_PROTOCOL')
    # Error details should list supported protocols so Agents can recover
    assert 'supported' in envelope['error']['details']
    assert 'axi4' in envelope['error']['details']['supported']
    print("[PASS] INVALID_PROTOCOL error envelope")


def test_signal_not_found_error_fsm():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'fsm-trace',
        str(FIXTURES_DIR / 'fsm_basic.vcd'),
        '--state', 'definitely_not_a_signal', '--json',
    ], expect_failure=True)
    _error_envelope_shape(envelope, expected_code='SIGNAL_NOT_FOUND')
    print("[PASS] SIGNAL_NOT_FOUND error envelope (fsm-trace)")


def test_signal_not_found_error_causality():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'causality',
        str(FIXTURES_DIR / 'causality_basic.vcd'),
        '--effect', 'nope_no_such_signal', '--at', '405ns', '--json',
    ], expect_failure=True)
    _error_envelope_shape(envelope, expected_code='SIGNAL_NOT_FOUND')
    print("[PASS] SIGNAL_NOT_FOUND error envelope (causality)")


def test_invalid_time_range_error():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'protocol-decode',
        str(FIXTURES_DIR / 'axi4_basic.vcd'),
        '--protocol', 'axi4', '--signals', '*m_axi*',
        '--begin', 'not_a_time', '--json',
    ], expect_failure=True)
    _error_envelope_shape(envelope, expected_code='INVALID_TIME_RANGE')
    print("[PASS] INVALID_TIME_RANGE error envelope")


# ----- Skill manifest tests -----

def test_skill_manifest_full():
    """`--skill-manifest` returns the full manifest JSON"""
    manifest = _run([sys.executable, str(VCD_ANALYZER), '--skill-manifest'])
    assert manifest['name'] == 'vcd_analyzer'
    assert 'version' in manifest
    assert 'capabilities' in manifest
    assert 'error_codes' in manifest

    skill_names = {c['skill'] for c in manifest['capabilities']}
    assert skill_names == {
        'info', 'list', 'dump', 'summary', 'snapshot', 'compare', 'search',
        'protocol_decode', 'fsm_trace', 'causality', 'anomaly_detect',
    }

    # Each capability has the expected shape
    for cap in manifest['capabilities']:
        assert 'skill' in cap
        assert 'command' in cap
        assert 'description' in cap
        assert 'category' in cap
        assert 'input_schema' in cap
        assert 'result_schema' in cap
        assert 'example_cli' in cap

    print("[PASS] --skill-manifest returns complete manifest")


def test_skill_info_by_name():
    """`--skill-info <name>` returns one capability block"""
    for name in ('protocol_decode', 'fsm_trace', 'causality', 'anomaly_detect'):
        cap = _run([sys.executable, str(VCD_ANALYZER), '--skill-info', name])
        assert cap['skill'] == name
        assert 'input_schema' in cap
    print("[PASS] --skill-info works for every Skill")


def test_skill_info_unknown_name():
    """`--skill-info <unknown>` exits non-zero with a helpful message"""
    result = subprocess.run(
        [sys.executable, str(VCD_ANALYZER), '--skill-info', 'bogus_skill'],
        capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'unknown skill' in result.stderr.lower() or 'unknown skill' in result.stdout.lower()
    print("[PASS] --skill-info rejects unknown names")


def test_manifest_error_codes_match_implementation():
    """Every error code emitted by the implementation should appear in the manifest"""
    manifest = _run([sys.executable, str(VCD_ANALYZER), '--skill-manifest'])
    manifest_codes = {entry['code'] for entry in manifest['error_codes']}

    # These are the codes our implementation can produce today
    expected_codes = {
        'FILE_NOT_FOUND', 'PARSE_ERROR', 'INVALID_PROTOCOL',
        'SIGNAL_NOT_FOUND', 'INVALID_TIME_RANGE', 'INVALID_ARGUMENT',
        'INSUFFICIENT_DATA', 'RESOURCE_LIMIT', 'INTERNAL_ERROR',
    }
    missing = expected_codes - manifest_codes
    assert not missing, f"Manifest is missing error codes: {missing}"
    print("[PASS] Manifest documents all known error codes")


# ----- Basic-query envelope tests (info / list / dump / summary / snapshot / compare / search) -----

def test_info_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'info',
        str(FIXTURES_DIR / 'basic_trace.vcd'), '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'info'
    # Legacy fields still present, just nested under result
    assert 'signal_count' in envelope['result']
    print("[PASS] info emits standardized success envelope")


def test_list_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'list',
        str(FIXTURES_DIR / 'basic_trace.vcd'),
        '--filter', 'clk', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'list'
    assert 'signals' in envelope['result']
    # --filter is normalized into a pattern list at argparse time
    assert 'clk' in envelope['input']['filter']
    print("[PASS] list emits standardized success envelope")


def test_dump_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'dump',
        str(FIXTURES_DIR / 'basic_trace.vcd'),
        '--begin', '0ns', '--end', '30ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'dump'
    assert 'events' in envelope['result']
    print("[PASS] dump emits standardized success envelope")


def test_summary_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'summary',
        str(FIXTURES_DIR / 'basic_trace.vcd'),
        '--begin', '0ns', '--end', '30ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'summary'
    assert 'window' in envelope['result']
    assert 'rows' in envelope['result']
    print("[PASS] summary emits standardized success envelope")


def test_snapshot_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'snapshot',
        str(FIXTURES_DIR / 'basic_trace.vcd'),
        '--at', '20ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'snapshot'
    assert envelope['input']['at'] == '20ns'
    assert 'signals' in envelope['result']
    print("[PASS] snapshot emits standardized success envelope")


def test_compare_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'compare',
        str(FIXTURES_DIR / 'basic_trace.vcd'),
        '--at', '10ns,30ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'compare'
    assert 'diffs' in envelope['result']
    print("[PASS] compare emits standardized success envelope")


def test_search_envelope():
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'search',
        str(FIXTURES_DIR / 'search_trace.vcd'),
        '--condition', 'tb.valid=1,tb.ready=1',
        '--begin', '0ns', '--end', '100ns', '--json',
    ])
    _success_envelope_shape(envelope)
    assert envelope['skill'] == 'search'
    assert envelope['result']['mode'] in ('interval', 'segment', 'event')
    print("[PASS] search emits standardized success envelope")


def test_compare_invalid_at_returns_error_envelope():
    """compare's '--at' must be exactly two comma-separated times — bad input
    should surface a structured INVALID_TIME_RANGE error envelope."""
    envelope = _run([
        sys.executable, str(VCD_ANALYZER), 'compare',
        str(FIXTURES_DIR / 'basic_trace.vcd'),
        '--at', '10ns', '--json',
    ], expect_failure=True)
    _error_envelope_shape(envelope, expected_code='INVALID_TIME_RANGE')
    print("[PASS] compare INVALID_TIME_RANGE error envelope")


if __name__ == '__main__':
    print("Running Phase 2 (skill envelope / manifest / error) tests...\n")

    test_protocol_decode_envelope()
    test_fsm_trace_envelope()
    test_causality_envelope()
    test_anomaly_detect_envelope()

    test_invalid_protocol_error()
    test_signal_not_found_error_fsm()
    test_signal_not_found_error_causality()
    test_invalid_time_range_error()

    test_skill_manifest_full()
    test_skill_info_by_name()
    test_skill_info_unknown_name()
    test_manifest_error_codes_match_implementation()

    # Phase 3.5: basic CLI queries promoted to standard Skill envelope
    test_info_envelope()
    test_list_envelope()
    test_dump_envelope()
    test_summary_envelope()
    test_snapshot_envelope()
    test_compare_envelope()
    test_search_envelope()
    test_compare_invalid_at_returns_error_envelope()

    print("\n[SUCCESS] All Phase 2 envelope/manifest tests passed!")
