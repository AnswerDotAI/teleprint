# teleprint

A terminal UI library for applications built around a transcript. Output prints into the terminal's own scrollback, visible blocks remain interactive, and a status bar and line editor update at the bottom. It is written for AI CLIs and REPLs; `ipyai` is the reference application.

A chat or REPL session produces a growing sequence of inputs, responses, tool calls, and results. Readers need to follow new output, expand a result, enter the next request, and look back through earlier work. teleprint models that sequence as blocks and manages the transition from interactive output on screen to a printed record in scrollback.

## The transcript model

teleprint starts at the shell's cursor position and preserves the existing screen. As output grows, rows scroll into the terminal's history. Native terminal or tmux search, selection, and copy continue to work there. Quitting leaves the printed transcript behind.

The interface has three parts:

- The visible transcript renders blocks from the current model. Blocks can stream, update, and collapse or expand while they remain in the active screen region.
- The live tail contains the status bar and input editor. Completion menus and other transient controls appear near it. These controls do not become transcript output.
- The transcript view uses the alternate screen to browse older blocks, search, expand, and copy them. Closing it restores the main screen. Other temporary views can use the same alternate-screen approach; an application records the resulting choice as a block when it belongs in the history.

Rows that enter native scrollback are written once. teleprint does not locate or repaint them later. A tool result can therefore remain interactive while visible and become ordinary terminal text as new output moves it off screen. A block that spans the top edge can still be toggled while part of it is visible. Repainting after a collapse can leave a short repeated section at the scrollback boundary; already printed rows stay unchanged.

The block model holds the current document. Scrollback records what was printed at the time. Editing or removing a block updates the model without rewriting that record. The transcript view renders the current model, including content hidden by collapsed blocks. Search can find that content and expand its block; copy uses the block's source text rather than wrapped terminal rows or box-drawing characters. Clipboard copy uses OSC 52.

## Why a transcript library

A transcript-oriented application needs output to grow beyond the viewport while its visible portion remains interactive. Merely placing a UI below the shell prompt does not provide that behavior. An inline region that keeps its content within the application's viewport cannot substitute for printing rows into native scrollback as they leave the screen.

[Textual](https://textual.textualize.io/guide/app/#run-inline) provides a widget-based application model, including inline mode. [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/full_screen_apps.html) provides line editing and full-screen application tools. teleprint specifically manages blocks that print through to scrollback, their visible interaction, and a separate live view of older content. The scrollback behavior is part of its contract, not an application-specific scrolling widget.

teleprint uses Rich to render block content on both the main screen and in the transcript view. The compositor owns repainting and input dispatch. Applications can use Rich renderables without introducing a second content format for historical views.

The AI CLI use case supplies concrete requirements: streamed responses, collapsible tool results, responsive input during execution, and foreground commands that temporarily need the terminal. teleprint supplies the terminal UI independently of the execution protocol. ipyai combines it with Jupyter kernels and an AI conversation model; those are application choices, not requirements of teleprint.

## Install and examples

```bash
pip install teleprint
```

From a source checkout, run the [echo REPL](examples/repl.py):

```bash
python examples/repl.py
```

It maintains an input line, prints submitted text and responses as blocks, and supports block toggling. [demo2.py](examples/demo2.py) demonstrates the compositor's scrollback and repaint behavior; [pyrepl.py](examples/pyrepl.py) connects a Python execution client.

The main components are:

- `Compositor`: prints and updates blocks, manages the live tail, and dispatches input.
- `Block`: stores Rich-renderable content, source text, gutters, and collapsed state.
- `Buffer` and the input parser: line editing plus key, mouse, paste, and terminal-response handling.
- `TranscriptView`: browsing, searching, toggling, and copying the block model on the alternate screen.
- Widgets for completion menus, tooltips, and signatures.
- `RealTty` and `EmuTty`: real-terminal I/O and a pyghostty-backed terminal for headless tests.

## Foreground commands and terminal ownership

One component owns terminal input and output at a time. When an editor or foreground command needs the terminal, the application calls `comp.release()`, hands over I/O, then awaits `comp.reanchor()` when the command finishes. The compositor resumes below the command's output. Earlier output remains in history.

`comp.record_block()` adds output that is already on the terminal to the block model without printing it a second time. The application owns process execution and output capture. In ipyai, shell processes and the emulator mirror used to capture their output belong to the shell integration; teleprint provides the terminal handoff operations.

## Background work and errors

Key handlers run synchronously in arrival order. Start slow work with `comp.spawn(coro)` to keep input responsive. What do you need back?

- **Nothing — it should just happen:** call `comp.spawn(coro)`. In an `on_key` handler, return the coroutine and the dispatcher spawns it for you.
- **A handle — you will cancel it or check on it:** keep `spawn`'s return value.

```python
def on_key(k):
    if k.name == 'tab': return complete()
    elif k.name == 'enter':
        state['run'] = comp.spawn(run_cell(buf.text), name='run')
    elif k.name == 'ctrl+c' and state['run'] is not None:
        state['run'].cancel()
```

`spawn` retains each task until completion. Set `on_task_error` to report uncaught failures through the UI instead of writing a traceback over the screen:

```python
comp.on_task_error = lambda e, t: comp.print_block(f'{t.get_name()} failed: {e!r}', gutter=ERR)
```

Cancellation does not call this hook. Without a hook, failures retain asyncio's default reporting. Use a task created outside `spawn` when its owner awaits it and handles the exception itself; routing that failure through the hook as well would report it twice.

## Signals and testing

`await comp.start()` installs signal handlers on the main thread; `comp.stop()` restores them. The handlers work as follows:

- **SIGWINCH:** calls `on_resize` when set, or resizes and repaints by default. An application with its own tail state should set `comp.on_resize = lambda: (comp.resize(), paint())`. During a terminal handoff, its resize handler can forward the new size to the foreground command instead of repainting over it.
- **SIGINT:** enters key dispatch as `Key('ctrl+c')`.
- **SIGTERM/SIGHUP:** restore the terminal before terminating with the default signal disposition.

Off the main thread, signal registration is skipped. `EmuTty` accepts seeded input and feeds output through a headless Ghostty terminal, including responses to terminal queries. Tests can inspect the resulting screen and scrollback without an interactive terminal.

The interface uses standard terminal cursor operations, mouse reporting, and bracketed paste. Applications should provide keyboard equivalents for mouse actions. Optional capabilities such as synchronized output improve presentation without changing the interaction model. See [DEV.md](DEV.md#compatibility) for compatibility targets and [the founding design notes](DESIGN.md) for parser, terminal-mode, and signal research. Historical proposals in those notes are not all part of the current library.

## Development

```bash
pip install -e .[dev]
pytest
```
