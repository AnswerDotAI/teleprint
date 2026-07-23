from rich.text import Text
from teleprint.compositor import Compositor
from teleprint.testing import FakeTty
from teleprint.transcript import TranscriptView

G = (Text('» ', style='green'), Text('  '))

def make(cols=32, rows=8):
    tty = FakeTty(cols, rows)
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
