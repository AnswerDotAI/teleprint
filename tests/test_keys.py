from teleprint.keys import Parser, Key, Paste, Mouse, CPR

def feed1(data):
    "Feed in one call, expect exactly one event."
    evs = Parser().feed(data)
    assert len(evs) == 1, evs
    return evs[0]

def test_printables_and_controls():
    assert feed1('a') == Key('a', 'a')
    assert feed1('é') == Key('é', 'é')
    assert feed1('日') == Key('日', '日')
    assert feed1('\r') == Key('enter')
    assert feed1('\n') == Key('ctrl+j')
    assert feed1('\t') == Key('tab')
    assert feed1('\x7f') == Key('backspace')
    assert feed1('\x01') == Key('ctrl+a')
    assert feed1('\x0f') == Key('ctrl+o')

def test_escape_sequences():
    assert feed1('\x1b[A') == Key('up')
    assert feed1('\x1b[1;5C') == Key('ctrl+right')
    assert feed1('\x1b[1;2A') == Key('shift+up')
    assert feed1('\x1b[3~') == Key('delete')
    assert feed1('\x1b[5~') == Key('pageup')
    assert feed1('\x1b[Z') == Key('shift+tab')
    assert feed1('\x1bOP') == Key('f1')
    assert feed1('\x1bf') == Key('alt+f')
    assert feed1('\x1b\x7f') == Key('alt+backspace')

def test_mouse_and_cpr():
    assert feed1(b'\x1b[<0;3;15M') == Mouse(0, 2, 14, True)
    assert feed1(b'\x1b[<64;5;5M') == Mouse(64, 4, 4, True)
    assert feed1(b'\x1b[<0;3;15m') == Mouse(0, 2, 14, False)
    assert feed1(b'\x1b[12;1R') == CPR(11, 0)

def test_split_sequences():
    p = Parser()
    assert p.feed(b'\x1b[<0;1;') == []
    assert p.feed(b'2M') == [Mouse(0, 0, 1, True)]
    assert p.feed('日'.encode()[:1]) == []
    assert p.feed('日'.encode()[1:]) == [Key('日', '日')]
    assert p._buf == b''

def test_lone_escape_flush():
    p = Parser()
    assert p.feed(b'\x1b') == []
    assert p.flush() == [Key('escape')]
    assert p.feed(b'\x1b[') == []
    assert p.flush() == [Key('escape'), Key('[', '[')]  # ESC resolved; the '[' reparses as a printable

def test_paste():
    p = Parser()
    evs = p.feed(b'\x1b[200~hello\nworld\x1b[201~x')
    assert evs == [Paste('hello\nworld'), Key('x', 'x')]
    p2 = Parser()
    assert p2.feed(b'\x1b[200~abc\x1b[20') == []
    assert p2.feed(b'1~') == [Paste('abc')]

def test_mixed_stream():
    evs = Parser().feed(b'hi\r\x1b[A\x1b[<0;1;1M')
    assert evs == [Key('h','h'), Key('i','i'), Key('enter'), Key('up'), Mouse(0,0,0,True)]

def test_control_strings():
    from teleprint.keys import Ctl
    assert feed1(b'\x1b]11;rgb:fafa/fafa/f4f4\x1b\\') == Ctl('osc', '11;rgb:fafa/fafa/f4f4')
    assert feed1(b'\x1b]0;title\x07') == Ctl('osc', '0;title')  # BEL terminator
    assert feed1(b'\x1b_Gi=31;OK\x1b\\') == Ctl('apc', 'Gi=31;OK')
    assert feed1(b'\x1bP1$r0m\x1b\\') == Ctl('dcs', '1$r0m')
    p = Parser()
    assert p.feed(b'\x1b_Gi=31;O') == []          # split mid-payload
    assert p.feed(b'K\x1b') == []                 # split mid-terminator
    assert p.feed(b'\\x') == [Ctl('apc', 'Gi=31;OK'), Key('x', 'x')]
    assert p._buf == b''
