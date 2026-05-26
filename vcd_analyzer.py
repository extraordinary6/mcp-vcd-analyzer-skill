#!/usr/bin/env python3
"""VCD waveform analyzer for Agent-based RTL debug.

Usage: vcd_analyzer [--json] <command> <file> [options]

Commands:
  info       <file>                               File overview (timescale, signal count, time span, scopes)
  list       <file> [--filter K1,K2]               List signals with path and bit width
  dump       <file> [--begin T] [--end T] [--filter K1,K2]   Print signal value changes in time order
  summary    <file> [--begin T] [--end T] [--filter K1,K2]   Per-signal stats: change count, unique values, static detection
  snapshot   <file> --at T [--filter K1,K2]        Known signal values at a given time point
  compare    <file> --at T1,T2 [--filter K1,K2]    Diff signal values between two time points
  search     <file> --condition C [--show K1,K2] [--changed K] [--begin T] [--end T]
                                                        Conditional search and associated signal observation

Global options:
  --json       Output compact structured JSON instead of text (time fields include *_ticks)
  --limit N    Max rows/records to emit; default 200; 0 = unlimited.
               Streaming commands stop after detecting the first unshown result.
  --verbose    Show extra fields; if --limit is omitted, disables truncation

Argument formats:
  <file>          VCD file path
  --filter K1,K2  Comma-separated patterns. Plain text uses case-insensitive substring match;
                  patterns containing * or ? use case-insensitive glob match.
                  e.g. --filter clk,rst   --filter '*_valid,*_ready,*_data'   --filter 'top.u_dma.*'
  --begin T       Start time with optional unit suffix: 0, 100ns, 17.5us, 1ms, 500ps, 200fs
  --end T         End time, same format as --begin. Omit for no upper bound
  --at T          Time point for snapshot. For compare: two points comma-separated: --at 17.5us,17.7us
  --condition C   Comma-separated AND conditions: SIG=VAL, SIG==VAL, SIG!=VAL.
                  Condition signal patterns must match exactly one signal.
                  SIG!=VAL does not match x/z/undef; use SIG=x to search unknown.
                  Values use numeric or 4-state matching: 5, 0x5, b0101, b1x0z.
  --show K1,K2    Optional associated signals to display while condition holds;
                  segment mode splits whenever shown values change.
  --changed K     Optional trigger signal; emit events only when this signal really changes.
                  For ordinary signals, first observed values are not treated as changes.
                  VCD event variables count each trigger; t=0 initialization is ignored.

Examples:
  vcd_analyzer info sim.vcd
  vcd_analyzer list sim.vcd --filter tdata,tvalid,tready
  vcd_analyzer dump sim.vcd --begin 17.5us --end 17.6us --filter clk,rst,state
  vcd_analyzer summary sim.vcd --filter dll_st,locked
  vcd_analyzer snapshot sim.vcd --at 17.55us --filter init_done,state
  vcd_analyzer compare sim.vcd --at 17.535us,17.56us --filter init_done,link_active,state
  vcd_analyzer search sim.vcd --condition "state=5"
  vcd_analyzer search sim.vcd --condition "arvalid=1,arready=1" --show araddr,arlen,arid
  vcd_analyzer search sim.vcd --changed data_out --condition "valid=0" --show data_out,valid
  vcd_analyzer search sim.vcd --condition "valid=x"
  vcd_analyzer --json summary sim.vcd --filter tvalid,tready

Notes:
  search requires at least one observed value_change in the VCD data section;
  empty waveforms are reported as an input/data issue rather than as a false
  "no match" result.
"""

__version__ = '1.3.9'

import sys
import os
import re
import math
import json
import argparse
from collections import defaultdict

# -- Time utilities ----------------------------------------------------------

_UNITS = {'fs': 1e-15, 'ps': 1e-12, 'ns': 1e-9, 'us': 1e-6, 'ms': 1e-3, 's': 1.0}


# Resource limits — generous defaults that never trip on real engineering
# files but reject pathological/malicious inputs cleanly.
# Override per-process via environment variables, e.g.:
#   VCD_ANALYZER_MAX_VARS=2000000 vcd_analyzer info big.vcd
def _env_int(name, default):
    """Read a positive integer resource limit from the environment."""
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_VARS = _env_int('VCD_ANALYZER_MAX_VARS', 1_000_000)
MAX_REASSEMBLE_BITS = _env_int('VCD_ANALYZER_MAX_REASSEMBLE_BITS', 65536)
MAX_TIME_ARG_LEN = 100         # CLI/programmatic time string length cap
MAX_TIME_TICKS = (1 << 63) - 1  # int64 max — keeps downstream arithmetic safe
MAX_FILTER_PATTERN_LEN = 256
MAX_FILTER_WILDCARDS = 16

