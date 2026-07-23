"Minimal block model: identity, Rich-renderable forms, click targets. No app nouns live here."
from rich.text import Text

class Block:
    """One transcript block, presented content-first: no header line and no printed identity.

    The first content line IS the chrome: `gutter` styles the left edge (a first-line
    prefix and a continuation prefix), carries the toggle click target, and its
    bright-vs-dim state keeps affordances honest (bright means clickable, dim means
    history). Collapsed presentation is the first line plus a dim `… (+N lines)` tail.

    `body` is a list of Rich renderables (streams append parts). `tag` is a free-form
    app label (never rendered). `collapse_at` auto-collapses the block when its
    rendered height crosses the threshold (None: never)."""
    def __init__(self, bid, body=None, gutter=None, tag=None, collapse_at=None):
        self.id, self.tag, self.collapse_at = bid, tag, collapse_at
        self.body = [] if body is None else [body]
        self.gutter = gutter or (Text(''), Text(''))
        self.collapsed = False
        self.committed = False
        self.height = 0
        self._first = None   # cached first-line content segments, for cheap collapsed summaries
