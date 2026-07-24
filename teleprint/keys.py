"Terminal input parsing: bytes to key/mouse/paste/CPR events, incrementally."
import re
from dataclasses import dataclass

@dataclass
class Key:
    name: str          # 'a', 'enter', 'ctrl+a', 'alt+f', 'shift+tab', 'up', ...
    char: str = None   # the literal character, for printables

@dataclass
class Paste:
    text: str

@dataclass
class Mouse:
    btn: int
    x: int             # 0-indexed cell
    y: int
    press: bool

@dataclass
class CPR:
    row: int           # 0-indexed
    col: int

@dataclass
class Ctl:
    "A control string (OSC/APC/DCS/PM/SOS): a terminal reply or embedded control payload, never keystrokes."
    kind: str   # 'osc', 'apc', 'dcs', 'pm', 'sos'
    data: str   # the payload between introducer and terminator

STR_KINDS = {0x5d: 'osc', 0x5f: 'apc', 0x50: 'dcs', 0x5e: 'pm', 0x58: 'sos'}
MAX_CTL = 1 << 20  # a runaway unterminated control string gets dropped rather than buffered forever

C0 = {0x0d:'enter', 0x0a:'enter', 0x09:'tab', 0x7f:'backspace', 0x00:'ctrl+space'}
CSI_FINAL = {'A':'up','B':'down','C':'right','D':'left','H':'home','F':'end','Z':'shift+tab'}
CSI_TILDE = {1:'home',2:'insert',3:'delete',4:'end',5:'pageup',6:'pagedown',7:'home',8:'end'}
SS3 = {'A':'up','B':'down','C':'right','D':'left','H':'home','F':'end','P':'f1','Q':'f2','R':'f3','S':'f4'}
PASTE_END = b'\x1b[201~'
CSI_RE = re.compile(rb'\A\x1b\[([<>?]?)([0-9;]*)([\x40-\x7e])')

def _mod(name, m):
    "Apply xterm modifier param `m` to key `name` (1=none, +1 shift, +2 alt, +4 ctrl)."
    b = m - 1
    if b & 4: name = 'ctrl+' + name
    if b & 2: name = 'alt+' + name
    if b & 1: name = 'shift+' + name
    return name

def _c0(b):
    if b in C0: return Key(C0[b])
    return Key(f'ctrl+{chr(b + 0x60)}') if b < 0x20 else None

def _utf8len(b):
    return 1 if b < 0x80 else 2 if b >> 5 == 6 else 3 if b >> 4 == 14 else 4 if b >> 3 == 30 else 1

class Parser:
    """Incremental VT input parser: `feed` bytes (arbitrarily split), get events out.

    Escape sequences and UTF-8 characters split across reads are buffered until
    complete. A lone ESC is indistinguishable from the start of a sequence, so it
    stays buffered; the caller resolves it by calling `flush` after its own read
    timeout (timing lives in the event loop, not the parser). CPR replies conflict
    with F3 (`ESC[1;2R`): CPR wins, as everywhere else."""
    def __init__(self):
        self._buf = b''
        self._paste = None  # bytes collected so far when inside a bracketed paste
        self._armed = False  # set by flush on first sight of a pending ESC; new bytes disarm

    def feed(self, data):
        if isinstance(data, str): data = data.encode()
        if data: self._armed = False
        self._buf += data
        out = []
        while self._buf:
            if self._paste is not None:
                i = self._buf.find(PASTE_END)
                if i < 0:
                    keep = max(len(self._buf) - len(PASTE_END) + 1, 0)
                    self._paste += self._buf[:keep]
                    self._buf = self._buf[keep:]
                    break
                self._paste += self._buf[:i]
                self._buf = self._buf[i + len(PASTE_END):]
                out.append(Paste(self._paste.decode(errors='replace')))
                self._paste = None
                continue
            ev, used = self._parse1()
            if not used: break  # incomplete sequence: wait for more bytes
            self._buf = self._buf[used:]
            if ev == 'PASTE_START': self._paste = b''
            elif ev is not None: out.append(ev)
        return out

    def flush(self):
        """Resolve a buffered leading ESC as the escape key, arming on the first call and firing on
        the second (call after a read timeout). One-call resolution shattered escape sequences whose
        tail was still in flight -- a late CPR reply became composer text -- so only an ESC still
        pending across two full timeouts resolves; any new bytes disarm."""
        if not self._buf.startswith(b'\x1b'): return []
        if not self._armed:
            self._armed = True
            return []
        rest = self._buf[1:]
        self._buf = b''
        self._armed = False
        return [Key('escape')] + self.feed(rest)

    def _parse1(self):
        buf = self._buf
        b0 = buf[0]
        if b0 != 0x1b:
            if b0 < 0x20 or b0 == 0x7f: return _c0(b0), 1
            n = _utf8len(b0)
            if len(buf) < n: return None, 0
            ch = buf[:n].decode(errors='replace')
            return Key(ch, ch), n
        if len(buf) < 2: return None, 0
        if buf[1:2] == b'[': return self._csi(buf)
        if buf[1:2] == b'O':
            if len(buf) < 3: return None, 0
            k = SS3.get(chr(buf[2]))
            return (Key(k) if k else None), 3
        # Control strings win over alt-chords: ESC ] P X ^ _ introduce OSC/DCS/SOS/PM/APC, so
        # alt+], alt+P, alt+X, alt+^, alt+_ can never be bindings -- the price of guaranteeing a
        # terminal reply never leaks into the composer as keystrokes.
        if buf[1] in STR_KINDS: return self._str_seq(buf)
        b1 = buf[1]
        if b1 == 0x1b: return Key('escape'), 1  # ESC ESC: first one is real
        sub = _c0(b1) if b1 < 0x20 or b1 == 0x7f else Key(chr(b1)) if b1 < 0x80 else None
        return (Key(f'alt+{sub.name}') if sub else None), 2

    def _str_seq(self, buf):
        "Consume a control string whole: OSC ends at BEL or ST, the rest at ST. Keys never leak out of one."
        kind = STR_KINDS[buf[1]]
        i = 2
        while i < len(buf):
            b = buf[i]
            if b == 0x07 and kind == 'osc': return Ctl(kind, buf[2:i].decode(errors='replace')), i + 1
            if b == 0x1b:
                if i + 1 >= len(buf): return None, 0  # terminator may be split across reads
                if buf[i + 1] == 0x5c: return Ctl(kind, buf[2:i].decode(errors='replace')), i + 2
            i += 1
        return (None, len(buf)) if len(buf) > MAX_CTL else (None, 0)

    def _csi(self, buf):
        m = CSI_RE.match(buf)
        if m is None: return (None, 0) if len(buf) < 24 else (None, 2)  # incomplete, or junk: drop the ESC [
        priv, fin, used = m[1], chr(m[3][0]), m.end()
        ps = [int(p) for p in m[2].decode().split(';') if p]
        if priv == b'<' and fin in 'Mm':
            btn, x, y = (ps + [0, 1, 1])[:3]
            return Mouse(btn, x-1, y-1, fin == 'M'), used
        if fin == 'R' and len(ps) >= 2: return CPR(ps[0]-1, ps[1]-1), used
        if fin == '~':
            if ps and ps[0] == 200: return 'PASTE_START', used
            k = CSI_TILDE.get(ps[0] if ps else 0)
            return (Key(_mod(k, ps[1]) if len(ps) > 1 else k) if k else None), used
        k = CSI_FINAL.get(fin)
        return (Key(_mod(k, ps[1]) if len(ps) > 1 else k) if k else None), used
