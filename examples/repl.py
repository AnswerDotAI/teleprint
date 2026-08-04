#!/usr/bin/env python3
"""Echo-REPL: the bottom-prompt + scrollable-transcript shape, with no execution machinery.

Type at the prompt (readline-emacs keys work: ctrl-a/e/k/u/w/y, alt-b/f, arrows);
Enter prints your line as an input block plus an echoed output block, which scroll
away naturally while the prompt stays put. Click a #n line or use ctrl-O to toggle
blocks. ctrl-C or ctrl-D on an empty line quits.
"""
import asyncio
from rich.text import Text
from teleprint.buffer import Buffer
from teleprint.compositor import Compositor
from teleprint.tty import RealTty

HINT = 'echo-REPL -- Enter echoes; click #n lines; ctrl-D quits'

async def amain():
    t = RealTty()
    t.write('\x1b[?1000;1006h\x1b[?2004h')  # SGR mouse + bracketed paste
    done = asyncio.Event()
    buf = Buffer()
    try:
        comp = await Compositor(t).start()  # start owns the signals now: WINCH repaints, ctrl-C arrives as a key
        def paint():
            comp.set_tail(Text(HINT, style='reverse'), Text('> ') + Text(buf.text),
                          cursor=(1, buf.cell_cursor('> ')))
        def on_key(k):
            if k.name in ('ctrl+c', 'ctrl+d') and not buf.text:
                done.set()
                return
            if k.name == 'enter':
                line = buf.text
                buf.clear()
                comp.print_block(Text(line), gutter=(Text('» ', style='green'), Text('  ')))
                comp.print_block(f'echo: {line}')
            elif k.name == 'ctrl+o':
                live = [b for b in comp.blocks.values() if not b.committed]
                if live: comp.toggle(live[-1])
            elif k.name == 'ctrl+c': buf.clear()
            else: buf.handle(k)
            paint()
        comp.on_key = on_key
        comp.on_paste = lambda text: (buf.insert(text), paint())
        comp.on_resize = lambda: (comp.resize(), paint())
        loop = asyncio.get_running_loop()
        loop.add_reader(t.fd, lambda: comp.on_bytes(t.read(timeout=0)))
        paint()
        try:
            while not done.is_set():   # the input parser needs periodic flushes (esc disambiguation)
                try: await asyncio.wait_for(done.wait(), 0.2)
                except asyncio.TimeoutError: comp.flush_input()
        finally:
            loop.remove_reader(t.fd)
            comp.stop()
    finally:
        t.write('\x1b[?2004l\x1b[?1000;1006l\r\n')
        t.restore()

if __name__ == '__main__':
    try: asyncio.run(amain())
    except Exception:
        import traceback
        traceback.print_exc()  # after the finally restored the terminal, so it is readable
