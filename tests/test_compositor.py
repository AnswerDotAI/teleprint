from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.testing import FakeTty

G = (Text('» ', style='green'), Text('  '))  # 2-cell gutter: the first-line glyph carries the click target

def make(cols=30, rows=8):
    tty = FakeTty(cols, rows)
    return tty, Compositor(tty).start()

def test_blocks_and_tail():
    tty, comp = make()
    comp.set_tail('status: ok', '> ')
    comp.print_block('body one\nmore one', gutter=G)
    comp.print_block('body two', gutter=G)
    assert tty.term.text() == '» body one\n  more one\n» body two\nstatus: ok\n>'
    assert tty.term.cursor == (0, comp._park)

def test_scroll_commits_and_map_holds():
    tty, comp = make(30, 6)
    comp.set_tail('> ')
    bs = [comp.print_block(f'top {i}\nbot {i}', gutter=G) for i in range(5)]
    assert bs[0].committed and bs[1].committed and bs[2].committed
    assert not bs[4].committed
    lines = tty.term.contents().splitlines()
    for i in range(5):
        assert lines.count(f'» top {i}') == 1 and lines.count(f'  bot {i}') == 1
    assert tty.term.cursor == (0, comp._park)

def test_cpr_sync_matches_prediction():
    tty, comp = make(20, 5)
    comp.set_tail('> ')
    for i in range(7):
        comp.print_block(f'b{i}')
        pred = comp._park
        assert comp.sync() == (pred, 0)

def test_toggle_in_place():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    b1 = comp.print_block('alpha\nbeta', gutter=G)
    comp.print_block('gamma', gutter=G)
    before = tty.term.text()
    comp.toggle(b1)
    assert tty.term.text() == '» alpha … (+1 lines)\n» gamma\n>'
    comp.toggle(b1)
    assert tty.term.text() == before
    assert tty.term.cursor == (0, comp._park)
    assert tty.term.contents().count('beta') == 1

def test_click_toggles():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    b1 = comp.print_block('headline\nhidden body', gutter=G)
    row = comp._row(0)
    comp.on_bytes(b'\x1b[<0;%d;%dM' % (1, row+1))  # SGR mouse is 1-indexed; col 1 = the gutter glyph
    assert b1.collapsed
    assert 'hidden body' not in tty.term.text()
    comp.on_bytes(b'\x1b[<0;%d;%dM' % (1, row+1))
    assert not b1.collapsed
    assert 'hidden body' in tty.term.text()

def test_single_line_blocks_not_toggleable():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    b = comp.print_block('only line', gutter=G)
    comp.on_bytes(b'\x1b[<0;1;%dM' % (comp._row(0)+1))
    assert not b.collapsed
    comp.toggle(b)
    assert not b.collapsed  # nothing to hide

def test_click_on_committed_is_inert():
    tty, comp = make(30, 6)
    comp.set_tail('> ')
    bs = [comp.print_block(f'top {i}\nbot {i}', gutter=G) for i in range(5)]
    js = [j for j,(b,_) in enumerate(comp._lines) if b == bs[2].id]
    assert bs[2].committed and js  # committed (its first line scrolled) yet a row is still on screen
    comp.on_bytes(b'\x1b[<0;%d;%dM' % (1, comp._row(js[0])+1))
    assert not bs[2].collapsed  # nothing happened: history is inert

def test_progressive_commit():
    tty, comp = make(30, 6)
    comp.set_tail('> ')
    blk = comp.print_block(gutter=G)
    for i in range(15): comp.extend(blk, f'chunk {i}')
    assert blk.committed
    lines = tty.term.contents().splitlines()
    for i in range(15): assert sum(1 for l in lines if l.endswith(f'chunk {i}')) == 1
    assert tty.term.cursor == (0, comp._park)

