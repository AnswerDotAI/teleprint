from teleprint.buffer import Buffer
from teleprint.widgets import CompletionMenu, Tooltip
from rich.text import Text

def test_menu_cycles_into_buffer():
    b = Buffer('import o')
    m = CompletionMenu(b, ['opcode', 'operator', 'os'], start=7)
    assert not m.insert_common()  # common prefix 'o' does not extend the span
    m.cycle(1)
    assert (b.text, b.cursor, m.i) == ('import opcode', 13, 0)
    m.cycle(1)
    assert b.text == 'import operator'
    m.cycle(-1)
    assert b.text == 'import opcode'
    m.cycle(-1)
    assert (b.text, m.i) == ('import os', 2)  # wraps

def test_menu_common_prefix_and_single():
    b = Buffer('import zl')
    m = CompletionMenu(b, ['zlib'], start=7)
    assert m.insert_common()
    assert (b.text, b.cursor) == ('import zlib', 11)
    b2 = Buffer('x = ma')
    m2 = CompletionMenu(b2, ['math', 'matmul'], start=4)
    assert m2.insert_common()  # 'mat' extends 'ma'
    assert b2.text == 'x = mat'

def test_menu_renderable_window():
    b = Buffer('m')
    m = CompletionMenu(b, [f'mod{i}' for i in range(20)], start=0, show=4)
    plain = m.renderable().plain
    assert 'mod0' in plain and 'mod4' not in plain and '(20 matches)' in plain
    m.i = 10
    plain = m.renderable().plain
    assert 'mod10' in plain and 'mod0' not in plain

def test_tooltip_clips():
    t = Tooltip(Text('\n'.join(f'line{i}' for i in range(20))), max_lines=3)
    plain = t.renderable().plain
    assert 'line2' in plain and 'line3' not in plain and '…' in plain
    assert Tooltip('short').renderable().plain == 'short'
