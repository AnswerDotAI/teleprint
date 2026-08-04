# teleprint

A terminal UI library for apps built around a transcript: blocks print through to the terminal's own scrollback, a status bar and line editor repaint at the bottom, and everything in between stays clickable until it scrolls away. Written for AI CLIs and REPLs; `ipyai` is the reference app.

The pieces:

- a compositor that prints Rich-renderable blocks and repaints the visible ones in place (collapse/expand on click)
- a line editor and a key/mouse/paste input parser
- transcript mode on the alt screen: browse, search, and copy the block history (copy via OSC 52)
- a borrow layer: foreground commands take the real tty; anything headless runs against an emulator mirror
- widgets (status bar, signature panel) and a pyghostty-backed terminal emulator, so the whole surface is testable headlessly

The design sections below are the founding notes, moved here verbatim; DEV.md keeps the development record.

## Thesis

Terminal UI libraries pick a center. Textual's center is the screen: a widget tree composited onto a canvas the app owns. prompt_toolkit's center is the prompt: a line editor that borrows the tty and gives it back. Our needs (ipyai: chat transcript, tool calls, status bar, input line) orbit neither — they orbit the transcript: an append-mostly document of blocks with a small mutable edge. Neither library models that, because each refuses half of it: Textual won't cede the screen to scrollback, pt won't model anything above the prompt. teleprint's center is the transcript, and the terminal's own scrollback is its durable rendering. The name records the model: a teleprinter prints every message onto durable paper as it arrives, printhead always at the live edge.

The invariant everything else derives from:

> There is one history — the printed transcript — and anything that scrolls is a view of it. Never a parallel world.

Contract form: the block model is the source of truth; the pane is its durable rendering; the pager is its live rendering; every gesture scrolls one of those two renderings and nothing else. This is the acceptance test for features: app-private scroll buffers, mutable history, and interaction paths that exist only in one rendering all fail it.

Prior art that validates the block concept without occupying this slot: Warp (blocks, but by being the terminal emulator), Ink's Static/dynamic split (same committed/live division, no interactivity in the committed region, React-centered), Textual's inline mode (the repaint mechanics, but everything stays inside the app region and clears on exit). The strongest demand evidence: Claude Code (Ink/React) and codex (ratatui/Rust) independently converged on this exact surface grammar — print-through transcript, gutter-marked block types, repainted input-plus-status tail — and Claude Code ships the zone model precisely: its visible blocks are clickable (tool calls toggle open), going inert once scrolled. Every AI CLI is currently hand-rolling a private version of this core.

## Surfaces

Three surfaces, one rule each:

- **Durable** (main screen): printed blocks, append-only. Owned by the terminal/tmux once printed: native scroll, search, copy-mode, detach survival.
- **Live tail** (bottom rows): status bar + input editor, diff-repainted in place every frame.
- **Ephemeral** (alt screen): pager, pickers, modals. Leave no residue; only an *outcome block* enters the transcript (the decision, printed — like shell history recording the command, not the completion menu). fzf is the exemplar: transient full-screen moments don't make a tool a TUI; identity is where the app rests.

