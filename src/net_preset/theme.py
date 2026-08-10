"""A Windows 11 flavoured dark theme, built from the standard library only.

ttk's native Windows themes draw their controls through the OS and ignore most
colour options, so a dark window built on them comes out half light. `clam` is
the one bundled theme that honours every colour, which is why everything here
starts by switching to it and then rebuilds the look by hand.

Two details come from the system rather than being invented: the accent colour,
so the app matches the user's personalisation, and the title bar, which stays
light unless asked otherwise through DWM.
"""

from __future__ import annotations

import contextlib
import ctypes
import winreg
from ctypes import wintypes
from tkinter import ttk

# Windows 11 dark surfaces. The window sits on the darkest tone and controls are
# lifted slightly out of it, which is how the system separates layers without
# drawing borders around everything.
WINDOW = "#202020"
SURFACE = "#2d2d2d"
SURFACE_HOVER = "#323232"
SURFACE_PRESSED = "#272727"
BORDER = "#383838"

TEXT = "#ffffff"
TEXT_SECONDARY = "#c5c5c5"

# Text drawn on the accent is one of these two, whichever the accent can carry.
# The choice needs contrast_ratio, so TEXT_ON_ACCENT is derived further down.
_ON_ACCENT_CANDIDATES = ("#000000", TEXT)

# Windows 11 does not use pure red for critical text on dark backgrounds; it is
# unreadable against #202020. This is the system's own lightened tone.
DANGER = "#ff99a4"

_FALLBACK_ACCENT = "#0078d4"

BODY_FONT = ("Segoe UI Variable Text", 10)
VALUE_FONT = ("Consolas", 11)
TITLE_FONT = ("Segoe UI Variable Display", 12)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34


def accent_colour() -> str:
    """The user's Windows accent colour, or the system default if unreadable.

    DWM stores it as 0x00BBGGRR, so the byte order is reversed from HTML.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\DWM") as key:
            value, _ = winreg.QueryValueEx(key, "AccentColor")
    except OSError:
        return _FALLBACK_ACCENT
    red = value & 0xFF
    green = (value >> 8) & 0xFF
    blue = (value >> 16) & 0xFF
    return f"#{red:02x}{green:02x}{blue:02x}"


def _channels(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _lighten(colour: str, amount: float) -> str:
    """Mix *colour* towards white by *amount* (0..1)."""

    def mix(channel: int) -> int:
        return min(255, round(channel + (255 - channel) * amount))

    red, green, blue = _channels(colour)
    return f"#{mix(red):02x}{mix(green):02x}{mix(blue):02x}"


def _darken(colour: str, amount: float) -> str:
    """Mix *colour* towards black by *amount* (0..1)."""

    def mix(channel: int) -> int:
        return max(0, round(channel * (1 - amount)))

    red, green, blue = _channels(colour)
    return f"#{mix(red):02x}{mix(green):02x}{mix(blue):02x}"


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colours.

    Used to keep the status line readable: the message that says applying a
    profile went wrong is the one that must never be hard to read.
    """

    def luminance(colour: str) -> float:
        channels = []
        for value in _channels(colour):
            fraction = value / 255
            channels.append(
                fraction / 12.92 if fraction <= 0.03928 else ((fraction + 0.055) / 1.055) ** 2.4
            )
        red, green, blue = channels
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def text_on_accent(accent: str) -> str:
    """Black or white for text sitting on *accent*, whichever can be read.

    Windows can derive the accent from the wallpaper, so it moves on its own
    without anyone touching a setting, and no single foreground survives the
    move. Black scores 4.64 on the default blue #0078d4 but 1.91 on #680081, a
    purple this machine drifted to by itself, where white scores 11.02. Ties go
    to black, which is what Windows 11 puts on a light accent.
    """
    return max(_ON_ACCENT_CANDIDATES, key=lambda colour: contrast_ratio(colour, accent))


# The answer for the accent as it stood when the module was imported. apply(),
# menu_options() and listbox_options() each recompute from the accent they read,
# so a widget always gets the colour that matches the fill it is drawn on; this
# is the exported shorthand for callers that only need the current answer.
TEXT_ON_ACCENT = text_on_accent(accent_colour())


