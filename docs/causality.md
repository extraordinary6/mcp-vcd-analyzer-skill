# Causality Analysis Skill

## Overview

The `causality` skill analyzes potential root causes for a signal change. It examines signal changes within a configurable time window before the effect, ranks them by temporal proximity and historical correlation, and constructs a causal chain.

This is one of the most powerful skills for AI Agents debugging RTL designs — given a symptom (e.g., "error_flag went high at 17.5us"), it automatically surfaces the likely culprits.

## Command Format

```bash
python vcd_analyzer.py causality <file> --effect <signal> --at <time> [--window <duration>] [--json]
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | string | Yes | Path to VCD file |
| `--effect` | string | Yes | Effect signal name (must match exactly one signal) |
| `--at` | time | Yes | Time when the effect occurred (e.g., `17.5us`) |
| `--window` | duration | No | Search window before effect time (default: `100ns`) |
| `--json` | flag | No | Output JSON format |

## Algorithm

For each signal that changed within the search window:

1. **Temporal proximity score** (40% weight)
   - Closer in time to the effect = higher score
   - Linear decay: 1.0 at delta=0, 0.0 at delta=window

2. **Historical correlation score** (60% weight)
   - Counts how often this signal's change has historically preceded the effect signal's changes
   - Score = matches / total_effect_events
   - Captures real causal relationships across the entire trace

3. **Combined correlation** = 0.4 × temporal + 0.6 × historical

4. **Clock signal filtering**
   - Signals named `clk`, `clock`, `ck`, `*_clk`, `*_clock` are filtered
   - Signals with transitions >= 10x the effect signal are filtered (likely clocks)

5. **Confidence levels**
   - `high`: >= 5 historical occurrences
   - `medium`: 2-4 historical occurrences
   - `low`: < 2 historical occurrences

## Output Structure

### JSON Format

```json
{
  "status": "success",
  "skill": "causality",
  "input": {
    "file": "causality_basic.vcd",
    "effect_signal": "error_flag",
    "effect_time": "405ns",
    "search_window": "50ns"
  },
  "result": {
    "effect": {
      "signal": "causality_test.error_flag",
      "time": "405ns",
      "value": "1"
    },
    "potential_causes": [
      {
        "signal": "causality_test.fifo_full",
        "change_time": "395ns",
        "delta": "10ns",
        "value": "1",
        "correlation": 0.92,
        "temporal_score": 0.8,
        "historical_score": 1.0,
        "confidence": "high",
        "pattern": "fifo_full changed -> error_flag changed (observed 11/11 times)"
      }
    ],
    "causal_chain": [
      {"signal": "counter[3:0]", "time": "355ns", "value": "3"},
      {"signal": "busy", "time": "375ns", "value": "1"},
      {"signal": "timeout_counter", "time": "385ns", "value": "100"},
      {"signal": "fifo_full", "time": "395ns", "value": "1"},
      {"signal": "error_flag", "time": "405ns", "value": "1"}
    ]
  },
  "suggestions": [
    "High correlation with fifo_full (92%), likely root cause"
  ]
}
```

### Output Fields

#### effect (object)
Information about the signal change being analyzed:
- `signal`: Full signal path
- `time`: When the effect occurred (formatted)
- `time_ticks`: When the effect occurred (in ticks)
- `value`: Signal value at effect time

#### potential_causes (array)
Each candidate cause includes:
- `signal`: Candidate signal path
- `change_time`: When the candidate changed (formatted)
- `change_time_ticks`: When the candidate changed (in ticks)
- `delta`: Time between candidate change and effect (formatted)
- `delta_ticks`: Time delta in ticks
- `value`: Value the candidate changed to
- `correlation`: Combined correlation score (0.0-1.0)
- `temporal_score`: Temporal proximity score (0.0-1.0)
- `historical_score`: Historical correlation score (0.0-1.0)
- `confidence`: `high`, `medium`, or `low`
- `pattern`: Human-readable description with statistics

#### causal_chain (array)
Top causes arranged chronologically, ending with the effect.

## Use Cases

### 1. Debug Unexpected Signal Behavior

```bash
python vcd_analyzer.py causality sim.vcd --effect error_flag --at 17.5us --json
```

When `error_flag` unexpectedly goes high, find what changed just before.

### 2. Trace State Transition Trigger

```bash
python vcd_analyzer.py causality sim.vcd --effect state --at 250ns --window 50ns
```

Find what triggered a state transition by analyzing signals that changed in the 50ns window before.

### 3. Identify Performance Bottleneck

```bash
python vcd_analyzer.py causality sim.vcd --effect stall --at 1.2ms --window 200ns
```

When a pipeline stalls, find the signals correlated with the stall event.

### 4. Multi-Step Debug Flow (Agent Workflow)

```bash
# Step 1: Find anomalies
python vcd_analyzer.py anomaly-detect sim.vcd --filter "*error*" --json

