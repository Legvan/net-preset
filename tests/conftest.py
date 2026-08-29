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


# -- what Windows thinks a window's icon is ------------------------------------
#
# Tk answers `wm iconbitmap -default` by putting the icon on the TkTopLevel
# window *class* rather than on one window, which is exactly why a toplevel
# created afterwards comes up wearing it. So the class is where a default shows
# up, and a window with no icon of its own answers 0 to WM_GETICON.
#
# The consequence for tests is that the default is process-wide and outlives
# every root: once one test has applied one, every Tk window made afterwards has
# it. `clear_default_icon` is how a test says what it is starting from.
#
# ctypes and wintypes are imported inside the helpers rather than at the top of
# this file, because `from ctypes import wintypes` raises on a platform that is
# not Windows and this module is imported for every test in the suite.

_GA_ROOT = 2
_GCLP_HICONSM = -34
_WM_GETICON = 0x7F


def _frame(window) -> int:
    """The HWND Windows draws the title bar on.

    `winfo_id` answers the widget's own HWND, and a Tk toplevel is a child of
    the frame that carries the decorations, so this walks up to that frame.

    `update`, not `update_idletasks`: Tk does not hand an icon to Windows until
    the window's events have been through the loop, so a read taken any sooner
    reports what was true before the last call rather than after it.
    """
    import ctypes
    from ctypes import wintypes

    window.update()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    return user32.GetAncestor(wintypes.HWND(window.winfo_id()), _GA_ROOT)


def window_icons(window) -> tuple[int, int]:
    """The small icon on *window*'s window class, and the one *window* owns itself."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClassLongPtrW.restype = ctypes.c_void_p
    user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    frame = _frame(window)
    return (
        user32.GetClassLongPtrW(frame, _GCLP_HICONSM) or 0,
        user32.SendMessageW(frame, _WM_GETICON, 0, 0) or 0,
    )


def clear_default_icon(window) -> None:
    """Take the default icon off the Tk window class, for the rest of this process.

    An empty file name is how `wm iconbitmap -default` is told to stop having
    one; measured, it leaves the class icon at zero. Nothing puts Tk's own
    feather back afterwards, and nothing needs it to: no test asserts on the
    icon a window has when nobody asked for one.

    Both `update` calls are load-bearing, and were measured rather than added
    for luck. Without the first, a window that has been created but has not yet
    been round the event loop keeps its icon and the call does nothing at all;
    without the second, the change has not reached Windows by the time it is
    read back.
    """
    window.update()
    window.tk.call("wm", "iconbitmap", window._w, "-default", "")
    window.update()


# The application window, built the way the shipped program builds it: as the
# first Tk root of a process that has had none. That is not a detail, and it was
# measured rather than assumed. Tk installs a default icon through the main
# window's wrapper, and a root that has not yet been round the event loop has no
# wrapper yet, so Tk stores the icon and applies it when the wrapper appears.
# That store-for-later works once per process: on a *second* root, the same call
# made at the same moment is silently dropped, and the window keeps whatever the
# class already had. `Application.__init__` asks for the icon in its first three
# lines, which is the first case; every test in this suite is the second. So the
# only way to see what the operator sees is to start a process that has had no
# root yet.
#
# GetIconInfoExW is what tells the two apart without a control to compare
# against: an icon Tk loaded out of its own DLL names that DLL and the resource
# "tk", and one loaded from a file names neither.
_FIRST_ROOT_PROBE = r"""
import ctypes, sys, time, tkinter as tk
from ctypes import wintypes
from pathlib import Path

store = Path(sys.argv[1])
from net_preset import app as module

module.profiles_path = lambda: store / "profiles.json"
module.settings_path = lambda: store / "settings.json"
module.ethernet_adapters = lambda: []

window = None
for attempt in range(8):
    try:
        window = module.Application()
        break
    except tk.TclError:
        time.sleep(0.05 * (attempt + 1))
if window is None:
    print("FLAKE")
    raise SystemExit(3)
window.withdraw()
window.update()


class ICONINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
        ("wResID", wintypes.WORD),
        ("szModName", wintypes.WCHAR * 260),
        ("szResName", wintypes.WCHAR * 260),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
frame = user32.GetAncestor(wintypes.HWND(window.winfo_id()), 2)
handle = user32.GetClassLongPtrW(frame, -34) or 0

info = ICONINFOEXW()
info.cbSize = ctypes.sizeof(info)
if handle:
    user32.GetIconInfoExW(wintypes.HICON(handle), ctypes.byref(info))
print(handle)
print(info.szModName)
print(info.szResName)
window.destroy()
"""


def first_root_icon(store) -> tuple[int, str, str]:
    """(class icon, the module it came out of, the resource it was) for a fresh window.

    *store* is a directory the child writes its profiles and settings into, so
    the probe never reads or writes the operator's own.
    """
    import subprocess
    import sys

    finished = subprocess.run(
        [sys.executable, "-c", _FIRST_ROOT_PROBE, str(store)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if finished.returncode == 3:
        pytest.skip("Tk would not start in the probe process, so this test lost its coverage")
    assert finished.returncode == 0, f"the probe failed:\n{finished.stdout}\n{finished.stderr}"
    handle, module, resource = finished.stdout.splitlines()[:3]
    return int(handle), module, resource
