# Development

The root crate contains the transcript renderer and terminal I/O. Rust clients do not link Python. The unpublished `py/` crate exposes `Transcript` and Kitty `Image` through `python/teleprint/`; maturin gets both package versions from the root `Cargo.toml`.

In the Answer.AI workspace, after the initial editable installation:

```sh
cargo develop && cargo test --features tty && pytest -q
```

The Rust tests select blocks and draft rows and hand a real PTY between raw input, a cooked program, and decoded keys. The Python tests feed the extension's output to Ghostty and check scrollback, cursor position, styles and image placement. Use `uv build --wheel` to build a distribution through the same backend as a standalone install.

## Printing a frame

The renderer remembers the rows it last painted and the cursor's position in them. A frame starts at the first changed row, clears the stale suffix and prints the new one. When the new suffix extends below the terminal, normal line feeds put completed rows into native scrollback before the live tail is painted. The renderer never switches to the alternate screen or requests a cursor position.

The caller supplies the ordered blocks, draft and cursor. A resize reflows text at its new width. For tables and numbered gutters, render them at the new width and pass `(key, source, display)` triples to `sync_projected_at`. Each display row sets and clears its own SGR attributes.

When a width change leaves part of a block above the viewport, its remaining rows stay on the terminal as printed. Repainting the block at the new width could repeat words already in native scrollback. The next whole block reflows normally. Text appended to the committed source starts as a plain continuation on the next row. Replacing committed text does not rewrite scrollback.

[DESIGN.md](DESIGN.md) records the original terminal interaction research, including proposals for the former Python compositor.
