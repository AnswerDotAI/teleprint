from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.testing import EmuTty

G = (Text('» ', style='green'), Text('  '))  # 2-cell gutter: the glyph carries the click target

def make(cols=30, rows=8):
    tty = EmuTty(cols, rows)
    return tty, Compositor(tty).start()

def parked(tty, comp):
    "Standing invariant: the emulator's cursor is exactly where the frame said it parked it."
    return tty.term.cursor == (comp._cursor[1], comp._cursor[0])

def click_row(tty, comp, bid):
    "Send a real SGR press on the first visible row of block `bid` (1-indexed on the wire)."
    y = next(y for y, e in enumerate(comp._screen) if e and e[0] == bid)
    comp.on_bytes(b'\x1b[<0;1;%dM' % (y + 1))

def test_blocks_and_tail():
    tty, comp = make()
    comp.set_tail('status: ok', '> ')
    comp.print_block('body one\nmore one', gutter=G)
    comp.print_block('body two', gutter=G)
    assert tty.term.text() == '» body one\n  more one\n» body two\nstatus: ok\n>'
    assert parked(tty, comp)

def test_scroll_inks_each_row_once():
    tty, comp = make(30, 6)
    comp.set_tail('> ')
    bs = [comp.print_block(f'top {i}\nbot {i}', gutter=G) for i in range(5)]
    assert comp._ws == 5  # 10 content rows, 5 fit above the tail
    lines = tty.term.contents().splitlines()
    for i in range(5):
        assert lines.count(f'» top {i}') == 1 and lines.count(f'  bot {i}') == 1
    assert not any(b.committed for b in bs)  # write-once: everything stays in the document
    assert parked(tty, comp)

def test_lazy_claim_preserves_shell():
    "Startup adopts the shell's cursor row: its screen stays put and scrolls off only as content needs the space."
    tty = EmuTty(30, 8)
    tty.term.feed(b'aai-ws $ some old command\r\naai-ws $ ')  # a shell mid-session
    comp = Compositor(tty).start()
    comp.set_tail('> ')
    comp.print_block('first', gutter=G)
    scr = tty.term.text().splitlines()
    assert scr[0] == 'aai-ws $ some old command'      # untouched above the region
    assert scr[1].startswith('aai-ws $')
    assert scr[2] == '» first'
    for i in range(8): comp.print_block(f'b{i}', gutter=G)
    lines = tty.term.contents().splitlines()
    assert lines.count('aai-ws $ some old command') == 1  # scrolled into history intact, once
    assert parked(tty, comp)

def test_toggle_in_place():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    b1 = comp.print_block('alpha\nbeta', gutter=G)
    comp.print_block('gamma', gutter=G)
    before = tty.term.text()
    comp.toggle(b1)
    assert tty.term.text() == '» alpha … (+1 lines)\n» gamma\n>'
    assert tty.term.style(8, 0)['faint'] and not tty.term.style(2, 0)['faint']  # the count is dim, the content is not
    comp.toggle(b1)
    assert tty.term.text() == before
    assert parked(tty, comp)
    assert tty.term.contents().count('beta') == 1

def test_click_toggles():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    b1 = comp.print_block('headline\nhidden body', gutter=G)
    click_row(tty, comp, b1.id)
    assert b1.collapsed
    assert 'hidden body' not in tty.term.text()
    click_row(tty, comp, b1.id)
    assert not b1.collapsed
    assert 'hidden body' in tty.term.text()

def test_single_line_blocks_not_toggleable():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    b = comp.print_block('only line', gutter=G)
    click_row(tty, comp, b.id)
    assert not b.collapsed
    comp.toggle(b)
    assert not b.collapsed  # nothing to hide

def test_straddler_stays_toggleable():
    "A block whose top rows have inked still toggles from its visible rows: the screen redraws from the model (policy 2)."
    tty, comp = make(30, 6)
    comp.set_tail('> ')
    bs = [comp.print_block(f'top {i}\nbot {i}', gutter=G) for i in range(5)]
    assert comp._ws == 5                      # bs[2]'s top row inked, its bot row visible
    click_row(tty, comp, bs[2].id)
    assert bs[2].collapsed
    scr = tty.term.text().splitlines()
    assert '» top 2 … (+1 lines)' in scr      # rematerialized in its current (folded) state
    assert scr[-1] == '>'                     # window slid back: screen full, tail in place
    assert parked(tty, comp)