# Additional header-section caps. Defaults are far above any legitimate
# engineering VCD but cleanly refuse pathological/malicious construction.
#
# Two failure modes are used:
#  - fail-fast (raise _VCDResourceError): for caps whose violation would
#    corrupt data correctness (lost value_changes, lost $var declarations,
#    deep scope that breaks path reconstruction).
#  - silent drop (truncate retained list): for metadata-only caps whose
#    violation only affects the cosmetic output of `info --verbose`. These
#    are noted inline where they apply.
MAX_INT_DIGITS = 100              # any int-from-string in header (width, bit idx, msb/lsb)
MAX_SIGNAL_WIDTH = MAX_REASSEMBLE_BITS  # max bits per single $var declaration
MAX_VALUE_ARG_LEN = MAX_SIGNAL_WIDTH + 2  # target value string, allows b<MAX_SIGNAL_WIDTH bits>
MAX_DECIMAL_VALUE_DIGITS = 100  # avoid Python 3.9 int() CPU DoS on --value decimal
MAX_HEX_VALUE_DIGITS = max(1, (MAX_SIGNAL_WIDTH + 3) // 4)
MAX_HEADER_BODY_TOKENS = 131072   # any $<kw>...$end section body length (metadata-only effect:
                                  # truncates $comment / $date / $version bodies; $var bodies
                                  # are never long enough to be affected in practice)
MAX_COMMENTS = 1024               # number of $comment sections retained (metadata-only)
MAX_SCOPE_DEPTH = 256             # $scope nesting depth (fail-fast: lost scope breaks path)
MAX_INITIAL_TOKENS = 131072       # tokens buffered from same line as $enddefinitions $end
                                  # (fail-fast: these are data tokens, dropping them
                                  # would silently corrupt waveforms)


# IEEE 1364-2005 18.2.2 real value_change is 'r' + real_number where
# real_number follows C99 printf("%g") shape: optional sign, integer and/or
# fractional digits, optional exponent. Used to reject garbage tokens like
# 'reset' that start with 'r' but aren't a numeric value_change.
#
# Pattern written to avoid backtracking (no alternation overlap):
#   sign?  ( digits  ( '.' digits? )?  |  '.' digits )  exponent?
# The two top-level alternatives are disjoint (start with digit vs '.'),
# so the engine never has to backtrack between them. Inputs are also
# length-bounded below; real_number tokens in VCD value_changes shouldn't
# exceed reasonable %g output width.
_REAL_RE = re.compile(
    r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$'
)
_REAL_MAX_LEN = 64  # Defensive cap: %.16g + sign + exponent fits well under this

# Extended VCD port state character → 4-state mapping (IEEE 1364-2005 18.4.3.1).
# Strengths (driver levels 0-7) are not exposed; for RTL debug the 4-state value
# is what matters. Conflict states (d/u/l/h) collapse to their logical level.
_PORT_STATE = {
    # Input (testfixture)
    'D': '0', 'U': '1', 'N': 'x', 'Z': 'z', 'd': '0', 'u': '1',
    # Output (DUT)
    'L': '0', 'H': '1', 'X': 'x', 'T': 'z', 'l': '0', 'h': '1',
    # Unknown direction (both input and output active)
    '0': '0', '1': '1', '?': 'x', 'F': 'z',
    'A': 'x', 'a': 'x', 'B': 'x', 'b': 'x', 'C': 'x', 'c': 'x', 'f': 'z',
}


def _parse_timescale(text):
    """Extract base time unit in seconds from $timescale line.

    IEEE 1364-2005 18.2.3.8 only allows 1, 10, or 100 as the number, but
    we accept any positive integer for lenience. A zero, missing, or
    pathologically long number falls back to 1e-12 (1 ps) — the standard's
    default — to avoid downstream division-by-zero in parse_time and CPU
    DoS from int() on huge digit strings (Python 3.9 is O(n^2)).
    """
    m = re.search(r'(\d+)\s*(fs|ps|ns|us|ms|s)', text)
    if not m:
        return 1e-12
    digits = m.group(1)
    # Length cap matches parse_time's MAX_TIME_ARG_LEN. The standard allows
    # only 1/10/100 (≤3 digits), so anything multi-line absurd is corruption.
    if len(digits) > MAX_TIME_ARG_LEN:
        return 1e-12
    n = int(digits)
    if n <= 0:
        return 1e-12
    return n * _UNITS[m.group(2)]


class _TimeParseError(ValueError):
    """Raised by parse_time on invalid input; caught in main() for friendly CLI errors."""


class _FilterParseError(argparse.ArgumentTypeError):
    """Raised when --filter contains an unsafe or unsupported pattern.
    argparse handles this automatically with a friendly message."""


class _ValueParseError(ValueError):
    """Raised when a target value is too large or malformed beyond tolerant matching."""


class _ConditionParseError(ValueError):
    """Raised when search --condition / --show / --changed is invalid."""


class _VCDResourceError(RuntimeError):
    """Raised when a VCD input exceeds configured resource limits.
    Surfaced in main() as a CLI error, no Python traceback."""


def _check_time_range(ticks, original):
    if ticks < 0:
        raise _TimeParseError('time must be non-negative; got {!r}'.format(original))
    if ticks > MAX_TIME_TICKS:
        raise _TimeParseError(
            'time value too large; got {!r}, max ticks is {}'.format(original, MAX_TIME_TICKS))
    return ticks


def _parse_vcd_timestamp_token(tok):
    """Parse a VCD '#<digits>' simulation_time token into an int.

    Returns int on success, None for malformed input (e.g. '#1.5' — digit
    prefix passed the isdigit() pre-check but int() rejects it). The
    None-path preserves the round-7 "tolerant reader" behavior: malformed
    timestamps are silently skipped, the rest of the stream continues.

    Raises _VCDResourceError for inputs that would cause CPU/memory DoS or
    exceed int64. Python 3.11+ has PEP 678 (int_max_str_digits) baked in,
    but we target 3.9 where int(s) is O(n^2) for huge n; even on 3.11+
    the PEP 678 ValueError would otherwise become an unhandled traceback.
    """
    digits = tok[1:]
    if len(digits) > MAX_TIME_ARG_LEN:
        raise _VCDResourceError(
            'VCD timestamp token too long: {} digits (max {}); '
            'file may be corrupt or malicious'.format(len(digits), MAX_TIME_ARG_LEN))
    try:
        v = int(digits)
    except ValueError:
        return None  # tolerated malformed (e.g. '#1.5')
    if v > MAX_TIME_TICKS:
        raise _VCDResourceError(
            'VCD timestamp too large: got {}, max ticks is {}'.format(v, MAX_TIME_TICKS))
    return v


def _safe_int_digits(s):
    """Parse a digit string from VCD header to int with bounded cost.

    Used wherever the header declares an integer in user-controlled
    position: $var width, [msb:lsb] range, [N] bit index. Returns int
    on success, None for empty / malformed / oversized inputs. Never
    raises — caller decides whether to skip the declaration or raise
    _VCDResourceError with richer context.

    Length cap MAX_INT_DIGITS=100 defends against the same Python 3.9
    O(n^2) decimal-int and Python 3.11+ PEP 678 ValueError issues as
    _parse_vcd_timestamp_token. 100 digits is far beyond any legitimate
    bit width or index (which fit in 4 digits comfortably).
    """
    if not s or len(s) > MAX_INT_DIGITS:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_time(s, ts_sec):
    """Parse time string with optional unit suffix to internal VCD timestamp.

    VCD timestamps per IEEE 1364-2005 18.2.3.8 are non-negative integers.
    - With unit: any non-negative value, scaled to ticks (e.g. '17.5us', '.5ns')
    - Without unit: must be a non-negative integer tick count

    Bare '10.5' (no unit) is rejected to avoid silent int() truncation;
    use '10.5ns' to specify a fractional time. Whitespace between number
    and unit is NOT allowed ('5 ns' is rejected; standard unit literals
    are written as a single token).

    Hardened against:
    - ZeroDivisionError when ts_sec <= 0 (e.g. malformed $timescale)
    - Overflow / non-finite intermediate values
    - Overlong input strings (CPU DoS)
    - Tick counts exceeding int64
    """
    if s is None:
        return None
    if not isinstance(s, str):
        raise _TimeParseError(
            'time value must be a string; got {}'.format(type(s).__name__))
    if len(s) > MAX_TIME_ARG_LEN:
        raise _TimeParseError(
            'time value too long; max length is {}'.format(MAX_TIME_ARG_LEN))
    stripped = s.strip()
    # Anchored match — no \s* between value and unit ('5 ns' must be rejected).
    m = re.match(r'^([+-]?)(\d+\.\d*|\.\d+|\d+)(fs|ps|ns|us|ms|s)?$', stripped)
    if not m:
        # Fall back to bare integer ('100', '-5'); reject anything else.
        try:
            v = int(stripped)
        except (ValueError, TypeError):
            raise _TimeParseError(
                'invalid time value {!r}; expected integer ticks or value '
                'with fs/ps/ns/us/ms/s suffix'.format(s))
        return _check_time_range(v, s)
    sign, val_str, unit = m.group(1), m.group(2), m.group(3)
    if sign == '-' and val_str.strip('0.') != '':
        # Reject negative non-zero. '-0' / '-0.0' silently treated as 0.
        raise _TimeParseError(
            'time must be non-negative; got {!r}'.format(s))
    if unit is None:
        if '.' in val_str:
            raise _TimeParseError(
                'bare numeric time must be integer ticks; got {!r}. '
                'Use a unit suffix for fractional times, e.g. {}ns'.format(s, val_str))
        return _check_time_range(int(val_str), s)
    if ts_sec <= 0:
        raise _TimeParseError(
            'cannot convert time with unit because VCD $timescale is 0 or invalid')
    try:
        scaled = float(val_str) * _UNITS[unit] / ts_sec
    except (OverflowError, ValueError, ZeroDivisionError):
        raise _TimeParseError('invalid time value {!r}'.format(s))
    if not math.isfinite(scaled):
        raise _TimeParseError('time value {!r} is not finite'.format(s))
    return _check_time_range(int(round(scaled)), s)


def fmt_time(ts, ts_sec):
    """Format internal timestamp to human-readable string.

    Picks the smallest unit u where |scaled| < 1000, preferring natural
    boundaries. E.g. with timescale 1ns, #5 prints as '5ns' not '5000ps';
    #17534700 prints as '17.5347us'.

    Defensive: non-finite ts or ts_sec produces '?', not 'infs' / 'nans'.
    """
    if ts == 0:
        return '0s'
    # math.isfinite handles int, float, bool. inf/nan slip through arithmetic
    # otherwise and produce garbage like 'infs'.
    try:
        if not (math.isfinite(ts) and math.isfinite(ts_sec)):
            return '?'
    except TypeError:
        return '?'
    if ts_sec <= 0:
        return '?'
    sec = ts * ts_sec
    for u in ('fs', 'ps', 'ns', 'us', 'ms', 's'):
        scaled = sec / _UNITS[u]
        if abs(scaled) < 1000 or u == 's':
            return '{:g}{}'.format(scaled, u)
    return '{:g}s'.format(sec)


# -- Value formatting --------------------------------------------------------

def fmt_val(value, info):
    """Format signal value per IEEE 1364-2005 18.2.2.

    info: dict with 'width' (required) and 'type' (optional, default 'wire').

    Real/realtime values (18.2.2) carry the simulator's %.16g rendering as
    their literal value string and have no bit width — declared width (often
    64) is purely cosmetic and must not trigger vector left-extension.
    Multi-bit vectors are left-extended per Table 18-1: MSB X/Z extends
    with X/Z, else 0. Events (var_type 'event' per 18.2.3.7) display as
    'triggered' since the dumped value is just a marker.
    """
    vtype = info.get('type', 'wire')
    if vtype == 'event':
        return 'triggered'
    if vtype in ('real', 'realtime'):
        return value
    width = info['width']
    # Malformed VCD may dump more 4-state bits than the declared width
    # (for example an over-long extended-VCD port state). Do not truncate
    # to the LSBs: that silently fabricates a plausible numeric value.
    # Show explicit unknowns instead.
    if _is_4state_bits(value) and len(value) > width:
        value = 'x' * width
    if width == 1:
        return value
    # Left-extend short vectors. Writer drops redundant MSB bits when they
    # match the extension char of MSB (Table 18-2).
    if len(value) < width:
        msb = value[0]
        pad = msb if msb in ('x', 'z') else '0'
        value = pad * (width - len(value)) + value
    if 'x' in value or 'z' in value:
        return 'b' + value
    try:
        d = int(value, 2)
        hw = max((width + 3) // 4, 1)
        return '{} (0x{})'.format(d, format(d, 'x').zfill(hw))
    except ValueError:
        return 'b' + value


def val_to_int(value):
    """Try converting to int, None on x/z or pathologically long values.

    int(s, 2) is O(n) for base-2 (PEP 678 does not apply to power-of-two
    bases) so the worst case after MAX_SIGNAL_WIDTH=65536 is sub-ms — but
    we cap anyway as defense in depth, in case a future code path lets
    an unbounded value reach here.
    """
    if 'x' in value or 'z' in value:
        return None
    if len(value) > MAX_SIGNAL_WIDTH:
        return None
    try:
        return int(value, 2) if len(value) > 1 else int(value)
    except ValueError:
        return None




def _clamp_overwide_logic_value(value, info):
    """Preserve clean 4-state state while rejecting malformed over-wide dumps.

    Legal VCD writers may omit redundant MSB bits; fmt_val() and condition
    matching already left-extend short values. A value longer than the
    declared width is malformed. Do not truncate it to the LSBs: that would
    turn corrupt input into a plausible-looking numeric value. Instead,
    degrade to all-x at the declared width so downstream dump/snapshot/search
    sees an explicit unknown.
    """
    vtype = info.get('type', 'wire')
    if vtype in ('real', 'realtime', 'event'):
        return value
    width = info.get('width')
    if width is None:
        return value
    if _is_4state_bits(value) and len(value) > width:
        return 'x' * width
    return value

def _normalize_filter_patterns(value):
    """Normalize and bound user-supplied substring/glob patterns.

    Plain text remains substring matching. Only '*' and '?' trigger glob
    matching; '[' is literal because VCD bus ranges like data[7:0] are
    common signal names. Pattern length and wildcard count are bounded
    to keep Python 3.9's fnmatch/regex translation from becoming a CPU
    DoS surface ('a*a*a*...b' style inputs can be slow in older Python).
    Consecutive '*' are collapsed (matches glob semantics, reduces backtracking).

    Used by:
    - argparse type= on --filter (raises argparse-friendly error)
    - VCDParser.match() applied to internally-stored keyword lists
    """
    if value is None:
        return None
    if isinstance(value, str):
        raw_patterns = value.split(',')
    elif isinstance(value, (list, tuple, set)):
        raw_patterns = value
    else:
        raise _FilterParseError(
            'filter patterns must be a string or a sequence of strings; got {}'.format(
                type(value).__name__))
    out = []
    for raw in raw_patterns:
        pat = str(raw).strip()
        if not pat:
            continue
        if len(pat) > MAX_FILTER_PATTERN_LEN:
            raise _FilterParseError(
                'filter pattern too long; max length is {}'.format(MAX_FILTER_PATTERN_LEN))
        pat = re.sub(r'\*+', '*', pat)  # collapse `**` → `*`
        if pat.count('*') + pat.count('?') > MAX_FILTER_WILDCARDS:
            raise _FilterParseError(
                'too many wildcard characters in filter pattern; max is {}'.format(
                    MAX_FILTER_WILDCARDS))
        out.append(pat)
    return out


def _glob_lite_regex(pattern):
    """Translate the tool's minimal glob syntax to a compiled regex.

    Only '*' and '?' are special. Everything else — notably '[' and ']' in
    VCD bus ranges such as data[7:0] — is matched literally. This deliberately
    avoids fnmatch's character-class syntax so documented filters like
    '*data[7:0]' match the literal signal path 'tb.data[7:0]'.

    Pattern length and wildcard count are already bounded by
    _normalize_filter_patterns(), so the generated regex is small and safe.
    """
    parts = ['^']
    for ch in pattern:
        if ch == '*':
            parts.append('.*')
        elif ch == '?':
            parts.append('.')
        else:
            parts.append(re.escape(ch))
    parts.append('$')
    return re.compile(''.join(parts))


# -- VCD Parser with bit-exploded signal reassembly -------------------------

# IEEE 1364-2005 declaration keywords that introduce a $<kw> ... $end section.
_DECL_KEYWORDS = {'$timescale', '$scope', '$upscope', '$var',
                  '$comment', '$date', '$version', '$enddefinitions'}

# Simulation keywords that wrap value_changes until $end. The keyword and $end
# are pure markers — the wrapped value_changes are parsed normally.
# Four-state VCD (18.2.3.9-12) + extended VCD (18.4.1 BNF).
_SIM_KEYWORDS = {'$dumpall', '$dumpoff', '$dumpon', '$dumpvars',
                 '$dumpports', '$dumpportsoff', '$dumpportson', '$dumpportsall'}

# Sections that can appear in the data area whose body is NOT value_changes
# and must be skipped wholesale until $end. $comment (18.2.3.1) is in both
# header and data; $vcdclose (18.3.6.1) wraps a final simulation time token.
_DATA_SKIP_SECTIONS = {'$comment', '$vcdclose'}


class VCDParser:
    """Streaming VCD parser. Token-based: handles single-line and multi-line
    sections, inline simulation keyword blocks, and multi-line port values
    per IEEE 1364-2005 Section 18.

    Auto-reassembles bit-exploded signals (QuestaSim writes 512-bit signals
    as 512 individual 1-bit $var entries with [N] suffix).

    Extended VCD ($dumpports) support level: port_state characters are
    lowered to 4-state values (0/1/x/z) for RTL debug. The strength0 and
    strength1 components are parsed but discarded — preserving them would
    rarely benefit RTL-level analysis and clutters the value display.
    """

    def __init__(self, path):
        self.path = path
        self.ts_str = ''
        self.ts_sec = 1e-12        # timescale in seconds
        self.signals = {}           # sig_id -> {path, width, type, aliases}
        self._data_offset = 0
        # Header metadata per IEEE 1364-2005 18.2.3:
        #   $date    - simulation date string (18.2.3.2)
        #   $version - simulator vendor/version (18.2.3.3)
        #   $comment - free-form, may appear multiple times (18.2.3.1)
        # Captured verbatim for provenance display; an agent inspecting an
        # unknown VCD benefits from knowing which simulator produced it
        # (QuestaSim 2023.1 vs Icarus Verilog vs VCS) and when, since
        # downstream debug heuristics may depend on simulator quirks.
        self.date = ''
        self.version = ''
        self.comments = []
        # If $enddefinitions $end is followed by data tokens on the same
        # line(s) buffered by readline, those tokens replay first in data.
        self._initial_tokens = []
        self._bit_map = {}          # sym -> (sig_id, bit_index)
        self._bit_state_template = {}  # sig_id -> initial bit list for replay-local reassembly
        self._parse_header()

    def _parse_header(self):
        """Token-based header parse. Sections may span multiple lines;
        $end is the only terminator (IEEE 1364-2005 18.2.1)."""
        scope = []
        raw_vars = []  # (sym, name, width, bit_idx_str, scope_path, vtype)
        current_kw = None
        body = []
        done = False

        with open(self.path, 'r', encoding='utf-8', errors='replace') as f:
            while not done:
                line = f.readline()
                if not line:
                    break
                for tok in line.split():
                    if done:
                        # Buffer tokens that share the same line as
                        # `$enddefinitions $end`. These are data tokens
                        # (value_changes, timestamps), so they MUST NOT
                        # be silently dropped — that would corrupt the
                        # waveform without the user noticing. Fail-fast.
                        # Normal VCDs have at most a handful of tokens
                        # on this line; 131072 is comfortably above any
                        # legitimate use.
                        if len(self._initial_tokens) >= MAX_INITIAL_TOKENS:
                            raise _VCDResourceError(
                                'too many data tokens on the same line as '
                                '$enddefinitions $end (>{}); file may be '
                                'corrupt or malicious'.format(MAX_INITIAL_TOKENS))
                        self._initial_tokens.append(tok)
                        continue
                    if current_kw is None:
                        if tok in _DECL_KEYWORDS:
                            current_kw = tok
                            body = []
                        # else: stray token, ignore
                    elif tok == '$end':
                        # Section complete
                        if current_kw == '$timescale':
                            ts_body = ' '.join(body)
                            self.ts_str = '$timescale ' + ts_body + ' $end'
                            self.ts_sec = _parse_timescale(ts_body)
                        elif current_kw == '$scope' and len(body) >= 2:
                            # Cap nesting depth to defend against
                            # 1M-level $scope-without-$upscope construction.
                            if len(scope) >= MAX_SCOPE_DEPTH:
                                raise _VCDResourceError(
                                    '$scope nesting depth exceeds {}; '
                                    'file may be corrupt or malicious'.format(MAX_SCOPE_DEPTH))
                            scope.append(body[1])
                        elif current_kw == '$upscope':
                            if scope:
                                scope.pop()
                        elif current_kw == '$var' and len(body) >= 4:
                            vtype = body[0]

                            def _collect_bracket(tokens, i):
                                if i >= len(tokens) or not tokens[i].startswith('['):
                                    return None, i
                                parts = []
                                while i < len(tokens):
                                    parts.append(tokens[i])
                                    if ']' in tokens[i]:
                                        return ''.join(parts), i + 1
                                    i += 1
                                return None, i

                            size_expr, idx_after_size = _collect_bracket(body, 1)
                            if size_expr is not None:
                                m = re.match(r'\[(\d+):(\d+)\]$', size_expr)
                                if not m:
                                    current_kw = None
                                    continue
                                msb = _safe_int_digits(m.group(1))
                                lsb = _safe_int_digits(m.group(2))
                                if msb is None or lsb is None:
                                    # Overlong or malformed digits — skip
                                    # this $var rather than abort, since
                                    # the rest of the header may still be
                                    # useful.
                                    current_kw = None
                                    continue
                                w = abs(msb - lsb) + 1
                                idx = idx_after_size
                            else:
                                w = _safe_int_digits(body[1])
                                if w is None:
                                    current_kw = None
                                    continue
                                idx = 2
                            # Hazard 1 mitigation: refuse pathological widths
                            # before they reach fmt_val (which would try to
                            # allocate `pad * (width - len(value))` bytes).
                            # Real signals never approach MAX_SIGNAL_WIDTH.
                            if w <= 0 or w > MAX_SIGNAL_WIDTH:
                                raise _VCDResourceError(
                                    '$var width {} exceeds max {}; '
                                    'file may be corrupt or malicious'.format(
                                        w, MAX_SIGNAL_WIDTH))
                            if len(body) <= idx + 1:
                                current_kw = None
                                continue
                            sym, name = body[idx], body[idx + 1]

                            # Per IEEE 1364 free-format, the bracket reference
                            # range can be split into several tokens, e.g.
                            # 'data [7 : 0]' → ['data', '[7', ':', '0]'].
                            bit_str, _idx_after_ref = _collect_bracket(body, idx + 2)
                            # Per IEEE 1364-2005 18.2.3.7 reference syntax:
                            #   identifier [bit_select_index]      → single bit
                            #   identifier [msb_index : lsb_index] → range
                            # For multi-bit refs with a range, fold it into
                            # the name so the displayed path is 'data[7:0]'.
                            # For w==1 with [N], keep bit_str separate for
                            # the bit-explosion heuristic below.
                            if bit_str is not None and w > 1:
                                name = name + bit_str
                                bit_str = None
                            # Resource cap: refuse to allocate unbounded memory
                            # for malicious VCDs declaring millions of $var.
                            # Default 500k is ~25x larger than typical QuestaSim
                            # files; tune via VCD_ANALYZER_MAX_VARS env var.
                            if len(raw_vars) >= MAX_VARS:
                                raise _VCDResourceError(
                                    'too many $var declarations: more than {}. '
                                    'Set VCD_ANALYZER_MAX_VARS to raise the limit.'.format(MAX_VARS))
                            raw_vars.append((sym, name, w, bit_str, '.'.join(scope), vtype))
                        elif current_kw == '$enddefinitions':
                            done = True
                        elif current_kw == '$date':
                            # Tokens collapsed to single-spaced string;
                            # original used \t / multi-line for readability.
                            self.date = ' '.join(body)
                        elif current_kw == '$version':
                            self.version = ' '.join(body)
                        elif current_kw == '$comment':
                            # Per 18.2.3.1, $comment may appear multiple
                            # times. Silent drop after the cap is safe:
                            # comments are metadata, not data — losing
                            # the 1025th comment only affects what
                            # `info --verbose` prints, never the waveform.
                            if len(self.comments) < MAX_COMMENTS:
                                self.comments.append(' '.join(body))
                        current_kw = None
                    else:
                        # Bound section body. In practice this only
                        # truncates oversized $comment / $date / $version
                        # bodies — metadata. $var bodies are 4-8 tokens,
                        # $scope is 2, $timescale is 2; none come close
                        # to the cap. Silent drop is safe because:
                        #   - the $end token still closes the section
                        #     correctly (we still see it in the outer
                        #     loop, we just stop appending to body)
                        #   - dropped tokens never become part of any
                        #     value_change interpretation
                        if len(body) < MAX_HEADER_BODY_TOKENS:
                            body.append(tok)
            self._data_offset = f.tell()

        # Phase 2: detect and reassemble bit-exploded signals.
        # Bit-exploded heuristic per QuestaSim convention: each bit is a
        # 1-bit $var with [N] suffix. We auto-reassemble ONLY when the bit
        # indices form a complete 0..max_bit contiguous set. Standard-legal
        # partial dumps (e.g. only $var ... bus[4] ... emitted) must NOT be
        # synthesized as a bus[4:0] with phantom lower bits — they are kept
        # as individual bit-select references.
        bit_groups = defaultdict(dict)  # (scope, base_name) -> {bit_idx: sym}
        bit_types = {}                   # (scope, base_name) -> vtype
        duplicate_bit_groups = set()      # groups with duplicate bit indices; never reassemble
        standalone = []
        bit_select_singletons = []       # (sym, name, idx, sc, vtype)

        for sym, name, w, bit_str, sc, vtype in raw_vars:
            if w == 1 and bit_str is not None:
                m = re.match(r'\[(\d+)\]', bit_str)
                if m:
                    idx = _safe_int_digits(m.group(1))
                    if idx is None:
                        # Overlong/malformed bit index — treat the $var as
                        # a standalone signal (its bit_str folded back).
                        standalone.append((sym, name + bit_str, 1, sc, vtype))
                        continue
                    group_key = (sc, name)
                    group = bit_groups[group_key]
                    if idx in group:
                        # Illegal VCD: duplicate bit-select declaration for the
                        # same reconstructed bus bit.  Do not silently let the
                        # later symbol overwrite the earlier one; mark the group
                        # non-reassemblable so all raw bit-select declarations
                        # remain visible as standalone signals.
                        duplicate_bit_groups.add(group_key)
                    else:
                        group[idx] = sym
                    # Resource cap: refuse to allocate gigantic synthesized
                    # buses (per-call template copy cost scales linearly).
                    # Default 65536 is 128× typical QuestaSim bit-bus size;
                    # tune via VCD_ANALYZER_MAX_REASSEMBLE_BITS env var.
                    if len(group) > MAX_REASSEMBLE_BITS:
                        raise _VCDResourceError(
                            'bit-exploded group {}.{} has more than {} bits. '
                            'Set VCD_ANALYZER_MAX_REASSEMBLE_BITS to raise the limit.'.format(
                                sc or '<root>', name, MAX_REASSEMBLE_BITS))
                    bit_types[(sc, name)] = vtype
                    bit_select_singletons.append((sym, name, idx, sc, vtype))
                    continue
                # A 1-bit reference written as a range (for example
                # data[0:0]) is not a bit-exploded bus bit. Preserve the
                # reference suffix in the displayed path instead of silently
                # dropping it. Some simulators emit this non-canonical form.
                standalone.append((sym, name + bit_str, 1, sc, vtype))
                continue
            standalone.append((sym, name, w, sc, vtype))

        # Partition bit_groups: contiguous-from-0 with ≥2 bits → reassemble;
        # everything else → individual bit-select references. A single
        # '[0]' declaration alone is NOT a bus — it's a partial dump that
        # happens to use bit 0; synthesizing it as 'data[0:0]' would lie
        # about the file structure.
        #
        # DoS guard: do NOT compute set(range(max+1)) — a malicious VCD with
        # 'bus[0]' + 'bus[1000000000]' would force materialization of a
        # billion-element set (gigabytes of RAM). Indices [0..max] form a
        # contiguous run iff: count == max+1 AND 0 is present. Both checks
        # are O(1) on dict_keys.
        non_contiguous = set(duplicate_bit_groups)
        for key, bits in bit_groups.items():
            if key in non_contiguous:
                continue
            indices = bits.keys()
            n = len(indices)
            if n < 2:
                non_contiguous.add(key)
                continue
            max_idx = max(indices)
            if max_idx + 1 != n or 0 not in indices:
                non_contiguous.add(key)

        # Each non-contiguous bit-select becomes a standalone 'name[idx]' signal
        for sym, name, idx, sc, vtype in bit_select_singletons:
            if (sc, name) in non_contiguous:
                standalone.append((sym, '{}[{}]'.format(name, idx), 1, sc, vtype))

        # Register standalone signals. Per IEEE 1364-2005 18.2.3.7, the same
        # identifier_code can be referenced under multiple paths. First seen
        # type wins when aliases have different var_types.
        for sym, name, w, sc, vtype in standalone:
            path = '{}.{}'.format(sc, name) if sc else name
            if sym in self.signals:
                self.signals[sym]['aliases'].append(path)
                if sc and sc not in self.signals[sym].setdefault('scopes', []):
                    self.signals[sym]['scopes'].append(sc)
            else:
                self.signals[sym] = {
                    'path': path, 'width': w, 'type': vtype,
                    'aliases': [path], 'scope': sc, 'scopes': [sc] if sc else []
                }

        for (sc, name), bits in bit_groups.items():
            if not bits or (sc, name) in non_contiguous:
                continue
            max_bit = max(bits.keys())
            width = max_bit + 1
            path = '{}.{}[{}:0]'.format(sc, name, max_bit) if sc else '{}[{}:0]'.format(name, max_bit)
            sig_id = '__grp__{}__{}'.format(sc, name)
            self.signals[sig_id] = {
                'path': path, 'width': width,
                'type': bit_types.get((sc, name), 'wire'),
                'aliases': [path], 'scope': sc, 'scopes': [sc] if sc else [],
                'synthesized': True,    # bit-exploded reassembled bus
                'raw_bits': len(bits),  # number of $var declarations consumed
            }
            self._bit_state_template[sig_id] = ['x'] * width
            # Per IEEE 1364-2005 18.2.3.7, the same identifier_code can be
            # referenced under multiple paths. When two bit-exploded buses
            # share per-bit identifier codes (e.g. bus[0]/aliasbus[0] both
            # use '!'), each is a separate synthesized signal that must
            # update independently. _bit_map is therefore 1-to-many.
            for idx, sym in bits.items():
                self._bit_map.setdefault(sym, []).append((sig_id, idx))

        # Raw $var counts (transparent to IEEE 1364 spec) so 'info' can
        # report accurate metadata even when reassembly collapses many
        # declarations into a single synthesized bus. Distinct from
        # `signal_count` (post-reassembly view used by agent commands).
        self.raw_var_count = len(raw_vars)
        self.raw_type_counts = defaultdict(int)
        for _sym, _name, _w, _bit_str, _sc, vtype in raw_vars:
            self.raw_type_counts[vtype] += 1

    def match(self, keywords):
        """Return set of sig_ids matching any pattern, or None for all.

        Plain patterns use case-insensitive substring matching. Patterns
        containing '*' or '?' use the tool's minimal glob-lite matching:
        '*' matches any span, '?' matches one character, and all other
        characters are literal. This intentionally differs from fnmatch:
        '[' and ']' are NOT character-class delimiters because VCD bus ranges
        like data[7:0] are common signal names.

        Input is normalized through _normalize_filter_patterns to bound
        pattern length and wildcard count.
        """
        if not keywords:
            return None
        raw_pats = [k.lower() for k in _normalize_filter_patterns(keywords) or []]
        if not raw_pats:
            return None
        pats = []
        for pat in raw_pats:
            if any(ch in pat for ch in '*?'):
                pats.append(('glob', _glob_lite_regex(pat)))
            else:
                pats.append(('substr', pat))
        out = set()
        for sid, info in self.signals.items():
            for path in info['aliases']:
                pl = path.lower()
                hit = False
                for kind, pat in pats:
                    hit = pat.match(pl) is not None if kind == 'glob' else pat in pl
                    if hit:
                        out.add(sid)
                        break
                if hit:
                    break
        return out

    def _data_tokens(self):
        """Generator yielding all tokens from the data section."""
        for t in self._initial_tokens:
            yield t
        with open(self.path, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(self._data_offset)
            for line in f:
                for t in line.split():
                    yield t

    def _is_structural_token(self, tok):
        """Return True when tok is structural rather than an identifier_code.

        Only #<digits> has positional ambiguity: it can be a timestamp at
        top level, or a legal identifier_code after b/r/p. If such a token is
        declared as a normal signal or bit-exploded bit, it is the symbol;
        otherwise it is structural and must be pushed back so the outer loop
        can process it as a timestamp.
        """
        if tok is None:
            return True
        if tok.startswith('#') and len(tok) > 1 and tok[1].isdigit():
            return tok not in self.signals and tok not in self._bit_map
        return False

    def _consume_value_change(self, tok, next_token, pushback):
        """Parse one VCD value_change token sequence.

        Returns (identifier_code, value_str) on a valid value_change, or None
        when tok is malformed / not a value_change. This is the single shared
        validation path used by iter_events() and scan_time_range(), so info's
        reported time range stays aligned with dump/search parsing behavior.

        next_token is a zero-arg function over the same pushback-capable token
        stream as the caller. If a token consumed while validating b/r/p turns
        out to be structural, it is pushed back in the same order used by the
        old local parsers.
        """
        if not tok:
            return None
        first = tok[0]

        if first in '01xXzZ':
            sym = tok[1:]
            if not sym:
                return None
            return sym, first.lower()

        if first in 'bB':
            bits = tok[1:]
            if not bits or any(c not in '01xXzZ' for c in bits):
                return None
            sym = next_token()
            if self._is_structural_token(sym):
                if sym is not None:
                    pushback.append(sym)
                return None
            return sym, bits.lower()

        if first in 'rR':
            body = tok[1:]
            if len(body) > _REAL_MAX_LEN or not _REAL_RE.match(body):
                return None
            sym = next_token()
            if self._is_structural_token(sym):
                if sym is not None:
                    pushback.append(sym)
                return None
            return sym, body

        if first == 'p':
            # Extended VCD (18.4.3.1): p<state> <s0> <s1> <id>.
            # Keep this validation in one place so malformed port events are
            # treated identically by iter_events() and scan_time_range().
            state = tok[1:] if len(tok) > 1 else ''
            if not state or any(c not in _PORT_STATE for c in state):
                return None

            s0 = next_token()
            if s0 is None or len(s0) != 1 or s0 not in '01234567':
                if s0 is not None:
                    pushback.append(s0)
                return None

            s1 = next_token()
            if s1 is None or len(s1) != 1 or s1 not in '01234567':
                if s1 is not None:
                    pushback.append(s1)
                pushback.append(s0)
                return None

            sym = next_token()
            if self._is_structural_token(sym):
                if sym is not None:
                    pushback.append(sym)
                pushback.append(s1)
                pushback.append(s0)
                return None
            return sym, ''.join(_PORT_STATE[c] for c in state)

        return None

    def iter_events(self, t0=0, t1=None, sids=None):
        """Yield (time, sig_id, value_str) with bit reassembly.

        Token-based, context-sensitive. Section keywords ($comment/$vcdclose/
        $dumpvars/$dumpoff/$dumpon/$dumpall/$dumpports*) are only recognized
        when the parser is at a top-level position (expecting either a
        timestamp or a value_change opener). After 'b<bits>', 'r<num>', or
        'p<state> <s0> <s1>' the NEXT token is consumed as identifier_code
        even if it happens to be the string '$comment' (legal per
        IEEE 1364-2005 18.2.1: identifier_code is any printable ASCII).

        Initial value changes appearing before any '#T' timestamp are
        emitted at logical t=0 (typical case: $dumpvars block directly
        after $enddefinitions without a leading #0).
        """
        cur_t = 0
        pending = {}

        def _flush():
            if not pending:
                return []
            items = list(pending.items())
            pending.clear()
            return items

        # Pushback-capable token stream. Lets us peek the next token in
        # b/r value_change branches and refuse it if it looks structural
        # (timestamp or section keyword) — otherwise malformed inputs
        # like 'b1010\n#10\n1!' would silently consume #10 as the
        # identifier_code and corrupt the timeline.
        raw = self._data_tokens()
        pushback = []
        # Replay-local bit state. iter_events() must be pure with respect
        # to parser metadata: compare/search/summary/snapshot may replay
        # the same VCDParser multiple times and in non-monotonic order.
        # Object-level mutable state would leak future bit values into
        # earlier snapshots for bit-exploded buses.
        #
        # Laziness: when the caller selected a subset of signals (sids),
        # maintain only the synthesized bit-buses that can be emitted for
        # this query. This avoids touching large unrelated bit-exploded
        # buses during catch-up scans, while preserving exact behavior for
        # selected buses and for no-filter calls.
        if sids is None:
            bit_map = self._bit_map
            bit_state = {gid: bits[:] for gid, bits in self._bit_state_template.items()}
        else:
            bit_map = {}
            needed_gids = set()
            for sym0, refs in self._bit_map.items():
                kept = [(gid, idx) for gid, idx in refs if gid in sids]
                if kept:
                    bit_map[sym0] = kept
                    for gid, _idx in kept:
                        needed_gids.add(gid)
            bit_state = {gid: self._bit_state_template[gid][:] for gid in needed_gids}

        def _next():
            return pushback.pop() if pushback else next(raw, None)

        try:
            while True:
                tok = _next()
                if tok is None:
                    break
                # Top-level: any unknown $keyword starts a section ending at
                # $end. This is safer than passing the body through as value
                # changes — '$bogus 1! $end' must not pollute the waveform.
                # Known wrappers ($dumpvars etc) are pass-through (their body
                # IS value_changes per 18.2.3.9-12).
                if tok == '$end':
                    continue
                if tok in _SIM_KEYWORDS:
                    continue
                if tok.startswith('$'):
                    # $comment, $vcdclose, $bogus, ...: drop body to $end
                    for t in raw:
                        if t == '$end':
                            break
                    continue
    
                if tok.startswith('#') and len(tok) > 1 and tok[1].isdigit():
                    new_t = _parse_vcd_timestamp_token(tok)
                    if new_t is None:
                        # Malformed (e.g. '#1.5'); silently skip per round-7 policy.
                        continue
                    if cur_t >= t0:
                        for sid, val in _flush():
                            yield cur_t, sid, val
                    cur_t = new_t
                    if t1 is not None and cur_t > t1:
                        return
                    continue
    
                # Shared value_change parser. Keeping b/r/p validation in one
                # helper prevents scan_time_range() and iter_events() from
                # drifting apart when malformed-token rules are adjusted.
                parsed = self._consume_value_change(tok, _next, pushback)
                if parsed is None:
                    continue
                sym, val = parsed
    
                # Catch-up before t0: update bit_state only, don't emit.
                # Standalone state is owned by callers (e.g. _build_snapshot
                # accumulates it from yielded events), so nothing to do here
                # for the standalone case — the continue is correct.
                if cur_t < t0:
                    if sym in bit_map:
                        bit_val = val if _is_4state_bits(val) and len(val) == 1 else 'x'
                        for gid, idx in bit_map[sym]:
                            bit_state[gid][idx] = bit_val
                    continue
    
                # Bit-exploded signal: aggregate into virtual bus value(s).
                # If the same identifier_code drives multiple synthesized buses
                # (via aliased parent declarations), each gets its own event.
                #
                # IMPORTANT: do NOT continue after this branch. Per IEEE 1364-2005
                # 18.2.3.7, the same identifier_code can be referenced by both a
                # standalone $var (e.g. clk) AND a bit-select $var (e.g.
                # data_bus[0]) when RTL assigns one to the other. If we continued,
                # the standalone alias would silently never emit events and the
                # agent would see clk as a flat line. Fall through to the
                # standalone block so both signals update on the same value_change.
                if sym in bit_map:
                    bit_val = val if _is_4state_bits(val) and len(val) == 1 else 'x'
                    for gid, idx in bit_map[sym]:
                        bit_state[gid][idx] = bit_val
                        if sids is None or gid in sids:
                            pending[gid] = ''.join(reversed(bit_state[gid]))
    
                # Standalone signal (may run after the bit-bus branch above when
                # the sym serves both roles).
                if sym not in self.signals:
                    continue
                if sids is not None and sym not in sids:
                    continue
                pending[sym] = _clamp_overwide_logic_value(val, self.signals[sym])
    
            # Final flush
            if cur_t >= t0:
                for sid, val in _flush():
                    yield cur_t, sid, val
        finally:
            close = getattr(raw, 'close', None)
            if close is not None:
                close()

    def scan_time_range(self):
        """Min/max timestamps in the file.

        If any value_change occurs before the first #T (an initial $dumpvars
        block), t_min is 0. Time is observed-max (never less than the largest
        seen), so malformed VCDs with timestamps going backwards do not produce
        negative duration. Value-change body validation uses the same shared token consumer as
        iter_events(), so info/dump agree on malformed b/r/p bodies.

        The underlying token generator owns an open file. Close it explicitly
        on all paths instead of relying on garbage collection if a resource
        error is raised while scanning a corrupt file.
        """
        t_min = t_max = None
        saw_initial_data = False
        raw = self._data_tokens()
        pushback = []

        def _next():
            return pushback.pop() if pushback else next(raw, None)

        try:
            while True:
                tok = _next()
                if tok is None:
                    break
                if tok == '$end' or tok in _SIM_KEYWORDS:
                    continue
                if tok.startswith('$'):
                    for t in raw:
                        if t == '$end':
                            break
                    continue
                if tok.startswith('#') and len(tok) > 1 and tok[1].isdigit():
                    t = _parse_vcd_timestamp_token(tok)
                    if t is None:
                        continue
                    if t_min is None:
                        t_min = 0 if saw_initial_data else t
                    t_max = t if t_max is None else max(t_max, t)
                    continue

                # Shared value_change validation. We do not need the
                # parsed sym/value here; the goal is only to know whether a
                # legitimate value_change appears before the first timestamp.
                if self._consume_value_change(tok, _next, pushback) is not None:
                    if t_min is None:
                        saw_initial_data = True
        finally:
            close = getattr(raw, 'close', None)
            if close is not None:
                close()

        if t_min is None and saw_initial_data:
            t_min = t_max = 0
        return t_min, t_max



# -- Subcommands -------------------------------------------------------------

_DEFAULT_LIMIT = 200


def _json(obj):
    """Compact JSON for agent use."""
    print(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))


def _limit(args, cmd):
    """Resolve global output limit. --verbose disables truncation unless an
    explicit --limit was supplied. --limit 0 always means unlimited."""
    val = getattr(args, 'limit', None)
    if val is None:
        return 0 if getattr(args, 'verbose', False) else _DEFAULT_LIMIT
    if val < 0:
        raise _TimeParseError('limit must be non-negative; got {}'.format(val))
    return val


def _clip(seq, limit):
    if limit == 0:
        return seq, False
    return seq[:limit], len(seq) > limit


def _trunc_line(shown, total, noun):
    return '... truncated: {}/{} {} shown.'.format(shown, total, noun)


def _trunc_line_lower_bound(shown, total, noun):
    """Truncation line when scanning stopped at the first unshown result.

    Used by streaming commands where --limit is an execution bound, not just
    an output bound. `total` is a lower bound (normally shown + 1),
    not the exact global result count.
    """
    return '... truncated: {}/{}+ {} shown.'.format(shown, total, noun)


def _total_json_fields(total, truncated):
    """Return JSON count fields for exact vs early-stopped result sets.

    When truncated is true, total is only a lower bound (usually limit+1).
    Keeping it numeric is convenient for agents, while total_is_exact prevents
    consumers from treating it as the real global count.
    """
    return {'total': total, 'total_is_exact': not truncated}


def _count_label(shown, total, truncated):
    """Human count label for result headers."""
    return '{}+'.format(total) if truncated else str(total)


def _selected_sids(vcd, sids):
    """Return an explicit set of selected signal ids."""
    return set(vcd.signals.keys()) if sids is None else set(sids)


def _fmt_maybe(value, info):
    return fmt_val(value, info) if value is not None else '(undef)'


def _time_pair(prefix, t, ts):
    """Return both integer ticks and human-readable time for JSON outputs."""
    return {prefix + '_ticks': t, prefix + '_h': fmt_time(t, ts) if t is not None else None}


def _build_snapshot(vcd, t_at, sids=None):
    """Replay from start through t_at, return known {sig_id: value} only."""
    state = {}
    for _t, sid, val in vcd.iter_events(0, t_at, sids):
        state[sid] = val
    return state


def _build_snapshot_before(vcd, t_at, sids=None):
    """Replay from start up to, but excluding, t_at.

    Used by search --changed. A value_change exactly at --begin must remain
    observable as a transition. Because VCD timestamps are integer ticks, the
    exclusive snapshot is simply the inclusive snapshot at t_at - 1. At t=0
    there is no prior state; initialization is handled explicitly by the
    changed-mode loop and is not reported as a real change.
    """
    if t_at <= 0:
        return {}
    return _build_snapshot(vcd, t_at - 1, sids)


def _parse_target_value(text):
    """Parse search/condition target once with bounded cost.

    Returns (target_raw, target_int):

      - Numeric targets (decimal, 0x..., 0b..., b...) get target_int and are
        matched only by numeric equality.
      - 4-state binary literals with x/z keep a raw bit-string target. Explicit
        binary prefixes are stripped because VCD stores vector values as
        ``1x0`` internally, not ``b1x0``.

    Invalid hex and negative decimal targets are rejected rather than silently
    producing no matches; VCD value_change text is unsigned, and x/z literals
    should be written in binary form (e.g. b1x0z).
    """
    if text is None:
        raise _ValueParseError('target value must not be empty')
    raw = str(text).lower().strip()
    if not raw:
        raise _ValueParseError('target value must not be empty')
    if len(raw) > MAX_VALUE_ARG_LEN:
        raise _ValueParseError(
            'target value too long; max length is {}'.format(MAX_VALUE_ARG_LEN))

    if raw.startswith('-'):
        raise _ValueParseError(
            'negative target values are not supported for VCD signal matching')

    if raw.startswith('0x'):
        body = raw[2:]
        if not body:
            raise _ValueParseError('hex target must contain at least one digit')
        if len(body) > MAX_HEX_VALUE_DIGITS:
            raise _ValueParseError(
                'hex target too wide; max hex digits is {}'.format(MAX_HEX_VALUE_DIGITS))
        try:
            return raw, int(raw, 16)
        except ValueError:
            raise _ValueParseError(
                'invalid hex target {!r}; x/z literals must use binary form like b1x0z'.format(text))

    if raw.startswith('0b'):
        body = raw[2:]
        if not body:
            raise _ValueParseError('binary target must contain at least one bit')
        if len(body) > MAX_SIGNAL_WIDTH:
            raise _ValueParseError(
                'binary target too wide; max bits is {}'.format(MAX_SIGNAL_WIDTH))
        try:
            return body, int(body, 2)
        except ValueError:
            if all(c in '01xz' for c in body):
                return body, None
            raise _ValueParseError(
                'invalid binary target {!r}; expected only 0/1/x/z'.format(text))

    if raw.startswith('b'):
        body = raw[1:]
        if not body:
            raise _ValueParseError('binary target must contain at least one bit')
        if len(body) > MAX_SIGNAL_WIDTH:
            raise _ValueParseError(
                'binary target too wide; max bits is {}'.format(MAX_SIGNAL_WIDTH))
        try:
            return body, int(body, 2)
        except ValueError:
            if all(c in '01xz' for c in body):
                return body, None
            raise _ValueParseError(
                'invalid binary target {!r}; expected only 0/1/x/z'.format(text))

    # Bare target: decimal numeric if possible, otherwise literal 4-state
    # string (e.g. ``1x0``). Cap pure decimal digit count before int().
    if raw.startswith('+'):
        raise _ValueParseError(
            'signed target values are not supported; write unsigned values')
    if raw.isdigit() and len(raw) > MAX_DECIMAL_VALUE_DIGITS:
        raise _ValueParseError(
            'decimal target too long; max digits is {}'.format(MAX_DECIMAL_VALUE_DIGITS))
    try:
        return raw, int(raw)
    except ValueError:
        if len(raw) > MAX_SIGNAL_WIDTH:
            raise _ValueParseError(
                'literal target too wide; max characters is {}'.format(MAX_SIGNAL_WIDTH))
        return raw, None


def _is_4state_bits(text):
    return text is not None and text != '' and all(c in '01xz' for c in text)


def _left_extend_bits(bits, width):
    """Apply VCD vector left-extension to a 4-state bit string.

    When a dumped vector is shorter than its declared width, IEEE VCD
    semantics extend the MSB leftward: x extends with x, z with z, and
    0/1 with 0. Use the same rule for user 4-state targets so a condition
    such as data=b1x0 can match an 8-bit stored value 000001x0 without
    asking the Agent to spell out every leading zero.
    """
    if width is None or len(bits) >= width:
        return bits
    msb = bits[0]
    pad = msb if msb in ('x', 'z') else '0'
    return pad * (width - len(bits)) + bits


def _value_matches(value, target_raw, target_int, width=None):
    """Match a recorded value against a parsed search target.

    Numeric targets (decimal/hex/binary without x/z) match only by numeric
    equality, avoiding the decimal/binary collision where target 10 would
    otherwise raw-match a 2-bit value "10".

    Non-numeric 4-state targets (for example b1x0 -> raw "1x0") match as
    bit patterns. If the signal width is known, both the dumped value and the
    target are left-extended to that width using VCD rules before comparison.
    This preserves exact x/z semantics while avoiding the need to write every
    leading zero for wide buses. Non-bit-string literals fall back to exact
    string equality.
    """
    if target_int is not None:
        iv = val_to_int(value)
        return iv is not None and iv == target_int
    if width is not None and _is_4state_bits(value) and _is_4state_bits(target_raw):
        if len(target_raw) > width:
            return False
        return _left_extend_bits(value, width) == _left_extend_bits(target_raw, width)
    return value == target_raw


_COND_RE = re.compile(r'^\s*(.+?)\s*(==|=|!=)\s*(.+?)\s*$')


def _has_unknown(value):
    """True when a VCD value is unknown/ambiguous for negative predicates."""
    return value is None or 'x' in value or 'z' in value


def _condition_match(value, op, target_raw, target_int, width=None):
    """Evaluate one resolved condition against a raw VCD value.

    Equality reuses the existing two-mode value matcher, so numeric targets
    are compared numerically and mixed x/z literals are compared as 4-state
    bit patterns, width-aware when the signal width is available.

    Inequality is deliberately stricter than `not _value_matches(...)`:
    x/z/undef do NOT satisfy `!=`. In RTL debug, unknown is not evidence that
    a signal is definitely different from a value. Users who want unknowns
    should ask for them explicitly, e.g. `valid=x`.
    """
    if value is None:
        return False
    if op in ('=', '=='):
        return _value_matches(value, target_raw, target_int, width)
    if op == '!=':
        if _has_unknown(value):
            return False
        return not _value_matches(value, target_raw, target_int, width)
    raise AssertionError('unsupported condition operator {}'.format(op))


def _parse_conditions(text):
    """Parse comma-separated AND conditions into unresolved condition dicts."""
    if text is None or not str(text).strip():
        raise _ConditionParseError('search requires --condition')
    conditions = []
    for item in str(text).split(','):
        item = item.strip()
        if not item:
            continue
        m = _COND_RE.match(item)
        if not m:
            raise _ConditionParseError(
                'invalid condition {!r}; expected SIG=VAL, SIG==VAL, or SIG!=VAL'.format(item))
        sig_pat = m.group(1).strip()
        op = m.group(2)
        val_text = m.group(3).strip()
        if not sig_pat or not val_text:
            raise _ConditionParseError(
                'invalid empty signal/value in condition {!r}'.format(item))
        target_raw, target_int = _parse_target_value(val_text)
        conditions.append({
            'pattern': sig_pat,
            'op': op,
            'target_raw': target_raw,
            'target_int': target_int,
            'original': item,
            'value_text': val_text,
        })
    if not conditions:
        raise _ConditionParseError('search requires at least one condition')
    return conditions


def _resolve_one_signal(vcd, pattern, role):
    """Resolve a condition/trigger pattern to exactly one signal id.

    Matching normally follows VCDParser.match(): substring unless '*' or '?'
    is present. For condition/trigger positions, however, an exact full path
    should win over substring matches. Otherwise a precise path like
    'tb.u.rd_valid' would be rejected merely because 'tb.u.rd_valid0' exists.
    """
    pat = str(pattern).strip()
    pl = pat.lower()
    exact = set()
    if '*' not in pat and '?' not in pat:
        for sid, info in vcd.signals.items():
            for path in info['aliases']:
                if path.lower() == pl:
                    exact.add(sid)
        if len(exact) == 1:
            return next(iter(exact))
        if len(exact) > 1:
            examples = [vcd.signals[s]['path']
                        for s in sorted(exact, key=lambda sid: vcd.signals[sid]['path'])[:5]]
            raise _ConditionParseError(
                '{} pattern {!r} exactly matches {} signals; use list to choose a more specific name, examples: {}'.format(
                    role, pattern, len(exact), ', '.join(examples)))

    sids = vcd.match([pattern])
    if not sids:
        raise _ConditionParseError('{} pattern {!r} matches no signals'.format(role, pattern))
    if len(sids) != 1:
        examples = [vcd.signals[s]['path']
                    for s in sorted(sids, key=lambda sid: vcd.signals[sid]['path'])[:5]]
        extra = ', examples: {}'.format(', '.join(examples)) if examples else ''
        raise _ConditionParseError(
            '{} pattern {!r} matches {} signals; use list to choose a more specific name{}'.format(
                role, pattern, len(sids), extra))
    return next(iter(sids))


def _resolve_conditions(vcd, text):
    """Parse and resolve condition signal patterns to signal ids."""
    resolved = []
    seen = set()
    for c in _parse_conditions(text):
        sid = _resolve_one_signal(vcd, c['pattern'], 'condition signal')
        key = (sid, c['op'], c['target_raw'], c['target_int'])
        if key in seen:
            continue
        seen.add(key)
        c = dict(c)
        c['sid'] = sid
        c['path'] = vcd.signals[sid]['path']
        c['width'] = vcd.signals[sid]['width']
        resolved.append(c)
    return resolved


def _resolve_show_sids(vcd, show_patterns):
    """Resolve --show patterns to one or more signal ids.

    Show positions are allowed to match multiple signals, but an exact full
    path still wins over substring matching for that specific pattern. This
    keeps `--show tb.data` from unexpectedly also selecting `tb.data_out`;
    users who want broad matching can still write `--show data` or use glob
    patterns such as `--show "*data*"`.
    """
    if not show_patterns:
        return []
    # Normalize even for list inputs.  argparse already does this for CLI
    # strings, but repeating the bounded, idempotent normalization keeps the
    # helper safe for programmatic callers as well.
    pats = _normalize_filter_patterns(show_patterns)
    if not pats:
        return []

    selected = set()
    missing = []
    for pat in pats:
        pat_text = str(pat).strip()
        exact = set()
        if '*' not in pat_text and '?' not in pat_text:
            pl = pat_text.lower()
            for sid, info in vcd.signals.items():
                for path in info['aliases']:
                    if path.lower() == pl:
                        exact.add(sid)
            if exact:
                selected.update(exact)
                continue

        matched = vcd.match([pat_text])
        if matched:
            selected.update(matched)
        else:
            missing.append(pat_text)

    if missing:
        raise _ConditionParseError(
            '--show matches no signals: {}'.format(', '.join(missing)))
    if not selected:
        raise _ConditionParseError('--show matches no signals')
    return sorted(selected, key=lambda sid: vcd.signals[sid]['path'])


def _conditions_hold(state, conditions):
    for c in conditions:
        if not _condition_match(
                state.get(c['sid']), c['op'], c['target_raw'],
                c['target_int'], c.get('width')):
            return False
    return True


def _condition_label(conditions):
    return ','.join(c['original'] for c in conditions)


def _condition_result_text(conditions):
    return ','.join('{}{}{}'.format(c['path'], c['op'], c['value_text']) for c in conditions)


def _show_values(vcd, state, show_sids, verbose=False):
    """Return (values, meta) for show signals in current state.

    The return shape is intentionally stable regardless of verbose. meta is
    None unless verbose=True. This avoids type-dependent unpacking in search.
    """
    values = {}
    meta = {} if verbose else None
    for sid in show_sids:
        info = vcd.signals[sid]
        path = info['path']
        raw = state.get(sid)
        values[path] = fmt_val(raw, info) if raw is not None else '(undef)'
        if verbose:
            meta[path] = {'raw': raw, 'width': info['width'], 'type': info.get('type', 'wire')}
    return values, meta


def _values_text(values):
    return ' '.join('{}={}'.format(k, v) for k, v in values.items())


def _search_end_time(vcd, t0, t1):
    if t1 is not None:
        return t1
    _mn, mx = vcd.scan_time_range()
    if mx is None:
        raise _ConditionParseError(
            'search cannot evaluate condition: VCD data section contains no value changes')
    return mx


def _event_groups(vcd, t0, t1, sids):
    """Yield (time, [(sid, val), ...]) groups in time order."""
    cur_t = None
    group = []
    for t, sid, val in vcd.iter_events(t0, t1, sids):
        if cur_t is None:
            cur_t = t
        if t != cur_t:
            yield cur_t, group
            cur_t, group = t, []
        group.append((sid, val))
    if cur_t is not None:
        yield cur_t, group


def _summary_rows(vcd, t0, t1, sids):
    """Return (rows, counts) for window summary.

    Static means known at t0 and no value changes after t0 inside the window.
    Undefined means selected but not known at t0 and no value changes inside
    the window. No unknown values are invented.

    For 1-bit signals, rise/fall counts are reported for clean 0->1 and 1->0
    transitions only. x/z-related transitions still count as changes, but not
    as rises/falls.
    """
    selected = _selected_sids(vcd, sids)
    initial = _build_snapshot(vcd, t0, selected)
    stats = {}
    for sid, val in initial.items():
        info = vcd.signals[sid]
        stats[sid] = {
            'changes': 0, 'first_at': None, 'last_at': None,
            'initial': val, 'last': val, 'unique': {val},
            'prev': val, 'rise_count': 0 if info['width'] == 1 else None,
            'fall_count': 0 if info['width'] == 1 else None,
        }
    for t, group in _event_groups(vcd, t0, t1, selected):
        if t <= t0:
            continue
        for sid, val in group:
            info = vcd.signals[sid]
            is_scalar = info['width'] == 1
            if sid not in stats:
                stats[sid] = {
                    'changes': 0, 'first_at': None, 'last_at': None,
                    'initial': None, 'last': None, 'unique': set(),
                    'prev': None, 'rise_count': 0 if is_scalar else None,
                    'fall_count': 0 if is_scalar else None,
                }
            s = stats[sid]
            prev = s.get('prev')
            if is_scalar:
                if prev == '0' and val == '1':
                    s['rise_count'] += 1
                elif prev == '1' and val == '0':
                    s['fall_count'] += 1
            s['changes'] += 1
            if s['first_at'] is None:
                s['first_at'] = t
            s['last_at'] = t
            s['last'] = val
            s['prev'] = val
            s['unique'].add(val)

    rows = []
    for sid in sorted(stats, key=lambda x: vcd.signals[x]['path']):
        info = vcd.signals[sid]
        s = stats[sid]
        kind = 'active' if s['changes'] else 'static'
        row = {
            'kind': kind,
            'path': info['path'],
            'value': fmt_val(s['last'], info) if kind == 'static' else None,
            'changes': s['changes'],
            'rise_count': s['rise_count'],
            'fall_count': s['fall_count'],
            'init': _fmt_maybe(s['initial'], info),
            'last': _fmt_maybe(s['last'], info),
        }
        if s['first_at'] is not None:
            row['first_at_ticks'] = s['first_at']
            row['first_at'] = fmt_time(s['first_at'], vcd.ts_sec)
            row['first_at_h'] = row['first_at']
            row['last_at_ticks'] = s['last_at']
            row['last_at'] = fmt_time(s['last_at'], vcd.ts_sec)
            row['last_at_h'] = row['last_at']
        if s['unique']:
            row['unique'] = len(s['unique'])
        row['_width'] = info['width']
        row['_type'] = info.get('type', 'wire')
        rows.append(row)

    undefined = sorted(selected - set(stats), key=lambda x: vcd.signals[x]['path'])
    counts = {
        'selected': len(selected), 'defined': len(stats), 'undefined': len(undefined),
        'active': sum(1 for r in rows if r['kind'] == 'active'),
        'static': sum(1 for r in rows if r['kind'] == 'static'),
    }
    return rows, undefined, counts

def _public_row(row, verbose=False):
    r = dict(row)
    width = r.pop('_width', None)
    typ = r.pop('_type', None)
    if verbose:
        r['width'] = width
        r['type'] = typ
    return r


def cmd_info(vcd, args):
    t_min, t_max = vcd.scan_time_range()
    ts = vcd.ts_sec
    synth = [s for s in vcd.signals.values() if s.get('synthesized')]
    r = {
        'file': vcd.path,
        'size_bytes': os.path.getsize(vcd.path),
        'timescale': vcd.ts_str.replace('$timescale', '').replace('$end', '').strip(),
        # Provenance metadata from VCD header (IEEE 1364-2005 18.2.3.1-3).
        # Tells the agent which simulator produced the file and when, so
        # downstream debug can apply tool-specific heuristics (e.g. QuestaSim
        # bit-explodes wide buses but iverilog doesn't).
        'date': vcd.date,
        'version': vcd.version,
        'comments': list(vcd.comments),
        'signal_count': len(vcd.signals),
        'reference_count': vcd.raw_var_count,
        'synthesized_buses': len(synth),
        'var_types': dict(sorted(vcd.raw_type_counts.items(), key=lambda x: -x[1])),
        'time_min': fmt_time(t_min, ts) if t_min is not None else None,
        'time_min_ticks': t_min,
        'time_min_h': fmt_time(t_min, ts) if t_min is not None else None,
        'time_max': fmt_time(t_max, ts) if t_max is not None else None,
        'time_max_ticks': t_max,
        'time_max_h': fmt_time(t_max, ts) if t_max is not None else None,
        'duration': fmt_time(t_max - t_min, ts) if t_min is not None and t_max is not None else None,
        'duration_ticks': (t_max - t_min) if t_min is not None and t_max is not None else None,
        'duration_h': fmt_time(t_max - t_min, ts) if t_min is not None and t_max is not None else None,
        # Use declaration-time scope metadata instead of splitting public
        # paths on '.'. Escaped identifiers may legally contain dots;
        # path.split('.') would invent fake hierarchy such as tb.\foo.
        'scopes': sorted(set(
            sc for v in vcd.signals.values() for sc in v.get('scopes', []) if sc
        )),
    }
    if args.json:
        _json(r)
    else:
        print('File      : {}'.format(r['file']))
        print('Size      : {:,} bytes'.format(r['size_bytes']))
        if r['date']:
            print('Date      : {}'.format(r['date']))
        if r['version']:
            print('Tool      : {}'.format(r['version']))
        print('Timescale : {}'.format(r['timescale']))
        if r['signal_count'] == r['reference_count']:
            print('Signals   : {}'.format(r['signal_count']))
        elif r['synthesized_buses']:
            print('Signals   : {} ({} $var decls, {} reassembled as bit-buses)'.format(
                r['signal_count'], r['reference_count'], r['synthesized_buses']))
        else:
            print('Signals   : {} unique ({} $var refs via aliases)'.format(
                r['signal_count'], r['reference_count']))
        print('Types     : {}'.format(', '.join('{}={}'.format(k, v) for k, v in r['var_types'].items())))
        print('Time      : {} ~ {} ({})'.format(r['time_min'], r['time_max'], r['duration']))
        for s in r['scopes']:
            print('  scope: {}'.format(s))
        if r['comments'] and getattr(args, 'verbose', False):
            # Comments verbose-only: typical files have boilerplate
            # ("Generated by ..."), worth showing only on demand.
            print('Comments  :')
            for c in r['comments']:
                print('  - {}'.format(c))


def cmd_list(vcd, args):
    limit = _limit(args, 'list')
    sids = vcd.match(args.filter)
    entries = []
    for sid, info in vcd.signals.items():
        if sids is not None and sid not in sids:
            continue
        vtype = info.get('type', 'wire')
        for path in info['aliases']:
            e = {'path': path, 'width': info['width'], 'type': vtype}
            if getattr(args, 'verbose', False):
                e['id'] = sid
                if info.get('synthesized'):
                    e['synthesized'] = True
                    e['raw_bits'] = info.get('raw_bits')
            entries.append(e)
    entries.sort(key=lambda e: e['path'])
    shown, trunc = _clip(entries, limit)
    if args.json:
        _json({'total': len(entries), 'shown': len(shown), 'truncated': trunc, 'signals': shown})
    else:
        print('Matched: {}/{}'.format(len(entries), len(vcd.signals)))
        for e in shown:
            print('  {:<60} {:>5}  {}'.format(e['path'], e['width'], e['type']))
        if trunc:
            print(_trunc_line(len(shown), len(entries), 'signals'))


def cmd_dump(vcd, args):
    ts = vcd.ts_sec
    t0 = parse_time(args.begin, ts) if args.begin else 0
    t1 = parse_time(args.end, ts) if args.end else None
    if t1 is not None and t1 < t0:
        raise _TimeParseError('end time must be >= begin time')
    sids = vcd.match(args.filter)
    limit = _limit(args, 'dump')
    total = 0
    truncated = False
    events = []
    for t, sid, val in vcd.iter_events(t0, t1, sids):
        total += 1
        if limit != 0 and len(events) >= limit:
            truncated = True
            break
        info = vcd.signals[sid]
        e = {'time': t, 'time_ticks': t, 'time_h': fmt_time(t, ts),
             'path': info['path'], 'value': fmt_val(val, info)}
        if getattr(args, 'verbose', False):
            e['width'] = info['width']
            e['type'] = info.get('type', 'wire')
        events.append(e)
    if args.json:
        obj = {'shown': len(events), 'truncated': truncated, 'events': events}
        obj.update(_total_json_fields(total, truncated))
        _json(obj)
        return
    if not events:
        print('(no changes in range)')
        return
    cur = None
    for e in events:
        if e['time'] != cur:
            cur = e['time']
            print('T={}'.format(e['time_h']))
        if getattr(args, 'verbose', False):
            print('  {:<55} w={} {} = {}'.format(e['path'], e.get('width'), e.get('type'), e['value']))
        else:
            print('  {:<55} = {}'.format(e['path'], e['value']))
    if truncated:
        print(_trunc_line_lower_bound(len(events), total, 'events'))


def cmd_summary(vcd, args):
    ts = vcd.ts_sec
    t0 = parse_time(args.begin, ts) if args.begin else 0
    t1 = parse_time(args.end, ts) if args.end else None
    if t1 is not None and t1 < t0:
        raise _TimeParseError('end time must be >= begin time')
    sids = vcd.match(args.filter)
    selected = _selected_sids(vcd, sids)
    rows, undef_sids, counts = _summary_rows(vcd, t0, t1, selected)
    active = [r for r in rows if r['kind'] == 'active']
    static = [r for r in rows if r['kind'] == 'static']
    ordered = active + static
    if getattr(args, 'verbose', False):
        for sid in undef_sids:
            info = vcd.signals[sid]
            ordered.append({'kind': 'undefined', 'path': info['path'], 'value': None,
                            'changes': 0, 'rise_count': 0 if info['width'] == 1 else None,
                            'fall_count': 0 if info['width'] == 1 else None,
                            'init': '(undef)', 'last': '(undef)',
                            '_width': info['width'], '_type': info.get('type', 'wire')})
    limit = _limit(args, 'summary')
    shown, trunc = _clip(ordered, limit)
    begin_h = fmt_time(t0, ts)
    end_h = fmt_time(t1, ts) if t1 is not None else None
    if args.json:
        _json({'window': {'begin': begin_h, 'end': end_h,
                          'begin_ticks': t0, 'begin_h': begin_h,
                          'end_ticks': t1, 'end_h': end_h}, **counts,
               'shown': len(shown), 'truncated': trunc,
               'rows': [_public_row(r, getattr(args, 'verbose', False)) for r in shown]})
        return
    print('Window: {}..{}'.format(begin_h, end_h if end_h is not None else '(end)'))
    print('Selected: {}, Defined: {}, Undefined: {}'.format(
        counts['selected'], counts['defined'], counts['undefined']))
    print('Active: {}, Static: {}'.format(counts['active'], counts['static']))
    current = None
    for r in shown:
        if r['kind'] != current:
            current = r['kind']
            print('\n{}'.format(current.upper()))
        if r['kind'] == 'active':
            if getattr(args, 'verbose', False):
                edge = '' if r.get('rise_count') is None else ' r={} f={}'.format(
                    r.get('rise_count', 0), r.get('fall_count', 0))
                print('  {:<45} w={} {} chg={}{} init={} last={} first@{} last@{} uniq={}'.format(
                    r['path'], r['_width'], r['_type'], r['changes'], edge, r['init'], r['last'],
                    r.get('first_at', '-'), r.get('last_at', '-'), r.get('unique', 0)))
            else:
                edge = '' if r.get('rise_count') is None else ' r={} f={}'.format(
                    r.get('rise_count', 0), r.get('fall_count', 0))
                print('  {:<45} chg={}{} init={} last={}'.format(
                    r['path'], r['changes'], edge, r['init'], r['last']))
        elif r['kind'] == 'static':
            if getattr(args, 'verbose', False):
                print('  {:<45} w={} {} value={}'.format(r['path'], r['_width'], r['_type'], r['value']))
            else:
                print('  {:<45} value={}'.format(r['path'], r['value']))
        else:
            print('  {:<45} w={} {}'.format(r['path'], r['_width'], r['_type']))
    if not rows and not undef_sids:
        print('(no selected signals)')
    if trunc:
        print(_trunc_line(len(shown), len(ordered), 'rows'))


def cmd_snapshot(vcd, args):
    ts = vcd.ts_sec
    t_at = parse_time(args.at, ts)
    sids0 = vcd.match(args.filter)
    selected = _selected_sids(vcd, sids0)
    state = _build_snapshot(vcd, t_at, selected)
    rows = []
    for sid in sorted(state, key=lambda s: vcd.signals[s]['path']):
        info = vcd.signals[sid]
        r = {'path': info['path'], 'value': fmt_val(state[sid], info)}
        if getattr(args, 'verbose', False):
            r['width'] = info['width']
            r['type'] = info.get('type', 'wire')
        rows.append(r)
    undef = sorted(selected - set(state), key=lambda s: vcd.signals[s]['path'])
    if getattr(args, 'verbose', False):
        for sid in undef:
            info = vcd.signals[sid]
            rows.append({'path': info['path'], 'value': None, 'undefined': True,
                         'width': info['width'], 'type': info.get('type', 'wire')})
    limit = _limit(args, 'snapshot')
    shown, trunc = _clip(rows, limit)
    if args.json:
        _json({'at': fmt_time(t_at, ts), 'at_ticks': t_at, 'at_h': fmt_time(t_at, ts),
               'selected': len(selected), 'known': len(state),
               'undefined': len(undef), 'shown': len(shown), 'truncated': trunc,
               'signals': shown})
        return
    if not state:
        print('No known values at {}.'.format(fmt_time(t_at, ts)))
    else:
        print('Known snapshot @ {}'.format(fmt_time(t_at, ts)))
    if getattr(args, 'verbose', False):
        print('Selected: {}, Known: {}, Undefined: {}'.format(len(selected), len(state), len(undef)))
    for r in shown:
        if r.get('undefined'):
            print('  {:<55} = (undef)'.format(r['path']))
        elif getattr(args, 'verbose', False):
            print('  {:<55} w={} {} = {}'.format(r['path'], r.get('width'), r.get('type'), r['value']))
        else:
            print('  {:<55} = {}'.format(r['path'], r['value']))
    if trunc:
        print(_trunc_line(len(shown), len(rows), 'signals'))


def cmd_compare(vcd, args):
    ts = vcd.ts_sec
    parts = args.at.split(',')
    if len(parts) != 2:
        raise _TimeParseError(
            '--at needs two times separated by comma, e.g. --at 17.5us,17.7us')
    ta, tb = parse_time(parts[0].strip(), ts), parse_time(parts[1].strip(), ts)
    if tb < ta:
        raise _TimeParseError('second compare time must be >= first compare time')
    sids = vcd.match(args.filter)
    sa = _build_snapshot(vcd, ta, sids)
    sb = _build_snapshot(vcd, tb, sids)
    diffs = []
    for sid in sorted(set(sa) | set(sb), key=lambda s: vcd.signals[s]['path']):
        va, vb = sa.get(sid), sb.get(sid)
        if va != vb:
            info = vcd.signals[sid]
            d = {'path': info['path'],
                 'at_t1': fmt_val(va, info) if va is not None else '(undef)',
                 'at_t2': fmt_val(vb, info) if vb is not None else '(undef)'}
            if getattr(args, 'verbose', False):
                d['width'] = info['width']
                d['type'] = info.get('type', 'wire')
            diffs.append(d)
    limit = _limit(args, 'compare')
    shown, trunc = _clip(diffs, limit)
    if args.json:
        _json({'t1': fmt_time(ta, ts), 't1_ticks': ta, 't1_h': fmt_time(ta, ts),
               't2': fmt_time(tb, ts), 't2_ticks': tb, 't2_h': fmt_time(tb, ts),
               'total': len(diffs), 'shown': len(shown), 'truncated': trunc,
               'diffs': shown})
    else:
        print('Compare: {} vs {}'.format(fmt_time(ta, ts), fmt_time(tb, ts)))
        print('{} changed, {} unchanged'.format(len(diffs), len(set(sa) | set(sb)) - len(diffs)))
        for d in shown:
            print('  {:<48} {} -> {}'.format(d['path'], d['at_t1'], d['at_t2']))
        if trunc:
            print(_trunc_line(len(shown), len(diffs), 'diffs'))


def cmd_search(vcd, args):
    ts = vcd.ts_sec
    t0 = parse_time(args.begin, ts) if args.begin else 0
    t1_raw = parse_time(args.end, ts) if args.end else None
    t1 = _search_end_time(vcd, t0, t1_raw)
    if t1 < t0:
        raise _TimeParseError('end time must be >= begin time')

    conditions = _resolve_conditions(vcd, args.condition)
    show_sids = _resolve_show_sids(vcd, args.show)
    changed_sid = _resolve_one_signal(vcd, args.changed, 'changed signal') if args.changed else None
    if changed_sid is not None and not show_sids:
        show_sids = [changed_sid]

    selected = set(c['sid'] for c in conditions)
    selected.update(show_sids)
    if changed_sid is not None:
        selected.add(changed_sid)

    # Inclusive snapshot is correct for interval/segment modes: they ask
    # what state holds at t0.  changed mode needs the state before t0 so
    # an edge exactly at --begin remains observable.
    state = (_build_snapshot_before(vcd, t0, selected)
             if changed_sid is not None else _build_snapshot(vcd, t0, selected))
    limit = _limit(args, 'search')
    verbose = getattr(args, 'verbose', False)
    cond_label = _condition_label(conditions)
    cond_text = _condition_result_text(conditions)

    if changed_sid is not None:
        events = []
        total = 0
        truncated = False
        for t, group in _event_groups(vcd, t0, t1, selected):
            changed = set()
            for sid, val in group:
                old_val = state.get(sid)
                # For ordinary state-carrying signals, --changed means the
                # value is different from the previous known state. VCD
                # variables of type `event` are different: every value_change
                # token is a trigger even if the dumped marker text repeats.
                # In both cases, t=0 dumpvars-style initialization is not a
                # real change.
                if t == 0 and old_val is None:
                    pass
                elif vcd.signals[sid].get('type') == 'event':
                    changed.add(sid)
                elif old_val is None:
                    # First observed value for an ordinary state signal is a
                    # definition, not evidence of a transition.  This matters
                    # when --begin is after time 0 and a signal is first dumped
                    # inside the query window.
                    pass
                elif old_val != val:
                    changed.add(sid)
                state[sid] = val
            if changed_sid not in changed:
                continue
            if not _conditions_hold(state, conditions):
                continue
            values, meta = _show_values(vcd, state, show_sids, verbose)
            event = {'time_ticks': t, 'time_h': fmt_time(t, ts), 'values': values}
            if verbose:
                event['meta'] = meta
            total += 1
            if limit != 0 and len(events) >= limit:
                truncated = True
                break
            events.append(event)
        if args.json:
            obj = {'mode': 'event', 'condition': cond_label,
                   'condition_resolved': cond_text,
                   'changed': vcd.signals[changed_sid]['path'],
                   'show': [vcd.signals[sid]['path'] for sid in show_sids],
                   'begin_ticks': t0, 'begin_h': fmt_time(t0, ts),
                   'end_ticks': t1, 'end_h': fmt_time(t1, ts),
                   'shown': len(events), 'truncated': truncated,
                   'events': events}
            obj.update(_total_json_fields(total, truncated))
            _json(obj)
            return
        if events:
            print('Found: {} event(s)'.format(_count_label(len(events), total, truncated)))
            for e in events:
                print('  T={:<12} {}'.format(e['time_h'], _values_text(e['values'])))
            if truncated:
                print(_trunc_line_lower_bound(len(events), total, 'events'))
        else:
            print('No event in {}..{} where {} changed and {}.'.format(
                fmt_time(t0, ts), fmt_time(t1, ts), vcd.signals[changed_sid]['path'], cond_text))
        return

    # Interval/segment mode. A segment is an interval further split whenever
    # the displayed show-value tuple changes while the condition remains true.
    has_show = bool(show_sids)
    active = _conditions_hold(state, conditions)
    seg_start = t0 if active else None
    seg_values = None
    seg_meta = None
    if active and has_show:
        seg_values, seg_meta = _show_values(vcd, state, show_sids, verbose)

    results = []
    total = 0
    truncated = False

    def emit_interval(a, b):
        return {'begin_ticks': a, 'begin_h': fmt_time(a, ts),
                'end_ticks': b, 'end_h': fmt_time(b, ts)}

    def append_result(row):
        nonlocal total, truncated
        total += 1
        if limit != 0 and len(results) >= limit:
            truncated = True
            return True
        results.append(row)
        return False

    for t, group in _event_groups(vcd, t0, t1, selected):
        # _build_snapshot(vcd, t0) already applied all value_changes at t0.
        # Replaying the same group is idempotent for legal VCD, but skipping
        # it avoids duplicate work for large initial dumps at the window start.
        if t <= t0:
            continue
        # Interval/segment mode only needs the current cross-section state;
        # changed-mode edge detection is handled in its own branch above.
        for sid, val in group:
            state[sid] = val

        cond_ok = _conditions_hold(state, conditions)
        if not has_show:
            if cond_ok and not active:
                active = True
                seg_start = t
            elif not cond_ok and active:
                if append_result(emit_interval(seg_start, t)):
                    break
                active = False
                seg_start = None
            continue

        if not cond_ok:
            if active:
                row = emit_interval(seg_start, t)
                row['values'] = seg_values
                if verbose:
                    row['meta'] = seg_meta
                if append_result(row):
                    break
                active = False
                seg_start = None
                seg_values = None
                seg_meta = None
            continue

        new_values, new_meta = _show_values(vcd, state, show_sids, verbose)
        if not active:
            active = True
            seg_start = t
            seg_values = new_values
            seg_meta = new_meta
        elif new_values != seg_values:
            row = emit_interval(seg_start, t)
            row['values'] = seg_values
            if verbose:
                row['meta'] = seg_meta
            if append_result(row):
                break
            seg_start = t
            seg_values = new_values
            seg_meta = new_meta

    if active and not truncated:
        row = emit_interval(seg_start, t1)
        if has_show:
            row['values'] = seg_values
            if verbose:
                row['meta'] = seg_meta
        append_result(row)

    if args.json:
        key = 'segments' if has_show else 'intervals'
        obj = {'mode': 'segment' if has_show else 'interval',
               'condition': cond_label,
               'condition_resolved': cond_text,
               'show': [vcd.signals[sid]['path'] for sid in show_sids],
               'begin_ticks': t0, 'begin_h': fmt_time(t0, ts),
               'end_ticks': t1, 'end_h': fmt_time(t1, ts),
               'shown': len(results), 'truncated': truncated,
               key: results}
        obj.update(_total_json_fields(total, truncated))
        _json(obj)
        return

    noun = 'segment' if has_show else 'interval'
    if results:
        print('Found: {} {}(s)'.format(_count_label(len(results), total, truncated), noun))
        for r in results:
            if has_show:
                print('  {:<12}..{:<12} {}'.format(
                    r['begin_h'], r['end_h'], _values_text(r['values'])))
            else:
                print('  {:<12}..{:<12} {}'.format(r['begin_h'], r['end_h'], cond_text))
        if truncated:
            print(_trunc_line_lower_bound(len(results), total, noun + 's'))
    else:
        print('No {} in {}..{} where {}.'.format(
            noun, fmt_time(t0, ts), fmt_time(t1, ts), cond_text))


# -- Skill Framework ---------------------------------------------------------
#
# Phase 2 standardizes the JSON output shape across all Skills so AI Agents
# can consume responses uniformly. Every Skill emits:
#
#   {
#     "status": "success" | "error",
#     "skill": "<name>",
#     "execution_time_ms": <int>,
#     "input": { ... },
#     "result": { ... },          # only on success
#     "metadata": { ... },        # only on success
#     "suggestions": [ ... ],
#     "error": { code, message, details }   # only on error
#   }
#
# Helpers below build the response and structured error objects. Existing
# fields inside "result"/"input" are preserved, so older tests keep passing.

import time as _time

# Stable error codes Agents can branch on. New codes can be added without
# breaking existing consumers.
SKILL_ERROR_CODES = {
    'FILE_NOT_FOUND',
    'PARSE_ERROR',
    'INVALID_PROTOCOL',
    'SIGNAL_NOT_FOUND',
    'INVALID_TIME_RANGE',
    'INVALID_ARGUMENT',
    'INSUFFICIENT_DATA',
    'RESOURCE_LIMIT',
    'INTERNAL_ERROR',
}


class SkillError(Exception):
    """Raised by Skills to emit a structured error response.

    Caught by run_skill() and converted into the standard JSON error envelope.
    Carry an error code (one of SKILL_ERROR_CODES), a human-readable message,
    and an optional details dict for Agent-side diagnostics.
    """
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _build_metadata(vcd, t0, t1, signals_matched=None):
    """Build the standard metadata block included in every Skill response."""
    try:
        file_size = os.path.getsize(vcd.path)
    except OSError:
        file_size = None

    if signals_matched is None:
        signals_matched = len(vcd.signals)

    md = {
        'vcd_file_size': file_size,
        'analyzer_version': __version__,
        'signals_matched': signals_matched,
    }
    if t0 is not None or t1 is not None:
        md['time_range_analyzed'] = [
            fmt_time(t0 or 0, vcd.ts_sec),
            fmt_time(t1, vcd.ts_sec) if t1 is not None else 'end',
        ]
    return md


def _skill_envelope(skill_name, started_at, input_dict, result, metadata, suggestions):
    """Assemble a successful Skill response with execution_time_ms populated."""
    elapsed_ms = int(round((_time.perf_counter() - started_at) * 1000))
    envelope = {
        'status': 'success',
        'skill': skill_name,
        'execution_time_ms': elapsed_ms,
        'input': input_dict,
        'result': result,
        'metadata': metadata,
        'suggestions': suggestions,
    }
    return envelope


def _skill_error_envelope(skill_name, started_at, input_dict, code, message, details=None):
    """Assemble an error response. Used by run_skill() exception handlers."""
    elapsed_ms = int(round((_time.perf_counter() - started_at) * 1000))
    return {
        'status': 'error',
        'skill': skill_name,
        'execution_time_ms': elapsed_ms,
        'input': input_dict,
        'error': {
            'code': code,
            'message': message,
            'details': details or {},
        },
        'suggestions': [],
    }


def run_skill(skill_name, args, fn):
    """Execute a Skill handler `fn(args, started_at)` with unified error handling.

    The handler should either:
      - return None (handler does its own printing — for backward compatibility), or
      - return a fully-built envelope dict (preferred for new Skills).

    Exceptions raised by `fn` are translated into structured error envelopes
    when --json is requested. In text mode, they fall through to the existing
    main() error handlers so the CLI experience is unchanged.
    """
    started_at = _time.perf_counter()
    try:
        return fn(args, started_at)
    except SkillError as e:
        if getattr(args, 'json', False):
            envelope = _skill_error_envelope(
                skill_name, started_at,
                _skill_input_from_args(args, skill_name),
                e.code, e.message, e.details
            )
            _json(envelope)
            sys.exit(1)
        else:
            sys.exit('Error [{}]: {}'.format(e.code, e.message))


def _skill_input_from_args(args, skill_name):
    """Build a best-effort input echo from argparse Namespace for error envelopes."""
    inp = {'file': getattr(args, 'file', None)}
    # Skill-specific arguments
    for key in ('protocol', 'signals', 'state', 'stuck_threshold',
                'glitch_threshold', 'effect', 'at', 'window', 'filter',
                'begin', 'end'):
        val = getattr(args, key, None)
        if val is not None:
            inp[key] = val
    return inp


# -- Protocol Decoders -------------------------------------------------------

class ProtocolDecoder:
    """Base class for protocol decoders"""
    def __init__(self, vcd, signal_pattern):
        self.vcd = vcd
        self.ts_sec = vcd.ts_sec
        self.signal_map = self._identify_signals(signal_pattern)

    def _identify_signals(self, pattern):
        """Identify and map protocol signals. Override in subclass."""
        raise NotImplementedError

    def decode(self, t0, t1):
        """Decode protocol transactions. Returns (transactions, violations, statistics)."""
        raise NotImplementedError


class AXI4Decoder(ProtocolDecoder):
    """AXI4 protocol decoder"""

    def _identify_signals(self, pattern):
        """Identify AXI4 signals from pattern"""
        matched_sids = self.vcd.match(pattern)
        if not matched_sids:
            raise ValueError(f"No signals matched pattern: {pattern}")

        signal_map = {}
        for sid in matched_sids:
            path = self.vcd.signals[sid]['path']
            # Extract signal name (last part after dot, or full path if no dot)
            signal_name = path.split('.')[-1].lower()

            # Write Address Channel
            if 'awvalid' in signal_name:
                signal_map['awvalid'] = sid
            elif 'awready' in signal_name:
                signal_map['awready'] = sid
            elif 'awaddr' in signal_name:
                signal_map['awaddr'] = sid
            elif 'awlen' in signal_name:
                signal_map['awlen'] = sid
            elif 'awsize' in signal_name:
                signal_map['awsize'] = sid
            elif 'awburst' in signal_name:
                signal_map['awburst'] = sid

            # Write Data Channel
            elif 'wvalid' in signal_name:
                signal_map['wvalid'] = sid
            elif 'wready' in signal_name:
                signal_map['wready'] = sid
            elif 'wdata' in signal_name:
                signal_map['wdata'] = sid
            elif 'wlast' in signal_name:
                signal_map['wlast'] = sid
            elif 'wstrb' in signal_name:
                signal_map['wstrb'] = sid

            # Write Response Channel
            elif 'bvalid' in signal_name:
                signal_map['bvalid'] = sid
            elif 'bready' in signal_name:
                signal_map['bready'] = sid
            elif 'bresp' in signal_name:
                signal_map['bresp'] = sid

            # Read Address Channel
            elif 'arvalid' in signal_name:
                signal_map['arvalid'] = sid
            elif 'arready' in signal_name:
                signal_map['arready'] = sid
            elif 'araddr' in signal_name:
                signal_map['araddr'] = sid
            elif 'arlen' in signal_name:
                signal_map['arlen'] = sid

            # Read Data Channel
            elif 'rvalid' in signal_name:
                signal_map['rvalid'] = sid
            elif 'rready' in signal_name:
                signal_map['rready'] = sid
            elif 'rdata' in signal_name:
                signal_map['rdata'] = sid
            elif 'rresp' in signal_name:
                signal_map['rresp'] = sid
            elif 'rlast' in signal_name:
                signal_map['rlast'] = sid

        return signal_map

    def decode(self, t0, t1):
        """Decode AXI4 transactions in time range [t0, t1]"""
        transactions = []
        violations = []

        # Track state for each channel
        write_txns = []  # List of write transactions
        read_txns = []   # List of read transactions
        txn_id_counter = 0

        # Current signal values
        current_vals = {}
        for name, sid in self.signal_map.items():
            current_vals[name] = None

        # Track previous handshake states to avoid duplicate captures
        prev_aw_handshake = False
        prev_w_handshake = False
        prev_ar_handshake = False
        prev_r_handshake = False

        # Collect all events
        all_sids = list(self.signal_map.values())
        for t, sid, val in self.vcd.iter_events(t0, t1, all_sids):
            # Find signal name
            sig_name = None
            for name, s in self.signal_map.items():
                if s == sid:
                    sig_name = name
                    break

            if sig_name:
                current_vals[sig_name] = val

            # Check for write address handshake (check after every event)
            aw_handshake = (current_vals.get('awvalid') == '1' and
                           current_vals.get('awready') == '1')

            if aw_handshake and not prev_aw_handshake:
                # Write address accepted
                addr_val = current_vals.get('awaddr', '0')
                addr = val_to_int(addr_val) if addr_val else 0
                awlen_val = current_vals.get('awlen', '0')
                burst_len = val_to_int(awlen_val) + 1 if awlen_val else 1

                txn = {
                    'id': txn_id_counter,
                    'type': 'write',
                    'addr': addr,
                    'addr_time': t,
                    'burst_len': burst_len,
                    'data': [],
                    'status': 'pending'
                }
                write_txns.append(txn)
                txn_id_counter += 1

            prev_aw_handshake = aw_handshake

            # Check for write data handshake (check after every event)
            w_handshake = (current_vals.get('wvalid') == '1' and
                          current_vals.get('wready') == '1')

            # Record data on new handshake OR when wdata changes during active handshake
            if w_handshake:
                if not prev_w_handshake or sig_name == 'wdata':
                    # New handshake OR wdata changed during handshake - record data
                    wdata_val = current_vals.get('wdata', '0')
                    data = val_to_int(wdata_val) if wdata_val else 0
                    wlast = current_vals.get('wlast') == '1'

                    # Find matching transaction (most recent pending)
                    for txn in reversed(write_txns):
                        if txn['status'] == 'pending':
                            txn['data'].append(data)
                            if wlast:
                                txn['data_time'] = t
                            break

            prev_w_handshake = w_handshake

            # Check for write response handshake (check after every event)
            b_handshake = (current_vals.get('bvalid') == '1' and
                          current_vals.get('bready') == '1')

            if b_handshake:
                # Write response received
                bresp_val = current_vals.get('bresp', '0')
                bresp = val_to_int(bresp_val) if bresp_val else 0

                # Find matching transaction
                for txn in reversed(write_txns):
                    if txn['status'] == 'pending' and 'data_time' in txn:
                        txn['end_time'] = t
                        txn['status'] = ['OKAY', 'EXOKAY', 'SLVERR', 'DECERR'][bresp] if bresp < 4 else 'UNKNOWN'
                        transactions.append(txn)
                        break

            # Check for read address handshake (check after every event)
            ar_handshake = (current_vals.get('arvalid') == '1' and
                           current_vals.get('arready') == '1')

            if ar_handshake and not prev_ar_handshake:
                # Read address accepted
                addr_val = current_vals.get('araddr', '0')
                addr = val_to_int(addr_val) if addr_val else 0
                arlen_val = current_vals.get('arlen', '0')
                burst_len = val_to_int(arlen_val) + 1 if arlen_val else 1

                txn = {
                    'id': txn_id_counter,
                    'type': 'read',
                    'addr': addr,
                    'addr_time': t,
                    'burst_len': burst_len,
                    'data': [],
                    'status': 'pending'
                }
                read_txns.append(txn)
                txn_id_counter += 1

            prev_ar_handshake = ar_handshake

            # Check for read data handshake (check after every event)
            r_handshake = (current_vals.get('rvalid') == '1' and
                          current_vals.get('rready') == '1')

            # Record data on new handshake OR when rdata changes during active handshake
            if r_handshake:
                if not prev_r_handshake or sig_name == 'rdata':
                    # New handshake OR rdata changed during handshake - record data
                    rdata_val = current_vals.get('rdata', '0')
                    data = val_to_int(rdata_val) if rdata_val else 0
                    rlast = current_vals.get('rlast') == '1'
                    rresp_val = current_vals.get('rresp', '0')
                    rresp = val_to_int(rresp_val) if rresp_val else 0

                    # Find matching transaction
                    for txn in reversed(read_txns):
                        if txn['status'] == 'pending':
                            txn['data'].append(data)
                            if rlast:
                                txn['end_time'] = t
                                txn['status'] = ['OKAY', 'EXOKAY', 'SLVERR', 'DECERR'][rresp] if rresp < 4 else 'UNKNOWN'
                                transactions.append(txn)
                            break

            prev_r_handshake = r_handshake

        # Detect protocol violations
        violations = self._detect_violations(transactions, t0, t1)

        # Calculate statistics
        statistics = self._compute_statistics(transactions, t0, t1)

        return transactions, violations, statistics


    def _detect_violations(self, transactions, t0, t1):
        """Detect AXI4 protocol violations"""
        violations = []

        # Check for incomplete transactions
        for txn in transactions:
            if txn['status'] == 'pending':
                violations.append({
                    'type': 'incomplete_transaction',
                    'time': fmt_time(txn['addr_time'], self.ts_sec),
                    'time_ticks': txn['addr_time'],
                    'severity': 'warning',
                    'description': f"{txn['type'].capitalize()} transaction to 0x{txn['addr']:X} not completed"
                })

            # Check burst length mismatch
            if 'data' in txn and len(txn['data']) != txn['burst_len']:
                violations.append({
                    'type': 'burst_length_mismatch',
                    'time': fmt_time(txn.get('end_time', txn['addr_time']), self.ts_sec),
                    'time_ticks': txn.get('end_time', txn['addr_time']),
                    'severity': 'error',
                    'description': f"Expected {txn['burst_len']} beats, got {len(txn['data'])}"
                })

        return violations

    def _compute_statistics(self, transactions, t0, t1):
        """Compute performance statistics"""
        if not transactions:
            return {
                'total_transactions': 0,
                'read_count': 0,
                'write_count': 0,
                'avg_latency': None,
                'bandwidth_utilization': 0.0
            }

        read_txns = [t for t in transactions if t['type'] == 'read']
        write_txns = [t for t in transactions if t['type'] == 'write']

        # Calculate average latency
        latencies = []
        for txn in transactions:
            if 'end_time' in txn and 'addr_time' in txn:
                latency = txn['end_time'] - txn['addr_time']
                latencies.append(latency)

        avg_latency = sum(latencies) / len(latencies) if latencies else 0

        # Calculate bandwidth utilization (simplified)
        total_time = t1 - t0 if t1 else 0
        active_time = sum(latencies)
        bandwidth_util = (active_time / total_time) if total_time > 0 else 0.0

        return {
            'total_transactions': len(transactions),
            'read_count': len(read_txns),
            'write_count': len(write_txns),
            'avg_latency': fmt_time(int(avg_latency), self.ts_sec) if avg_latency > 0 else None,
            'avg_latency_ticks': int(avg_latency) if avg_latency > 0 else None,
            'bandwidth_utilization': round(bandwidth_util, 2)
        }


class APBDecoder(ProtocolDecoder):
    """APB (Advanced Peripheral Bus) protocol decoder.

    Supports APB3 with PREADY and PSLVERR.
    """

    def _identify_signals(self, pattern):
        matched_sids = self.vcd.match(pattern)
        if not matched_sids:
            raise ValueError(f"No signals matched pattern: {pattern}")

        signal_map = {}
        for sid in matched_sids:
            path = self.vcd.signals[sid]['path']
            signal_name = path.split('.')[-1].lower()

            # APB signal identification
            if 'paddr' in signal_name:
                signal_map['paddr'] = sid
            elif 'pwrite' in signal_name:
                signal_map['pwrite'] = sid
            elif 'psel' in signal_name:
                signal_map['psel'] = sid
            elif 'penable' in signal_name:
                signal_map['penable'] = sid
            elif 'pwdata' in signal_name:
                signal_map['pwdata'] = sid
            elif 'prdata' in signal_name:
                signal_map['prdata'] = sid
            elif 'pready' in signal_name:
                signal_map['pready'] = sid
            elif 'pslverr' in signal_name:
                signal_map['pslverr'] = sid

        return signal_map

    def decode(self, t0, t1):
        """Decode APB transactions.

        APB state machine:
        IDLE -> SETUP (psel=1, penable=0) -> ACCESS (psel=1, penable=1, pready=1) -> IDLE
        """
        transactions = []
        violations = []
        txn_id = 0

        current_vals = {name: None for name in self.signal_map}
        # APB has three logical states: IDLE / SETUP / ACCESS
        prev_state = 'IDLE'
        current_txn = None

        all_sids = list(self.signal_map.values())

        # Group events by timestamp - process all events at same time as a batch,
        # then evaluate the state transition. This avoids spurious transitions
        # when multiple signals change at the same time.
        events_by_time = {}
        for t, sid, val in self.vcd.iter_events(t0, t1, all_sids):
            events_by_time.setdefault(t, []).append((sid, val))

        for t in sorted(events_by_time.keys()):
            # Apply all signal updates at this timestamp
            for sid, val in events_by_time[t]:
                for name, s in self.signal_map.items():
                    if s == sid:
                        current_vals[name] = val
                        break

            psel = current_vals.get('psel')
            penable = current_vals.get('penable')
            pready = current_vals.get('pready')

            # Determine state
            if psel == '1' and penable == '0':
                state = 'SETUP'
            elif psel == '1' and penable == '1':
                state = 'ACCESS'
            else:
                state = 'IDLE'

            # State transition: IDLE -> SETUP (start of transaction)
            if prev_state == 'IDLE' and state == 'SETUP':
                pwrite = current_vals.get('pwrite')
                paddr = current_vals.get('paddr', '0')
                pwdata = current_vals.get('pwdata', '0')

                current_txn = {
                    'id': txn_id,
                    'type': 'write' if pwrite == '1' else 'read',
                    'addr': val_to_int(paddr) if paddr else 0,
                    'addr_time': t,
                    'status': 'pending',
                }
                if pwrite == '1':
                    current_txn['data'] = val_to_int(pwdata) if pwdata else 0
                txn_id += 1

            # SETUP -> ACCESS: enable phase started
            elif prev_state == 'SETUP' and state == 'ACCESS':
                if current_txn:
                    current_txn['access_time'] = t

            # Inside ACCESS: check pready, possibly complete transaction
            if state == 'ACCESS' and pready == '1' and current_txn and current_txn['status'] == 'pending':
                pslverr = current_vals.get('pslverr')
                prdata = current_vals.get('prdata', '0')

                current_txn['end_time'] = t
                if pslverr == '1':
                    current_txn['status'] = 'SLVERR'
                else:
                    current_txn['status'] = 'OKAY'

                if current_txn['type'] == 'read':
                    current_txn['data'] = val_to_int(prdata) if prdata else 0

                transactions.append(current_txn)
                current_txn = None

            # State transitions: detect violations
            if prev_state == 'SETUP' and state == 'IDLE':
                # Aborted before reaching ACCESS - protocol violation
                violations.append({
                    'type': 'protocol_violation',
                    'time': fmt_time(t, self.ts_sec),
                    'time_ticks': t,
                    'severity': 'error',
                    'description': 'PSEL deasserted in SETUP phase without entering ACCESS'
                })
                current_txn = None

            prev_state = state

        # Compute statistics
        statistics = self._compute_statistics(transactions, t0, t1)

        return transactions, violations, statistics

    def _compute_statistics(self, transactions, t0, t1):
        if not transactions:
            return {
                'total_transactions': 0,
                'read_count': 0,
                'write_count': 0,
                'error_count': 0,
                'avg_latency': None,
            }

        reads = [tt for tt in transactions if tt['type'] == 'read']
        writes = [tt for tt in transactions if tt['type'] == 'write']
        errors = [tt for tt in transactions if tt.get('status') == 'SLVERR']

        latencies = [tt['end_time'] - tt['addr_time']
                     for tt in transactions if 'end_time' in tt]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0

        return {
            'total_transactions': len(transactions),
            'read_count': len(reads),
            'write_count': len(writes),
            'error_count': len(errors),
            'avg_latency': fmt_time(int(avg_latency), self.ts_sec) if avg_latency > 0 else None,
            'avg_latency_ticks': int(avg_latency) if avg_latency > 0 else None,
        }


class UARTDecoder(ProtocolDecoder):
    """UART protocol decoder.

    Decodes byte values from serial transitions. Auto-detects baud rate by
    measuring the first start bit duration.

    Signal naming conventions:
    - tx: signal containing 'tx' or 'txd'
    - rx: signal containing 'rx' or 'rxd'

    Frame format assumed: 1 start (0), 8 data (LSB first), 1 stop (1)
    """

    def __init__(self, vcd, signal_pattern, baud_rate=None):
        self.baud_rate = baud_rate  # bits per second; None = auto-detect
        super().__init__(vcd, signal_pattern)

    def _identify_signals(self, pattern):
        matched_sids = self.vcd.match(pattern)
        if not matched_sids:
            raise ValueError(f"No signals matched pattern: {pattern}")

        signal_map = {}
        for sid in matched_sids:
            path = self.vcd.signals[sid]['path']
            signal_name = path.split('.')[-1].lower()

            if 'tx' in signal_name and 'rx' not in signal_name:
                signal_map['tx'] = sid
            elif 'rx' in signal_name and 'tx' not in signal_name:
                signal_map['rx'] = sid

        return signal_map

    def decode(self, t0, t1):
        """Decode UART bytes from each line (TX/RX)."""
        transactions = []
        violations = []

        for line_name in ('tx', 'rx'):
            if line_name not in self.signal_map:
                continue
            line_sid = self.signal_map[line_name]
            line_txns, line_violations = self._decode_line(line_sid, line_name, t0, t1)
            transactions.extend(line_txns)
            violations.extend(line_violations)

        # Sort by start time
        transactions.sort(key=lambda x: x['addr_time'])
        for i, t in enumerate(transactions):
            t['id'] = i

        statistics = self._compute_statistics(transactions, t0, t1)
        return transactions, violations, statistics

    def _decode_line(self, sid, line_name, t0, t1):
        """Decode bytes on a single UART line."""
        transactions = []
        violations = []

        # Collect all transitions on this line
        events = []
        for t, s, val in self.vcd.iter_events(t0, t1, [sid]):
            events.append((t, val))

        if len(events) < 2:
            return transactions, violations

        # Auto-detect bit duration: smallest gap between transitions tends to be 1 bit
        # Skip the first event (initial value setup)
        gaps = []
        for i in range(1, len(events) - 1):
            gap = events[i + 1][0] - events[i][0]
            if gap > 0:
                gaps.append(gap)

        if not gaps:
            return transactions, violations

        # Bit time = the minimum gap (assumes at least one single-bit gap)
        bit_time = min(gaps)
        if bit_time == 0:
            return transactions, violations

        # Walk through events looking for falling edges (potential start bits)
        i = 0
        while i < len(events) - 1:
            t, val = events[i]
            # Look for a falling edge to '0' from '1'
            if val == '0' and i > 0 and events[i - 1][1] == '1':
                # Potential start bit
                start_time = t

                # Sample 8 data bits at start_time + 1.5*bit_time + k*bit_time
                byte_val = 0
                valid_frame = True
                for bit_idx in range(8):
                    sample_time = start_time + bit_time + bit_idx * bit_time + bit_time // 2
                    bit_val = self._get_value_at_time(events, sample_time)
                    if bit_val == '1':
                        byte_val |= (1 << bit_idx)
                    elif bit_val != '0':
                        valid_frame = False
                        break

                # Check stop bit
                stop_sample_time = start_time + bit_time + 8 * bit_time + bit_time // 2
                stop_val = self._get_value_at_time(events, stop_sample_time)

                end_time = start_time + 10 * bit_time

                if not valid_frame:
                    violations.append({
                        'type': 'framing_error',
                        'time': fmt_time(start_time, self.ts_sec),
                        'time_ticks': start_time,
                        'severity': 'error',
                        'description': f'Invalid bit value in {line_name} frame at {fmt_time(start_time, self.ts_sec)}'
                    })
                elif stop_val != '1':
                    violations.append({
                        'type': 'stop_bit_error',
                        'time': fmt_time(start_time, self.ts_sec),
                        'time_ticks': start_time,
                        'severity': 'error',
                        'description': f'Missing stop bit on {line_name} at {fmt_time(start_time, self.ts_sec)}'
                    })
                else:
                    transactions.append({
                        'type': line_name,
                        'addr': 0,  # UART has no address
                        'addr_time': start_time,
                        'end_time': end_time,
                        'data': byte_val,
                        'data_ascii': chr(byte_val) if 32 <= byte_val < 127 else '.',
                        'status': 'OKAY',
                        'line': line_name,
                        'bit_time_ticks': bit_time
                    })

                # Skip past this frame
                while i < len(events) and events[i][0] < end_time:
                    i += 1
                continue
            i += 1

        return transactions, violations

    def _get_value_at_time(self, events, t):
        """Get value of line at time t from event list"""
        last_val = events[0][1] if events else 'x'
        for tt, val in events:
            if tt > t:
                break
            last_val = val
        return last_val

    def _compute_statistics(self, transactions, t0, t1):
        if not transactions:
            return {
                'total_transactions': 0,
                'tx_count': 0,
                'rx_count': 0,
                'bit_time': None,
            }

        tx_count = sum(1 for t in transactions if t['line'] == 'tx')
        rx_count = sum(1 for t in transactions if t['line'] == 'rx')

        # Estimated bit time = first transaction's measured bit_time
        bit_time = transactions[0].get('bit_time_ticks', 0)
        baud_rate = None
        if bit_time > 0:
            # baud = 1 / (bit_time * ts_sec)
            baud_rate = int(round(1.0 / (bit_time * self.ts_sec)))

        return {
            'total_transactions': len(transactions),
            'tx_count': tx_count,
            'rx_count': rx_count,
            'bit_time': fmt_time(bit_time, self.ts_sec) if bit_time else None,
            'bit_time_ticks': bit_time,
            'baud_rate': baud_rate
        }


class SPIDecoder(ProtocolDecoder):
    """SPI protocol decoder (Mode 0: CPOL=0, CPHA=0).

    Signal naming conventions:
    - sclk: contains 'sclk' or 'sck'
    - cs_n: contains 'cs' (chip select, active low)
    - mosi: contains 'mosi'
    - miso: contains 'miso'

    Mode 0: MOSI/MISO are sampled on the rising edge of SCLK.
    """

    def _identify_signals(self, pattern):
        matched_sids = self.vcd.match(pattern)
        if not matched_sids:
            raise ValueError(f"No signals matched pattern: {pattern}")

        signal_map = {}
        for sid in matched_sids:
            path = self.vcd.signals[sid]['path']
            signal_name = path.split('.')[-1].lower()

            if 'sclk' in signal_name or signal_name.endswith('_sck') or signal_name == 'sck':
                signal_map['sclk'] = sid
            elif 'mosi' in signal_name:
                signal_map['mosi'] = sid
            elif 'miso' in signal_name:
                signal_map['miso'] = sid
            elif 'cs' in signal_name and signal_name != 'sclk':
                # Avoid matching 'spi_sclk' which contains 's' followed by 'c'
                # More specific: cs_n, ncs, cs0, etc.
                if 'cs_n' in signal_name or signal_name.endswith('_cs') or 'ncs' in signal_name or signal_name == 'cs':
                    signal_map['cs_n'] = sid

        return signal_map

    def decode(self, t0, t1):
        """Decode SPI transactions delimited by CS_N going low/high."""
        transactions = []
        violations = []
        txn_id = 0

        # Validate required signals
        if 'sclk' not in self.signal_map:
            raise ValueError("SPI decoder requires sclk signal")

        # Get all signals
        sclk_sid = self.signal_map['sclk']
        cs_sid = self.signal_map.get('cs_n')
        mosi_sid = self.signal_map.get('mosi')
        miso_sid = self.signal_map.get('miso')

        # Collect all events
        all_sids = list(self.signal_map.values())
        events = list(self.vcd.iter_events(t0, t1, all_sids))

        # Current values
        current = {'sclk': '0', 'cs_n': '1', 'mosi': '0', 'miso': '0'}

        # Track active transaction
        active_txn = None
        bits_received_mosi = []
        bits_received_miso = []
        prev_sclk = '0'
        prev_cs = '1'

        for t, sid, val in events:
            # Map sid to name
            for name, s in self.signal_map.items():
                if s == sid:
                    current[name] = val
                    break

            sclk = current.get('sclk', '0')
            cs_n = current.get('cs_n', '1')

            # Detect CS_N falling edge: start of transaction
            if prev_cs == '1' and cs_n == '0':
                active_txn = {
                    'id': txn_id,
                    'type': 'spi',
                    'addr': 0,
                    'addr_time': t,
                    'status': 'pending',
                }
                txn_id += 1
                bits_received_mosi = []
                bits_received_miso = []
                prev_sclk = sclk  # Reset reference

            # SCLK rising edge: sample MOSI/MISO (Mode 0)
            if active_txn is not None and prev_sclk == '0' and sclk == '1':
                if mosi_sid is not None:
                    bits_received_mosi.append(current.get('mosi', '0'))
                if miso_sid is not None:
                    bits_received_miso.append(current.get('miso', '0'))

            # Detect CS_N rising edge: end of transaction
            if active_txn is not None and prev_cs == '0' and cs_n == '1':
                active_txn['end_time'] = t

                # Convert bits to bytes (MSB first)
                if bits_received_mosi:
                    mosi_val = self._bits_to_int(bits_received_mosi)
                    active_txn['mosi_data'] = mosi_val
                    active_txn['mosi_bits'] = len(bits_received_mosi)
                if bits_received_miso:
                    miso_val = self._bits_to_int(bits_received_miso)
                    active_txn['miso_data'] = miso_val
                    active_txn['miso_bits'] = len(bits_received_miso)

                active_txn['status'] = 'OKAY'
                transactions.append(active_txn)
                active_txn = None

            prev_sclk = sclk
            prev_cs = cs_n

        statistics = self._compute_statistics(transactions, t0, t1)
        return transactions, violations, statistics

    def _bits_to_int(self, bits):
        """Convert MSB-first bit list to integer"""
        val = 0
        for b in bits:
            val = (val << 1) | (1 if b == '1' else 0)
        return val

    def _compute_statistics(self, transactions, t0, t1):
        if not transactions:
            return {
                'total_transactions': 0,
                'avg_bits_per_txn': 0
            }

        total_bits = sum(t.get('mosi_bits', 0) for t in transactions)
        return {
            'total_transactions': len(transactions),
            'avg_bits_per_txn': total_bits / len(transactions) if transactions else 0
        }


def cmd_protocol_decode(vcd, args):
    """Protocol decode command handler"""
    def _run(args, started_at):
        ts = vcd.ts_sec
        try:
            t0 = parse_time(args.begin, ts) if args.begin else 0
            t1 = parse_time(args.end, ts) if args.end else None
        except _TimeParseError as e:
            raise SkillError('INVALID_TIME_RANGE', str(e))

        protocol = args.protocol.lower()

        # Normalize signal pattern
        if args.signals:
            try:
                signals = _normalize_filter_patterns(args.signals)
            except _FilterParseError as e:
                raise SkillError('INVALID_ARGUMENT',
                                  'invalid --signals pattern: {}'.format(e))
        else:
            signals = None

        # Create decoder
        try:
            if protocol == 'axi4':
                decoder = AXI4Decoder(vcd, signals)
            elif protocol == 'apb':
                decoder = APBDecoder(vcd, signals)
            elif protocol == 'uart':
                decoder = UARTDecoder(vcd, signals)
            elif protocol == 'spi':
                decoder = SPIDecoder(vcd, signals)
            else:
                raise SkillError(
                    'INVALID_PROTOCOL',
                    'Unsupported protocol: {}'.format(protocol),
                    {'supported': ['axi4', 'apb', 'uart', 'spi']})
        except ValueError as e:
            # Decoders raise ValueError when signal pattern matches nothing.
            raise SkillError('SIGNAL_NOT_FOUND', str(e),
                              {'pattern': signals})

        # Decode
        transactions, violations, statistics = decoder.decode(t0, t1)

        # Format transactions for output (protocol-specific)
        formatted_txns = _format_transactions(transactions, protocol, ts)

        # Generate suggestions
        suggestions = _generate_suggestions(transactions, violations, statistics, protocol)

        # Build standardized envelope
        signals_matched = len(decoder.signal_map) if hasattr(decoder, 'signal_map') else 0
        input_dict = {
            'file': vcd.path,
            'protocol': protocol,
            'signals': signals,
            'time_range': [fmt_time(t0, ts), fmt_time(t1, ts) if t1 else 'end']
        }
        result = {
            'transactions': formatted_txns,
            'violations': violations,
            'statistics': statistics,
        }
        metadata = _build_metadata(vcd, t0, t1, signals_matched=signals_matched)

        envelope = _skill_envelope(
            'protocol_decode', started_at, input_dict, result, metadata, suggestions)

        if getattr(args, 'json', False):
            _json(envelope)
        else:
            _print_protocol_result(envelope, protocol, statistics, formatted_txns,
                                    violations, suggestions, ts)
        return envelope

    return run_skill('protocol_decode', args, _run)


def _format_transactions(transactions, protocol, ts):
    """Format transactions for output based on protocol"""
    formatted = []
    for i, txn in enumerate(transactions):
        f = {
            'id': txn.get('id', i),
            'type': txn['type'],
            'start_time': fmt_time(txn['addr_time'], ts),
            'start_time_ticks': txn['addr_time'],
            'status': txn['status']
        }
        if 'end_time' in txn:
            f['end_time'] = fmt_time(txn['end_time'], ts)
            f['end_time_ticks'] = txn['end_time']

        if protocol == 'axi4':
            f['addr'] = f"0x{txn['addr']:X}"
            f['burst_len'] = txn['burst_len']
            if txn.get('data'):
                f['data'] = [f"0x{d:X}" for d in txn['data']]
        elif protocol == 'apb':
            f['addr'] = f"0x{txn['addr']:X}"
            if 'data' in txn:
                f['data'] = f"0x{txn['data']:X}"
        elif protocol == 'uart':
            f['line'] = txn.get('line')
            f['data'] = f"0x{txn['data']:02X}"
            f['data_ascii'] = txn.get('data_ascii')
        elif protocol == 'spi':
            if 'mosi_data' in txn:
                f['mosi'] = f"0x{txn['mosi_data']:0{(txn['mosi_bits']+3)//4}X}"
                f['mosi_bits'] = txn['mosi_bits']
            if 'miso_data' in txn:
                f['miso'] = f"0x{txn['miso_data']:0{(txn['miso_bits']+3)//4}X}"
                f['miso_bits'] = txn['miso_bits']

        formatted.append(f)
    return formatted


def _generate_suggestions(transactions, violations, statistics, protocol):
    """Generate protocol-specific suggestions"""
    suggestions = []
    if violations:
        suggestions.append(f"Found {len(violations)} protocol violation(s)")
    if not transactions:
        suggestions.append("No transactions found in specified time range")

    if protocol == 'axi4':
        util = statistics.get('bandwidth_utilization', 1.0)
        if util < 0.5 and transactions:
            suggestions.append(
                f"Low bandwidth utilization ({util*100:.0f}%), check for stalls"
            )
    elif protocol == 'apb':
        errors = statistics.get('error_count', 0)
        if errors > 0:
            suggestions.append(f"{errors} APB transaction(s) returned SLVERR")
    elif protocol == 'uart':
        if statistics.get('baud_rate'):
            suggestions.append(f"Auto-detected baud rate: {statistics['baud_rate']}")

    return suggestions


def _print_protocol_result(result, protocol, statistics, formatted_txns, violations, suggestions, ts):
    """Print text output for protocol decode result"""
    print(f"Protocol: {protocol.upper()}")
    print(f"Time range: {result['input']['time_range'][0]} ~ {result['input']['time_range'][1]}")

    if protocol == 'axi4':
        print(f"\nTransactions: {statistics['total_transactions']}")
        print(f"  Reads:  {statistics['read_count']}")
        print(f"  Writes: {statistics['write_count']}")
        if formatted_txns:
            print("\nTransaction Details:")
            for txn in formatted_txns:
                end_str = f" -> {txn['end_time']}" if 'end_time' in txn else " (pending)"
                print(f"  [{txn['id']}] {txn['type'].upper()}: {txn['addr']} @ {txn['start_time']}{end_str} [{txn['status']}]")
                if 'data' in txn:
                    print(f"      Data: {', '.join(txn['data'])}")
        print(f"\nStatistics:")
        print(f"  Avg Latency: {statistics.get('avg_latency') or 'N/A'}")
        print(f"  Bandwidth Utilization: {statistics['bandwidth_utilization']*100:.1f}%")

    elif protocol == 'apb':
        print(f"\nTransactions: {statistics['total_transactions']}")
        print(f"  Reads:  {statistics['read_count']}")
        print(f"  Writes: {statistics['write_count']}")
        print(f"  Errors: {statistics['error_count']}")
        if formatted_txns:
            print("\nTransaction Details:")
            for txn in formatted_txns:
                data_str = f" data={txn.get('data', '?')}" if 'data' in txn else ""
                print(f"  [{txn['id']}] {txn['type'].upper()}: {txn['addr']}{data_str} @ {txn['start_time']} [{txn['status']}]")
        print(f"\nStatistics:")
        print(f"  Avg Latency: {statistics.get('avg_latency') or 'N/A'}")

    elif protocol == 'uart':
        print(f"\nBytes decoded: {statistics['total_transactions']}")
        print(f"  TX: {statistics['tx_count']}")
        print(f"  RX: {statistics['rx_count']}")
        if statistics.get('baud_rate'):
            print(f"  Bit time: {statistics['bit_time']} (~{statistics['baud_rate']} baud)")
        if formatted_txns:
            print("\nBytes:")
            for txn in formatted_txns:
                print(f"  [{txn['id']}] {txn['line'].upper()}: {txn['data']} '{txn['data_ascii']}' @ {txn['start_time']}")

    elif protocol == 'spi':
        print(f"\nTransactions: {statistics['total_transactions']}")
        if formatted_txns:
            print("\nTransaction Details:")
            for txn in formatted_txns:
                mosi = txn.get('mosi', 'N/A')
                miso = txn.get('miso', 'N/A')
                print(f"  [{txn['id']}] @ {txn['start_time']} -> {txn.get('end_time', '?')}: MOSI={mosi}, MISO={miso}")

    if violations:
        print(f"\nViolations: {len(violations)}")
        for v in violations:
            print(f"  [{v['severity'].upper()}] {v['time']}: {v['description']}")

    if suggestions:
        print(f"\nSuggestions:")
        for s in suggestions:
            print(f"  - {s}")


# -- FSM Trace ---------------------------------------------------------------

class FSMTracer:
    """State machine tracer"""

    def __init__(self, vcd, state_signal):
        self.vcd = vcd
        self.ts_sec = vcd.ts_sec

        # Find state signal
        matched = vcd.match([state_signal])
        if not matched:
            raise ValueError(f"State signal not found: {state_signal}")
        if len(matched) > 1:
            raise ValueError(f"State signal pattern matched multiple signals: {state_signal}")

        self.state_sid = list(matched)[0]
        self.state_path = vcd.signals[self.state_sid]['path']

    def trace(self, t0, t1, stuck_threshold=100000):  # 100us default
        """Extract state transitions and detect anomalies"""
        transitions = []
        states_seen = {}  # value -> count

        current_state = None
        state_start_time = None
        prev_time = t0

        # Collect state transitions
        for t, sid, val in self.vcd.iter_events(t0, t1, [self.state_sid]):
            if current_state is not None:
                duration = t - state_start_time
                transitions.append({
                    'from': current_state,
                    'to': val,
                    'time': t,
                    'time_ticks': t,
                    'duration_in_from': duration,
                    'duration_in_from_ticks': duration
                })

                # Track state occurrences
                if current_state not in states_seen:
                    states_seen[current_state] = 0
                states_seen[current_state] += 1

            current_state = val
            state_start_time = t
            prev_time = t

        # Handle final state
        if current_state is not None and t1:
            final_duration = t1 - state_start_time
            if current_state not in states_seen:
                states_seen[current_state] = 0
            states_seen[current_state] += 1

        # Detect anomalies
        anomalies = self._detect_anomalies(transitions, stuck_threshold)

        # Compute statistics
        statistics = self._compute_statistics(transitions, states_seen, t0, t1)

        return transitions, anomalies, statistics

    def _detect_anomalies(self, transitions, stuck_threshold):
        """Detect FSM anomalies"""
        anomalies = []

        for trans in transitions:
            duration = trans['duration_in_from']

            # Stuck state: duration exceeds threshold
            if duration > stuck_threshold:
                anomalies.append({
                    'type': 'stuck_state',
                    'state': trans['from'],
                    'time': fmt_time(trans['time'] - duration, self.ts_sec),
                    'time_ticks': trans['time'] - duration,
                    'duration': fmt_time(duration, self.ts_sec),
                    'duration_ticks': duration,
                    'severity': 'warning',
                    'description': f"State {trans['from']} held for {fmt_time(duration, self.ts_sec)} (threshold: {fmt_time(stuck_threshold, self.ts_sec)})"
                })

        return anomalies

    def _compute_statistics(self, transitions, states_seen, t0, t1):
        """Compute FSM statistics"""
        if not transitions:
            return {
                'total_transitions': 0,
                'unique_states': len(states_seen),
                'states': []
            }

        # Calculate time spent in each state
        state_durations = {}
        for trans in transitions:
            state = trans['from']
            duration = trans['duration_in_from']

            if state not in state_durations:
                state_durations[state] = []
            state_durations[state].append(duration)

        # Format state statistics
        state_stats = []
        for state, durations in state_durations.items():
            total_time = sum(durations)
            avg_time = total_time / len(durations)
            min_time = min(durations)
            max_time = max(durations)

            state_stats.append({
                'state': state,
                'occurrences': len(durations),
                'total_time': fmt_time(total_time, self.ts_sec),
                'total_time_ticks': total_time,
                'avg_time': fmt_time(int(avg_time), self.ts_sec),
                'avg_time_ticks': int(avg_time),
                'min_time': fmt_time(min_time, self.ts_sec),
                'min_time_ticks': min_time,
                'max_time': fmt_time(max_time, self.ts_sec),
                'max_time_ticks': max_time
            })

        # Sort by total time (descending)
        state_stats.sort(key=lambda x: x['total_time_ticks'], reverse=True)

        return {
            'total_transitions': len(transitions),
            'unique_states': len(states_seen),
            'states': state_stats
        }


def cmd_fsm_trace(vcd, args):
    """FSM trace command handler"""
    def _run(args, started_at):
        ts = vcd.ts_sec
        try:
            t0 = parse_time(args.begin, ts) if args.begin else 0
            t1 = parse_time(args.end, ts) if args.end else None
            stuck_threshold = parse_time(args.stuck_threshold, ts) if args.stuck_threshold else 100000  # 100us
        except _TimeParseError as e:
            raise SkillError('INVALID_TIME_RANGE', str(e))

        state_signal = args.state

        # Create tracer
        try:
            tracer = FSMTracer(vcd, state_signal)
        except ValueError as e:
            raise SkillError('SIGNAL_NOT_FOUND', str(e),
                              {'pattern': state_signal})

        # Trace
        transitions, anomalies, statistics = tracer.trace(t0, t1, stuck_threshold)

        # Format transitions for output
        formatted_trans = []
        for i, trans in enumerate(transitions):
            formatted = {
                'id': i,
                'from': trans['from'],
                'to': trans['to'],
                'time': trans['time'],
                'time_ticks': trans['time_ticks'],
                'duration_in_from': fmt_time(trans['duration_in_from'], ts),
                'duration_in_from_ticks': trans['duration_in_from_ticks']
            }
            formatted_trans.append(formatted)

        # Generate suggestions
        suggestions = []
        if anomalies:
            suggestions.append(f"Found {len(anomalies)} anomaly(ies)")
            for anom in anomalies[:3]:  # Show first 3
                suggestions.append(f"State {anom['state']} stuck for {anom['duration']}")

        if statistics['total_transitions'] == 0:
            suggestions.append("No state transitions found in specified time range")

        # Build envelope
        input_dict = {
            'file': vcd.path,
            'state_signal': state_signal,
            'time_range': [fmt_time(t0, ts), fmt_time(t1, ts) if t1 else 'end'],
            'stuck_threshold': fmt_time(stuck_threshold, ts),
        }
        result = {
            'transitions': formatted_trans,
            'anomalies': anomalies,
            'statistics': statistics,
        }
        metadata = _build_metadata(vcd, t0, t1, signals_matched=1)
        envelope = _skill_envelope(
            'fsm_trace', started_at, input_dict, result, metadata, suggestions)

        if getattr(args, 'json', False):
            _json(envelope)
        else:
            # Text output
            print(f"State Signal: {tracer.state_path}")
            print(f"Time range: {input_dict['time_range'][0]} ~ {input_dict['time_range'][1]}")
            print(f"\nTransitions: {statistics['total_transitions']}")
            print(f"Unique States: {statistics['unique_states']}")

            if statistics['states']:
                print("\nState Statistics:")
                for st in statistics['states']:
                    print(f"  {st['state']}: {st['occurrences']} times, avg {st['avg_time']}, total {st['total_time']}")

            if formatted_trans:
                print(f"\nTransition Details (showing first 20):")
                for trans in formatted_trans[:20]:
                    print(f"  [{trans['id']}] {trans['from']} -> {trans['to']} @ {trans['time']} (held {trans['duration_in_from']})")
                if len(formatted_trans) > 20:
                    print(f"  ... and {len(formatted_trans) - 20} more")

            if anomalies:
                print(f"\nAnomalies: {len(anomalies)}")
                for anom in anomalies:
                    print(f"  [{anom['severity'].upper()}] {anom['time']}: {anom['description']}")

            if suggestions:
                print(f"\nSuggestions:")
                for s in suggestions:
                    print(f"  - {s}")
        return envelope

    return run_skill('fsm_trace', args, _run)


# -- Causality Analysis ------------------------------------------------------

class CausalityAnalyzer:
    """Analyze potential causes for a signal change.

    The analyzer searches for signals that changed before the effect time
    within a configurable window, then ranks them by:
    1. Temporal proximity (closer in time = more likely cause)
    2. Historical correlation (does this pattern repeat?)
    3. Value change direction matching
    """

    def __init__(self, vcd):
        self.vcd = vcd
        self.ts_sec = vcd.ts_sec

    def analyze(self, effect_signal, effect_time, window):
        """Find potential causes for effect_signal change at effect_time.

        Args:
            effect_signal: signal pattern (must match exactly one signal)
            effect_time: time of the effect (in ticks)
            window: search window before effect_time (in ticks)

        Returns:
            (effect_info, potential_causes, causal_chain)
        """
        # Resolve effect signal
        matched = self.vcd.match([effect_signal])
        if not matched:
            raise ValueError(f"Effect signal not found: {effect_signal}")
        if len(matched) > 1:
            raise ValueError(f"Effect signal matched multiple signals: {effect_signal}")
        effect_sid = list(matched)[0]
        effect_path = self.vcd.signals[effect_sid]['path']

        # Find the effect value at effect_time
        effect_value = self._get_value_at(effect_sid, effect_time)

        effect_info = {
            'signal': effect_path,
            'time': fmt_time(effect_time, self.ts_sec),
            'time_ticks': effect_time,
            'value': effect_value
        }

        # Identify clock-like signals (high frequency toggling) to filter them out.
        # A signal is considered a clock if it toggles much more frequently than
        # the effect signal, since clocks are usually not the *cause* of an event,
        # just a synchronizer.
        clock_sids = self._identify_clock_signals(effect_sid)

        # Collect all signal changes in the search window
        t0 = max(0, effect_time - window)
        t1 = effect_time

        # Group changes by signal
        signal_changes = {}  # sid -> [(time, value), ...]
        for t, sid, val in self.vcd.iter_events(t0, t1):
            if sid == effect_sid:
                continue  # Skip the effect signal itself
            if sid in clock_sids:
                continue  # Skip clock signals
            if sid not in signal_changes:
                signal_changes[sid] = []
            signal_changes[sid].append((t, val))

        # Compute correlation for each candidate signal
        candidates = []
        for sid, changes in signal_changes.items():
            if not changes:
                continue

            # Use the latest change before effect_time
            last_change_time, last_value = changes[-1]
            delta = effect_time - last_change_time

            # Temporal proximity score (closer = higher)
            # Linear decay: 1.0 at delta=0, 0.0 at delta=window
            temporal_score = max(0.0, 1.0 - (delta / window)) if window > 0 else 0.0

            # Historical correlation: check if this signal change pattern
            # has historically preceded effect signal changes
            historical_score, occurrences, total_effect_events = self._compute_historical_correlation(
                sid, effect_sid, window)

            # Combined correlation (weighted average)
            # Temporal proximity is the primary signal; historical correlation
            # is a tiebreaker and confidence booster
            correlation = 0.4 * temporal_score + 0.6 * historical_score

            # Confidence based on number of occurrences
            if occurrences >= 5:
                confidence = 'high'
            elif occurrences >= 2:
                confidence = 'medium'
            else:
                confidence = 'low'

            sig_path = self.vcd.signals[sid]['path']

            candidates.append({
                'signal': sig_path,
                'change_time': fmt_time(last_change_time, self.ts_sec),
                'change_time_ticks': last_change_time,
                'delta': fmt_time(delta, self.ts_sec),
                'delta_ticks': delta,
                'value': fmt_val(last_value, self.vcd.signals[sid]),
                'correlation': round(correlation, 3),
                'temporal_score': round(temporal_score, 3),
                'historical_score': round(historical_score, 3),
                'confidence': confidence,
                'pattern': f"{sig_path} changed -> {effect_path} changed (observed {occurrences}/{total_effect_events} times)"
            })

        # Sort by correlation (descending)
        candidates.sort(key=lambda x: x['correlation'], reverse=True)

        # Filter low-correlation candidates (< 0.1)
        candidates = [c for c in candidates if c['correlation'] >= 0.1]

        # Build causal chain from top candidates
        causal_chain = self._build_causal_chain(candidates, effect_info, signal_changes)

        return effect_info, candidates, causal_chain

    def _identify_clock_signals(self, effect_sid):
        """Identify clock-like signals (frequent regular toggling).

        A signal is considered clock-like if:
        1. It has many more transitions than the effect signal (>= 10x)
        2. Its transitions are roughly regular in interval

        Returns a set of signal IDs to exclude.
        """
        clock_sids = set()

        # Count transitions for effect signal
        effect_count = sum(1 for _ in self.vcd.iter_events(0, None, [effect_sid]))
        if effect_count == 0:
            return clock_sids

        # For each signal, check if it toggles much more frequently
        for sid in self.vcd.signals:
            if sid == effect_sid:
                continue

            # Quick check: name contains common clock identifiers
            sig_path = self.vcd.signals[sid]['path'].lower()
            sig_name = sig_path.split('.')[-1]
            if sig_name in ('clk', 'clock', 'ck') or sig_name.endswith('_clk') or sig_name.endswith('_clock'):
                clock_sids.add(sid)
                continue

            # Frequency check: count transitions
            count = sum(1 for _ in self.vcd.iter_events(0, None, [sid]))
            if count >= effect_count * 10 and count >= 20:
                # Very high frequency: likely a clock
                clock_sids.add(sid)

        return clock_sids

    def _get_value_at(self, sid, t):
        """Get the value of a signal at a specific time"""
        last_value = None
        for tt, ss, val in self.vcd.iter_events(0, t + 1, [sid]):
            if tt > t:
                break
            last_value = val
        return fmt_val(last_value, self.vcd.signals[sid]) if last_value else 'unknown'

    def _compute_historical_correlation(self, candidate_sid, effect_sid, window):
        """Check how often candidate signal changes precede effect signal changes.

        Returns:
            (correlation_score, matching_occurrences, total_effect_events)
        """
        # Collect all changes of the effect signal
        effect_times = []
        for t, sid, val in self.vcd.iter_events(0, None, [effect_sid]):
            effect_times.append(t)

        if len(effect_times) < 2:
            # Not enough history to compute correlation
            return 0.0, 0, len(effect_times)

        # For each effect event, check if candidate changed within window before it
        candidate_times = []
        for t, sid, val in self.vcd.iter_events(0, None, [candidate_sid]):
            candidate_times.append(t)

        if not candidate_times:
            return 0.0, 0, len(effect_times)

        # Count matches: candidate change followed by effect change within window
        matches = 0
        for effect_t in effect_times:
            # Look for candidate change in [effect_t - window, effect_t]
            t_low = max(0, effect_t - window)
            for cand_t in candidate_times:
                if t_low <= cand_t <= effect_t:
                    matches += 1
                    break  # One match per effect event

        # Correlation = matches / total effect events
        correlation = matches / len(effect_times) if effect_times else 0.0
        return correlation, matches, len(effect_times)

    def _build_causal_chain(self, candidates, effect_info, signal_changes):
        """Build a causal chain showing signal changes leading to the effect.

        The chain is constructed from the top candidates by sorting them
        chronologically.
        """
        if not candidates:
            return []

        # Take top 5 candidates
        top_candidates = candidates[:5]

        # Build chain entries
        chain_entries = []
        for cand in top_candidates:
            chain_entries.append({
                'signal': cand['signal'],
                'time': cand['change_time'],
                'time_ticks': cand['change_time_ticks'],
                'value': cand['value']
            })

        # Add the effect at the end
        chain_entries.append({
            'signal': effect_info['signal'],
            'time': effect_info['time'],
            'time_ticks': effect_info['time_ticks'],
            'value': effect_info['value']
        })

        # Sort chronologically
        chain_entries.sort(key=lambda x: x['time_ticks'])

        return chain_entries


def cmd_causality(vcd, args):
    """Causality analysis command handler"""
    def _run(args, started_at):
        ts = vcd.ts_sec

        # Parse effect time and window
        try:
            effect_time = parse_time(args.at, ts)
            if args.window:
                window = parse_time(args.window, ts)
            else:
                window = parse_time('100ns', ts)
        except _TimeParseError as e:
            raise SkillError('INVALID_TIME_RANGE', str(e))

        # Create analyzer
        analyzer = CausalityAnalyzer(vcd)

        # Analyze
        try:
            effect_info, candidates, causal_chain = analyzer.analyze(
                args.effect, effect_time, window)
        except ValueError as e:
            raise SkillError('SIGNAL_NOT_FOUND', str(e),
                              {'pattern': args.effect})

        # Generate suggestions
        suggestions = []
        if candidates:
            top = candidates[0]
            if top['correlation'] >= 0.7:
                suggestions.append(
                    f"High correlation with {top['signal']} ({top['correlation']:.0%}), "
                    f"likely root cause"
                )
            elif top['correlation'] >= 0.4:
                suggestions.append(
                    f"Moderate correlation with {top['signal']} ({top['correlation']:.0%}), "
                    f"investigate further"
                )
            else:
                suggestions.append(
                    f"Weak correlation with top candidate {top['signal']} "
                    f"({top['correlation']:.0%}), consider expanding search window"
                )

            if len(candidates) > 1:
                suggestions.append(
                    f"Found {len(candidates)} potential causes; "
                    f"check causal_chain for temporal ordering"
                )
        else:
            suggestions.append(
                "No correlated signals found in search window; "
                "try expanding --window or check for spontaneous events"
            )

        # Build envelope
        input_dict = {
            'file': vcd.path,
            'effect_signal': args.effect,
            'effect_time': fmt_time(effect_time, ts),
            'effect_time_ticks': effect_time,
            'search_window': fmt_time(window, ts),
            'search_window_ticks': window,
        }
        result = {
            'effect': effect_info,
            'potential_causes': candidates,
            'causal_chain': causal_chain,
        }
        metadata = _build_metadata(
            vcd, max(0, effect_time - window), effect_time,
            signals_matched=len(candidates))
        envelope = _skill_envelope(
            'causality', started_at, input_dict, result, metadata, suggestions)

        if getattr(args, 'json', False):
            _json(envelope)
        else:
            # Text output
            print(f"Effect Signal: {effect_info['signal']}")
            print(f"Effect Time:   {effect_info['time']} (value={effect_info['value']})")
            print(f"Search Window: {fmt_time(window, ts)} before effect")

            if candidates:
                print(f"\nPotential Causes: {len(candidates)} found")
                print(f"  {'#':<3} {'Signal':<50} {'Delta':<10} {'Value':<10} {'Corr':<8} {'Confidence'}")
                for i, c in enumerate(candidates[:10]):
                    print(f"  {i:<3} {c['signal']:<50} {c['delta']:<10} {str(c['value']):<10} "
                          f"{c['correlation']:<8} {c['confidence']}")
                if len(candidates) > 10:
                    print(f"  ... and {len(candidates) - 10} more")
            else:
                print("\nNo correlated signals found in search window.")

            if causal_chain:
                print(f"\nCausal Chain (chronological):")
                for entry in causal_chain:
                    marker = " <-- EFFECT" if entry['signal'] == effect_info['signal'] else ""
                    print(f"  {entry['time']:<12} {entry['signal']:<50} = {entry['value']}{marker}")

            if suggestions:
                print(f"\nSuggestions:")
                for s in suggestions:
                    print(f"  - {s}")
        return envelope

    return run_skill('causality', args, _run)


# -- Anomaly Detection -------------------------------------------------------

class AnomalyDetector:
    """Detect common waveform anomalies.

    Supported anomaly types:
    - stuck_signal: signal does not change for an extended period
    - glitch: pulse narrower than minimum width
    - metastability: x/z values observed during simulation
    - bus_contention: all-x or all-z values on multi-bit signals
    """

    def __init__(self, vcd):
        self.vcd = vcd
        self.ts_sec = vcd.ts_sec

    def detect(self, t0, t1, sids=None,
               stuck_threshold=None,
               glitch_threshold=None,
               check_metastability=True,
               check_bus_contention=True):
        """Run all anomaly detectors and return findings.

        Args:
            t0, t1: time range
            sids: optional set of signal IDs to check
            stuck_threshold: ticks beyond which a static signal is "stuck"
                             (default: 50% of analysis window or 100us)
            glitch_threshold: ticks below which a pulse is a "glitch"
                              (default: 5ns)
            check_metastability: detect x/z values
            check_bus_contention: detect all-x/z on multi-bit signals

        Returns:
            list of anomaly dicts
        """
        # Default thresholds
        if t1 is None:
            t1 = self.vcd.scan_time_range()[1] or 0
        analysis_window = t1 - t0

        if stuck_threshold is None:
            # 50% of analysis window, but not less than 100us
            stuck_threshold = max(analysis_window // 2,
                                  int(100e-6 / self.ts_sec))
        if glitch_threshold is None:
            # 5ns default
            glitch_threshold = int(5e-9 / self.ts_sec)

        # Determine which signals to check
        if sids is None:
            sids = set(self.vcd.signals.keys())
        else:
            sids = set(sids)

        anomalies = []

        # Collect all events into per-signal change lists
        signal_events = {sid: [] for sid in sids}
        for t, sid, val in self.vcd.iter_events(t0, t1, sids):
            signal_events[sid].append((t, val))

        # Run each detector
        for sid in sids:
            events = signal_events[sid]
            info = self.vcd.signals[sid]
            path = info['path']
            width = info['width']

            # Detect stuck signals
            self._detect_stuck(anomalies, sid, path, events, t0, t1, stuck_threshold)

            # Detect glitches (only single-bit signals)
            if width == 1:
                self._detect_glitch(anomalies, sid, path, events, glitch_threshold)

            # Detect metastability / unknown values
            if check_metastability:
                self._detect_unknown_values(anomalies, sid, path, events, width,
                                             check_bus_contention)

        # Sort by time (earliest first)
        anomalies.sort(key=lambda a: a.get('time_ticks', 0))

        # Compute summary
        summary = self._compute_summary(anomalies)

        return anomalies, summary

    def _detect_stuck(self, anomalies, sid, path, events, t0, t1, threshold):
        """Detect signals that don't change for >= threshold ticks.

        We consider two cases:
        1. Signal has no events in window: stuck for the entire window
        2. Signal has long gaps between events
        """
        if not events:
            # No changes at all -- check if the signal exists earlier
            # Signal is stuck for entire window
            duration = t1 - t0
            if duration >= threshold:
                anomalies.append({
                    'type': 'stuck_signal',
                    'signal': path,
                    'time': fmt_time(t0, self.ts_sec),
                    'time_ticks': t0,
                    'time_range': [fmt_time(t0, self.ts_sec), fmt_time(t1, self.ts_sec)],
                    'duration': fmt_time(duration, self.ts_sec),
                    'duration_ticks': duration,
                    'severity': self._stuck_severity(duration, threshold),
                    'description': f"Signal stuck (no changes) for {fmt_time(duration, self.ts_sec)}"
                })
            return

        # Check gap from window start to first event
        first_t, _ = events[0]
        gap = first_t - t0
        if gap >= threshold:
            anomalies.append({
                'type': 'stuck_signal',
                'signal': path,
                'time': fmt_time(t0, self.ts_sec),
                'time_ticks': t0,
                'time_range': [fmt_time(t0, self.ts_sec), fmt_time(first_t, self.ts_sec)],
                'duration': fmt_time(gap, self.ts_sec),
                'duration_ticks': gap,
                'severity': self._stuck_severity(gap, threshold),
                'description': f"Signal stuck for {fmt_time(gap, self.ts_sec)} before first change"
            })

        # Check gaps between consecutive events
        for i in range(len(events) - 1):
            t_curr, _ = events[i]
            t_next, _ = events[i + 1]
            gap = t_next - t_curr
            if gap >= threshold:
                anomalies.append({
                    'type': 'stuck_signal',
                    'signal': path,
                    'time': fmt_time(t_curr, self.ts_sec),
                    'time_ticks': t_curr,
                    'time_range': [fmt_time(t_curr, self.ts_sec), fmt_time(t_next, self.ts_sec)],
                    'duration': fmt_time(gap, self.ts_sec),
                    'duration_ticks': gap,
                    'severity': self._stuck_severity(gap, threshold),
                    'description': f"Signal stuck for {fmt_time(gap, self.ts_sec)}"
                })

        # Check gap from last event to window end
        last_t, _ = events[-1]
        gap = t1 - last_t
        if gap >= threshold:
            anomalies.append({
                'type': 'stuck_signal',
                'signal': path,
                'time': fmt_time(last_t, self.ts_sec),
                'time_ticks': last_t,
                'time_range': [fmt_time(last_t, self.ts_sec), fmt_time(t1, self.ts_sec)],
                'duration': fmt_time(gap, self.ts_sec),
                'duration_ticks': gap,
                'severity': self._stuck_severity(gap, threshold),
                'description': f"Signal stuck for {fmt_time(gap, self.ts_sec)} until window end"
            })

    def _stuck_severity(self, duration, threshold):
        """Classify stuck severity based on how long it's been stuck"""
        if duration >= threshold * 10:
            return 'critical'
        elif duration >= threshold * 3:
            return 'error'
        else:
            return 'warning'

    def _detect_glitch(self, anomalies, sid, path, events, threshold):
        """Detect pulses (single-bit signals) narrower than threshold.

        A glitch is a 0->1->0 or 1->0->1 pattern with intermediate state
        held for less than threshold.
        """
        if len(events) < 2:
            return

        for i in range(len(events) - 1):
            t_start, val_start = events[i]
            t_end, val_end = events[i + 1]
            pulse_width = t_end - t_start

            # Glitch: short pulse with returning value
            if pulse_width < threshold and pulse_width > 0:
                # Check if this is actually a pulse (value returns to original)
                if i + 2 < len(events):
                    _, val_after = events[i + 2]
                else:
                    val_after = None

                # A true glitch returns to the original value
                # We focus on isolated short pulses
                if val_start in ('0', '1') and val_end in ('0', '1') and val_start != val_end:
                    anomalies.append({
                        'type': 'glitch',
                        'signal': path,
                        'time': fmt_time(t_start, self.ts_sec),
                        'time_ticks': t_start,
                        'duration': fmt_time(pulse_width, self.ts_sec),
                        'duration_ticks': pulse_width,
                        'severity': 'warning',
                        'description': f"Pulse width {fmt_time(pulse_width, self.ts_sec)} < minimum expected {fmt_time(threshold, self.ts_sec)}"
                    })

    def _detect_unknown_values(self, anomalies, sid, path, events, width, check_bus):
        """Detect x/z values (metastability) and bus contention"""
        for t, val in events:
            val_lower = val.lower() if val else ''
            if not val_lower:
                continue

            # Check for any x/z characters
            has_x = 'x' in val_lower
            has_z = 'z' in val_lower

            if not (has_x or has_z):
                continue

            # Determine if it's full unknown (bus contention) or partial (metastability)
            # Strip leading 'b' from binary representation
            clean_val = val_lower.lstrip('b')

            if width > 1 and check_bus:
                # Multi-bit signal: check if all bits are x or z
                all_unknown = all(c in ('x', 'z') for c in clean_val if c != ' ')
                if all_unknown and len(clean_val) > 0:
                    anomalies.append({
                        'type': 'bus_contention',
                        'signal': path,
                        'time': fmt_time(t, self.ts_sec),
                        'time_ticks': t,
                        'value': val,
                        'severity': 'error',
                        'description': f"Bus contention detected (all bits unknown: {val})"
                    })
                    continue

            # Otherwise it's metastability (some bits unknown)
            anomalies.append({
                'type': 'metastability',
                'signal': path,
                'time': fmt_time(t, self.ts_sec),
                'time_ticks': t,
                'value': val,
                'severity': 'error',
                'description': f"Unknown value detected: {val}"
            })

    def _compute_summary(self, anomalies):
        """Summarize anomalies by type and severity"""
        summary = {
            'total_anomalies': len(anomalies),
            'critical': 0,
            'error': 0,
            'warning': 0,
            'by_type': {}
        }

        for a in anomalies:
            severity = a.get('severity', 'warning')
            anom_type = a.get('type', 'unknown')

            if severity in summary:
                summary[severity] += 1

            if anom_type not in summary['by_type']:
                summary['by_type'][anom_type] = 0
            summary['by_type'][anom_type] += 1

        return summary


def cmd_anomaly_detect(vcd, args):
    """Anomaly detection command handler"""
    def _run(args, started_at):
        ts = vcd.ts_sec
        try:
            t0 = parse_time(args.begin, ts) if args.begin else 0
            t1 = parse_time(args.end, ts) if args.end else None

            # Parse thresholds
            stuck_threshold = None
            if args.stuck_threshold:
                stuck_threshold = parse_time(args.stuck_threshold, ts)

            glitch_threshold = None
            if args.glitch_threshold:
                glitch_threshold = parse_time(args.glitch_threshold, ts)
        except _TimeParseError as e:
            raise SkillError('INVALID_TIME_RANGE', str(e))

        # Determine which signals to check
        try:
            sids = vcd.match(args.filter)
        except _FilterParseError as e:
            raise SkillError('INVALID_ARGUMENT',
                              'invalid --filter pattern: {}'.format(e))

        # Run detection
        detector = AnomalyDetector(vcd)
        anomalies, summary = detector.detect(
            t0, t1, sids=sids,
            stuck_threshold=stuck_threshold,
            glitch_threshold=glitch_threshold)

        # Generate suggestions
        suggestions = []
        if summary['critical'] > 0:
            suggestions.append(
                f"Critical: {summary['critical']} critical anomaly(ies) found, "
                f"investigate immediately"
            )
        if summary['by_type'].get('metastability', 0) > 0:
            suggestions.append(
                f"{summary['by_type']['metastability']} metastability issue(s); "
                f"review CDC (Clock Domain Crossing) design"
            )
        if summary['by_type'].get('bus_contention', 0) > 0:
            suggestions.append(
                f"{summary['by_type']['bus_contention']} bus contention(s); "
                f"check for multiple drivers"
            )
        if summary['by_type'].get('glitch', 0) > 0:
            suggestions.append(
                f"{summary['by_type']['glitch']} glitch(es); "
                f"check pulse width requirements"
            )
        if summary['by_type'].get('stuck_signal', 0) > 0:
            suggestions.append(
                f"{summary['by_type']['stuck_signal']} stuck signal(s); "
                f"verify expected activity"
            )
        if summary['total_anomalies'] == 0:
            suggestions.append("No anomalies detected; waveform appears clean")

        # Compute defaults used (for transparency in input echo)
        if t1 is None:
            t1 = vcd.scan_time_range()[1] or 0
        if stuck_threshold is None:
            stuck_threshold = max((t1 - t0) // 2, int(100e-6 / ts))
        if glitch_threshold is None:
            glitch_threshold = int(5e-9 / ts)

        signals_analyzed = len(sids) if sids is not None else len(vcd.signals)
        input_dict = {
            'file': vcd.path,
            'time_range': [fmt_time(t0, ts), fmt_time(t1, ts) if t1 else 'end'],
            'signals_analyzed': signals_analyzed,
            'stuck_threshold': fmt_time(stuck_threshold, ts),
            'glitch_threshold': fmt_time(glitch_threshold, ts),
        }
        result = {
            'anomalies': anomalies,
            'summary': summary,
        }
        metadata = _build_metadata(vcd, t0, t1, signals_matched=signals_analyzed)
        envelope = _skill_envelope(
            'anomaly_detect', started_at, input_dict, result, metadata, suggestions)

        if getattr(args, 'json', False):
            _json(envelope)
        else:
            # Text output
            print(f"Anomaly Detection Report")
            print(f"Time range: {input_dict['time_range'][0]} ~ {input_dict['time_range'][1]}")
            print(f"Signals analyzed: {input_dict['signals_analyzed']}")
            print(f"Thresholds: stuck >= {input_dict['stuck_threshold']}, "
                  f"glitch < {input_dict['glitch_threshold']}")

            print(f"\nSummary:")
            print(f"  Total anomalies: {summary['total_anomalies']}")
            print(f"  Critical: {summary['critical']}")
            print(f"  Error:    {summary['error']}")
            print(f"  Warning:  {summary['warning']}")

            if summary['by_type']:
                print(f"\nBy type:")
                for anom_type, count in sorted(summary['by_type'].items()):
                    print(f"  {anom_type}: {count}")

            if anomalies:
                print(f"\nAnomalies (showing first 20):")
                for a in anomalies[:20]:
                    sev = a.get('severity', 'warning').upper()
                    print(f"  [{sev:<8}] {a['time']:<12} {a['type']:<18} {a['signal']}")
                    print(f"             {a['description']}")
                if len(anomalies) > 20:
                    print(f"  ... and {len(anomalies) - 20} more")

            if suggestions:
                print(f"\nSuggestions:")
                for s in suggestions:
                    print(f"  - {s}")
        return envelope

    return run_skill('anomaly_detect', args, _run)


# -- CLI entry ---------------------------------------------------------------

def _add_time_args(sp):
    sp.add_argument('--begin', metavar='TIME',
                    help='start time, e.g. 0, 100ns, 17.5us (omit = from start)')
    sp.add_argument('--end', metavar='TIME',
                    help='end time, same format (omit = no upper bound)')


def _add_filter(sp):
    sp.add_argument('--filter', metavar='K1,K2,...',
                    type=_normalize_filter_patterns,
                    help='comma-separated substring/glob patterns, case-insensitive')


def _add_common(sp):
    # Also accept global-style output controls after the subcommand.
    # Defaults are SUPPRESS so values supplied before the subcommand survive.
    sp.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                    help='output compact structured JSON instead of text')
    sp.add_argument('--limit', type=int, default=argparse.SUPPRESS,
                    help='max rows/records to emit; default 200; 0 = unlimited; streaming commands stop after the first unshown result')
    sp.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS,
                    help='show extra fields; if --limit is omitted, disables truncation')


def _load_skill_manifest():
    """Load vcd_skill_manifest.json from the package directory."""
    manifest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'vcd_skill_manifest.json')
    if not os.path.isfile(manifest_path):
        sys.exit('Error: skill manifest not found at {}'.format(manifest_path))
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit('Error: cannot load skill manifest: {}'.format(e))


