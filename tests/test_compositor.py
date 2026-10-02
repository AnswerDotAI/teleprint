import asyncio, gc, os, signal
from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.testing import EmuTty

G = (Text('» ', style='green'), Text('  '))  # 2-cell gutter: the glyph carries the click target

async def make(cols=30, rows=8):
    tty = EmuTty(cols, rows)
    return tty, await Compositor(tty).start()

def parked(tty, comp):
    "Standing invariant: the emulator's cursor is exactly where the frame said it parked it."
    return tty.term.cursor == (comp._cursor[1], comp._cursor[0])

def click_row(tty, comp, bid):
    "Send a real SGR press on the first visible row of block `bid` (1-indexed on the wire)."
    y = next(y for y, e in enumerate(comp._screen) if e and e[0] == bid)
    comp.on_bytes(b'\x1b[<0;1;%dM' % (y + 1))

async def test_blocks_and_tail():
    tty, comp = await make()
    comp.set_tail('status: ok', '> ')
    comp.put('b1', 'body one\nmore one', gutter=G)
    comp.put('b2', 'body two', gutter=G)
    assert tty.term.text() == '» body one\n  more one\n» body two\nstatus: ok\n>'
    assert parked(tty, comp)

async def test_scroll_inks_each_row_once():
    tty, comp = await make(30, 6)
    comp.set_tail('> ')
    bs = [comp.put(f'b{i}', f'top {i}\nbot {i}', gutter=G) for i in range(5)]
    assert comp._ws == 5  # 10 content rows, 5 fit above the tail
    lines = tty.term.contents().splitlines()
    for i in range(5): assert lines.count(f'» top {i}') == 1 and lines.count(f'  bot {i}') == 1
    assert not any(b.committed for b in bs)  # write-once: everything stays in the document
    assert parked(tty, comp)

async def test_lazy_claim_preserves_shell():
    "Startup adopts the shell's cursor row: its screen stays put and scrolls off only as content needs the space."
    tty = EmuTty(30, 8)
    tty.term.feed(b'aai-ws $ some old command\r\naai-ws $ ')  # a shell mid-session
    comp = await Compositor(tty).start()
    comp.set_tail('> ')
    comp.put('first', 'first', gutter=G)
    scr = tty.term.text().splitlines()
    assert scr[0] == 'aai-ws $ some old command'      # untouched above the region
    assert scr[1].startswith('aai-ws $')
    assert scr[2] == '» first'
    for i in range(8): comp.put(f'b{i}', f'b{i}', gutter=G)
    lines = tty.term.contents().splitlines()
    assert lines.count('aai-ws $ some old command') == 1  # scrolled into history intact, once
    assert parked(tty, comp)

async def test_toggle_in_place():
    tty, comp = await make(30, 10)
    comp.set_tail('> ')
    b1 = comp.put('b1', 'alpha\nbeta', gutter=G)
    comp.put('gamma', 'gamma', gutter=G)
    before = tty.term.text()
    comp.toggle(b1.key)
    assert tty.term.text() == '» alpha … (+1 lines)\n» gamma\n>'
    assert tty.term.style(8, 0)['faint'] and not tty.term.style(2, 0)['faint']  # the count is dim, the content is not
    comp.toggle(b1.key)
    assert tty.term.text() == before
    assert parked(tty, comp)
    assert tty.term.contents().count('beta') == 1

async def test_click_toggles():
    tty, comp = await make(30, 10)
    comp.set_tail('> ')
    b1 = comp.put('b1', 'headline\nhidden body', gutter=G)
    click_row(tty, comp, b1.key)
    assert b1.collapsed
    assert 'hidden body' not in tty.term.text()
    click_row(tty, comp, b1.key)
    assert not b1.collapsed
    assert 'hidden body' in tty.term.text()

async def test_single_line_blocks_not_toggleable():
    tty, comp = await make(30, 10)
    comp.set_tail('> ')
    b = comp.put('b', 'only line', gutter=G)
    click_row(tty, comp, b.key)
    assert not b.collapsed
    comp.toggle(b.key)
    assert not b.collapsed  # nothing to hide