def test_fold_back_rematerializes():
    "Folding the big block slides the window back over already-inked rows, hole-free."
    tty, comp = make(30, 8)
    comp.set_tail('> ')
    comp.print_block('intro', gutter=G)
    big = comp.print_block('\n'.join(f'row {i}' for i in range(12)), gutter=G)
    assert comp._ws > 0 and 'intro' not in tty.term.text()
    comp.toggle(big)
    scr = tty.term.text().splitlines()
    assert scr[0] == '» intro'                # back on screen in current state
    assert '» row 0 … (+11 lines)' in scr
    assert parked(tty, comp)

def test_progressive_ink():
    "A streaming block taller than the screen inks its top rows mid-stream, each exactly once."
    tty, comp = make(30, 6)
    comp.set_tail('> ')
    blk = comp.print_block(gutter=G)
    for i in range(15): comp.extend(blk, f'chunk {i}')
    lines = tty.term.contents().splitlines()
    for i in range(15): assert sum(1 for l in lines if l.endswith(f'chunk {i}')) == 1
    assert parked(tty, comp)

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
    assert parked(tty, comp)

def test_born_over_threshold_collapses():
    tty, comp = make(40, 10)
    comp.set_tail('> ')
    b = comp.print_block('\n'.join(f'r{i}' for i in range(8)), gutter=G, collapse_at=3)
    assert b.collapsed
    assert '» r0 … (+7 lines)' in tty.term.text()
    assert 'r5' not in tty.term.contents()

def test_tail_updates():
    tty, comp = make(20, 6)
    comp.set_tail('status: 0', '> ')
    comp.print_block('x')
    for i in range(1, 4):
        comp.set_tail(f'status: {i}', '> ')
        assert tty.term.text().splitlines()[-2] == f'status: {i}'
        assert parked(tty, comp)

def test_tail_never_inks():
    "The tail is chrome: however much content scrolls past, history holds no copy of it."
    tty, comp = make(30, 6)
    comp.set_tail('STATUSLINE', '> ')
    for i in range(12): comp.print_block(f'block {i}', gutter=G)
    assert tty.term.contents().count('STATUSLINE') == 1  # the on-screen one; none in scrollback

def test_resize_then_everything_still_works():
    "Resize is just a frame at the new size: nothing demotes, toggles keep working."
    tty, comp = make(30, 8)
    comp.set_tail('> ')
    b1 = comp.print_block('stuff\nmore', gutter=G)
    tty.term.resize(40, 8)
    comp.resize()
    comp.set_tail('> ')  # the app repaints its tail after a resize (rendered tail rows are width-stale)
    comp.print_block('after', gutter=G)
    assert 'after' in tty.term.text()
    comp.toggle(b1)
    assert b1.collapsed and '» stuff … (+1 lines)' in tty.term.text()
    assert parked(tty, comp)

def test_height_shrink_inks_overflow():
    tty, comp = make(30, 10)
    comp.set_tail('> ')
    for i in range(7): comp.print_block(f'line {i}', gutter=G)
    tty.term.resize(30, 5)
    comp.resize()
    comp.set_tail('> ')
    scr = tty.term.text().splitlines()
    assert scr[-1] == '>' and scr[-2] == '» line 6'   # the newest content hugs the tail
    lines = tty.term.contents().splitlines()
    for i in range(7): assert f'» line {i}' in lines  # nothing lost: overflow inked
    assert parked(tty, comp)

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
    assert parked(tty, comp)

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
    assert tty.term.cursor == (4, 1)  # tail line 1, col 4 (region starts at row 0 on a fresh screen)
    assert parked(tty, comp)

