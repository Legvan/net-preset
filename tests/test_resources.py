"""The icon: the file itself, where the program finds it, and how it is applied."""

import struct
import sys
from pathlib import Path

import pytest
from conftest import clear_default_icon, window_icons

from net_preset.resources import ICON_NAME, apply_icon, icon_path

# Derived from this file rather than from the module under test, so the two
# agree about where the repository is only if the module is right.
REPO = Path(__file__).resolve().parents[1]
ICON = REPO / "assets" / ICON_NAME

windowed = pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows display")


class Recorder:
    """A stand-in for a window that remembers how iconbitmap was called."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self._error = error

    def iconbitmap(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))
        if self._error is not None:
            raise self._error


# -- finding the file ----------------------------------------------------------


def test_a_frozen_build_reads_the_icon_out_of_its_bundle(tmp_path, monkeypatch):
    bundled = tmp_path / ICON_NAME
    bundled.write_bytes(ICON.read_bytes())
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert icon_path() == bundled


def test_a_frozen_build_that_did_not_bundle_the_icon_answers_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert icon_path() is None


def test_a_frozen_build_ignores_the_repository_it_was_built_from(tmp_path, monkeypatch):
    """The bundle is the only place a frozen run looks.

    Without this, a resolver that fell back to the source tree would pass every
    test on this machine -- the repository is right here -- and ship an
    executable that finds nothing on the operator's.
    """
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert ICON.is_file()
    assert icon_path() is None


def test_running_from_source_reads_the_icon_out_of_the_repository(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert icon_path() == ICON


# -- putting it on a window ----------------------------------------------------


def test_the_icon_is_asked_for_as_the_default(monkeypatch):
    """`default` is the whole point: it is what later toplevels inherit.

    Without it the dialog would come up wearing Tk's feather beside a window
    that does not, which is worse than neither having one.
    """
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    window = Recorder()
    assert apply_icon(window) is True
    assert window.calls == [((), {"default": str(ICON)})]


def test_a_missing_icon_costs_the_icon_and_not_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    window = Recorder()
    assert apply_icon(window) is False
    assert window.calls == []


def test_a_window_that_refuses_the_icon_costs_the_icon_and_not_the_window(tmp_path, monkeypatch):
    """The icon was asked for, and Tk said no. That is a decoration, not a fault."""
    (tmp_path / ICON_NAME).write_bytes(b"this is not an icon")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    import tkinter as tk

    window = Recorder(error=tk.TclError("error reading icon"))
    assert apply_icon(window) is False
    assert window.calls == [((), {"default": str(tmp_path / ICON_NAME)})]


@windowed
def test_a_file_that_went_away_after_the_check_is_what_the_guard_catches(
    root, tmp_path, monkeypatch
):
    """The other half of the test above: that the error it stubs is a real one.

    A stubbed TclError proves the handler catches what it is given; only Tk can
    say that this is what Tk raises, and the path that raises it is one with no
    file at the end of it -- which `icon_path` has just looked for and found. So
    this is the race between the two, and it is the reason the call is guarded
    at all rather than the check being trusted.
    """
    import tkinter as tk

    from net_preset import resources

    gone = tmp_path / ICON_NAME
    with pytest.raises(tk.TclError):
        root.iconbitmap(default=str(gone))

    monkeypatch.setattr(resources, "icon_path", lambda: gone)
    assert apply_icon(root) is False
    assert root.winfo_exists()


# -- what Windows does with it -------------------------------------------------


@windowed
def test_windows_takes_the_icon_and_hands_it_to_a_window_opened_later(root, monkeypatch):
    """The claim the dialog depends on, checked against Windows rather than assumed.

    Cleared first, because the default lives on the window class and so is
    shared by every Tk window in this process: without that, this would pass on
    whatever an earlier test left behind.
    """
    import tkinter as tk

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    clear_default_icon(root)
    assert window_icons(root)[0] == 0

    assert apply_icon(root) is True
    applied, _ = window_icons(root)
    assert applied != 0, "the icon never reached the window class"

    dialog = tk.Toplevel(root)
    try:
        dialog.update_idletasks()
        inherited, own = window_icons(dialog)
        assert inherited == applied
        assert own == 0, "a window that had to be told is not one that inherited"
    finally:
        dialog.destroy()


# -- the file the build ships --------------------------------------------------

_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
_PNG_FROM = 128
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _directory(raw: bytes) -> list[tuple[int, int, int]]:
    """Every entry as (pixel size, byte length, byte offset)."""
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert (reserved, kind) == (0, 1), "not an icon directory"
    entries = []
    for index in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack(
            "<BBBBHHII", raw[6 + 16 * index : 22 + 16 * index]
        )
        # A single byte cannot hold 256, so an icon directory writes zero.
        entries.append((width or 256, length, offset))
    return entries


def test_the_committed_icon_parses_as_an_icon_directory():
    """A binary asset in git, so this is the guard against one arriving mangled.

    Nothing else in the suite would notice a truncated commit, a checkout that
    translated line endings in it, or a merge that took half of one copy.
    """
    raw = ICON.read_bytes()
    entries = _directory(raw)
    assert [size for size, _, _ in entries] == list(_SIZES)

    end = 6 + 16 * len(entries)
    for size, length, offset in entries:
        assert offset == end, f"the {size} px entry does not follow the one before it"
        assert offset + length <= len(raw), f"the {size} px entry runs off the end"
        end = offset + length
    assert end == len(raw), "there are bytes in the file no entry claims"


def test_the_small_sizes_are_bitmaps_and_the_large_ones_are_png():
    """Not a style choice: GDI+ on this machine declines a PNG entry at some sizes.

    make_icon.py's own docstring records it, and Explorer wants the two big
    sizes compressed. A change that made every entry one kind or the other
    would pass every other test here.
    """
    raw = ICON.read_bytes()
    for size, length, offset in _directory(raw):
        blob = raw[offset : offset + length]
        if size >= _PNG_FROM:
            assert blob[:8] == _PNG_MAGIC, f"the {size} px entry is not a PNG"
            assert struct.unpack(">II", blob[16:24]) == (size, size)
        else:
            assert blob[:8] != _PNG_MAGIC, f"the {size} px entry is a PNG"
            header, width, doubled = struct.unpack("<Iii", blob[:12])
            assert header == 40, f"the {size} px entry is not a BITMAPINFOHEADER"
            assert width == size
            # Colour over mask, both counted: an icon bitmap says twice its height.
            assert doubled == size * 2
