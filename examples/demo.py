#!/usr/bin/env python3
"""Morning smoke test: drive the real compositor on a real tty.

Run bare, in tmux, and in Terminal.app. Watch for: repaint flicker (no mode
2026 yet), CPR races at startup, clean block-at-a-time scrollback when you
scroll up afterwards, clicks toggling headers, ctrl-O doing the same from the
keyboard. 'q' (or ctrl-C) quits and restores the terminal.

Wheel inside tmux delegates to copy-mode (the DEV.md wheel scheme); outside
tmux the wheel is ignored for now.
"""
import asyncio, os, subprocess, time
from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.tty import RealTty

HINT = 'click a #n line, ctrl-O toggles newest, ctrl-L recovers, q quits'

async def amain():
    t = RealTty()
    t.write('\x1b[?1000;1006h')  # SGR mouse: clicks only, wheel stays native
    try:
        comp = await Compositor(t).start()  # start owns the signals: WINCH -> on_resize, ctrl-C -> a key
        comp.set_tail(Text(f'teleprint demo -- {HINT}', style='reverse'), Text('> '))
        blocks = []
        GUT = (Text('» ', style='green'), Text('  '))
        def block(body):
            blocks.append(comp.print_block(body, gutter=GUT))
        def resized():
            comp.resize()  # width rewrap invalidated the map: everything demotes
            comp.set_tail(Text(f'resized to {comp.cols}x{comp.rows} -- old blocks are history, ctrl-L revives recent ones', style='reverse'), Text('> '))
            block('printed after the resize, so this one is live\n(and toggleable)')
        comp.on_resize = resized
        for i in range(4):
            block(f'body line one of block {i}\nbody line two of block {i}')
            await asyncio.sleep(0.4)
        stream = comp.print_block('streaming block (taller than your screen)', gutter=GUT)
        blocks.append(stream)
        for i in range(comp.rows + 8):
            comp.extend(stream, f'streamed line {i}')
            comp.set_tail(Text(f'streaming... {i}', style='reverse'), Text('> '))
            await asyncio.sleep(0.12)
        # fresh blocks after the stream, so clickable ones exist at rest even in a short pane
        for i in range(3):
            block(f'this block is live: click its » line to toggle\n(second body line)')
            await asyncio.sleep(0.3)
        if os.environ.get('TMUX'):  # wheel-up hands the gesture to tmux copy-mode (exits at bottom, clicks resume)
            comp.on_wheel = lambda d: subprocess.run(['tmux', 'copy-mode', '-eu']) if d < 0 else None
        done = asyncio.Event()
        comp.on_key = lambda k: done.set() if k.name in ('q', 'ctrl+c') else None
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        def on_tty():
            data = t.read(timeout=0)
            if b'\x0f' in data:  # ctrl-O: toggle the newest un-committed block
                live = [b for b in blocks if not b.committed]
                if live: comp.toggle(live[-1])
                data = data.replace(b'\x0f', b'')
            if b'\x0c' in data:  # ctrl-L: the recovery gesture -- clear, reprint recent blocks live
                comp.clear(*blocks[-3:])
                comp.set_tail(Text(f'recovered: last {min(len(blocks),3)} blocks live again -- {HINT}', style='reverse'), Text('> '))
                data = data.replace(b'\x0c', b'')
            comp.on_bytes(data)
        loop.add_reader(t.fd, on_tty)
        try:
            while not done.is_set():
                try: await asyncio.wait_for(done.wait(), 1.0)
                except asyncio.TimeoutError:
                    comp.flush_input()
                    comp.set_tail(Text(f'idle {int(time.monotonic()-t0)}s -- {HINT}', style='reverse'), Text('> '))
        finally:
            loop.remove_reader(t.fd)
            comp.stop()
    finally:
        t.write('\x1b[?1000;1006l\r\n')
        t.restore()

if __name__ == '__main__':
    try: asyncio.run(amain())
    except Exception:
        import traceback
        traceback.print_exc()  # after the finally restored the terminal, so it is readable
