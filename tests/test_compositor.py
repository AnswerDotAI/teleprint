import asyncio, gc, os, signal
import pytest
from rich.text import Text
from rich.style import Style
from rich.syntax import Syntax
from rich.cells import cell_len
from teleprint.buffer import Buffer
from teleprint.compositor import Compositor
from teleprint.testing import EmuTty

G = (Text('»»» ', style='bold green'), Text('··· ', style='dim'))

async def settle():
    for _ in range(3): await asyncio.sleep(0)

def click(tty, comp, text):
    y = next(i for i, line in enumerate(tty.term.text().splitlines()) if text in line)
    comp.on_bytes(b'\x1b[<0;1;%dM' % (y + 1))

async def test_stream_fold_edit_and_history(terminal):
    tty, comp = await terminal(rows=8, shell='old command\r\nshell $ ')
    comp.set_tail('STATUS', '> ')
    comp.put('in', 'request', gutter=G)
    assert tty.term.text().splitlines()[:3] == ['old command', 'shell $', '»»» request']
    out = comp.put('out', gutter=G, collapse_at=4)
    for i in range(4): comp.extend('out', f'chunk {i}')
    assert not out.collapsed
    comp.extend('out', 'chunk 4')
    assert out.collapsed and 'chunk 0 … (+4 lines)' in tty.term.text()
    comp.extend('out', 'chunk 5')
    assert 'chunk 0 … (+5 lines)' in tty.term.text()
    comp.toggle('out')
    for i in range(6, 15): comp.extend('out', f'chunk {i}')
    assert not out.collapsed and 'chunk 14' in tty.term.text()
    lines = tty.term.contents().splitlines()
    for i in range(15): assert sum(line.endswith(f'chunk {i}') for line in lines) == 1
    assert lines.count('old command') == 1 and lines.count('STATUS') == 1
    history = lines[:-len(tty.term.text().splitlines())]
    click(tty, comp, 'chunk 14')  # the first row has inked, but the visible part still toggles
    assert out.collapsed and 'request' in tty.term.text() and 'chunk 0 … (+14 lines)' in tty.term.text()
    assert tty.term.contents().splitlines()[:len(history)] == history
    assert comp.put('out', 'new one\nnew two', gutter=G, source='new source') is out
    assert out.collapsed and out.source == 'new source'
    comp.toggle('out')
    comp.put('middle', 'inserted', gutter=G, after='in')
    assert tty.term.text().splitlines() == ['»»» request', '»»» inserted', '»»» new one', '··· new two', 'STATUS', '>']
    comp.drop('middle')
    assert 'inserted' not in tty.term.text() and 'new two' in tty.term.text()
    assert tty.term.contents().splitlines()[:len(history)] == history

async def test_resize_clipping_and_transients(terminal):
    tty, comp = await terminal(30, 10, '\r\n'.join(f'shell {i}' for i in range(9)))
    comp.set_tail('status', '> ')
    comp.put('code', Syntax('', 'python', theme='monokai').highlight('x = 6*7'), gutter=G)
    comp.set_tail('status', '> ', over=[Text(f'pick {i}') for i in range(5)])
    assert all(f'pick {i}' in tty.term.text() for i in range(5)) and tty.term.text().endswith('status\n>')
    comp.set_tail('status', '> ')
    assert 'pick 0' not in tty.term.contents() and 'x = 6*7' in tty.term.text()
    for i in range(10): comp.put(f'b{i}', f'line {i}\nmore {i}', gutter=G)
    before = tty.term.text()
    comp.set_tail('status', '> ', over=[Text('MENU')])
    click(tty, comp, 'MENU')
    assert not any(b.collapsed for b in comp.blocks.values())
    comp.set_tail('status', '> ')
    assert tty.term.text() == before and 'MENU' not in tty.term.contents()
    for cols, rows in ((80, 10), (30, 5), (80, 10)):
        tty.term.resize(cols, rows)
        comp.resize()
        comp.set_tail('status', '> ')
        assert tty.term.contents().splitlines().count('status') == 1
        assert all(f'line {i}' in tty.term.contents() for i in range(10))
    comp.set_tail('status ' + 'x' * comp.cols, Text('>>> ab\n... cd'), cursor=(1, 1, 6))
    x, y = tty.term.cursor
    assert x == 6 and tty.term.text().splitlines()[y] == '... cd'
    comp.put('wide', 'y' * 78 + '\n' + 'z' * 78, gutter=G, collapse_at=1)
    assert comp.blocks['wide'].collapsed
    assert all(sum(cell_len(s.text) for s in segs) <= comp.cols for _, segs in comp._doc_rows())
    assert tty.term.text().endswith('>>> ab\n... cd')

async def test_numbered_dim_blocks_and_actions(terminal):
    tty, comp = await terminal(rows=12)
    comp.numbering = True
    hits = []
    comp.on_act = hits.append
    comp.set_tail(Text('[mode]', style=Style(meta={'act': 'mode'})), '> ')
    comp.put('old', 'aa\nbb', gutter=G)
    comp.put('one', 'one liner', gutter=G)
    newest = comp.put('new', 'cc\ndd', gutter=G, pad=True, dim=True)
    assert tty.term.text().splitlines()[:6] == ['»1» aa', '··· bb', '»»» one liner', '', '»0» cc', '··· dd']
    assert comp.numbered == {'0': 'new', '1': 'old'} and newest.height == 2
    assert tty.term.style(1, 4)['faint'] and tty.term.style(4, 5)['faint'] and not tty.term.style(0, 0)['faint']
    comp.toggle(comp.numbered['0'])
    assert '»0» cc … (+1 lines)' in tty.term.text()
    comp.put('new', 'cc\ndd', gutter=G, pad=True)
    assert not tty.term.style(1, 4)['faint']
    click(tty, comp, '[mode]')
    click(tty, comp, 'one liner')
    click(tty, comp, '>')
    assert hits == ['mode'] and not comp.blocks['one'].collapsed
    for i in range(12): comp.put(f'b{i}', 'top\nbottom', gutter=G)
    assert len(comp.numbered) <= 10 and 'old' not in comp.numbered.values()
    assert '»»» aa' in tty.term.contents() and '»1» aa' not in tty.term.contents()

