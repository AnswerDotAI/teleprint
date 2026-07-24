"""The persistent shell on its own pty: spawn, per-command relay with sentinel boundaries, exit.

One interactive shell (bash or zsh) runs as session leader of a fresh pty, injected with prompt
integration: the user's rc sources first, then the prompt empties (the app's composer is the
prompt), and each prompt emits a private OSC sentinel carrying `$?` and `$PWD`. `relay_shell`
shuttles bytes between the app's terminal and the shell until that sentinel arrives -- the
command boundary -- stripping it from the sinks, so it never reaches a real terminal (no
terminal or tmux support is required). Output tees into a `mirror`, a headless
`pyghostty.Terminal` whose `contents()` is the cleaned residue (the emulator, not a regex,
digests control sequences; an alt screen erases itself by its own semantics). Job control is
the shell's own: `fg`/`bg`/`jobs`/ctrl-Z are builtins, and a stop simply returns to the prompt.
The one-shot runner this file once carried was deleted when the persistent shell landed."""
import asyncio, fcntl, os, pty, re, signal, struct, tempfile, termios

__all__ = ['Job', 'spawn_shell', 'relay_shell', 'finish_job']

SENTINEL = re.compile(rb'\x1b\]7770;([^;\x07]*);([^\x07]*)\x07')

SHELL_RC = r'''[ -f ~/.bashrc ] && . ~/.bashrc
PS1=''
PROMPT_COMMAND='__tp_ec=$?; __tp_pc=1; stty -echo; printf "\033]7770;%s;%s\a" "$__tp_ec" "$PWD"; unset __tp_pc'
trap '[ -z "$__tp_pc" ] && stty echo' DEBUG
'''
# $? is captured before stty clobbers it; the __tp_pc guard keeps the DEBUG trap (which re-enables
# echo for the *user's* commands and their children) from re-echoing PROMPT_COMMAND's own steps,
# so the sentinel is emitted only after echo is off: the next written command never echoes.

ZSH_RC = r'''export ZDOTDIR="$HOME"
[ -f "$HOME/.zshrc" ] && . "$HOME/.zshrc"
precmd_functions=(); preexec_functions=()   # prompt frameworks' hooks would fight ours; aliases/functions/PATH survive
precmd() { local ec=$?; stty -echo; printf '\033]7770;%s;%s\a' "$ec" "$PWD"; }
preexec() { stty echo; }                    # once per submitted command line, before it runs: children see normal echo
unsetopt zle
PROMPT=''; RPROMPT=''
'''

class Job:
    "A process on its own pty: `pid` is also its process group (session leader)"
    def __init__(self, cmd, pid, pgid, master_fd):
        self.cmd, self.pid, self.pgid, self.master_fd = cmd, pid, pgid, master_fd
        self.captured, self.state = [], 'running'
    def __repr__(self): return f'Job({self.cmd!r}, pgid={self.pgid}, {self.state})'
    def resize(self, cols, rows):
        "Propagate a new terminal size: set the pty's winsize (the kernel WINCHes the foreground group)."
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        try: os.killpg(self.pgid, signal.SIGWINCH)
        except ProcessLookupError: pass

def _writen(fd, data):
    while data: data = data[os.write(fd, data):]

def spawn_shell(size=None, cwd=None, sh=None):
    """Fork the user's interactive shell as session leader of a fresh pty, with injected prompt
    integration (sentinel boundary, empty prompt, unechoed command reads). bash and zsh are
    supported (`--rcfile` and `ZDOTDIR` injection respectively); anything else runs as bash."""
    sh = sh or os.environ.get('SHELL', 'bash')
    zsh = os.path.basename(sh) == 'zsh'
    if zsh:
        zdot = tempfile.mkdtemp(prefix='teleprint-zdot-')
        with open(os.path.join(zdot, '.zshrc'), 'w') as f: f.write(ZSH_RC)
    else:
        rc = tempfile.NamedTemporaryFile('w', suffix='.bashrc', delete=False)
        rc.write(SHELL_RC)
        rc.close()
    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            if size: fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', size[1], size[0], 0, 0))
            if cwd: os.chdir(cwd)
            if zsh:
                os.environ['ZDOTDIR'] = zdot
                os.execlp(sh, 'zsh', '-i')
            else: os.execlp('bash', 'bash', '--noediting', '--rcfile', rc.name, '-i')
        finally: os._exit(127)
    return Job('$SHELL', pid, pid, master_fd)

async def relay_shell(job, write=None, mirror=None, in_fd=None):
    """Shuttle bytes between the app's terminal and the shell's pty until the shell prints its
    prompt: returns ('prompt', exit_code, pwd) -- or 'eof' if the shell itself died. Output goes
    through `write` (None streams nothing to the screen) and tees into `mirror` and
    `job.captured`; `in_fd` (the real tty's fd, raw mode) relays keystrokes in. The sentinel is
    stripped from the sinks (it is boundary metadata, not output); a partial escape tail is held
    back across reads so a sentinel split over two chunks cannot leak or be missed."""
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    buf = b''
    def _finish(res):
        if not done.done(): done.set_result(res)
    def _sink(data):
        if not data: return
        job.captured.append(data)
        if mirror is not None: mirror.feed(data)
        if write is not None: write(data)
    def on_master():
        nonlocal buf
        try: data = os.read(job.master_fd, 65536)
        except OSError: data = b''
        if not data:
            job.state = 'done'
            _sink(buf); buf = b''
            return _finish('eof')
        buf += data
        m = SENTINEL.search(buf)
        if m:
            _sink(buf[:m.start()])
            buf = b''
            try: code = int(m.group(1))
            except ValueError: code = 0
            return _finish(('prompt', code, m.group(2).decode(errors='replace')))
        esc = buf.rfind(b'\x1b')
        keep = esc if esc != -1 and len(buf) - esc < 64 else len(buf)  # hold back a possible partial sentinel
        _sink(buf[:keep])
        buf = buf[keep:]
    def on_stdin():
        data = os.read(in_fd, 4096)
        if data: _writen(job.master_fd, data)
    loop.add_reader(job.master_fd, on_master)
    if in_fd is not None: loop.add_reader(in_fd, on_stdin)
    try: return await done
    finally:
        loop.remove_reader(job.master_fd)
        if in_fd is not None: loop.remove_reader(in_fd)

def finish_job(job):
    "Collect the shell's exit status, then close the pty; returns the exit code (negative = terminating signal)"
    _, status = os.waitpid(job.pid, 0)  # collect first: closing the master would SIGHUP it
    os.close(job.master_fd)
    ec = os.waitstatus_to_exitcode(status)
    return -(ec - 128) if ec > 128 else ec
