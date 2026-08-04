# teleprint development notes

Design rationale and working notes for teleprint and the ipyai rewrite that drives it, distilled from the founding research conversation (which started from kittytgp and "what else can Unicode placeholders do?"). The Thesis, Surfaces, and Why-not sections live in README.md; what was built when lives in git. This file keeps what a newcomer needs: the rules that govern the design, the architecture as it stands, and what is deliberately not done yet.

## The design principle

Work *with* the terminal's natural behavior, never against it. This is the #1 goal and the
tiebreaker for every design decision: when a feature needs the terminal to be something it
isn't, the feature changes shape, not the terminal. In practice:

- History IS the terminal's scrollback. We never track, address, or repaint anything that has
  scrolled away: content crosses the top edge via real LF scrolls, once, and tmux/terminal
  search, selection, and copy work on it natively forever.
- Launch like any CLI program: adopt the shell's cursor row (the one CPR) and start printing
  there. The shell's screen stays put and scrolls off only as content needs the space. No
  clear-screen takeover; quitting leaves the transcript in scrollback.
- Resize is a repaint, not an investigation. We never ask the terminal where our rows went; we
  redraw the visible region from the model, and the terminal's own rewrap of inked history is
  its business.
- Scrolling is handed back: wheel-up in tmux enters tmux copy-mode (`-e` returns at bottom).
  We don't reimplement what the terminal does better.
- Jobs get the real pty (process group, raw mode, signals through the line discipline); their
  output is digested by a terminal emulator, not by regexes — and vim's alt screen erases
  itself from the record by its own semantics, for free.
- The alt screen is used for exactly what it exists for: a deliberately-entered, self-restoring
  modal surface (the transcript view) — never to own the main history.

