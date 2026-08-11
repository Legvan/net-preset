import sys
import types
from pathlib import PureWindowsPath

import pytest

from net_preset import elevation
from net_preset.system import system_directory


def fake_ctypes(**exports):
    """A stand-in for elevation.ctypes whose windll.shell32 offers *exports*."""
    shell32 = types.SimpleNamespace(**exports)
    return types.SimpleNamespace(windll=types.SimpleNamespace(shell32=shell32))


@pytest.mark.skipif(sys.platform != "win32", reason="asks the Windows token")
def test_is_elevated_answers_a_boolean():
    assert isinstance(elevation.is_elevated(), bool)


def test_ensure_elevated_does_nothing_when_already_elevated(monkeypatch):
    monkeypatch.setattr(elevation, "is_elevated", lambda: True)
    monkeypatch.setattr(
        elevation, "relaunch_elevated", lambda *a, **k: pytest.fail("must not relaunch")
    )
    assert elevation.ensure_elevated() is True


def test_ensure_elevated_relaunches_and_asks_the_caller_to_exit(monkeypatch):
    monkeypatch.setattr(elevation, "is_elevated", lambda: False)
    monkeypatch.setattr(elevation, "relaunch_elevated", lambda *a, **k: True)
    assert elevation.ensure_elevated() is False


def test_a_refused_prompt_lets_the_caller_carry_on(monkeypatch):
    # The operator cancelled the UAC dialog. Keep running unelevated so the
    # window can still say why nothing can be applied.
    monkeypatch.setattr(elevation, "is_elevated", lambda: False)
    monkeypatch.setattr(elevation, "relaunch_elevated", lambda *a, **k: False)
    assert elevation.ensure_elevated() is True


def test_the_frozen_process_relaunches_itself_not_the_interpreter(monkeypatch):
    recorded = {}

    def fake_shell_execute(hwnd, verb, file, params, directory, show):
        recorded.update(verb=verb, file=file, params=params)
        return 42

    monkeypatch.setattr(elevation, "_shell_execute", fake_shell_execute)
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    monkeypatch.setattr(elevation.sys, "executable", r"C:\apps\net-preset.exe")
    assert elevation.relaunch_elevated([]) is True
    assert recorded["verb"] == "runas"
    assert recorded["file"] == r"C:\apps\net-preset.exe"
    assert recorded["params"] == ""


def test_running_from_source_relaunches_through_the_interpreter(monkeypatch):
    recorded = {}

    def fake_shell_execute(hwnd, verb, file, params, directory, show):
        recorded.update(file=file, params=params)
        return 42

    monkeypatch.setattr(elevation, "_shell_execute", fake_shell_execute)
    monkeypatch.delattr(elevation.sys, "frozen", raising=False)
    monkeypatch.setattr(elevation.sys, "executable", r"C:\Python\python.exe")
    assert elevation.relaunch_elevated(["--debug"]) is True
    assert recorded["file"] == r"C:\Python\python.exe"
    assert "net_preset" in recorded["params"]
    assert "--debug" in recorded["params"]


def test_a_refusal_is_reported_as_false(monkeypatch):
    # ShellExecuteW returns 5 (SE_ERR_ACCESSDENIED) when UAC is cancelled.
    monkeypatch.setattr(elevation, "_shell_execute", lambda *a: 5)
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    assert elevation.relaunch_elevated([]) is False


def test_is_elevated_answers_false_when_the_probe_itself_fails(monkeypatch):
    def explode():
        raise OSError("shell32 refused")

    monkeypatch.setattr(elevation, "ctypes", fake_ctypes(IsUserAnAdmin=explode))
    assert elevation.is_elevated() is False


def test_the_success_boundary_sits_just_above_thirty_two(monkeypatch):
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    # 32 is SE_ERR_DLLNOTFOUND, the highest of the error codes; success starts at 33.
    monkeypatch.setattr(elevation, "_shell_execute", lambda *a: 32)
    assert elevation.relaunch_elevated([]) is False
    monkeypatch.setattr(elevation, "_shell_execute", lambda *a: 33)
    assert elevation.relaunch_elevated([]) is True


def test_an_argument_containing_a_space_stays_one_argument(monkeypatch):
    recorded = {}

    def fake_shell_execute(hwnd, verb, file, params, directory, show):
        recorded.update(params=params)
        return 42

    monkeypatch.setattr(elevation, "_shell_execute", fake_shell_execute)
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    assert elevation.relaunch_elevated(["--config", r"C:\Program Files\net.json"]) is True
    assert recorded["params"] == '--config "C:\\Program Files\\net.json"'


def test_the_elevated_copy_is_asked_for_a_normal_visible_window(monkeypatch):
    recorded = {}

    def fake_shell_execute(hwnd, verb, file, params, directory, show):
        recorded.update(hwnd=hwnd, show=show)
        return 42

    monkeypatch.setattr(elevation, "_shell_execute", fake_shell_execute)
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    assert elevation.relaunch_elevated([]) is True
    # No owner window, and SW_SHOWNORMAL (1), because a hidden relaunch would look
    # to the operator exactly like the application refusing to start.
    assert (recorded["hwnd"], recorded["show"]) == (None, 1)


def test_the_elevated_copy_starts_in_the_system_directory(monkeypatch):
    recorded = {}

    def fake_shell_execute(hwnd, verb, file, params, directory, show):
        recorded.update(directory=directory)
        return 42

    monkeypatch.setattr(elevation, "_shell_execute", fake_shell_execute)
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    monkeypatch.setattr(elevation, "system_directory", lambda: r"C:\Windows\System32")
    assert elevation.relaunch_elevated([]) is True
    assert recorded["directory"] == r"C:\Windows\System32"


def test_the_elevated_copy_never_inherits_this_process_directory(monkeypatch):
    # A null lpDirectory hands the elevated copy whatever directory this process is
    # sitting in, and a portable copy of this program is started from wherever it was
    # dropped: a USB stick, a Downloads folder, a desktop, none of which need a token
    # to write to. The current directory is on the search path Windows uses for DLLs
    # and for programs named without one, so the copy that is about to hold the
    # administrator token must be given a directory that needs the token to write to.
    recorded = {}

    def fake_shell_execute(hwnd, verb, file, params, directory, show):
        recorded.update(directory=directory)
        return 42

    monkeypatch.setattr(elevation, "_shell_execute", fake_shell_execute)
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    assert elevation.relaunch_elevated([]) is True
    assert recorded["directory"] is not None
    assert PureWindowsPath(recorded["directory"]).is_absolute()
    assert recorded["directory"] == system_directory()


def test_the_wrapper_hands_shellexecutew_its_arguments_in_order(monkeypatch):
    recorded = []

    def fake_shellexecutew(*args):
        recorded.append(args)
        return 1234

    monkeypatch.setattr(elevation, "ctypes", fake_ctypes(ShellExecuteW=fake_shellexecutew))
    result = elevation._shell_execute(None, "runas", r"C:\apps\net-preset.exe", "-m x", None, 1)
    assert result == 1234
    assert recorded == [(None, "runas", r"C:\apps\net-preset.exe", "-m x", None, 1)]
