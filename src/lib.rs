//! Replace keyed blocks on the main screen while earlier rows enter the terminal's own scrollback.
mod ansi;
pub mod kitty;
mod layout;
#[cfg(all(unix, feature = "tty"))]
pub mod tty;
pub use layout::{plain, wrap};
pub use ansi::runs;

use layout::{Row, layout};
use std::{borrow::Cow, collections::HashMap, fmt::Write, mem};

#[derive(Default)]
enum History { #[default] New, Past, Live(usize) }

/// The keyed block or wrapped tail row at a point in the rendered transcript.
pub enum Hit<'a> { Block(&'a str), Tail(usize) }

#[derive(Default)]
struct Block { text: String, source: String, sealed: usize, rows: Vec<Row>, history: History }

impl Block {
    fn seal(&mut self) {
        self.sealed = self.source.len();
        self.rows.clear();
        self.history = History::Live(0);
    }

    fn set(&mut self, source: &str, text: &str, width: usize) -> bool {
        let source = if source.contains('\x1b') { Cow::Owned(plain(source)) } else { Cow::Borrowed(source) };
        if self.text == text && self.source == source { return false; }
        if !source.starts_with(&self.source[..self.sealed]) { self.sealed = source.len(); }
        let display = if self.sealed == 0 { text } else {
            let added = &source[self.sealed..];
            added.strip_prefix('\n').unwrap_or(added)
        };
        let rows = layout::body(display, width);
        if let History::Live(ink) = &mut self.history && *ink > 0 {
            // Keep an unchanged inked prefix. Otherwise keep the old visible suffix height, without replaying that prefix.
            let same = self.rows.iter().zip(&rows).take_while(|(a, b)| a == b).count();
            if *ink > same { *ink = rows.len().saturating_sub(self.rows.len() - *ink).max(same); }
        }
        self.text = text.into();
        self.source = source.into_owned();
        self.rows = rows;
        true
    }
}

fn tail_layout(text: &str, width: usize, cursor: Option<usize>) -> (Vec<Row>, Option<(usize, usize)>) {
    let mut rows = if text.is_empty() && cursor.is_none() { vec![] } else { layout(text, width) };
    let caret = cursor.map(|end| {
        let end = text.floor_char_boundary(end.min(text.len()));
        let before = layout(&text[..end], width);
        let x = before.last().unwrap().cells;
        let y = before.len() - 1 + x / width;
        if y == rows.len() { rows.push(Row::default()); }
        (y, x % width)
    });
    (rows, caret)
}

/// Rows needed for a tail, including the cursor when it wraps past the last column.
pub fn tail_height(text: &str, cols: u16, cursor: Option<usize>) -> usize { tail_layout(text, usize::from(cols.max(1)), cursor).0.len() }

/// A transcript starts at column zero of the caller's current line. Write each returned frame in order.
pub struct Transcript {
    width: usize, height: usize, blocks: Vec<(String, Block)>, tail: String, cursor: Option<usize>, screen: Vec<Row>, live: usize, tail_top: usize, parked: (usize, usize)
}

impl Transcript {
    pub fn new(cols: u16, rows: u16) -> Self {
        Self { width: cols.max(1).into(), height: rows.max(1).into(), blocks: vec![], tail: String::new(), cursor: None, screen: vec![], live: 0, tail_top: 0, parked: (0, 0)
        }
    }

    /// Append or replace a block without changing its place.
    pub fn put(&mut self, key: &str, text: &str) -> String {
        if let Some((_, block)) = self.blocks.iter_mut().find(|(k, _)| k == key) { if !block.set(text, text, self.width) { return String::new(); } } else {
            let mut block = Block::default();
            block.set(text, text, self.width);
            self.blocks.push((key.into(), block));
        }
        self.frame(None)
    }

    /// Project the supplied block order. The live tail never enters scrollback. Its cursor is a UTF-8 byte offset.
    pub fn sync(&mut self, blocks: &[(&str, &str)], tail: &str, cursor: Option<usize>) -> String {
        self.sync_at(self.width as u16, self.height as u16, blocks, tail, cursor)
    }