Within the main screen, two zones by mutability: **inked** (scrolled off-screen into the terminal's buffer; written once, immutable) and **visible** (redrawn from the block model on any change, so everything on screen is clickable -- folding a block simply repaints the screen from the model, however long ago it printed). The tail is part of the visible region and never enters history. Interactivity map: everything visible interactive; inked history native and inert (the transcript view for live access); alt-screen surfaces interactive while up.

## Background work, tasks, and errors

A teleprint app is one event loop reading one tty. Key handlers run synchronously -- deciding what a key means takes microseconds and must happen in arrival order -- so anything slow (a kernel round trip, an AI turn) is *launched* from a handler, never awaited inline. The compositor owns the launching. Two rules cover every case, picked by one question: **what do you need back?**

- **Nothing -- it should just happen** -> `comp.spawn(coro)`. In an `on_key` handler, simply `return` the coroutine: the dispatcher spawns it for you.
- **A handle -- you will cancel it or check on it** -> keep `spawn`'s return value.

```python
def on_key(k):
    if k.name == 'tab': return complete()  # fire-and-forget: the dispatcher spawns it
    elif k.name == 'enter':
        state['run'] = comp.spawn(run_cell(buf.text), name='run')  # handle kept: ctrl-C cancels it
    elif k.name == 'ctrl+c' and state['run'] is not None:
        state['run'].cancel()
```

Why `spawn` and never a bare `asyncio.create_task` in app code: asyncio holds only weak references to tasks, so a fire-and-forgotten task can be garbage-collected mid-flight, and an uncaught exception surfaces as a deferred "Task exception was never retrieved" splat on stderr -- which in raw mode garbles the composited screen, minutes after the cause. `spawn` keeps a strong reference until the task finishes and routes an uncaught failure to the one place the app chooses:

```python
comp.on_task_error = lambda e, t: comp.print_block(f'{t.get_name()} failed: {e!r}', gutter=ERR)
```

Unset, nothing is silenced: `spawn` deliberately does not retrieve the exception then, so asyncio's default report still fires (promptly, since the reference is released on completion). Cancellation is lifecycle, not failure -- a cancelled task never reaches the hook.

The one legitimate bare `create_task` is a task whose exception its owner consumes at an `await` site, where the hook would double-report (ipyai's stream consumer is the canonical case). That code characteristically lives below the UI layer and has no compositor in reach -- so in app code, a bare `create_task` or a dropped handle reads as a bug at a glance.

## Signals

`await comp.start()` takes them; `comp.stop()` gives them back. Apps register nothing:

- **SIGWINCH** -> `comp.on_resize` if set, else adopt-the-size-and-repaint. Set the hook whenever the app owns tail state (every real app does): `comp.on_resize = lambda: (comp.resize(), paint())`. It owns the *whole* response, so during a borrow it can forward the winsize to the foreground job and skip the repaint that would garble the borrowed screen.
- **SIGINT** -> synthesized as `Key('ctrl+c')` through normal dispatch. RealTty's cbreak keeps ISIG on, so ctrl-C arrives as a signal at rest but as an in-band byte during raw-mode borrows: either way `on_key` sees one `ctrl+c` key, and interrupt policy lives in exactly one branch.
- **SIGTERM/SIGHUP** -> restore the terminal, then die by the default disposition: a killed TUI must not leave the user's terminal raw.

Signals register only on the main thread (a CPython constraint); elsewhere `start()` skips them silently, which is also why headless tests run unchanged.

## Why not prompt_toolkit or Textual

Beyond the centering argument:

- **stdin has one owner.** CPR replies, keys, mouse, paste interleave unattributed on one fd. Our compositor needs CPR; pt's renderer also issues CPR and assumes it owns the read loop; the jobs relay and modals need the fd too. Embedding pt means demultiplexing stdin into someone else's framework assumptions (its `get_app()` application singleton, Application-owned loop) forever.
- **One rendering dialect.** Rich renders every block everywhere. pt's formatted-text model would be a second dialect; two renderers for one document drift (the pandoc lesson: one center dialect).
- The borrow contract — who owns stdin/stdout right now (tail at rest, job, modal, pager) — is the design's real center and exists in pt only implicitly (`in_terminal` chaining). Fresh code makes it a first-class object.

What we crib rather than reinvent: pt's vt100 parser tables and quirk comments, CPR-timeout discipline, raw/cooked mode management, the shape of Buffer (text+cursor+undo as pure data); Textual's XTermParser (compact modern input parser) and inline-mode mechanics (cursor-up repaint, `ESC[6n` origin tracking, `ESC[J`, render to stderr); clikernel base.py's termios lore (ONLCR off for bare LF, ICANON off because canonical mode drops bytes past MAX_CANON with BEL spam; IEXTEN off because on BSD/macOS ^O is VDISCARD and the driver eats it — found when the demo's ctrl-O binding went dead). pt's external-command philosophy (`run_system_command`: yield the real tty, don't virtualize) confirms the jobs layer sits below any UI library.

