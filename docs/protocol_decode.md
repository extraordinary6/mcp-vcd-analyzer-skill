# Protocol Decode Skill

## Overview

The `protocol-decode` skill analyzes VCD files and decodes bus protocol transactions, providing detailed transaction information, protocol violation detection, and performance statistics.

## Supported Protocols

- **AXI4**: AMBA AXI4 protocol (read/write transactions)
- APB: AMBA APB protocol (coming soon)
- UART: Universal Asynchronous Receiver-Transmitter (coming soon)
- SPI: Serial Peripheral Interface (coming soon)

## Usage

```bash
python vcd_analyzer.py protocol-decode <vcd_file> --protocol <protocol_type> [options]
```

### Options

- `--protocol TYPE`: Protocol type (required): `axi4`, `apb`, `uart`, `spi`
- `--signals PATTERN`: Signal pattern to match (e.g., `*m_axi*`, `s_apb_*`); default: `*`
- `--begin TIME`: Start time (e.g., `0`, `100ns`, `17.5us`)
- `--end TIME`: End time (same format as begin)
- `--json`: Output in JSON format
- `--limit N`: Maximum number of transactions to display
- `--verbose`: Show extra details

## Examples

### AXI4 Protocol Decoding

```bash
# Decode all AXI4 transactions
python vcd_analyzer.py protocol-decode design.vcd --protocol axi4 --signals "*m_axi*"

# Decode transactions in a specific time range
python vcd_analyzer.py protocol-decode design.vcd --protocol axi4 --signals "*axi*" --begin 100ns --end 500ns

# JSON output for programmatic processing
python vcd_analyzer.py protocol-decode design.vcd --protocol axi4 --signals "*axi*" --json
```

## Output Format

### Text Output

```
Protocol: AXI4
Time range: 0s ~ end

Transactions: 3
  Reads:  1
  Writes: 2

Transaction Details:
  [0] WRITE: 0x1000 @ 50ns -> 80ns [OKAY]
      Data: 0xDEADBEEF
  [1] WRITE: 0x2000 @ 120ns -> 160ns [OKAY]
      Data: 0xCAFEBABE, 0x12345678
  [2] READ: 0x1000 @ 200ns -> 210ns [OKAY]
      Data: 0xDEADBEEF

Violations: 0

Statistics:
  Avg Latency: 26.666ns
  Bandwidth Utilization: 0.0%

Suggestions:
  - Low bandwidth utilization (0%), check for stalls
```

### JSON Output

```json
{
  "status": "success",
  "skill": "protocol_decode",
  "input": {
    "file": "design.vcd",
    "protocol": "axi4",
    "signals": ["*m_axi*"],
    "time_range": ["0s", "end"]
  },
  "result": {
    "transactions": [
      {
        "id": 0,
        "type": "write",
        "start_time": "50ns",
        "start_time_ticks": 50000,
        "addr": "0x1000",
        "burst_len": 1,
        "status": "OKAY",
        "end_time": "80ns",
        "end_time_ticks": 80000,
        "data": ["0xDEADBEEF"]
      }
    ],
    "violations": [],
    "statistics": {
      "total_transactions": 3,
      "read_count": 1,
      "write_count": 2,
      "avg_latency": "26.666ns",
      "avg_latency_ticks": 26666,
      "bandwidth_utilization": 0.0
    }
  },
  "suggestions": [
    "Low bandwidth utilization (0%), check for stalls"
  ]
}
```

## AXI4 Protocol Details

### Detected Signals

The decoder automatically identifies AXI4 signals based on naming conventions:

**Write Address Channel:**
- `awvalid`, `awready`, `awaddr`, `awlen`, `awsize`, `awburst`

**Write Data Channel:**
- `wvalid`, `wready`, `wdata`, `wlast`, `wstrb`

**Write Response Channel:**
- `bvalid`, `bready`, `bresp`

**Read Address Channel:**
- `arvalid`, `arready`, `araddr`, `arlen`

**Read Data Channel:**
- `rvalid`, `rready`, `rdata`, `rlast`, `rresp`

### Transaction Information

Each transaction includes:
- **Type**: `read` or `write`
- **Address**: Transaction address
- **Burst Length**: Number of data beats
- **Data**: Array of data values (hex format)
- **Status**: Response status (`OKAY`, `EXOKAY`, `SLVERR`, `DECERR`)
- **Timing**: Start and end timestamps

### Protocol Violations

The decoder detects:
- Incomplete transactions (missing response)
- Burst length mismatches
- Protocol timing violations (coming soon)

### Performance Statistics

- **Total Transactions**: Count of all transactions
- **Read/Write Count**: Breakdown by type
- **Average Latency**: Mean time from address to response
- **Bandwidth Utilization**: Percentage of time with active transfers

## Testing

Run the test suite:

```bash
cd verify/fixtures
python test_protocol_decode.py
```

## Implementation Notes

- The decoder uses event-driven analysis, tracking signal changes over time
- Handshake detection follows AXI4 specification (valid && ready)
- Burst transactions are reconstructed from individual data beats
- Transaction matching uses FIFO ordering for pending transactions
