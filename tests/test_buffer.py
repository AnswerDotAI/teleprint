from teleprint.buffer import Buffer
from teleprint.keys import Key

def press(b, *names):
    for n in names: b.handle(Key(n, n if len(n) == 1 else None))
    return b

def test_insert_and_move():
    b = Buffer()
    press(b, *'hello')
    assert (b.text, b.cursor) == ('hello', 5)
    press(b, 'ctrl+a'); assert b.cursor == 0
    press(b, 'ctrl+e'); assert b.cursor == 5
    press(b, 'left', 'left', 'ctrl+b'); assert b.cursor == 2
    press(b, 'ctrl+f'); assert b.cursor == 3

def test_delete_and_cut():
    b = Buffer('hello world')
    press(b, 'ctrl+w')
    assert b.text == 'hello ' and b.cut == 'world'
    press(b, 'ctrl+y')
    assert b.text == 'hello world'
    press(b, 'ctrl+u')
    assert b.text == '' and b.cut == 'hello world'
    b2 = Buffer('abc'); b2.cursor = 1
    press(b2, 'ctrl+k'); assert b2.text == 'a'
    press(b2, 'backspace'); assert b2.text == ''
    press(b2, 'backspace'); assert b2.text == ''  # no-op at start

def test_word_moves():
    b = Buffer('foo bar_baz qux')
    press(b, 'alt+b'); assert b.cursor == 12
    press(b, 'alt+b'); assert b.cursor == 8  # bar_baz: underscore is not alnum
    b.cursor = 0
    press(b, 'alt+f'); assert b.cursor == 3

def test_unhandled():
    b = Buffer('x')
    assert not b.handle(Key('enter'))
    assert not b.handle(Key('ctrl+c'))
    assert b.text == 'x'

def test_cell_cursor_wide():
    b = Buffer('日本')
    assert b.cell_cursor('> ') == 6  # 2 prompt cells + two double-width chars
    b.cursor = 1
    assert b.cell_cursor('> ') == 4

def test_multiline_up_down():
    b = Buffer('abc\ndefgh\nxy')
    b.cursor = 8  # in 'defgh', col 4
    assert b.handle(Key('up')) and b.cursor == 3   # clamped to end of 'abc'
    assert not b.handle(Key('up'))                 # top line: host takes over (history)
    b.cursor = 8
    assert b.handle(Key('down')) and b.cursor == 12  # col clamped to end of 'xy'
    assert not b.handle(Key('down'))

def test_suggestion_accept():
    b = Buffer('import o')
    b.suggestion = 's'
    assert b.handle(Key('right')) and b.text == 'import os' and not b.suggestion
    b.suggestion = 'x'
    b.cursor = 2
    assert b.handle(Key('right')) and b.cursor == 3 and b.text == 'import os'  # mid-text: plain move
