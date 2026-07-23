"The jobs layer: real processes on real ptys, output mirrored into a headless terminal."
import asyncio, os, signal
import pyghostty
from teleprint.jobs import spawn_job, relay_job, watch_job, finish_job
from teleprint.testing import FakeTty

SH = '/bin/sh'  # pinned: the user's $SHELL must not shape test behavior

def test_fg_echo_and_residue():
    "Foreground: bytes reach the screen and the mirror; the mirror's contents are the clean residue."
    async def go():
        tty = FakeTty(60, 12)
        with pyghostty.Terminal(60, 12) as mirror:
            job = spawn_job('echo hello world', sh=SH, size=(60, 12))
            res = await asyncio.wait_for(relay_job(job, tty.write, mirror=mirror), 10)
            assert res == 'eof' and job.state == 'done'
            assert finish_job(job) == 0
            assert 'hello world' in tty.term.text()
            assert mirror.contents().strip() == 'hello world'
    asyncio.run(go())

def test_exit_code():
    async def go():
        job = spawn_job('exit 3', sh=SH)
        assert await asyncio.wait_for(relay_job(job), 10) == 'eof'
        assert finish_job(job) == 3
    asyncio.run(go())

def test_stdin_relay_via_master():
    "Input written to the master reaches the job (tests drive the master directly; the app passes in_fd)."
    async def go():
        tty = FakeTty(40, 10)
        job = spawn_job('cat', sh=SH, size=(40, 10))
        task = asyncio.ensure_future(relay_job(job, tty.write))
        await asyncio.sleep(0.2)
        os.write(job.master_fd, b'hi there\n')
        await asyncio.sleep(0.2)
        os.write(job.master_fd, b'\x04')  # ^D: canonical-mode EOF ends cat
        assert await asyncio.wait_for(task, 10) == 'eof'
        assert finish_job(job) == 0
        assert 'hi there' in tty.term.text()
    asyncio.run(go())

def test_suspend_cont_and_signal_exit():
    "^Z through the pty line discipline stops the job; cont + re-relay resumes; signal exits decode negative."
    async def go():
        job = spawn_job('sleep 30', sh=SH, size=(40, 10))
        task = asyncio.ensure_future(relay_job(job))
        await asyncio.sleep(0.2)
        os.write(job.master_fd, b'\x1a')
        assert await asyncio.wait_for(task, 10) == 'stopped'
        assert job.state == 'stopped'
        job.cont()
        task = asyncio.ensure_future(relay_job(job))
        await asyncio.sleep(0.2)
        os.killpg(job.pgid, signal.SIGTERM)
        assert await asyncio.wait_for(task, 10) == 'eof'
        assert finish_job(job) == -signal.SIGTERM
    asyncio.run(go())

def test_altscreen_erases_itself_from_residue():
    "Jeremy's vim rule needs no detection: the emulator's alt-screen semantics drop it from contents()."
    async def go():
        with pyghostty.Terminal(60, 12) as mirror:
            job = spawn_job(r"printf '\033[?1049hSECRET DRAWING\033[?1049l'; echo visible after", sh=SH, size=(60, 12))
            assert await asyncio.wait_for(relay_job(job, mirror=mirror), 10) == 'eof'
            assert finish_job(job) == 0
            resid = mirror.contents()
            assert 'visible after' in resid and 'SECRET' not in resid
    asyncio.run(go())

def test_bg_drains_without_stall():
    "The ipythonng gap-1 regression: a chatty bg job must not stall on a full pty buffer."
    async def go():
        with pyghostty.Terminal(80, 24, scrollback=100_000) as mirror:
            job = spawn_job('seq 1 20000', sh=SH, size=(80, 24))
            exits = []
            res = await asyncio.wait_for(watch_job(job, mirror, lambda j, r: exits.append(r)), 30)
            assert res == 'eof' and exits == ['eof']
            assert finish_job(job) == 0
            assert sum(map(len, job.captured)) > 64 * 1024  # more than a pty buffer's worth flowed
            assert mirror.contents().splitlines()[-1] == '20000'
    asyncio.run(go())

def test_resize_reaches_job():
    "Job.resize sets the pty winsize; a subsequent size query inside the job sees it."
    async def go():
        tty = FakeTty(50, 12)
        job = spawn_job('sleep 0.4; stty size', sh=SH, size=(50, 12))
        task = asyncio.ensure_future(relay_job(job, tty.write))
        await asyncio.sleep(0.15)
        job.resize(97, 41)
        assert await asyncio.wait_for(task, 10) == 'eof'
        assert finish_job(job) == 0
        assert '41 97' in tty.term.text()  # stty reports rows cols
    asyncio.run(go())
