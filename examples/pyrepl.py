#!/usr/bin/env python3
"""A real Python REPL on teleprint: a jupygate kernel driven over `jupyasyncclient`.

Enter runs the line in a kernel created for this session (jupygate must be
running, like any Jupyter server; `IPYAI_GATEWAY` overrides the default URL).
Outputs print as blocks (inputs, stdout, results, errors) that scroll into the
transcript while the prompt stays put, arriving as the cell runs. Tab completes
(single match inserts, several show a menu line). ctrl-C interrupts a running
cell, or clears the line at idle. ctrl-D on an empty line quits. Clicks and
ctrl-O toggle blocks -- including while a cell is running.
"""
import asyncio, os
from rich.text import Text
from teleprint.buffer import Buffer
from teleprint.compositor import Compositor
from teleprint.tty import RealTty
from jupyasyncclient import JupyAsyncKernelClient
from fastcore.nbio import msg2out
from jupywire.route import OUTPUT_MSGS

GATEWAY = os.environ.get('IPYAI_GATEWAY', 'http://127.0.0.1:8787')
HINT = 'python -- Enter runs; Tab completes; ctrl-C interrupts; ctrl-D quits'

def _text(t): return ''.join(t) if isinstance(t, list) else (t or '')

async def amain():
    t = RealTty()
    t.write('\x1b[?1000;1006h\x1b[?2004h')
    try:
        async with await JupyAsyncKernelClient.connect(GATEWAY, cwd=os.getcwd()) as kc: await repl(t, kc)
    finally:
        t.write('\x1b[?2004l\x1b[?1000;1006l\r\n')
        t.restore()

async def repl(t, kc):
    buf = Buffer()
    comp = await Compositor(t).start()
    state = dict(stream=None, menu=None, run=None)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    def paint():
        lines = [Text(HINT if state['run'] is None else 'running... (ctrl-C interrupts)', style='reverse')]
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
        elif ot == 'error': comp.print_block(Text.from_ansi('\n'.join(o.get('traceback', []))))

    async def run_cell(code):
        try:
            async for m in kc.run(code):
                if m['msg_type'] in OUTPUT_MSGS: on_out(msg2out(m))
        finally:
            state['run'] = None
            paint()

    async def complete():
        matches, start = await kc.complete(buf.text, buf.cursor)
        if not matches: return
        common = os.path.commonprefix(matches)
        if len(matches) == 1 or len(common) > buf.cursor - start:
            buf.text = buf.text[:start] + common + buf.text[buf.cursor:]
            buf.cursor = start + len(common)
        state['menu'] = matches if len(matches) > 1 else None
        paint()

    def on_key(k):
        if k.name == 'ctrl+d' and not buf.text:
            done.set()
            return
        if k.name == 'ctrl+o':
            live = [b for b in comp.blocks.values() if not b.committed]
            if live: comp.toggle(live[-1])
        elif k.name == 'ctrl+c':  # in-band or synthesized from SIGINT: one surface either way
            if state['run'] is not None: comp.spawn(kc.interrupt_kernel(), name='interrupt')
            else: buf.clear()
        elif k.name == 'enter' and buf.text and state['run'] is None:
            comp.print_block(Text(buf.text), gutter=(Text('» ', style='green'), Text('  ')))
            state.update(stream=None, menu=None)
            state['run'] = comp.spawn(run_cell(buf.text), name='run')  # handle kept: it gates ctrl-C and teardown
            buf.clear()
        elif k.name == 'tab' and buf.text and state['run'] is None:
            paint()
            return complete()   # a returned coroutine is scheduled by the compositor
        else:
            state['menu'] = None
            buf.handle(k)
        paint()

    def on_tty():
        data = t.read(timeout=0)
        if data: comp.on_bytes(data)

    comp.on_key = on_key
    comp.on_paste = lambda text: (buf.insert(text), paint())
    comp.on_resize = lambda: (comp.resize(), paint())  # app tail state: own the whole resize response
    loop.add_reader(t.fd, on_tty)
    paint()
    try:
        while not done.is_set():   # the input parser needs periodic flushes (esc disambiguation)
            try: await asyncio.wait_for(done.wait(), 0.2)
            except asyncio.TimeoutError: comp.flush_input()
    finally:
        loop.remove_reader(t.fd)
        comp.stop()
        if state['run'] is not None: state['run'].cancel()

def main():
    try: asyncio.run(amain())
    except Exception:
        import traceback
        traceback.print_exc()

if __name__ == '__main__': main()
