"Tail widgets: reusable input-adjacent UI, rendered as tail lines. App-agnostic: no kernels, no completion sources."
import os.path
from rich.text import Text

class CompletionMenu:
    """A cycling completion menu over a `Buffer` span.

    `matches` replace the span from `start` to the buffer's cursor; `cycle` writes
    the highlighted match into the buffer live (Tab / shift+Tab semantics), and
    `insert_common` gives the readline common-prefix behavior. The host owns the
    lifecycle: keep while cycling, drop on accept or on any other key."""
    def __init__(self, buf, matches, start, show=8):
        self.buf, self.matches, self.start, self.show, self.i = buf, matches, start, show, None

    def insert_common(self):
        "Insert the longest common prefix when it extends the span; True if it did."
        common = os.path.commonprefix(self.matches)
        if len(self.matches) == 1 or len(common) > self.buf.cursor - self.start:
            self.buf.text = self.buf.text[:self.start] + common + self.buf.text[self.buf.cursor:]
            self.buf.cursor = self.start + len(common)
            return True
        return False

    def cycle(self, d=1):
        "Highlight the next (`d`=1) or previous (`d`=-1) match, writing it into the buffer span."
        if self.i is None: self.i = 0 if d > 0 else len(self.matches) - 1
        else: self.i = (self.i + d) % len(self.matches)
        m = self.matches[self.i]
        self.buf.text = self.buf.text[:self.start] + m + self.buf.text[self.buf.cursor:]
        self.buf.cursor = self.start + len(m)

    def renderable(self):
        "One dim line: a window of matches around the highlight, the highlighted one reversed."
        lo = 0 if self.i is None else max(0, min(self.i - self.show // 2, len(self.matches) - self.show))
        t = Text()
        for j, m in enumerate(self.matches[lo:lo + self.show], lo):
            if j > lo: t.append('  ')
            t.append(m, style='reverse' if j == self.i else 'dim')
        if len(self.matches) > self.show: t.append(f'  ({len(self.matches)} matches)', style='dim')
        return t

class Tooltip:
    "A transient text panel (e.g. a signature from inspection), clipped to `max_lines` tail lines."
    def __init__(self, text, max_lines=8):
        self.text, self.max_lines = text, max_lines

    def renderable(self):
        t = self.text if isinstance(self.text, Text) else Text(str(self.text))
        lines = t.split('\n')
        if len(lines) > self.max_lines:
            t = Text('\n').join(lines[:self.max_lines])
            t.append('\n…', style='dim')
        return t

class Signature:
    "A call signature: name and a compact wrapping params line, the active param bold, dim doc excerpt below."
    def __init__(self, name, params, active=None, doc='', doc_lines=6):
        self.name, self.params, self.active, self.doc, self.doc_lines = name, params, active, doc, doc_lines

    def renderable(self):
        t = Text(self.name, style='bold')
        t.append('(', style='dim')
        for i, p in enumerate(self.params):
            if i: t.append(', ', style='dim')
            t.append(p, style='bold' if i == self.active else 'dim')
        t.append(')', style='dim')
        if self.doc:
            doc = Tooltip(Text(self.doc, style='dim'), max_lines=self.doc_lines).renderable()
            t.append('\n')
            t.append(doc)
        return t
