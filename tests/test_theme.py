import sys
import tkinter as tk

import pytest

from net_preset import theme


def test_the_accent_colour_is_a_hex_triplet():
    colour = theme.accent_colour()
    assert colour.startswith("#") and len(colour) == 7
    int(colour[1:], 16)


def test_body_text_is_readable_on_the_window():
    assert theme.contrast_ratio(theme.TEXT, theme.WINDOW) >= 4.5


def test_secondary_text_is_readable_on_the_window():
    assert theme.contrast_ratio(theme.TEXT_SECONDARY, theme.WINDOW) >= 4.5


def test_the_danger_colour_is_readable_on_the_window():
    # Pure red fails this against #202020, which is why the tone is lightened.
    assert theme.contrast_ratio(theme.DANGER, theme.WINDOW) >= 4.5


def test_text_is_readable_on_the_surface():
    assert theme.contrast_ratio(theme.TEXT, theme.SURFACE) >= 4.5


def test_identical_colours_have_a_ratio_of_one():
    assert theme.contrast_ratio("#808080", "#808080") == pytest.approx(1.0)


def test_black_on_white_is_the_maximum_ratio():
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.05)


def test_the_listbox_options_carry_the_window_background():
    options = theme.listbox_options()
    assert options["background"] == theme.WINDOW
    assert options["borderwidth"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows display")
def test_applying_the_theme_to_a_real_root_leaves_it_dark():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    try:
        root.withdraw()
        theme.apply(root)
        assert root.cget("background") == theme.WINDOW
        from tkinter import ttk

        assert ttk.Style(root).theme_use() == "clam"
    finally:
        root.destroy()
