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
    # No guard around this, because there is nothing for one to catch. On the
    # Python this project requires, `Path.is_file` is `os.path.isfile`, which
    # swallows OSError and ValueError and answers False. Measured on 3.14, all
    # False and none of them raising: a drive that is not there
    # (Q:\gone\net-preset.ico), a host that does not answer
    # (\\nosuchhost\share\icon.ico), a name with a null in it, and a path past
    # the length limit. So the check is the guard.
    return candidate if candidate.is_file() else None


def apply_icon(window: tk.Wm) -> bool:
    """Put the icon on *window* and on every window opened after it.

    The `default` option is the whole point of the call, and what it does on
    Windows was measured rather than taken on trust. `wm iconbitmap` on its own
    puts an icon on the one window it is given; `wm iconbitmap -default` puts it
    on the Tk window class instead, so every toplevel made afterwards is drawn
    with it without owning one. That is how `dialog.ProfileDialog` comes up
    wearing the icon without being told about it.

    Call this before the window has been round the event loop -- which is where
    `app.Application.__init__` calls it -- and Tk keeps the icon until the
    window's frame exists and applies it then. Measured: that holds for the
    first root of a process, which is every run of the shipped program, and not
    for a second one, where the same call at the same moment is dropped. The
    fix is not to force the window through an update first: that maps it, and
    an empty window would appear on screen before it had been built.

    Answers whether the icon landed, so a caller that wants to know can ask. The
    window itself does not: there is nothing useful to tell an operator about a
    missing decoration, and the status line at the foot of the window is for
    things that happened to their card.
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
