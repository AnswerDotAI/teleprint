from teleprint.buffer import Buffer
from teleprint.keys import Key

def press(buf, *names):
    for name in names: buf.handle(Key(name, name if len(name) == 1 else None))

def test_word_edit_cut_and_yank():
    b = Buffer('foo bar_baz qux')
    press(b, 'alt+b')
    assert b.cursor == 12
    press(b, 'alt+b')
    assert b.cursor == 8  # underscore is a word boundary
    press(b, 'ctrl+w')
    assert b.text == 'foo baz qux' and b.cut == 'bar_' and b.cursor == 4
    press(b, 'ctrl+y', 'alt+f')
    assert b.text == 'foo bar_baz qux' and b.cursor == 11
    press(b, 'ctrl+k')
    assert b.text == 'foo bar_baz' and b.cut == ' qux'
    press(b, 'ctrl+u', 'backspace', 'ctrl+y')
    assert b.text == 'foo bar_baz' and b.cursor == 11

def test_multiline_navigation_hands_history_back_to_host():
    b = Buffer('abc\ndefgh\nxy')
    b.cursor = 8
    assert b.handle(Key('up')) and b.cursor == 3
    assert not b.handle(Key('up'))
    b.cursor = 8
    assert b.handle(Key('down')) and b.cursor == 12
    assert not b.handle(Key('down'))
