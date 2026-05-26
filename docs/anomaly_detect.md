# Anomaly Detection Skill

## Overview

The `anomaly-detect` skill automatically scans VCD waveforms for common issues:
stuck signals, glitches, metastability, and bus contention. It's typically the
first skill an AI Agent runs to identify what to investigate further.

## Command Format

```bash
python vcd_analyzer.py anomaly-detect <file> [options] [--json]
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | string | Yes | Path to VCD file |
| `--filter` | pattern | No | Restrict analysis to matching signals |
| `--begin` | time | No | Start of analysis window |
| `--end` | time | No | End of analysis window |
| `--stuck-threshold` | time | No | Stuck signal threshold (default: 50% of window or 100us) |
| `--glitch-threshold` | time | No | Glitch pulse width threshold (default: 5ns) |
| `--json` | flag | No | Output JSON format |

## Detected Anomaly Types

### 1. stuck_signal (warning/error/critical)
A signal does not change for an extended period.

**Severity scaling**:
- `warning`: duration >= threshold
- `error`: duration >= 3x threshold
- `critical`: duration >= 10x threshold

**When to investigate**: Stuck signals often indicate:
- Forgotten reset deassertion
- Disconnected logic
- Clock domain issues
- Bug in driving logic

### 2. glitch (warning)
A single-bit signal pulses high (or low) for less than the glitch threshold.

**When to investigate**: Glitches may indicate:
- Combinational hazards
- Pulse width violations
- Race conditions
- Need for synchronization

### 3. metastability (error)
A signal value contains `x` or `z` bits (unknown/high-impedance).

**When to investigate**: Metastability is a sign of:
- Uninitialized registers
- Clock domain crossing (CDC) issues
- Missing reset
- Logic propagation through unknown values

### 4. bus_contention (error)
All bits of a multi-bit signal are `x` or `z`, indicating multiple drivers
or no driver.

**When to investigate**: Bus contention usually means:
- Multiple drivers active simultaneously
- Tristate buffer enable logic broken
- Missing pull-up/pull-down

## Output Structure

### JSON Format

```json
{
  "status": "success",
  "skill": "anomaly_detect",
  "input": {
    "file": "anomaly_basic.vcd",
    "time_range": ["0s", "863ns"],
    "signals_analyzed": 7,
    "stuck_threshold": "300ns",
    "glitch_threshold": "5ns"
  },
  "result": {
    "anomalies": [
      {
        "type": "glitch",
        "signal": "anomaly_test.glitch_signal",
        "time": "120ns",
        "time_ticks": 120000,
        "duration": "2ns",
        "duration_ticks": 2000,
        "severity": "warning",
        "description": "Pulse width 2ns < minimum expected 5ns"
      },
      {
        "type": "metastability",
        "signal": "anomaly_test.sync_flag",
        "time": "202ns",
        "time_ticks": 202000,
        "value": "x",
        "severity": "error",
        "description": "Unknown value detected: x"
      },
      {
        "type": "bus_contention",
        "signal": "anomaly_test.data_bus[7:0]",
        "time": "272ns",
        "value": "x",
        "severity": "error",
        "description": "Bus contention detected (all bits unknown: x)"
      },
      {
        "type": "stuck_signal",
        "signal": "anomaly_test.stuck_signal",
        "time": "182ns",
        "time_range": ["182ns", "813ns"],
        "duration": "631ns",
        "severity": "warning",
        "description": "Signal stuck for 631ns"
      }
    ],
    "summary": {
      "total_anomalies": 4,
      "critical": 0,
      "error": 2,
      "warning": 2,
      "by_type": {
        "glitch": 1,
        "metastability": 1,
        "bus_contention": 1,
        "stuck_signal": 1
      }
    }
  },
  "suggestions": [
    "1 metastability issue(s); review CDC (Clock Domain Crossing) design",
    "1 bus contention(s); check for multiple drivers",
    "1 glitch(es); check pulse width requirements",
    "1 stuck signal(s); verify expected activity"
  ]
}
```

### Anomaly Fields

Common fields across all anomaly types:
- `type`: Anomaly category (`stuck_signal`, `glitch`, `metastability`, `bus_contention`)
- `signal`: Full signal path
- `time`: When the anomaly occurred (formatted)
- `time_ticks`: When the anomaly occurred (in ticks)
- `severity`: `warning`, `error`, or `critical`
- `description`: Human-readable description

Type-specific fields:
- **stuck_signal**: `time_range`, `duration`, `duration_ticks`
- **glitch**: `duration`, `duration_ticks`
- **metastability** / **bus_contention**: `value`

### Summary Fields

- `total_anomalies`: Total count
- `critical`, `error`, `warning`: Count per severity
- `by_type`: Count per anomaly type

## Use Cases

### 1. Initial Sanity Check

```bash
python vcd_analyzer.py anomaly-detect sim.vcd --json
```

Quickly scan a fresh simulation for obvious issues.

### 2. Focused Investigation

```bash
python vcd_analyzer.py anomaly-detect sim.vcd --filter "*axi*" --json
```

Check only AXI-related signals.

### 3. Time Window Analysis

```bash
python vcd_analyzer.py anomaly-detect sim.vcd --begin 100us --end 200us --json
```

Drill into a specific time range when an issue is suspected.

### 4. Strict Glitch Hunting

```bash
python vcd_analyzer.py anomaly-detect sim.vcd --glitch-threshold 10ns --json
```

Find any pulse narrower than 10ns.

### 5. Agent Multi-Step Workflow

```bash
# 1. Scan for anomalies
python vcd_analyzer.py anomaly-detect sim.vcd --json

