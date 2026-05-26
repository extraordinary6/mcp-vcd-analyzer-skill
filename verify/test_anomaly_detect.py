"""
Unit tests for anomaly detection functionality
"""

import json
import subprocess
import sys
from pathlib import Path


def run_anomaly_detect(vcd_file, **kwargs):
    """Helper to run anomaly-detect command and return parsed JSON result"""
    fixtures_dir = Path(__file__).parent / 'fixtures'
    vcd_path = fixtures_dir / vcd_file

    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / 'vcd_analyzer.py'),
        'anomaly-detect',
        str(vcd_path),
        '--json'
    ]

    if 'stuck_threshold' in kwargs:
        cmd.extend(['--stuck-threshold', kwargs['stuck_threshold']])
    if 'glitch_threshold' in kwargs:
        cmd.extend(['--glitch-threshold', kwargs['glitch_threshold']])
    if 'filter' in kwargs:
        cmd.extend(['--filter', kwargs['filter']])
    if 'begin' in kwargs:
        cmd.extend(['--begin', kwargs['begin']])
    if 'end' in kwargs:
        cmd.extend(['--end', kwargs['end']])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}")

    return json.loads(result.stdout)


def test_basic_anomaly_detection():
    """Test basic anomaly detection runs successfully"""
    result = run_anomaly_detect('anomaly_basic.vcd',
                                 stuck_threshold='300ns',
                                 glitch_threshold='5ns')

    assert result['status'] == 'success'
    assert result['skill'] == 'anomaly_detect'

    # Should have detected anomalies
    anomalies = result['result']['anomalies']
    assert len(anomalies) > 0

    # Summary should be present
    summary = result['result']['summary']
    assert 'total_anomalies' in summary
    assert 'by_type' in summary
    assert summary['total_anomalies'] == len(anomalies)

    print("[PASS] Basic anomaly detection test passed")


def test_glitch_detection():
    """Test that glitches are correctly detected"""
    result = run_anomaly_detect('anomaly_basic.vcd',
                                 stuck_threshold='1us',  # high to avoid stuck noise
                                 glitch_threshold='5ns')

    glitches = [a for a in result['result']['anomalies'] if a['type'] == 'glitch']

    # We created 2 glitches: 2ns and 1ns pulses
    assert len(glitches) >= 2, f"Expected >=2 glitches, got {len(glitches)}"

    # All glitches should be on glitch_signal
    for g in glitches:
        assert 'glitch_signal' in g['signal']
        # Pulse width should be less than threshold (5ns)
        assert g['duration_ticks'] < 5000  # 5ns in ps ticks
        assert g['severity'] == 'warning'

    print("[PASS] Glitch detection test passed")


def test_metastability_detection():
    """Test that x values are detected as metastability"""
    result = run_anomaly_detect('anomaly_basic.vcd', stuck_threshold='1us')

    metas = [a for a in result['result']['anomalies'] if a['type'] == 'metastability']
    assert len(metas) >= 1

    # sync_flag has an x value
    sync_meta = next((m for m in metas if 'sync_flag' in m['signal']), None)
    assert sync_meta is not None
    assert sync_meta['severity'] == 'error'
    assert 'x' in sync_meta['value'].lower()

    print("[PASS] Metastability detection test passed")


def test_bus_contention_detection():
    """Test that all-x bus values are detected as contention"""
    result = run_anomaly_detect('anomaly_basic.vcd', stuck_threshold='1us')

    contentions = [a for a in result['result']['anomalies'] if a['type'] == 'bus_contention']
    assert len(contentions) >= 1

    # data_bus has all-x value
    bus_cont = next((c for c in contentions if 'data_bus' in c['signal']), None)
    assert bus_cont is not None
    assert bus_cont['severity'] == 'error'

    print("[PASS] Bus contention detection test passed")


