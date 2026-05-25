# FSM Trace Skill

## Overview

The `fsm-trace` skill extracts state machine transitions from VCD waveforms and detects anomalies such as stuck states. It provides detailed statistics about state durations and transition patterns.

## Command Format

```bash
python vcd_analyzer.py fsm-trace <file> --state <signal> [options]
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | string | Yes | Path to VCD file |
| `--state` | string | Yes | State signal name or pattern (e.g., `state`, `fsm_state[2:0]`) |
| `--stuck-threshold` | time | No | Threshold for stuck state detection (default: 100us) |
| `--begin` | time | No | Start time (e.g., `10ns`, `1us`) |
| `--end` | time | No | End time (e.g., `100ns`, `10us`) |
| `--json` | flag | No | Output JSON format |

## Output Structure

### JSON Format

```json
{
  "status": "success",
  "skill": "fsm_trace",
  "input": {
    "file": "fsm_basic.vcd",
    "state_signal": "state",
    "time_range": ["0s", "end"],
    "stuck_threshold": "100us"
  },
  "result": {
    "transitions": [
      {
        "id": 0,
        "from": "0",
        "to": "1",
        "time": "50ns",
        "time_ticks": 50000,
        "duration_in_from": "50ns",
        "duration_in_from_ticks": 50000
      }
    ],
    "anomalies": [
      {
        "type": "stuck_state",
        "state": "11",
        "time": "220ns",
        "time_ticks": 220000,
        "duration": "100ns",
        "duration_ticks": 100000,
        "severity": "warning",
        "description": "State 11 held for 100ns (threshold: 50ns)"
      }
    ],
    "statistics": {
      "total_transitions": 16,
      "unique_states": 6,
      "states": [
        {
          "state": "0",
          "occurrences": 4,
          "total_time": "140ns",
          "total_time_ticks": 140000,
          "avg_time": "35ns",
          "avg_time_ticks": 35000,
          "min_time": "30ns",
          "min_time_ticks": 30000,
          "max_time": "50ns",
          "max_time_ticks": 50000
        }
      ]
    }
  },
  "suggestions": [
    "Found 1 anomaly(ies)",
    "State 11 stuck for 100ns"
  ]
}
```

### Output Fields

#### transitions (array)
Each transition contains:
- `id`: Transition sequence number
- `from`: Source state value
- `to`: Destination state value
- `time`: Transition time (formatted)
- `time_ticks`: Transition time in ticks
- `duration_in_from`: Time spent in source state (formatted)
- `duration_in_from_ticks`: Time spent in source state in ticks

#### anomalies (array)
Detected anomalies:
- `type`: Anomaly type (`stuck_state`)
- `state`: State value
- `time`: When the state started (formatted)
- `time_ticks`: When the state started in ticks
- `duration`: How long the state was held (formatted)
- `duration_ticks`: How long the state was held in ticks
- `severity`: Severity level (`warning`, `error`)
- `description`: Human-readable description

#### statistics (object)
FSM statistics:
- `total_transitions`: Total number of state transitions
- `unique_states`: Number of unique states observed
- `states`: Array of per-state statistics (sorted by total time, descending)
  - `state`: State value
  - `occurrences`: Number of times this state was entered
  - `total_time`: Total time spent in this state (formatted)
  - `total_time_ticks`: Total time in ticks
  - `avg_time`: Average duration per occurrence (formatted)
  - `avg_time_ticks`: Average duration in ticks
  - `min_time`: Minimum duration (formatted)
  - `min_time_ticks`: Minimum duration in ticks
  - `max_time`: Maximum duration (formatted)
  - `max_time_ticks`: Maximum duration in ticks

## Use Cases

### 1. Basic State Machine Analysis

```bash
python vcd_analyzer.py fsm-trace sim.vcd --state "fsm_state[2:0]" --json
```

Extracts all state transitions and provides statistics about state durations.

### 2. Detect Stuck States

```bash
python vcd_analyzer.py fsm-trace sim.vcd --state "state" --stuck-threshold 50ns --json
```

Detects states that are held longer than the specified threshold.

### 3. Analyze Specific Time Window

```bash
python vcd_analyzer.py fsm-trace sim.vcd --state "state" --begin 100ns --end 1us --json
```

Analyzes state machine behavior within a specific time range.

### 4. Debug State Machine Deadlock

When a state machine appears to be stuck:
1. Use `fsm-trace` to identify which state is stuck
2. Check the `anomalies` array for stuck state warnings
3. Use the `time` field to locate when the state became stuck
4. Follow up with `causality` analysis to find the root cause

## Agent Usage Guidelines

When a user asks about state machines:

- **"Is the FSM stuck?"** → Check `anomalies` for `stuck_state` entries
- **"Which state takes the longest?"** → Look at `statistics.states[0]` (sorted by total time)
- **"How many times did it enter state X?"** → Find state X in `statistics.states` and check `occurrences`
- **"Show me all transitions"** → Display the `transitions` array

## Implementation Details

### State Encoding Support

The FSM tracer works with any state encoding:
- Binary encoding: `000`, `001`, `010`, etc.
- One-hot encoding: `0001`, `0010`, `0100`, etc.
- Gray code: `00`, `01`, `11`, `10`, etc.
- Custom encoding: Any bit pattern

State values are treated as strings for maximum flexibility.

### Anomaly Detection

**Stuck State Detection:**
- Compares each state duration against the threshold
- Default threshold: 100us (configurable via `--stuck-threshold`)
- Severity: `warning`

Future enhancements may include:
- Dead state detection (states never exited)
- Unexpected transition detection (transitions not in expected flow)
- Oscillation detection (rapid back-and-forth between states)

### Performance

- Time complexity: O(n) where n is the number of state changes
- Memory: O(n) to store all transitions
- Suitable for large VCD files with time range filtering

## Examples

### Example 1: Normal Flow Analysis

```bash
$ python vcd_analyzer.py fsm-trace fsm_basic.vcd --state "state"

State Signal: fsm_test.state[2:0]
Time range: 0s ~ end

Transitions: 16
Unique States: 6

State Statistics:
  0: 4 times, avg 35ns, total 140ns
  10: 4 times, avg 35ns, total 140ns
  11: 1 times, avg 100ns, total 100ns
  1: 4 times, avg 20ns, total 80ns
  100: 2 times, avg 20ns, total 40ns
  111: 1 times, avg 30ns, total 30ns

Transition Details (showing first 20):
  [0] 0 -> 1 @ 50000 (held 50ns)
  [1] 1 -> 10 @ 70000 (held 20ns)
  [2] 10 -> 100 @ 120000 (held 50ns)
  ...
```

### Example 2: Stuck State Detection

```bash
$ python vcd_analyzer.py fsm-trace fsm_basic.vcd --state "state" --stuck-threshold 50ns

...

Anomalies: 1
  [WARNING] 220ns: State 11 held for 100ns (threshold: 50ns)

Suggestions:
  - Found 1 anomaly(ies)
  - State 11 stuck for 100ns
```

## Testing

Unit tests are available in `verify/test_fsm_trace.py`:

```bash
python verify/test_fsm_trace.py
```

Test coverage includes:
- Basic FSM tracing
- Stuck state detection
- Time range filtering
- State statistics calculation
- JSON output format validation

## Related Skills

- **causality**: Find root causes for unexpected state transitions
- **anomaly-detect**: Broader anomaly detection across all signals
- **protocol-decode**: Decode protocol state machines (e.g., AXI handshake)

## Version History

- **v2.0.0** (2026-05-25): Initial implementation
  - State transition extraction
  - Stuck state detection
  - Per-state statistics
  - JSON output format
