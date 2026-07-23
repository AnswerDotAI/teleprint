#!/usr/bin/env python3
"""A real Python REPL on teleprint: an execnb worker over the clikernel stream protocol.

Enter runs the line in the persistent worker; outputs print as blocks (inputs,
stdout, results, errors) that scroll into the transcript while the prompt stays
put. Tab completes (single match inserts, several show a menu line). ctrl-C
interrupts a running cell, or clears the line at idle. ctrl-D on an empty line
quits. Clicks and ctrl-O toggle blocks -- including while a cell is running.
"""
import os, select, signal
from rich.text import Text
from teleprint.buffer import Buffer
from teleprint.compositor import Compositor
from teleprint.tty import RealTty
from clikernel.stream import StreamWorker

HINT = 'python -- Enter runs; Tab completes; ctrl-C interrupts; ctrl-D quits'

def _text(t):
    return ''.join(t) if isinstance(t, list) else (t or '')

def main():
    t = RealTty()
    t.write('\x1b[?1000;1006h\x1b[?2004h')
    resized, done = [], []
    signal.signal(signal.SIGWINCH, lambda *a: resized.append(1))
    w = StreamWorker()
    buf = Buffer()
    comp = Compositor(t).start()
    state = dict(stream=None, menu=None)

    def paint():
        lines = [Text(HINT if w.busy is None else 'running... (ctrl-C interrupts)', style='reverse')]
        if state['menu']: lines.append(Text('  '.join(state['menu'][:8]), style='dim'))
        lines.append(Text('>>> ') + Text(buf.text))
        comp.set_tail(*lines, cursor=(len(lines)-1, buf.cell_cursor('>>> ')))

    def on_out(o):
        ot = o.get('output_type')
        if ot == 'stream':
            if state['stream'] is None: state['stream'] = comp.print_block(gutter=(Text('« ', style='cyan'), Text('  ')))
            txt = _text(o.get('text')).rstrip('\n')
            if txt: comp.extend(state['stream'], txt)
        elif ot in ('execute_result', 'display_data'):
            data = o.get('data', {})
            if 'image/png' in data: comp.print_block('[image output -- see ipyai for kitty rendering]')
            elif 'text/plain' in data: comp.print_block(_text(data['text/plain']))
        elif ot == 'error':
            comp.print_block(Text.from_ansi('\n'.join(o.get('traceback', []))))

    def complete():
        matches, start = w.complete(buf.text, buf.cursor)
        if not matches: return
        common = os.path.commonprefix(matches)
        if len(matches) == 1 or len(common) > buf.cursor - start:
            buf.text = buf.text[:start] + common + buf.text[buf.cursor:]
            buf.cursor = start + len(common)
        state['menu'] = matches if len(matches) > 1 else None

    def on_key(k):
        if k.name == 'ctrl+d' and not buf.text:
            done.append(1)
            return
        if k.name == 'ctrl+o':
            live = [b for b in comp.blocks.values() if not b.committed]
            if live: comp.toggle(live[-1])
        elif k.name == 'enter' and buf.text and w.busy is None:
            comp.print_block(Text(buf.text), gutter=(Text('» ', style='green'), Text('  ')))
            state.update(stream=None, menu=None)
            w.exec(buf.text)
            buf.clear()
        elif k.name == 'tab' and buf.text and w.busy is None:
            complete()
        else:
            state['menu'] = None
            buf.handle(k)
        paint()

    comp.on_key = on_key
    comp.on_paste = lambda text: (buf.insert(text), paint())
    paint()
    try:
        while not done:
            try:
                r, _, _ = select.select([t.fd, w.fd], [], [], 0.2)
            except KeyboardInterrupt:  # ISIG on: ctrl-C arrives as SIGINT
                if w.busy: w.interrupt()
                else:
                    buf.clear()
                    paint()
                continue
            if w.fd in r:
                for ev in w.pump():
                    if ev.get('ev') == 'out': on_out(ev['output'])
                    elif ev.get('ev') == 'done': paint()
            if t.fd in r:
                data = t.read(timeout=0)
                if data: comp.on_bytes(data)
            if not r: comp.flush_input()
            if resized:
                resized.clear()
                comp.resize()
                paint()
    finally:
        w.close()
        t.write('\x1b[?2004l\x1b[?1000;1006l\r\n')
        t.restore()

if __name__ == '__main__':
    try: main()
    except Exception:
        import traceback
        traceback.print_exc()
