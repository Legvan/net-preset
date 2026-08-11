"""Shared fixtures for the tests that need a real Tk root.

Creating a root fails here for two unrelated reasons, and the bare
`except tk.TclError: pytest.skip("no display available")` that each test file
used to carry reported both of them as the second one.

The first reason is the real one: a machine with no usable display cannot run
these tests and should skip them.

The second is local and transient. Every root re-reads Tcl's own library files
from the interpreter directory, and something on this machine intermittently
holds one of them: it surfaces as `couldn't read file ".../init.tcl": No error`,
or a `ttk/panedwindow.tcl` that is missing and then is not, in roughly one root
in twelve. Reported as an absent display it becomes a silent pass -- measured at
seven skips in twelve full runs, and it once hid a mutation that should have
failed the suite. Retrying clears it.

So: retry first, and if every attempt fails, still skip rather than fail -- but
say which of the two happened, because only one of them is worth worrying about.
"""

import time
import tkinter as tk
from collections.abc import Callable

import pytest

_ATTEMPTS = 8

# What a machine with genuinely nowhere to draw says. Anything else is the flake.
_MISSING_DISPLAY = (
    "no display name",
    "couldn't connect to display",
    "can't open display",
    "no $display environment variable",
)


def _is_missing_display(error: tk.TclError) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _MISSING_DISPLAY)


def new_root(factory: Callable[[], tk.Tk] = tk.Tk) -> tk.Tk:
    """A Tk root, retrying past the transient failure described above.

    *factory* builds it: `tk.Tk` for a bare root, or a subclass such as the
    application window, whose `__init__` creates an interpreter of its own and
    meets the same flake on the way.
    """
    error = None
    for attempt in range(_ATTEMPTS):
        try:
            return factory()
        except tk.TclError as failure:
            # Nowhere to draw is not a condition that improves on the next go,
            # and retrying it would put nearly two seconds of sleep in front of
            # every skipped test on a headless machine.
            if _is_missing_display(failure):
                pytest.skip(f"no display available: {failure}")
            error = failure
            time.sleep(0.05 * (attempt + 1))
    pytest.skip(
        f"Tk would not start in {_ATTEMPTS} attempts and this is NOT a missing "
        f"display, so this test lost its coverage: {error!r}"
    )


@pytest.fixture
def root():
    """A withdrawn Tk root, destroyed when the test ends."""
    window = new_root()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()
