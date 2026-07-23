"Transcript mode: an alt-screen viewport re-rendering the block model, with a block cursor and a live composer."
from rich.segment import Segment
from rich.style import Style

class TranscriptView:
    """The browsing surface: entered deliberately, left cleanly (alt screen: no residue).

    Renders the MODEL at current width -- not captured scrollback text -- so committed
    history is toggleable here: the main screen's inert-history limitation lifts where
    it matters. The composer is the same Buffer the main tail edits: type or paste
    while browsing; the host decides what Enter does (submit-and-leave, per design).

    `tail_fn` returns (renderables, cursor) exactly as the host passes to `set_tail`."""
    def __init__(self, comp, tail_fn):
        self.comp, self.tail_fn = comp, tail_fn
        self.active = False
        self.top = 0
        self.cur = None        # block-cursor id (its gutter renders reversed)
        self.lines = []        # the whole model, rendered: (block id, segments) per line
        self.changed = set()   # blocks toggled here, resynced to the main screen at leave
        self._view_rows = max(1, comp.rows - 2)

    # -- lifecycle -------------------------------------------------------------
    def enter(self):
        self.comp.tty.write('\x1b[?1049h')
        self.active = True
        self.changed = set()
        self.cur = next(reversed(self.comp.blocks), None)
        self.rebuild(bottom=True)

    def leave(self):
        self.comp.tty.write('\x1b[?1049l')
        self.active = False
        for bid in self.changed:  # toggles made here repaint on the main screen where still live
            self.comp.refresh_block(self.comp.blocks[bid])
        self.changed = set()

    # -- model rendering -------------------------------------------------------
    def rebuild(self, bottom=False):
        c = self.comp
        self.lines = []
        for bid, blk in c.blocks.items():
            lines = c._block_lines(blk, live=blk.height > 1)  # committed blocks are live HERE
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
        self._view_rows = max(1, c.rows - len(tail))
        self.top = max(0, min(self.top, max(0, len(self.lines) - self._view_rows)))
        out = ['\x1b[H']
        view = self.lines[self.top:self.top + self._view_rows]
        for i in range(self._view_rows):
            out.append('\x1b[K' + c._ansi(view[i][1] if i < len(view) else []) + '\r\n')
        for j, l in enumerate(tail):
            out.append('\x1b[K' + c._ansi(l) + ('\r\n' if j < len(tail) - 1 else '\r'))
        row, col = len(tail) - 1, 0
        if cursor is not None:
            if len(cursor) == 3:
                ri, li, col = cursor
                row = sum(len(g) for g in groups[:ri]) + li
            else: row, col = cursor
        c.tty.write(''.join(out) + f'\x1b[{self._view_rows + row + 1};{col + 1}H')

    # -- navigation and disclosure --------------------------------------------
    def scroll(self, d):
        self.top += d
        self.draw()

    def move(self, d):
        "Move the block cursor, scrolling so its first line is visible."
        order = [bid for bid, b in self.comp.blocks.items() if b.height > 0]
        if not order: return
        i = order.index(self.cur) if self.cur in order else len(order) - 1
        self.cur = order[max(0, min(len(order) - 1, i + d))]
        self.rebuild()
        first = next(i for i, (b, _) in enumerate(self.lines) if b == self.cur)
        if first < self.top: self.top = first
        elif first >= self.top + self._view_rows: self.top = first - self._view_rows + 1
        self.draw()

    def toggle_current(self):
        if self.cur is not None: self._toggle(self.comp.blocks[self.cur])

    def _toggle(self, blk):
        if blk.height <= 1: return
        blk.collapsed = not blk.collapsed
        self.changed.add(blk.id)
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
