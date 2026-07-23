"Test harness: a fake tty backed by pyghostty's emulator, for headless compositor tests."
from collections import deque
from pyghostty import Terminal, ffi, lib
from pyghostty._ffi import check

class FakeTty:
    """The app's side of a terminal, emulated: writes feed a headless Ghostty, reads
    return injected input plus the emulator's own query responses (CPR, DECRQM, ...).

    The write/read/size/flush surface is the draft borrow-contract tty interface:
    whatever owns the terminal at a given moment holds exactly this object."""
    def __init__(self, cols=80, rows=24, scrollback=10_000, bg=None):
        self.term = Terminal(cols, rows, scrollback)
        self._input = deque()
        # The emulator's query responses ("written back to the pty") become readable input.
        self._on_pty = ffi.callback('void(GhosttyTerminal, void*, const uint8_t*, size_t)',
                                    lambda t,u,d,n: self._input.append(ffi.buffer(d,n)[:]))
        check(lib.ghostty_terminal_set(self.term._t[0], lib.GHOSTTY_TERMINAL_OPT_WRITE_PTY, self._on_pty), 'set write_pty')
        if bg is not None:  # a configured background makes the emulator answer OSC 11 queries (theme detection)
            c = ffi.new('GhosttyColorRgb*', dict(zip('rgb', bg)))
            check(lib.ghostty_terminal_set(self.term._t[0], lib.GHOSTTY_TERMINAL_OPT_COLOR_BACKGROUND, c), 'set bg')

    def write(self, data):
        "App output: feed the emulator; any query responses queue for `read`."
        self.term.feed(data)

    def flush(self): pass

    def read(self):
        "All pending input bytes (injected and emulator responses), b'' when none."
        out = b''.join(self._input)
        self._input.clear()
        return out

    def inject(self, data):
        "Test-side: queue bytes as if sent by the terminal (keys, mouse, paste)."
        if isinstance(data, str): data = data.encode()
        self._input.append(data)

    @property
    def size(self): return self.term.size

    def close(self): self.term.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
