# Protocol Decoders: APB, UART, SPI

Extension of the `protocol-decode` skill to support APB, UART, and SPI protocols
in addition to AXI4.

See `docs/protocol_decode.md` for AXI4-specific documentation.

## Supported Protocols

| Protocol | Description |
|----------|-------------|
| AXI4 | AMBA AXI4 (read/write with handshakes) |
| APB | AMBA APB3 (peripheral bus with PREADY/PSLVERR) |
| UART | Universal Asynchronous Receiver-Transmitter |
| SPI | Serial Peripheral Interface (Mode 0) |

## Command Format

```bash
python vcd_analyzer.py protocol-decode <file> --protocol <type> --signals <pattern> [--json]
```

---

## APB Decoder

### Signal Identification

The decoder looks for these APB signals (case-insensitive substring match on
last path component):

| Signal | Required | Description |
|--------|----------|-------------|
| `paddr` | Yes | Address bus |
| `pwrite` | Yes | Direction (1=write, 0=read) |
| `psel` | Yes | Select |
| `penable` | Yes | Enable |
| `pwdata` | Yes (writes) | Write data |
| `prdata` | Yes (reads) | Read data |
| `pready` | Yes | Slave ready |
| `pslverr` | No | Slave error |

### Decoded Transactions

Each APB transaction includes:
- `type`: `read` or `write`
- `addr`: Hex address
- `data`: Hex data (write data for writes, read data for reads)
- `status`: `OKAY` or `SLVERR`
- `start_time` / `end_time`: Transaction timing

### Detected Violations

- `protocol_violation`: PSEL deasserted in SETUP phase without entering ACCESS

### Example

```bash
$ python vcd_analyzer.py protocol-decode apb_basic.vcd --protocol apb --signals "*p*"

Protocol: APB
Time range: 0s ~ end

Transactions: 3
  Reads:  1
  Writes: 2
  Errors: 1

Transaction Details:
  [0] WRITE: 0x1000 data=0xDEADBEEF @ 40ns [OKAY]
  [1] READ: 0x1000 data=0xDEADBEEF @ 90ns [OKAY]
  [2] WRITE: 0x2000 data=0xCAFEBABE @ 140ns [SLVERR]

Statistics:
  Avg Latency: 23.333ns

Suggestions:
  - 1 APB transaction(s) returned SLVERR
```

---

## UART Decoder

### Signal Identification

The decoder looks for TX and RX lines:
- `tx` / `txd`: Transmit line
- `rx` / `rxd`: Receive line

### Decoding

- Frame format: 1 start bit (0), 8 data bits (LSB first), 1 stop bit (1)
- Baud rate: Auto-detected from minimum gap between transitions
- Each decoded byte includes both hex value and ASCII representation (if printable)

### Decoded Transactions

Each UART byte includes:
- `type`: `tx` or `rx`
- `data`: Hex byte value
- `data_ascii`: ASCII character (or `.` if non-printable)
- `line`: `tx` or `rx`
- `start_time` / `end_time`: Frame timing

### Detected Violations

- `framing_error`: Invalid bit value during data sampling
- `stop_bit_error`: Stop bit not high (= 1)

### Example

```bash
$ python vcd_analyzer.py protocol-decode uart_basic.vcd --protocol uart --signals "uart_tx,uart_rx"

Protocol: UART
Time range: 0s ~ end

Bytes decoded: 3
  TX: 3
  RX: 0
  Bit time: 100ns (~10000000 baud)

Bytes:
  [0] TX: 0x41 'A' @ 120ns
  [1] TX: 0x42 'B' @ 1.22us
  [2] TX: 0x43 'C' @ 2.42us

Suggestions:
  - Auto-detected baud rate: 10000000
```

---

## SPI Decoder

### Signal Identification

The decoder looks for SPI signals (case-insensitive substring match):

| Signal | Required | Description |
|--------|----------|-------------|
| `sclk` / `sck` | Yes | Serial clock |
| `cs` / `cs_n` / `ncs` | No | Chip select (active low) |
| `mosi` | No | Master Out, Slave In |
| `miso` | No | Master In, Slave Out |

### Decoding

- Mode 0 only (CPOL=0, CPHA=0): Data sampled on rising edge of SCLK
- Transactions delimited by CS_N going low/high
- Data assumed MSB-first

### Decoded Transactions

Each SPI transaction includes:
- `type`: `spi`
- `mosi`: Hex value sent from master (if MOSI signal present)
- `mosi_bits`: Number of MOSI bits transferred
- `miso`: Hex value sent from slave (if MISO signal present)
- `miso_bits`: Number of MISO bits transferred
- `start_time` / `end_time`: From CS_N falling to CS_N rising

### Example

```bash
$ python vcd_analyzer.py protocol-decode spi_basic.vcd --protocol spi --signals "spi_*"

Protocol: SPI
Time range: 0s ~ end

Transactions: 2

Transaction Details:
  [0] @ 70ns -> 970ns: MOSI=0xA5, MISO=0x5A
  [1] @ 1.17us -> 2.07us: MOSI=0xFF, MISO=0x00
```

---

## Agent Usage Guidelines

When deciding which protocol decoder to use:

1. **Check signal names**: AXI signals have `aw`/`w`/`b`/`ar`/`r` prefixes;
   APB has `p*`; UART has `tx`/`rx`; SPI has `sclk`/`mosi`/`miso`/`cs`
2. **Use signal pattern**: Always pass `--signals` to focus on the relevant bus
3. **For UART**: Specify TX/RX signals explicitly with comma-separated list
4. **For SPI**: Make sure the pattern catches all 4 signals (sclk, cs, mosi, miso)

### Common Workflows

**"Decode this AXI traffic"**
```bash
python vcd_analyzer.py protocol-decode sim.vcd --protocol axi4 --signals "m_axi_*"
```

**"Show me APB register accesses"**
```bash
python vcd_analyzer.py protocol-decode sim.vcd --protocol apb --signals "*p*"
```

**"What's being sent over UART?"**
```bash
python vcd_analyzer.py protocol-decode sim.vcd --protocol uart --signals "tx,rx"
```

**"Decode SPI flash commands"**
```bash
python vcd_analyzer.py protocol-decode sim.vcd --protocol spi --signals "flash_*"
```

## Limitations

### APB
- Only supports APB3 (with PREADY); APB2 wait states not detected
- Does not check setup/hold timing

### UART
- Assumes 1 start, 8 data, 1 stop bit format (most common)
- Does not detect parity bits
- Baud rate is heuristically detected from minimum transition gap

### SPI
- Mode 0 only (CPOL=0, CPHA=0)
- Assumes MSB-first transfer
- Multi-slave configurations may require per-CS analysis

## Version History

- **v2.0.0** (2026-05-26): Initial implementation of APB/UART/SPI decoders
  - APB3 with PSLVERR support
  - UART with auto baud detection
  - SPI Mode 0 with MOSI/MISO decoding
  - Unified output format with AXI4 decoder