def _colorref(colour: str) -> int:
    """Hex colour to the 0x00BBGGRR integer DWM expects."""
    red, green, blue = _channels(colour)
    return (blue << 16) | (green << 8) | red


def enable_dark_title_bar(root) -> None:
    """Ask DWM for a dark title bar and a border that matches the window.

    Silent on failure: an older Windows build simply keeps its light chrome,
    which is cosmetic. Nothing here is worth taking the window down for, and the
    border attribute in particular only exists from Windows 11.
    """
    with contextlib.suppress(Exception):
        root.update_idletasks()
        # winfo_id() is the Tk frame; the titled window is its parent.
        hwnd = wintypes.HWND(ctypes.windll.user32.GetParent(root.winfo_id()))
        dwm = ctypes.windll.dwmapi

        flag = wintypes.BOOL(True)
        dwm.DwmSetWindowAttribute(
            hwnd,
            wintypes.DWORD(_DWMWA_USE_IMMERSIVE_DARK_MODE),
            ctypes.byref(flag),
            ctypes.sizeof(flag),
        )

        # Without this the frame keeps a light hairline that reads as a stray
        # outline against the dark window.
        border = wintypes.DWORD(_colorref(BORDER))
        dwm.DwmSetWindowAttribute(
            hwnd,
            wintypes.DWORD(_DWMWA_BORDER_COLOR),
            ctypes.byref(border),
            ctypes.sizeof(border),
        )


