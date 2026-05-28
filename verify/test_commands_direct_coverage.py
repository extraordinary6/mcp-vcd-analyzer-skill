import json
import pytest
import vcd_analyzer as va
from conftest import write_vcd, minimal_vcd, ns, load_json_stdout


def make_axi_vcd(tmp_path):
    text = minimal_vcd('''$var wire 1 ! valid $end
$var wire 1 " ready $end
$var wire 8 # data $end
$var event 1 $ ev $end
''', '''$dumpvars
0!
0"
b00000000 #
$end
#5
1"
#10
1!
b00001010 #
#15
b00001011 #
#20
0!
#30
1$
#40
1$
''')
    return write_vcd(tmp_path, text)


def test_cmd_info_list_dump_summary_snapshot_compare_direct(tmp_path, capsys):
    p = make_axi_vcd(tmp_path)
    v = va.VCDParser(str(p))

    va.cmd_info(v, ns(json=True))
    info = load_json_stdout(capsys.readouterr().out)['result']
    assert info['signal_count'] == 4
    assert info['time_max_ticks'] == 40

    va.cmd_list(v, ns(json=False, filter='*data', limit=10))
    out = capsys.readouterr().out
    assert 'tb.data' in out

    va.cmd_dump(v, ns(json=True, begin='10ns', end='15ns', filter='data', limit=10))
    dump = load_json_stdout(capsys.readouterr().out)['result']
    assert [e['value'] for e in dump['events']] == ['10 (0x0a)', '11 (0x0b)']

    va.cmd_summary(v, ns(json=True, begin='0ns', end='20ns', filter='valid,data', verbose=True))
    summary = load_json_stdout(capsys.readouterr().out)['result']
    assert summary['selected'] == 2
    assert summary['active'] >= 1

    va.cmd_snapshot(v, ns(json=True, at='15ns', filter='data,valid'))
    snap = load_json_stdout(capsys.readouterr().out)['result']
    vals = {r['path']: r['value'] for r in snap['signals']}
    assert vals['tb.data'] == '11 (0x0b)'
    assert vals['tb.valid'] == '1'

    va.cmd_compare(v, ns(json=True, at='10ns,20ns', filter='valid'))
    comp = load_json_stdout(capsys.readouterr().out)['result']
    assert comp['total'] == 1
    assert comp['diffs'][0]['at_t1'] == '1'
    assert comp['diffs'][0]['at_t2'] == '0'


def test_cmd_search_interval_segment_event_direct(tmp_path, capsys):
    p = make_axi_vcd(tmp_path)
    v = va.VCDParser(str(p))

    va.cmd_search(v, ns(json=True, condition='valid=1', limit=10))
    intervals = load_json_stdout(capsys.readouterr().out)['result']
    assert intervals['mode'] == 'interval'
    assert intervals['intervals'][0]['begin_ticks'] == 10
    assert intervals['intervals'][0]['end_ticks'] == 20

    va.cmd_search(v, ns(json=True, condition='valid=1', show='data', limit=10))
    segs = load_json_stdout(capsys.readouterr().out)['result']
    assert segs['mode'] == 'segment'
    assert [s['values']['tb.data'] for s in segs['segments']] == ['10 (0x0a)', '11 (0x0b)']

    va.cmd_search(v, ns(json=True, condition='valid=1', changed='data', show='data,valid', limit=10))
    ev = load_json_stdout(capsys.readouterr().out)['result']
    assert ev['mode'] == 'event'
    assert [e['time_ticks'] for e in ev['events']] == [10, 15]

    va.cmd_search(v, ns(json=True, condition='ev=1', changed='ev', limit=10))
    ev2 = load_json_stdout(capsys.readouterr().out)['result']
    assert [e['time_ticks'] for e in ev2['events']] == [30, 40]


def test_command_error_paths_direct(tmp_path):
    # With the standardized Skill envelope, internal parse exceptions
    # (_TimeParseError / _ConditionParseError) are translated into
    # SkillError. In text mode, run_skill turns SkillError into a SystemExit
    # with the structured "Error: [CODE] ..." message.
    p = make_axi_vcd(tmp_path)
    v = va.VCDParser(str(p))

    def _exit_message(fn, kwargs):
        with pytest.raises(SystemExit) as exc:
            fn(v, ns(**kwargs))
        return str(exc.value)

    assert 'INVALID_TIME_RANGE' in _exit_message(va.cmd_dump,
        dict(begin='20ns', end='10ns'))
    assert 'INVALID_TIME_RANGE' in _exit_message(va.cmd_summary,
        dict(begin='20ns', end='10ns'))
    assert 'INVALID_TIME_RANGE' in _exit_message(va.cmd_compare,
        dict(at='20ns,10ns'))
    assert 'INVALID_ARGUMENT' in _exit_message(va.cmd_search,
        dict(condition='valid=1,ready=1', show='missing'))
    # ambiguous substring 'v' matches both valid/ev
    assert 'INVALID_ARGUMENT' in _exit_message(va.cmd_search,
        dict(condition='v=1'))


def test_search_empty_vcd_error(tmp_path):
    p = write_vcd(tmp_path, '$timescale 1ns $end\n$scope module tb $end\n$var wire 1 ! a $end\n$upscope $end\n$enddefinitions $end\n')
    v = va.VCDParser(str(p))
    # _search_end_time raises _ConditionParseError outside the cmd_search
    # try-block; it propagates up to main() which converts to a generic
    # text-mode error. Direct invocation still sees the raw exception.
    with pytest.raises(va._ConditionParseError):
        va.cmd_search(v, ns(condition='a=1'))
