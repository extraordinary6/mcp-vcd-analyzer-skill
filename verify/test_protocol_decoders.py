"""
Unit tests for APB/UART/SPI protocol decoders
"""

import json
import subprocess
import sys
from pathlib import Path


def run_decode(vcd_file, protocol, signals):
    """Helper to run protocol-decode and return parsed JSON"""
    fixtures_dir = Path(__file__).parent / 'fixtures'
    vcd_path = fixtures_dir / vcd_file

    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / 'vcd_analyzer.py'),
        'protocol-decode',
        str(vcd_path),
        '--protocol', protocol,
        '--signals', signals,
        '--json'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return json.loads(result.stdout)


# ----- APB Tests -----

def test_apb_basic():
    """Test APB transaction decoding"""
    result = run_decode('apb_basic.vcd', 'apb', '*p*')

    assert result['status'] == 'success'
    assert result['skill'] == 'protocol_decode'

    txns = result['result']['transactions']
    assert len(txns) == 3

    # First: write 0xDEADBEEF to 0x1000
    assert txns[0]['type'] == 'write'
    assert txns[0]['addr'] == '0x1000'
    assert txns[0]['data'] == '0xDEADBEEF'
    assert txns[0]['status'] == 'OKAY'

    # Second: read 0xDEADBEEF from 0x1000
    assert txns[1]['type'] == 'read'
    assert txns[1]['addr'] == '0x1000'
    assert txns[1]['data'] == '0xDEADBEEF'
    assert txns[1]['status'] == 'OKAY'

    # Third: write 0xCAFEBABE to 0x2000, SLVERR
    assert txns[2]['type'] == 'write'
    assert txns[2]['addr'] == '0x2000'
    assert txns[2]['data'] == '0xCAFEBABE'
    assert txns[2]['status'] == 'SLVERR'

    print("[PASS] APB basic decoding test passed")


def test_apb_statistics():
    """Test APB statistics calculation"""
    result = run_decode('apb_basic.vcd', 'apb', '*p*')

    stats = result['result']['statistics']
    assert stats['total_transactions'] == 3
    assert stats['read_count'] == 1
    assert stats['write_count'] == 2
    assert stats['error_count'] == 1
    assert stats['avg_latency'] is not None

    print("[PASS] APB statistics test passed")


def test_apb_error_suggestion():
    """Test that APB SLVERR generates suggestion"""
    result = run_decode('apb_basic.vcd', 'apb', '*p*')

    suggestions = ' '.join(result['suggestions']).lower()
    assert 'slverr' in suggestions or 'error' in suggestions

    print("[PASS] APB error suggestion test passed")


# ----- UART Tests -----

def test_uart_basic():
    """Test UART byte decoding"""
    result = run_decode('uart_basic.vcd', 'uart', 'uart_tx,uart_rx')

    assert result['status'] == 'success'

    txns = result['result']['transactions']
    assert len(txns) == 3

    # Bytes should be 'A', 'B', 'C'
    assert txns[0]['data'] == '0x41'
    assert txns[0]['data_ascii'] == 'A'
    assert txns[1]['data'] == '0x42'
    assert txns[1]['data_ascii'] == 'B'
    assert txns[2]['data'] == '0x43'
    assert txns[2]['data_ascii'] == 'C'

    # All on TX line
    for txn in txns:
        assert txn['line'] == 'tx'
        assert txn['type'] == 'tx'

    print("[PASS] UART basic decoding test passed")


def test_uart_baud_detection():
    """Test UART auto baud rate detection"""
    result = run_decode('uart_basic.vcd', 'uart', 'uart_tx,uart_rx')

    stats = result['result']['statistics']
    assert stats['baud_rate'] is not None
    assert stats['bit_time'] is not None

    # Bit time should be approximately 100ns (10 Mbaud)
    assert 90000 < stats['bit_time_ticks'] < 110000

    print("[PASS] UART baud detection test passed")


# ----- SPI Tests -----

def test_spi_basic():
    """Test SPI transaction decoding"""
    result = run_decode('spi_basic.vcd', 'spi', 'spi_*')

    assert result['status'] == 'success'

    txns = result['result']['transactions']
    assert len(txns) == 2

    # First transaction: MOSI=0xA5, MISO=0x5A
    assert txns[0]['mosi'] == '0xA5'
    assert txns[0]['miso'] == '0x5A'

    # Second transaction: MOSI=0xFF, MISO=0x00
    assert txns[1]['mosi'] == '0xFF'
    assert txns[1]['miso'] == '0x00'

    print("[PASS] SPI basic decoding test passed")


def test_spi_cs_delimitation():
    """Test that SPI transactions are correctly delimited by CS_N"""
    result = run_decode('spi_basic.vcd', 'spi', 'spi_*')

    txns = result['result']['transactions']

    # Each transaction should have a clear start/end
    for txn in txns:
        assert 'start_time' in txn
        assert 'end_time' in txn
        # Start time should be before end time
        assert txn['start_time_ticks'] < txn['end_time_ticks']

    print("[PASS] SPI CS delimitation test passed")


def test_spi_8bit_transfer():
    """Test that SPI transfers 8 bits per byte"""
    result = run_decode('spi_basic.vcd', 'spi', 'spi_*')

    txns = result['result']['transactions']
    for txn in txns:
        assert txn.get('mosi_bits') == 8
        assert txn.get('miso_bits') == 8

    print("[PASS] SPI 8-bit transfer test passed")


# ----- Common Tests -----

def test_unsupported_protocol():
    """Test that unsupported protocol raises error"""
    fixtures_dir = Path(__file__).parent / 'fixtures'
    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / 'vcd_analyzer.py'),
        'protocol-decode',
        str(fixtures_dir / 'apb_basic.vcd'),
        '--protocol', 'i2c',  # Not supported
        '--signals', '*',
        '--json'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0  # Should fail

    print("[PASS] Unsupported protocol test passed")


def test_json_format_compliance():
    """Test that all protocol decoders produce compliant JSON"""
    test_cases = [
        ('apb_basic.vcd', 'apb', '*p*'),
        ('uart_basic.vcd', 'uart', 'uart_tx,uart_rx'),
        ('spi_basic.vcd', 'spi', 'spi_*'),
    ]

    for vcd_file, protocol, signals in test_cases:
        result = run_decode(vcd_file, protocol, signals)
        assert 'status' in result
        assert 'skill' in result
        assert 'input' in result
        assert 'result' in result
        assert 'suggestions' in result
        assert 'transactions' in result['result']
        assert 'violations' in result['result']
        assert 'statistics' in result['result']

    print("[PASS] JSON format compliance test passed")


if __name__ == '__main__':
    print("Running APB/UART/SPI decoder tests...\n")

    # APB tests
    test_apb_basic()
    test_apb_statistics()
    test_apb_error_suggestion()

    # UART tests
    test_uart_basic()
    test_uart_baud_detection()

    # SPI tests
    test_spi_basic()
    test_spi_cs_delimitation()
    test_spi_8bit_transfer()

    # Common tests
    test_unsupported_protocol()
    test_json_format_compliance()

    print("\n[SUCCESS] All protocol decoder tests passed!")
