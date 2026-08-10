"""The modal window where one profile is typed in, for both adding and editing.

The dialog collects text, checks it with `profile.validate` and reports what the
user chose. It saves nothing: where profiles are kept and what an adapter does
with them are somebody else's business.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence
from dataclasses import dataclass
from tkinter import messagebox, ttk

from . import theme
from .profile import DEFAULT_MASK, FieldError, Profile, validate

FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "Nazwa"),
    ("address", "Adres IP"),
    ("mask", "Maska"),
    ("gateway", "Brama"),
    ("dns", "DNS"),
    ("dns_alt", "DNS alternatywny"),
)

# The field column is held this wide so that the longest message `validate` can
# produce still fits on one line. Left to size itself, the column would grow the
# moment an error appeared and the whole form would jump sideways under the
# user's hands.
_FIELD_WIDTH = 260


@dataclass(frozen=True)
class DialogResult:
    """What the user did with the dialog, and the profile it left behind.

    `action` is "save", "delete" or "cancel". The profile is the form's contents
    on a save, the profile being edited on a delete, and nothing on a cancel.
    """

    action: str
    profile: Profile | None = None


def _without_own_name(names: Sequence[str], profile: Profile | None) -> tuple[str, ...]:
    """The names the form must not collide with.

    A profile being edited is not its own duplicate, so its current name comes
    out of the list; without this, saving a profile whose name was never touched
    would be refused. `validate` compares names case-insensitively, so the
    removal has to as well.
    """
    if profile is None:
        return tuple(names)
    own = profile.name.strip().casefold()
    return tuple(name for name in names if name.strip().casefold() != own)


class ProfileDialog(tk.Toplevel):
    """A form for one profile: six fields, a message under each, three buttons.

    `result` stays None until the user finishes, which is how the caller — and
    the dialog itself — tells a rejected form from a closed one.
    """

    def __init__(
        self,
        parent,
        *,
        profile: Profile | None = None,
        other_names: Sequence[str] = (),
    ) -> None:
        super().__init__(parent)
        self.result: DialogResult | None = None
        self.delete_button: ttk.Button | None = None

        self._profile = profile
        self._other_names = _without_own_name(other_names, profile)
        self._values: dict[str, tk.StringVar] = {}
        self._entries: dict[str, ttk.Entry] = {}
        self._messages: dict[str, tk.StringVar] = {}

        # Hidden until `show` has placed it, so it never flashes in a corner of
        # the screen first and then jumps over the parent.
        self.withdraw()
        self.title("Edycja ustawień" if profile is not None else "Nowe ustawienia")
        self.resizable(False, False)
        self.configure(background=theme.WINDOW)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Return>", self._on_return)
        # A technician typing addresses works on the numeric keypad, and its
        # Enter is a different keysym.
        self.bind("<KP_Enter>", self._on_return)
        self.bind("<Escape>", self._on_escape)

        self._build()

    # -- the seams the caller and the tests use ---------------------------------

    def value_of(self, field: str) -> str:
        """The text in *field*, trimmed.

        Trimming happens here rather than at each call site, so a value can
        never reach `validate` or a `Profile` with the padding a user leaves
        behind when pasting an address.
        """
        return self._values[field].get().strip()

    def set_value(self, field: str, text: str) -> None:
        """Put *text* in *field*, as though it had been typed."""
        self._values[field].set(text)

    def entry_of(self, field: str) -> ttk.Entry:
        """The entry widget for *field*."""
        return self._entries[field]

    def error_of(self, field: str) -> str:
        """The message currently shown under *field*, empty when it is fine."""
        return self._messages[field].get()

    # -- what the buttons do ----------------------------------------------------

    def on_confirm(self) -> None:
        """Check the form. On success report a save and close; otherwise stay."""
        candidate = Profile(**{field: self.value_of(field) for field, _ in FIELDS})
        errors = validate(candidate, self._other_names)
        self._mark(errors)
        if errors:
            # Put the cursor where the work is, rather than making the user hunt
            # for the field that was refused.
            self.entry_of(errors[0].field).focus_set()
            return

        self.result = DialogResult("save", candidate)
        self._close()

    def on_cancel(self) -> None:
        """Abandon the form."""
        self.result = DialogResult("cancel")
        self._close()

    def on_delete(self) -> None:
        """Ask first, and only then report that the profile should go."""
        if self._profile is None or not self.confirm_delete():
            return

        self.result = DialogResult("delete", self._profile)
        self._close()

    def confirm_delete(self) -> bool:
        """Ask the user whether to really delete the profile.

        A method of its own, and not a call inside `on_delete`, so that a test
        can answer the question without a message box on the screen.
        """
        name = self._profile.name if self._profile is not None else ""
        return messagebox.askyesno(
            "Usuwanie ustawień",
            f"Usunąć ustawienia {name}?",
            icon="warning",
            # Windows puts the safe answer under the Enter key when the other
            # one cannot be undone, and this one cannot.
            default=messagebox.NO,
            parent=self,
        )

    # -- opening and closing ----------------------------------------------------

    def show(self) -> DialogResult:
        """Open the dialog over its parent and block until the user is done."""
        self.transient(self.master)
        self._place_over_parent()
        self.deiconify()
        # Nothing below may run any earlier than this. A window that is still
        # withdrawn has no frame yet, and Tk builds it a new one whenever the
        # transient or withdrawn state changes: a dark title bar asked for
        # before the window is on the screen is set on a frame that is then
        # thrown away, and the dialog opens as a white caption over a black
        # form. A focus asked for that early is forgotten in the same way, and
        # the first thing the user types goes nowhere. Both were seen happening.
        self.wait_visibility()
        # A Toplevel has its own frame, so it needs asking in its own right —
        # the theme only dressed the main window.
        theme.enable_dark_title_bar(self)
        self.grab_set()
        self._focus_first_gap()
        self.wait_window(self)
        # The window's X goes through `on_cancel`, but a parent that tears the
        # dialog down leaves no result at all, and that is a cancel too.
        return self.result if self.result is not None else DialogResult("cancel")

    def _close(self) -> None:
        self.grab_release()
        self.destroy()

    # -- building it ------------------------------------------------------------

    def _build(self) -> None:
        body = ttk.Frame(self, padding=(24, 20, 24, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1, minsize=_FIELD_WIDTH)

        for index, (field, caption) in enumerate(FIELDS):
            value = tk.StringVar(self, value=self._initial(field))
            message = tk.StringVar(self, value="")
            entry = ttk.Entry(body, textvariable=value, style="TEntry")

            ttk.Label(body, text=caption).grid(row=index * 2, column=0, sticky="w", padx=(0, 16))
            entry.grid(row=index * 2, column=1, sticky="ew")
            # The message keeps its line whether or not it has anything to say,
            # so a rejected field pushes nothing around when it speaks up. It is
            # written in the danger tone rather than the secondary grey: it has
            # to read as a refusal and match the outline on the field above it.
            ttk.Label(body, textvariable=message, foreground=theme.DANGER).grid(
                row=index * 2 + 1, column=1, sticky="w", pady=(2, 10)
            )

            self._values[field] = value
            self._entries[field] = entry
            self._messages[field] = message

        self._build_buttons(body, row=len(FIELDS) * 2)

    def _build_buttons(self, body: ttk.Frame, *, row: int) -> None:
        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        # The empty first column takes all the slack, which pins deleting to the
        # left and the everyday pair to the right.
        buttons.columnconfigure(0, weight=1)

        if self._profile is not None:
            self.delete_button = ttk.Button(
                buttons, text="USUŃ", style="Danger.TButton", command=self.on_delete
            )
            self.delete_button.grid(row=0, column=0, sticky="w")

        ttk.Button(buttons, text="Anuluj", style="Secondary.TButton", command=self.on_cancel).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(buttons, text="Zapisz", style="Accent.TButton", command=self.on_confirm).grid(
            row=0, column=2
        )

    def _initial(self, field: str) -> str:
        if self._profile is not None:
            return getattr(self._profile, field)
        # A /24 is what almost every small site runs, so it is the one value
        # worth filling in for the user.
        return DEFAULT_MASK if field == "mask" else ""

    def _focus_first_gap(self) -> None:
        """Start on the first field with nothing in it, or on the first field."""
        for field, _ in FIELDS:
            if not self.value_of(field):
                self.entry_of(field).focus_set()
                return
        self.entry_of(FIELDS[0][0]).focus_set()

    def _place_over_parent(self) -> None:
        """Centre the dialog on its parent, a little above the middle."""
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # -- marking what is wrong --------------------------------------------------

    def _mark(self, errors: Sequence[FieldError]) -> None:
        """Show this round's problems, and only this round's.

        Every field is cleared first: a field the user has since corrected is
        told apart from a broken one only by the absence of a fresh error, so
        setting the new marks without dropping the old ones would leave a
        corrected field looking wrong for ever.
        """
        for field, _ in FIELDS:
            self._entries[field].configure(style="TEntry")
            self._messages[field].set("")

        for error in errors:
            self._entries[error.field].configure(style="Invalid.TEntry")
            self._messages[error.field].set(error.message)

    # -- keyboard ---------------------------------------------------------------

    def _on_return(self, _event: tk.Event) -> None:
        self.on_confirm()

    def _on_escape(self, _event: tk.Event) -> None:
        self.on_cancel()
