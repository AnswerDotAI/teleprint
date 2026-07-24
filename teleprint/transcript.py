"Transcript mode: an alt-screen viewport re-rendering the block model, with a block cursor, search, and a live composer."
import base64, re
from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

class TranscriptView:
    """The browsing surface: entered deliberately, left cleanly (alt screen: no residue).

    Renders the MODEL at current width -- not captured scrollback text -- so committed
    history is toggleable here: the main screen's inert-history limitation lifts where
    it matters. The composer is the same Buffer the main tail edits: type or paste
    while browsing; the host decides what Enter does (submit-and-leave, per design).

    `tail_fn` returns (renderables, cursor) exactly as the host passes to `set_tail`.

    Keys dispatch through `on_key` (the host calls it first, as with `on_mouse`), in
    tmux/less/vim-normal style over blocks: `/` `?` `n` `N` search the model -- block
    sources and text, so matches inside collapsed blocks are found and expanded on
    landing -- `g` `G` jump to the ends, `y` copies the cursor block's model text via
    OSC 52. The composer coexists by focus: an unbound printable starts composing
    transparently (`i` does so explicitly, costing nothing bound), Esc returns to
    browsing; while composing, every other key is the host's."""
    def __init__(self, comp, tail_fn):
        self.comp, self.tail_fn = comp, tail_fn
        self.active = False
        self.top = 0
        self.cur = None        # block-cursor id (its gutter renders reversed)
        self.lines = []        # the whole model, rendered: (block id, segments) per line
        self.composing = False # compose focus: keys are the composer's until Esc
        self.search = None     # open search prompt: {'pat', 'd'}
        self.last = None       # last executed search (pat, d), for n/N
        self.msg = None        # transient status line (search misses, copy feedback)
        self.follow = False  # pinned to the tail: new blocks scroll the view (navigation unpins, G re-pins)
        self._view_rows = max(1, comp.rows - 2)

    # -- lifecycle -------------------------------------------------------------
    def enter(self):
        self.comp.tty.write('\x1b[?1049h')
        self.active = True
        self.comp.paused = True  # the alt screen owns the tty: main-screen frames go model-only until leave
        self.composing, self.search, self.msg = False, None, None
        self.cur = next(reversed(self.comp.blocks), None)
        self.follow = True   # entered at the tail: stream new blocks (`less +F`) until a navigation unpins
        self.rebuild(bottom=True)

    def leave(self):
        self.comp.tty.write('\x1b[?1049l')
        self.active = False
        self.comp.paused = False
        for blk in self.comp.blocks.values(): self.comp._dirty(blk)  # toggles/edits made here render fresh
        self.comp._frame()  # one catch-up frame: anything printed or changed during the view inks and paints now

    # -- model rendering -------------------------------------------------------
    def _hl_matches(self, segs, rx):
        "Segments with `rx` matches restyled in reverse video: split at span boundaries, style the in-span pieces."
        plain = ''.join(s.text for s in segs)
        spans = [m.span() for m in rx.finditer(plain) if m.end() > m.start()]
        if not spans: return segs
        out, pos = [], 0
        for s in segs:
            end = pos + len(s.text)
            cuts = sorted({pos, end, *(c for sp in spans for c in sp if pos < c < end)})
            for a, b in zip(cuts, cuts[1:]):
                st = (s.style or Style()) + Style(reverse=True) if any(sa <= a < sb for sa, sb in spans) else s.style
                out.append(Segment(plain[a:b], st))
            pos = end
        return out

    def rebuild(self, bottom=False):
        c = self.comp
        rx = self._rx(self.last[0]) if self.last is not None else None
        self.lines = []
        for bid, blk in c.blocks.items():
            lines = c._block_lines(blk, live=blk.height > 1)  # committed blocks are live HERE
            if rx is not None: lines = [(b, self._hl_matches(segs, rx)) for b, segs in lines]
            if bid == self.cur and lines:
                segs = [Segment(s.text, (s.style or Style()) + Style(reverse=True)) if i == 0 else s
                        for i, s in enumerate(lines[0][1])]
                lines[0] = (bid, segs)
            self.lines += lines
        if bottom: self.top = len(self.lines)  # clamped to the last page in draw
        self.draw()

    def draw(self):
        c = self.comp
        tail_rends, cursor = self.tail_fn()
        groups = [c._render(r) for r in tail_rends]
        tail = [l for g in groups for l in g]
        row, col = len(tail) - 1, 0
        if cursor is not None:
            if len(cursor) == 3:
                ri, li, col = cursor
                row = sum(len(g) for g in groups[:ri]) + li
            else: row, col = cursor
        if self.search is not None or self.msg:  # the search prompt (cursor in it) or a status note, below the composer
            p = ('/' if self.search['d'] > 0 else '?') + self.search['pat'] if self.search is not None else self.msg
            if self.search is not None: row, col = len(tail), cell_len(p)
            tail += c._render(Text(p, style='' if self.search is not None else 'dim'))
        self._view_rows = max(1, c.rows - len(tail))
        self.top = max(0, min(self.top, max(0, len(self.lines) - self._view_rows)))
        out = ['\x1b[H']
        view = self.lines[self.top:self.top + self._view_rows]
        for i in range(self._view_rows):
            out.append('\x1b[K' + c._ansi(view[i][1] if i < len(view) else []) + '\r\n')
        for j, l in enumerate(tail):
            out.append('\x1b[K' + c._ansi(l) + ('\r\n' if j < len(tail) - 1 else '\r'))
        c.tty.write(''.join(out) + f'\x1b[{self._view_rows + row + 1};{col + 1}H')

    # -- navigation and disclosure --------------------------------------------
    def scroll(self, d):
        self.follow = False
        self.top += d
        self.draw()

    def _order(self):
        return [bid for bid, b in self.comp.blocks.items() if b.height > 0]

    def _scroll_to(self, bid):
        "Scroll so `bid`'s first line is visible, and draw."
        first = next((i for i, (b, _) in enumerate(self.lines) if b == bid), None)
        if first is not None:
            if first < self.top: self.top = first
            elif first >= self.top + self._view_rows: self.top = first - self._view_rows + 1
        self.draw()

    def move(self, d):
        "Move the block cursor, scrolling so its first line is visible."
        self.follow = False
        order = self._order()
        if not order: return
        i = order.index(self.cur) if self.cur in order else len(order) - 1
        self.cur = order[max(0, min(len(order) - 1, i + d))]
        self.rebuild()
        self._scroll_to(self.cur)

    def select(self, bid):
        "Block cursor to `bid` (host motions, e.g. structure jump), scrolled into view."
        self.follow = False
        self.cur = bid
        self.rebuild()
        self._scroll_to(bid)

    def notify(self):
        "The host's new-content signal: while following, the view tracks the tail as blocks arrive."
        if self.active and self.follow:
            self.cur = next(reversed(self.comp.blocks), None)
            self.rebuild(bottom=True)

    def jump(self, end):
        "The g/G motions: block cursor to the first or last block. G resumes following the tail."
        self.follow = end
        order = self._order()
        if not order: return
        self.cur = order[-1 if end else 0]
        if not end: self.top = 0
        self.rebuild(bottom=end)

    def toggle_current(self):
        if self.cur is not None: self._toggle(self.comp.blocks[self.cur])

    def _toggle(self, blk):
        if blk.height <= 1: return
        blk.collapsed = not blk.collapsed
        self.rebuild()

    def on_mouse(self, ev):
        "While active: wheel scrolls the viewport, button-0 press toggles the block under the cell."
        if not self.active: return False
        if ev.press and ev.btn in (64, 65): self.scroll(-3 if ev.btn == 64 else 3)
        elif ev.press and ev.btn == 0:
            j = self.top + ev.y
            if j < len(self.lines):
                self.cur = self.lines[j][0]
                self._toggle(self.comp.blocks[self.cur])
        return True

    # -- search and copy -------------------------------------------------------
    def block_text(self, blk):
        "The model text search matches and `y` copies: the stored source, else plain text extracted from the rendering."
        if blk.source is not None: return blk.source
        return '\n'.join(''.join(s.text for s in l) for l in self.comp._content_lines(blk))

    def _rx(self, pat):
        "Smart-case regex; an invalid pattern matches literally."
        fl = 0 if any(c.isupper() for c in pat) else re.IGNORECASE
        try: return re.compile(pat, fl)
        except re.error: return re.compile(re.escape(pat), fl)

    def find(self, d):
        "Land the cursor on the next block matching the last search, walking `d`-wards with wraparound."
        if self.last is None: return
        rx = self._rx(self.last[0])
        order = self._order()
        if not order: return
        i = order.index(self.cur) if self.cur in order else len(order) - 1
        for k in range(1, len(order) + 1):
            bid = order[(i + d * k) % len(order)]
            if rx.search(self.block_text(self.comp.blocks[bid])): return self._land(bid)
        self.msg = f'{self.last[0]}: not found'
        self.draw()

    def _land(self, bid):
        "Cursor to a match, expanding it (fold-open-on-search) so the found text is on show."
        blk = self.comp.blocks[bid]
        if blk.collapsed: blk.collapsed = False  # fold-open on landing; the leave-frame repaints main-screen
        self.cur = bid
        self.rebuild()
        self._scroll_to(bid)

    def copy_current(self):
        "OSC 52: the cursor block's model text to the system clipboard (survives ssh/tmux)."
        if self.cur is None: return
        text = self.block_text(self.comp.blocks[self.cur])
        self.comp.tty.write('\x1b]52;c;' + base64.b64encode(text.encode()).decode() + '\x07')
        self.msg = f'copied {len(text)} chars'
        self.draw()

    # -- keys ------------------------------------------------------------------
    def on_key(self, k):
        "Modal dispatch: True when consumed; anything else is the host's (composer editing, Enter, leaving)."
        self.msg = None
        if self.search is not None: return self._search_key(k)
        if self.composing:
            if k.name != 'escape': return False
            self.composing = False
            return True
        if k.name == 'up': self.move(-1)
        elif k.name == 'down': self.move(1)
        elif k.name == 'pageup': self.scroll(-self._view_rows)
        elif k.name == 'pagedown': self.scroll(self._view_rows)
        elif k.char in ('/', '?'):
            self.search = dict(pat='', d=1 if k.char == '/' else -1)
            self.draw()
        elif k.char == 'n': self.find(self.last[1] if self.last else 1)
        elif k.char == 'N': self.find(-self.last[1] if self.last else -1)
        elif k.char == 'g': self.jump(False)
        elif k.char == 'G': self.jump(True)
        elif k.char == 'y': self.copy_current()
        elif k.char == 'i': self.composing = True
        elif k.char is not None:
            self.composing = True  # transparent compose: the unbound printable is the host's to insert
            return False
        else: return False
        return True

    def _search_key(self, k):
        s = self.search
        if k.name == 'enter':
            self.search = None
            if s['pat']:
                self.last = (s['pat'], s['d'])
                self.find(s['d'])
                return True
        elif k.name == 'escape': self.search = None
        elif k.name == 'backspace':
            if s['pat']: s['pat'] = s['pat'][:-1]
            else: self.search = None
        elif k.char is not None: s['pat'] += k.char
        self.draw()
        return True
