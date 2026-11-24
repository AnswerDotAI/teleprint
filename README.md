# teleprint

Print a growing transcript to the terminal's own scrollback. A block is a string with a key: update a streamed reply by using its key again. Visible rows change in place; rows that already scrolled away stay as printed.

Inside a Tokio runtime, write each frame to the same terminal, starting at column zero:

```rust
use teleprint::{Transcript, tty::Tty};

let mut tty = Tty::open()?;
let (cols, rows) = tty.size()?;
let mut view = Transcript::new(cols, rows);
tty.write(&view.put("request", "How many?"))?;
tty.write(&view.put("reply", "Working…"))?;
tty.write(&view.put("reply", "42"))?;
tty.write(&view.finish())?;
```

Use `sync` to replace the ordered blocks together and show an editable draft below them. Pass a UTF-8 byte offset into the draft to place the cursor. The caller handles input; `Tty.events` supplies key, paste and resize events.

`tty.mouse(true)` also requests clicks and wheel events. Use `view.hit(clicked_row - cursor_row)` with a signed row difference to get the block key or wrapped tail row under a click.

```rust
let blocks = [("request", "How many?"), ("reply", "42")];
let draft = "> next";
tty.write(&view.sync(&blocks, draft, Some(draft.len())))?;
```

For a table or other width-dependent layout, pass `(key, source, display)` triples to `sync_projected_at(cols, rows, blocks, draft, cursor)`. Use the same source across resizes. `resize(cols, rows)` reflows plain text. `finish()` removes the draft and leaves the cursor below the transcript.

SGR colors and emphasis survive; input cannot move the cursor or issue terminal commands. Use `plain(text)` for a pipe or file. For a Kitty-capable terminal, `kitty::Image::decode(base64)` reads a PNG or JPEG. Its `render(id, max_cols, tmux)` returns the protocol transmission to write first and a placeholder grid for a transcript block. Images sent on the primary screen must be sent again for alternate-screen placement; tmux requires `allow-passthrough on`.

## Python

Install with `pip install teleprint`. The Python `Transcript` class calls the same Rust implementation:

```python
import os, sys
from teleprint import Transcript

t = Transcript(*os.get_terminal_size())
for frame in (t.put('reply', 'Working…'), t.put('reply', '42'), t.finish()):
    sys.stdout.write(frame)
    sys.stdout.flush()
```

See [DEV.md](DEV.md) for the package layout and tests.
