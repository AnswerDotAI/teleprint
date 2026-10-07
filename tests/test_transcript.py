import base64
from rich.text import Text
from teleprint.keys import Key
from teleprint.buffer import Buffer
from teleprint.transcript import TranscriptView

G = (Text('» ', style='green'), Text('  '))

def search(tv, text):
    for ch in '/' + text: tv.on_key(Key(ch, ch))
    tv.on_key(Key('enter'))

async def test_browse_search_copy_and_return(terminal):
    tty, comp = await terminal(40, 10)
    comp.set_tail('> ')
    first = comp.put('first', 'alpha\nhidden needle here', gutter=G)
    comp.toggle('first')
    comp.put('second', 'beta\nsecond needle', gutter=G)
    for i in range(6): comp.put(f'b{i}', f'top {i}\nbot {i}', gutter=G, source=f'SRC = {i}')
    before = tty.term.text()
    tv = TranscriptView(comp, lambda: ([Text('[transcript]')], None))
    comp.on_mouse = tv.on_mouse
    tv.enter()
    assert '[transcript]' in tty.term.text() and 'top 5' in tty.term.text()
    tv.scroll(-100)
    row = next(i for i, line in enumerate(tty.term.text().splitlines()) if 'top 0' in line)
    comp.on_bytes(b'\x1b[<0;2;%dM' % (row + 1))
    assert comp.blocks['b0'].collapsed and 'top 0 … (+1 lines)' in tty.term.text()
    search(tv, 'needle')
    assert tv.cur == 'first' and not first.collapsed
    lines = tty.term.text().splitlines()
    row = next(i for i, line in enumerate(lines) if 'hidden needle' in line)
    col = lines[row].index('needle')
    assert tty.term.style(col, row)['inverse'] and not tty.term.style(col - 3, row)['inverse']
    tv.on_key(Key('n', 'n'))
    assert tv.cur == 'second'
    tv.on_key(Key('N', 'N'))
    assert tv.cur == 'first'
    search(tv, 'SRC = 5')  # source, not the rendered body
    assert tv.cur == 'b5'
    writes, write = [], tty.write
    tty.write = lambda data: (writes.append(data), write(data))
    tv.on_key(Key('y', 'y'))
    assert '\x1b]52;c;' + base64.b64encode(b'SRC = 5').decode() + '\x07' in writes
    comp.on_bytes(b'\x1b[<0;2;10M')  # the view's tail is not a block target
    assert not comp.blocks['b5'].collapsed
    tv.leave()
    assert tty.term.text() == before  # edits above the main window never rewrite its printed record

async def test_follow_browse_compose_and_catch_up(terminal):
    tty, comp = await terminal()
    comp.set_tail('> ')
    comp.put('first', 'first', gutter=G)
    buf, changes = Buffer(), []
    app_hook = lambda: changes.append(1)
    comp.on_change = app_hook
    tv = TranscriptView(comp, lambda: ([Text('> ' + buf.text)], (0, buf.cell_cursor('> '))))
    tv.enter()
    comp.put('new', 'streamed in', gutter=G)
    assert tv.cur == 'new' and 'streamed in' in tty.term.text() and changes == [1]
    tv.move(-1)
    top, cur = tv.top, tv.cur
    comp.put('later', 'while browsing', gutter=G)
    assert (tv.top, tv.cur) == (top, cur) and not tv.follow
    tv.on_key(Key('G', 'G'))
    assert tv.follow and tv.cur == 'later' and 'while browsing' in tty.term.text()
    for ch in 'xn':
        key = Key(ch, ch)
        assert not tv.on_key(key)
        buf.handle(key)
    tv.draw()
    assert tv.composing and tty.term.text().endswith('> xn')
    x, y = tty.term.cursor
    assert x == 4 and tty.term.text().splitlines()[y] == '> xn'
    assert tv.on_key(Key('escape')) and not tv.composing
    assert not tv.on_key(Key('escape'))
    tv.leave()
    assert comp.on_change is app_hook and changes == [1, 1]
    assert 'streamed in' in tty.term.text() and 'while browsing' in tty.term.text() and not comp.paused
    assert all(tty.term.contents().splitlines().count('» ' + text) == 1 for text in ('streamed in', 'while browsing'))