def apply(root) -> None:
    """Dress *root* and every ttk widget under it in the dark theme."""
    accent = accent_colour()
    accent_hover = _lighten(accent, 0.12)
    accent_pressed = _darken(accent, 0.12)
    on_accent = text_on_accent(accent)

    root.configure(background=WINDOW)

    # The combobox dropdown is a plain Tk listbox that ttk styling never reaches.
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", accent)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=WINDOW, foreground=TEXT, font=BODY_FONT)
    style.configure("TFrame", background=WINDOW)
    style.configure("TLabel", background=WINDOW, foreground=TEXT)
    style.configure("Secondary.TLabel", foreground=TEXT_SECONDARY)
    style.configure("Value.TLabel", font=VALUE_FONT)
    style.configure("TSeparator", background=BORDER)

    # Primary action, filled with the accent the way Windows 11 fills its
    # default button. The label takes whichever of black and white the accent
    # of the day can carry.
    style.configure(
        "Accent.TButton",
        background=accent,
        foreground=on_accent,
        bordercolor=accent,
        focuscolor=accent,
        borderwidth=0,
        padding=(18, 7),
        anchor="center",
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", SURFACE), ("pressed", accent_pressed), ("active", accent_hover)],
        foreground=[("disabled", TEXT_SECONDARY)],
    )

    # The everyday actions beside it: lifted out of the window on a surface tone
    # with a hairline border, the way Windows 11 draws a standard button.
    style.configure(
        "Secondary.TButton",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        focuscolor=SURFACE,
        borderwidth=1,
        padding=(18, 7),
        anchor="center",
    )
    style.map(
        "Secondary.TButton",
        background=[("pressed", SURFACE_PRESSED), ("active", SURFACE_HOVER)],
        foreground=[("disabled", TEXT_SECONDARY)],
    )

    # Deleting is a standard button whose text carries the warning. Windows 11
    # does not fill a destructive button; it tints the label and leaves the rest.
    style.configure(
        "Danger.TButton",
        background=SURFACE,
        foreground=DANGER,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        focuscolor=SURFACE,
        borderwidth=1,
        padding=(18, 7),
        anchor="center",
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", SURFACE_PRESSED), ("active", SURFACE_HOVER)],
        foreground=[("disabled", TEXT_SECONDARY)],
    )

    # Dialog fields. The focus ring is the accent, which is the only cue Windows
    # 11 gives a text box that is taking input.
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        borderwidth=1,
        padding=(8, 5),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", accent)],
        # clam draws the inside of the border in its own pale blue on focus and
        # keeps doing so however the border itself is coloured, which leaves a
        # washed-out hairline inside the accent ring. The same three options have
        # to be spelled out for the combobox below, for the same reason.
        lightcolor=[("focus", accent)],
        darkcolor=[("focus", accent)],
    )

    # A field holding something the program cannot use. The danger outline wins
    # in every state, focus included, so the mark stays until the value is right.
    style.configure("Invalid.TEntry", bordercolor=DANGER)
    style.map(
        "Invalid.TEntry",
        bordercolor=[("focus", DANGER), ("!focus", DANGER)],
        lightcolor=[("focus", DANGER)],
        darkcolor=[("focus", DANGER)],
    )

    # A dropdown built from a menubutton rather than a combobox. clam's combobox
    # keeps a light grey fill behind its arrow whatever background, troughcolor
    # or state mapping it is given, which leaves a bright button beside an
    # otherwise dark control. A menubutton has no such element, and the menu it
    # opens is a classic Tk widget whose colours are all ours to set.
    style.layout(
        "Dropdown.TMenubutton",
        [
            (
                "Menubutton.border",
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Menubutton.focus",
                            {
                                "sticky": "nswe",
                                "children": [
                                    (
                                        "Menubutton.padding",
                                        {
                                            "sticky": "nswe",
                                            "children": [("Menubutton.label", {"sticky": "nswe"})],
                                        },
                                    )
                                ],
                            },
                        )
                    ],
                },
            )
        ],
    )
    style.configure(
        "Dropdown.TMenubutton",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        focuscolor=SURFACE,
        borderwidth=1,
        padding=(10, 5),
        anchor="w",
    )
    style.map(
        "Dropdown.TMenubutton",
        background=[("pressed", SURFACE_PRESSED), ("active", SURFACE_HOVER)],
        bordercolor=[("focus", accent)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=TEXT,
        arrowcolor=TEXT_SECONDARY,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        # The drop-down arrow is a sibling of the field, not a child of it, with
        # its own fill. Miss any of these and it keeps clam's light grey button
        # next to an otherwise dark control.
        troughcolor=SURFACE,
        arrowsize=14,
        borderwidth=1,
        padding=(8, 4),
        # A read-only combobox draws its text as a selection whenever it has
        # focus. Left at the default that is a bright blue block over the value.
        selectbackground=SURFACE,
        selectforeground=TEXT,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE), ("active", SURFACE_HOVER)],
        # "readonly" has to appear here too: it is the state this combobox is
        # always in, and without it the arrow button keeps clam's light default
        # while the field around it goes dark.
        background=[
            ("readonly", SURFACE),
            ("pressed", SURFACE_PRESSED),
            ("active", SURFACE_HOVER),
        ],
        foreground=[("readonly", TEXT)],
        troughcolor=[("readonly", SURFACE), ("active", SURFACE_HOVER)],
        arrowcolor=[("active", TEXT)],
        bordercolor=[("focus", accent)],
        lightcolor=[("focus", accent)],
        darkcolor=[("focus", accent)],
    )

    enable_dark_title_bar(root)


def menu_options() -> dict[str, object]:
    """Colours for a tk.Menu, which ttk styling never reaches."""
    accent = accent_colour()
    return {
        "tearoff": 0,
        "background": SURFACE,
        "foreground": TEXT,
        "activebackground": accent,
        "activeforeground": text_on_accent(accent),
        "selectcolor": TEXT,
        "borderwidth": 0,
        "relief": "flat",
        "activeborderwidth": 0,
        "font": BODY_FONT,
    }


def listbox_options() -> dict[str, object]:
    """Colours for a tk.Listbox, which ttk styling never reaches either."""
    accent = accent_colour()
    return {
        "background": WINDOW,
        "foreground": TEXT,
        "selectbackground": accent,
        "selectforeground": text_on_accent(accent),
        "borderwidth": 0,
        "highlightthickness": 1,
        "highlightbackground": BORDER,
        "highlightcolor": accent,
        "activestyle": "none",
        "font": BODY_FONT,
    }