def test_cursor_parking_survives_ops():
    tty, comp = make(30, 8)
    comp.set_tail('status', '> ', cursor=(1, 2))
    assert parked(tty, comp)
    comp.print_block('body', gutter=G)
    assert parked(tty, comp)
    comp.set_tail('status', '> x', cursor=(0, 3))  # cursor on the FIRST tail line
    x, y = tty.term.cursor
    assert x == 3 and tty.term.text().splitlines()[y] == 'status'
    comp.print_block('more', gutter=G)
    assert parked(tty, comp)

def test_cursor_parking_wide_chars():
    tty, comp = make(30, 8)
    comp.set_tail('> 日本', cursor=(0, 2 + 4))
    assert tty.term.cursor == (6, 0)

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
    assert tty.term.cursor[0] == 7            # 2 prompt + 3 ascii + 2 wide cells
    comp.on_bytes(b'\x02\x02\x02X')           # ctrl+b x3, insert
    assert tty.term.text().splitlines()[-1] == '> hXi 日'
    comp.on_bytes(b'\x05\r')                  # ctrl+e then enter
    scr = tty.term.text().splitlines()
    assert '» hXi 日' in scr and 'echo: hXi 日' in scr
    assert scr[-1] == '>'  # buffer cleared, prompt trailing space trimmed by the formatter
    assert parked(tty, comp)

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
    assert tty.term.cursor[0] == 4  # parked at the empty prompt throughout

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
    once overflowed the row, and a real terminal's autowrap sheared the frame (found live, 2026-07-24)."""
    from rich.syntax import Syntax
    from rich.cells import cell_len
    tty, comp = make(40, 12)
    comp.set_tail(Text('status line'), Text('> '))
    hl = Syntax('', 'python', theme='monokai').highlight('x = 6*7')
    comp.print_block(hl, gutter=(Text('>>> '), Text('... ')))
    for e in comp._screen:
        if e: assert sum(cell_len(s.text) for s in e[1]) <= 40
    scr = tty.term.text().splitlines()
    assert scr[0].startswith('>>> x = 6*7')
    assert scr[-2] == 'status line' and scr[-1] == '>'

def test_collapsed_summary_cropped():
    "gutter + first line + the dim count can exceed the width; the composed line is cropped, not wrapped."
    from rich.cells import cell_len
    tty, comp = make(30, 10)
    comp.set_tail(Text('tail'))
    b = comp.print_block('y' * 28, gutter=(Text('» '), Text('  ')), collapse_at=2)
    comp.extend(b, 'z' * 28)
    comp.extend(b, 'w' * 28)
    assert b.collapsed
    for bid, segs in comp._doc_rows():
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
    "The borrow choreography: tail erased (chrome), the epoch ends; job bytes flow raw; reanchor resumes below."
    tty, comp = make(40, 10)
    b1 = comp.print_block('block one', gutter=G)
    comp.set_tail(Text('status'), Text('> '), cursor=(1, 2))
    comp.release()
    scr = tty.term.text()
    assert 'block one' in scr and 'status' not in scr  # tail gone from glass, content stays
    assert b1.committed and comp._epoch == [] and comp._tail == []
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

def test_transients_take_free_rows_first():
    "While the region is still filling the screen, `over` rows slide the tail down into free rows: nothing covered, nothing inked."
    tty, comp = make(30, 10)
    comp.set_tail('status', '> ')
    comp.print_block('one', gutter=G)
    comp.set_tail('status', '> ', over=[Text('menu')])
    assert tty.term.text().splitlines() == ['» one', 'menu', 'status', '>']
    assert comp._ws == 0
    comp.set_tail('status', '> ')  # closing is just the next frame without it
    assert tty.term.text().splitlines() == ['» one', 'status', '>']
    assert tty.term.contents().count('menu') == 0  # never inked

def test_transients_cover_and_never_ink():
    "Once the region spans the screen, `over` rows cover the newest transcript rows; ws never moves, so nothing inks and closing restores."
    tty, comp = make(30, 8)
    comp.set_tail('status', '> ')
    for i in range(10): comp.print_block(f'line {i}', gutter=G)
    ws0 = comp._ws
    before = tty.term.text()
    comp.set_tail('status', '> ', over=[Text('MENUROW')])
    assert comp._ws == ws0                       # over never advances the window
    scr = tty.term.text().splitlines()
    assert scr[-3:] == ['MENUROW', 'status', '>']
    assert '» line 9' not in scr                 # the newest row is covered, not scrolled
    comp.set_tail('status', '> ')
    assert tty.term.text() == before             # restored exactly
    assert tty.term.contents().count('MENUROW') == 0