The pt lore harvest in detail (from `input/vt100.py` raw_mode, `application.py`, `renderer.py`, `output/flush_stdout.py` — read 2026-07-23, worth keeping even if pt itself isn't used):

- **Raw mode is a delta, not `setraw`.** pt patches only what it means to change ("On OS X, `pty.setraw()` fails" — their comment): lflag clears ECHO | ICANON | IEXTEN | **ISIG** (ctrl-C/Z arrive as bytes; the app owns interrupt semantics), iflag clears **IXON | IXOFF** (or ctrl-S silently freezes output — the classic "my terminal is stuck") and **ICRNL | INLCR | IGNCR** (Enter arrives as `\r`, distinguishable from ctrl-J). It never touches oflag, unlike clikernel's ONLCR handling. Set **VMIN=1 explicitly**: on Solaris-family systems the VMIN slot aliases VEOF and defaults to 4, so reads mysteriously buffer.
- **All tcgetattr/tcsetattr wrapped in try/except**: stdin may be /dev/null, an SSH pipe with no allocated tty, or closed mid-session ("Inappropriate ioctl for device").
- **Cooked mode for borrows must restore ICRNL specifically** — without it, `input()` inside a borrowed terminal shows `^M` instead of accepting Enter.
- **SIGWINCH belongs to the event loop** (`loop.add_signal_handler`, not `signal.signal`) and must be saved/restored around borrows, since the borrowed program may install its own. Backstop: **poll the size every 0.5s anyway** — SIGWINCH can't be delivered off the main thread or on Windows, and a resize during suspension is missed entirely.
- **Suspend (ctrl-Z) is a borrow too**: pt's `suspend_to_background` runs *through* `run_in_terminal` — restore cooked mode, `os.kill(0, SIGTSTP)` (the whole process *group*, for piped-input cases), and raw mode re-establishes on SIGCONT return.
- **SIGINT needs restoring at two levels**: the Python handler *and* the C-level one via `PyOS_getsig`/`PyOS_setsig` (stable ABI) — some embedders change the OS handler under Python.
- **Writes need armor**: EINTR from a resize mid-write is ignorable (the resize repaint re-renders); make stdout blocking around writes (uvloop makes it non-blocking → `BlockingIOError` on big flushes); encode with `errors='replace'` (ascii locales exist).
- **CPR discipline**: assume supported only after the first reply; probe with a 2s timer and mark NOT_SUPPORTED on silence (with a callback so the UI can adapt); track outstanding requests in a queue so replies pair with requests; skip CPR entirely for dumb terminals, non-tty stdout, or `$PROMPT_TOOLKIT_NO_CPR=1` (their pexpect escape hatch — we'll want the same for harness-driven runs); "it's nicer to draw bottom toolbars only once the height is known, to avoid flicker when the CPR response arrives."

Line editing is the honest cost of going fresh: readline-emacs subset first (arrows, ctrl-a/e/k/u/w/y, alt-b/f, ctrl-r) — what ~99% of fingers use; vi mode later at most, as a mechanical crib of pt's binding tables. `!vim` through the jobs layer covers real editing.

Multiline input scheme (decided 2026-07-23, replacing an earlier assumption that pt's checker behavior needed inventing): **Enter is smart, alt-enter is a newline, ctrl-O stays toggle.** Enter routes through IPython's `check_complete` (a `check` op on the stream protocol, answered worker-side -- which keeps IPython out of the UI process entirely, one step beyond today's ipyai, which needs a client-side TransformerManager because its kernel is remote): complete submits, incomplete inserts a continuation newline, invalid submits so execution shows the error. ipythonng's `check_complete` patch (single-line magic/alias commands count complete) rides into the worker shell. Alt-enter *always* inserts a newline, in both code and prompt mode -- the codex/Claude Code convention, and the first real typing path for multiline prompts (previously bracketed paste or F2-editor only; both remain). What stock IPython loses: meta-enter force-execute (double-Enter covers it) and ctrl-o insert-newline -- measured to be unknown even to a decades-long IPython user, so teleprint's toggle keeps the key. Findings for the record: ipyai has NO custom Enter handling today (it inherits jupyter_console's checker-driven behavior wholesale), and its multiline customization lives one layer over, in the transformer pipeline (`transform_prompt_mode`/`transform_dots`), which is routing, not completeness.

## Install

```bash
pip install teleprint
```

## Development

```bash
pip install -e .[dev]
pytest
```