The same principle governs optional capabilities: one is a safe enhancement when its fallback
is *absence* (image doesn't show, colors are duller, repaint flickers); it is a trap when its
fallback is *a different interaction design* (then everything ships twice). Scheme-based OSC 8
click handlers (custom URI scheme → kitty open_actions / OS scheme handler → helper → control
socket) were designed in full and rejected on this rule: Terminal.app and PuTTY lack OSC 8, so
it can never be the one scheme. OSC 8 survives cosmetically only (file paths as links; harmless
where unsupported), never load-bearing.

The compositor's write-once rewrite is this principle applied in the negative: the one place we
fought the terminal (bookkeeping rows it owned) generated the whole resize/CPR bug class, and
deleting the fight deleted the bugs.

## Write-once history

Scrolling happens only when the app prints, so the compositor controls what crosses into history — and under write-once that control is total: a row is painted into scrollback exactly once, in its block's displayed state at the moment it crosses the top edge, and is never addressed again. History reads exactly as the screen did. A block whose top rows have crossed stays fully toggleable while any of it is visible (the screen is redrawn from the model; the only cost is a short duplicated run in the buffer at the seam, which is invisible in the common scroll-back-a-few-screens case).

## Interaction

One scheme, stated once: **keyboard is the complete interface; SGR mouse is an accelerator onto the same actions; wheel scrolls history, clicks act on what's live.** The mouse never exposes an action the keyboard can't do.

- Keyboard: ctrl-O toggles the most recent tool call; alt-digit toggles the block wearing that digit (see Ambient numbering below).
- Clicks: SGR mouse (1000+1006), supported by every target terminal. A click resolves through the per-frame row→target map; valid by construction, since clicks only reach the app when the viewport is at the live position (scrolled up, tmux/the terminal owns the mouse).
- Wheel: inside tmux, delegate — on wheel-up run `tmux copy-mode -eu` on our own pane, handing the gesture back to tmux (this reproduces the branch tmux's own default wheel binding takes for non-mouse apps; `-e` exits copy-mode at bottom, resuming clicks). Outside tmux there is no portable "enter scrollback programmatically", so wheel opens the transcript view; shift+wheel reaches native scrollback in most terminals.

Ambient numbering (settled 2026-07-24; supersedes the "picker mode" design): the newest ≤10
*toggleable* visible blocks (height > 1) wear a digit, 0 = newest, read off the block and
pressed as alt-digit to toggle the wearer — no mode, no overlay, one keystroke. Numbers are
assigned newest-first among blocks with rows in the window, so renumbering happens only on
append (≤10 first-row repaints through the frame diff); a straddler whose first row has inked
shows no digit (still click-toggleable). Inking needs no special case and rule 2 stays pure:
numbering is a property of the *window*, so a row leaving the window de-numbers in the same
frame, and digits never reach history at all — no strip code, clean gutters in scrollback
(built and discovered mechanically; supersedes the "digits ink as displayed" ruling, whose
point was refusing a strip mechanism — none exists). To keep gutter
width constant under renumber churn (content renders at cols - gutter_width), all gutters are
3 chars + space, shaped `x\dx`: the type glyph flanks a middle cell that rests as the glyph
(`»»»`) and carries the digit when numbered (`»4»`). Glyph per type (strictly 1-cell,
unambiguous width): code in `»»»` bold, prompt in `›››` bold, tool use `≡≡≡` dim, code result
`«««`, prompt response `‹‹‹`, continuation `···` dim (never numbered), errors are the result
glyph in red. Exact glyphs to be eyeballed live before freezing.


Click hit-testing rides Rich's own machinery (the mechanism Textual uses): `Style.meta` carries arbitrary data on rendered Segments and is never emitted in ANSI. The block *chrome* (gutter marker, disclosure header — composed by the compositor around the content) gets `Style(meta={'toggle': block_id})`; content renderers never know about clicking. The compositor holds the frame it just painted (it rendered those rows anyway); a click maps row → target via the per-frame map, then col → segment by accumulating `cell_len`, then reads meta and dispatches the same action the keyboard would. Zone rules fall out: only the current frame hit-tests, and inked history carries nothing to strip since meta never reached the terminal. Hit-testing and emission share one width accounting, so only terminal-vs-Rich width disagreement (emoji, ambiguous-width) can drift a click by a cell — mitigated by making targets line-granular (whole header line toggles: robust and a bigger target). Tail and transient rows carry actions the same way: `Style(meta={'act': token})` dispatches to the `on_act` hook (the status-line mode button, picker rows). Segment-granular targets are reserved for rare multi-action lines; in-prose links use cosmetic OSC 8. The transcript view reuses all of this against its own viewport map.

## Transcript mode

An alt-screen, deliberately-entered live projection of the block model — enter (ctrl-T), browse, leave; main screen untouched. The organizing principle: the main screen has no selection concept; the transcript view is where a **block cursor** exists, so its uses are exactly the operations that address a block as an object. Built: navigation and toggle-in-place (committed history becomes toggleable here, lifting the main screen's inert-history limitation where it matters), smart-case regex search over the MODEL (so it matches inside collapsed content; landing on a match expands it, vim's fold-open rule), model-granularity copy (`y` via OSC 52: the block's stored `source` — no box-drawing, no wrap artifacts), and the shared composer (type or paste while browsing; Enter with content submits and leaves; two focus states, `i` entering compose explicitly since single-key ops and typing collide on the op characters). Being built now: `e` edit, shift-arrow exchange jump, follow mode (`less +F` style: pinned to the tail while a turn streams; navigation unpins, `G` re-pins). Considered and dropped (2026-07-24, re-proposable from need): filtered projections, session archaeology, export-a-block-range.

**Hide and pin (built; the first dialog-editing operation).** `skipped` and `pinned` are base-`Message`
fields in aidialog (solveit's literal metadata keys, so files mean the same thing in every host):
`skipped` hides a message from the AI -- `dlg2hist` drops it from every projection -- and `pinned` marks
it for whatever context policy a host runs (solveit's `limit_msgs` eviction keeps pinned; ipyai
will compact rather than evict -- deferred, llmsurgery-based). In ipyai, `h` on the transcript-view cursor block flips `skipped` for the block's whole
exchange -- one exchange (input + outputs, or ask + reply + tool calls) is ONE dialog message, so both
halves go together by construction. Every block printed for an exchange is stamped with its message id
at record time (`_stamp_exchange`); hidden exchanges render dim on both surfaces (`Block.dim`, a
self-invalidating property). Persistence: `%ipyai save`/`load` ride the meta_attrs round-trip for
free, and a toggle just saves the session dialog whole, like every other event. The ctx meter
reflects a hide only after the next turn: it
reports the API's measured usage, not an estimate.

Copy-mode and the transcript view divide labor: **copy-mode = the archive** (complete, searchable, inert), **transcript view = the live view** (compact, clickable, in-place). Same document, two projections.

## Compatibility

Targets: Terminal.app, iTerm2, ghostty, kitty, Windows Terminal, VTE (GNOME Terminal, Terminator), mintty, modern PuTTY (0.77+). Explicitly out: legacy conhost, old PuTTY.

- **Tier 0, everywhere, unprobed**: VT100 cursor ops, `ESC[J`, CPR (`ESC[6n`), SGR mouse, keyboard. The whole must-work feature set (repaint, ctrl-O, clicks) rides on this.
- **Tier 1, probed, absence-fallback**: synchronized output (mode 2026; probe `CSI ?2026$p`), truecolor (Rich auto-downgrades to 256), OSC 8 rendered as links.
- **Tier 2, delight**: kitty Unicode-placeholder images via kittytgp (kitty/ghostty). Placeholders are ordinary text, so they flow through all three zones and survive tmux; retransmitting the same image id updates pixels retroactively, even in scrollback.

The floor is set by Terminal.app (no truecolor, no OSC 8, no 2026) seconded by PuTTY (no OSC 8, no 2026). Detection philosophy is pt's, not terminfo's: assume VT100, probe with short timeouts, degrade silently.

Resize: adopt the new size and repaint from the model — the same move as any other frame. A width change re-renders every row at the new width; a height shrink may push rows across the top edge (inked, by the growth rule); the terminal's own rewrap of already-inked history is its business, not ours. No CPR is involved after startup, so there is nothing asynchronous to race: the SIGWINCH handler marks dirty and the next frame is simply correct. tmux zoom (`prefix-z`) is just a resize.

## Architecture: the pair

UI process + execution kernel subprocess, from day one (clikernel was in-proc once; painful in practice — settled by experience, not speculation). What the pair buys:

- The patch_stdout problem class is structurally impossible: user code's stdout lands kernel-side and arrives as output events; the UI tty has exactly one writer, the compositor.
- The transcript outlives the kernel: restart/crash is just an event block; model, history, scrollback intact. Interrupt is a signal to the kernel on a keybinding; the tail never freezes.
- One event model: kernel events, pty job bytes, input, resize — one asyncio loop reading fds, updating the model, repainting.

The wire is the Jupyter protocol — but NOT jupyter_console, which was always the painful layer in classic ipyai (the ZMQTerminalIPythonApp subclass, the pt coupling, the iopub tee); the wire is adopted, the framework is not. This was settled by experiment: a minimal custom stream protocol was built first and, one op at a time, turned out to be re-implementing Jupyter's vocabulary — `complete` (= complete_request), `check` (= is_complete_request), `inspect` (= inspect_request), then unsolicited events (= iopub), display-id updates (= update_display_data), and a live event loop between cells (= the kernel architecture itself: OS threads advance between serial requests but asyncio tasks freeze, and a background thread's print lands in the protocol pipe). Stack: **teleprint** (tty half, wire-agnostic) + **jupygate** + **jupyasyncclient** (the gateway hosts the kernels over HTTP/WS -- kernels outlive the app, enabling attach and warm resume -- and the async client demuxes replies, un-fencing completion/inspection *during* execution; conkernelclient played this role pre-gateway) + **ipymini** (the kernel; owning both ends retires the fighting-other-frameworks objection). Stolen from the conkernel experiment: `ModuleKernelManager` (kernelspec-free `python -m ipymini -f {connection_file}` launch) and the liveness-polled execute pattern (ZMQ death is silent: no EOF, the reply just never comes). Incremental iopub consumption — rendering messages to blocks as they arrive — is the one piece that is ours alone.

The tty seam: interactive commands (`!vim`) run UI-side where the tty lives; capture-only execution stays kernel-side; cwd crosses (queried from the kernel per job spawn). Kernel outputs are nbformat-shaped (stream/display_data/execute_result/error) — already the block model's input; we render typed.

## Compositor model (write-once; spiked and verified in examples/demo2.py)

The screen is a projection of the model; scrollback is a write-once record. Four rules (demo2's docstring is the spec):

1. **The screen always shows the last screenful of the rendered transcript** (window start `ws = max(0, len(rows) - avail)`), redrawn from the model on any change using absolute positioning — which never scrolls, so it never touches history.
2. **The only thing that reaches scrollback is a deliberate scroll**: when growth advances `ws`, the rows crossing the top edge are painted in their current state and pushed with real LFs at the bottom row. Whatever crossed is inked forever; we never address it again. This subsumes the old "progressive commit": a streaming block taller than the screen inks its top rows mid-stream in their at-that-moment form, with no special machinery.
3. **Everything visible is live**: clicks map row → target through the frame just painted (no history arithmetic). Folding changes the model; the next frame repaints. If content shrinks, `ws` slides back and already-inked rows are shown again in their current state ("policy 2", Jeremy's ruling: the buffer may keep a short duplicated run at the seam — accepted, because scrollback a few screens up always reads clean, and toggling on screen never harms anything already inked). Transients (completion menu, session-picker modal, spinner) are frame layout directly above the status row — they take free rows below the tail while the region is still filling the screen, cover the newest transcript rows once it has, and never ink. App exit is an inking event: quit paints one clean final frame (no transients) before handing the terminal back.
4. **Resize = read the new size and repaint** (a height shrink may advance `ws`, inking the overflow — the growth rule again). The single CPR in the program runs once at startup to learn the region origin, so launch behaves like any CLI: the shell's screen stays put and its rows scroll off only as real content needs the space.

Paint state is two integers — `top` (screen row of the region origin, worn down to 0 as scrolls absorb the shell's rows) and `ws` — plus the per-frame row→target click map. Carried over from the old model:

- **Rich is render-only.** Renderable -> segments -> ANSI; the compositor is the only animator; rich.live stays excluded.
- **No printed identity.** Nothing re-parses scrollback (the transcript view reads the model; resume reads the session file), so blocks print no id.
- **Content renders at `cols - gutter_width`** and every composed row is clipped to `cols`: one row is one screen row, never a wrap (see the width lesson under Development loop).
- Synchronized-output brackets (mode 2026, where probed) make each frame atomic.

Deleted with the old interior, and with them their whole bug class and test family: the block→line map over history, bottom-anchored `_park` arithmetic, commit/demotion bookkeeping, the archival restyle (and the bright/dim clickable-vs-history affordance — everything visible is clickable now, and history is above the edge), the async resize CPR resync (`on_resync`, `flush_input` expiry, provisional anchors), and line-granular tail diff-repaint as a special case (the frame engine repaints the window; row-level diffing is an optimization to add only if ever needed).

The one bug class transients ever produced is closed by a rule worth keeping: **a frame is just
rows -- the engine sizes the region to the whole frame** (window + over rows + tail), scrolling
for room like any growth (shell rows are already final, so nothing pre-paints) and never letting
over rows or the tail clip (a pathological transient taller than the screen clips itself; the
tail survives). The bug it killed: at launch the region is a few rows at the bottom of a full
screen, and a session picker taller than region+free rows painted its overflow off-frame --
looking exactly like a hang (loop idle in select(), keys answered blind). Transients were a
second row species with their own space arithmetic; now they participate in the one sizing
computation, and the class is gone (test_transients_grow_a_young_region pins it).

## Packaging

Two pieces; the boundary rule: **teleprint knows blocks, surfaces, and the terminal; it must never contain the nouns "model", "tool call", "session", or "IPython".**

- **teleprint** (this lib): borrow contract, input parser, compositor (write-once frame engine: ink-on-growth, transients, click map), buffer/tail editor, transcript view, widgets, the shell layer. Block concept minimal: identity, Rich-renderable forms, click targets, `source` (model-level text for search/copy). kittytgp optional. Headless-testable: feed escape sequences, assert the emulated screen (per-cell styles included, via `EmuTty.term.style`).
- **ipyai** (continues under its name; experimental, few users, so clean breaks are cheap): block types and gutters, fastllm model wiring, session files (dialog `.ipynb`s under `./.ipyai/sessions/`), magics, ctx management, mime renderers. It lives on branch `ipyaing`, landing as one clean-break merge deleting the pt/jupyter_console implementation; git main keeps classic ipyai daily-drivable until then.

Extraction rule: design as-if-extractable (clean imports, no host reach-arounds), extract on the second consumer. A terminal renderer for solveit dialogs is the likely second consumer and near-demo.

Amended for tail widgets (Jeremy): the second-consumer test is the wrong bar for input-adjacent UI -- "a completion menu is clearly what nearly anyone building this kind of app wants; there's nothing special about us." Widgets we find we need get pulled into teleprint immediately (`teleprint/widgets.py`: `CompletionMenu`, `Tooltip`, `Signature`), which also forces the decoupled API: the widgets know Buffers and renderables, never kernels or completion sources. The second-consumer rule still governs bigger extractions (e.g. the block model as a standalone).

Explicitly NO third module for "REPL machinery": the generic tail machinery (completion menu as tail growth, the ghost-text suggestion slot with stacked async providers, programmatic input-setting) belongs in teleprint, and the IPython-mode specifics (`.`/`;`/`!`/`%` dispatch, magics, lexer switching) stay app-side -- a middle framework module would violate the extraction rule with only one consumer. Input-setting needs no machinery at all: Buffer is pure data (`buf.text = ...; paint()`), which is how tab completion already inserts.

## Development loop

The binding constraint on model-assisted development here is that the agent must *see its work* fast, in text, without cramming the model's input. Screenshots of terminal windows fail every part of that. Two tiers:

- **Inner loop: an in-process headless terminal — pyghostty (our libghostty-vt bindings), the only backend.** All I/O goes through the borrow contract's interface, so tests swap the real tty for a harness: app output feeds the emulator (`vt_write`), input events (keys, SGR mouse clicks, paste) are seeded as bytes, and the harness answers `ESC[6n` queries from the emulator's cursor state (`CURSOR_X`/`CURSOR_Y` in the data API) — answering the one startup CPR (the region origin). Assertions read the emulated *screen grid* (formatter plain-text dumps, cell/row APIs) and scrollback, not raw ANSI: "row 4 is a dimmed header", "history contains the block once, expanded". Because the emulator IS Ghostty's production core, assertions vouch for a real target terminal: honest width accounting (grapheme clustering — exactly where click hit-testing could drift), reflow on resize (so resize repaint is inner-loop testable), kitty graphics state for kittytgp, even "is mouse tracking active" as a queryable bool. Scenario in, small text grid out — milliseconds, deterministic, pytest-able, and golden-file snapshots diff readably in an agent transcript. pyte was the original plan and was dropped (inactive since 2023, known wide-char bugs, no reflow); with a real-terminal-fidelity backend there is nothing to hedge, so no emulator-neutral backend layer exists — the harness API sits directly on pyghostty, which is a dev dependency only. Pure block-rendering tests need less: Rich's own capture/export_text, no emulator.

  As built, one detail improved on the plan above: the harness does not answer `ESC[6n` itself — libghostty-vt's effects system does. `GHOSTTY_TERMINAL_OPT_WRITE_PTY` installs a callback receiving every query response the terminal writes back to the pty (CPR, DECRQM, DA1...), so `EmuTty` just queues those bytes as readable input: the emulator's own production query-answering is what the compositor talks to. `teleprint.testing.EmuTty`'s surface — `write(data)` / `read()` (returns all pending bytes, `b''` when none) / `seed(data)` / `size` / `flush()` / context manager — is the borrow-contract tty interface: whatever owns the terminal at a given moment holds exactly this object, and `RealTty` has the same shape.
- **Outer loop: real tmux via fastmux.bg.** For exactly the behaviors the emulator can't vouch for: copy-mode entry from wheel delegation (send the SGR bytes, assert `pane_in_mode`), commit cleanliness in real scrollback (`capture-pane -p -S`), zoom/resize rewrap, passthrough. `capture-pane` returns plain text (or `-e` with escapes), so the agent reads real-terminal truth without images. Slower and stateful; used for the interop suite and spike verification, not the edit-test loop.

What neither tier covers — actual rendering in Terminal.app/kitty/ghostty (fonts, real wheel feel, image pixels) — stays a human smoke-test, rare by design.

Two suite lessons that keep paying: run pytest per-repo (combining two repos' test dirs moves
rootdir above the ini, silently disabling asyncio mode); and width-invariant assertions (every
painted row <= cols) catch the wrap-corruption class that screen-text assertions miss -- a real
terminal's autowrap shears the frame when a full-width-highlighted row plus gutter passes
`cols`, while the same corruption is invisible on a mostly-empty emulator screen.

Wedge diagnosis, no sudo needed: ipyai registers `faulthandler.register(SIGQUIT)` at startup, so
`C-\` into a stuck-looking app prints every thread's Python stack to the pane and continues --
capturable over tmux (`send_keys('%N', 'C-\\')` + `screen()`). For a process too far gone for
signal handlers, macOS `sample <pid>` gives native stacks unprivileged (enough to tell spinning
from parked); py-spy would give Python stacks uncooperatively but needs sudo on macOS, so it is
the last resort.

## Transcript persistence

The session IS its file: one dialog `.ipynb` per session under `./.ipyai/sessions/` (uuid names, a self-excluding `.gitignore`), written whole and atomically on every event (aidialog's `write_ipynb`) -- solveit's model exactly, so a session file opens in Jupyter or solveit as-is. Outputs persist client-side from the block model (richer than kernel-side repr flattening: exact streams, error structures, real PNG bytes) as verbatim **nbformat outputs** on each message. The kernel id, model, and think level ride in the notebook metadata: resume warm-attaches the stamped kernel when it is still alive. History navigation and ghost suggestions mine this directory's session files per mode; IPython's `history.sqlite` and the old `ipyai_*` tables are out of the picture entirely (superseded 2026-08: the gateway made kernel-side storage wrong -- a shared or remote kernel's db is not ours to write).

## ipyai assistant wiring

The session IS a Dialog (aidialog): `add_cell` appends code/note messages (bare-string cells
become markdown notes; outputs are verbatim nbformat), `run_prompt` appends a prompt message and
sets its output to the reply. Ctx = `dlg2hist`; `$`var``/`!`cmd`` refs both evaluate in the
kernel at turn time (! via the kernel's own `getoutput`: stdout+stderr, kernel cwd, `var_expand`
interpolation) and merge into one synthetic variables turn at the
top of history (aidialog's `vars_hist`). On backend failure the pending prompt message is removed
so a retry cannot double it (regression-tested).

One stream, two consumers: the AsyncChat stream tees into fastllm's AsyncStreamFormatter
(`fmt.outp` is the canonical stored/replayed form; `fmt2hist` round-trips it) and TurnRenderer
(blocks). The interrupt marker is solveit's `*[Response interrupted]*` (ai_fmt strips it on
replay). The ctx meter reads AsyncChat.last_req_use, not chat.use: per-request usage is the ctx
size, while chat.use sums a whole turn's tool-call steps and would overcount. Resume renders
stored replies via fmt2hist: text parts feed the md flow, tool parts replay as real collapsed
tool blocks.

ctrl-C during a turn cancels a dedicated stream-consumer TASK, not the run_prompt coroutine --
cancelling the caller would disrupt its own cleanup awaits (aclose, the kernel writeback).
`Assistant.cancel_turn` is the one entry; the partial is frozen via `TurnRenderer.stopped()` and
the turn records with `<system>user interrupted</system>`.

The kernel tool is named `py`, not `python`: codex rejects thread creation when a dynamic tool
takes a name "reserved for use by this model", and `python` is one. CUSTOM_TOOL_NAMES lists both
(safepyrun's extension seeds the legacy name for old kernels); only one is ever defined.

## Modes and routing

Three modes, one per interpreter the app fronts: **prompt** (the AI), **code** (the kernel),
**shell** (the persistent shell). `M-p`/`M-c`/`M-s` select a mode directly (normal usage);
clicking the status-bar mode segment cycles through the three; the composer mark shows the mode
(`››› ` / `»»» ` / `$$$ `), and an empty composer hints the two OTHER modes' M- keys (the
current mode is already on the status line; the hint answers "how do I get out"). Prefix
overrides work identically from every mode, one submission at
a time: `.` sends a prompt, `;` runs code, a leading `!` runs shell. A submission *starting*
with `!` goes to the shell whole (multiline is fine: it is one shell script); *embedded* `!`
(`x = !ls`) is ordinary code, keeping IPython's exact SList capture semantics kernel-side.
Records canonicalize independent of mode: shell submissions store as `!cmd` cells, prompts as
prompt messages -- so load, resume, ctx, and export never need to know the screen's mode.

The dispatch rule this settles: kernel-adjacent state rides the comm magic (`%ipyai`);
terminal/UI state gets keys and transients; shell state belongs to the shell itself. No fake
magics: `%fg`/`%jobs` died when job control moved into the persistent shell (below).

## Shell layer

**One persistent shell** (settled 2026-07-24; replaces the per-command jobs machinery). The
first shell submission lazily spawns the user's shell, interactive with job control, as session
leader of its own pty -- so `cd`, exports, venv activation, aliases, functions, and the jobs
table all persist across commands, and `fg`/`bg`/`jobs`/ctrl-Z are the shell's own builtins
(we rebuild none of it: work *with* the shell too). Injected prompt integration (bash:
`--rcfile` sourcing the user's rc first, then PROMPT_COMMAND + a guarded DEBUG trap for echo;
zsh: a `ZDOTDIR` shim sourcing the user's rc then owning `precmd`/`preexec` and clearing prompt
frameworks' hook arrays; other shells run as bash, fish parked) emits a private OSC carrying
`$?` and `$PWD` at each prompt: that marker is the
**command boundary** -- stripped by the relay, so it never reaches the terminal: NO terminal or tmux support is required (and an unknown private OSC would be ignored anyway). `$PWD` back-syncs to
the kernel after each command, so the two worlds agree about where you are (an OSC 133 upgrade
is a parked option: same mechanism, plus prompt-jump in integrated terminals).

Per-command flow: borrow (release -> raw), write the submission to the shell's pty, relay/tee
into a fresh per-command mirror, boundary marker ends the borrow (reanchor), and the mirror's
emulator-cleaned residue records as a `!cmd` cell with the exit code -- the same
record_block/add_cell shape as before (the mirror IS the cleaner: alt-screen apps erase
themselves from `contents()` by their own semantics -- vim's rule for free). ctrl-Z needs no
special handling: the shell stops the child and prints its prompt, which IS the boundary.
F2 rides the same borrow with `record=False`: composer text to a temp file, `$EDITOR` runs it
(resolved from the app's own env -- the pty shell's rc overrides env vars, see edit_buffer),
clean exit reloads the composer, nonzero (vim's `:cq`) abandons; nothing records.
Between borrows a background drain pumps the channel into a rolling mirror (bg job output, `[1]
Done` notices); at the next borrow anything drained prints first as a small block. `exit` kills
the shell: it respawns fresh on next use, with a note. Quit gate: C-D while a shell exists warns
once (the shell and any jobs in it close with the app); an immediate second C-D quits, and the
app's teardown deletes the owned terminal, taking its jobs with it -- exactly a terminal window
close. (The old `pgrep -P` children listing died with the local pty: the shell's process now
lives in the gateway, so there is no local child list to show.)

Teleprint side: `jobs.py` is gone (2026-08-04). The shell became a jupygate-hosted terminal
(ptymini pty, ws attach), owned by ipyai and deleted on exit, so the machinery moved to
`ipyai/shell.py`: `GateShell` carries the same rc/sentinel choreography (it is all in-band, so
it survived the transport change verbatim) and `relay` keeps `relay_shell`'s exact contract.
Teleprint keeps only what is genuinely terminal-UI: the borrow choreography
(`release()`/`reanchor()`/`record_block()`, RealTty `raw()`/`cooked()`) is exactly as before.
Known env facts, by design: the shell's environment is its own (spawned by the gateway, with the app's TERM/COLORTERM overlaid;
cwd flows shell -> kernel via the boundary marker, kernel `%cd` does NOT flow back to the
shell); kernel-side `!` (embedded forms) sees the kernel's environment.

## The %ipyai magic

A NORMAL kernel-side async line magic (`ipyai/magic.py`) talking to the host app over Jupyter
comms -- no socket, no app-side line interception (route() sends `%ipyai ...` to the kernel as
code in all modes). Every command is request/reply: the magic opens a comm, calls
`kernel.unlock()` (ipymini releases the shell queue so the host's comm_msg can be processed
mid-cell) and awaits the host's ack with a ~5s timeout -- so "no ipyai host attached" and
non-ipymini kernels fail loudly, and results return as ordinary cell output, flowing into ctx +
persistence for free. Host side: KernelSession.run routes comm messages to `App._on_comm`, which
dispatches `_ipyai_cmd` and acks by request id. Setters double as getters (`%ipyai model` with no
value shows the current one); settings are session-only, config.json is never written. The
kernel-bridge path is deliberately unwired: a `py`-tool-invoked `%ipyai` would need nested async
run_cell inside the kernel's running loop, which IPython can't do; it fails via the magic's own
timeout. Async line magics ride `fastcore.aio.enable_async_magics`, which ipymini enables in
MiniShell. One live note: Enter is gated while a cell runs (by design), so scripted drivers must
wait for the ack block before the next submit or input piles up in the composer.

## Parked / next

**Dialog editing (the next big milestone).** Editing is model-first; the glass is a log. The
durable main screen stays append-only (the thesis holds): edits never repaint history. All edits
-- cut/paste to move, edit a prompt's input or a reply's text, edit cell source, drop/pin for AI
ctx -- apply to the session Dialog (+ store), where aidialog's cut_msgs/paste_msgs/move_msgs/msg
editors already do the work. The transcript view re-renders from the model, so it is both the
editor surface and where edits show; ctx assembly, resume, and export read the model, so edits
flow everywhere downstream. Optional one-line note block on the main screen keeps the log honest.
`e` (settled 2026-07-24, being built): the block cursor is finer than the model, so targets map
honestly onto the ONE message an exchange is -- `e` on an input/shell block edits cell source, on
an ask block edits the prompt's content, on any reply-side block edits the WHOLE reply markdown
(tool calls and results live inside that blob; F2-under-the-view was considered and deferred -- a
job borrow beneath the alt screen loses the view when $EDITOR exits its own alt screen). Editing
state owns Enter (write back, stay in view) and Esc (cancel); no re-exec -- editing a tool result
rewrites what the AI remembers observing, kernel state untouched. Write-back updates message +
store row + block bodies (first block re-rendered, sibling reply blocks emptied -- scrollback
keeps the old text, that is the log). Per-piece reply editing via reply2dlg/dlg2reply is the
refinement if whole-reply grates. Structural ops work at MESSAGE granularity (cell+outputs,
prompt+reply move together); collapse and `y` stay block-level. Hide (`h`, solveit's key) and the
pin flag are BUILT (see Transcript mode); remaining approved-in-principle keys: `x` cut,
`p` paste-after, `*` pin-toggle UI.

**Retry / edit-and-resubmit (settled with Jeremy 2026-07-24, BUILT).** A retry state -- (target
message, kind) -- with two entrances: `alt-r` recalls the most recent exchange (prompt, code, or
shell; immediacy makes this serve both "bad reply" and "typo'd cell") and `E` in the transcript
view recalls any prompt (prompts only: reaching back is a conversation rewind; old code has no
honest re-run story). Both fill the composer, switch the mode to match, and show a `↻` status
cell. A kind-matched submit rewinds first -- the target and everything after it leave the dialog
(`remove_msgs`) and the block model (`Compositor.remove_block`; ink in scrollback stays, the log
is a log), and the save that follows writes the truncated dialog whole -- then the normal run
path appends the fresh turn. A
mismatched-kind submit or Esc disarms (truncating as a side effect of unrelated code would be a
footgun). No dim, no orphan bookkeeping: the window shows the model as it now stands (Jeremy's
call, overruling my dim-the-orphans lean -- dim means hidden, not deleted). `alt-up` keeps its
existing force-history binding; retry lives on `alt-r`. Record surgery vs time travel: `e` edits
in place with no re-run; `E`/`alt-r` rewind and re-run.

**Directory-scoped sessions (BUILT 2026-08, via session files; supersedes the sqlite-JOIN design
of 2026-07-23).** Sessions live where they happen: one dialog `.ipynb` per session under the
launch directory's `./.ipyai/sessions/`, so directory scoping is just the filesystem -- no shared
db, no cwd JOIN. Plain `ipyai` always starts fresh; bare `-r` opens the chooser over this
directory's files (digits choose, empty Enter picks the newest, `n` starts fresh), `-r PREFIX`
resumes one by filename. History and ghost suggestions mine the same files, mode-scoped (code
cells / prompts / shell cells per composer mode), so unrelated kernels' lines never leak in --
the junk-suggestion symptom died with the shared db. The chooser stays cwd-only and renders as an
in-flow `over` transient at startup: it evaporates without inking.

**Jobs work: superseded.** The per-command jobs machinery (and its planned %jobs/M-j additions)
was replaced wholesale by the persistent shell -- see "Shell layer". Being built now.

**Load and resume are different features (settled).** `%ipyai load` / `-l` is starter
templates: a curated file authored to be run — cells execute silently to rebuild kernel state
(errors surface loudly), the dialog becomes the AI context, nothing paints; the file itself is
the readable artifact, and `%ipyai` housekeeping cells are filtered on load and never recorded.
Session resume (`-r`) is recall, the classic codex/claude convention: paint the transcript, run
nothing — deliberately, since a transcript records what happened and was never curated for
re-execution (side effects, cost, a vanished world). Resumed state stays cold by design; the
two compose when needed: resume for the record, `%ipyai load` of a curated file for the state.

**Smaller items:**
- Style-level assertions work in the pytest harness: `EmuTty.term.style(x, y)` (pyghostty) reads
  per-cell attributes and colors; see test_toggle_in_place's dim-count assert for the idiom.
- Status line (settled 2026-07-24, being built): a single throbber cell at far left -- braille
  spinner while busy, plain space idle -- replaces the `model responding... (C-C stops)` text
  segment, making the line's content effectively constant-width (the truncation-policy question
  evaporates with the long live segment). C-C discoverability moves into the hint text.
  (Note-frontmatter var exposure from classic ipyai: considered and DROPPED 2026-07-24 -- the
  per-prompt `$`var``/`!`cmd`` references are the whole story for injecting live values.)
