"The compositor: renders the visible tail of the block document from the model; scrollback is a write-once record."
import time
from rich.console import Console
from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from .blocks import Block
from .keys import Parser, Key, Paste, Mouse, CPR, Ctl

class Compositor:
    """Owns the terminal under the write-once contract (DEV.md "Compositor model"): the screen
    always shows the last screenful of the rendered document -- the current epoch's blocks plus
    the tail -- redrawn from the model on any change with absolute positioning. The only thing
    that reaches scrollback is a deliberate scroll: rows crossing the top edge are painted in
    their at-that-moment state and inked forever; nothing above the edge is ever addressed
    again. Shrink slides the window back and already-inked rows repaint in their current state
    (policy 2), so everything visible stays live and clickable.

    Paint state: `_top` (screen row of the region origin, worn down to 0 as scrolls absorb the
    shell's rows), `_ws` (document rows inked so far), and the per-frame screen map for clicks.
    The single CPR runs at `start` (and again at `reanchor`, the same synchronous boundary) to
    learn the origin; it is never asked mid-flight, so there is nothing asynchronous to race."""
    def __init__(self, tty):
        self.tty = tty
        self._adopt_size()
        self.blocks = {}
        self._epoch = []      # ids of the blocks forming the current screen document (reset by a borrow)
        self._next_id = 1
        self._ws = 0          # document rows inked into scrollback
        self._top = 0         # screen row of the region origin
        self._screen = []     # per screen row: the (bid, segs) entry painted there this frame
        self._tail = []       # rendered tail entries [(None, segs), ...]
        self._tail_cursor = None
        self._over = []       # rendered transient entries, laid out directly above the tail; never ink
        self._cursor = (0, 0) # where the last frame parked the visible cursor
        self._parser = Parser()
        self.on_key = None    # callable(Key)
        self.on_paste = None  # callable(str)
        self.on_ctl = None    # callable(Ctl): OSC/APC/DCS replies and payloads
        self.on_wheel = None  # callable(direction: -1 up, 1 down), e.g. tmux copy-mode delegation
        self.on_mouse = None  # callable(Mouse) -> bool handled: lets a mode take the mouse over click/wheel defaults
        self.on_act = None    # callable(token): a click landed on tail/transient chrome carrying Style(meta={'act': token})
        self.numbering = False  # apps opt in: newest visible toggleable blocks wear alt-digit numbers
        self.numbered = {}     # per-frame {digit str: block id}, for the app's alt-digit binding
        self.paused = False   # an alt-screen surface owns the tty (transcript view): frames are model-only until unpause

    def _adopt_size(self):
        self.cols, self.rows = self.tty.size
        self._consoles = {}
        self.console = self._console(self.cols)
        self._painted = [None] * self.rows  # per-row ANSI of the last frame, for diffing

    def _console(self, width):
        if width not in self._consoles:
            self._consoles[width] = Console(width=width, force_terminal=True, color_system='truecolor',
                                            markup=False, highlight=False)
        return self._consoles[width]

    def _invalidate(self): self._painted = [None] * self.rows

    # -- input side -----------------------------------------------------------
    def _ask_cursor(self, timeout=2.0):
        "The one CPR round-trip, used only at synchronous boundaries (start, reanchor) where nothing else is in flight."
        self.tty.write('\x1b[6n')
        deadline = time.monotonic() + timeout
        while True:
            for ev in self._parser.feed(self.tty.read()):
                if isinstance(ev, CPR): return ev.row, ev.col
                self._dispatch(ev)  # keys typed while we waited are not lost
            if time.monotonic() >= deadline: raise RuntimeError(f'no CPR reply within {timeout}s')

    def start(self):
        "Adopt the tty: learn the region origin (the shell's cursor row), so launch looks like any CLI program."
        row, col = self._ask_cursor()
        if col:
            self.tty.write('\r\n')
            row = min(row + 1, self.rows - 1)
        self._top = row
        return self

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
        elif isinstance(ev, CPR): pass  # only _ask_cursor awaits these; a stray reply is noise
        elif isinstance(ev, Ctl):
            if self.on_ctl: self.on_ctl(ev)
        elif isinstance(ev, Key):
            if self.on_key: self.on_key(ev)
        elif isinstance(ev, Paste):
            if self.on_paste: self.on_paste(ev.text)

    def click(self, x, y):
        "A click at cell (x, y): line-granular through the per-frame screen map, dispatching the row's Style.meta action ('toggle' on block gutters, 'act' on tail/transient chrome)."
        e = self._screen[y] if 0 <= y < len(self._screen) else None
        if not e: return  # unpainted rows are not click targets
        for s in e[1]:
            meta = s.style.meta if s.style else {}
            if 'toggle' in meta: return self.toggle(self.blocks[meta['toggle']])
            if 'act' in meta and self.on_act: return self.on_act(meta['act'])

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
        and a real terminal's autowrap would shear the frame."""
        con = self._console(max(1, self.cols - self._gutter_width(blk)))
        lines = [l for part in blk.body for l in con.render_lines(part, pad=False)]
        blk.height = len(lines)  # CONTENT height, whatever the disclosure state paints
        blk._first = lines[0] if lines else []
        return lines

    def _gutter_segs(self, g, live, bid):
        "The gutter as segments; live gutters carry the toggle click target."
        gt = g.copy() if isinstance(g, Text) else Text(str(g))
        if not gt.plain: return []
        if live: gt.stylize(Style(meta={'toggle': bid}))
        return self._render(gt)[0]

    def _summary_suffix(self, hidden):
        return self._render(Text(f' … (+{hidden} lines)', style='dim'))[0]

    def _fit(self, line):
        "Crop a composed line to the terminal width: one document row must be one screen row, never a wrap."
        if sum(cell_len(s.text) for s in line) > self.cols: return Segment.adjust_line_length(line, self.cols)
        return line

    def _block_lines(self, blk, live=None):
        "Content-first presentation rows: gutter + body lines; collapsed shows line one plus a dim count."
        lines = self._content_lines(blk)
        if live is None: live = len(lines) > 1
        else: live = live and len(lines) > 1
        first_g, cont_g = blk.gutter
        out = []
        shown = lines[:1] if blk.collapsed else lines
        for i, segs in enumerate(shown):
            g = self._gutter_segs(first_g if i == 0 else cont_g, live, blk.id)
            line = g + list(segs)
            if blk.collapsed and i == 0 and len(lines) > 1: line += self._summary_suffix(len(lines) - 1)
            if blk.dim: line = [Segment(s.text, (s.style or Style()) + Style(dim=True)) for s in line]
            out.append((blk.id, self._fit(line)))
        return out

    def _block_rows(self, blk):
        "Presentation rows from the per-block cache, rebuilt when stale (model changed or width changed)."
        if getattr(blk, '_rows', None) is None or blk._rw != self.cols:
            blk._rows, blk._rw = self._block_lines(blk), self.cols
        return blk._rows

    def _dirty(self, blk): blk._rows = None

    def _doc_rows(self):
        out, self._spans = [], {}
        for bid in self._epoch:
            rows = self._block_rows(self.blocks[bid])
            self._spans[bid] = (len(out), len(rows))
            out += rows
        return out

    def _numbered_row(self, blk, d):
        """Row 0 of `blk` with digit `d` in the gutter's middle cell (`»»»` -> `»4»`). Needs a
        first-line gutter of >= 3 chars; keeps its base style (spans are not preserved: gutters
        are single-styled by convention)."""
        g = blk.gutter[0]
        gt = g.copy() if isinstance(g, Text) else Text(str(g))
        p = gt.plain
        if len(p.rstrip()) < 3: return None
        nt = Text(p[0] + str(d) + p[2:], style=gt.style)
        nt.stylize(Style(meta={'toggle': blk.id}))
        line = self._render(nt)[0] + list(blk._first)
        if blk.collapsed and blk.height > 1: line += self._summary_suffix(blk.height - 1)
        return (blk.id, self._fit(line))

    def _number(self, rows, ws):
        """Assign digits 0..9 to the newest visible toggleable blocks, newest first, substituting
        each block's first row with its numbered form. Digits ink as displayed (rule 2 stays
        pure). A straddler whose first row has already inked wears no digit; one-liners have
        nothing to toggle and are skipped without consuming a digit."""
        self.numbered = {}
        d = 0
        for bid in reversed(self._epoch):
            if d > 9: break
            start, cnt = self._spans[bid]
            if start + cnt <= ws: break   # this block and everything older sit above the window
            blk = self.blocks[bid]
            if blk.height <= 1 or start < ws: continue
            e = self._numbered_row(blk, d)
            if e is None: continue
            rows[start] = e
            self.numbered[str(d)] = bid
            d += 1

    # -- the frame ------------------------------------------------------------
    def _frame(self):
        """One redraw from the model: make room below the origin (the shell's own rows scroll
        off first -- already final, no pre-paint), ink whatever growth pushed across the top
        edge, then repaint the window and tail and park the cursor. Row-level diffing keeps
        keystroke frames cheap and flicker-free on terminals without mode 2026."""
        if self.paused: return  # the model advanced; the catch-up frame at unpause inks and paints the backlog
        rows = self._doc_rows()
        h, ntail = self.rows, len(self._tail)
        avail = max(0, h - ntail)
        ws = max(0, len(rows) - avail)
        if self.numbering: self._number(rows, ws)
        out = ['\x1b[?2026h']
        need = min(len(rows), avail) + min(len(self._over), avail) + ntail - (h - self._top)  # a frame is just rows: transients size the region like any other row
        if need > 0:
            k = min(need, self._top)
            out.append(f'\x1b[{h};1H' + '\n' * k)
            self._top -= k
            self._painted = [None] * h
        d = ws - self._ws
        while d > 0:  # rows _ws..ws cross the edge: paint in current state, push with real LFs, chunked
            k = min(d, h)
            for i in range(k):
                out.append(f'\x1b[{i + 1};1H\x1b[K' + self._ansi(rows[self._ws + i][1]))
            out.append(f'\x1b[{h};1H' + '\n' * k)
            self._ws += k; d -= k
            self._painted = [None] * h
        self._ws = ws  # shrink slides the window back: policy 2
        v = len(rows) - ws
        over = self._over[:max(0, h - ntail)]                  # the tail is never clipped: a pathological transient clips instead
        nover = len(over)
        free = h - (self._top + v + ntail)                     # blank rows below the tail
        covered = min(max(0, nover - max(0, free)), v)         # transcript rows the transients cover (region already spans the screen)
        entries = [rows[ws + i] for i in range(v - covered)] + over + self._tail
        self._screen = [None] * h
        for i, e in enumerate(entries):
            y = self._top + i
            if y >= h: break
            self._paint_row(y, e, out)
        for y in range(min(self._top + len(entries), h), h): self._paint_row(y, None, out)
        row = self._top + v - covered + nover + (self._tail_cursor[0] if self._tail_cursor and ntail else max(ntail - 1, 0))
        col = self._tail_cursor[1] if self._tail_cursor and ntail else 0
        row = min(row, h - 1)
        out.append(f'\x1b[{row + 1};{col + 1}H\x1b[?2026l')
        self.tty.write(''.join(out))
        self._cursor = (row, col)

    def _paint_row(self, y, e, out):
        ansi = '' if e is None else self._ansi(e[1])
        self._screen[y] = e
        if self._painted[y] == ansi: return
        out.append(f'\x1b[{y + 1};1H\x1b[K' + ansi)
        self._painted[y] = ansi

    # -- public operations ----------------------------------------------------
    def print_block(self, body=None, gutter=None, tag=None, collapse_at=None, source=None):
        "Append a block to the document (auto-collapsed when born over its threshold) and repaint."
        blk = Block(self._next_id, body, gutter=gutter, tag=tag, collapse_at=collapse_at, source=source)
        self._next_id += 1
        self.blocks[blk.id] = blk
        self._content_lines(blk)  # measure, so the collapse threshold applies before first paint
        if blk.collapse_at and blk.height > blk.collapse_at: blk.collapsed = True
        self._epoch.append(blk.id)
        self._frame()
        return blk

    def record_block(self, body=None, gutter=None, tag=None, collapse_at=None, source=None):
        """A model-only block: enters the model outside the screen document, painting nothing --
        for content whose bytes are already on glass (a fg job's pty output). The transcript
        view, persistence, and the Dialog all see it; the screen never repeats it."""
        blk = Block(self._next_id, body, gutter=gutter, tag=tag, collapse_at=collapse_at, source=source)
        self._next_id += 1
        self.blocks[blk.id] = blk
        self._content_lines(blk)  # sets height, so the collapse threshold and views work
        if blk.collapse_at and blk.height > blk.collapse_at: blk.collapsed = True
        blk.committed = True
        return blk

    def extend(self, blk, part):
        "Append a body part to the still-growing last block; a collapsed block grows its count, not the screen."
        assert not self._epoch or self._epoch[-1] == blk.id or blk.id > self._epoch[-1], 'only the last block can grow'
        con = self._console(max(1, self.cols - self._gutter_width(blk)))
        new = con.render_lines(part, pad=False)
        first = blk.height == 0
        blk.body.append(part)
        if first and new: blk._first = new[0]
        crossing = blk.collapse_at and not blk.collapsed and blk.height + len(new) > blk.collapse_at
        blk.height += len(new)
        if crossing:
            blk.collapsed = True  # crossing the threshold: fold to the summary, then keep counting
            self._dirty(blk)
        elif blk.collapsed:
            rows = self._block_rows(blk)  # cache exists at current width: refresh the one summary row
            line = (self._gutter_segs(blk.gutter[0], True, blk.id) + list(blk._first)
                    + self._summary_suffix(blk.height - 1))
            rows[:] = [(blk.id, self._fit(line))]
        else:
            rows = self._block_rows(blk)
            first_g, cont_g = blk.gutter
            base = blk.height - len(new)
            for i, segs in enumerate(new):
                g = self._gutter_segs(first_g if base + i == 0 else cont_g, True, blk.id)
                rows.append((blk.id, self._fit(g + list(segs))))
        self._frame()

    def toggle(self, blk):
        "Flip a block's disclosure: the screen redraws from the model, so anything in the document toggles (however far its top has inked); one-liners have nothing to hide."
        if blk.committed or blk.height <= 1: return
        blk.collapsed = not blk.collapsed
        self._dirty(blk)
        self._frame()

    def refresh_block(self, blk):
        "Repaint after model changes made elsewhere (e.g. transcript-mode toggles)."
        self._dirty(blk)
        if blk.id in self._epoch: self._frame()


    def set_body(self, blk, *renderables, source=None):
        """Replace a block's content in the model (editing): new renderables, new `source`, re-measured.
        No repaint here -- the caller frames (or a transcript view rebuilds) when it is ready; rows
        already in scrollback keep the old text, which is the log being a log."""
        blk.body = list(renderables)
        blk.source = source
        blk._first = None
        self._dirty(blk)
        self._content_lines(blk)  # height now reflects the new content, for disclosure and views

    def remove_block(self, blk):
        """Remove a block from the model entirely (conversation rewind): the window then shows the model
        as it now stands. Rows the block already inked stay in history -- the log is a log."""
        self.blocks.pop(blk.id, None)
        if blk.id in self._epoch: self._epoch.remove(blk.id)
        self._frame()

    def set_tail(self, *renderables, cursor=None, over=()):
        """Repaint the tail. `cursor=(line, cell col)` rests the visible cursor on that tail line;
        the 3-form `(renderable_idx, line_within, cell col)` addresses a line of one renderable,
        staying correct however the others wrap. `over` renderables are transients (completion
        menu, tooltip, picker): they sit directly above the tail, take free rows below it while
        the region is still filling the screen, cover the newest transcript rows once it has,
        and never ink -- closing one is just the next frame without it."""
        groups = [self._render(r) for r in renderables]
        if cursor is not None and len(cursor) == 3:
            ri, li, col = cursor
            cursor = (sum(len(g) for g in groups[:ri]) + li, col)
        self._tail_cursor = cursor
        self._tail = [(None, l) for g in groups for l in g]
        self._over = [(None, l) for r in over for l in self._render(r)]
        self._frame()

    def resize(self):
        "Adopt the new size and repaint from the model: the same move as any other frame, nothing asynchronous. The app should repaint its tail next (old rendered tail rows are width-stale, so they are dropped here)."
        self._adopt_size()  # block caches self-invalidate on width change (_block_rows checks _rw)
        self._top = min(self._top, self.rows - 1)  # height-shrink during the startup phase may misplace the origin by the terminal's own trim; heals when the region reaches the top
        self._tail = []
        self._over = []
        self._tail_cursor = None
        self._invalidate()
        self._frame()

    def release(self):
        """Begin a borrow: the borrower (a fg job on the pty) owns the terminal until `reanchor`.
        Content rows on glass are final -- the borrower's output will scroll them into history --
        and the tail (chrome, not transcript) is erased, leaving the cursor at column 0 of a
        fresh line. The epoch ends: after the borrow the document restarts below the borrower's
        output, and everything before it is inked for good."""
        v = len(self._doc_rows()) - self._ws
        y = self._top + v
        if y > self.rows - 1:  # content reaches the bottom row: open a fresh line (the scroll inks one row, as displayed)
            self.tty.write(f'\x1b[{self.rows};1H\r\n\x1b[J')
            y = self.rows - 1
        else:
            self.tty.write(f'\x1b[{y + 1};1H\x1b[J')
        for b in self.blocks.values(): b.committed = True
        self._epoch = []
        self._ws = 0
        self._tail = []
        self._over = []
        self._tail_cursor = None
        self._top = y
        self._invalidate()

    def reanchor(self):
        "End a borrow: whatever the borrower painted is history now; adopt the (possibly new) size and learn a fresh origin -- the startup move again."
        self._adopt_size()
        for b in self.blocks.values(): b.committed = True
        self._epoch = []
        self._ws = 0
        self._tail = []
        self._over = []
        self._tail_cursor = None
        row, col = self._ask_cursor()
        if col:
            self.tty.write('\r\n')
            row = min(row + 1, self.rows - 1)
        self._top = row
