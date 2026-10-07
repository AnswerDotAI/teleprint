from rich.text import Text
from teleprint.buffer import Buffer
from teleprint.keys import Key
from teleprint.widgets import CompletionMenu, Tooltip

def test_completion_journey():
    b = Buffer('import o')
    menu = CompletionMenu(b, ['opcode', 'operator', 'os'], start=7, show=2)
    assert not menu.insert_common()
    for direction, expected in ((1, 'opcode'), (1, 'operator'), (-1, 'opcode'), (-1, 'os')):
        menu.cycle(direction)
        assert b.text == 'import ' + expected and b.cursor == len(b.text)
    plain = menu.renderable().plain
    assert 'os' in plain and 'opcode' not in plain and '(3 matches)' in plain
    b = Buffer('x = ma')
    menu = CompletionMenu(b, ['math', 'matmul'], start=4)
    assert menu.insert_common() and b.text == 'x = mat'
    menu.cycle(1)
    assert b.text == 'x = math'
    b.suggestion = '.sqrt'
    b.cursor = 2
    b.handle(Key('right'))
    assert b.cursor == 3 and b.text == 'x = math'  # not accepted in the middle of the text
    b.cursor = len(b.text)
    b.handle(Key('right'))
    assert b.text == 'x = math.sqrt' and not b.suggestion

def test_tooltip_clips():
    tip = Tooltip(Text('\n'.join(f'line{i}' for i in range(20))), max_lines=3)
    assert tip.renderable().plain == 'line0\nline1\nline2\n…'
