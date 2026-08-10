"""Relaunch the process under an administrator token when it does not hold one.

Changing IPv4 configuration needs administrator rights, and making that change is
the whole point of this application, so it asks once at launch rather than once
per apply. The frozen executable carries requireAdministrator in its manifest and
arrives already elevated; running from source has no manifest at all, which is
what this module is for.

It detects and relaunches, and decides nothing else: the caller gets a bool back
and chooses what to do with it.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from collections.abc import Sequence

__all__ = ["ensure_elevated", "is_elevated", "relaunch_elevated"]

_PACKAGE = "net_preset"
_RUNAS = "runas"
_SW_SHOWNORMAL = 1

# ShellExecuteW answers above 32 on success; at or below 32 the value is an error
# code, and 5 (SE_ERR_ACCESSDENIED) is what a cancelled UAC prompt returns.
_SHELL_EXECUTE_SUCCESS_FLOOR = 32


def is_elevated() -> bool:
    """Whether the process already holds an administrator token."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        # No shell32, no such export, or the call failed: treat it as not elevated
        # rather than letting a probe take the application down.
        return False


def _shell_execute(
    hwnd: int | None,
    verb: str,
    file: str,
    params: str,
    directory: str | None,
    show: int,
) -> int:
    """Thin wrapper around ShellExecuteW, kept separate so the tests can replace it."""
    return ctypes.windll.shell32.ShellExecuteW(hwnd, verb, file, params, directory, show)


def relaunch_elevated(argv: Sequence[str] | None = None) -> bool:
    """Ask Windows to start this program again with an administrator token.

    *argv* is the argument list to pass on, the current one when None. Returns True
    when the elevated copy started and the caller should exit, False when the prompt
    was refused or the call failed.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    # PyInstaller points sys.executable at the bundled exe, which takes the
    # program's own arguments; from source the same attribute is the interpreter,
    # which needs -m and the package name in front of them. list2cmdline applies
    # Windows' own quoting rules, so an argument containing a space survives.
    parts = arguments if getattr(sys, "frozen", False) else ["-m", _PACKAGE, *arguments]
    params = subprocess.list2cmdline(parts)

    result = _shell_execute(None, _RUNAS, sys.executable, params, None, _SW_SHOWNORMAL)
    return result > _SHELL_EXECUTE_SUCCESS_FLOOR


def ensure_elevated() -> bool:
    """Elevate if needed: True to carry on, False when the caller must exit.

    False means an elevated copy has taken over and this one is redundant.
    """
    if is_elevated():
        return True
    # A refused prompt is not a reason to disappear: the operator cancelled, and a
    # window that never opens looks like a crash. Carry on unelevated instead and
    # let the application say for itself that applying needs administrator rights.
    return not relaunch_elevated()
