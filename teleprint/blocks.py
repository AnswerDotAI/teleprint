"Minimal block model: identity, Rich-renderable forms, click targets. No app nouns live here."
from rich.text import Text

class Block:
    """One transcript block, presented content-first: no header line and no printed identity.

    The first content line IS the chrome: `gutter` styles the left edge (a first-line
    prefix and a continuation prefix) and carries the toggle click target. Collapsed
    presentation is the first line plus a dim `… (+N lines)` tail. Everything visible
    is clickable (write-once: the screen redraws from the model), so no bright-vs-dim
    affordance state exists.

    `body` is a list of Rich renderables (streams append parts). `tag` is a free-form
    app label (never rendered). `collapse_at` auto-collapses the block when its
    rendered height crosses the threshold (None: never). `source` is the model-level
    text behind the rendering (a cell's code, a reply's markdown): what search matches
    and copy yields; None falls back to plain-text extraction from the rendering."""
    def __init__(self, bid, body=None, gutter=None, tag=None, collapse_at=None, source=None):
        self.id, self.tag, self.collapse_at, self.source = bid, tag, collapse_at, source
        self.body = [] if body is None else [body]
        self.gutter = gutter or (Text(''), Text(''))
        self.collapsed = False
        self.dim = False  # presentational mute (e.g. hidden-from-AI): both surfaces render the block dim
        self.committed = False  # outside the screen document (a borrow ended its epoch, or record_block): model-only now
        self.height = 0
        self._first = None   # cached first-line content segments, for cheap collapsed summaries

    @property
    def dim(self): return self._dim
    @dim.setter
    def dim(self, v):
        "Setting dim invalidates the block's cached presentation rows, so the next frame renders the new state."
        self._dim = v
        self._rows = None