def test_stuck_signal_detection():
    """Test that stuck signals are detected"""
    result = run_anomaly_detect('anomaly_basic.vcd', stuck_threshold='300ns')

    stucks = [a for a in result['result']['anomalies'] if a['type'] == 'stuck_signal']
    assert len(stucks) >= 1

    # stuck_signal stays at 1 for ~631ns
    stuck = next((s for s in stucks if s['signal'] == 'anomaly_test.stuck_signal'), None)
    assert stuck is not None
    assert stuck['duration_ticks'] >= 300000  # 300ns in ps ticks

    print("[PASS] Stuck signal detection test passed")


def test_signal_filter():
    """Test that signal filtering works"""
    # Only check glitch_signal
    result = run_anomaly_detect('anomaly_basic.vcd',
                                 stuck_threshold='1us',
                                 glitch_threshold='5ns',
                                 filter='glitch_signal')

    # All anomalies should be on glitch_signal
    for a in result['result']['anomalies']:
        assert 'glitch_signal' in a['signal']

    print("[PASS] Signal filter test passed")


def test_severity_summary():
    """Test that severity summary is computed correctly"""
    result = run_anomaly_detect('anomaly_basic.vcd',
                                 stuck_threshold='300ns',
                                 glitch_threshold='5ns')

    summary = result['result']['summary']
    anomalies = result['result']['anomalies']

    # Sum of severity counts should equal total
    severity_total = summary['critical'] + summary['error'] + summary['warning']
    assert severity_total == summary['total_anomalies']

    # Count by severity manually and verify
    manual_critical = sum(1 for a in anomalies if a['severity'] == 'critical')
    manual_error = sum(1 for a in anomalies if a['severity'] == 'error')
    manual_warning = sum(1 for a in anomalies if a['severity'] == 'warning')

    assert summary['critical'] == manual_critical
    assert summary['error'] == manual_error
    assert summary['warning'] == manual_warning

    print("[PASS] Severity summary test passed")


def test_time_range_filter():
    """Test that time range filter works"""
    # Only first 150ns: should have the first glitch but not later anomalies
    result = run_anomaly_detect('anomaly_basic.vcd',
                                 begin='0ns', end='150ns',
                                 stuck_threshold='1us',
                                 glitch_threshold='5ns')

    # All anomalies should be within time range
    for a in result['result']['anomalies']:
        assert a['time_ticks'] <= 150000  # 150ns in ps ticks

    print("[PASS] Time range filter test passed")


def test_json_output_format():
    """Test JSON output format compliance"""
    result = run_anomaly_detect('anomaly_basic.vcd')

    # Top-level structure
    assert 'status' in result
    assert 'skill' in result
    assert 'input' in result
    assert 'result' in result
    assert 'suggestions' in result

    # Result structure
    assert 'anomalies' in result['result']
    assert 'summary' in result['result']

    # Summary structure
    summary = result['result']['summary']
    assert 'total_anomalies' in summary
    assert 'critical' in summary
    assert 'error' in summary
    assert 'warning' in summary
    assert 'by_type' in summary

    # Anomaly structure
    for a in result['result']['anomalies']:
        assert 'type' in a
        assert 'signal' in a
        assert 'time' in a
        assert 'time_ticks' in a
        assert 'severity' in a
        assert 'description' in a

    print("[PASS] JSON output format test passed")


def test_anomalies_sorted_by_time():
    """Test that anomalies are sorted chronologically"""
    result = run_anomaly_detect('anomaly_basic.vcd', stuck_threshold='300ns')

    anomalies = result['result']['anomalies']
    for i in range(len(anomalies) - 1):
        assert anomalies[i]['time_ticks'] <= anomalies[i + 1]['time_ticks']

    print("[PASS] Anomalies sorted by time test passed")


if __name__ == '__main__':
    print("Running anomaly detection tests...\n")

    test_basic_anomaly_detection()
    test_glitch_detection()
    test_metastability_detection()
    test_bus_contention_detection()
    test_stuck_signal_detection()
    test_signal_filter()
    test_severity_summary()
    test_time_range_filter()
    test_json_output_format()
    test_anomalies_sorted_by_time()

    print("\n[SUCCESS] All anomaly detection tests passed!")
