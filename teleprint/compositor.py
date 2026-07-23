"The compositor: prints blocks through to scrollback, repaints the visible zone in place, owns the tail."
import time
from rich.console import Console
from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from .blocks import Block
from .keys import Parser, Key, Paste, Mouse, CPR, Ctl

class Compositor:
    """Owns the terminal: an append-mostly document of blocks printed through to
    scrollback, a repaintable visible zone, and the tail below the blocks.

    Geometry anchors to `_park`, the terminal row (0-indexed) of the last painted
    line -- or of the next line, when nothing is painted yet. Tracked line `j` is
    on row `_park - (total-1-j)`, so everything is relative to the bottom and no
    scroll counter exists. Lines that scroll off the top leave the map and their
    blocks commit: archival-restyled in place first when still whole (commit
    discipline: whole blocks, never torn fragments), silently when already torn
    by a taller-than-screen stream (progressive commit)."""
    def __init__(self, tty):
        self.tty = tty
        self._adopt_size()
        self.blocks = {}
        self._lines = []   # tracked visible lines: (block id, segments); tail lines use id None
        self._ntail = 0
        self._park = 0
        self._next_id = 1
        self._coff = 0   # rows the visible cursor is parked above `_park`
        self._ccol = 0   # ...and its column there
        self._tail_cursor = None  # (tail line index, cell col) where the cursor should rest
        self._parser = Parser()
        self.on_key = None    # callable(Key)
        self.on_paste = None  # callable(str)
        self.on_ctl = None    # callable(Ctl): OSC/APC/DCS replies and payloads
        self.on_wheel = None  # callable(direction: -1 up, 1 down), e.g. tmux copy-mode delegation
        self.on_mouse = None  # callable(Mouse) -> bool handled: lets a mode take the mouse over click/wheel defaults

    def _adopt_size(self):
        self.cols, self.rows = self.tty.size
        self._consoles = {}
        self.console = self._console(self.cols)

    def _console(self, width):
        if width not in self._consoles:
            self._consoles[width] = Console(width=width, force_terminal=True, color_system='truecolor',
                                            markup=False, highlight=False)
        return self._consoles[width]

    def resize(self):
        """Adopt a new terminal size: width rewrap killed the line map, so demote every block.
        The old tail is chrome, not transcript: the next paint overwrites it in place rather
        than duplicating it into history. The cursor stays with its line through a rewrap, so
        its post-resize CPR row locates the old tail exactly -- except when a tail line itself
        rewrapped to a different height (rare: tails are short), which can leave a fragment."""
        ntail, coff = self._ntail, self._coff
        self._adopt_size()
        for b in self.blocks.values(): b.committed = True
        self._lines = []
        self._ntail = 0
        self._coff = self._ccol = 0
        row, col = self.sync()
        if ntail:
            k = min(max(ntail - 1 - coff, 0), row)
            if k or col: self.tty.write((f'\x1b[{k}A' if k else '') + '\r')
            self._park = row - k
        elif col:
            self.tty.write('\r\n')
            self._park = min(row + 1, self.rows - 1)

    # -- input side -----------------------------------------------------------
    def sync(self, timeout=2.0):
        "CPR round-trip: ask the terminal where the cursor is and adopt its answer, waiting up to `timeout` (pt's CPR discipline: replies through a zoom-redrawing tmux take real time)."
        self.tty.write('\x1b[6n')
        deadline = time.monotonic() + timeout
        while True:
            for ev in self._parser.feed(self.tty.read()):
                if isinstance(ev, CPR):
                    self._park = ev.row + self._coff  # the cursor may be parked above the last painted line
                    return self._park, ev.col
                self._dispatch(ev)  # keys typed while we waited are not lost
            if time.monotonic() >= deadline: raise RuntimeError(f'no CPR reply within {timeout}s')

    def start(self):
        "Adopt the tty: learn the origin via CPR and normalize to column 0."
        self._home()
        return self

    def _home(self):
        row, col = self.sync()
        if col:
            self.tty.write('\r\n')
            self._park = min(row+1, self.rows-1)

    def on_bytes(self, data):
        "Parse terminal input and dispatch it: clicks and wheel handled here, keys and pastes go to the `on_key`/`on_paste` hooks."
        for ev in self._parser.feed(data): self._dispatch(ev)

    def flush_input(self):
        "Resolve a pending lone ESC as the escape key (call after the event loop's read timeout)."
        for ev in self._parser.flush(): self._dispatch(ev)

    def _dispatch(self, ev):
        if isinstance(ev, Mouse):
            if self.on_mouse and self.on_mouse(ev): return  # a mode (e.g. transcript view) owns the mouse
            if ev.press and ev.btn == 0: self.click(ev.x, ev.y)
            elif ev.press and ev.btn in (64, 65) and self.on_wheel: self.on_wheel(-1 if ev.btn == 64 else 1)
        elif isinstance(ev, CPR): self._park = ev.row + self._coff
        elif isinstance(ev, Ctl):
            if self.on_ctl: self.on_ctl(ev)
        elif isinstance(ev, Key):
            if self.on_key: self.on_key(ev)
        elif isinstance(ev, Paste):
            if self.on_paste: self.on_paste(ev.text)

    def click(self, x, y):
        "A click at cell (x, y): line-granular, dispatching the header's Style.meta action."
        total = len(self._lines)
        j = y - self._park + total - 1
        if not 0 <= j < total: return
        bid, segs = self._lines[j]
        if bid is None or self.blocks[bid].committed: return  # tail rows and history are not click targets
        for s in segs:
            meta = s.style.meta if s.style else {}
            if 'toggle' in meta: return self.toggle(self.blocks[meta['toggle']])

    # -- rendering ------------------------------------------------------------
    def _render(self, renderable): return self.console.render_lines(renderable, pad=False)

    def _ansi(self, segs):
        return ''.join(s.style.render(s.text) if s.style else s.text for s in segs)

    def _gutter_width(self, blk):
        f, c = blk.gutter
        return max(cell_len(f.plain if isinstance(f, Text) else str(f)),
                   cell_len(c.plain if isinstance(c, Text) else str(c)))

    def _content_lines(self, blk):
        """Rendered segment-lines of the whole body (all parts concatenated), caching the first line.
        Content renders at cols minus the gutter width: a full-width renderable (e.g. a Syntax
        with a background theme) would otherwise overflow the row once the gutter lands in front,
        and a real terminal's autowrap would shear every following row off the line map."""
        con = self._console(max(1, self.cols - self._gutter_width(blk)))
        lines = [l for part in blk.body for l in con.render_lines(part, pad=False)]
        blk.height = len(lines)  # CONTENT height, whatever the disclosure state paints
        blk._first = lines[0] if lines else []
        return lines

    def _gutter_segs(self, g, live, bid):
        "The gutter as segments: live gutters carry the toggle meta, others render dim (not clickable)."
        gt = g.copy() if isinstance(g, Text) else Text(str(g))
        if not gt.plain: return []
        if live: gt.stylize(Style(meta={'toggle': bid}))
        else: gt.stylize('dim')
        return self._render(gt)[0]

    def _summary_suffix(self, hidden):
        return self._render(Text(f' … (+{hidden} lines)', style='dim'))[0]

    def _fit(self, line):
        "Crop a composed line to the terminal width: one tracked line must be one screen row, never a wrap."
        if sum(cell_len(s.text) for s in line) > self.cols: return Segment.adjust_line_length(line, self.cols)
        return line

    def _block_lines(self, blk, archival=False, live=None):
        "Content-first presentation: gutter + body lines; collapsed shows line one plus a dim count."
        lines = self._content_lines(blk)
        first_g, cont_g = blk.gutter
        if live is None: live = not archival and not blk.committed and len(lines) > 1
        else: live = live and len(lines) > 1
        out = []
        shown = lines[:1] if blk.collapsed else lines
        for i, segs in enumerate(shown):
            g = self._gutter_segs(first_g if i == 0 else cont_g, live, blk.id)
            line = g + list(segs)
            if blk.collapsed and i == 0 and len(lines) > 1: line += self._summary_suffix(len(lines) - 1)
            out.append((blk.id, self._fit(line)))
        return out

    def refresh_block(self, blk):
        "Repaint a block in place when still live on the main screen (after model changes made elsewhere, e.g. transcript-mode toggles)."
        if blk.committed: return
        js = [j for j, (b, _) in enumerate(self._lines) if b == blk.id]
        if not js: return
        self._repaint(js[0], self._live_lines(blk) + self._lines[js[-1] + 1:])
        self._repark()

    def _row(self, j): return self._park - (len(self._lines)-1 - j)

    # -- painting -------------------------------------------------------------
    def _repaint(self, j0, entries):
        "Replace tracked lines from index `j0` with `entries` and repaint that suffix."
        self._unpark()
        if not entries and j0: j0, entries = j0-1, [self._lines[j0-1]]
        total = len(self._lines)
        append = j0 == total
        start = self._park + (1 if append and total else 0) if append else self._row(j0)
        scroll = max(0, start + max(len(entries),1) - 1 - (self.rows-1))
        if scroll: self._commit_scrolled(scroll, j0)
        w = []
        if total and not append: k = total-1-j0; w.append((f'\x1b[{k}A' if k else '')+'\r')
        for i,(bid,segs) in enumerate(entries):
            if i or (append and total): w.append('\r\n')
            w.append('\x1b[K'+self._ansi(segs))
        new_total = j0 + len(entries)
        if new_total < total: w.append('\x1b[J')  # the document shrank: clear the leftovers below
        w.append('\r')
        self.tty.write(''.join(w))
        self._park = min(start + len(entries) - 1, self.rows-1) if entries else start
        self._lines = self._lines[:j0] + entries
        drop = -min(0, self._park - (len(self._lines)-1))  # lines pushed above the screen top
        for bid,_ in self._lines[:drop]:
            if bid is not None: self.blocks[bid].committed = True
        self._lines = self._lines[drop:]

    def _commit_scrolled(self, scroll, j0):
        "Before rows scroll away: archival-restyle whole affected blocks, mark them committed."
        victims = {bid for j,(bid,_) in enumerate(self._lines[:j0])
                   if bid is not None and self._row(j) < scroll}
        for bid in sorted(victims):
            blk = self.blocks[bid]
            if blk.committed: continue
            js = [j for j,(b,_) in enumerate(self._lines) if b == bid]
            painted = min(1, blk.height) if blk.collapsed else blk.height
            if len(js) == painted: self._restyle(js, self._block_lines(blk, archival=True))
            blk.committed = True

    def _restyle(self, js, entries):
        "Rewrite equal-height lines `js` in place (contiguous), returning the cursor to park."
        assert len(js) == len(entries)
        total = len(self._lines)
        k_up, k_down = total-1-js[0], total-1-js[-1]
        w = [(f'\x1b[{k_up}A' if k_up else '')+'\r']
        for i,(bid,segs) in enumerate(entries):
            if i: w.append('\r\n')
            w.append('\x1b[K'+self._ansi(segs))
            self._lines[js[i]] = entries[i]
        w.append((f'\x1b[{k_down}B' if k_down else '')+'\r')
        self.tty.write(''.join(w))

    # -- public operations ----------------------------------------------------
    def _live_lines(self, blk):
        "Freshly rendered presentation lines for `blk` at current width (height is content lines, set in _block_lines)."
        return self._block_lines(blk)

    def print_block(self, body=None, gutter=None, tag=None, collapse_at=None):
        "Append a block above the tail (auto-collapsed when born over its threshold), committing whatever this pushes into history."
        blk = Block(self._next_id, body, gutter=gutter, tag=tag, collapse_at=collapse_at)
        self._next_id += 1
        self.blocks[blk.id] = blk
        lines = self._live_lines(blk)
        if blk.collapse_at and not blk.collapsed and blk.height > blk.collapse_at:
            blk.collapsed = True
            lines = self._live_lines(blk)
        j0 = len(self._lines) - self._ntail
        self._repaint(j0, lines + self._lines[j0:])
        self._repark()
        return blk

    def clear(self, *blks):
        "The ctrl-L gesture: scroll the screen into history (one history: never erase printed transcript), then reprint `blks` from the model, live again."
        self._commit_scrolled(self.rows, len(self._lines))  # archival-restyle whole visible blocks first: they are about to become history
        self.tty.write('\n' * self.rows + '\x1b[H')
        self._lines = []
        self._ntail = 0
        self._park = 0
        self._coff = self._ccol = 0  # ESC[H homed the cursor
        entries = []
        for blk in blks:
            blk.committed = False
            entries += self._live_lines(blk)
        self._repaint(0, entries)

    def extend(self, blk, part):
        "Append a body part to the still-growing last block; scrolled lines are final (progressive commit); a collapsed block grows its count, not the screen."
        nont = [b for b, _ in self._lines if b is not None]
        assert not nont or nont[-1] == blk.id or blk.id > nont[-1], 'only the last block can grow'
        new = self._render(part)
        js = [j for j, (b, _) in enumerate(self._lines) if b == blk.id]
        crossing = (blk.collapse_at and not blk.collapsed and not blk.committed and js
                    and blk.height + len(new) > blk.collapse_at)
        blk.body.append(part)
        if not js and blk.height == 0 and new: blk._first = new[0]  # first content of a born-empty stream block
        if crossing:
            blk.collapsed = True  # crossing the threshold: fold to the summary (re-rendered from the full body, so the count is right), then keep counting
            self._repaint(js[0], self._live_lines(blk) + self._lines[js[-1] + 1:])
            self._repark()
            return
        blk.height += len(new)
        if blk.collapsed:
            if js:
                line = (self._gutter_segs(blk.gutter[0], not blk.committed, blk.id)
                        + list(blk._first) + self._summary_suffix(blk.height - 1))
                self._restyle([js[0]], [(blk.id, self._fit(line))])
                self._repark()
            return
        entries = [(blk.id, l) for l in new]
        j0 = len(self._lines) - self._ntail
        self._repaint(j0, entries + self._lines[j0:])
        self._repark()

    def toggle(self, blk):
        "Flip a visible block's disclosure in place; history is inert; one-liners have nothing to hide."
        if blk.committed or blk.height <= 1: return
        blk.collapsed = not blk.collapsed
        js = [j for j, (b, _) in enumerate(self._lines) if b == blk.id]
        if not js: return
        self._repaint(js[0], self._live_lines(blk) + self._lines[js[-1] + 1:])
        self._repark()

    def _unpark(self):
        "Return the visible cursor to the park position (column 0 of the last painted line)."
        if self._coff: self.tty.write(f'\x1b[{self._coff}B')
        if self._coff or self._ccol: self.tty.write('\r')
        self._coff = self._ccol = 0

    def _repark(self):
        "Rest the visible cursor at the requested tail position, tracked as an offset from park."
        if self._tail_cursor is None or not self._ntail: return
        li, col = self._tail_cursor
        j = len(self._lines) - self._ntail + li
        if not 0 <= j < len(self._lines): return
        k = len(self._lines) - 1 - j
        w = (f'\x1b[{k}A' if k else '') + (f'\x1b[{col}C' if col else '')
        if w: self.tty.write(w)
        self._coff, self._ccol = k, col

    def set_tail(self, *renderables, cursor=None):
        """Repaint the tail, diffing line-by-line when its height is unchanged. `cursor=(line, cell col)`
        rests the visible cursor on that tail line; the 3-form `(renderable_idx, line_within, cell col)`
        addresses a line of one renderable, staying correct however the others wrap."""
        groups = [self._render(r) for r in renderables]
        if cursor is not None and len(cursor) == 3:
            ri, li, col = cursor
            cursor = (sum(len(g) for g in groups[:ri]) + li, col)
        self._tail_cursor = cursor
        entries = [(None, l) for g in groups for l in g]
        j0 = len(self._lines) - self._ntail
        if entries and len(entries) == self._ntail:
            self._unpark()
            total = len(self._lines)
            w = []
            for i,e in enumerate(entries):
                j = j0+i
                if self._ansi(e[1]) == self._ansi(self._lines[j][1]): continue
                k = total-1-j
                w.append((f'\x1b[{k}A' if k else '')+'\r\x1b[K'+self._ansi(e[1])+(f'\x1b[{k}B' if k else '')+'\r')
                self._lines[j] = e
            if w: self.tty.write(''.join(w))
            self._repark()
        else:
            self._repaint(j0, entries)
            self._ntail = len(entries)
            self._repark()