async def test_straddler_stays_toggleable():
    "A block whose top rows have inked still toggles from its visible rows: the screen redraws from the model (policy 2)."
    tty, comp = await make(30, 6)
    comp.set_tail('> ')
    bs = [comp.put(f'b{i}', f'top {i}\nbot {i}', gutter=G) for i in range(5)]
    assert comp._ws == 5                      # bs[2]'s top row inked, its bot row visible
    click_row(tty, comp, bs[2].key)
    assert bs[2].collapsed
    scr = tty.term.text().splitlines()
    assert '» top 2 … (+1 lines)' in scr      # rematerialized in its current (folded) state
    assert scr[-1] == '>'                     # window slid back: screen full, tail in place
    assert parked(tty, comp)

async def test_fold_back_rematerializes():
    "Folding the big block slides the window back over already-inked rows, hole-free."
    tty, comp = await make(30, 8)
    comp.set_tail('> ')
    comp.put('intro', 'intro', gutter=G)
    big = comp.put('big', '\n'.join(f'row {i}' for i in range(12)), gutter=G)
    assert comp._ws > 0 and 'intro' not in tty.term.text()
    comp.toggle(big.key)
    scr = tty.term.text().splitlines()
    assert scr[0] == '» intro'                # back on screen in current state
    assert '» row 0 … (+11 lines)' in scr
    assert parked(tty, comp)

async def test_progressive_ink():
    "A streaming block taller than the screen inks its top rows mid-stream, each exactly once."
    tty, comp = await make(30, 6)
    comp.set_tail('> ')
    blk = comp.put('blk', gutter=G)
    for i in range(15): comp.extend(blk.key, f'chunk {i}')
    lines = tty.term.contents().splitlines()
    for i in range(15): assert sum(1 for l in lines if l.endswith(f'chunk {i}')) == 1
    assert parked(tty, comp)

async def test_stream_collapse_at_threshold():
    tty, comp = await make(40, 12)
    comp.set_tail('> ')
    blk = comp.put('blk', gutter=G, collapse_at=4)
    for i in range(4): comp.extend(blk.key, f'line {i}')
    assert not blk.collapsed
    comp.extend(blk.key, 'line 4')  # crossing the threshold folds to the summary line
    assert blk.collapsed
    assert '» line 0 … (+4 lines)' in tty.term.text()
    comp.extend(blk.key, 'line 5')  # ...which keeps counting while the model grows
    assert '… (+5 lines)' in tty.term.text()
    assert 'line 3' not in tty.term.text()
    comp.toggle(blk.key)  # re-expand shows everything accumulated
    assert not blk.collapsed and 'line 3' in tty.term.text() and 'line 5' in tty.term.text()
    comp.extend(blk.key, 'line 6')  # the user expanded it, so growth never refolds it
    assert not blk.collapsed and 'line 6' in tty.term.text()
    assert parked(tty, comp)

async def test_born_over_threshold_collapses():
    tty, comp = await make(40, 10)
    comp.set_tail('> ')
    b = comp.put('b', '\n'.join(f'r{i}' for i in range(8)), gutter=G, collapse_at=3)
    assert b.collapsed
    assert '» r0 … (+7 lines)' in tty.term.text()
    assert 'r5' not in tty.term.contents()

async def test_tail_updates():
    tty, comp = await make(20, 6)
    comp.set_tail('status: 0', '> ')
    comp.put('x', 'x')
    for i in range(1, 4):
        comp.set_tail(f'status: {i}', '> ')
        assert tty.term.text().splitlines()[-2] == f'status: {i}'
        assert parked(tty, comp)

async def test_tail_never_inks():
    "The tail is chrome: however much content scrolls past, history holds no copy of it."
    tty, comp = await make(30, 6)
    comp.set_tail('STATUSLINE', '> ')
    for i in range(12): comp.put(f'b{i}', f'block {i}', gutter=G)
    assert tty.term.contents().count('STATUSLINE') == 1  # the on-screen one; none in scrollback

async def test_resize_then_everything_still_works():
    "Resize is just a frame at the new size: nothing demotes, toggles keep working."
    tty, comp = await make(30, 8)
    comp.set_tail('> ')
    b1 = comp.put('b1', 'stuff\nmore', gutter=G)
    tty.term.resize(120, 8)
    comp.resize()
    status = 's' * 100
    comp.set_tail(status, '> ')  # the app repaints its tail after a resize (rendered tail rows are width-stale)
    comp.put('after', 'after', gutter=G)
    assert status in tty.term.text() and 'after' in tty.term.text()  # rendering follows the owned tty, not the host process's width
    comp.toggle(b1.key)
    assert b1.collapsed and '» stuff … (+1 lines)' in tty.term.text()
    assert parked(tty, comp)