def test_stream_collapse_at_threshold():
    tty, comp = make(40, 12)
    comp.set_tail('> ')
    blk = comp.print_block(gutter=G, collapse_at=4)
    for i in range(4): comp.extend(blk, f'line {i}')
    assert not blk.collapsed
    comp.extend(blk, 'line 4')  # crossing the threshold folds to the summary line
    assert blk.collapsed
    assert '» line 0 … (+4 lines)' in tty.term.text()
    comp.extend(blk, 'line 5')  # ...which keeps counting while the model grows
    assert '… (+5 lines)' in tty.term.text()
    assert 'line 3' not in tty.term.text()
    comp.toggle(blk)  # re-expand shows everything accumulated
    assert not blk.collapsed and 'line 3' in tty.term.text() and 'line 5' in tty.term.text()
    assert tty.term.cursor == (0, comp._park)

def test_born_over_threshold_collapses():
    tty, comp = make(40, 10)
    comp.set_tail('> ')
    b = comp.print_block('\n'.join(f'r{i}' for i in range(8)), gutter=G, collapse_at=3)
    assert b.collapsed
    assert '» r0 … (+7 lines)' in tty.term.text()
    assert 'r5' not in tty.term.contents()

def test_tail_diff():
    tty, comp = make(20, 6)
    comp.set_tail('status: 0', '> ')
    comp.print_block('x')
    for i in range(1, 4):
        comp.set_tail(f'status: {i}', '> ')
        assert tty.term.text().splitlines()[-2] == f'status: {i}'
        assert tty.term.cursor == (0, comp._park)

def test_resize_demotes():
    tty, comp = make(30, 8)
    comp.set_tail('> ')
    b1 = comp.print_block('stuff\nmore', gutter=G)
    tty.term.resize(40, 8)
    comp.resize()
    assert b1.committed
    comp.set_tail('> ')  # the app repaints its own tail after a resize
    comp.print_block('after', gutter=G)
    assert 'after' in tty.term.text()
    comp.toggle(b1)  # demoted: inert
    assert not b1.collapsed
    assert tty.term.cursor == (0, comp._park)

def test_clear_reprints_live():
    tty, comp = make(30, 8)
    comp.set_tail('> ')
    bs = [comp.print_block(f'top{i}\nbot{i}', gutter=G) for i in range(6)]
    assert bs[0].committed
    comp.clear(*bs[-2:])
    comp.set_tail('> ')
    assert not bs[-2].committed and not bs[-1].committed
    scr = tty.term.text().splitlines()
    assert scr[0] == '» top4'  # reprinted from the model at the top of the cleared screen
    row = scr.index('» top5')
    comp.on_bytes(b'\x1b[<0;%d;%dM' % (1, row+1))
    assert bs[-1].collapsed  # clickable again after the recovery gesture
    assert tty.term.cursor == (0, comp._park)
    lines = tty.term.contents().splitlines()
    assert sum(1 for l in lines if l.endswith('bot3')) == 1  # preserved though not revived
    assert sum(1 for l in lines if l.endswith('top4')) == 2  # preserved once, reprinted once
    assert bs[3].committed  # not-revived visible blocks committed by the clear

def test_wheel_hook_and_partial_escapes():
    tty, comp = make()
    comp.set_tail('> ')
    b = comp.print_block('body\nmore', gutter=G)
    hits = []
    comp.on_wheel = hits.append
    comp.on_bytes(b'\x1b[<64;5;3M\x1b[<65;5;3M\x1b[<0;1;')  # two wheel events + a split click
    assert hits == [-1, 1]
    comp.on_bytes(b'1M')  # completes the press on row 1: the block's first line
    assert b.collapsed
    assert comp._parser._buf == b''  # nothing left buffered once sequences complete

def test_cursor_parks_at_tail_cursor():
    tty, comp = make(30, 8)
    comp.set_tail('status', '> hi', cursor=(1, 4))
    assert comp._coff == 0  # the last tail line IS the park line
    assert tty.term.cursor == (4, comp._park)

def test_cursor_parking_survives_ops():
    tty, comp = make(30, 8)
    comp.set_tail('status', '> ', cursor=(1, 2))
    assert tty.term.cursor == (2, comp._park)
    comp.print_block('body', gutter=G)
    assert tty.term.cursor == (2, comp._park)
    comp.set_tail('status', '> x', cursor=(0, 3))  # cursor on the FIRST tail line: one row above park
    assert comp._coff == 1
    assert tty.term.cursor == (3, comp._park - 1)
    row, col = comp.sync()
    assert row == comp._park
    comp.print_block('more', gutter=G)
    assert tty.term.cursor == (3, comp._park - 1)

