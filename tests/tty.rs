#![cfg(all(unix, feature = "tty"))]

use ptymini::PtyCore;
use std::{env, io, process::{Command, Stdio}, time::Duration};
use teleprint::tty::{Event, KeyCode, RawEvent, Tty};

fn stty() -> io::Result<Vec<u8>> {
    let out = Command::new("stty").arg("-g").stdin(Stdio::inherit()).output()?;
    assert!(out.status.success());
    Ok(out.stdout)
}

async fn use_terminal() -> io::Result<()> {
    let original = stty()?;
    let mut tty = Tty::open()?;
    assert_ne!(stty()?, original);
    assert_eq!(tty.cooked(stty)?, original);

    tty.write("resize the foreground terminal")?;
    let mut byte = [0];
    assert!(matches!(tty.events.raw(&mut byte).await?, RawEvent::Resize(49, 13)));
    tty.write("send input")?;
    assert!(matches!(tty.events.raw(&mut byte).await?, RawEvent::Input(1)));
    assert_eq!(byte, [3]); // Ctrl-C is a byte for the borrower, not a signal.
    let event = tty.events.next().await.unwrap()?;
    assert!(matches!(event, Event::Key(k) if k.code == KeyCode::Up), "{event:?}");
    drop(tty);
    assert_eq!(stty()?, original);
    Ok(())
}

fn wait_for(pty: &PtyCore, marker: &str) {
    loop {
        let (bytes, end, _) = pty.read_from(0, None);
        let output = String::from_utf8_lossy(&bytes);
        if output.contains(marker) { return; }
        assert!(pty.alive() && pty.wait_change(Some(end), Some(5.0)), "waiting for {marker}: {output}");
    }
}

#[tokio::test]
async fn terminal_handoff() -> io::Result<()> {
    if env::var_os("TELEPRINT_TEST_PTY").is_some() { return tokio::time::timeout(Duration::from_secs(5), use_terminal()).await?; }
    let args = ["env".into(), "TELEPRINT_TEST_PTY=1".into(), env::current_exe()?.to_str().unwrap().into(),
                "--exact".into(), "terminal_handoff".into(), "--nocapture".into()];
    let pty = PtyCore::spawn(&args, None, None, 20, 80, 4096)?;
    wait_for(&pty, "resize the foreground terminal");
    pty.resize(13, 49)?;
    wait_for(&pty, "send input");
    pty.write(b"\x03\x1b[A")?;
    assert_eq!(pty.wait(Some(6.0)), Some(0), "{}", String::from_utf8_lossy(&pty.read_from(0, None).0));
    Ok(())
}
