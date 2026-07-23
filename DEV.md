# teleprint development notes

This file records the design as it stood before the first line of code, distilled from the founding research conversation (July 2026, starting from kittytgp and "what else can Unicode placeholders do?"). It states what we landed on, why, and what we rejected. The README will say what teleprint does; this says why it is shaped this way.

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

Within the main screen, three zones by mutability: **committed** (scrolled off-screen; immutable), **visible** (on-screen above the tail; repaintable in place via the block→line map, clickable), **tail**. Interactivity map: tail always interactive; visible blocks interactive until they scroll away; committed history inert (pager for live access); alt-screen surfaces interactive while up.

## Commit discipline

Scrolling happens only when the app prints, so the compositor controls what crosses into history. Before output pushes block N off-screen, repaint it into its **archival form**: whole blocks always (never torn repaint fragments), interactive styling stripped (bright disclosure markers mean clickable, dim means history — affordances stay honest), and disclosure state per policy. Archiving tool calls *expanded* makes tmux search cover everything while the live view stays compact; archive-collapsed is the terse alternative; per-block-type choice.

## Interaction

One scheme, stated once: **keyboard is the complete interface; SGR mouse is an accelerator onto the same actions; wheel scrolls history, clicks act on what's live.** The mouse never unlocks anything the keyboard can't do.