def test_cursor_parking_wide_chars():
    tty, comp = make(30, 8)
    comp.set_tail('> 日本', cursor=(0, 2 + 4))
    assert tty.term.cursor == (6, comp._park)

def test_repl_shape_typing_and_enter():
    "The echo-REPL wiring, headless: keys through the parser edit the tail; Enter prints blocks."
    from teleprint.buffer import Buffer
    tty, comp = make(40, 10)
    buf = Buffer()
    def paint(): comp.set_tail(Text('> ') + Text(buf.text), cursor=(0, buf.cell_cursor('> ')))
    def on_key(k):
        if k.name == 'enter':
            line = buf.text
            buf.clear()
            comp.print_block(line, gutter=G)
            comp.print_block(f'echo: {line}')
        else: buf.handle(k)
        paint()
    comp.on_key = on_key
    paint()
    comp.on_bytes('hi 日'.encode())
    assert tty.term.text().splitlines()[-1] == '> hi 日'
    assert tty.term.cursor == (7, comp._park)  # 2 prompt + 3 ascii + 2 wide cells
    comp.on_bytes(b'\x02\x02\x02X')            # ctrl+b x3, insert
    assert tty.term.text().splitlines()[-1] == '> hXi 日'
    comp.on_bytes(b'\x05\r')                   # ctrl+e then enter
    scr = tty.term.text().splitlines()
    assert '» hXi 日' in scr and 'echo: hXi 日' in scr
    assert scr[-1] == '>'  # buffer cleared, prompt trailing space trimmed by the formatter
    assert tty.term.cursor == (2, comp._park)

def test_python_repl_with_worker():
    "The pyrepl wiring headless: a real execnb worker's outputs land as blocks in the emulator."
    import select, time
    from teleprint.buffer import Buffer
    from clikernel.stream import StreamWorker
    tty, comp = make(50, 12)
    with StreamWorker() as w:
        buf = Buffer()
        state = {'stream': None}
        def paint(): comp.set_tail(Text('>>> ') + Text(buf.text), cursor=(0, buf.cell_cursor('>>> ')))
        def on_key(k):
            if k.name == 'enter':
                comp.print_block(buf.text, gutter=G)
                state['stream'] = None
                w.exec(buf.text)
                buf.clear()
            else: buf.handle(k)
            paint()
        comp.on_key = on_key
        paint()
        def run_to_done(timeout=20):
            end = time.monotonic() + timeout
            while w.busy and time.monotonic() < end:
                select.select([w.fd], [], [], 0.05)
                for ev in w.pump():
                    if ev.get('ev') != 'out': continue
                    o = ev['output']
                    txt = o.get('text', '')
                    txt = ''.join(txt) if isinstance(txt, list) else txt
                    if o['output_type'] == 'stream':
                        if state['stream'] is None: state['stream'] = comp.print_block(gutter=G)
                        comp.extend(state['stream'], txt.rstrip('\n'))
                    elif o['output_type'] == 'execute_result':
                        comp.print_block(''.join(o['data']['text/plain']))
            assert w.busy is None
        comp.on_bytes(b'6*7\r')
        run_to_done()
        scr = tty.term.text().splitlines()
        assert '» 6*7' in scr and '42' in scr
        comp.on_bytes(b'print("hello teleprint")\r')
        run_to_done()
        assert 'hello teleprint' in tty.term.text()
        matches, start = w.complete('import o', 8)
        assert 'os' in matches
    assert tty.term.cursor == (4, comp._park)  # parked at the empty prompt throughout

def test_resize_does_not_duplicate_tail():
    "Zoom toggles must not accumulate tail copies in the transcript: the tail is chrome, not history."
    tty, comp = make(40, 10)
    comp.set_tail('hint line', '> ', cursor=(1, 2))
    comp.print_block('body', gutter=G)
    for cols in (80, 40, 80):  # zoom in, out, in
        tty.term.resize(cols, 10)
        comp.resize()
        comp.set_tail('hint line', '> ', cursor=(1, 2))
    lines = tty.term.contents().splitlines()
    assert lines.count('hint line') == 1
    assert lines.count('» body') == 1
    assert tty.term.cursor == (2, comp._park)