async def test_height_shrink_inks_overflow():
    tty, comp = await make(30, 10)
    comp.set_tail('> ')
    for i in range(7): comp.put(f'b{i}', f'line {i}', gutter=G)
    tty.term.resize(30, 5)
    comp.resize()
    comp.set_tail('> ')
    scr = tty.term.text().splitlines()
    assert scr[-1] == '>' and scr[-2] == '» line 6'   # the newest content hugs the tail
    lines = tty.term.contents().splitlines()
    for i in range(7): assert f'» line {i}' in lines  # nothing lost: overflow inked
    assert parked(tty, comp)

async def test_resize_does_not_duplicate_tail():
    "Zoom toggles must not accumulate tail copies in the transcript: the tail is chrome, not history."
    tty, comp = await make(40, 10)
    comp.set_tail('hint line', '> ', cursor=(1, 2))
    comp.put('body', 'body', gutter=G)
    for cols in (80, 40, 80):  # zoom in, out, in
        tty.term.resize(cols, 10)
        comp.resize()
        comp.set_tail('hint line', '> ', cursor=(1, 2))
    lines = tty.term.contents().splitlines()
    assert lines.count('hint line') == 1
    assert lines.count('» body') == 1
    assert parked(tty, comp)

async def test_wheel_hook_and_partial_escapes():
    tty, comp = await make()
    comp.set_tail('> ')
    b = comp.put('b', 'body\nmore', gutter=G)
    hits = []
    comp.on_wheel = hits.append
    comp.on_bytes(b'\x1b[<64;5;3M\x1b[<65;5;3M\x1b[<0;1;')  # two wheel events + a split click
    assert hits == [-1, 1]
    comp.on_bytes(b'1M')  # completes the press on row 1: the block's first line
    assert b.collapsed
    assert comp._parser._buf == b''  # nothing left buffered once sequences complete

async def test_cursor_parks_at_tail_cursor():
    tty, comp = await make(30, 8)
    comp.set_tail('status', '> hi', cursor=(1, 4))
    assert tty.term.cursor == (4, 1)  # tail line 1, col 4 (region starts at row 0 on a fresh screen)
    assert parked(tty, comp)

async def test_cursor_parking_survives_ops():
    tty, comp = await make(30, 8)
    comp.set_tail('status', '> ', cursor=(1, 2))
    assert parked(tty, comp)
    comp.put('body', 'body', gutter=G)
    assert parked(tty, comp)
    comp.set_tail('status', '> x', cursor=(0, 3))  # cursor on the FIRST tail line
    x, y = tty.term.cursor
    assert x == 3 and tty.term.text().splitlines()[y] == 'status'
    comp.put('more', 'more', gutter=G)
    assert parked(tty, comp)

async def test_cursor_parking_wide_chars():
    tty, comp = await make(30, 8)
    comp.set_tail('> 日本', cursor=(0, 2 + 4))
    assert tty.term.cursor == (6, 0)

async def test_repl_shape_typing_and_enter():
    "The echo-REPL wiring, headless: keys through the parser edit the tail; Enter prints blocks."
    from teleprint.buffer import Buffer
    tty, comp = await make(40, 10)
    buf = Buffer()
    def paint(): comp.set_tail(Text('> ') + Text(buf.text), cursor=(0, buf.cell_cursor('> ')))
    def on_key(k):
        if k.name == 'enter':
            line = buf.text
            buf.clear()
            comp.put(f'in{len(comp.blocks)}', line, gutter=G)
            comp.put(f'out{len(comp.blocks)}', f'echo: {line}')
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

