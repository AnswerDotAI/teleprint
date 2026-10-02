"The compositor: renders the visible tail of the block document from the model; scrollback is a write-once record."
import asyncio, os, re, signal, time
from contextlib import asynccontextmanager
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

    The app names each block with a key (`put`). `blocks` holds them in document order; the
    epoch is the run of blocks still in the screen document, until `commit` or `borrow` ends it.

    Paint state: `_top` (screen row of the region origin, worn down to 0 as scrolls absorb the
    shell's rows), `_ws` (document rows inked so far), and the per-frame screen map for clicks.
    The single CPR runs at `start` (and again when a `borrow` ends, the same quiet boundary) to
    learn the origin; both await it, but nothing else is ever in flight there, so there is no race."""
    def __init__(self, tty):
        self.tty = tty
        self._adopt_size()
        self.blocks = {}      # key -> Block, in document order
        self._epoch = []      # keys of the blocks forming the current screen document, in document order
        self._ws = 0          # document rows inked into scrollback
        self._top = 0         # screen row of the region origin
        self._screen = []     # per screen row: the (key, segs) entry painted there this frame
        self._tail = []       # rendered tail entries [(None, segs), ...]
        self._tail_cursor = None
        self._over = []       # rendered transient entries, laid out directly above the tail; never ink
        self._cursor = (0, 0) # where the last frame parked the visible cursor
        self._parser = Parser()
        self.on_key = None    # callable(Key); a returned coroutine is scheduled
        self.on_paste = None  # callable(str)
        self.on_ctl = None    # callable(Ctl): OSC/APC/DCS replies and payloads
        self.on_wheel = None  # callable(direction: -1 up, 1 down), e.g. tmux copy-mode delegation
        self.on_mouse = None  # callable(Mouse) -> bool handled: lets a mode take the mouse over click/wheel defaults
        self.on_act = None    # callable(token): a click landed on tail/transient chrome carrying Style(meta={'act': token})
        self.on_task_error = None  # callable(exc, task): a spawned task failed; unset -> asyncio's default report
        self.on_resize = None  # callable(): SIGWINCH; unset -> self.resize(). Set it to own the whole response (apps with tail state)
        self.on_change = None  # callable(): put, drop or extend changed the document (TranscriptView follows through it)
        self._tasks = set()   # strong refs to spawned tasks: asyncio's registry holds only weak ones
        self._signals = []    # signals start() registered on the loop, removed by stop()
        self._modes = []      # DEC private modes set through set_modes, reset by stop and by fatal signals
        self._reading = False # whether the tty reader is installed (real ttys only; borrow and query suspend it)
        self._ticker = None   # the input-flush task running while the reader is installed
        self._borrowing = False
        self._borrow_resize = None  # borrow's on_resize: SIGWINCH goes here while the terminal is lent
        self.numbering = False  # apps opt in: newest visible toggleable blocks wear alt-digit numbers
        self.numbered = {}     # per-frame {digit str: block key}, for the app's alt-digit binding
        self.paused = False   # an alt-screen surface owns the tty (transcript view): frames are model-only until unpause

    def _adopt_size(self):
        self.cols, self.rows = self.tty.size
        self._consoles = {}
        self.console = self._console(self.cols)
        self._painted = [None] * self.rows  # per-row ANSI of the last frame, for diffing

    def _console(self, width):
        if width not in self._consoles:
            self._consoles[width] = Console(width=width, height=self.rows, force_terminal=True,
                color_system='truecolor', markup=False, highlight=False)
        return self._consoles[width]

    def _invalidate(self): self._painted = [None] * self.rows

    # -- input side -----------------------------------------------------------
    async def _ask_cursor(self, timeout=2.0):
        "The one CPR round-trip, used only at quiet boundaries (start, the end of a borrow). Input read while waiting, before or after the reply, is dispatched, not lost."
        self.tty.write('\x1b[6n')
        deadline = time.monotonic() + timeout
        while True:
            evs = self._parser.feed(self.tty.read())
            cpr = next((ev for ev in evs if isinstance(ev, CPR)), None)
            for ev in evs:
                if ev is not cpr: self._dispatch(ev)
            if cpr is not None: return cpr.row, cpr.col
            if time.monotonic() >= deadline: raise RuntimeError(f'no CPR reply within {timeout}s')
            await asyncio.sleep(0)  # yield between the short blocking reads: the loop stays live at the boundary

    async def _anchor(self):
        "Learn the region origin: the cursor's row, or the next row when the cursor is mid-line."
        row, col = await self._ask_cursor()
        if col:
            self.tty.write('\r\n')
            row = min(row + 1, self.rows - 1)
        self._top = row

    async def query(self, payload, until, timeout=0.5):
        """Write the terminal query `payload`, then read input until the bytes regex `until` matches it or
        `timeout` seconds pass, returning the bytes read. Keys and other input in those bytes are still dispatched."""
        reading = self._reading
        self._reader_off()
        try:
            self.tty.write(payload)
            data, end = b'', time.monotonic() + timeout
            while time.monotonic() < end:
                if new := self.tty.read():
                    data += new
                    for ev in self._parser.feed(new): self._dispatch(ev)
                    if re.search(until, data): break
                else: await asyncio.sleep(0.005)
            return data
        finally:
            if reading: self._reader_on()

    async def start(self):
        """Adopt the tty and the loop: learn the region origin (the shell's cursor row), take the signals, turn
        on SGR mouse reporting and bracketed paste, and on a real tty start reading input."""
        await self._anchor()
        self._register_signals()
        self.set_modes(1000, 1006, 2004)
        self._reader_on()
        return self

    def stop(self):
        "Give back what start() took: the reader, the terminal modes and the signal handlers. Other kept task handles stay the app's to cancel."
        self._reader_off()
        self._reset_modes()
        loop = asyncio.get_running_loop()
        for sig in self._signals: loop.remove_signal_handler(sig)
        self._signals.clear()

    def set_modes(self, *modes, on=True):
        "Set DEC private `modes` (reset them with `on=False`). Modes left set are reset by `stop` and by fatal signals."
        if not modes: return
        self.tty.write(f'\x1b[?{";".join(map(str, modes))}{"h" if on else "l"}')
        for m in modes:
            if m in self._modes: self._modes.remove(m)
            if on: self._modes.append(m)

    def _reset_modes(self):
        "Reset every mode still set, newest first (the alternate screen before mouse reporting)."
        if self._modes: self.tty.write(f'\x1b[?{";".join(map(str, reversed(self._modes)))}l')
        self._modes = []

    def _reader_on(self):
        "On a real tty, read input as it arrives and resolve pending escapes on a 0.2 s tick. Test ttys have no `fd`: tests feed `on_bytes` directly."
        fd = getattr(self.tty, 'fd', None)
        if fd is None or self._reading: return
        asyncio.get_running_loop().add_reader(fd, lambda: self.on_bytes(os.read(fd, 4096)))
        self._ticker = self.spawn(self._flush_tick(), name='input-flush')
        self._reading = True

    def _reader_off(self):
        if not self._reading: return
        asyncio.get_running_loop().remove_reader(self.tty.fd)
        self._ticker.cancel()
        self._reading, self._ticker = False, None

    async def _flush_tick(self):
        while True:
            await asyncio.sleep(0.2)
            self.flush_input()

    def spawn(self, coro, name=None):
        """Schedule background work from sync UI code: create the task, keep it alive (asyncio holds
        only weak refs), and surface an uncaught failure through `on_task_error` (unset: asyncio's
        default report). Returns the Task -- keep it iff you will cancel or check it. The one case
        for a bare `create_task` instead is a task whose exception the owner consumes at an `await`
        site, where the hook would double-report."""
        t = asyncio.create_task(coro, name=name)
        self._tasks.add(t)
        t.add_done_callback(self._task_done)
        return t

    def _task_done(self, t):
        self._tasks.discard(t)
        if t.cancelled() or self.on_task_error is None: return  # unset: don't retrieve, so asyncio's own report survives
        if (e := t.exception()) is not None: self.on_task_error(e, t)

    def _register_signals(self):
        "WINCH -> `on_resize` (unset: adopt+repaint); INT -> a synthetic ctrl-C key, one surface whichever transport; TERM/HUP -> restore the tty, then die by default disposition."
        loop = asyncio.get_running_loop()
        hs = ((signal.SIGWINCH, self._on_winch), (signal.SIGINT, lambda: self._dispatch(Key('ctrl+c'))),
            (signal.SIGTERM, lambda: self._fatal(signal.SIGTERM)), (signal.SIGHUP, lambda: self._fatal(signal.SIGHUP)))
        for sig, h in hs:
            try: loop.add_signal_handler(sig, h)
            except ValueError: return  # signals work only on the main thread; a worker-thread loop just goes without
            self._signals.append(sig)

    def _on_winch(self):
        if self._borrowing:
            if self._borrow_resize: self._borrow_resize()
        elif self.on_resize: self.on_resize()
        else: self.resize()

    def _fatal(self, sig):
        "A fatal signal: reset the terminal modes and put the tty back first, then die by the default disposition so the exit status stays honest."
        self._reset_modes()
        self.tty.restore()
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)

    def on_bytes(self, data):
        "Parse terminal input and dispatch it: clicks and wheel handled here, keys and pastes go to the `on_key`/`on_paste` hooks."
        for ev in self._parser.feed(data): self._dispatch(ev)

    def flush_input(self):
        "Resolve a pending escape whose wait has run out (the reader's tick calls this on a real tty; tests call it directly)."
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
            if self.on_key:
                r = self.on_key(ev)
                if asyncio.iscoroutine(r): self.spawn(r)  # a handler may return a coroutine: the async action it wants scheduled
        elif isinstance(ev, Paste):
            if self.on_paste: self.on_paste(ev.text)

    def click(self, x, y):
        "A click at cell (x, y): line-granular through the per-frame screen map, dispatching the row's Style.meta action ('toggle' on block gutters, 'act' on tail/transient chrome)."
        e = self._screen[y] if 0 <= y < len(self._screen) else None
        if not e: return  # unpainted rows are not click targets
        for s in e[1]:
            meta = s.style.meta if s.style else {}
            if 'toggle' in meta: return self.toggle(meta['toggle'])
            if 'act' in meta and self.on_act: return self.on_act(meta['act'])

    # -- rendering ------------------------------------------------------------
    def _render(self, renderable): return self.console.render_lines(renderable, pad=False)

    def _ansi(self, segs): return ''.join(s.style.render(s.text) if s.style else s.text for s in segs)

    def _gutter_width(self, blk):
        f, c = blk.gutter
        return max(cell_len(f.plain if isinstance(f, Text) else str(f)), cell_len(c.plain if isinstance(c, Text) else str(c)))

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

    def _gutter_segs(self, g, key):
        "The gutter as segments, carrying the block's toggle click target."
        gt = g.copy() if isinstance(g, Text) else Text(str(g))
        if not gt.plain: return []
        gt.stylize(Style(meta={'toggle': key}))
        return self._render(gt)[0]

    def _summary_suffix(self, hidden): return self._render(Text(f' … (+{hidden} lines)', style='dim'))[0]

    def _fit(self, line):
        "Crop a composed line to the terminal width: one document row must be one screen row, never a wrap."
        if sum(cell_len(s.text) for s in line) > self.cols: return Segment.adjust_line_length(line, self.cols)
        return line

    def _row(self, blk, i, segs, gutter=None):
        """Presentation row for content line `i` of `blk`: its gutter (or `gutter`, e.g. a numbered one), the
        content `segs`, and on a collapsed block's first line the dim count of hidden lines. A dim block's row
        is dim throughout. Cropped to the width."""
        g = gutter if gutter is not None else blk.gutter[0 if i == 0 else 1]
        line = self._gutter_segs(g, blk.key) + list(segs)
        if i == 0 and blk.collapsed and blk.height > 1: line += self._summary_suffix(blk.height - 1)
        if blk.dim: line = [Segment(s.text, (s.style or Style()) + Style(dim=True)) for s in line]
        return (blk.key, self._fit(line))

    def _block_lines(self, blk):
        "Content-first presentation rows: a blank pad row if the block has one, then line one when collapsed, else every line."
        lines = self._content_lines(blk)
        shown = lines[:1] if blk.collapsed else lines
        return ([(blk.key, [])] if blk.pad else []) + [self._row(blk, i, segs) for i, segs in enumerate(shown)]

    def _block_rows(self, blk):
        "Presentation rows from the per-block cache, rebuilt when stale (model changed or width changed)."
        if blk._rows is None or blk._rw != self.cols: blk._rows, blk._rw = self._block_lines(blk), self.cols
        return blk._rows

    def _dirty(self, blk): blk._rows = None

    def _doc_rows(self):
        out, self._spans = [], {}
        for key in self._epoch:
            rows = self._block_rows(self.blocks[key])
            self._spans[key] = (len(out), len(rows))
            out += rows
        return out

    def _digit_gutter(self, g, d):
        """Gutter `g` with digit `d` in its middle cell (`»»»` -> `»4»`), or None when `g` is under 3 cells.
        Keeps the base style only: gutters are single-styled by convention."""
        gt = g.copy() if isinstance(g, Text) else Text(str(g))
        p = gt.plain
        if len(p.rstrip()) < 3: return None
        return Text(p[0] + str(d) + p[2:], style=gt.style)

    def _number(self, rows, ws):
        """Assign digits 0..9 to the newest visible toggleable blocks, newest first, substituting
        each block's first content row (past any pad row) with its numbered form. Digits ink as displayed (rule 2 stays
        pure). A straddler whose first row has already inked wears no digit; one-liners have
        nothing to toggle and are skipped without consuming a digit."""
        self.numbered = {}
        d = 0
        for key in reversed(self._epoch):
            if d > 9: break
            start, cnt = self._spans[key]
            if start + cnt <= ws: break   # this block and everything older sit above the window
            blk = self.blocks[key]
            if blk.height <= 1 or start + blk.pad < ws: continue
            g = self._digit_gutter(blk.gutter[0], d)
            if g is None: continue
            rows[start + blk.pad] = self._row(blk, 0, blk._first, gutter=g)
            self.numbered[str(d)] = key
            d += 1

    # -- the frame ------------------------------------------------------------
    def _frame(self):
        """One redraw from the model: make room below the origin (the shell's own rows scroll
        off first -- already final, no pre-paint), ink whatever growth pushed across the top
        edge, then repaint the window and tail and park the cursor. Row-level diffing keeps
        keystroke frames cheap and flicker-free on terminals without mode 2026."""
        if self.paused or self._borrowing: return  # the model advanced; the catch-up frame at unpause inks and paints the backlog
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
            for i in range(k): out.append(f'\x1b[{i + 1};1H\x1b[K' + self._ansi(rows[self._ws + i][1]))
            out.append(f'\x1b[{h};1H' + '\n' * k)
            self._ws += k
            d -= k
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
    def put(self, key, *body, gutter=None, source=None, collapse_at=None, dim=False, pad=False, after=None, ink=True):
        """Create or replace the block for `key`, repaint if it is on screen, and return it. A replaced block
        keeps its place and its fold state. A new block goes after the block keyed `after`, or at the end of
        the document. It joins the screen document unless it lands among committed blocks. With `ink=False`
        it is recorded without being painted, for content already on glass such as a foreground job's output."""
        blk = self.blocks.get(key)
        if blk is None:
            blk = Block(key, body, gutter=gutter, collapse_at=collapse_at, source=source, pad=pad)
            self._insert(blk, after, ink)
        else: blk.set(body, gutter=gutter, collapse_at=collapse_at, source=source, pad=pad)
        blk.dim = dim
        self._content_lines(blk)
        self._auto_fold(blk)
        if key in self._epoch: self._frame()
        self._changed()
        return blk

    def _insert(self, blk, after, ink):
        "Place new `blk` after the block keyed `after` (the end if None). It joins the epoch before its successor, unless that successor is committed or `ink` is off."
        if after is not None and after not in self.blocks: raise KeyError(after)
        nxt = None
        if after is None or after == next(reversed(self.blocks)): self.blocks[blk.key] = blk
        else:
            items = list(self.blocks.items())
            i = next(j for j, (k, _) in enumerate(items) if k == after) + 1
            nxt = items[i][0]
            items.insert(i, (blk.key, blk))
            self.blocks = dict(items)
        if not ink or (nxt is not None and self.blocks[nxt].committed): blk.committed = True
        else: self._epoch.insert(len(self._epoch) if nxt is None else self._epoch.index(nxt), blk.key)

    def _auto_fold(self, blk):
        "Collapse `blk` the first time its height passes `collapse_at`. A user toggle disarms this."
        if blk.auto_fold and blk.collapse_at and blk.height > blk.collapse_at:
            blk.collapsed, blk.auto_fold = True, False
            self._dirty(blk)

    def _changed(self):
        if self.on_change: self.on_change()

    def drop(self, *keys):
        "Remove blocks from the model, as in a conversation rewind. The window then shows the model as it now stands, and rows already inked stay in history."
        live = False
        for k in keys:
            if self.blocks.pop(k, None) is None: continue
            if k in self._epoch:
                self._epoch.remove(k)
                live = True
        if live: self._frame()
        self._changed()

    def extend(self, key, part):
        "Append a body part to block `key`, rendering only the new lines. A collapsed block grows its hidden-line count, not the screen."
        blk = self.blocks[key]
        new = self._console(max(1, self.cols - self._gutter_width(blk))).render_lines(part, pad=False)
        blk.body.append(part)
        if blk.height == 0 and new: blk._first = new[0]
        base = blk.height
        blk.height += len(new)
        self._auto_fold(blk)
        if blk._rows is not None and blk._rw == self.cols:
            if blk.collapsed: blk._rows[blk.pad:] = [self._row(blk, 0, blk._first)]
            else: blk._rows += [self._row(blk, base + i, segs) for i, segs in enumerate(new)]
        if key in self._epoch: self._frame()
        self._changed()

    def toggle(self, key, collapsed=None):
        """Flip block `key`'s disclosure, or set it to `collapsed`. A block whose top rows have inked still
        toggles, because the screen redraws from the model. One-liners have nothing to hide. A toggle that
        changes the block disarms its auto-collapse."""
        blk = self.blocks[key]
        if blk.height <= 1: return
        new = not blk.collapsed if collapsed is None else collapsed
        if new == blk.collapsed: return
        blk.collapsed, blk.auto_fold = new, False
        self._dirty(blk)
        if key in self._epoch: self._frame()

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

    def commit(self):
        """End the epoch: the blocks on screen become printed trace, and later blocks print below them.
        The tail (chrome, not transcript) is erased, leaving the cursor at column 0 of a fresh line."""
        v = len(self._doc_rows()) - self._ws
        y = self._top + v
        if y > self.rows - 1:  # content reaches the bottom row: open a fresh line (the scroll inks one row, as displayed)
            self.tty.write(f'\x1b[{self.rows};1H\r\n\x1b[J')
            y = self.rows - 1
        else: self.tty.write(f'\x1b[{y + 1};1H\x1b[J')
        for k in self._epoch: self.blocks[k].committed = True
        self._epoch = []
        self._ws = 0
        self._tail = []
        self._over = []
        self._tail_cursor = None
        self._top = y
        self._invalidate()

    @asynccontextmanager
    async def borrow(self, on_resize=None):
        """Lend the terminal to a foreground program, yielding the tty. The borrow commits the screen, stops
        reading input, pauses frames, resets the terminal modes set through `set_modes` and enters raw mode.
        While it lasts, SIGWINCH goes to `on_resize`. On exit it restores cooked mode and those modes, learns a
        fresh origin below whatever the borrower printed, and then resumes input. Blocks put during the borrow
        paint after it."""
        reading = self._reading
        self._reader_off()
        self.commit()
        modes = list(self._modes)
        self._reset_modes()  # the borrower must not receive mouse reports or paste brackets
        self.tty.raw()
        self._borrowing, self._borrow_resize = True, on_resize
        try: yield self.tty
        finally:
            self.tty.cooked()
            self.set_modes(*modes)
            self._borrowing, self._borrow_resize = False, None
            self._adopt_size()
            await self._anchor()
            if reading: self._reader_on()
