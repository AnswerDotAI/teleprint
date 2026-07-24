"The real-terminal side of the tty interface: `RealTty` matches `testing.EmuTty`'s surface."
import os, select, sys, termios, tty as _tty

class RealTty:
    """write/read/size/flush over the process's controlling terminal, in cbreak-plus mode.

    The termios delta follows the pt lore harvest in DEV.md: cbreak, then clear
    IEXTEN (BSD/macOS eats ^O as VDISCARD), IXON/IXOFF (or ctrl-S freezes
    output), ICRNL (Enter arrives as \\r, distinct from ctrl-J), and set VMIN=1
    (Solaris-family defaults it to 4 via the VEOF slot). ISIG is left on: ctrl-C
    still interrupts, which an early-stage app usually wants; clear it when the
    app handles interrupts itself."""
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self.fd)
        _tty.setcbreak(self.fd)
        a = termios.tcgetattr(self.fd)
        a[3] &= ~termios.IEXTEN
        a[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL)
        a[6][termios.VMIN] = 1
        termios.tcsetattr(self.fd, termios.TCSANOW, a)

    def write(self, data):
        if isinstance(data, str): data = data.encode()
        os.write(sys.stdout.fileno(), data)

    def flush(self): pass

    def raw(self):
        """Enter full raw mode for a job borrow: ISIG off so ^C/^Z reach the job through the pty
        line discipline instead of signaling the app, ECHO off, OPOST off for byte-faithful relay."""
        self._app_mode = termios.tcgetattr(self.fd)
        _tty.setraw(self.fd)

    def cooked(self):
        "Return to app (cbreak-plus) mode after a borrow."
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self._app_mode)

    def read(self, timeout=0.02):
        "All bytes arriving within `timeout` (draining briefly once something arrives); b'' when none."
        out = b''
        while select.select([self.fd], [], [], timeout)[0]:
            out += os.read(self.fd, 1024)
            timeout = 0.005
        return out

    @property
    def size(self):
        c = os.get_terminal_size()
        return c.columns, c.lines

    def restore(self): termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
    def __enter__(self): return self
    def __exit__(self, *args): self.restore()