async def test_python_repl_with_gateway():
    "The pyrepl wiring headless: a real gateway kernel's outputs land as blocks in the emulator."
    from teleprint.buffer import Buffer
    from jupygate.core import create_app, serve
    from jupyasyncclient import JupyAsyncKernelClient
    tty, comp = await make(50, 12)
    server = serve(create_app(), port=0, in_thread=True)
    try:
        kc = await JupyAsyncKernelClient.connect(server.url)
        buf = Buffer()
        state = {'stream': None}
        pending = []
        def paint(): comp.set_tail(Text('>>> ') + Text(buf.text), cursor=(0, buf.cell_cursor('>>> ')))
        def on_key(k):
            if k.name == 'enter':
                comp.put(f'b{len(comp.blocks)}', buf.text, gutter=G)
                state['stream'] = None
                pending.append(buf.text)
                buf.clear()
            else: buf.handle(k)
            paint()
        comp.on_key = on_key
        paint()
        async def run_submitted():
            for o in await kc.exec_outs(pending.pop()):
                txt = o.get('text', '')
                txt = ''.join(txt) if isinstance(txt, list) else txt
                if o['output_type'] == 'stream':
                    if state['stream'] is None: state['stream'] = comp.put(f'b{len(comp.blocks)}', gutter=G).key
                    comp.extend(state['stream'], txt.rstrip('\n'))
                elif o['output_type'] == 'execute_result': comp.put(f'b{len(comp.blocks)}', ''.join(o['data']['text/plain']))
        comp.on_bytes(b'6*7\r')
        await run_submitted()
        scr = tty.term.text().splitlines()
        assert '» 6*7' in scr and '42' in scr
        comp.on_bytes(b'print("hello teleprint")\r')
        await run_submitted()
        assert 'hello teleprint' in tty.term.text()
        matches, _ = await kc.complete('import o', 8)
        assert 'os' in matches
        await kc.shutdown_kernel()
        await kc.aclose()
    finally: server.should_exit = True
    assert tty.term.cursor[0] == 4  # parked at the empty prompt throughout

async def test_tail_cursor_renderable_form():
    "The (renderable_idx, line_within, col) cursor form survives other renderables wrapping."
    tty, comp = await make(20, 8)
    status = 'a status line that certainly wraps at twenty columns'
    comp.set_tail(status, Text('>>> ab\n... cd'), cursor=(1, 1, 6))
    x, y = tty.term.cursor
    scr = tty.term.text().splitlines()
    assert scr[y] == '... cd' and x == 6

async def test_on_ctl_hook():
    "Control strings reach on_ctl as events and never leak keystrokes into on_key."
    from teleprint.keys import Ctl
    tty, comp = await make()
    got, keys = [], []
    comp.on_ctl = got.append
    comp.on_key = keys.append
    comp.on_bytes(b'\x1b]11;rgb:1111/2222/3333\x1b\\ab')
    assert got == [Ctl('osc', '11;rgb:1111/2222/3333')]
    assert [k.name for k in keys] == ['a', 'b']

async def test_full_width_renderable_never_wraps():
    """A Syntax with a background theme renders full console width; with the gutter in front that
    once overflowed the row, and a real terminal's autowrap sheared the frame (found live, 2026-07-24)."""
    from rich.syntax import Syntax
    from rich.cells import cell_len
    tty, comp = await make(40, 12)
    comp.set_tail(Text('status line'), Text('> '))
    hl = Syntax('', 'python', theme='monokai').highlight('x = 6*7')
    comp.put('code', hl, gutter=(Text('>>> '), Text('... ')))
    for e in comp._screen:
        if e: assert sum(cell_len(s.text) for s in e[1]) <= 40
    scr = tty.term.text().splitlines()
    assert scr[0].startswith('>>> x = 6*7')
    assert scr[-2] == 'status line' and scr[-1] == '>'

async def test_collapsed_summary_cropped():
    "gutter + first line + the dim count can exceed the width; the composed line is cropped, not wrapped."
    from rich.cells import cell_len
    tty, comp = await make(30, 10)
    comp.set_tail(Text('tail'))
    b = comp.put('b', 'y' * 28, gutter=(Text('» '), Text('  ')), collapse_at=2)
    comp.extend(b.key, 'z' * 28)
    comp.extend(b.key, 'w' * 28)
    assert b.collapsed
    for bid, segs in comp._doc_rows(): assert sum(cell_len(s.text) for s in segs) <= 30
    assert tty.term.text().splitlines()[-1] == 'tail'

async def test_put_without_ink_paints_nothing():
    tty, comp = await make(40, 10)
    comp.put('visible', 'visible one', gutter=G)
    before = tty.term.text()
    blk = comp.put('sh', 'line a\nline b\nline c', gutter=G, collapse_at=2, ink=False)
    assert tty.term.text() == before          # nothing painted
    assert blk.committed and blk.collapsed    # in the model, folded past its threshold
    assert blk.height == 3 and comp.blocks['sh'] is blk

