"""
Unit tests for causality analysis functionality
"""

import json
import subprocess
import sys
from pathlib import Path


def run_causality(vcd_file, effect_signal, effect_time, window=None):
    """Helper to run causality command and return parsed JSON result"""
    fixtures_dir = Path(__file__).parent / 'fixtures'
    vcd_path = fixtures_dir / vcd_file

    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / 'vcd_analyzer.py'),
        'causality',
        str(vcd_path),
        '--effect', effect_signal,
        '--at', effect_time,
        '--json'
    ]

    if window:
        cmd.extend(['--window', window])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}")

    return json.loads(result.stdout)


def test_basic_causality():
    """Test basic causality analysis"""
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '50ns')

    assert result['status'] == 'success'
    assert result['skill'] == 'causality'

    # Check input echo
    assert result['input']['effect_signal'] == 'error_flag'
    assert result['input']['effect_time'] == '405ns'

    # Check effect info
    effect = result['result']['effect']
    assert effect['signal'] == 'causality_test.error_flag'
    assert effect['time'] == '405ns'

    print("[PASS] Basic causality test passed")


def test_top_cause_identification():
    """Test that the most correlated cause is correctly identified"""
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '50ns')

    causes = result['result']['potential_causes']
    assert len(causes) > 0

    # fifo_full should be the top cause (historical correlation is very high)
    top_cause = causes[0]
    assert 'fifo_full' in top_cause['signal']
    assert top_cause['correlation'] >= 0.7
    assert top_cause['confidence'] == 'high'

    print("[PASS] Top cause identification test passed")


def test_clock_filtering():
    """Test that clock signals are filtered out"""
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '50ns')

    # No clock signal should appear in the causes
    causes = result['result']['potential_causes']
    for cause in causes:
        sig_name = cause['signal'].split('.')[-1].lower()
        assert sig_name not in ('clk', 'clock', 'ck'), \
            f"Clock signal {sig_name} should be filtered out"

    print("[PASS] Clock filtering test passed")


def test_causal_chain():
    """Test causal chain construction"""
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '50ns')

    chain = result['result']['causal_chain']
    assert len(chain) > 0

    # Last entry should be the effect
    last_entry = chain[-1]
    assert last_entry['signal'] == 'causality_test.error_flag'

    # Chain should be sorted chronologically
    for i in range(len(chain) - 1):
        assert chain[i]['time_ticks'] <= chain[i+1]['time_ticks']

    print("[PASS] Causal chain test passed")


def test_narrow_window_no_causes():
    """Test that narrow window returns no causes"""
    # 1ns window - too narrow to find any cause
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '1ns')

    assert result['status'] == 'success'
    causes = result['result']['potential_causes']

    # Either no causes or only very weak ones
    if causes:
        # All should have low correlation
        for cause in causes:
            assert cause['correlation'] < 0.5

    # Should have a suggestion to expand window
    suggestions = ' '.join(result['suggestions']).lower()
    if not causes:
        assert 'window' in suggestions or 'spontaneous' in suggestions

    print("[PASS] Narrow window test passed")


def test_temporal_score():
    """Test that closer signals get higher temporal scores"""
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '50ns')

    causes = result['result']['potential_causes']
    assert len(causes) >= 2

    # fifo_full at 395ns (10ns before) should have higher temporal score
    # than counter at 355ns (50ns before)
    fifo_full = next((c for c in causes if 'fifo_full' in c['signal']), None)
    counter = next((c for c in causes if 'counter[3:0]' in c['signal']), None)

    if fifo_full and counter:
        assert fifo_full['temporal_score'] > counter['temporal_score']

    print("[PASS] Temporal score test passed")


def test_json_output_format():
    """Test JSON output format compliance"""
    result = run_causality('causality_basic.vcd', 'error_flag', '405ns', '50ns')

    # Top-level structure
    assert 'status' in result
    assert 'skill' in result
    assert 'input' in result
    assert 'result' in result
    assert 'suggestions' in result

    # Result structure
    assert 'effect' in result['result']
    assert 'potential_causes' in result['result']
    assert 'causal_chain' in result['result']

    # Verify cause structure
    for cause in result['result']['potential_causes']:
        required_fields = {
            'signal', 'change_time', 'change_time_ticks',
            'delta', 'delta_ticks', 'value', 'correlation',
            'temporal_score', 'historical_score', 'confidence', 'pattern'
        }
        assert required_fields.issubset(cause.keys())

    print("[PASS] JSON output format test passed")


if __name__ == '__main__':
    print("Running causality analysis tests...\n")

    test_basic_causality()
    test_top_cause_identification()
    test_clock_filtering()
    test_causal_chain()
    test_narrow_window_no_causes()
    test_temporal_score()
    test_json_output_format()

    print("\n[SUCCESS] All causality tests passed!")
