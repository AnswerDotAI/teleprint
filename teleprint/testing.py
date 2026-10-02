"Test harness: an emulated tty backed by pyghostty, for headless compositor tests."
from collections import deque
from pyghostty import Terminal

class EmuTty:
    """The app's side of a terminal, emulated: writes feed a headless Ghostty, reads
    return seeded input plus the emulator's own query responses (CPR, DECRQM, ...).

    The write/read/size/flush surface is the draft borrow-contract tty interface:
    whatever owns the terminal at a given moment holds exactly this object."""
    def __init__(self, cols=80, rows=24, scrollback=10_000, bg=None):
        self.term = Terminal(cols, rows, scrollback, bg=bg)  # with `bg` set, the emulator answers OSC 11 queries (theme detection)
        self._input = deque()
        self.term.on_reply(self._input.append)

    def write(self, data):
        "App output: feed the emulator; any query responses queue for `read`."
        self.term.feed(data)

    def flush(self): pass
    def raw(self): pass      # borrow-mode termios changes have no headless equivalent
    def cooked(self): pass
    def restore(self): pass  # termios restore has no headless equivalent either

    def read(self):
        "All pending input bytes (seeded and emulator responses), b'' when none."
        out = b''.join(self._input)
        self._input.clear()
        return out

    def seed(self, data):
        "Test-side: queue bytes as if sent by the terminal (keys, mouse, paste)."
        if isinstance(data, str): data = data.encode()
        self._input.append(data)

    @property
    def size(self): return self.term.size

    def close(self): self.term.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