async def test_borrow():
    "The borrow choreography: the tail (chrome) is erased and the epoch ends; the borrower writes directly; afterwards the region resumes below its output, and blocks put meanwhile paint then."
    tty, comp = await make(40, 10)
    b1 = comp.put('b1', 'block one', gutter=G)
    comp.set_tail(Text('status'), Text('> '), cursor=(1, 2))
    async with comp.borrow() as t:
        scr = tty.term.text()
        assert 'block one' in scr and 'status' not in scr  # tail gone from glass, content stays
        assert b1.committed and comp._epoch == [] and comp._tail == []
        t.write(b'job says hi\r\njob line two\r\n')        # the borrower prints directly
        comp.put('late', 'put during the job', gutter=G)
        assert 'put during' not in tty.term.text()        # frames are paused: nothing paints over the job
    comp.put('after', 'after the job', gutter=G)
    comp.set_tail(Text('> '))
    lines = tty.term.text().splitlines()
    assert lines.index('job says hi') < lines.index('» put during the job') < lines.index('» after the job') < lines.index('>')
    assert 'status' not in tty.term.contents()          # the erased tail never entered history

async def test_borrow_turns_modes_off_then_on():
    "The borrowed program gets no mouse reports or paste brackets: the modes are off for the borrow and on again when it ends."
    tty, comp = await make(40, 10)
    modes = (1000, 1006, 2004)
    assert all(tty.term.mode(m) for m in modes)
    async with comp.borrow():
        assert not any(tty.term.mode(m) for m in modes)
    assert all(tty.term.mode(m) for m in modes)

async def test_borrow_with_no_tail():
    tty, comp = await make(40, 10)
    comp.put('only', 'only block', gutter=G)
    async with comp.borrow() as t: t.write(b'raw\r\n')
    comp.set_tail(Text('> '))
    lines = tty.term.text().splitlines()
    assert lines.index('» only block') < lines.index('raw') < lines.index('>')

async def test_transients_take_free_rows_first():
    "While the region is still filling the screen, `over` rows slide the tail down into free rows: nothing covered, nothing inked."
    tty, comp = await make(30, 10)
    comp.set_tail('status', '> ')
    comp.put('one', 'one', gutter=G)
    comp.set_tail('status', '> ', over=[Text('menu')])
    assert tty.term.text().splitlines() == ['» one', 'menu', 'status', '>']
    assert comp._ws == 0
    comp.set_tail('status', '> ')  # closing is just the next frame without it
    assert tty.term.text().splitlines() == ['» one', 'status', '>']
    assert tty.term.contents().count('menu') == 0  # never inked

async def test_transients_cover_and_never_ink():
    "Once the region spans the screen, `over` rows cover the newest transcript rows; ws never moves, so nothing inks and closing restores."
    tty, comp = await make(30, 8)
    comp.set_tail('status', '> ')
    for i in range(10): comp.put(f'b{i}', f'line {i}', gutter=G)
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

async def test_covered_rows_not_clickable():
    "A click on a transient row is inert: the screen map holds the transient there, not the covered block."
    tty, comp = await make(30, 8)
    comp.set_tail('status', '> ')
    bs = [comp.put(f'b{i}', f'top {i}\nbot {i}', gutter=G) for i in range(4)]
    comp.set_tail('status', '> ', over=[Text('menu')])
    y = next(y for y, e in enumerate(comp._screen) if e and 'menu' in ''.join(s.text for s in e[1]))
    comp.on_bytes(b'\x1b[<0;1;%dM' % (y + 1))
    assert not any(b.collapsed for b in bs)      # nothing toggled
    assert parked(tty, comp)


async def test_put_after_inserts_in_document_order():
    "A new block goes after `after`: on screen when its successor is on screen, into the model only (committed) when its successor has been committed."
    tty, comp = await make(30, 10)
    comp.set_tail('> ')
    comp.put('a', 'alpha', gutter=G)
    comp.put('c', 'gamma', gutter=G)
    comp.put('b', 'beta', gutter=G, after='a')
    assert list(comp.blocks) == ['a', 'b', 'c'] and comp._epoch == ['a', 'b', 'c']
    assert tty.term.text().splitlines()[:3] == ['» alpha', '» beta', '» gamma']
    comp.commit()
    comp.put('d', 'delta', gutter=G)
    x = comp.put('x', 'late output', gutter=G, after='a')
    assert x.committed and comp._epoch == ['d'] and list(comp.blocks) == ['a', 'x', 'b', 'c', 'd']

