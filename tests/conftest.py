"Suite-wide ambient-state hygiene."
import pytest, signal
from teleprint.compositor import Compositor
from teleprint.testing import EmuTty

_SIGS = (signal.SIGWINCH, signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

@pytest.fixture(autouse=True)
def _restore_signal_dispositions():
    """Compositor tests register process-global signal handlers on function-scoped loops that then
    close; unrestored, the dead-loop dispositions leak across tests -- breaking ctrl-C suite aborts
    from the first compositor test onward. Ambient process state is conftest's job: snapshot, restore."""
    saved = {s: signal.getsignal(s) for s in _SIGS}
    yield
    for s, h in saved.items(): signal.signal(s, h)

@pytest.fixture
async def terminal():
    owned = []
    async def start(cols=40, rows=10, shell=''):
        tty = EmuTty(cols, rows)
        if shell: tty.write(shell)
        comp = Compositor(tty)
        owned.append((tty, comp))
        return tty, await comp.start()
    yield start
    for tty, comp in owned:
        comp.stop()
        tty.close()