- Keyboard: ctrl-O toggles the most recent tool call; a picker mode numbers visible blocks for direct toggle; ctrl-L clears and reprints the recent transcript from the model at current width (the recovery gesture after resize/zoom kills the line map — familiar muscle memory, user-invoked because it duplicates lines in history).
- Clicks: SGR mouse (1000+1006), supported by every target terminal. A click resolves through the block→line map; valid by construction, since clicks only reach the app when the viewport is at the live position (scrolled up, tmux/the terminal owns the mouse).
- Wheel: inside tmux, delegate — on wheel-up run `tmux copy-mode -eu` on our own pane, handing the gesture back to tmux (this reproduces the branch tmux's own default wheel binding takes for non-mouse apps; `-e` exits copy-mode at bottom, resuming clicks). Outside tmux there is no portable "enter scrollback programmatically", so wheel opens the pager; shift+wheel reaches native scrollback in most terminals.

The governing principle, learned while rejecting OSC 8 click-through: a capability is a safe enhancement when its fallback is *absence* (image doesn't show, colors are duller, repaint flickers); it is a trap when its fallback is *a different interaction design* (then everything ships twice). Scheme-based OSC 8 click handlers (custom URI scheme → kitty open_actions / OS scheme handler → helper → control socket) were designed in full and rejected on this rule: Terminal.app and PuTTY lack OSC 8, so it can never be the one scheme. OSC 8 survives cosmetically only (file paths as links; harmless where unsupported), never load-bearing.

Click hit-testing rides Rich's own machinery (the mechanism Textual uses): `Style.meta` carries arbitrary data on rendered Segments and is never emitted in ANSI. The block *chrome* (gutter marker, disclosure header — composed by the compositor around the content) gets `Style(meta={'toggle': block_id})`; content renderers never know about clicking. The compositor retains the rendered segment lines for the visible zone (needed for repaint anyway); a click maps row → block/line via the line map, then col → segment by accumulating `cell_len`, then reads meta and dispatches the same action the keyboard would. Zone rules fall out: only visible blocks retain segments, so only visible blocks hit-test; commit discards them, and archival copies carry nothing to strip since meta never reached the terminal. Hit-testing and emission share one width accounting, so only terminal-vs-Rich width disagreement (emoji, ambiguous-width) can drift a click by a cell — mitigated by making targets line-granular (whole header line toggles: robust and a bigger target). Segment-granular targets are reserved for rare multi-action lines; in-prose links use cosmetic OSC 8. The pager reuses all of this against its own viewport map.

## The pager

(Name unsettled; "pager" wrongly suggests the primary reading surface. Candidates: transcript view, review mode.) An alt-screen, deliberately-entered live projection of the block model — enter, browse, leave; main screen untouched. The organizing principle: the main screen has no selection concept; the pager is where a **block cursor** exists, so its uses are exactly the operations that address a block as an object:

- Disclosure and navigation: toggle any block in place (including nested: a tool call's args/stdout/diff separately); jump by structure (next tool call, next prompt, first error); search *inside* collapsed content; filtered projections (errors only; prose only).
- Model-granularity copy and quote: copy a block's payload (not its rendering — no box-drawing, no wrap artifacts); quote a block into the composer; pull a past prompt up to edit-and-resubmit.
- Context editing (strategically the biggest for ipyai): the transcript model is approximately the LLM context window; drop/pin blocks, fork from block N, per-block token weight.
- Session archaeology: preview a stored session from the picker before resuming; browse stored sessions with the same renderer.
- Export: select a block range → dialog/notebook (block model ≈ nbformat outputs ≈ solveit Dialog; near-serialization, not conversion).
- Follow mode (`less +F` style) during long agent runs.

Copy-mode and the pager divide labor: **copy-mode = the archive** (complete, searchable, inert), **pager = the live view** (compact, clickable, in-place). Same document, two projections.

## Compatibility

Targets: Terminal.app, iTerm2, ghostty, kitty, Windows Terminal, VTE (GNOME Terminal, Terminator), mintty, modern PuTTY (0.77+). Explicitly out: legacy conhost, old PuTTY.

- **Tier 0, everywhere, unprobed**: VT100 cursor ops, `ESC[J`, CPR (`ESC[6n`), SGR mouse, keyboard. The whole must-work feature set (repaint, ctrl-O, clicks) rides on this.
- **Tier 1, probed, absence-fallback**: synchronized output (mode 2026; probe `CSI ?2026$p`), truecolor (Rich auto-downgrades to 256), OSC 8 rendered as links.
- **Tier 2, delight**: kitty Unicode-placeholder images via kittytgp (kitty/ghostty). Placeholders are ordinary text, so they flow through all three zones and survive tmux; retransmitting the same image id updates pixels retroactively, even in scrollback.

The floor is set by Terminal.app (no truecolor, no OSC 8, no 2026) seconded by PuTTY (no OSC 8, no 2026). Detection philosophy is pt's, not terminfo's: assume VT100, probe with short timeouts, degrade silently.

Resize: width changes rewrap scrollback (terminal-side), destroying the line map — demote every block to committed, repaint the tail, let ctrl-L recover. As built, the tail repaints IN PLACE over its old rows (the tail is chrome, not transcript: it must not duplicate into history on every zoom toggle) -- locatable because a rewrap keeps the cursor attached to its line, so the post-resize CPR row plus the known tail height finds the old tail top; exact unless a tail line itself rewrapped (rare: tails are short). Height-only resizes are recoverable (no rewrap; rebuild the map after CPR). tmux zoom (`prefix-z`) in a split layout is a width change: old blocks go inert, by design.

## Why not prompt_toolkit or Textual

Beyond the centering argument:

- **stdin has one owner.** CPR replies, keys, mouse, paste interleave unattributed on one fd. Our compositor needs CPR; pt's renderer also issues CPR and assumes it owns the read loop; the jobs relay and modals need the fd too. Embedding pt means demultiplexing stdin into someone else's framework assumptions (its `get_app()` context singleton, Application-owned loop) forever.
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

## Architecture: the pair

UI process + execution worker subprocess, from day one (clikernel was in-proc once; painful in practice — settled by experience, not speculation). The worker embeds execnb's CaptureShell (crib `_make_shell()` in clikernel/cli.py: the minimal incantation, class renamed for `in_notebook` checks, tracebacks suppressed since execnb captures structured errors). What the pair buys:

- The patch_stdout problem class is structurally impossible: user code's stdout lands in CaptureShell worker-side and arrives as output events; the UI tty has exactly one writer, the compositor.
- The transcript outlives the kernel: restart/crash is just an event block; model, history, scrollback intact. Interrupt = SIGINT to worker on a keybinding; the tail never freezes.
- One event model: worker events, pty job bytes, input, resize — one asyncio loop reading fds, updating the model, repainting.

CaptureShell's outputs are nbformat-shaped (stream/display_data/execute_result/error) — already the block model's input; clikernel flattens to text for LLM clients, we render typed. On the clikernel branch of the wire decision, the protocol needs a streaming variant (JSON-lines: output events as they happen, done marker with the result) rather than clikernel's body-at-end — extend clikernel.base, don't fork it. The tty seam: interactive commands (`!vim`) run UI-side where the tty and the ipythonng shepherd live; capture-only commands worker-side; var expansion crosses (worker expands the string, UI spawns the job).

Jobs layer (ipythonng jobs.py: pty.fork shepherd, own pgrp, tcsetpgrp, status pipe — the bash dance) eventually moves into teleprint: it is terminal-borrowing machinery and belongs with the borrow contract. Known gaps to fix on the way: background jobs stall when the pty buffer fills (fix: loop.add_reader on master_fd, print drained output above the tail); finished bg jobs are never reaped and their output never lands in history.

The wire between the pair is an open decision to make consciously, with two live options:

- **Jupyter protocol, kernel = ipymini.** ipyai already runs on it. It brings specified side-channels the tail will want: `complete_request`/`inspect_request` for tab popups, `stdin_request` for `input()`, a control channel so interrupt (and completion) work while a cell runs, streaming outputs natively, any-kernel interop, and multiple clients. With ipymini we own both ends, which removes most of the fighting-other-frameworks objection. Costs: jupyter_client dependency weight, message ceremony, and the iopub-tee awkwardness ipyai carries today.
- **clikernel stream protocol, worker = execnb.** Minimal, one fd, kernel-agnostic, proven daily in LLM-agent use. Costs: strictly serial request/response — completion, inspection, stdin, and streaming all have to be grown bespoke, and side requests during execution need a second channel.

Deciding criterion: how much of Jupyter's message vocabulary the tail actually needs. Idle-time completion is easy either way; completion and interrupt *during* execution, and `input()`, favor Jupyter. Decide no later than when the tail grows tab completion.

Decision 2026-07-23 (Jeremy): **start on the clikernel stream protocol with an execnb worker**; reconsider Jupyter only when specific triggers fire. Clarification that shaped it: **images never force the switch.** CaptureShell captures nbformat-shaped outputs in-process, so a matplotlib figure is a `display_data` with an `image/png` mime bundle on any wire; the streaming protocol carries the bundle as base64 and kittytgp renders it app-side (it is already ipyai's image renderer, and placeholder images are ordinary text to the compositor). The real Jupyter triggers are: `input()` (needs a worker→UI request mid-execution — crossing to bidirectional-during-execution bespoke means building a small Jupyter), completion/inspection while a cell runs (control channel), and multiple clients or foreign kernels. Interrupt is NOT a trigger: the worker is a process, SIGINT covers ctrl-C. When a trigger fires, the choice is "grow the stream protocol one more event" vs "adopt Jupyter to ipymini", judged by how much vocabulary is being re-invented.

Protocol topology, clarified after the fact: clikernel now has two worker protocols plus a bridge, and each surface has a first-class living client -- nothing is a subset-in-waiting. The **text protocol** (`cli.py` + `base.py`) is codex-native: codex's `write_stdin` primitive drives the worker directly, which is why the framing is in-band delimiters (a raw-stdin client gets no message boundaries from its transport) and why the `<stream-protocol>` banner is self-describing (the client's first read is the documentation). **MCP** is the bridge for harnesses without stdin primitives (Claude Code): the `base.py` supervisor exposes execute/restart/interrupt tools and relays over the text protocol. The **JSON-lines stream protocol** (`stream.py`) is UI-native (teleprint): every line each way is one JSON object, code as a `"code"` field (so multiline cells need no framing at all), outputs as typed nbformat events. The two workers share `_make_shell`, not the wire. A consolidation (MCP supervisor driving a stream worker, rendering events to text) was considered and shelved: it would move rendering across the wire boundary for no felt benefit, and each protocol's shape is load-bearing for its client. Planned protocol growth stays one `op` at a time: `check` (check_complete, for the Enter handler) next, `inspect` later, all idle-time request/response -- the Jupyter trigger boundary is unchanged.

REVERSED same day, by the decision's own criterion. Building the REPL surfaced, one op at a time, that we were re-implementing Jupyter's vocabulary: `complete` (= complete_request), planned `check` (= is_complete_request) and `inspect` (= inspect_request), then the background-output analysis demanded unsolicited events (= iopub), display-id updates (= update_display_data), and a live event loop between cells (= the kernel architecture itself; OS threads advance between our worker's requests but asyncio tasks freeze, and a background thread's print lands in the protocol pipe). The trigger fired: **ipyaing goes Jupyter protocol** -- but NOT jupyter_console, which was always the painful layer in ipyai (the ZMQTerminalIPythonApp subclass, the pt coupling, the iopub tee); the wire is adopted, the framework is not. Stack: **teleprint** (tty half, wire-agnostic, unchanged) + **conkernelclient** (published lib: concurrent-safe AsyncKernelClient with demuxed replies -- which also un-fences completion/inspection *during* execution) + **ipymini** (the kernel; owned both ends, so the fighting-frameworks objection stays retired). **conkernel** remains a standing experiment, not a dependency: ipyaing steals its `ModuleKernelManager` (kernelspec-free `python -m ipymini -f {connection_file}` launch), its dead-kernel liveness-poll execute pattern (ZMQ death is silent: no EOF, the reply just never comes), and folds its kernel-config idea into ipyai's config. What conkernel does NOT provide: incremental iopub consumption (it drains at completion; ipyaing renders messages to blocks as they arrive) -- that is the one genuinely new piece. `clikernel/stream.py`'s UI role ends here (deletion candidate at the ipyaing merge); clikernel's text protocol and MCP bridge continue unchanged for their own clients.

## Tail requirements (from ipyai as built)

Reading ipyai's current implementation (July 2026) surfaced two facts and a feature floor.

Fact one: **ipyai is already a pair.** `IPyAIApp` subclasses jupyter_console's `ZMQTerminalIPythonApp`; the kernel runs behind ZMQ, outputs are teed off iopub into a buffer for context building, and pt supplies the editor. So the pair architecture isn't new for ipyai; the open wire decision (see Architecture) is whether to stay on Jupyter or swap the heavy plumbing (KernelManager, iopub wrapping, jupyter_console shell subclass) for the minimal clikernel equivalent — either way, no second process is being introduced for the first time. kittytgp is likewise already wired as ipyai's image/png mime renderer; continuity, not new work.

Fact two: **streaming replies stress the compositor.** Today Rich `Live` renders the streaming markdown response. In teleprint terms a streaming reply is a live block growing above the tail — and it can grow past the screen. So the compositor must support **progressive commit**: the top of a still-growing block scrolls into history (archival form applied per commit discipline) while the visible remainder keeps repainting. This is a core compositor requirement, not an app nicety — and one Rich cannot meet: `rich.live`\'s vertical_overflow options are ellipsis (truncate with "...") or "visible", which mangles and repeats lines once content exceeds the screen, since Live cannot repaint above the viewport. ipyai experience: rich.live has been more annoyance than help. So Rich is render-only in teleprint (renderable → segments → ANSI); the compositor is the only animator, and rich.live is explicitly excluded.

The feature floor — what ipyai's pt layer does today, which teleprint's tail must be able to express (as lib primitives; the behaviors themselves stay app-side):

- **Ghost text with stacked async providers.** Grey-text suggestions come from two sources with priority: mode-aware history (separate sqlite-backed prompt vs code histories) overridden by AI inline completion (Alt-., a small fast `completion_model` at fixed low reasoning effort, given prefix/suffix plus recent-code context, ephemeral). Suggestions arrive async with a document-unchanged guard and invalidate-on-ready; accepted via Right/Ctrl-F/Ctrl-E. Lib implications: the Buffer carries a suggestion slot; providers are async and stackable; history is a provider interface, not a file.
- **Tab completion popup.** The menu renders as temporary tail growth — the tail is the one surface allowed to change height per frame — not as a float system.
- **Mode-dependent input rendering.** Syntax highlighting switches lexer by mode (Python vs plain in prompt mode), and the prompt indicator reflects mode (Alt-p toggle; the `.`/`;`/`!`/`%` dispatch itself is app-level line transformation).
- **Block-model actions bound in the tail.** Paste-from-last-response (Alt-Shift-W all blocks, Alt-Shift-1..9 nth, Alt-Shift-Up/Down cycle) are payload-copy operations over the block model — the pager's copy family, reachable without entering the pager.
- **Editor escape.** F2 opens the buffer in `$EDITOR` — a standard borrow: suspend tail, run editor on a temp file, restore. Alt-Up/Down history navigation.

Two tail details borrowed from the codex comparison rather than ipyai: a ghost-text *placeholder* (dim hint like `Implement {feature}` shown in an empty input — distinct from the suggestion stack above, which needs typed text), and a status bar that earns its keep (model, cwd, context-remaining — a permanent context display pairs naturally with the pager's per-block token weights for the why behind the number).

## Packaging

Three pieces; the boundary rule: **teleprint knows blocks, surfaces, and the terminal; it must never contain the nouns "LLM", "tool call", "session", or "IPython".**

- **teleprint** (this lib): borrow contract, input parser, compositor (block→line map, commit/archival), tail editor, pager, pickers, jobs/shepherd. Block concept minimal: identity, Rich-renderable forms (collapsed/expanded/archival), click targets. kittytgp optional. Headless-testable: feed escape sequences, assert writes.
- **clikernel**: worker protocol + supervisor, kernel-agnostic already; gains the streaming mode if it wins the wire decision.
- **ipyai** (continues under its name; experimental, few users, so the clean break is cheap now): block types, LLM backends, sqlite sessions, magics, context management, mime renderers, ipythonng's renderers and jobs semantics.

Extraction rule: design as-if-extractable (clean imports, no host reach-arounds), extract on the second consumer. A terminal renderer for solveit dialogs is the likely second consumer and near-demo. Naming lineage for the record: pypline rejected (PyPI conflict via normalization; and "pipeline" teaches the wrong center); papertape/tickertape/platen were the other candidates from the teletype family.

Amended for tail widgets (Jeremy, 2026-07-23): the second-consumer test is the wrong bar for input-adjacent UI -- "a completion menu is clearly what nearly anyone building this kind of app wants; there's nothing special about us." Widgets we find we need get pulled into teleprint immediately (`teleprint/widgets.py`: `CompletionMenu` -- a cycling menu over a Buffer span with readline common-prefix insertion and live cycle-into-buffer; `Tooltip` -- a clipped transient panel for signatures/docs), which also forces the decoupled API: the widgets know Buffers and renderables, never kernels or completion sources. The second-consumer rule still governs bigger extractions (e.g. the block model as a standalone).

The ipyai succession path (agreed 2026-07-23): branch `ipyaing` in the ipyai repo (created), rewriting on teleprint + `clikernel.stream` while main stays daily-drivable; lands as one clean-break merge deleting the pt/jupyter_console implementation. Explicitly NO fourth module for "REPL machinery": the generic tail machinery (completion menu as tail growth, the ghost-text suggestion slot with stacked async providers, programmatic input-setting) belongs in teleprint per the Tail-requirements floor, and the IPython-mode specifics (`.`/`;`/`!`/`%` dispatch, magics, lexer switching) stay app-side -- a middle framework module would violate the extraction rule with only one consumer. Input-setting needs no machinery at all: Buffer is pure data (`buf.text = ...; paint()`), which is how tab completion already inserts; the Alt-Shift paste-from-response bindings parse as-is (`ESC W` -> `alt+W`, `CSI 1;4A` -> `alt+shift+up`) and are payload reads over `Block.body`.

Execution order for the branch (agreed 2026-07-23 evening): **deletion spree first** -- shell.py/app.py (the jupyter_console/pt stratum) go entirely; cli.py's mains are replaced, `ipyai` now runs the new code (no `-classic` shim: git main preserves the old implementation for daily driving until the merge); the AI stratum (controller, backends, db, transforms, kittytgp renderer) stays in-tree, unwired, for later re-attachment. Then **checkpoint 0**: pyrepl replicated inside ipyai on the new machinery -- asyncio main (`loop.add_reader` for the tty + conkernelclient coroutines), incremental iopub-to-blocks, complete_request Tab, control-channel interrupt, stolen conkernel bits. Proving that end-to-end proves the whole new stack; checkpoint 1 (is_complete Enter, alt-enter, multiline) and the AI re-attachment are then additions to a working program.

Checkpoint 0 landed same evening (on branch `ipyaing`): app.py/shell.py/test_app_initialize/test_cli_e2e deleted; pyproject drops jupyter_console+ipykernel for teleprint+conkernelclient+ipymini; `ipyai/kernel.py` holds the conkernel steals (ModuleKernelManager, liveness-polled incremental `run`, `complete` via conkernelclient's generic `shell_request`); `ipyai/cli.py` is the asyncio App (injectable tty for the harness). End-to-end test green on first run: FakeTty + real ipymini, typed bytes through to result/stream/error blocks, Tab menu, cursor parked. The two live-LLM backend roundtrip tests fail environmentally (claude: user-level config gates the CLI; codex: model answered from its own context) -- not deletion fallout; verify against main.

Checkpoint 1 followed: `KernelSession.check` (is_complete_request), smart Enter (submit on complete/invalid, auto-indented continuation on incomplete, with a buffer-unchanged staleness guard around the round-trip), alt-enter as unconditional newline, and multiline prompt rendering (`>>> `/`... ` 4-cell prefixes; cursor row/col computed across newlines). Test: `def f():` grows an indented continuation under a `...` line, closes on blank-line Enter, `f()` yields 42; alt-enter inserts a newline into a complete expression. Known cp1 simplifications: status/menu tail lines assumed unwrapped; ctrl-a/e are buffer-wise, not line-wise, in multiline input.

Then the completion/inspection pass: Tab auto-selects the first match (cycling live into the buffer; shift+Tab backward, Enter accepts, any other key dismisses), and shift+Tab on a bare buffer shows the signature tooltip via `inspect_request` (`KernelSession.inspect`) -- the third predicted Jupyter op, arriving on schedule. Tail layout settled: dim status line ABOVE the prompt (a shell context-line, matching how prompts naturally migrate down a fresh screen -- the earlier reverse-video styling was what felt wrong, not the position), popups (menu/tooltip) below the prompt. Supporting compositor change: `set_tail` gained the cursor 3-form `(renderable_idx, line_within, cell_col)`, which stays correct however other tail renderables wrap -- retiring the status-lines-assumed-unwrapped caveat for good.

## Development loop

The binding constraint on LLM-assisted development here is that the agent must *see its work* fast, in text, without cramming context. Screenshots of terminal windows fail every part of that. Two tiers:

- **Inner loop: an in-process headless terminal — pyghostty (our libghostty-vt bindings), the only backend.** All I/O goes through the borrow contract's interface, so tests swap the real tty for a harness: app output feeds the emulator (`vt_write`), input events (keys, SGR mouse clicks, paste) are injected as bytes, and the harness answers `ESC[6n` queries from the emulator's cursor state (`CURSOR_X`/`CURSOR_Y` in the data API) — closing the CPR loop that the compositor depends on. Assertions read the emulated *screen grid* (formatter plain-text dumps, cell/row APIs) and scrollback, not raw ANSI: "row 4 is a dimmed header", "history contains the block once, expanded". Because the emulator IS Ghostty's production core, assertions vouch for a real target terminal: honest width accounting (grapheme clustering — exactly where click hit-testing could drift), reflow on resize (so resize-demotion is inner-loop testable), kitty graphics state for kittytgp, even "is mouse tracking active" as a queryable bool. Scenario in, small text grid out — milliseconds, deterministic, pytest-able, and golden-file snapshots diff readably in an agent transcript. pyte was the original plan and was dropped (inactive since 2023, known wide-char bugs, no reflow); with a real-terminal-fidelity backend there is nothing to hedge, so no emulator-neutral backend layer exists — the harness API sits directly on pyghostty, which is a dev dependency only. Pure block-rendering tests need less: Rich's own capture/export_text, no emulator.

  As built (2026-07-23), one detail improved on the plan above: the harness does not answer `ESC[6n` itself — libghostty-vt's effects system does. `GHOSTTY_TERMINAL_OPT_WRITE_PTY` installs a callback receiving every query response the terminal writes back to the pty (CPR, DECRQM, DA1...), so `FakeTty` just queues those bytes as readable input: the emulator's own production query-answering is what the compositor talks to. PROVISIONAL, for Jeremy's review: `teleprint.testing.FakeTty`'s surface — `write(data)` / `read()` (returns all pending bytes, `b''` when none) / `inject(data)` / `size` / `flush()` / context manager — is the first draft of the borrow-contract tty interface; whatever owns the terminal at a given moment holds exactly this object, and the real-tty implementation gets the same shape.
- **Outer loop: real tmux via bgtmux.** For exactly the behaviors the emulator can't vouch for: copy-mode entry from wheel delegation (send the SGR bytes, assert `pane_in_mode`), commit cleanliness in real scrollback (`capture-pane -p -S`), zoom/resize rewrap, passthrough. `capture-pane` returns plain text (or `-e` with escapes), so the agent reads real-terminal truth without images. Slower and stateful; used for the interop suite and spike verification, not the edit-test loop.

What neither tier covers — actual rendering in Terminal.app/kitty/ghostty (fonts, real wheel feel, image pixels) — stays a human smoke-test, rare by design.


## Staging

The full design has a strict-subset first ship: keyboard-only, history inert — block model, tail, repaint, ctrl-O and the picker, no pager, no mouse. The pager, wheel delegation, and hit-testing stack on top without changing what gets printed, so growing the subset into the full design is low-regret on exactly one condition: printed block identity is settled first (see Open questions), so early scrollback and the later pager agree. The subset's known cost — and part of why the pager exists at all — is that toggling an already-committed block can only append-and-duplicate: a fresh expanded copy prints at the bottom while the stale collapsed original remains above, and scrollback and search accumulate both forms.


## Open questions

- Stable block identity in printed form, so today's scrollback and a future pager agree (decide before anything ships output — the printed form is format, not presentation: colors and wrapping can change per release, but the identity marker is parsed by the future). One id, three carriers: streaming wire events append to a growing block by id, the model keys blocks by it, scrollback prints it — deciding once decides all three. The gutter is its natural printed home (cf. Claude Code and codex both using the gutter as the block-type channel). Leading candidate: session-scoped monotonic counter, dim-styled, on every block header line and every collapsed summary.
- ~~Completion UI in the tail~~: built in minimal form (menu line as tail growth, in pyrepl); graduating it into the lib plus the ghost-text suggestion slot is the next teleprint increment.
- Pager's real name.
- ~~The wire~~: decided (clikernel stream protocol; see Architecture for the Jupyter reopen-triggers).
- When jobs.py moves from ipythonng.

## First milestone

Originally planned as a few-hundred-line throwaway spike; reframed (Jeremy, 2026-07-23) once pyghostty existed: the harness is production-grade emulation, so code proven against it doesn't need re-proving, and a throwaway would be written twice for nothing. Instead the first milestone is the real modules, red-green against the harness from hour one: pyghostty history/resize; teleprint's testing harness, minimal block model, and compositor — block→line map, CPR origin tracking, tail repaint, toggle a visible block in place (height change, re-emit below), commit blocks whole in archival form, progressive commit of a still-growing block taller than the screen, resize-demotion, Style.meta click hit-testing end-to-end. YAGNI holds: smallest interfaces that pass the tests.

Built overnight 2026-07-23 (all of the above, 12 compositor/harness tests + 8 pyghostty tests green). As-built notes, each a small design fact worth keeping:

- **Geometry is bottom-anchored.** The compositor tracks one number, `_park` (terminal row of the last painted line); tracked line `j` sits at `_park - (total-1-j)`. No scroll counter, no absolute origin: scrolling only moves things relative to the top, never the bottom. CPR verifies or repairs `_park` directly, and `sync()` after every operation agreeing with the emulator's cursor is one of the standing tests.
- **The map's lifecycle does the zone model.** Lines that scroll above row 0 leave the tracked map; a block losing lines is marked committed — after an archival restyle in place if it is still whole (commit discipline), silently if already torn (that is progressive commit: a growing block's scrolled lines are final by construction, and `extend` keeps appending below). Clicks resolve only through tracked lines of un-committed blocks, so "visible interactive / history inert" needs no separate bookkeeping.
- **Archival is currently restyle-only** (same disclosure, chrome dimmed), so commit never changes height. The archive-expanded policy (DEV: per-block-type) is not yet implemented; when it is, commit can change height and rides the ordinary repaint path.
- **Tail diff-repaint is line-granular:** equal-height tail updates rewrite only lines whose rendered ANSI changed; height changes fall back to a suffix repaint.
- **Style-level assertions remain to be written** (e.g. "the archival header is actually dim"): needs `ghostty_grid_ref_style`/`GhosttyStyle` reading in pyghostty. Plain-text assertions cover structure and uniqueness today.
- **pyghostty grew the needed surface:** `contents()` (select_all + selection-format: scrollback plus screen, unwrap+trim copy semantics), `text()` redefined to the visible screen only (the formatter's NULL-selection path formats the *whole* screen including scrollback — that was a latent bug in first-light `text()`), `resize()` with reflow, `ref()` grid refs, and a documented workaround for cffi ABI mode's inability to pass unions by value (layout-identical struct twins + function-pointer casts; see `_ffi.py`). The harness's CPR loop uses libghostty's own `WRITE_PTY` effect, so query answers come from Ghostty's production code, not harness simulation.

What the harness genuinely can't answer stays with a human in a real terminal, via a small demo script driving the real compositor with fake blocks (run bare, in tmux, in Terminal.app): perceived repaint flicker without 2026, CPR races on a live tty under interleaved output, copy-mode scrollback cleanliness, wheel-delegation feel. Most likely failure remains CPR races — the demo exists to surface them early.

## Second milestone (agreed 2026-07-23): the REPL shape

Toward "bottom prompt, scrollable inputs/outputs" — each step harness-tested before the next:

1. **Input parser** (`keys.py`): bytes → events (keys incl. ctrl/alt/modifiers, bracketed paste, SGR mouse, CPR), cribbing Textual's XTermParser shape; incremental (split escapes and split UTF-8 across reads); bare-ESC ambiguity resolved by the caller via flush (timing lives in the loop, not the parser). The compositor's ad-hoc mouse/CPR regexes migrate into it.
2. **Buffer + tail-as-prompt**: pt's Buffer shape (text+cursor as pure data), readline-emacs subset. Known compositor gap to close: the terminal cursor must park at the buffer's edit point, not column 0 of the last tail line (harness-checkable against the emulator's cursor, wide chars included).
3. **Echo-REPL** (`examples/repl.py`): Enter → prompt block + fake output block; validates the whole feel with zero execution machinery. Apps stay out of the lib (packaging rule).
4. **Worker pair**: clikernel stream protocol + execnb CaptureShell (per the wire decision above); blocking execution first, streaming outputs via progressive commit after; mime bundles from day one (kittytgp renders app-side).
5. **Tab completion** (the tail-growth menu) — bespoke `complete` event on the stream at idle-time only, per the wire decision's triggers.

Built same day, all five steps (31 teleprint tests + 5 stream-protocol tests in clikernel green). As-built notes: `clikernel/stream.py` is an additive module (worker `main` + `StreamWorker` supervisor) reusing cli.py's `_make_shell`; outputs currently arrive at execution end, not intra-cell — the event-per-output wire shape means intra-cell streaming is a later worker-internal change, invisible to clients. Completion uses IPython's `Completer.complete(line_buffer=, cursor_pos=)`. Interrupt is process-level SIGINT, confirmed mid-`sleep`. `examples/repl.py` is the echo-REPL (no execution); `examples/pyrepl.py` is the real thing: persistent execnb worker, out/result/error blocks (error tracebacks via `Text.from_ansi`), tab completion with a menu line as tail growth, ctrl-C interrupt-or-clear, clicks during execution. Image outputs are detected but render as a placeholder note: kittytgp wiring is the next increment.

## Checkpoint 2 (agreed 2026-07-23 night): daily-drivable polish

All approved for immediate work, before cp3's AI re-attachment:

1. **IPython-esque signatures**: compact params line first (single wrapping line), ACTIVE param bold (client-side paren/comma parse decides which), doc excerpt dim below. Teleprint's Tooltip/Signature widget renders; ipyai derives (parse the `Signature:` line from the inspect blob now; teaching ipymini a structured `inspect_reply` mime is the better later move -- we own the kernel).
2. **History**: code cells are ALREADY recorded kernel-side by ipymini's real IPython HistoryManager into the real `history.sqlite` -- so decades of vanilla-ipython history are available on day one. Client reads it directly with **apsw** (not stdlib sqlite3: the reader races another process's writer thread, exactly where stdlib's implicit-transaction magic, coarse errors, and linked-sqlite variance bite; apsw = SQLite's real semantics, read-only URI opens, explicit busy timeout, bundled current amalgamation). AI/prompt entries go client-side to ipyai's own fastlite db as before (claude_prompts pattern). Teleprint sees only a history provider interface. Old `IPyAIHistory` logic (mode-aware, newest-first) is recoverable from git main; rewrite small against the provider interface. Alt-Up/Down history nav; up/down move within multiline input, IPython rule (up on top line = history).
3. **kittytgp images**: display_data image/png renders as Unicode placeholders (the founding feature home again).
4. **Input syntax highlighting**: pygments via Rich on the buffer text; prefixes/cursor math unchanged (styling only).
5. **Ghost text**: Buffer suggestion slot + stacked async providers (history provider only; the AI provider arrives with cp3).
6. **mdhtml -> Rich exporter**: mdhtml owns meaning, Rich owns paint, teleprint owns the screen. Replies parse to mdhtml trees so fenced code becomes addressable sub-blocks (Alt-Shift grabs by structure, not regex; per-block toggling inside replies; block-level context editing). Lives as an ipyai submodule for now, extraction to its own project later (mdhtml2term in spirit); kills mistletoe and never adopts rich.markdown.

Progress: items 1 (Signature widget in teleprint; ipyai/sig.py call_context/parse_sig_text/active_param, pure-tested; do_inspect shows the panel with active param bold, live-tested against print), 4 (ansi_dark highlighting in paint; gotcha: Text.rstrip eats whitespace-only continuation lines -- drop exactly the one newline highlight() appends), 2 and 5 (teleprint Buffer: up/down within multiline returning False at the edges so the host takes history, suggestion slot accepted by right/ctrl+e at end; ipyai/history.py: apsw read-only provider over IPython-schema db, newest-first dedupe, stash-based prev/next, first-line ghost suggestions; cli wiring: up/alt+up down/alt+down, ghost painted dim after cursor, nav reset on edit/submit -- all tested kernel-free via App(tty, history=...)) are DONE. Remaining: 3 (kittytgp images), 6 (mdhtml exporter).

CHECKPOINT 2 COMPLETE (same night): item 3 -- `App.show_image` splits kittytgp's `build_render_bytes` payload at the final ST: the APC transmit goes raw to the tty (cursor-neutral), the placeholder grid becomes an ordinary block via `Text.from_ansi` (repaints/commits/survives tmux like any text; verified by the placeholder char landing in the emulator grid). Item 6 -- `ipyai/mdrich.py`: `md_blocks(md)` walks mdhtml's JustHTML DOM to one Rich renderable per top-level block (inline styles, Syntax for fences with language, nested lists, quotes, tables, hr); extraction candidate. Suite hygiene lessons: run pytest per-repo (combining two repos' test dirs moves rootdir above the ini, silently disabling asyncio mode and breaking deselect paths); and `App(tty)` in tests must pass `history=None` -- the default loads the REAL 300MB history.sqlite, and ghost text from Jeremy's actual past code contaminated a screen assertion (History default is now a 'default' sentinel; None = off). Final: ipyai 59 passed (3 live-LLM tests deselected), teleprint 39 passed.

Matplotlib + kitty detection pass (after cp2): kittytgp grew the ergonomic API teleprint wanted -- `render_parts` (transmit bytes and placeholder text separately, fully headless with explicit cols/rows), `kitty_probe`/`kitty_supported` (a tiny `a=q` graphics query fenced by DA1: every terminal answers DA1, so a silent query after the DA1 reply means no support -- and the ghostty emulator ANSWERS the probe, so detection is honestly testable headless), and `kitty_env_hint` (kitty/ghostty env evidence, needed under tmux where terminals' probe replies are not routed to panes). `App.detect_kitty()` runs probe-then-hints at startup; without support, images print a `[image WxHpx ...]` note instead. Matplotlib verified end-to-end: `%matplotlib inline` then a figure through real ipymini lands as ONE placeholder block -- this is faithful Jupyter semantics (the classic figure-shows-twice: flush display_data plus execute_result repr; Jupyter renders both), so nothing to fix kernel-side; `on_out` suppresses only a byte-identical execute_result repeat per cell, so distinct images in one cell all render. Known parser gap recorded: keys.py doesn't consume APC/OSC/DCS strings, so probe replies are read raw before the parser sees input; teaching the parser string-sequences is a small future hardening. Suites: kittytgp 7, ipyai 61, teleprint 39, all green (kittytgp tests must pin `passthrough="none"`: auto-detection sees $TMUX in dev kernels).

Field bug, same night (Jeremy in tmux-on-ghostty got the fallback note): the env hint missed twice -- tmux OVERWRITES `TERM_PROGRAM` to 'tmux' (so that check can never work in tmux), and `GHOSTTY_RESOURCES_DIR` (which DOES survive into panes) wasn't actually checked. Fixed: the hint now checks the surviving id vars first, then asks tmux for `#{client_termname}` (authoritative: 'xterm-ghostty' -- reflects the currently attached client, so it also handles attaching from a different terminal). Verified live from the dev kernel's own tmux env, both paths.

## Decisions round, 2026-07-24 morning

- **Pager name: `transcript` (transcript mode).** Settled.
- **Transcript-mode live composer (new requirement, Jeremy)**: while scrolling history in transcript mode, the input stays stuck at the bottom rows -- type or paste into it while browsing; Enter with a non-empty buffer submits AND exits the mode (back to the live tail, block printed normally). Mechanically: the alt-screen surface renders viewport-over-model above + the same Buffer below, so the composer is shared state, not a copy; nav keys scroll, printable/paste route to the Buffer, exactly the on_key discipline the main screen already uses.
- **Parser hardened for control strings** (was open item 3): keys.py now consumes OSC/APC/DCS/PM/SOS whole (BEL or ST terminated, split-safe, 1MB runaway cap) as `Ctl(kind, data)` events -- keystrokes can never leak out of a terminal reply; compositor grew `on_ctl`. Costs the (genuinely ambiguous) alt+]/alt+P/etc. bindings. First consumer: **theme detection** -- App queries OSC 11 at startup (DA1-fenced probe, shared `_probe` helper), picks ansi_light/ansi_dark by background luminance, silence stays dark; `_hl(code, theme)` carries it everywhere code renders. Emulator answers OSC 11 once FakeTty configures a bg (`FakeTty(bg=(r,g,b))`), so the whole path tests headless. OSC 52 clipboard-write needs no reply handling (fire-and-forget; the terminal does the work) -- confirmed not a driver.
- **Code rendering unified** (Jeremy's screenshot: three styles): submitted `in` blocks now render through the same `_hl` as the input line -- colored, never bold; tracebacks keep IPython's own coloring; mdrich fences already use the same Syntax theme. Theme currently duplicated as a default in mdrich: unify via config at cp3.
- **Printed block identity: leaning HIDDEN-or-nothing, decision still Jeremy's.** Analysis: no planned feature actually re-parses scrollback (the pager reads the model; session resume reads the db), the picker already numbers visible blocks on demand for humans, so a permanently-printed id is chrome without a consumer. Options if identity must print: OSC 8 URI on the header (invisible, survives tmux>=3.4 history, stripped by Terminal.app -- but outside tmux there is no capture path anyway) vs dim `#n` (visible, survives as plain text everywhere, greppable, noisy). Foreclosed by printing nothing: post-hoc scrollback mining by shell_sage-ish tools.

## Transcript persistence design (agreed 2026-07-24, for cp3b)

Outputs persist client-side from the block model (richer than kernel-side repr flattening: exact streams, error structures, real PNG bytes), one row per cell, the value being the cell's outputs as a verbatim **nbformat outputs JSON array** (the iopub dicts we already consume -- zero schema invention, and export-to-ipynb or to a solveit Dialog is nearly a SELECT). Storage: **IPython's own history.sqlite, extended with our tables** (Jeremy: reuse the db, add tables -- old ipyai's claude_prompts already lived alongside the history/sessions tables), so `(session, line)` references are real same-db joins against the kernel's numbering. Rules: prefix new tables (`ipyai_*`) against future IPython schema collisions; NEVER flip the db's journal mode (WAL persists and it is the kernel's file first); writers use busy timeouts and short transactions (the kernel's HistorySavingThread is a concurrent writer); write at cell completion. Deliberate decisions still open at cp3b: prune/size policy for base64 PNGs, table naming settled: consistent `ipyai_` prefixes throughout, legacy names migrate (ipyai is barely used; backwards compat is explicitly not important -- Jeremy). Kernel-side `db_log_output`/ipythonng flattening stays OFF for ipyaing: that machinery answers vanilla terminal IPython's needs, not a block-model app's ('%history -o' in-kernel parity is the one thing forgone).

## Roadmap reorder (Jeremy, 2026-07-24): transcript mode pulls forward

cp4 folds into the cp2.9 wave -- exercise it right away against real REPL content (collapse-everything makes mini-IPython sessions rich enough to need it). Order: **cp2.9a** blocks presentation pass (content-first chrome enacting print-nothing identity, `>>> ` gutters + glyph language, preview-collapse `first line + … (+N)`, collapse-at-threshold streaming with live counter, stderr styling, errors-always-open); **cp2.9b** transcript mode v1 (alt-screen viewport re-rendering the block model at current width -- not captured scrollback text; block cursor; toggle-in-place works on committed history at last, the inert-history limitation lifting where it matters; wheel scrolls natively since alt-screen wheel events come to the app; live composer: the same Buffer pinned to the bottom rows, type/paste while browsing, Enter-if-nonempty submits AND exits, Esc leaves); then cp3a assistant, cp3b trimmings + persistence. AI-era refinements to transcript mode (structure-jump over reply sub-blocks, search-inside-collapsed) layer in at cp3 without rework.

## cp2.9 built (2026-07-24): content-first blocks + transcript mode

**2.9a.** Headers are gone: `Block(body, gutter, tag, collapse_at)` -- `body` is a list of parts
(streams append), `gutter` a (first, continuation) styled-Text pair carrying the toggle meta
(bright when clickable, dim when history/single-line: affordances honest), `tag` a free app
label (never rendered), no printed identity anywhere. Collapsed = first line + dim `… (+N lines)`.
`collapse_at` auto-folds: at birth for tall static blocks, and mid-stream at threshold crossing
(fully-visible by construction since the app caps it below screen height), after which the
summary count ticks live while the model grows; toggle re-expands everything accumulated.
`height` now means CONTENT lines (painted height differs when collapsed -- commit-restyle uses
painted). ipyai: `>>> `/`... ` green gutters on inputs (the transcript reads like a REPL),
`« ` colored gutters by kind, stderr styled red in-stream, errors always open, outputs/results
fold at ~half screen. Two extend() lessons: compute the crossing BEFORE appending to the body
(else the re-render double-counts), and the only-last-block-can-grow assert must admit a
born-empty stream block that has no tracked lines yet (newest id wins).

**2.9b.** `teleprint/transcript.py` TranscriptView: alt-screen (1049) full-redraw viewport over
the MODEL rendered at current width -- committed history toggleable here (`_block_lines` grew a
`live=` override; the main screen's inert-history limitation lifts in the view), block cursor as
a reversed gutter, wheel/click via a new `Compositor.on_mouse` mode hook (a mode may own the
mouse), and the live composer: `tail_fn` returns exactly what `set_tail` takes, so the SAME
Buffer renders at the view's bottom rows with the real cursor -- type/paste while browsing,
Enter-with-content submits and leaves, Esc/ctrl-T leaves, empty Enter toggles the cursor block.
Toggles made in the view are tracked and resynced onto still-live main-screen blocks at leave
(`Compositor.refresh_block`); committed blocks diverge by design (frozen prints, mutable model).
Resize while active just leaves the view (rewrap invalidates it; re-entry is one keystroke).
ipyai binds ctrl-T. Suites: teleprint 46, ipyai 66 (3 live-LLM deselected), all green.

## Next checkpoints, detailed (written pre-compaction 2026-07-24; this section drives the work)

**cp3a — the assistant lives.** Goal: `;`-prefixed prompts stream an AI reply as structured blocks; ipyaing replaces old ipyai daily.
1. *Mode routing, app-side.* on_enter decides BEFORE the kernel: prompt mode (config default, Alt-p toggles, `;` prefix forces) routes to run_prompt; code mode to the kernel as now. Port the SEMANTICS of core.py's `transform_prompt_mode`/`transform_dots` (`.`-prefix continuation etc.) as submit-time routing, not kernel transformers. Prompt-mode Enter always submits (English is never "incomplete"); input renders plain (skip `_hl`), prompt marker restyled to show mode.
2. *Controller port, minimal.* Old IPyAIController minus pt: context assembly now reads THE BLOCK MODEL (recent blocks replace the old iopub output_buffer tee), backends.py picks the backend; reply text lands kernel-side via a silent execute setting LAST_RESPONSE (enables user access + paste bindings).
3. *Streaming reply rendering -- the one genuinely new design.* Use `mdhtml.blocks(md)` top-level source spans incrementally: accumulate streamed markdown; when a span CLOSES (span count grows), render that completed top-level block via mdrich node rendering and print it as its own teleprint block (fences as Syntax, one style); the in-flight partial streams as a dim plain growing block that is REPLACED in place when its boundary closes (it is the last, still-live block: toggle-machinery repaint). At done, finalize the tail. Reply blocks get an 'ai' gutter kind; tall code fences get collapse_at.
4. *Interrupt:* ctrl-C during run_prompt cancels the backend task (kernel interrupt unchanged in code mode).

**cp3b — trimmings.**
1. *Tool calls:* kernel_bridge (kept, AsyncKernelClient-compatible) wires to KernelSession's client; tool-call events render as collapsed-by-default blocks (args first line, result appended via extend) -- the founding use case, on cp2.9 machinery.
2. *Paste bindings:* Alt-Shift-W/1-9/Up/Down insert fenced sub-blocks into the composer, addressed by mdhtml.blocks structure over the stored reply markdown (never regex).
3. *AI ghost text:* completion_model provider stacked over history (Alt-. explicit first), document-unchanged guard, via the Buffer suggestion slot.
4. *Sessions + persistence:* per the Transcript-persistence design above (ipyai_* tables in history.sqlite, apsw writer, nbformat outputs arrays at cell completion, PNG prune policy decided then); resume loads a past session's blocks into the model -- the transcript view browses old sessions for free.
5. *Config port:* backend/model/prompt_mode/code_theme from ipyai config; `code_theme` becomes the single source for _hl + mdrich + detect_theme override.

**cp5 — the jobs layer.** ipythonng's jobs.py (pty shepherd, pgrp, tcsetpgrp) migrates into teleprint beside the borrow contract: `!cmd` output becomes blocks natively (captured UI-side, not kernel-side), `!vim` is a full borrow; fix the two known gaps on the way (bg jobs stall when the pty buffer fills -- add_reader and drain above the tail; finished bg jobs never reaped into history). After: transcript-view search (including inside collapsed), model-granularity copy (OSC 52), context editing (drop/pin blocks), export-to-ipynb (nearly a SELECT once persistence lands).

**Parked/known:** OSC 11 theme detection under tmux unverified (fallback = dark; fix would be a tmux-side query); keys parser traded away alt+]/alt+P/etc. for control-string safety; teleprint README still scaffold; MANY repos carry uncommitted work awaiting Jeremy's review (teleprint, pyghostty, ipyai branch, clikernel, kittytgp, bgtmux, shell_sage, dotfiles).

## cp3 built (2026-07-24, while Jeremy was at lunch): the assistant lives, with trimmings

All of cp3a and cp3b landed in one pass, live-verified end to end in a real tmux pane (codex backend:
tool call into the live kernel, streamed reply blocks, persistence rows, resume). ipyai suite 77
(4 live-LLM roundtrips deselected), teleprint 48, all green. As-built notes:

**Semantics correction.** The cp3a goal line above said "`;`-prefixed prompts" -- a pre-compaction
summary error. The shipped semantics are classic ipyai's, confirmed from its README and ported exactly
in `assistant.route`: code mode sends `.`-prefixed input as a prompt (dot stripped); prompt mode
(config default, `-p`, alt-p) sends everything as a prompt except `;` (stripped, runs as code) and
`!`/`%` lines (pass to the kernel untouched). Prompt-mode Enter always submits; prompt input renders
plain with a magenta `ai> ` marker (4 cells, same cursor math); `.`-prefixed input in code mode also
renders plain.

**New modules, ipyai side.** `config.py` (IPython-free port of load_config/load_sysp; code_theme
default is now 'auto' = OSC 11 detection, an explicit theme skips it and feeds `_hl` + mdrich both --
the single-source rule). `bridge.py`: `ConBridge(KernelBridge)` whose `_exec` speaks ConKernelClient
(reply via its pending-queue reader with an explicit msg_id, iopub drained by that id so silent execs
never leak into the next cell's renderer), plus `set_vars` and `setup_tools` (defines the kernel-side
`py` tool, injects the importable ones). `assistant.py`: the controller port -- context assembled from
app-kept cell records (source + raw iopub pairs, the block model's data side), `$`var``/`!`cmd``
expansion via the bridge, ConversationSeed from in-memory turns, LAST_RESPONSE/_ai_last_prompt landed
kernel-side by one silent execute, `ai_complete` for ghost text. `reply.py`: the streaming design --
`ReplyRenderer` watches `mdhtml.blocks` spans over accumulated markdown, finalizes each span as its own
block when a later span appears (the first closure replaces the dim in-flight partial in place),
`Grower` re-renders a live block whole per update with fold-at-threshold; `TurnRenderer` dispatches
raw backend events (str deltas, thinking, tool_/command_) -- thinking streams dim-italic then folds,
tool calls print as `name(args)` lines that fold collapsed when the result arrives. `store.py`:
`ipyai_prompts`/`ipyai_cells`/`ipyai_sessions` in the kernel's history.sqlite via apsw (busy timeout,
single-statement transactions, journal mode untouched); cells persist as verbatim nbformat outputs
arrays at completion; `ipyai --sessions` lists, `ipyai -r N` resumes (display + conversation seed +
provider thread id -- kernel state is deliberately NOT rebuilt; noted as a semantic choice vs old
load_notebook's re-execution).

**Cancellation pattern.** ctrl-C during a turn cancels a dedicated stream-consumer TASK, not the
run_prompt coroutine -- cancelling the caller would poison its own cleanup awaits (aclose,
wait_provider_session_id, the kernel writeback). `Assistant.cancel_turn` is the one entry; the
partial is frozen via `TurnRenderer.stopped()` and the turn records with `<system>user interrupted</system>`.

**Tool name saga (live finding).** Codex now rejects thread creation when any dynamic tool is named
`python` ("reserved for use by this model" -- new behavior, per Jeremy from yesterday). The kernel
tool is therefore `py`, solveit's name. CUSTOM_TOOL_NAMES lists both `py` and legacy `python`
(safepyrun's extension seeds the latter for old kernels); only one is ever defined, so codex never
sees the reserved name from ipyaing. Jeremy's existing sysp.txt still says `python` in prose --
harmless (models see the real tool list) but worth a mention.

**Compositor width bug (the day's big lesson, found ONLY live).** Block content rendered at full
console width; the gutter then pushed rows past `cols`, and a real terminal's autowrap sheared every
following row off the line map -- stale fragments committed into scrollback on every code-block print.
Headless never showed it because `ansi_dark` highlight() emits no background so lines stay short;
Jeremy's real `monokai` config pads every highlighted line to full width. Diagnosed by teeing RealTty
writes to a byte log (94 visible cells on a 90-col pane). Fix in teleprint: `_content_lines` renders
body parts at `cols - gutter_width` (per-width Console cache), and `_fit` crops any composed line
(gutter + content + summary suffix) to `cols` -- one tracked line is one screen row, never a wrap.
Regression tests pin both (a monokai Syntax body; an over-wide collapsed summary). Harness lesson
recorded: the emulator wrapped identically but the corruption was invisible on a mostly-empty screen --
width-invariant assertions (every tracked line <= cols) are what catch this class, not screen text.

**Deliberately not ported yet:** the `%ipyai` runtime command surface (model/theme/think setters,
save/load .ipynb, reset); note-frontmatter var exposure (`exposed-vars`/`shell-cmds`) -- prompt and
history `$`var``/`!`cmd`` refs work; PNG prune policy for ipyai_cells (rows carry full base64);
per-mode history queries (History is the kernel's code history; prompts only enter nav via add_local).
Resume renders stored replies from formatter text, so tool calls replay in their flattened 🔧 form.

## fastllm/llmsurgery.hist adoption (assessed 2026-07-24, PROPOSAL -- decision Jeremy's)

Not using either yet. The api backends ride **lisette** (fastllm's predecessor; fastllm.chat is
"similar to lisette" by its own docstring), and the chat+code-history XML is hand-rolled twice:
`assistant.context()`'s `<context><code><output>` tags (ported from old core.py) and
backend_common's `seed_to_flat_history`/`seed_to_notebook_xml`. Verified empirically: lisette's
stored `<details class='tool-usage-details'>` reply format parses cleanly through fastllm's
`fmt2hist` -- same convention family, so existing stored responses round-trip.

Fit is excellent because the shapes already match:
- `Assistant.cells` (source + verbatim nbformat outputs) + turns IS a llmsurgery `Dialog` (code
  messages carry Jupyter outputs; prompt messages carry `ai_output`). Adopting Dialog as the session
  model replaces context()/seed_to_*/full_prompt with `dlg2hist` (which also upgrades output
  rendering: latex normalization, real image parts for vision models instead of '[image]'), makes
  save/load-.ipynb (the unported %ipyai feature) plain Dialog save/load, solveit-openable, and
  `reply2dlg` gives paste bindings and transcript explode structured reply access.
- fastllm replaces lisette near-mechanically in api_client (AsyncChat signature parity: model/sp/
  hist/ns/tools/cache) and adds vendor routing incl. a native `codex` vendor (chatgpt backend-api +
  CODEX_AUTH_TOKEN from the codex auth json -- could subsume CodexAPIBackend's litellm aliasing),
  usage/cost tracking (`c.use` -- the status-bar context-display material), search, prefill.
- Reply serialization becomes fastllm's: stream tees into `AsyncStreamFormatter` (canonical
  hist2fmt string, stored) + TurnRenderer (blocks), with TurnRenderer's event vocabulary shrinking
  to fastllm's item shapes (dict thinking/text, Part tool_use/tool_result). `fmt2hist`/`hist2fmt`
  are the lossless inverses. CLI backends (claude-cli, codex app-server) keep their transports but
  adopt fastllm Parts for events + hist2fmt for storage -- one convention end-to-end.
- Solveit is the worked example: `dlg2hist(msgs, aim_info)` -> `*hist, prompt, _` ->
  `AsyncChat(**chat_kw)(prompt, stream=True, max_steps=40)` -> `AsyncStreamFormatter.format_stream`
  appended live into the message output (aimsg.py run_ai/prepare_context/_astream_to_msg); its
  ghost machinery (run_ghost/fill_middle) is the superset of our Alt-. completion.

Not covered by adoption: provider-thread reuse (codex threads, claude-cli resume) stays orthogonal;
`aim_info` capability dicts must be supplied per backend/model; fastllm's codex.py module itself is
an empty stub (the vendor mapping is the working path).

## cp4: fastllm/llmsurgery adoption (Jeremy: GO, 2026-07-24 afternoon)

Scope expanded past the proposal above by the post-lunch discussion: fastllm's native `codex`
vendor (chatgpt backend-api completions + auth-json token) and fastllm-claude-code's registered
`claude_code` api make BOTH CLI backends moot, so the whole backend layer collapses to one
fastllm backend plus a model/vendor table (~930 lines deletable: codex_client 287,
claude_client 265, api_client 82, most of backend_common 262, backends 39; tooling.py's
provider-shape adapters go too -- fastllm takes tools/ns directly). Also cements core.py's
deletion (nothing in the new stack imports it).

Agreed order: (1) mechanical lisette->fastllm swap; (2) Dialog as the session model + dlg2hist
(deletes context()/seed_to_*/full_prompt XML assembly; save/load-.ipynb becomes Dialog save/load);
(3) reply serialization via AsyncStreamFormatter tee (hist2fmt canonical string stored,
TurnRenderer consumes fastllm item shapes); (4) CLI-backend deletions.

Constraints from Jeremy: fastllm-claude-code is NOT recently tested -- wire it but defer heavy
testing; he is checking it in parallel; tell him when it is the last untested item. Commits are
his (he commits as we go).

cp4 BUILT (same afternoon). All four steps landed as one pass; ipyai suite 41 passed (the 36-test
drop is the deleted legacy suite: no deselects remain -- the live-LLM roundtrip tests died with
their backends). Live-verified end to end in tmux on the codex vendor: routed prompt, streamed
reply, `py` tool call into the live kernel, kernel writeback, persistence, `--sessions`, resume.

As-built notes:
- **Deleted**: core.py (1013), codex_client (287), claude_client (265), backend_common (262),
  api_client (82), old backends registry, mcp_server.py + mcp_bridge.py + the `ipyai-mcp-bridge`
  console script (their only consumer was claude_client; fastllm-claude-code bridges tools with
  its own in-process MCP server), samples/ + tools/capture_samples.sh (stream-shape research for
  the deleted clients), and 9 test files. Deps: lisette + mcp out; fastllm, fastllm-claude-code,
  llmsurgery, apsw in.
- **backends.py is now a table**: BackendSpec carries AsyncChat routing kwargs. `codex` =
  vendor_name codex (auth from ~/.codex/auth.json); `claude` = api_name+vendor_name claude_code
  (lazy `import fastllm_claude_code.core` at chat construction -- the bare package import does
  NOT register the api, a trap found by checking); `claude-api` = anthropic + cache. Old names
  (codex-api, claude-cli) alias to the new, so existing config files resolve.
- **The session IS a Dialog** (llmsurgery): add_cell appends code/note messages (bare-string
  cells become markdown notes, nbformat outputs verbatim), run_prompt appends a prompt message
  and sets its output to the reply (the Message.output setter wraps prompt_output and clears the
  ai_output cache). Context = `dlg2hist`; var/shell `$`v``/`!`c`` expansions are extra str parts
  prepended to the final prompt parts. On backend failure the pending prompt message is removed
  so a retry cannot double it (regression-tested).
- **One stream, two consumers**: the AsyncChat stream tees into AsyncStreamFormatter
  (format_item per item -> fmt.outp is the canonical stored/replayed form, fmt2hist-round-trip
  tested) and TurnRenderer (blocks). TurnRenderer's vocabulary is now fastllm's: dicts with
  text/thinking deltas, Part tool_use/tool_result; the old kind-dict events and command_* family
  are gone. Interrupt marker is now solveit's `*[Response interrupted]*` (ai_fmt strips it on
  replay). chat.use lands on Assistant.last_use; the context meter (built 2026-07-24 evening) uses the
  new fastllm AsyncChat.last_req_use (one-line addition to _track: per-request usage, since chat.use sums
  a turn's tool-call steps and would overcount context) -- Assistant.ctx_usage = (prompt+completion tokens
  of the final request, aim_info max_input_tokens), painted as 'ctx 34.2k/256k (13%)' in the dim status
  line. Cost display skipped as moot under subscription auth (Jeremy). Jeremy's stale sysp.txt deleted;
  the current default regenerates on next launch.
- **Resume renders stored replies via fmt2hist**: text parts feed the md flow (the stored `- ⏳`
  progress markers are filtered for display), tool parts replay through TurnRenderer.event as
  real collapsed tool blocks -- better than the old flattened 🔧 form. Old-format stored replies
  fall back to feeding the raw text.
- **provider_session_id plumbing deleted end to end** (store schema, save_prompt, resume_state):
  the codex vendor is stateless completions and claude_code replays history per turn.
- Sysp/system prompt: DEFAULT_SYSTEM_PROMPT now describes `<code>`/`<markdown>` message context
  (dlg2hist's shape). Jeremy's existing sysp.txt persists with the old prose -- worth refreshing.

NOT yet tested: the `claude` backend live (fastllm-claude-code is wired and its api registration
verified, but no live turn has run -- Jeremy is checking that package in parallel; it is now THE
last untested item). Also untested live: claude-api (mechanical anthropic vendor, low risk).

## cp5 built (2026-07-23): the jobs layer

As built, teleprint side: `jobs.py` ports ipythonng's pty shepherd (fork on a fresh pty; shepherd
forks the command in its own pgrp, tcsetpgrp, status pipe relaying `pgid`/`stopped`; exit code
carried via os._exit with >128 signal encoding; finish_job reaps BEFORE closing the master), plus
three additions: winsize at spawn (shepherd-side ioctl, so the command never races it) and
`Job.resize` for SIGWINCH forwarding; `cwd=` (chdir in the shepherd); and the blocking copy loop
became asyncio `relay_job` (returns 'eof'|'stopped') with `watch_job` as the bg drain wrapper.
Compositor grew the borrow choreography: `release()` (commit everything, erase the tail, cursor to
col 0) / `reanchor()` (adopt size, relearn origin via CPR), and `record_block()` -- a model-only
block that paints nothing. RealTty grew `raw()`/`cooked()` (ISIG off during a borrow so ^C/^Z reach
the job through the pty line discipline; ECHO/OPOST off for byte-faithful relay); FakeTty no-ops.

ipyai side: `route()` gained a 'job' kind -- a SINGLE-LINE bare-`!` command (or `%fg`), in either
mode; multiline or embedded `!` (`x = !ls`) stays kernel-side code with exact SList semantics.
`App.run_job`: fg jobs release the compositor, remove the app's tty reader, go raw, and relay with
output tee'd into a mirror -- a headless pyghostty Terminal sized like the tty; at exit the
mirror's `contents()` (scrollback+screen) becomes a model-only residue block plus an
`assistant.add_cell` record, and nonzero exits print a small `exit N`/`signal N` error block (there
is no `$?` to consult). `cmd &` (we strip the `&`; sh never sees it) spawns + `watch_job`: the
continuous drain fixes ipythonng gap 1 (pty-buffer stall), and the reap callback fixes gap 2 --
residue prints as a NORMAL block (bg wrote nothing on glass) with a `[n] done` note. Reaps arriving
during a borrow defer until reanchor. `%fg [n]` is intercepted app-side: SIGCONT + re-borrow; a
still-RUNNING bg job can also be foregrounded (its watcher is cancelled and the fg relay takes
over). SIGWINCH during a borrow forwards to job + mirror only; the compositor adopts the size at
reanchor. cwd is queried from the kernel per spawn (`eval_expr`), fallback os.getcwd().

Design record -- the mirror IS the cleaner: no regex or heuristics digest job output; the emulator
does. The alt screen erases itself from `contents()` by its own semantics, which implements
Jeremy's vim rule for free (vim configured to keep the alt screen leaves its last screen in
history; default vim leaves nothing). Known env quirk, by design: a bare-`!` job runs in the UI
process's environment (cwd synced per spawn, env vars NOT), while kernel-side `!` sees the
kernel's. Left for later: quitting with live bg/stopped jobs orphans them (no shell-style "there
are stopped jobs" gate yet); bg mirrors keep their spawn size until a `%fg` resize; `%jobs` peek
(render a running job's mirror) parked.

## Since cp5 (2026-07-23): claude backend verified; async magics consolidated; %ipyai designed

Claude backend live-verified (real `ipyai -b claude` in tmux via fastmux.bg -- bgtmux is now
fastmux.bg): streamed replies, tool turns through the kernel bridge, cross-turn history, think
blocks, ctrl-C mid-stream leaves a healthy session. Both model call sites already stream
(`run_prompt` and completions both pass `stream=True`; the claude_code backend raises on
stream=False, so a future non-streaming path fails loudly). One issue found, belongs to
fastllm-claude-code not ipyai: tool-result continuation resumes with prompt "." and the model
visibly narrates interpreting it (core.py:39 anticipated this; make a proper tool-use prompt).

Async magics consolidated into `fastcore.aio`: `enable_async_magics(ip, fmt=None)` /
`disable_async_magics(ip)` -- one line transform rewriting `run_line_magic`/`run_cell_magic` calls
(top-level only; function bodies stay sync) into `await maybe_await(...)`, plus class-level
`run_cell_magic` wrap, per-shell sentinel so double-enable (e.g. ipyai user imports
ipykernel-helper) wraps exactly once, `fmt=` hook preserving ipykernel-helper's FT-to-HTML
layering. Migrated: ipykernel-helper (deleted its copy, passes fmt), ipythonng (deleted
`_await_magic`, 45 passed), ipymini (enabled in MiniShell, 83 passed incl. an e2e async line
magic through a real kernel). fastcore 03c_aio.ipynb has the unit story.

%ipyai design (agreed with Jeremy, building next): a NORMAL kernel-side line magic in
`ipyai/magic.py`, talking to the host app over jupyter comms -- no unix socket, no app-side
intercept. route() already sends `%ipyai ...` lines to the kernel as code in both modes.
Mechanics: magic opens `comm.create_comm(target_name='ipyai')` (comm_open/comm_msg ride iopub);
ALL commands are async request/reply -- `kernel.unlock()` then await the host's comm_msg ack with
~5s timeout, so "no ipyai host attached" fails loudly everywhere and results return as ordinary
cell output (flows into cell_outputs -> context + persistence for free). unlock costs nothing
here: a bare magic line is the whole cell, and the app pipelines nothing (Enter gated on busy).
Non-ipymini kernels: raise (no unlock -> no reply path; no silent fire-and-forget fallback).
Host side: KernelSession.run and kernel_bridge's drain currently drop non-OUTPUT_MSGS iopub;
both must route comm_open/comm_msg to one handler; replies go `kc.shell_request('comm_msg',
reply=False, comm_id=..., data=...)` (concexec tests prove mid-cell delivery under unlock).
Registration exec'd into the kernel at attach_assistant. Nothing special for completion/help:
magic-name tab-completion and `%ipyai?` come free from the kernel's own machinery (the app
routes Tab/shift-Tab through complete_request/inspect_request); subcommand completion would need
a custom complete_command hook -- skipped for now. Scope (the "fairly obvious" set): bare
`%ipyai` returns current settings + command list; `model`, `completion_model`, `think`,
`code_theme` (future blocks only -- bodies store highlighted Text), `prompt` (toggle prompt
mode), `sessions`. Settings are session-only (no config.json write; old behavior). Deferred,
each pending a decision: `reset` (new session row vs append?), `save`/`load` (PNG prune policy;
file-save vs sqlite store overlap), backend switching, `%%ipyai` cell magic (dead: prompt mode
covers it). ipyai/README.md is stale end to end (old process model, old backend names, old tool
list, `-l` flag); the %ipyai command set decides its rewrite.

%ipyai as built (2026-07-23), design above held with two deviations: (1) the bridge path is NOT
wired -- a `py`-tool-invoked `%ipyai` would need nested async run_cell inside the kernel's
running loop, which IPython can't do, so ConBridge stays untouched and that path fails via the
magic's own timeout; only KernelSession.run routes COMM_MSGS (to `App._on_comm` via the
`on_comm` attr). (2) Setters double as getters (`%ipyai model` shows the value). Pieces:
kernel-side `ipyai/magic.py` (async `ipyai()` magic, module-level comm + req-numbered pending
futures, PrettyString acks, UsageError for host-reported errors, RuntimeError for no-unlock and
timeout); host-side `App._on_comm` (comm_open tracks id, comm_msg dispatches `_ipyai_cmd`, ack
by req id) + `_ipyai_cmd` (settings text / setters on Assistant attrs / theme / prompt toggle /
`_sessions_text` shared with --sessions); registration via extension_manager.load_extension in
attach_assistant (try/except: kernels without ipyai just lack the magic). Tests
(ipyai tests/test_magic.py, 3): e2e settings/setter/getter/toggle/unknown-cmd, `x = %ipyai ...`
assignment capture, and no-host loud-failure (on_comm unwired, kernel-side _TIMEOUT shrunk).
Suite 51 passed. Live-verified in tmux: all commands incl. sessions against the real store,
`%ipyai?` docstring help and settings display both render as blocks (tall enough screens; small
ones auto-collapse -- that fold hid the settings block in the first test run, 16-row FakeTty).
One live note: Enter is gated while a cell runs (by design), so scripted drivers must wait for
the ack block before the next submit or input piles up in the composer.

## Decisions round, 2026-07-23 evening (all agreed with Jeremy)

**Transcript view becomes copy-mode** (tmux/less/vim-normal over blocks): one modal view, block
cursor, single-key ops. Search `/` `?` `n` `N`, motion `g` `G`. Search runs over the MODEL (block
sources/text), never the painted screen, so it finds matches inside collapsed blocks; landing in
one expands it (vim's fold-open-on-search rule). `y` = model-granularity copy: the block's stored
source (unwrapped, no gutters, no fold markers) to the system clipboard via OSC 52 (survives
ssh/tmux).

**Editing is model-first; the glass is a log.** The durable main screen stays append-only (the
thesis holds): edits never repaint history. All edits -- cut/paste to move, edit a prompt's input
or a reply's text, edit cell source, drop/pin for AI context -- apply to the session Dialog (+
store), where aidialog's cut_msgs/paste_msgs/move_msgs/msg editors already do the work. The
transcript view re-renders from the model, so it is both the editor surface and where edits show;
context assembly, resume, and export read the model, so edits flow everywhere downstream.
Optional one-line note block on the main screen keeps the log honest. `e` loads the block's
source into the shared composer; Enter writes back (no re-exec; re-run-after-edit maybe later).
Structural ops work at MESSAGE granularity (cell+outputs, prompt+reply move together); collapse
and `y` stay block-level. Proposed keys (approved in principle): `y` copy, `x` cut, `p`
paste-after, `e` edit, `d` drop-from-context, `*` pin. Dialog editing is the tricky part -- its
own later checkpoint.

**%ipyai decisions:** `reset` = fresh AI conversation AND a new session row (honest resume
boundaries), kernel untouched. `save`/`load` = EXPORT/IMPORT of the Dialog as .ipynb
(Jupyter/solveit-openable); sqlite stays the single resume source; export-to-ipynb IS `%ipyai
save`, not a separate feature. Image persistence: cap at 2M pixels total per image, downscale-
to-fit at persist time (display stays full size), image/jpeg handled alongside image/png.

**Backends dissolve into model strings** (solveit's shape: `codex/gpt-5.6-luna` etc., vendor as
first path segment, split('/',1)). One flat namespace: `%ipyai model claude_code/claude-sonnet-4-6`
is just a model change mid-session; the backend-switching question evaporates. The split belongs
in FASTLLM, not per-app: mk_client's resolution chain (explicit vendor -> base_url+key ->
infer_api_name) gains a vendor-prefix branch; bare names keep working; multi-slash model paths
fine (first segment only). claude_code promotes to a resolvable vendor (it's a transport, not an
HTTP vendor -- the resolution branch must consult the api registry, not just vendor_mapping; the
acomplete.py:153 special case is the seam). The prefix earns its keep:
claude_code/claude-sonnet-4-6 vs anthropic/claude-sonnet-4-6 is subscription-CLI vs API-key for
the SAME model name -- only the prefix can carry that. Then ipyai deletes: backends.py, config's
per-backend models table (collapses to two vendor/model strings), the -b flag (or alias sugar).
fastllm change needs care: it has other users. Solveit's own split-and-pass can delete later.

**Also:** fastllm-claude-code "." continuation issue: FIXED by Jeremy (the note above is stale).
teleprint README: move Thesis/Surfaces/Why-not sections out of this file via exhash cross-file
`m` (verbatim, no drift) plus connective tissue. ipyai README: full rewrite, now unblocked.
Jobs-layer leftovers (quit-with-live-jobs gate, %jobs peek, bg mirror resize) confirmed wanted.
Package split (Jeremy, 2026-07-23): llmsurgery's dialog layer (dialog/hist/dlgskill/ipynb) is now
the `aidialog` package; llmsurgery keeps transcript surgery (ant/antskill/compact/oai). ipyai's
runtime imports (`Dialog`, `prompt_output`, `dlg2hist`) already come from aidialog, and the
dialog-editing checkpoint builds on aidialog. Earlier "llmsurgery" mentions in this file are
historical records: read them as the dialog layer, i.e. today's aidialog.

## Proposed next checkpoints (PROPOSAL, decision Jeremy's)

**cp6 -- copy-mode v1 (search + copy; no dialog editing).** The modal vocabulary lands with its
two easy verbs: `/` `?` `n` `N` `g` `G` over the block model with expand-on-land, `y` via OSC 52
(RealTty write; FakeTty asserts the escape). Establishes the key-dispatch structure the editing
verbs will later join.

**cp7 -- the flat model namespace + %ipyai round-out.** (a) fastllm: vendor-prefix parsing in
mk_client + claude_code as resolvable vendor, with tests, treading carefully (published users);
(b) ipyai deletion pass: backends.py gone, config collapses, -b to sugar; (c) `%ipyai reset`
(new session row), `%ipyai save`/`load` as Dialog export/import, `-l` startup load; (d) image
cap (2Mpx downscale, jpeg); (e) README pass: teleprint README assembled from DEV.md sections via
exhash moves, ipyai README rewritten. Dialog editing (cut/paste/edit/drop/pin) and the jobs-layer
leftovers stay parked for cp8+.
