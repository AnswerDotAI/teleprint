#!/usr/bin/env python3
"""A minimal teleprint app: the write-once design ("block-oriented CLI") driven for real.

The engine's four rules live in `teleprint.compositor.Compositor` (and DEV.md "Compositor
model"): the screen is the last screenful of the rendered document, redrawn from the model;
only deliberate scrolls reach scrollback, inked once and never addressed again; everything
visible is live (folding just changes the model; shrink rematerializes); resize is a repaint.
This example is what an *app* writes on top of that: content blocks with typed gutters, a
tail (status + prompt), transients riding `over`, key bindings, and one paint function.

Try it in tmux at various sizes: click any `x\\dx` gutter (or press alt-digit) to toggle that
block, click the [mode] button, `n` adds an exchange, `r` runs a fake cell with a spinner,
`m` modal, `c` completion menu, `x` closes them, `q` quits. Then scroll up (wheel: tmux
copy-mode) and read the inked history.
"""
import os, select, signal, subprocess, sys, time
from rich.style import Style
from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.tty import RealTty

# Gutters: 3 glyph cells + space, digit-in-the-middle when numbered (`»»»` -> `»4»`).
def _g(mark, style): return (Text(mark * 3 + ' ', style=style), Text('··· ', style='dim'))
GUT = dict(inp=_g('»', 'bold green'), out=_g('«', ''), ask=_g('›', 'bold magenta'),
           reply=_g('‹', ''), tool=_g('≡', 'dim'))
SPIN = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
MENU = Text(' area ', style='reverse') + Text('  abs   all   any   ascii   aiter ', style='dim')
MODAL = ['┌ sessions in ~/aai-ws/teleprint ──────────────┐',
         '│ ▸ 40627   3 cells   what does area do?       │',
         '│   40621  12 cells   resize experiments       │',
         '│   40598   5 cells   burst-input repro        │',
         '│ (a mock: x closes, choosing does nothing)    │',
         '└──────────────────────────────────────────────┘']
EXCHANGES = [
    [('inp', 'x = 41')],
    [('inp', 'x + 1'), ('out', '42')],
    [('inp', 'def area(w, h):\n    "Rectangle area."\n    return w * h')],
    [('inp', 'for i in range(18): print(i, area(i, i + 1))'),
     ('out', '\n'.join(f'{i} {i * (i + 1)}' for i in range(18)))],
    [('ask', '.what does area do?'),
     ('reply', 'area(w, h) multiplies width by height:\n\n- inputs: two numbers\n- output: their product\n\nIt has no side effects.')],
    [('tool', 'py: check_area(3, 4)\n12\nok')],
    [('inp', 'help(sorted)'), ('out', '\n'.join(f'doc line {i} of the sorted() help text' for i in range(40)))],
    [('inp', 'x * 2'), ('out', '82')],
    [('inp', 'sum(range(100))'), ('out', '4950')],
]
NOTE = 'click/alt-digit toggles · n adds · r runs · m modal · c menu · x closes · q quits'

def main():
    tty = RealTty()
    tty.write('\x1b[?1000;1006h')                 # SGR mouse on; teleprint parses the events
    comp = Compositor(tty).start()
    comp.numbering = True
    feed = iter(EXCHANGES)
    state = dict(mode='code', menu=False, modal=False, run=None, alive=True)

    def add():
        for kind, text in next(feed, []):
            comp.print_block(text, gutter=GUT[kind], tag=kind, collapse_at=comp.rows // 2)

    def transients():
        if state['modal']: return list(MODAL)
        over = []
        if state['run']:
            t, i = state['run']
            over.append(Text(f' {SPIN[i % len(SPIN)]} running {time.monotonic() - t:.1f}s', style='dim'))
        if state['menu']: over.append(MENU)
        return over

    def paint():
        btn = Text(f"[mode: {state['mode']}]", style=Style(reverse=True) + Style(meta={'act': 'mode'}))
        status = btn + Text(f' · demo2 {comp.cols}x{comp.rows} · {NOTE}', style='reverse')
        status.truncate(comp.cols, overflow='ellipsis')  # one row always: the tail must not wrap
        prompt = Text('»»» ' if state['mode'] == 'code' else '››› ',
                      style='bold green' if state['mode'] == 'code' else 'bold magenta')
        comp.set_tail(status, prompt, cursor=(1, 4), over=transients())

    def on_key(k):
        if k.name == 'q':
            state.update(menu=False, modal=False, run=None, alive=False)
            paint()                               # one clean final frame: transients never ink as exit debris
        elif k.name == 'n' and not state['modal']: add(); paint()
        elif k.name == 'r' and state['run'] is None:
            comp.print_block('slow_work()', gutter=GUT['inp'], tag='inp')
            state['run'] = (time.monotonic(), 0)
            paint()
        elif k.name == 'm': state['modal'] = True; paint()
        elif k.name == 'c': state['menu'] = not state['menu']; paint()
        elif k.name == 'x' and (state['modal'] or state['menu']):
            state.update(modal=False, menu=False); paint()
        elif k.name.startswith('alt+') and k.name[4:] in comp.numbered:
            comp.toggle(comp.blocks[comp.numbered[k.name[4:]]])

    comp.on_key = on_key
    comp.on_act = lambda token: (state.update(mode='prompt' if state['mode'] == 'code' else 'code'), paint())
    comp.on_wheel = lambda d: subprocess.run(['tmux', 'copy-mode', '-eu']) if d < 0 and os.environ.get('TMUX') else None
    signal.signal(signal.SIGWINCH, lambda *a: state.update(resized=True))

    for _ in range(5): add()
    paint()
    while state['alive']:
        r, _, _ = select.select([tty.fd], [], [], 0.1 if state['run'] else 0.25)
        if r: comp.on_bytes(os.read(tty.fd, 1024))
        else: comp.flush_input()
        if state.pop('resized', None):
            comp.resize()
            paint()
        if state['run']:
            t, i = state['run']
            if time.monotonic() - t > 3:
                state['run'] = None
                comp.print_block(f'slow_work finished in {time.monotonic() - t:.1f}s', gutter=GUT['out'], tag='out')
            else: state['run'] = (t, i + 1)
            paint()
    tty.write('\x1b[?1000;1006l\r\n')
    tty.restore()

if __name__ == '__main__':
    try: main()
    except Exception:
        import traceback
        traceback.print_exc()  # after restore, so it is readable
