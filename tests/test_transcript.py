import base64
from teleprint.keys import Key
from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.testing import EmuTty
from teleprint.transcript import TranscriptView

G = (Text('» ', style='green'), Text('  '))

def make(cols=32, rows=8):
    tty = EmuTty(cols, rows)
    comp = Compositor(tty).start()
    comp.set_tail('> ')
    return tty, comp

def test_transcript_browse_toggle_and_leave():
    tty, comp = make()
    bs = [comp.print_block(f'top {i}\nbot {i}', gutter=G) for i in range(6)]
    main_before = tty.term.text()
    tv = TranscriptView(comp, lambda: ([Text('[transcript]')], None))
    comp.on_mouse = tv.on_mouse
    tv.enter()
    scr = tty.term.text()
    assert '[transcript]' in scr and '» top 5' in scr      # bottom-anchored view on the alt screen
    tv.scroll(-100)
    assert '» top 0' in tty.term.text()                    # committed content browsable again
    row = next(i for i, l in enumerate(tty.term.text().splitlines()) if l.endswith('top 1'))
    comp.on_bytes(b'\x1b[<0;2;%dM' % (row + 1))            # click a COMMITTED block: toggleable here
    assert bs[1].collapsed
    assert '… (+1 lines)' in tty.term.text()
    tv.leave()
    assert tty.term.text() == main_before                  # main screen untouched: alt leaves no residue

def test_transcript_composer_cursor_and_resync():
    tty, comp = make()
    b1 = comp.print_block('alpha\nbeta', gutter=G)
    b2 = comp.print_block('gamma\ndelta', gutter=G)
    buf = ['']
    tv = TranscriptView(comp, lambda: ([Text('> ' + buf[0])], (0, 2 + len(buf[0]))))
    tv.enter()
    assert tv.cur == b2.id
    buf[0] = 'typed'
    tv.draw()
    scr = tty.term.text().splitlines()
    assert scr[-1] == '> typed'                            # the composer lives at the bottom of the view
    x, y = tty.term.cursor
    assert (x, scr[y]) == (7, '> typed')                   # ...with the visible cursor in it
    tv.move(-1)
    tv.toggle_current()
    assert b1.collapsed
    tv.leave()
    assert '» alpha … (+1 lines)' in tty.term.text()       # still-live block resynced on the main screen

def K(ch): return Key(ch, ch)

def test_transcript_search_motion_copy():
    tty, comp = make(cols=40, rows=10)
    b0 = comp.print_block('alpha\nhidden needle here', gutter=G)
    b1 = comp.print_block('beta\nsecond needle', gutter=G)
    b2 = comp.print_block('gamma\ndelta', gutter=G, source='SRC = gamma')
    comp.toggle(b0)
    assert b0.collapsed
    tv = TranscriptView(comp, lambda: ([Text('> ')], (0, 2)))
    tv.enter()
    assert tv.cur == b2.id
    assert tv.on_key(K('/'))
    for ch in 'needle': tv.on_key(K(ch))
    assert '/needle' in tty.term.text()          # the search prompt is visible
    tv.on_key(Key('enter'))
    assert tv.cur == b0.id and not b0.collapsed  # wrapped forward to the first match, expanded on landing
    assert 'hidden needle here' in tty.term.text()
    scr = tty.term.text().splitlines()
    row = next(i for i, l in enumerate(scr) if 'hidden needle' in l)
    col = scr[row].index('needle')
    assert tty.term.style(col, row)['inverse']           # the found text is highlighted in place
    assert not tty.term.style(col - 3, row)['inverse']   # surrounding text is not
    tv.on_key(K('n'))
    assert tv.cur == b1.id                       # next match
    tv.on_key(K('N'))
    assert tv.cur == b0.id                       # reverse
    tv.on_key(K('G'))
    assert tv.cur == b2.id
    tv.on_key(K('g'))
    assert tv.cur == b0.id
    tv.on_key(K('/'))
    for ch in 'SRC': tv.on_key(K(ch))
    tv.on_key(Key('enter'))
    assert tv.cur == b2.id                       # matched the stored source, not the rendering
    writes, w = [], tty.write
    tty.write = lambda d: (writes.append(d), w(d))
    tv.on_key(K('y'))
    b64 = base64.b64encode(b'SRC = gamma').decode()
    assert any(isinstance(d, str) and d.startswith('\x1b]52;c;' + b64) for d in writes)

def test_transcript_compose_focus():
    tty, comp = make()
    comp.print_block('alpha\nbeta', gutter=G)
    tv = TranscriptView(comp, lambda: ([Text('> ')], (0, 2)))
    tv.enter()
    assert not tv.on_key(K('x'))                 # unbound printable: host inserts it (transparent composing)
    assert tv.composing
    assert not tv.on_key(K('n'))                 # ops type while composing
    assert tv.on_key(Key('escape'))              # esc returns to browsing...
    assert not tv.composing
    assert not tv.on_key(Key('escape'))          # ...and esc while browsing is the host's (leave)
    assert tv.on_key(K('i'))                     # explicit compose entry
    assert tv.composing

def test_follow_mode_and_paused_frames():
    "Enter follows the tail; blocks printed during the view stream into it (main-screen frames stay model-only); navigation unpins, G re-pins; leave paints the backlog once."
    tty, comp = make()
    comp.print_block('first', gutter=G)
    tv = TranscriptView(comp, lambda: ([Text('[t]')], None))
    tv.enter()
    assert tv.follow
    alt_before = tty.term.text()
    b = comp.print_block('streamed in', gutter=G)
    tv.notify(); tv.draw()
    scr = tty.term.text()
    assert 'streamed in' in scr and tv.cur == b.id     # the view tracked the tail...
    assert '> ' not in scr.splitlines()[0]             # ...and no main-screen frame bled onto the alt screen
    tv.move(-1)
    assert not tv.follow                               # navigation unpins
    comp.print_block('while unpinned', gutter=G)
    tv.notify(); tv.draw()
    assert 'while unpinned' not in tty.term.text()     # unpinned: the view holds still
    tv.jump(True)
    assert tv.follow and 'while unpinned' in tty.term.text()   # G re-pins to the tail
    tv.leave()
    scr = tty.term.text()
    assert 'streamed in' in scr and 'while unpinned' in scr    # the catch-up frame painted the backlog
    assert comp._ws >= 0 and not comp.paused
