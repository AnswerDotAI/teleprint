//! Place an image in the transcript with the Kitty graphics protocol's Unicode placeholders.
use std::{env, io::{Cursor, Write}, process::Command as Process, sync::LazyLock};

use base64::{Engine, prelude::BASE64_STANDARD};
use flate2::write::ZlibEncoder;
use image::{ImageFormat, ImageReader};
use kitty_graphics_protocol::{Action, Command, Compression as KittyCompression, ImageFormat::Rgba};

/// Kitty's row numbers are a protocol table of combining marks, not consecutive code points.
static ROWS: LazyLock<Vec<char>> = LazyLock::new(|| {
    include_str!("rowcolumn_diacritics.txt").lines().filter_map(|line| u32::from_str_radix(line.split_once(';')?.0, 16).ok().and_then(char::from_u32)).collect()
});

pub struct Image { pixels: Vec<u8>, size: (u32, u32) }

impl Image {
    /// Read base64 PNG or JPEG and compress RGBA pixels once for direct transfer.
    pub fn decode(data: &str) -> Option<Self> {
        let compact: Vec<_> = data.bytes().filter(|b| !b.is_ascii_whitespace()).collect();
        let bytes = BASE64_STANDARD.decode(compact).ok()?;
        let reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format().ok()?;
        if !matches!(reader.format()?, ImageFormat::Png | ImageFormat::Jpeg) { return None; }
        let pixels = reader.decode().ok()?.into_rgba8();
        let mut zlib = ZlibEncoder::new(Vec::new(), flate2::Compression::fast());
        zlib.write_all(pixels.as_raw()).ok()?;
        Some(Self { pixels: zlib.finish().ok()?, size: pixels.dimensions() })
    }

    pub fn size(&self) -> (u32, u32) { self.size }

    /// Return the trusted transmit command and separate printable grid. Use a distinct nonzero 24-bit ID per placement.
    pub fn render(&self, id: u32, max_cols: u16, tmux: bool) -> (String, String) {
        let id = (id & 0xffffff).max(1);
        let (w, h) = (f64::from(self.size.0), f64::from(self.size.1));
        let scale = (f64::from(max_cols.max(1)) / w).min(ROWS.len() as f64 * 2.0 / h).min(1.0);
        let cols = ((w * scale).ceil() as u16).clamp(1, max_cols.max(1));
        let rows = ((h * scale / 2.0).ceil() as u16).clamp(1, ROWS.len() as u16);
        let command = Command::builder().action(Action::TransmitAndDisplay).format(Rgba).compression(KittyCompression::Zlib)
            .dimensions(self.size.0, self.size.1).quiet(2).image_id(id).unicode_placeholder(cols, rows).display_area(cols.into(), rows.into()).build();
        let mut transmit = String::new();
        for chunk in command.serialize_chunked(&self.pixels).expect("direct transfer of in-memory pixels") {
            if tmux {
                transmit.push_str("\x1bPtmux;");
                transmit.push_str(&chunk.replace('\x1b', "\x1b\x1b"));
                transmit.push_str("\x1b\\");
            } else { transmit.push_str(&chunk); }
        }
        let [_, r, g, b] = id.to_be_bytes();
        let mut grid = format!("\x1b[38;2;{r};{g};{b}m");
        let fill = "\u{10eeee}".repeat(usize::from(cols - 1));
        for (y, &mark) in ROWS.iter().take(usize::from(rows)).enumerate() {
            if y != 0 { grid.push('\n'); }
            grid.push('\u{10eeee}');
            grid.push(mark);
            grid.push_str(&fill);
        }
        grid.push_str("\x1b[39m");
        (transmit, grid)
    }
}

/// Environment evidence of Kitty graphics. No terminal query competes with the user's keyboard input.
pub fn supported() -> bool {
    let known = |name: &str| ["kitty", "ghostty", "wezterm"].iter().any(|term| name.to_ascii_lowercase().contains(term));
    if ["KITTY_WINDOW_ID", "GHOSTTY_RESOURCES_DIR", "WEZTERM_PANE"].iter().any(|key| env::var_os(key).is_some())
        || ["TERM", "TERM_PROGRAM"].iter().any(|key| env::var(key).is_ok_and(|v| known(&v))) { return true; }
    env::var_os("TMUX").is_some() && Process::new("tmux").args(["display-message", "-p", "#{client_termname}"]).output()
            .is_ok_and(|out| out.status.success() && known(&String::from_utf8_lossy(&out.stdout)))
}
