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
    done = asyncio.Event()
    buf = Buffer()
    try:
        comp = await Compositor(t).start()  # start owns the signals, mouse and paste modes, and reading input
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
                n = len(comp.blocks)
                comp.put(f'in{n}', Text(line), gutter=(Text('» ', style='green'), Text('  ')))
                comp.put(f'out{n}', f'echo: {line}')
            elif k.name == 'ctrl+o':
                live = [b for b in comp.blocks.values() if not b.committed]
                if live: comp.toggle(live[-1].key)
            elif k.name == 'ctrl+c': buf.clear()
            else: buf.handle(k)
            paint()
        comp.on_key = on_key
        comp.on_paste = lambda text: (buf.insert(text), paint())
        comp.on_resize = lambda: (comp.resize(), paint())
        paint()
        try: await done.wait()
        finally: comp.stop()
    finally:
        t.write('\r\n')
        t.restore()

if __name__ == '__main__':
    try: asyncio.run(amain())
    except Exception:
        import traceback
        traceback.print_exc()  # after the finally restored the terminal, so it is readable