async def test_stop_resets_terminal_modes():
    "start turns on SGR mouse reporting and bracketed paste; stop resets every mode still set, including ones the app set later."
    tty, comp = await make()
    assert all(tty.term.mode(m) for m in (1000, 1006, 2004))
    comp.set_modes(1049)
    assert tty.term.mode(1049)
    comp.stop()
    assert not any(tty.term.mode(m) for m in (1000, 1006, 2004, 1049))

async def test_input_after_cpr_is_dispatched():
    "A key that arrives in the same read as the startup CPR reply, behind it, still reaches on_key."
    tty = EmuTty(30, 8)
    comp = Compositor(tty)
    keys = []
    comp.on_key = keys.append
    w = tty.write
    def write(d):
        w(d)                                   # the emulator queues its reply to a CPR query here
        if d == '\x1b[6n': tty.seed(b'k')      # then the key, so both arrive in one read
    tty.write = write
    await comp.start()
    assert [k.name for k in keys] == ['k']

async def test_query_returns_reply_and_dispatches_keys():
    "query returns the raw reply bytes; a key read along the way still reaches on_key."
    tty = EmuTty(30, 8, bg=(250, 250, 244))
    comp = await Compositor(tty).start()
    keys = []
    comp.on_key = keys.append
    tty.seed(b'q')
    data = await comp.query(b'\x1b]11;?\x1b\\\x1b[c', rb'\x1b\[\?[0-9;]*c')
    assert b'rgb:' in data and [k.name for k in keys] == ['q']
async def test_act_rows_clickable():
    "Tail/transient rows carrying Style(meta={'act': ...}) dispatch through on_act; rows without meta stay inert."
    from rich.style import Style
    tty, comp = await make(30, 8)
    hits = []
    comp.on_act = hits.append
    btn = Text('[mode]', style=Style(meta={'act': 'mode'})) + Text(' status')
    comp.set_tail(btn, '> ')
    comp.put('body', 'body', gutter=G)
    y = next(y for y, e in enumerate(comp._screen) if e and '[mode]' in ''.join(s.text for s in e[1]))
    comp.on_bytes(b'\x1b[<0;2;%dM' % (y + 1))
    assert hits == ['mode']
    yp = next(y for y, e in enumerate(comp._screen) if e and ''.join(s.text for s in e[1]).startswith('>'))
    comp.on_bytes(b'\x1b[<0;1;%dM' % (yp + 1))
    assert hits == ['mode']  # the plain prompt row is not a target

G3 = (Text('»»» ', style='bold green'), Text('··· ', style='dim'))  # the 3-char x\dx gutter scheme

async def test_ambient_numbering():
    "Newest visible toggleable blocks wear digits (0 = newest) in the gutter's middle cell; one-liners skipped without consuming a digit."
    tty, comp = await make(40, 12)
    comp.numbering = True
    comp.set_tail('> ')
    b_old = comp.put('b_old', 'aa\nbb', gutter=G3)
    comp.put('one', 'one liner', gutter=G3)
    b_new = comp.put('b_new', 'cc\ndd', gutter=G3)
    scr = tty.term.text().splitlines()
    assert scr[0] == '»1» aa' and scr[2] == '»»» one liner' and scr[3] == '»0» cc'
    assert comp.numbered == {'0': b_new.key, '1': b_old.key}
    comp.toggle(comp.numbered['1'])   # the app's alt-1 gesture
    assert b_old.collapsed and '»1» aa … (+1 lines)' in tty.term.text()

async def test_numbering_shifts_on_append():
    tty, comp = await make(40, 12)
    comp.numbering = True
    comp.set_tail('> ')
    b1 = comp.put('b1', 'aa\nbb', gutter=G3)
    assert comp.numbered == {'0': b1.key}
    b2 = comp.put('b2', 'cc\ndd', gutter=G3)
    assert comp.numbered == {'0': b2.key, '1': b1.key}
    assert '»1» aa' in tty.term.text() and '»0» cc' in tty.term.text()

