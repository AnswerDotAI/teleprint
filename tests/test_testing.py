from teleprint.testing import FakeTty

def test_write_updates_screen():
    with FakeTty(20, 10) as tty:
        tty.write('hello\r\nworld')
        assert tty.term.text() == 'hello\nworld'
        assert tty.size == (20, 10)

def test_cpr_roundtrip():
    with FakeTty(20, 10) as tty:
        tty.write('hello\r\nworld')
        tty.write('\x1b[6n')
        assert tty.read() == b'\x1b[2;6R'  # CPR is 1-indexed row;col
        assert tty.read() == b''

def test_inject():
    with FakeTty() as tty:
        tty.inject('abc')
        tty.inject(b'\x1b[A')
        assert tty.read() == b'abc\x1b[A'
