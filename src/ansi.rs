use anstyle::{Color, Effects as E, Style};
use vte::ansi::{Attr, Handler, Processor};

#[derive(Default)]
struct Text { style: Style, runs: Vec<(Style, String)> }

fn color(c: vte::ansi::Color) -> Option<Color> {
    match c {
        vte::ansi::Color::Spec(c) => Some(anstyle::RgbColor(c.r, c.g, c.b).into()),
        vte::ansi::Color::Indexed(c) => Some(anstyle::Ansi256Color(c).into()),
        vte::ansi::Color::Named(c) if (c as u16) < 16 => Some(anstyle::Ansi256Color(c as u8).into()),
        _ => None,
    }
}

impl Handler for Text {
    fn input(&mut self, c: char) {
        if self.runs.last().is_none_or(|(style, _)| *style != self.style) { self.runs.push((self.style, String::new())); }
        self.runs.last_mut().unwrap().1.push(c);
    }
    fn linefeed(&mut self) { self.input('\n'); }
    fn carriage_return(&mut self) { self.input('\r'); }
    fn put_tab(&mut self, count: u16) { for _ in 0..count { self.input('\t'); } }

    fn terminal_attribute(&mut self, attr: Attr) {
        let s = self.style;
        let e = s.get_effects();
        let underlines = E::UNDERLINE | E::DOUBLE_UNDERLINE | E::CURLY_UNDERLINE | E::DOTTED_UNDERLINE | E::DASHED_UNDERLINE;
        self.style = match attr {
            Attr::Reset => Style::new(),
            Attr::Bold => s.bold(),
            Attr::Dim => s.dimmed(),
            Attr::Italic => s.italic(),
            Attr::Underline => s.effects(e.remove(underlines) | E::UNDERLINE),
            Attr::DoubleUnderline => s.effects(e.remove(underlines) | E::DOUBLE_UNDERLINE),
            Attr::Undercurl => s.effects(e.remove(underlines) | E::CURLY_UNDERLINE),
            Attr::DottedUnderline => s.effects(e.remove(underlines) | E::DOTTED_UNDERLINE),
            Attr::DashedUnderline => s.effects(e.remove(underlines) | E::DASHED_UNDERLINE),
            Attr::BlinkSlow | Attr::BlinkFast => s.blink(),
            Attr::Reverse => s.invert(),
            Attr::Hidden => s.hidden(),
            Attr::Strike => s.strikethrough(),
            Attr::CancelBold => s.effects(e.remove(E::BOLD)),
            Attr::CancelBoldDim => s.effects(e.remove(E::BOLD | E::DIMMED)),
            Attr::CancelItalic => s.effects(e.remove(E::ITALIC)),
            Attr::CancelUnderline => s.effects(e.remove(underlines)),
            Attr::CancelBlink => s.effects(e.remove(E::BLINK)),
            Attr::CancelReverse => s.effects(e.remove(E::INVERT)),
            Attr::CancelHidden => s.effects(e.remove(E::HIDDEN)),
            Attr::CancelStrike => s.effects(e.remove(E::STRIKETHROUGH)),
            Attr::Foreground(c) => s.fg_color(color(c)),
            Attr::Background(c) => s.bg_color(color(c)),
            Attr::UnderlineColor(c) => s.underline_color(c.and_then(color)),
        };
    }
}

/// Split terminal text into styled runs without retaining terminal commands.
pub fn runs(text: &str) -> Vec<(Style, String)> {
    let mut result = Text::default();
    let mut parser: Processor = Processor::new();
    parser.advance(&mut result, text.as_bytes());
    result.runs
}