def _handle_skill_manifest(args):
    """Handle --skill-manifest / --skill-info <name>.

    --skill-manifest dumps the entire manifest as JSON.
    --skill-info <name> dumps just one capability block, or exits non-zero
    if the requested skill is not in the manifest.
    """
    manifest = _load_skill_manifest()

    if args.skill_info:
        target = args.skill_info
        for cap in manifest.get('capabilities', []):
            if cap.get('skill') == target or cap.get('command') == target:
                _json(cap)
                return
        sys.exit("Error: unknown skill '{}'; known: {}".format(
            target, ', '.join(c['skill'] for c in manifest.get('capabilities', []))))

    # Default: print the whole manifest
    _json(manifest)


def main():
    p = argparse.ArgumentParser(
        prog='vcd_analyzer',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--json', action='store_true',
                   help='output compact structured JSON instead of text')
    p.add_argument('--limit', type=int, default=None,
                   help='max rows/records to emit; default 200; 0 = unlimited; streaming commands stop after the first unshown result')
    p.add_argument('--verbose', action='store_true',
                   help='show extra fields; if --limit is omitted, disables truncation')
    p.add_argument('--version', action='version', version='%(prog)s ' + __version__)

    # Skill discovery: --skill-manifest dumps the full manifest as JSON,
    # --skill-info <name> dumps just one skill's entry. These exit before
    # subcommand parsing so they don't conflict with normal usage.
    p.add_argument('--skill-manifest', action='store_true',
                   help='print the Skill manifest (vcd_skill_manifest.json) and exit')
    p.add_argument('--skill-info', metavar='NAME',
                   help='print one Skill capability entry by name and exit (e.g. protocol_decode)')

    sub = p.add_subparsers(dest='cmd', metavar='<command>')

    sp = sub.add_parser('info', help='file overview: timescale, signal count, time span, scopes')
    sp.add_argument('file', metavar='<file>', help='VCD file path'); _add_common(sp)

    sp = sub.add_parser('list', help='list signals with path and bit width')
    sp.add_argument('file', metavar='<file>'); _add_filter(sp); _add_common(sp)

    sp = sub.add_parser('dump', help='print value-change events in time order')
    sp.add_argument('file', metavar='<file>'); _add_time_args(sp); _add_filter(sp); _add_common(sp)

    sp = sub.add_parser('summary', help='window stats: active/static/undefined selected signals')
    sp.add_argument('file', metavar='<file>'); _add_time_args(sp); _add_filter(sp); _add_common(sp)

    sp = sub.add_parser('snapshot', help='known signal values at a given time point')
    sp.add_argument('file', metavar='<file>')
    sp.add_argument('--at', metavar='TIME', required=True, help='time point, e.g. 17.55us')
    _add_filter(sp); _add_common(sp)

    sp = sub.add_parser('compare', help='diff known signal values between two time points')
    sp.add_argument('file', metavar='<file>')
    sp.add_argument('--at', metavar='T1,T2', required=True, help='two time points comma-separated, e.g. 17.5us,17.7us')
    _add_filter(sp); _add_common(sp)

    sp = sub.add_parser('search', help='conditional search and associated signal observation')
    sp.add_argument('file', metavar='<file>'); _add_time_args(sp); _add_common(sp)
    sp.add_argument('--condition', metavar='COND', required=True,
                    help='comma-separated AND conditions, e.g. "valid=1,ready=1"; != does not match x/z/undef')
    sp.add_argument('--show', metavar='PAT1,PAT2,...', type=_normalize_filter_patterns,
                    help='signals to display while the condition holds; output segments split when shown values change')
    sp.add_argument('--changed', metavar='PATTERN',
                    help='emit events only when this signal really changes; VCD event vars count each trigger; must match exactly one signal')

    sp = sub.add_parser('protocol-decode', help='decode bus protocol transactions (AXI4/APB/UART/SPI)')
    sp.add_argument('file', metavar='<file>')
    sp.add_argument('--protocol', metavar='TYPE', required=True,
                    help='protocol type: axi4, apb, uart, spi')
    sp.add_argument('--signals', metavar='PATTERN',
                    help='signal pattern to match (e.g., m_axi_*, s_apb_*); default: *')
    _add_time_args(sp); _add_common(sp)

    sp = sub.add_parser('fsm-trace', help='trace state machine transitions and detect anomalies')
    sp.add_argument('file', metavar='<file>')
    sp.add_argument('--state', metavar='SIGNAL', required=True,
                    help='state signal name or pattern (e.g., state, fsm_state[2:0])')
    sp.add_argument('--stuck-threshold', metavar='TIME',
                    help='threshold for stuck state detection (e.g., 100us); default: 100us')
    _add_time_args(sp); _add_common(sp)

    sp = sub.add_parser('causality', help='find potential causes for a signal change')
    sp.add_argument('file', metavar='<file>')
    sp.add_argument('--effect', metavar='SIGNAL', required=True,
                    help='effect signal name or pattern (must match exactly one signal)')
    sp.add_argument('--at', metavar='TIME', required=True,
                    help='time when the effect occurred (e.g., 17.5us)')
    sp.add_argument('--window', metavar='DURATION',
                    help='search window before effect time (e.g., 100ns); default: 100ns')
    _add_common(sp)

    sp = sub.add_parser('anomaly-detect', help='detect waveform anomalies (stuck, glitch, metastability, bus contention)')
    sp.add_argument('file', metavar='<file>')
    sp.add_argument('--stuck-threshold', metavar='TIME',
                    help='ticks beyond which a static signal is "stuck"; default: 50%% of window or 100us')
    sp.add_argument('--glitch-threshold', metavar='TIME',
                    help='pulse width below which a single-bit transition is a "glitch"; default: 5ns')
    _add_time_args(sp); _add_filter(sp); _add_common(sp)

    args = p.parse_args()

    # Skill manifest discovery (exits before subcommand dispatch)
    if args.skill_manifest or args.skill_info:
        _handle_skill_manifest(args)
        return

    if not args.cmd:
        p.print_help()
        sys.exit(1)

    try:
        vcd = VCDParser(args.file)
        cmds = {'info': cmd_info, 'list': cmd_list, 'dump': cmd_dump, 'summary': cmd_summary,
                'snapshot': cmd_snapshot, 'compare': cmd_compare, 'search': cmd_search,
                'protocol-decode': cmd_protocol_decode, 'fsm-trace': cmd_fsm_trace,
                'causality': cmd_causality, 'anomaly-detect': cmd_anomaly_detect}
        cmds[args.cmd](vcd, args)
    except FileNotFoundError as e:
        sys.exit('Error: cannot open VCD file: {}'.format(e.filename or args.file))
    except IsADirectoryError as e:
        sys.exit('Error: not a file: {}'.format(e.filename or args.file))
    except PermissionError as e:
        sys.exit('Error: permission denied: {}'.format(e.filename or args.file))
    except _TimeParseError as e:
        sys.exit('Error: ' + str(e))
    except _ValueParseError as e:
        sys.exit('Error: ' + str(e))
    except _ConditionParseError as e:
        sys.exit('Error: ' + str(e))
    except _VCDResourceError as e:
        sys.exit('Error: ' + str(e))
    except _FilterParseError as e:
        # Reaches here only if raised from VCDParser.match() at runtime;
        # argparse handles the same error when raised from type=.
        sys.exit('Error: ' + str(e))


if __name__ == '__main__':
    import signal as _sig
    if hasattr(_sig, 'SIGPIPE'):
        _sig.signal(_sig.SIGPIPE, _sig.SIG_DFL)
    try:
        main()
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:
            pass
        sys.exit(0)
