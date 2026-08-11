import ctypes
import sys
import types
from pathlib import Path, PureWindowsPath

import pytest

from net_preset import system

REAL = r"C:\Windows\System32"
PLANTED = r"C:\Users\operator\Downloads"


def fake_ctypes(answer="", written=None, error=None):
    """A stand-in for system.ctypes whose GetSystemDirectoryW answers *answer*.

    *written* overrides the length it reports, which is how the API says the
    buffer was too small; *error* is raised instead of answering at all.
    """

    def get_system_directory(buffer, size):
        if error is not None:
            raise error
        buffer.value = answer
        return len(answer) if written is None else written

    kernel32 = types.SimpleNamespace(GetSystemDirectoryW=get_system_directory)
    return types.SimpleNamespace(
        create_unicode_buffer=ctypes.create_unicode_buffer,
        windll=types.SimpleNamespace(kernel32=kernel32),
    )


def test_the_directory_is_what_the_windows_api_answers(monkeypatch):
    monkeypatch.setattr(system, "ctypes", fake_ctypes(REAL))
    assert system.system_directory() == REAL


def test_the_environment_cannot_move_the_system_directory(monkeypatch):
    # %SystemRoot% and %windir% name the same directory and are the obvious way to
    # reach it, but they are environment variables: inherited from whatever started
    # this process, and settable by a standard user for their own session through
    # HKCU\Environment. Reading either of them would let an unprivileged account
    # choose which netsh runs with the administrator token, which is the whole
    # weakness this module exists to close.
    monkeypatch.setattr(system, "ctypes", fake_ctypes(REAL))
    for name in ("SystemRoot", "SYSTEMROOT", "windir", "WINDIR"):
        monkeypatch.setenv(name, PLANTED)
    assert system.system_directory() == REAL


def test_a_platform_with_no_windows_api_still_answers_an_absolute_path(monkeypatch):
    # ctypes.windll does not exist off Windows, and the tests import this module
    # there. Answering a bare name would be worse than answering nothing.
    monkeypatch.setattr(
        system, "ctypes", types.SimpleNamespace(create_unicode_buffer=ctypes.create_unicode_buffer)
    )
    assert PureWindowsPath(system.system_directory()).is_absolute()


@pytest.mark.parametrize(
    "error",
    [
        OSError("kernel32 refused"),
        ValueError("not a valid buffer"),
        ctypes.ArgumentError("wrong type for argument 1"),
        MemoryError(),
    ],
)
def test_no_refusal_from_the_api_escapes(monkeypatch, error):
    # OSError is not the only way a ctypes call fails: a bad conversion raises
    # ValueError or ctypes.ArgumentError, and the buffer allocation can raise
    # MemoryError. This runs while a profile is being applied, so an exception
    # escaping here would cost the apply rather than the value it was asked for.
    monkeypatch.setattr(system, "ctypes", fake_ctypes(error=error))
    assert PureWindowsPath(system.system_directory()).is_absolute()


def test_a_truncated_answer_is_refused(monkeypatch):
    # When the buffer is too small the API reports the size it needed instead of the
    # size it wrote, and the buffer holds a piece of a path. Half a directory is not
    # a directory, and taking it would put netsh somewhere that is neither.
    monkeypatch.setattr(system, "ctypes", fake_ctypes(r"C:\Win", written=system._MAX_PATH))
    answered = system.system_directory()
    assert answered != r"C:\Win"
    assert PureWindowsPath(answered).is_absolute()


def test_an_empty_answer_is_refused(monkeypatch):
    monkeypatch.setattr(system, "ctypes", fake_ctypes("", written=0))
    answered = system.system_directory()
    assert answered != ""
    assert PureWindowsPath(answered).is_absolute()


@pytest.mark.skipif(sys.platform != "win32", reason="asks the live Windows API")
def test_the_live_answer_holds_windows_own_programs():
    directory = Path(system.system_directory())
    assert directory.is_dir()
    # netsh is why this module exists. cmd.exe is there to say the directory really
    # is Windows' own rather than merely one that happens to exist.
    assert (directory / "netsh.exe").is_file()
    assert (directory / "cmd.exe").is_file()
