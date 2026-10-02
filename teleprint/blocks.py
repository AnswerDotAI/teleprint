"Minimal block model: identity, Rich-renderable forms, click targets. No app nouns live here."
from rich.text import Text

class Block:
    """One transcript block, presented content-first: no header line and no printed identity.

    The first content line IS the chrome: `gutter` styles the left edge (a first-line
    prefix and a continuation prefix) and carries the toggle click target. Collapsed
    presentation is the first line plus a dim `… (+N lines)` tail. Everything visible
    is clickable (write-once: the screen redraws from the model), so no bright-vs-dim
    affordance state exists.

    `key` is the app's name for the block, unique in the document: clicks and numbering
    report it. `body` is a list of Rich renderables (streams append parts). `collapse_at`
    collapses the block once, when its rendered height first passes the threshold (None:
    never); a user toggle disarms that. `source` is the model-level text behind the
    rendering (a cell's code, a reply's markdown): what search matches and copy yields;
    None falls back to plain-text extraction from the rendering. `pad` renders one leading
    blank row: presentation-only turn spacing, never part of the content."""
    def __init__(self, key, body=(), gutter=None, collapse_at=None, source=None, pad=False):
        self.key = key
        self.set(body, gutter=gutter, collapse_at=collapse_at, source=source, pad=pad)
        self.collapsed = False
        self.auto_fold = True  # collapse_at still applies: cleared once it fires, or when the user toggles
        self.dim = False  # presentational mute (e.g. hidden-from-AI): both surfaces render the block dim
        self.committed = False  # outside the screen document (a commit ended its epoch, or put with ink=False): model-only now
        self.height = 0
        self._rw = None

    def set(self, body=(), gutter=None, collapse_at=None, source=None, pad=False):
        "Replace the content and presentation fields, keeping the key and the fold state."
        self.body, self.collapse_at, self.source, self.pad = list(body), collapse_at, source, pad
        self.gutter = gutter or (Text(''), Text(''))
        self._first = None   # cached first-line content segments, for cheap collapsed summaries
        self._rows = None

    @property
    def dim(self): return self._dim
    @dim.setter
    def dim(self, v):
        "Setting dim invalidates the block's cached presentation rows, so the next frame renders the new state."
        self._dim = v
        self._rows = None
