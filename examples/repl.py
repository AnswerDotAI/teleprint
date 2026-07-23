#!/usr/bin/env python3
"""Echo-REPL: the bottom-prompt + scrollable-transcript shape, with no execution machinery.

Type at the prompt (readline-emacs keys work: ctrl-a/e/k/u/w/y, alt-b/f, arrows);
Enter prints your line as an input block plus an echoed output block, which scroll
away naturally while the prompt stays put. Click a #n line or use ctrl-O to toggle
blocks. ctrl-C or ctrl-D on an empty line quits.
"""
import signal, time
from rich.text import Text
from teleprint.buffer import Buffer
from teleprint.compositor import Compositor
from teleprint.tty import RealTty

HINT = 'echo-REPL -- Enter echoes; click #n lines; ctrl-D quits'

def main():
    t = RealTty()
    t.write('\x1b[?1000;1006h\x1b[?2004h')  # SGR mouse + bracketed paste
    resized = []
    signal.signal(signal.SIGWINCH, lambda *a: resized.append(1))
    done = []
    buf = Buffer()
    try:
        comp = Compositor(t).start()
        def paint():
            comp.set_tail(Text(HINT, style='reverse'), Text('> ') + Text(buf.text),
                          cursor=(1, buf.cell_cursor('> ')))
        def on_key(k):
            if k.name == 'ctrl+d' and not buf.text:
                done.append(1)
                return
            if k.name == 'enter':
                line = buf.text
                buf.clear()
                comp.print_block(Text(line), gutter=(Text('» ', style='green'), Text('  ')))
                comp.print_block(f'echo: {line}')
            elif k.name == 'ctrl+o':
                live = [b for b in comp.blocks.values() if not b.committed]
                if live: comp.toggle(live[-1])
            else:
                buf.handle(k)
            paint()
        comp.on_key = on_key
        comp.on_paste = lambda text: (buf.insert(text), paint())
        paint()
        while not done:
            try:
                data = t.read(timeout=0.2)
            except KeyboardInterrupt:  # ISIG is on in RealTty, so ctrl-C arrives as SIGINT
                break
            if resized:
                resized.clear()
                comp.resize()
                paint()
            if data: comp.on_bytes(data)
            else: comp.flush_input()
    finally:
        t.write('\x1b[?2004l\x1b[?1000;1006l\r\n')
        t.restore()

if __name__ == '__main__':
    try: main()
    except Exception:
        import traceback
        traceback.print_exc()  # after the finally restored the terminal, so it is readable
