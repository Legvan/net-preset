r"""The application icon: where it is, and getting it onto a window.

Without this the window wears Tk's feather, which is the one thing on screen
that says "this is a script" before anything else has been read.

The file is in two different places depending on how the program was started,
and both have to work:

    frozen      packaging\net-preset.spec bundles assets\net-preset.ico as data,
                so the onefile bootloader unpacks it into the temporary
                directory it puts in sys._MEIPASS and the icon sits directly in
                there. There is no repository underneath a shipped executable.

    from source sys._MEIPASS does not exist, and the module is being imported
                from src\net_preset\, so the repository root is two directories
                up and the icon is in assets\ beneath it.

sys._MEIPASS is PyInstaller's own name and the private-looking underscore is
part of it; it is read through getattr because it is absent in every run that
is not frozen, which includes every test.

Nothing here is allowed to stop the program. An icon that cannot be found or
cannot be read costs the icon and nothing else -- the window opens with the
feather, the same posture the rest of the application takes towards a missing
settings file or an adapter that has gone away.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

__all__ = ["ICON_NAME", "apply_icon", "icon_path"]

ICON_NAME = "net-preset.ico"

# Where the icon sits under the repository root, which is also where
# packaging\make_icon.py writes it and where the spec picks it up from.
ASSETS = "assets"


def icon_path() -> Path | None:
    """The icon file, or None when it is not where this run expects it.

    None is an answer rather than a failure: it is what a build that forgot the
    datas entry looks like, and what a source tree with the asset deleted looks
    like, and neither is worth an exception raised out of a window's __init__.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle is not None:
        candidate = Path(bundle) / ICON_NAME
    else:
        # .../src/net_preset/resources.py -> .../src/net_preset -> .../src -> repo
        candidate = Path(__file__).resolve().parents[2] / ASSETS / ICON_NAME
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        # A path this process cannot even ask about -- a disconnected drive, a
        # name the filesystem refuses. Still not worth taking the window down.
        return None


def apply_icon(window: tk.Wm) -> bool:
    """Put the icon on *window* and on every window opened after it.

    The `default` option is the whole point of the call. `wm iconbitmap` on its
    own dresses one window; `wm iconbitmap -default` sets the icon Tk gives to
    every toplevel created afterwards, which is how `dialog.ProfileDialog` comes
    up wearing it without being told. On Windows it is also the option that
    reaches the title bar rather than only the icon Alt-Tab shows.

    Answers whether the icon landed, so a caller that wants to know can ask. The
    window itself does not: there is nothing useful to tell an operator about a
    missing decoration, and the status line above the buttons is reserved for
    things that happened to their network card.
    """
    path = icon_path()
    if path is None:
        return False
    try:
        window.iconbitmap(default=str(path))
    except tk.TclError:
        # What Tk raises for a path with no file at the end of it, which
        # `icon_path` has just looked for and found: the guard is against the
        # gap between the two, and against a file this process may not read.
        #
        # It is not a check on the contents. Measured: Tk takes a file of
        # nonsense named .ico without complaint and installs something blank
        # from it, so a True here means the call went through and not that the
        # operator can see a plug. What keeps the artwork honest is build.ps1,
        # which redraws the icon and compares it against the committed file
        # before anything is packaged.
        return False
    return True