def test_covered_rows_not_clickable():
    "A click on a transient row is inert: the screen map holds the transient there, not the covered block."
    tty, comp = make(30, 8)
    comp.set_tail('status', '> ')
    bs = [comp.print_block(f'top {i}\nbot {i}', gutter=G) for i in range(4)]
    comp.set_tail('status', '> ', over=[Text('menu')])
    y = next(y for y, e in enumerate(comp._screen) if e and 'menu' in ''.join(s.text for s in e[1]))
    comp.on_bytes(b'\x1b[<0;1;%dM' % (y + 1))
    assert not any(b.collapsed for b in bs)      # nothing toggled
    assert parked(tty, comp)

def test_act_rows_clickable():
    "Tail/transient rows carrying Style(meta={'act': ...}) dispatch through on_act; rows without meta stay inert."
    from rich.style import Style
    tty, comp = make(30, 8)
    hits = []
    comp.on_act = hits.append
    btn = Text('[mode]', style=Style(meta={'act': 'mode'})) + Text(' status')
    comp.set_tail(btn, '> ')
    comp.print_block('body', gutter=G)
    y = next(y for y, e in enumerate(comp._screen) if e and '[mode]' in ''.join(s.text for s in e[1]))
    comp.on_bytes(b'\x1b[<0;2;%dM' % (y + 1))
    assert hits == ['mode']
    yp = next(y for y, e in enumerate(comp._screen) if e and ''.join(s.text for s in e[1]).startswith('>'))
    comp.on_bytes(b'\x1b[<0;1;%dM' % (yp + 1))
    assert hits == ['mode']  # the plain prompt row is not a target

G3 = (Text('»»» ', style='bold green'), Text('··· ', style='dim'))  # the 3-char x\dx gutter scheme

def test_ambient_numbering():
    "Newest visible toggleable blocks wear digits (0 = newest) in the gutter's middle cell; one-liners skipped without consuming a digit."
    tty, comp = make(40, 12)
    comp.numbering = True
    comp.set_tail('> ')
    b_old = comp.print_block('aa\nbb', gutter=G3)
    comp.print_block('one liner', gutter=G3)
    b_new = comp.print_block('cc\ndd', gutter=G3)
    scr = tty.term.text().splitlines()
    assert scr[0] == '»1» aa' and scr[2] == '»»» one liner' and scr[3] == '»0» cc'
    assert comp.numbered == {'0': b_new.id, '1': b_old.id}
    comp.toggle(comp.blocks[comp.numbered['1']])   # the app's alt-1 gesture
    assert b_old.collapsed and '»1» aa … (+1 lines)' in tty.term.text()

def test_numbering_shifts_on_append():
    tty, comp = make(40, 12)
    comp.numbering = True
    comp.set_tail('> ')
    b1 = comp.print_block('aa\nbb', gutter=G3)
    assert comp.numbered == {'0': b1.id}
    b2 = comp.print_block('cc\ndd', gutter=G3)
    assert comp.numbered == {'0': b2.id, '1': b1.id}
    assert '»1» aa' in tty.term.text() and '»0» cc' in tty.term.text()

def test_straddler_wears_no_digit_and_digits_ink():
    "A block whose first row has inked wears no digit, and the crossing row de-numbers naturally: digits are a window property, so history stays digit-free with no strip code."
    tty, comp = make(40, 6)
    comp.numbering = True
    comp.set_tail('> ')
    big = comp.print_block('\n'.join(f'r{i}' for i in range(5)), gutter=G3)
    comp.print_block('tail block\nx', gutter=G3)
    assert comp._ws > 0 and comp._spans[big.id][0] < comp._ws   # big's first row inked
    assert big.id not in comp.numbered.values()                 # ...so it wears no digit
    lines = tty.term.contents().splitlines()
    assert any(l.startswith('»»» r0') for l in lines)          # its first row crossed DE-numbered: numbering is a window property, so digits never reach history (flagged for Jeremy)

