from teleprint.keys import Parser, Key, Paste, Mouse, CPR, Ctl, STR_FLUSHES

def test_terminal_input_stream():
    p = Parser()
    assert p.feed('aé日\r\n\t\x7f\x01\x0f') == [
        Key('a', 'a'), Key('é', 'é'), Key('日', '日'), Key('enter'), Key('enter'), Key('tab'),
        Key('backspace'), Key('ctrl+a'), Key('ctrl+o')]
    assert p.feed(b'\x1b[A\x1b[1;5C\x1b[1;2A\x1b[3~\x1b[5~\x1b[Z\x1bOP\x1bf\x1b\x7f') == [
        Key('up'), Key('ctrl+right'), Key('shift+up'), Key('delete'), Key('pageup'), Key('shift+tab'),
        Key('f1'), Key('alt+f'), Key('alt+backspace')]
    assert p.feed(b'\x1b[<0;3;15M\x1b[<64;5;5M\x1b[<0;3;15m\x1b[12;1R') == [
        Mouse(0, 2, 14, True), Mouse(64, 4, 4, True), Mouse(0, 2, 14, False), CPR(11, 0)]

def test_split_paste_and_terminal_replies():
    p = Parser()
    assert p.feed(b'\x1b[<0;1;') == []
    assert p.feed(b'2M' + '日'.encode()[:1]) == [Mouse(0, 0, 1, True)]
    assert p.feed('日'.encode()[1:] + b'\x1b') == [Key('日', '日')]
    assert p.flush() == []  # a tick must not shatter the pending sequence
    assert p.feed(b'[48;87R\x1b[200~hello\nworld\x1b[20') == [CPR(47, 86)]
    assert p.feed(b'1~x\x1b]11;rgb:1111/') == [Paste('hello\nworld'), Key('x', 'x')]
    for _ in range(STR_FLUSHES - 1): assert p.flush() == []
    assert p.feed(b'2222/3333\x07\x1b_Gi=31;O') == [Ctl('osc', '11;rgb:1111/2222/3333')]
    assert p.feed(b'K\x1b') == []
    assert p.feed(b'\\x\x1bP1$r0m\x1b\\\x1b]0;title\x1b\\') == [
        Ctl('apc', 'Gi=31;OK'), Key('x', 'x'), Ctl('dcs', '1$r0m'), Ctl('osc', '0;title')]

def test_abandoned_escape_recovery():
    p = Parser()
    for data, ticks, expected in (
        (b'\x1b', 2, [Key('escape')]),
        (b'\x1b[', 2, [Key('escape'), Key('[', '[')]),
        (b'\x1b]x', STR_FLUSHES, [Key('escape'), Key(']', ']'), Key('x', 'x')])):
        assert p.feed(data) == []
        for _ in range(ticks - 1): assert p.flush() == []
        assert p.flush() == expected
    assert p.feed(b'ok') == [Key('o', 'o'), Key('k', 'k')]