# 2. For each critical/error anomaly, run causality analysis
python vcd_analyzer.py causality sim.vcd --effect <signal> --at <time> --json

# 3. If FSM is involved, trace state machine
python vcd_analyzer.py fsm-trace sim.vcd --state <state_sig> --json
```

## Agent Usage Guidelines

When a user asks "Is everything OK?":

1. Run `anomaly-detect` first
2. Check `summary.critical` - if > 0, focus there immediately
3. Group anomalies by `type` to identify common root causes
4. Use `causality` to drill into specific anomalies

### Interpreting Severity

- **critical**: Likely a major bug, must investigate
- **error**: Definite issue (x values, bus contention)
- **warning**: Possibly intentional but worth checking

### Common Workflows

**"Why isn't the design working?"**
1. anomaly-detect → see what's broken
2. Pick highest severity anomaly
3. causality on that anomaly's time
4. Drill down based on causal chain

**"Are there any CDC issues?"**
- Look for `metastability` anomalies
- These often indicate missing synchronizers

**"Is the bus working?"**
- Look for `bus_contention` anomalies
- Filter by bus signal pattern with `--filter`

## Implementation Notes

### Glitch Detection

Detects 0→1→0 or 1→0→1 patterns where the intermediate state is held for
less than the glitch threshold. Only applies to 1-bit signals.

### Stuck Detection

Examines gaps between consecutive events for each signal, plus the gap from
window start to first event, and from last event to window end.

### Metastability vs Bus Contention

- **metastability**: any x/z bits in the value (mixed known/unknown)
- **bus_contention**: ALL bits are x or z (multi-bit signal only)

This distinction helps Agents know whether to look for synchronizers
(metastability) or multi-driver bugs (bus contention).

### Performance

- Time complexity: O(n) where n is total events in window
- Memory: O(n) for storing per-signal event lists
- For large designs, use `--filter` to limit scope

## Examples

### Example 1: Find All Issues

```bash
$ python vcd_analyzer.py anomaly-detect sim.vcd --stuck-threshold 300ns --glitch-threshold 5ns

Anomaly Detection Report
Time range: 0s ~ 863ns
Signals analyzed: 7
Thresholds: stuck >= 300ns, glitch < 5ns

Summary:
  Total anomalies: 10
  Critical: 0
  Error:    2
  Warning:  8

By type:
  bus_contention: 1
  glitch: 2
  metastability: 1
  stuck_signal: 6

Anomalies (showing first 20):
  [WARNING ] 120ns        glitch             anomaly_test.glitch_signal
             Pulse width 2ns < minimum expected 5ns
  [ERROR   ] 202ns        metastability      anomaly_test.sync_flag
             Unknown value detected: x
  [ERROR   ] 272ns        bus_contention     anomaly_test.data_bus[7:0]
             Bus contention detected (all bits unknown: x)
  ...

Suggestions:
  - 1 metastability issue(s); review CDC (Clock Domain Crossing) design
  - 1 bus contention(s); check for multiple drivers
  - 2 glitch(es); check pulse width requirements
  - 6 stuck signal(s); verify expected activity
```

## Testing

Unit tests in `verify/test_anomaly_detect.py`:

```bash
python verify/test_anomaly_detect.py
```

Test coverage:
- Basic anomaly detection
- Each anomaly type (glitch, metastability, bus contention, stuck)
- Signal filtering
- Time range filtering
- Severity summary
- JSON format
- Chronological sorting

## Related Skills

- **causality**: Investigate the root cause of detected anomalies
- **fsm-trace**: If a state signal is anomalous, trace its behavior
- **protocol-decode**: Verify protocol compliance for detected issues

## Version History

- **v2.0.0** (2026-05-26): Initial implementation
  - Stuck signal detection with severity scaling
  - Glitch detection (narrow pulse)
  - Metastability detection (x/z values)
  - Bus contention detection (all-unknown buses)
  - Configurable thresholds
  - JSON output format