def test_dim_blocks():
    "blk.dim mutes a block on both surfaces: content and gutter render dim, and refresh_block repaints it live."
    tty, comp = make(40, 8)
    comp.set_tail('> ')
    b = comp.print_block('secret\nstuff', gutter=G)
    comp.print_block('after', gutter=G)
    assert not tty.term.style(0, 0)['faint']       # gutter cell, normal before the flip
    b.dim = True
    comp.refresh_block(b)
    assert tty.term.style(0, 0)['faint'] and tty.term.style(4, 1)['faint']   # gutter + body rows dim
    assert not tty.term.style(0, 2)['faint']       # the neighbouring block is untouched
    b.dim = False
    comp.refresh_block(b)
    assert not tty.term.style(0, 0)['faint']       # and the flip reverses cleanly

def test_transients_grow_a_young_region():
    "The picker-clip bug: a transient taller than the young region scrolls shell rows for room (the startup-growth move); every over row and the tail stay on screen; nothing inks."
    tty = EmuTty(30, 10)
    tty.write('\r\n'.join(f'shell {i}' for i in range(9)).encode())  # a full screen of shell history: the region is born tiny at the bottom
    comp = Compositor(tty).start()
    comp.set_tail('status', '> ')
    comp.print_block('hint', gutter=G)
    comp.set_tail('status', '> ', over=[Text(f'pick {i}') for i in range(5)])
    scr = tty.term.text().splitlines()
    for i in range(5): assert f'pick {i}' in scr     # every transient row on screen...
    assert 'status' in scr and '>' in scr            # ...with the tail below, never clipped
    assert comp._ws == 0                             # nothing of ours inked for it
    assert parked(tty, comp)
    comp.set_tail('status', '> ')
    assert 'pick 0' not in tty.term.text()           # evaporates without a trace
    assert '» hint' in tty.term.text().splitlines()  # and the content row is back on show

def test_set_body():
    "Model-first editing: set_body swaps content and re-measures; the live region shows the new text on the next frame; scrollback keeps the old (the log is a log)."
    tty, comp = make(30, 8)
    comp.set_tail('> ')
    b = comp.print_block('old text', gutter=G)
    comp.print_block('after', gutter=G)
    comp.set_body(b, 'new one\nnew two', source='new one\nnew two')
    assert b.height == 2 and b.source.startswith('new')
    comp.refresh_block(b)
    scr = tty.term.text()
    assert 'new one' in scr and 'new two' in scr and 'old text' not in scr
    assert 'after' in scr                       # the neighbour is untouched, order kept

def test_remove_block():
    "Conversation rewind: removed blocks leave the window (the model as it now stands); inked rows stay in history; a later block keeps rendering."
    tty, comp = make(30, 8)
    comp.set_tail('> ')
    keep = comp.print_block('keep me', gutter=G)
    b = comp.print_block('drop one\ndrop two', gutter=G)
    comp.print_block('tail block', gutter=G)
    comp.remove_block(b)
    scr = tty.term.text()
    assert 'drop one' not in scr and 'keep me' in scr and 'tail block' in scr
    assert b.id not in comp.blocks and b.id not in comp._epoch
    assert parked(tty, comp)
    comp.toggle(keep) if keep.height > 1 else None  # the survivors still behave (no stale spans)

def test_pad_block():
    "pad renders one leading blank presentation row; the digit stays on the content row; the pad is never content."
    tty, comp = make(40, 12)
    comp.numbering = True
    comp.set_tail('> ')
    comp.print_block('before', gutter=G3)
    b = comp.print_block('one\ntwo', gutter=G3, pad=True)
    scr = tty.term.text().splitlines()
    assert scr[:4] == ['»»» before', '', '»0» one', '··· two']
    assert b.height == 2
    comp.toggle(b)
    assert tty.term.text().splitlines()[2] == '»0» one … (+1 lines)'
    assert parked(tty, comp)
