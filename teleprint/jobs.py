"""Job control for PTY commands: a shepherd process relays suspends and carries the exit status.

Ported from ipythonng's jobs.py (whose copy stays, serving vanilla terminal IPython), with three
teleprint-specific additions: the pty gets a window size at spawn and a `Job.resize` for SIGWINCH
forwarding; the blocking `copy_job` select loop becomes the asyncio `relay_job`; and output tees
into a `mirror` -- a headless `pyghostty.Terminal` whose `contents()` after the job is the cleaned
transcript residue (the emulator, not a regex, digests control sequences; the alt screen erases
itself from the residue by its own semantics)."""
import asyncio, fcntl, os, pty, select, signal, struct, termios

__all__ = ['Job', 'spawn_job', 'finish_job', 'relay_job', 'watch_job']

class Job:
    "A PTY command: `pid` is its shepherd, `pgid` the command's own process group"
    def __init__(self, cmd, pid, pgid, master_fd, status_r):
        self.cmd, self.pid, self.pgid, self.master_fd, self.status_r = cmd, pid, pgid, master_fd, status_r
        self.captured, self.state = [], 'running'
    def __repr__(self): return f'Job({self.cmd!r}, pgid={self.pgid}, {self.state})'
    def resize(self, cols, rows):
        "Propagate a new terminal size: set the pty's winsize, then SIGWINCH the job."
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        try: os.killpg(self.pgid, signal.SIGWINCH)
        except ProcessLookupError: pass
    def cont(self):
        "SIGCONT a stopped job (the %fg gesture); `relay_job` again to resume interaction."
        os.killpg(self.pgid, signal.SIGCONT)
        self.state = 'running'

def _read_line(fd):
    buf = b''
    while not buf.endswith(b'\n'):
        b = os.read(fd, 1)
        if not b: break
        buf += b
    return buf.decode()

def _writen(fd, data):
    while data: data = data[os.write(fd, data):]

def _shepherd(cmd, sh, status_w, size, cwd):
    "Run `cmd` in its own pgrp (keeps it suspendable) and relay its stops -- runs inside the pty session"
    if size: fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', size[1], size[0], 0, 0))
    if cwd: os.chdir(cwd)
    cmd_pid = os.fork()
    if cmd_pid == 0:
        os.setpgid(0, 0)
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(0, os.getpgrp())
        # reset child's disposition
        for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU, signal.SIGPIPE):
            signal.signal(s, signal.SIG_DFL)
        os.execlp(sh, 'sh', '-c', cmd)
    try: os.setpgid(cmd_pid, cmd_pid)
    except OSError: pass
    os.tcsetpgrp(0, cmd_pid)
    os.write(status_w, f'pgid {cmd_pid}\n'.encode())
    while True:
        _, st = os.waitpid(cmd_pid, os.WUNTRACED)
        if os.WIFSTOPPED(st): os.write(status_w, b'stopped\n')
        else:
            ec = os.waitstatus_to_exitcode(st)
            os._exit(ec if ec >= 0 else 128 - ec)

def spawn_job(cmd, sh=None, size=None, cwd=None):
    """Fork a shepherd on a fresh PTY running `cmd`; `size=(cols, rows)` sets the pty's winsize
    (shepherd-side, so the command never races the ioctl); `cwd` is where it runs. Returns the parent-side `Job`."""
    sh = sh or os.environ.get('SHELL', '/bin/sh')
    status_r, status_w = os.pipe()
    pid, master_fd = pty.fork()
    if pid == 0:
        os.close(status_r)
        try: _shepherd(cmd, sh, status_w, size, cwd)
        finally: os._exit(127)
    os.close(status_w)
    msg = _read_line(status_r).split()
    if not msg or msg[0] != 'pgid':
        os.close(master_fd); os.close(status_r); os.waitpid(pid, 0)
        raise OSError(f'failed to start job: {cmd!r}')
    return Job(cmd, pid, int(msg[1]), master_fd, status_r)

def _drain(job, sink):
    "Forward any buffered pty output through `sink`"
    while select.select([job.master_fd], [], [], 0)[0]:
        try: data = os.read(job.master_fd, 65536)
        except OSError: return
        if not data: return
        sink(data)

async def relay_job(job, write=None, mirror=None, in_fd=None):
    """Shuttle bytes between the app's terminal and the job's pty until it exits ('eof') or suspends
    ('stopped'). Output goes through `write` (the tty's write; None streams nothing to the screen)
    and tees into `mirror` and `job.captured`. `in_fd` (the real tty's fd, raw mode) relays
    keystrokes in; None when input is driven through the master directly (tests, bg jobs)."""
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    def _finish(res):
        if not done.done(): done.set_result(res)
    def _sink(data):
        job.captured.append(data)
        if mirror is not None: mirror.feed(data)
        if write is not None: write(data)
    def on_master():
        try: data = os.read(job.master_fd, 65536)
        except OSError: data = b''
        if not data:
            job.state = 'done'
            return _finish('eof')
        _sink(data)
    def on_stdin():
        data = os.read(in_fd, 4096)
        if data: _writen(job.master_fd, data)
    def on_status():
        msg = _read_line(job.status_r)
        if msg.startswith('stopped'):
            _drain(job, _sink)
            job.state = 'stopped'
            _finish('stopped')
        else: loop.remove_reader(job.status_r)  # EOF: shepherd has exited; master EOF follows
    loop.add_reader(job.master_fd, on_master)
    loop.add_reader(job.status_r, on_status)
    if in_fd is not None: loop.add_reader(in_fd, on_stdin)
    try: return await done
    finally:
        loop.remove_reader(job.master_fd)
        loop.remove_reader(job.status_r)
        if in_fd is not None: loop.remove_reader(in_fd)

async def watch_job(job, mirror=None, on_exit=None):
    """Background watcher: drain the job's pty into `mirror`, nothing reaching the screen. The
    continuous drain is what keeps a chatty bg job from stalling on a full pty buffer. Calls
    `on_exit(job, result)` when the job exits or stops, and returns 'eof'|'stopped'."""
    res = await relay_job(job, mirror=mirror)
    if on_exit: on_exit(job, res)
    return res

def finish_job(job):
    "Reap the shepherd, then close the pty; returns the command's exit code (negative = killed by that signal)"
    _, status = os.waitpid(job.pid, 0)  # reap first: closing the master would SIGHUP the shepherd
    os.close(job.master_fd)
    os.close(job.status_r)
    ec = os.waitstatus_to_exitcode(status)
    return -(ec - 128) if ec > 128 else ec
