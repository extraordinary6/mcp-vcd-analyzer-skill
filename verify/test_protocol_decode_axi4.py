"""
Test protocol-decode skill for AXI4
"""
import subprocess
import json
import sys
from pathlib import Path


def run_decode(vcd_file, protocol, signals):
    """Run protocol-decode command and return parsed JSON"""
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
        print(f"Error: {result.stderr}")
        return None
    return json.loads(result.stdout)


def test_axi4_basic():
    """Test basic AXI4 transaction decoding"""
    result = run_decode('axi4_basic.vcd', 'axi4', '*m_axi*')

    assert result is not None
    assert result['status'] == 'success'
    assert result['skill'] == 'protocol_decode'

    # Check transactions
    txns = result['result']['transactions']
    assert len(txns) == 3, f"Expected 3 transactions, got {len(txns)}"

    # Check first write transaction
    assert txns[0]['type'] == 'write'
    assert txns[0]['addr'] == '0x1000'
    assert txns[0]['burst_len'] == 1
    assert txns[0]['status'] == 'OKAY'
    assert len(txns[0]['data']) == 1
    assert txns[0]['data'][0] == '0xDEADBEEF'

    # Check second write transaction (burst)
    assert txns[1]['type'] == 'write'
    assert txns[1]['addr'] == '0x2000'
    assert txns[1]['burst_len'] == 2
    assert txns[1]['status'] == 'OKAY'
    assert len(txns[1]['data']) == 2
    assert txns[1]['data'][0] == '0xCAFEBABE'
    assert txns[1]['data'][1] == '0x12345678'

    # Check read transaction
    assert txns[2]['type'] == 'read'
    assert txns[2]['addr'] == '0x1000'
    assert txns[2]['burst_len'] == 1
    assert txns[2]['status'] == 'OKAY'
    assert len(txns[2]['data']) == 1
    assert txns[2]['data'][0] == '0xDEADBEEF'

    # Check statistics
    stats = result['result']['statistics']
    assert stats['total_transactions'] == 3
    assert stats['read_count'] == 1
    assert stats['write_count'] == 2

    print("[PASS] AXI4 basic decoding test passed")


if __name__ == '__main__':
    print("Running AXI4 protocol decoder tests...\n")
    test_axi4_basic()
    print("\n[SUCCESS] All AXI4 tests passed!")