def test_tail_cursor_renderable_form():
    "The (renderable_idx, line_within, col) cursor form survives other renderables wrapping."
    tty, comp = make(20, 8)
    status = 'a status line that certainly wraps at twenty columns'
    comp.set_tail(status, Text('>>> ab\n... cd'), cursor=(1, 1, 6))
    x, y = tty.term.cursor
    scr = tty.term.text().splitlines()
    assert scr[y] == '... cd' and x == 6

def test_on_ctl_hook():
    "Control strings reach on_ctl as events and never leak keystrokes into on_key."
    from teleprint.keys import Ctl
    tty, comp = make()
    got, keys = [], []
    comp.on_ctl = got.append
    comp.on_key = keys.append
    comp.on_bytes(b'\x1b]11;rgb:1111/2222/3333\x1b\\ab')
    assert got == [Ctl('osc', '11;rgb:1111/2222/3333')]
    assert [k.name for k in keys] == ['a', 'b']

def test_full_width_renderable_never_wraps():
    """A Syntax with a background theme renders full console width; with the gutter in front that
    once overflowed the row, and a real terminal's autowrap sheared the map (found live, 2026-07-24)."""
    from rich.syntax import Syntax
    from rich.cells import cell_len
    tty, comp = make(40, 12)
    comp.set_tail(Text('status line'), Text('> '))
    hl = Syntax('', 'python', theme='monokai').highlight('x = 6*7')
    comp.print_block(hl, gutter=(Text('>>> '), Text('... ')))
    for bid, segs in comp._lines:
        assert sum(cell_len(s.text) for s in segs) <= 40
    scr = tty.term.text().splitlines()
    assert scr[0].startswith('>>> x = 6*7')
    assert scr[-2] == 'status line' and scr[-1] == '>'  # the tail landed where the map says

def test_collapsed_summary_cropped():
    "gutter + first line + the dim count can exceed the width; the composed line is cropped, not wrapped."
    from rich.cells import cell_len
    tty, comp = make(30, 10)
    comp.set_tail(Text('tail'))
    b = comp.print_block('y' * 28, gutter=(Text('» '), Text('  ')), collapse_at=2)
    comp.extend(b, 'z' * 28)
    comp.extend(b, 'w' * 28)
    assert b.collapsed
    for bid, segs in comp._lines:
        assert sum(cell_len(s.text) for s in segs) <= 30
    assert tty.term.text().splitlines()[-1] == 'tail'

def test_record_block_paints_nothing():
    tty, comp = make(40, 10)
    comp.print_block('visible one', gutter=G)
    before = tty.term.text()
    blk = comp.record_block('line a\nline b\nline c', gutter=G, tag='sh', collapse_at=2)
    assert tty.term.text() == before          # nothing painted
    assert blk.committed and blk.collapsed    # in the model, folded past its threshold
    assert blk.height == 3 and comp.blocks[blk.id] is blk

def test_release_then_reanchor():
    "The borrow choreography: tail erased (chrome), blocks committed; job bytes flow raw; reanchor resumes below."
    tty, comp = make(40, 10)
    b1 = comp.print_block('block one', gutter=G)
    comp.set_tail(Text('status'), Text('> '), cursor=(1, 2))
    comp.release()
    scr = tty.term.text()
    assert 'block one' in scr and 'status' not in scr  # tail gone from glass, content stays
    assert b1.committed and comp._ntail == 0 and comp._lines == []
    tty.write(b'job says hi\r\njob line two\r\n')      # the borrower prints directly
    comp.reanchor()
    comp.print_block('after the job', gutter=G)
    comp.set_tail(Text('> '))
    lines = tty.term.text().splitlines()
    assert lines.index('job says hi') < lines.index('» after the job') < lines.index('>')
    assert 'status' not in tty.term.contents()          # the erased tail never entered history

def test_release_with_no_tail():
    tty, comp = make(40, 10)
    comp.print_block('only block', gutter=G)
    comp.release()
    tty.write(b'raw\r\n')
    comp.reanchor()
    comp.set_tail(Text('> '))
    lines = tty.term.text().splitlines()
    assert lines.index('» only block') < lines.index('raw') < lines.index('>')
