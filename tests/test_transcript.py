from pyghostty import Terminal
from teleprint import Transcript


def test_stream_resize_and_finish():
    term, view = Terminal(18, 5), Transcript(18, 5)
    term.feed('shell\r\n')
    lines = [f'part {i}' for i in range(10)]
    lines[4] = 'thislinedoesnotfit'
    for i in range(len(lines)):
        if i == 5:
            term.resize(12, 4)
            lines[5] = 'saved\ntable row'
        blocks = [('input', 'request'), ('reply', '\n'.join(lines[:i+1]))]
        if i == 5: term.feed(view.sync_at(12, 4, blocks, '> draft', 3))
        else: term.feed(view.sync(blocks, '> draft', 3))
    assert term.text().splitlines() == ['part 7', 'part 8', 'part 9', '> draft']
    term.feed(view.put('reply', '\n'.join(lines[:-1] + ['saved reply'])))
    term.feed(view.finish())
    assert ''.join(term.contents().splitlines()) == ''.join(['shell', 'request', *lines[:-1], 'saved reply']).replace('\n', '')

    term, view = Terminal(8, 3), Transcript(8, 3)
    chunks = ['one', 'two', 'three', 'four']
    source = ' '.join(chunks)
    def paragraph(extra, gutter): return [('reply', source+extra, f'{gutter}\x1b[2m{source+extra}\x1b[0m')]
    term.feed(view.sync_projected_at(8, 3, [('reply', source, '\n'.join(chunks))], '> ', 2))
    term.resize(24, 5)
    term.feed(view.sync_projected_at(24, 5, paragraph('', '‹1‹ '), '> ', 2))
    term.feed(view.sync_projected_at(24, 5, paragraph(' and five', '‹3‹ '), '> ', 2))
    term.feed(view.sync_projected_at(24, 5, paragraph(' and five', '‹3‹ '), 'a\nb\nc\nd\ne', 8))
    term.resize(18, 5)
    term.feed(view.sync_projected_at(18, 5, paragraph(' and five\nsix', '‹0‹ '), '> ', 2))
    term.feed(view.finish())
    assert term.contents().splitlines() == [*chunks, ' and five', 'six']


def test_edit_draft_and_order():
    term, view = Terminal(8, 4), Transcript(8, 4)
    blocks = [('a', 'first'), ('b', '\x1b[1msecond\x1b[0m\x1b[2J\x1b]52;c;secret\x07')]
    term.feed(view.sync(blocks, '> 界界😀', len('> 界界😀'.encode())))
    assert term.text().splitlines() == ['first', 'second', '> 界界😀']
    assert term.style(0, 1)['bold'] and term.cursor == (0, 3)
    term.feed(view.sync([blocks[1], ('c', 'third')], 'one\ntwo\nthree\nfour\nfive', 5))
    assert term.text().splitlines() == ['two', 'three', 'four', 'five'] and term.cursor == (1, 0)
    term.feed(view.finish())
    assert term.contents().splitlines() == ['second', 'third']

    term, view = Terminal(20, 3), Transcript(20, 3)
    blocks = [(key, key) for key in 'ABCDE']
    term.feed(view.sync(blocks, '> ', 2))
    blocks[0] = ('A', 'A1\nA2\nA3')
    term.feed(view.sync(blocks, '> ', 2))
    assert term.contents().splitlines() == ['A', 'B', 'C', 'D', 'E', '>']


def test_kitty_placement_in_scrollback():
    from pyghostty import lib
    from teleprint import Image
    import re
    dot = Image.decode("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAE0lEQVR4nGPkkfvPAANMcBZeDgA6EwExjmZangAAAABJRU5ErkJggg==")
    transmit, grid = dot.render(0x123456, 8)
    term, view = Terminal(8, 3), Transcript(8, 3)
    term.feed(transmit)
    term.feed(view.put('plot', grid))
    assert lib.ghostty_kitty_graphics_image(term.get('kitty_graphics', 'GhosttyKittyGraphics'), 0x123456)
    assert term.style(0, 0)['fg'] == ('rgb', (0x12, 0x34, 0x56))
    placeholder = '\U0010eeee'
    def grid_widths(): return [row.count(placeholder) for row in term.text().splitlines() if placeholder in row]
    assert grid_widths() == [4, 4]

    # Unquote one tmux passthrough packet; Ghostty still has to accept the enclosed image.
    packet, grid = dot.render(0x654321, 2, tmux=True)
    payload = re.fullmatch(r'\x1bPtmux;((?:[^\x1b]|\x1b\x1b)+)\x1b\\', packet)
    assert payload
    term.feed(payload[1].replace('\x1b\x1b', '\x1b'))
    term.feed(view.put('plot', grid))
    assert lib.ghostty_kitty_graphics_image(term.get('kitty_graphics', 'GhosttyKittyGraphics'), 0x654321)
    assert grid_widths() == [2]
    term.feed(view.put('notes', 'one\ntwo\nthree'))
    assert placeholder in term.contents() and placeholder not in term.text()


def test_selective_sgr_resets_and_wrapping():
    label, rest = '[Alt-C code]', '  Alt-P prompt plain'
    text = f'\x1b[31;1;4m{label}\x1b[24m  Alt-P prompt\x1b[22m plain\x1b[39m\n»»» draft'
    for width in (70, 8):
        term, view = Terminal(width, 10), Transcript(width, 10)
        term.feed(view.sync([], text, len(text.encode())))
        for i in range(len(label + rest)):
            style = term.style(i % width, i // width)
            assert bool(style['underline']) == (i < len(label))
            assert style['bold'] == (i < (label + rest).index(' plain'))
            assert style['fg'] == ('palette', 1)
        y = (len(label + rest) - 1) // width + 1
        style = term.style(0, y)
        assert not style['underline'] and not style['bold'] and style['fg'] is None
        term.feed(view.sync([], '> plain', len('> plain')))
        assert not term.style(0, 0)['underline']
        term.close()