async def test_straddler_wears_no_digit_and_digits_ink():
    "A block whose first row has inked wears no digit, and the crossing row de-numbers naturally: digits are a window property, so history stays digit-free with no strip code."
    tty, comp = await make(40, 6)
    comp.numbering = True
    comp.set_tail('> ')
    big = comp.put('big', '\n'.join(f'r{i}' for i in range(5)), gutter=G3)
    comp.put('tail', 'tail block\nx', gutter=G3)
    assert comp._ws > 0 and comp._spans[big.key][0] < comp._ws   # big's first row inked
    assert big.key not in comp.numbered.values()                 # ...so it wears no digit
    lines = tty.term.contents().splitlines()
    assert any(l.startswith('»»» r0') for l in lines)          # its first row crossed DE-numbered: numbering is a window property, so digits never reach history (flagged for Jeremy)

async def test_dim_blocks():
    "`dim` mutes a block on both surfaces: gutter and content render dim, including a numbered first row, and a put with the same key repaints it live."
    tty, comp = await make(40, 8)
    comp.numbering = True
    comp.set_tail('> ')
    comp.put('b', 'secret\nstuff', gutter=G3)
    comp.put('after', 'after', gutter=G3)
    assert not tty.term.style(0, 0)['faint']       # gutter cell, normal before the flip
    comp.put('b', 'secret\nstuff', gutter=G3, dim=True)
    assert tty.term.text().splitlines()[0] == '»0» secret'
    assert tty.term.style(1, 0)['faint'] and tty.term.style(4, 1)['faint']   # the digit and the body rows are dim
    assert not tty.term.style(0, 2)['faint']       # the neighbouring block is untouched
    comp.put('b', 'secret\nstuff', gutter=G3)
    assert not tty.term.style(0, 0)['faint']       # and the flip reverses cleanly

async def test_transients_grow_a_young_region():
    "The picker-clip bug: a transient taller than the young region scrolls shell rows for room (the startup-growth move); every over row and the tail stay on screen; nothing inks."
    tty = EmuTty(30, 10)
    tty.write('\r\n'.join(f'shell {i}' for i in range(9)).encode())  # a full screen of shell history: the region is born tiny at the bottom
    comp = await Compositor(tty).start()
    comp.set_tail('status', '> ')
    comp.put('hint', 'hint', gutter=G)
    comp.set_tail('status', '> ', over=[Text(f'pick {i}') for i in range(5)])
    scr = tty.term.text().splitlines()
    for i in range(5): assert f'pick {i}' in scr     # every transient row on screen...
    assert 'status' in scr and '>' in scr            # ...with the tail below, never clipped
    assert comp._ws == 0                             # nothing of ours inked for it
    assert parked(tty, comp)
    comp.set_tail('status', '> ')
    assert 'pick 0' not in tty.term.text()           # evaporates without a trace
    assert '» hint' in tty.term.text().splitlines()  # and the content row is back on show

async def test_put_replaces_in_place():
    "Model-first editing: a put with an existing key swaps the content and re-measures, keeping the block's place and fold state; the live region shows the new text, and scrollback keeps the old (the log is a log)."
    tty, comp = await make(30, 8)
    comp.set_tail('> ')
    b = comp.put('b', 'old text\nold two', gutter=G)
    comp.put('after', 'after', gutter=G)
    comp.toggle('b')
    assert comp.put('b', 'new one\nnew two', gutter=G, source='new one\nnew two') is b
    assert b.height == 2 and b.source.startswith('new') and b.collapsed
    comp.toggle('b')
    scr = tty.term.text()
    assert 'new one' in scr and 'new two' in scr and 'old text' not in scr
    assert scr.index('new two') < scr.index('after')   # the neighbour is untouched, order kept

async def test_drop():
    "Conversation rewind: dropped blocks leave the window (the model as it now stands); inked rows stay in history; a later block keeps rendering."
    tty, comp = await make(30, 8)
    comp.set_tail('> ')
    comp.put('keep', 'keep me', gutter=G)
    comp.put('b', 'drop one\ndrop two', gutter=G)
    comp.put('tail', 'tail block', gutter=G)
    comp.drop('b')
    scr = tty.term.text()
    assert 'drop one' not in scr and 'keep me' in scr and 'tail block' in scr
    assert 'b' not in comp.blocks and 'b' not in comp._epoch
    assert parked(tty, comp)
    comp.toggle('keep')  # the survivors still behave (no stale spans)

async def test_pad_block():
    "pad renders one leading blank presentation row; the digit stays on the content row; the pad is never content."
    tty, comp = await make(40, 12)
    comp.numbering = True
    comp.set_tail('> ')
    comp.put('before', 'before', gutter=G3)
    b = comp.put('b', 'one\ntwo', gutter=G3, pad=True)
    scr = tty.term.text().splitlines()
    assert scr[:4] == ['»»» before', '', '»0» one', '··· two']
    assert b.height == 2
    comp.toggle(b.key)
    assert tty.term.text().splitlines()[2] == '»0» one … (+1 lines)'
    assert parked(tty, comp)