    /// Apply a resize and the application's new-width block layout in one frame.
    pub fn sync_at(&mut self, cols: u16, rows: u16, blocks: &[(&str, &str)], tail: &str, cursor: Option<usize>) -> String {
        self.sync_projected_at(cols, rows, blocks.iter().map(|&(k,t)| (k,t,t)), tail, cursor)
    }

    /// Supply (key, source, display) when layout can change independently of the block's content.
    pub fn sync_projected_at<'a>(&mut self, cols: u16, rows: u16, blocks: impl IntoIterator<Item=(&'a str, &'a str, &'a str)>, tail: &str, cursor: Option<usize>) -> String {
        let resized = self.set_size(cols, rows);
        let mut old: HashMap<_, _> = mem::take(&mut self.blocks).into_iter().collect();
        self.blocks = blocks.into_iter().map(|(key, source, text)| {
            let mut block = old.remove(key).unwrap_or_default();
            block.set(source, text, self.width);
            (key.to_owned(), block)
        }).collect();
        self.tail = tail.into();
        self.cursor = cursor;
        self.frame(resized)
    }

    /// Reflow the live screen at the terminal's new size, without replaying the old transcript.
    pub fn resize(&mut self, cols: u16, rows: u16) -> String { let resized = self.set_size(cols, rows); if resized.is_none() { return String::new(); } self.frame(resized) }

    fn set_size(&mut self, cols: u16, rows: u16) -> Option<usize> {
        let (w, h) = (usize::from(cols.max(1)), usize::from(rows.max(1)));
        if (w, h) == (self.width, self.height) { return None; }
        // On shrink the terminal has already reflowed any wide rows above the parked cursor.
        let (y, x) = self.parked;
        let above: usize = self.screen.iter().take(y).map(|row| row.cells.div_ceil(w).max(1)).sum();
        self.parked = ((above + x / w).min(h - 1), x % w);
        let physical_rows = |rows: &[Row]| rows.iter().map(|r| r.cells.div_ceil(w).max(1)).sum::<usize>();
        let (body, tail) = self.screen.split_at(self.live);
        let mut live = physical_rows(body).min(h.saturating_sub(physical_rows(tail)));
        let mut extent = physical_rows(&self.screen).min(h);
        if w == self.width {
            self.mark_history(self.blocks.iter().map(|(_, b)| b.rows.len()).sum::<usize>().saturating_sub(live));
        } else {
            // A width-dependent block can put an archived word and a visible word on one new row.
            // Leave its remaining physical rows on the terminal. Redraw from the next whole block.
            for (n, (_, b)) in self.blocks.iter_mut().rev().enumerate() {
                if live == 0 {
                    if n == 0 { b.seal(); } else { b.history = History::Past; }
                    continue;
                }
                let old = physical_rows(&b.rows);
                if old <= live && b.sealed == 0 {
                    live -= old;
                    b.rows = layout::body(&b.text, w);
                    b.history = History::Live(0);
                } else {
                    let keep = live.min(old);
                    live -= keep;
                    extent -= keep;
                    self.parked.0 = self.parked.0.saturating_sub(keep);
                    b.seal();
                }
            }
        }
        self.width = w;
        self.height = h;
        Some(extent)
    }

    /// Erase the live tail, leave the transcript on the terminal and move to a fresh line.
    pub fn finish(&mut self) -> String {
        self.tail.clear();
        self.cursor = None;
        let mut out = self.frame(None);
        if !self.screen.is_empty() { out.push_str("\r\n"); }
        self.reset();
        out
    }

    /// Forget the current screen and blocks. The next frame starts at the caller's cursor.
    pub fn reset(&mut self) { *self = Self::new(self.width as u16, self.height as u16); }

    /// Hit-test a row relative to the terminal cursor after the last frame.
    pub fn hit(&self, dy: isize) -> Option<Hit<'_>> {
        let row = self.parked.0.checked_add_signed(dy)?;
        if row >= self.screen.len() { return None; }
        if row >= self.live { return Some(Hit::Tail(self.tail_top + row - self.live)); }
        let mut from_end = self.live - row;
        for (key, block) in self.blocks.iter().rev() {
            if from_end <= block.rows.len() { return Some(Hit::Block(key)); }
            from_end -= block.rows.len();
        }
        None
    }

    // Track the immutable prefix by block, not by its old absolute row number.
    fn mark_history(&mut self, mut left: usize) {
        let n = self.blocks.len();
        for (i, (_, b)) in self.blocks.iter_mut().enumerate() {
            let rows = left.min(b.rows.len());
            // A draft can cover the entire last block while its next streamed row still belongs in history.
            b.history = if left > 0 && rows == b.rows.len() && i + 1 < n { History::Past } else { History::Live(rows) };
            left -= rows;
        }
    }

    fn history_end(&self) -> usize {
        let (mut pos, mut end) = (0, 0);
        for (_, b) in &self.blocks {
            match b.history {
                History::Past => end = pos + b.rows.len(), History::Live(0) => break, History::Live(rows) => return pos + rows, History::New => ()
            }
            pos += b.rows.len();
        }
        end
    }

    fn frame(&mut self, old_extent: Option<usize>) -> String {
        let (tail, caret) = tail_layout(&self.tail, self.width, self.cursor);
        let top = tail.len().saturating_sub(self.height).min(caret.map_or(usize::MAX, |(y, _)| y));
        self.tail_top = top;
        let tail = &tail[top..(top + self.height).min(tail.len())];
        let count: usize = self.blocks.iter().map(|(_, b)| b.rows.len()).sum();
        let from = self.history_end();
        let start = count.saturating_sub(self.height - tail.len()).max(from);
        let mut next: Vec<Row> = self.blocks.iter().flat_map(|(_, b)| b.rows.iter()).skip(from).cloned().collect();
        next.extend_from_slice(tail);
        let parked = caret.map(|(y, x)| (count - start + y - top, x)).unwrap_or_else(|| (next.len().saturating_sub(start - from + 1), 0));
        let same = if old_extent.is_some() { 0 } else { self.screen.iter().zip(&next).take_while(|(a, b)| a == b).count() };
        let mut out = String::new();
        let (mut y, mut x) = self.parked;
        if old_extent.is_some() || same != next.len() || same != self.screen.len() {
            // Repaint from the first changed row. Printing the overflow before the window scrolls it in once.
            let first = same.min(next.len().saturating_sub(1));
            if first == self.screen.len() && first > 0 {
                move_row(&mut out, y, first - 1);
                out.push_str("\r\n");
            } else {
                move_row(&mut out, y, first);
                out.push('\r');
            }
            // tmux archives the whole screen on ED at its origin. EL replaces rows without adding history.
            let old = old_extent.unwrap_or(self.screen.len());
            let extent = old.max(next.len());
            for i in first..extent {
                if i > first { out.push_str(if i < next.len() { "\r\n" } else { "\x1b[B\r" }); }
                out.push_str("\x1b[K");
                if let Some(row) = next.get(i) { out.push_str(&row.text); }
            }
            y = extent.saturating_sub(start - from + 1);
            x = if next.len() < old { 0 } else { next.last().map_or(0, |r| r.cells) };
        }
        if y != parked.0 || x != parked.1 {
            move_row(&mut out, y, parked.0);
            out.push('\r');
            if parked.1 > 0 { write!(out, "\x1b[{}C", parked.1).unwrap(); }
        }
        self.parked = parked;
        self.mark_history(start);
        self.live = count - start;
        self.screen = next.split_off(start - from);
        out
    }
}

fn move_row(out: &mut String, from: usize, to: usize) {
    if to < from { write!(out, "\x1b[{}A", from - to).unwrap(); }
    if to > from { write!(out, "\x1b[{}B", to - from).unwrap(); }
}