@pytest.mark.xfail(strict=True, raises=AssertionError,
    reason='Ghostty resize reflows a wrapped live tail into scrollback before repaint; tail chrome must never ink')
async def test_wrapped_tail_stays_out_of_history_on_resize(terminal):
    tty, comp = await terminal(80, 10)
    comp.set_tail('status ' + 'x' * 80, '>>> ab\n... cd')
    tty.term.resize(30, 5)
    comp.resize()
    comp.set_tail('status', '> ')
    assert tty.term.text() == 'status\n>'
    assert tty.term.contents().splitlines().count('status') == 1, tty.term.contents()

@pytest.mark.parametrize('with_tail', [False, True])
async def test_borrow_handoff_and_modes(terminal, with_tail):
    tty, comp = await terminal()
    comp.put('before', 'before job', gutter=G)
    if with_tail: comp.set_tail('status', '> ')
    modes = (1000, 1006, 2004)
    assert all(tty.term.mode(m) for m in modes)
    async with comp.borrow() as t:
        assert 'status' not in tty.term.text() and not any(tty.term.mode(m) for m in modes)
        t.write('job says hi\r\n')
        comp.put('record', 'job says hi', ink=False)
        comp.put('late', 'during job', gutter=G)
        assert 'during job' not in tty.term.text()
    assert all(tty.term.mode(m) for m in modes)
    comp.put('after', 'after job', gutter=G)
    comp.set_tail('> ')
    lines = tty.term.contents().splitlines()
    assert lines.index('»»» before job') < lines.index('job says hi') < lines.index('»»» during job') < lines.index('»»» after job')
    assert lines.count('job says hi') == 1 and 'status' not in lines
    comp.put('insert', 'old insertion', gutter=G, after='before')
    assert comp.blocks['insert'].committed and 'old insertion' not in tty.term.contents()
    comp.set_modes(1049)
    comp.stop()
    assert not any(tty.term.mode(m) for m in (*modes, 1049))

async def test_query_and_interleaved_input():
    with EmuTty(30, 8, bg=(250, 250, 244)) as tty:
        comp = Compositor(tty)
        keys, ctls = [], []
        comp.on_key, comp.on_ctl = keys.append, ctls.append
        write = tty.write
        def with_key(data):
            write(data)
            if data == '\x1b[6n': tty.seed('k')  # input arrives behind the real terminal's startup reply
        tty.write = with_key
        await comp.start()
        try:
            tty.seed('q')
            data = await comp.query(b'\x1b]11;?\x1b\\\x1b[c', rb'\x1b\[\?[0-9;]*c')
            assert b'rgb:' in data and [k.name for k in keys] == ['k', 'q']
            assert any(c.kind == 'osc' and c.data.startswith('11;rgb:') for c in ctls)
        finally: comp.stop()

async def test_editing_through_terminal_input(terminal):
    tty, comp = await terminal()
    buf = Buffer()
    def paint(): comp.set_tail(Text('> ') + Text(buf.text), cursor=(0, buf.cell_cursor('> ')))
    def on_key(k):
        if k.name == 'enter':
            comp.put(f'in{len(comp.blocks)}', buf.text, gutter=G)
            buf.clear()
        else: buf.handle(k)
        paint()
    comp.on_key = on_key
    paint()
    comp.on_bytes('hi 日'.encode())
    assert tty.term.text() == '> hi 日' and tty.term.cursor == (7, 0)
    comp.on_bytes(b'\x02\x02\x02X\x05\r')
    assert tty.term.text().splitlines() == ['»»» hXi 日', '>'] and tty.term.cursor == (2, 1)

async def test_background_errors_and_cancellation(terminal):
    _, comp = await terminal()
    seen = []
    comp.on_task_error = lambda e, t: seen.append(e)
    async def action(): raise ValueError('failed action')
    comp.on_key = lambda k: action()
    comp.on_bytes(b'x')
    await settle()
    assert len(seen) == 1 and isinstance(seen[0], ValueError)
    victim = comp.spawn(asyncio.sleep(60))
    victim.cancel()
    await asyncio.gather(victim, return_exceptions=True)
    assert len(seen) == 1
    reports, loop = [], asyncio.get_running_loop()
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda l, ctx: reports.append(ctx))
    try:
        comp.on_task_error = None
        comp.spawn(action())
        await settle()
        gc.collect()
        assert any(isinstance(c.get('exception'), ValueError) for c in reports)
    finally: loop.set_exception_handler(old)

async def test_signal_delivery_and_stop(terminal):
    _, comp = await terminal()
    resized, keys = [], []
    comp.on_resize, comp.on_key = lambda: resized.append(1), keys.append
    os.kill(os.getpid(), signal.SIGWINCH)
    os.kill(os.getpid(), signal.SIGINT)
    await settle()
    assert resized == [1] and [k.name for k in keys] == ['ctrl+c']
    comp.stop()
    os.kill(os.getpid(), signal.SIGWINCH)
    await settle()
    assert resized == [1]
    assert all(signal.getsignal(s) is signal.SIG_DFL for s in (signal.SIGTERM, signal.SIGHUP))