# Step 2: Pick the most severe anomaly time and analyze causes
python vcd_analyzer.py causality sim.vcd --effect <signal> --at <time> --json

# Step 3: Drill into a state machine if needed
python vcd_analyzer.py fsm-trace sim.vcd --state <state_sig> --json
```

## Agent Usage Guidelines

When a user reports an unexpected event:

- **"Why did X go high at time T?"** → Run causality with effect=X and at=T
- **"What caused the error?"** → First locate the error time, then run causality
- **"Trace the root cause"** → Use `causal_chain` to understand the sequence of events

### Interpreting Results

- **correlation >= 0.7**: Strong evidence of causal relationship
- **correlation 0.4-0.7**: Moderate; investigate further
- **correlation < 0.4**: Weak; possibly coincidental
- **confidence = high**: Pattern observed many times, very reliable
- **confidence = low**: Few observations, might be coincidence

### Common Pitfalls

1. **Wrong window size**: If no causes found, try expanding `--window`.
   Default is 100ns but may need to be larger for slow protocols.

2. **Single-event noise**: If `historical_score` is low but `temporal_score` is high,
   the signal happened to change right before by coincidence. Check the `pattern` field.

3. **Indirect causes**: The actual root cause may not appear in `potential_causes` if it
   changed too far before the effect. Use `causal_chain` to spot indirect chains.

## Implementation Notes

### Clock Detection

The analyzer automatically filters out clock signals using two heuristics:
- **Name-based**: Signals named `clk`, `clock`, `ck` or ending in `_clk`/`_clock`
- **Frequency-based**: Signals with transitions >= 10x the effect signal's transitions

This prevents the analyzer from incorrectly identifying the clock as the cause
of every event.

### Performance

- Time complexity: O(n × m) where n is signals in window, m is effect occurrences
- Memory: O(n) to store candidate signals
- For large designs, narrow the search window or filter signals beforehand

## Examples

### Example 1: FIFO Full Causing Error

```bash
$ python vcd_analyzer.py causality sim.vcd --effect error_flag --at 405ns --window 50ns

Effect Signal: causality_test.error_flag
Effect Time:   405ns (value=1)
Search Window: 50ns before effect

Potential Causes: 4 found
  #   Signal                                             Delta      Value   Corr     Confidence
  0   causality_test.fifo_full                           10ns       1       0.92     high
  1   causality_test.timeout_counter[7:0]                20ns       100     0.404    medium
  2   causality_test.busy                                30ns       1       0.324    medium
  3   causality_test.counter[3:0]                        50ns       3       0.109    medium

Causal Chain (chronological):
  355ns        counter[3:0]                                       = 3 (0x3)
  375ns        busy                                               = 1
  385ns        timeout_counter[7:0]                               = 100 (0x64)
  395ns        fifo_full                                          = 1
  405ns        error_flag                                         = 1 <-- EFFECT

Suggestions:
  - High correlation with fifo_full (92%), likely root cause
  - Found 4 potential causes; check causal_chain for temporal ordering
```

### Example 2: No Causes Found

```bash
$ python vcd_analyzer.py causality sim.vcd --effect error_flag --at 405ns --window 5ns

Effect Signal: causality_test.error_flag
Effect Time:   405ns (value=1)
Search Window: 5ns before effect

No correlated signals found in search window.

Suggestions:
  - No correlated signals found in search window; try expanding --window or check for spontaneous events
```

## Testing

Unit tests are available in `verify/test_causality.py`:

```bash
python verify/test_causality.py
```

Test coverage includes:
- Basic causality analysis
- Top cause identification
- Clock filtering
- Causal chain construction
- Narrow window handling
- Temporal score validation
- JSON output format

## Related Skills

- **anomaly-detect**: Find what events to analyze with causality
- **fsm-trace**: Drill into state machine if state signal is the cause
- **protocol-decode**: Understand protocol-level violations in the causal chain

## Version History

- **v2.0.0** (2026-05-26): Initial implementation
  - Temporal proximity scoring
  - Historical correlation analysis
  - Automatic clock filtering
  - Causal chain construction
  - JSON output format
