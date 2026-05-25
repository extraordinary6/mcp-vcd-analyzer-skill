"""
Unit tests for FSM trace functionality
"""

import json
import subprocess
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import vcd_analyzer


def run_fsm_trace(vcd_file, state_signal, stuck_threshold=None, begin=None, end=None):
    """Helper to run fsm-trace and return parsed JSON result"""
    fixtures_dir = Path(__file__).parent / 'fixtures'
    vcd_path = fixtures_dir / vcd_file

    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / 'vcd_analyzer.py'),
        'fsm-trace',
        str(vcd_path),
        '--state', state_signal,
        '--json'
    ]

    if stuck_threshold:
        cmd.extend(['--stuck-threshold', stuck_threshold])
    if begin:
        cmd.extend(['--begin', begin])
    if end:
        cmd.extend(['--end', end])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}")

    return json.loads(result.stdout)


def test_basic_fsm_trace():
    """Test basic FSM tracing"""
    result = run_fsm_trace('fsm_basic.vcd', 'state')

    assert result['status'] == 'success'
    assert result['skill'] == 'fsm_trace'

    # Check input echo
    assert result['input']['state_signal'] == 'state'

    # Check transitions
    transitions = result['result']['transitions']
    assert len(transitions) > 0

    # Verify transition structure
    trans = transitions[0]
    assert 'id' in trans
    assert 'from' in trans
    assert 'to' in trans
    assert 'time' in trans
    assert 'duration_in_from' in trans

    # Check statistics
    stats = result['result']['statistics']
    assert stats['total_transitions'] == len(transitions)
    assert stats['unique_states'] > 0
    assert len(stats['states']) == stats['unique_states']

    # Verify state statistics structure
    state_stat = stats['states'][0]
    assert 'state' in state_stat
    assert 'occurrences' in state_stat
    assert 'total_time' in state_stat
    assert 'avg_time' in state_stat
    assert 'min_time' in state_stat
    assert 'max_time' in state_stat

    print("[PASS] Basic FSM trace test passed")


def test_stuck_state_detection():
    """Test stuck state anomaly detection"""
    # Set threshold to 50ns to detect the 100ns WAIT state
    result = run_fsm_trace('fsm_basic.vcd', 'state', stuck_threshold='50ns')

    assert result['status'] == 'success'

    # Check anomalies
    anomalies = result['result']['anomalies']
    assert len(anomalies) > 0

    # Verify anomaly structure
    anom = anomalies[0]
    assert anom['type'] == 'stuck_state'
    assert 'state' in anom
    assert 'time' in anom
    assert 'duration' in anom
    assert anom['severity'] == 'warning'
    assert 'description' in anom

    # Check suggestions mention the anomaly
    suggestions = result['suggestions']
    assert any('anomaly' in s.lower() or 'stuck' in s.lower() for s in suggestions)

    print("[PASS] Stuck state detection test passed")


def test_time_range_filtering():
    """Test FSM trace with time range"""
    # Trace only first 200ns
    result = run_fsm_trace('fsm_basic.vcd', 'state', begin='0ns', end='200ns')

    assert result['status'] == 'success'

    # Should have fewer transitions than full trace
    transitions = result['result']['transitions']

    # All transitions should be within time range
    for trans in transitions:
        assert trans['time_ticks'] <= 200000  # 200ns in ticks

    print("[PASS] Time range filtering test passed")


def test_state_statistics():
    """Test state statistics calculation"""
    result = run_fsm_trace('fsm_basic.vcd', 'state')

    stats = result['result']['statistics']
    states = stats['states']

    # Verify statistics are sorted by total time (descending)
    for i in range(len(states) - 1):
        assert states[i]['total_time_ticks'] >= states[i+1]['total_time_ticks']

    # Verify avg_time calculation
    for state in states:
        # avg should be between min and max
        assert state['min_time_ticks'] <= state['avg_time_ticks'] <= state['max_time_ticks']

    print("[PASS] State statistics test passed")


def test_json_output_format():
    """Test JSON output format compliance"""
    result = run_fsm_trace('fsm_basic.vcd', 'state')

    # Check top-level structure
    assert 'status' in result
    assert 'skill' in result
    assert 'input' in result
    assert 'result' in result
    assert 'suggestions' in result

    # Check result structure
    assert 'transitions' in result['result']
    assert 'anomalies' in result['result']
    assert 'statistics' in result['result']

    # Verify all transitions have consistent fields
    transitions = result['result']['transitions']
    if transitions:
        required_fields = {'id', 'from', 'to', 'time', 'time_ticks',
                          'duration_in_from', 'duration_in_from_ticks'}
        for trans in transitions:
            assert required_fields.issubset(trans.keys())

    print("[PASS] JSON output format test passed")


if __name__ == '__main__':
    print("Running FSM trace tests...\n")

    test_basic_fsm_trace()
    test_stuck_state_detection()
    test_time_range_filtering()
    test_state_statistics()
    test_json_output_format()

    print("\n[SUCCESS] All FSM trace tests passed!")
