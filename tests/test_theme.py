import sys

import pytest

from net_preset import theme

# The `root` fixture lives in conftest.py, shared with the dialog tests. It
# hands back a withdrawn root and retries past a local flake that is not a
# missing display; see the note there.


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
def test_applying_the_theme_to_a_real_root_leaves_it_dark(root):
    theme.apply(root)
    assert root.cget("background") == theme.WINDOW
    from tkinter import ttk

    assert ttk.Style(root).theme_use() == "clam"


class _FakeKey:
    """Stands in for the registry handle, which is used as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


def test_the_accent_colour_reverses_the_dwm_byte_order(monkeypatch):
    # DWM stores 0x00BBGGRR, so the low byte is red and the high one is blue.
    # Reading it in HTML order themes the whole application in the wrong hue.
    monkeypatch.setattr(theme.winreg, "OpenKey", lambda *args, **kwargs: _FakeKey())
    monkeypatch.setattr(theme.winreg, "QueryValueEx", lambda key, name: (0x00112233, 4))
    assert theme.accent_colour() == "#332211"


def test_a_dark_accent_takes_white_text():
    # Windows can derive the accent from the wallpaper, so it moves on its own.
    # Black on this purple is 1.91, which is what made the choice necessary.
    assert theme.text_on_accent("#680081") == "#ffffff"


def test_a_light_accent_takes_black_text():
    assert theme.text_on_accent("#ffd700") == "#000000"


def test_the_choice_flips_at_the_crossover_grey():
    # One step apart, either side of the luminance where black and white score
    # the same. A function that answers with a constant cannot pass both.
    assert theme.text_on_accent("#757575") == "#ffffff"
    assert theme.text_on_accent("#767676") == "#000000"


def test_the_default_blue_accent_gets_a_readable_label():
    # Black wins on this one, 4.64 to white's 4.53.
    assert theme.contrast_ratio(theme.text_on_accent("#0078d4"), "#0078d4") >= 4.5


def test_the_purple_accent_gets_a_readable_label():
    assert theme.contrast_ratio(theme.text_on_accent("#680081"), "#680081") >= 4.5


def test_the_exported_on_accent_colour_suits_the_accent_in_the_registry():
    # Fails the moment anyone puts a literal back, on any machine whose accent
    # disagrees with the literal they chose.
    assert theme.contrast_ratio(theme.TEXT_ON_ACCENT, theme.accent_colour()) >= 4.5


def test_the_listbox_selection_can_be_read():
    options = theme.listbox_options()
    assert theme.contrast_ratio(options["selectforeground"], options["selectbackground"]) >= 4.5


def test_the_menu_highlight_can_be_read():
    options = theme.menu_options()
    assert theme.contrast_ratio(options["activeforeground"], options["activebackground"]) >= 4.5


@pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows display")
def test_the_primary_button_label_is_readable_on_whatever_accent_is_set(root):
    # The colour the widget is actually given, not the one the module exports.
    theme.apply(root)
    from tkinter import ttk

    style = ttk.Style(root)
    fill = style.lookup("Accent.TButton", "background")
    label = style.lookup("Accent.TButton", "foreground")
    assert theme.contrast_ratio(label, fill) >= 4.5


@pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows display")
def test_every_button_shows_a_focus_ring_against_its_own_fill(root):
    # 3.0 is the WCAG floor for a control outline rather than for text. A ring
    # painted in the button's own fill scores 1.0 and cannot be seen at all.
    theme.apply(root)
    from tkinter import ttk

    style = ttk.Style(root)
    for name in ("Accent.TButton", "Secondary.TButton", "Danger.TButton"):
        fill = style.lookup(name, "background")
        ring = style.lookup(name, "focuscolor")
        assert theme.contrast_ratio(ring, fill) >= 3.0, name
