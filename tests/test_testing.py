from teleprint.testing import EmuTty

def test_write_updates_screen():
    with EmuTty(20, 10) as tty:
        tty.write('hello\r\nworld')
        assert tty.term.text() == 'hello\nworld'
        assert tty.size == (20, 10)

def test_cpr_roundtrip():
    with EmuTty(20, 10) as tty:
        tty.write('hello\r\nworld')
        tty.write('\x1b[6n')
        assert tty.read() == b'\x1b[2;6R'  # CPR is 1-indexed row;col
        assert tty.read() == b''

def test_seed():
    with EmuTty() as tty:
        tty.seed('abc')
        tty.seed(b'\x1b[A')
        assert tty.read() == b'abc\x1b[A'
