use crate::ansi::runs;
use std::fmt::Write;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// Printable text for redirected output, preserving newlines and tabs but no terminal controls.
pub fn plain(text: &str) -> String {
    let mut out = String::new();
    for (_, run) in runs(text) { out.extend(run.chars().filter(|&c| !c.is_control() || matches!(c, '\n' | '\t'))); }
    out
}

#[derive(Clone, Default, PartialEq)]
pub(crate) struct Row { pub text: String, pub cells: usize }

/// Split styled text at terminal-cell boundaries. Each row can be painted independently.
pub(crate) fn layout(text: &str, width: usize) -> Vec<Row> {
    let mut rows = vec![Row::default()];
    for (style, run) in runs(text) {
        let mut styled = false;
        for g in run.graphemes(true) {
            let newline = matches!(g, "\n" | "\r\n");
            if !newline && g != "\t" && g.chars().any(char::is_control) { continue; }
            let row = rows.last_mut().unwrap();
            let n = if g == "\t" { 8 - row.cells % 8 } else { g.width() };
            let (glyph, cells) = if g == "\t" { (" ", 1) } else if n > width { ("�", 1) } else { (g, n) };
            for _ in 0..if g == "\t" { n } else { 1 } {
                let row = rows.last_mut().unwrap();
                if newline || row.cells + cells > width {
                    if styled {
                        write!(row.text, "{style:#}").unwrap();
                        styled = false;
                    }
                    rows.push(Row::default());
                }
                if newline { break; }
                let row = rows.last_mut().unwrap();
                if !styled && !style.is_plain() {
                    write!(row.text, "{style}").unwrap();
                    styled = true;
                }
                row.text.push_str(glyph);
                row.cells += cells;
            }
        }
        if styled { write!(rows.last_mut().unwrap().text, "{style:#}").unwrap(); }
    }
    rows
}

pub(crate) fn body(text: &str, width: usize) -> Vec<Row> {
    let mut rows = layout(text, width);
    if rows.last().is_some_and(|r| r.text.is_empty()) { rows.pop(); }
    rows
}

/// Terminal rows for a block: hard-wrapped graphemes, tabs expanded and SGR contained within each row.
pub fn wrap(text: &str, cols: u16) -> Vec<String> { body(text, usize::from(cols.max(1))).into_iter().map(|r| r.text).collect() }
