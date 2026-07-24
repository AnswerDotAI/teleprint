"The shell layer: a persistent bash/zsh on its own pty, sentinel boundaries, emulator-cleaned residue."
import asyncio, os
import pyghostty
from teleprint.jobs import spawn_shell, relay_shell, finish_job

async def _boot(sh='bash', size=(80, 24), cwd=None):
    s = spawn_shell(size=size, cwd=cwd, sh=sh)
    with pyghostty.Terminal(*size) as boot:
        assert (await asyncio.wait_for(relay_shell(s, mirror=boot), 15))[0] == 'prompt'
    return s

async def _run(s, cmd, size=(80, 24)):
    "One command through the shell; returns (result, cleaned residue)."
    with pyghostty.Terminal(*size) as m:
        os.write(s.master_fd, cmd.encode() + b'\n')
        res = await asyncio.wait_for(relay_shell(s, mirror=m), 15)
        return res, m.contents().strip()

async def _quit(s):
    os.write(s.master_fd, b'exit\n')
    assert await asyncio.wait_for(relay_shell(s), 15) == 'eof'
    finish_job(s)

def _shell_roundtrip(sh):
    "Boundary sentinel with exit code + pwd; state persists; the written command never echoes."
    async def go():
        s = await _boot(sh, cwd='/tmp')
        res, _ = await _run(s, 'cd /Users && TP_X=7')
        assert res == ('prompt', 0, '/Users')
        res, out = await _run(s, 'echo "$TP_X in $PWD"')
        assert res[0] == 'prompt' and out == '7 in /Users'   # output only: no command echo
        res, _ = await _run(s, 'false')
        assert res[1] == 1
        await _quit(s)
    asyncio.run(go())

def test_shell_bash(): _shell_roundtrip('bash')
def test_shell_zsh(): _shell_roundtrip('zsh')

def test_shell_own_job_control():
    "fg/bg/jobs/ctrl-Z are the shell's builtins: stop a child, see it in `jobs`, resume and end it."
    async def go():
        s = await _boot()
        os.write(s.master_fd, b'sleep 30\n')
        await asyncio.sleep(0.4)
        os.write(s.master_fd, b'\x1a')                       # ^Z: the shell stops it and prompts (= boundary)
        assert (await asyncio.wait_for(relay_shell(s), 15))[0] == 'prompt'
        res, out = await _run(s, 'jobs')
        assert 'sleep 30' in out
        os.write(s.master_fd, b'fg\n')
        await asyncio.sleep(0.4)
        os.write(s.master_fd, b'\x03')                       # ^C ends the resumed child
        res = await asyncio.wait_for(relay_shell(s), 15)
        assert res[0] == 'prompt' and res[1] != 0            # 130: died by SIGINT
        await _quit(s)
    asyncio.run(go())

def test_altscreen_erases_itself_from_residue():
    "Jeremy's vim rule needs no detection: the emulator's alt-screen semantics drop it from contents()."
    async def go():
        s = await _boot()
        res, out = await _run(s, r"printf '\033[?1049hSECRET DRAWING\033[?1049l'; echo visible after")
        assert res[0] == 'prompt'
        assert 'visible after' in out and 'SECRET' not in out
        await _quit(s)
    asyncio.run(go())

def test_chatty_output_never_stalls():
    "The old bg-stall regression, shell edition: a huge burst relays through a headless mirror without deadlock."
    async def go():
        s = await _boot()
        with pyghostty.Terminal(80, 24, scrollback=100_000) as m:
            os.write(s.master_fd, b'seq 1 20000\n')
            res = await asyncio.wait_for(relay_shell(s, mirror=m), 30)
            assert res[0] == 'prompt' and res[1] == 0
            assert m.contents().splitlines()[-1] == '20000'
        await _quit(s)
    asyncio.run(go())

def test_resize_reaches_shell_children():
    "Job.resize sets the pty winsize; a command inside the shell sees the new size."
    async def go():
        s = await _boot(size=(50, 12))
        s.resize(97, 41)
        res, out = await _run(s, 'stty size')
        assert res[0] == 'prompt' and '41 97' in out          # stty reports rows cols
        await _quit(s)
    asyncio.run(go())