# -- the spawn contract (README "Background work, tasks, and errors") ----------

async def _cycles(n=3):
    "A few loop cycles: enough for a spawned task to run and its done callback to fire."
    for _ in range(n): await asyncio.sleep(0)

async def test_spawn_failure_reaches_hook_cancellation_does_not():
    "spawn returns the named Task; an uncaught failure arrives at on_task_error as (exc, task); a cancelled task never does."
    tty, comp = await make()
    seen = []
    comp.on_task_error = lambda e, t: seen.append((e, t))
    async def boom(): raise ValueError('kaboom')
    t = comp.spawn(boom(), name='boomer')
    assert t.get_name() == 'boomer'
    await _cycles()
    (e, et), = seen
    assert isinstance(e, ValueError) and et is t
    victim = comp.spawn(asyncio.sleep(60), name='victim')
    victim.cancel()
    await asyncio.gather(victim, return_exceptions=True)
    assert victim.cancelled() and len(seen) == 1    # cancellation is lifecycle, not failure

async def test_spawn_unset_hook_keeps_default_report():
    "With no on_task_error, spawn must NOT retrieve the exception: asyncio's never-retrieved report still fires at GC."
    tty, comp = await make()
    reports = []
    loop = asyncio.get_running_loop()
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda l, ctx: reports.append(ctx))
    try:
        async def boom(): raise ValueError('unobserved')
        t = comp.spawn(boom())
        await _cycles()
        assert t.done()
        del t                                       # our strong ref was already released by the done callback
        gc.collect()                                # Task.__del__ reports the never-retrieved exception
        assert any(isinstance(c.get('exception'), ValueError) for c in reports)
    finally: loop.set_exception_handler(old)

async def test_handler_returned_coroutine_spawned_and_reported():
    "A coroutine returned from on_key is spawned by the dispatcher; its failure lands in on_task_error."
    tty, comp = await make()
    seen = []
    comp.on_task_error = lambda e, t: seen.append(e)
    async def action(): raise RuntimeError('action failed')
    comp.on_key = lambda k: action()
    comp.on_bytes(b'x')
    await _cycles()
    assert len(seen) == 1 and isinstance(seen[0], RuntimeError)

async def test_spawn_keeps_dropped_handle_alive():
    "Fire-and-forget: a spawned task whose handle is not kept survives gc and completes (the strong-ref set)."
    tty, comp = await make()
    done = []
    async def work():
        await asyncio.sleep(0.01)
        done.append(1)
    comp.spawn(work())
    gc.collect()
    await asyncio.sleep(0.05)
    assert done == [1]


# -- the signal contract (README "Signals") ------------------------------------
# start() registers on the running loop (main thread), so delivery is testable with a
# self-kill plus a few loop cycles; each test stop()s so no disposition outlives it.

async def test_winch_reaches_hook_and_stop_removes_it():
    "SIGWINCH -> on_resize while started; after stop(), the default disposition (ignore) is back."
    tty, comp = await make()
    calls = []
    comp.on_resize = lambda: calls.append(1)
    os.kill(os.getpid(), signal.SIGWINCH)
    await _cycles()
    assert calls == [1]
    comp.stop()
    os.kill(os.getpid(), signal.SIGWINCH)   # ignored now: nothing arrives, nothing raises
    await _cycles()
    assert calls == [1]

async def test_sigint_arrives_as_ctrl_c_key():
    "Out-of-band SIGINT is synthesized as Key('ctrl+c') through normal dispatch: one surface, either transport."
    tty, comp = await make()
    keys = []
    comp.on_key = keys.append
    os.kill(os.getpid(), signal.SIGINT)     # were registration broken, this would raise KeyboardInterrupt here
    await _cycles()
    assert [k.name for k in keys] == ['ctrl+c']
    comp.stop()

async def test_fatal_signals_registered_and_stop_restores():
    "TERM/HUP are taken by start() and given back by stop(); _fatal's 3-line body is idiom, reviewed not spawned."
    tty, comp = await make()
    assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
    assert signal.getsignal(signal.SIGHUP) is not signal.SIG_DFL
    comp.stop()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL
