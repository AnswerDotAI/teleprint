//! A transcript's connection to a real terminal. The client decides what each event means.
use std::{ffi::{CStr, OsStr}, fs::{File, OpenOptions}, io::{self, Read, Write}, os::unix::{ffi::OsStrExt, fs::OpenOptionsExt}, time::Duration};
use tokio::{io::unix::AsyncFd, signal::unix::{Signal, SignalKind, signal}};

pub use crossterm::{cursor::position as cursor_position, event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind}};
use crossterm::{
    cursor::{MoveTo, Show}, event::{self, DisableBracketedPaste, EnableBracketedPaste}, execute,
    terminal::{self, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen}
};

pub enum RawEvent { Input(usize), Resize(u16, u16) }

/// Both decoders wait on the same tty. A cancelled wait leaves no reader behind
/// to steal input from a foreground program.
pub struct Events { fd: AsyncFd<File>, resize: Signal }

impl Events {
    fn open() -> io::Result<Self> {
        // macOS kqueue needs the actual PTY, not the /dev/tty multiplexer.
        // Reopen it so O_NONBLOCK doesn't change the caller's stdin.
        let mut path = [0; libc::PATH_MAX as usize];
        let err = unsafe { libc::ttyname_r(libc::STDIN_FILENO, path.as_mut_ptr(), path.len()) };
        if err != 0 { return Err(io::Error::from_raw_os_error(err)); }
        let path = OsStr::from_bytes(unsafe { CStr::from_ptr(path.as_ptr()) }.to_bytes());
        let file = OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK).open(path)?;
        // The registered File owns this fd until the AsyncFd is dropped.
        let fd = unsafe { AsyncFd::register_with_interest(file, tokio::io::Interest::READABLE)? };
        Ok(Self { fd, resize: signal(SignalKind::window_change())? })
    }

    async fn event(&mut self) -> io::Result<Event> {
        loop {
            // One read can leave several decoded keys in crossterm's buffer.
            if event::poll(Duration::ZERO)? { return event::read(); }
            tokio::select! {
                ready = self.fd.readable() => ready?.clear_ready(),
                _ = self.resize.recv() => { let (w, h) = terminal::size()?; return Ok(Event::Resize(w, h)); }
            }
        }
    }

    pub async fn next(&mut self) -> Option<io::Result<Event>> { Some(self.event().await) }

    pub async fn raw(&mut self, buf: &mut [u8]) -> io::Result<RawEvent> {
        loop {
            tokio::select! {
                ready = self.fd.readable() => {
                    if let Ok(n) = ready?.try_io(|fd| (&*fd.get_ref()).read(buf)) { return n.map(RawEvent::Input); }
                }
                _ = self.resize.recv() => { let (w, h) = terminal::size()?; return Ok(RawEvent::Resize(w, h)); }
            }
        }
    }
}

pub struct Tty { pub events: Events, out: io::Stdout, alternate: bool, mouse: bool }

impl Tty {
    pub fn open() -> io::Result<Self> {
        let events = Events::open()?;
        terminal::enable_raw_mode()?;
        let mut tty = Self { events, out: io::stdout(), alternate: false, mouse: false };
        execute!(tty.out, EnableBracketedPaste)?;
        Ok(tty)
    }

    pub fn size(&self) -> io::Result<(u16, u16)> { terminal::size() }

    /// Request SGR clicks and wheel events without mouse-motion traffic.
    pub fn mouse(&mut self, on: bool) -> io::Result<()> {
        self.write(if on { "\x1b[?1000h\x1b[?1006h" } else { "\x1b[?1000l\x1b[?1006l" })?;
        self.mouse = on;
        Ok(())
    }

    pub fn write(&mut self, frame: &str) -> io::Result<()> {
        let mut out = self.out.lock();
        out.write_all(frame.as_bytes())?;
        out.flush()
    }

    pub fn cooked<T>(&mut self, f: impl FnOnce() -> io::Result<T>) -> io::Result<T> {
        let mouse = self.mouse;
        if mouse { self.mouse(false)?; }
        execute!(self.out, DisableBracketedPaste, Show)?;
        terminal::disable_raw_mode()?;
        let result = f();
        terminal::enable_raw_mode()?;
        execute!(self.out, EnableBracketedPaste)?;
        if mouse { self.mouse(true)?; }
        result
    }

    pub fn alternate(&mut self, on: bool) -> io::Result<()> {
        if on != self.alternate {
            if on { execute!(self.out, EnterAlternateScreen, Clear(ClearType::All), MoveTo(0, 0))?; }
            else { execute!(self.out, LeaveAlternateScreen)?; }
            self.alternate = on;
        }
        Ok(())
    }
}

impl Drop for Tty {
    fn drop(&mut self) {
        let _ = self.alternate(false);
        if self.mouse { let _ = self.mouse(false); }
        let _ = execute!(self.out, DisableBracketedPaste, Show);
        let _ = terminal::disable_raw_mode();
    }
}
