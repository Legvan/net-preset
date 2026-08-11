"""The main window: the profile list, the four things it can do, and the wiring.

Everything below this module is finished and tested on its own. What is left here
is the part no unit test can settle — what the operator sees, in what order, and
which button is allowed to do what — plus the one piece of real machinery the
window owns: the worker thread that applies a profile.

Three rules shape the code:

Nothing that touches the operating system happens on the UI thread except reading
the adapters, which is one API call and cheap enough to run on a timer. Applying a
profile runs on a worker thread, because netsh can take twenty seconds per command.

The worker never touches Tk. Tkinter is not thread-safe and the punishment for
ignoring that is a crash weeks later with no stack to read, so the worker's last
act is `after(0, ...)` and nothing else.

The window says what the adapter actually did. Every message on the status line
either comes from `apply_profile`, which read the adapter back, or names something
this module itself did. Nothing here guesses.
"""

from __future__ import annotations

import contextlib
import threading
import tkinter as tk
from collections.abc import Callable, Iterable, Iterator, Sequence
from tkinter import font as tkfont
from tkinter import ttk

from net_preset import theme
from net_preset.adapters import AdapterState, ethernet_adapters
from net_preset.apply import (
    DEFAULT_ATTEMPTS,
    NETSH_TIMEOUT_SECONDS,
    POLL_SECONDS,
    Outcome,
    apply_profile,
)
from net_preset.dialog import ProfileDialog
from net_preset.elevation import is_elevated
from net_preset.profile import Profile
from net_preset.settings import load_adapter_choice, save_adapter_choice, settings_path
from net_preset.store import load_profiles, profiles_path, save_profiles

WINDOW_TITLE = "net-preset"

# What `fit` needs of a font: the pixel width of a string. tkfont.Font.measure is
# the one the window passes; a test passes a fixed width per character.
Measure = Callable[[str], int]

# DHCP is not a profile: it cannot be edited or deleted, and it is always the
# first row, so the operator's way back to a working network is in the same place
# whatever else the list holds.
DHCP_LABEL = "DHCP (automatycznie)"

# The status line, in the order the window prefers to say them. The first two
# stop the window doing anything at all, so they outrank the note about a stored
# file, which describes something already lost.
NO_ADAPTER = "Nie znaleziono karty Ethernet"
NOT_ELEVATED = "Brak uprawnień administratora — USTAW nie zadziała"
READY = "Gotowy"
NO_ANSWER = "Brak odpowiedzi — polecenie może wciąż działać w tle"
SAVE_FAILED = "Nie udało się zapisać listy ustawień"
APPLY_FAILED = "Nie udało się zmienić ustawień"

# The current-state line.
NO_CARD = "brak karty"
NO_ADDRESS = "brak adresu"
NO_LEASE = "DHCP, brak dzierżawy"

LIST_HEIGHT = 8
REFRESH_MS = 2000
PADDING = 16

# Wide enough that the three buttons do not fill the row: the slack is what puts
# a gap in front of USTAW, and without it the everyday pair and the one that
# changes the network read as a single segmented control.
CONTENT_WIDTH = 420

# Two lines are reserved for the status whether or not they are needed, so a long
# message cannot resize a window the operator has no way of resizing back. The
# height is measured off the label rather than worked out, because a reservation
# that is a few pixels short is a window that grows the first time something goes
# wrong — the worst moment to move the buttons under somebody's hand.
STATUS_LINES = 2
STATUS_GAP = 8
# The width `fit` wraps to. A ttk label asks for four pixels more than its text
# measures (measured, both dimensions), and asking for more than the column holds
# is what widens the window.
LABEL_PADDING = 4
STATUS_WIDTH = CONTENT_WIDTH - LABEL_PADDING

# When to stop waiting for the worker and give the window back. An honest apply
# can take three netsh calls that each spend their whole budget plus the
# read-back poll, so anything shorter would cut off a slow success; past that,
# `run_netsh`'s own docstring warns that its timeout is a budget rather than a
# ceiling, and a worker that outlives it must not take the buttons with it.
WATCHDOG_MS = int(1000 * (3 * NETSH_TIMEOUT_SECONDS + DEFAULT_ATTEMPTS * POLL_SECONDS + 10))


def list_entries(profiles: Sequence[Profile]) -> list[str]:
    """The rows of the list: DHCP first, then every profile in the stored order."""
    return [DHCP_LABEL, *(profile.label for profile in profiles)]


def state_text(adapter: AdapterState | None) -> str:
    """What the *Teraz* line says about *adapter* as it stands.

    The APIPA case is named rather than shown. A 169.254 address is Windows
    saying no DHCP server answered, and "DHCP, brak dzierżawy" is that in words;
    printing the address instead would have the operator reading a number that
    means the opposite of a working link.
    """
    if adapter is None:
        return NO_CARD
    if adapter.apipa:
        return NO_LEASE
    primary = adapter.primary
    return NO_ADDRESS if primary is None else f"{primary[0]} /{primary[1]}"


def fit(text: str, measure: Measure, width: int, lines: int = STATUS_LINES) -> str:
    """*text* wrapped into at most *lines* lines, none of them wider than *width*.

    *measure* answers the pixel width of a string in the font the label draws
    with, so the answer is exact rather than a guess at how many characters go on
    a line: 110 characters of Polish lower case take two lines, the same count in
    capitals takes three, and one long token takes as many as it likes.

    Counting characters is what the first version of this did, and it was wrong in
    both directions. Tk's own wraplength is no better on its own — it breaks at
    spaces only, so a netsh refusal or an OSError carrying one long unbroken path
    makes the label *wider* than the window as well as taller, and the window is
    not resizable. So a token too wide for a line of its own is cut here, and what
    still does not fit is dropped with an ellipsis: the part that names the
    problem is at the front, which is why the tail is what goes.
    """
    rows: list[str] = []
    current = ""
    overflowed = False

    for atom in _atoms(str(text).split(), measure, width):
        if not current:
            current = atom
        elif measure(f"{current} {atom}") <= width:
            current = f"{current} {atom}"
        elif len(rows) + 1 < lines:
            rows.append(current)
            current = atom
        else:
            overflowed = True
            break

    rows.append(current)
    if overflowed:
        rows[-1] = _with_ellipsis(rows[-1], measure, width)
    return "\n".join(rows)


def _atoms(words: Iterable[str], measure: Measure, width: int) -> Iterator[str]:
    """The words, with any word too wide for a whole line cut into pieces."""
    for word in words:
        while measure(word) > width:
            cut = _longest_prefix(word, measure, width)
            yield word[:cut]
            word = word[cut:]
        yield word


def _longest_prefix(word: str, measure: Measure, width: int) -> int:
    """How much of *word* fits in *width*, and never less than one character.

    Never less, because the caller cuts the word at this point and goes round
    again: a zero would spin for ever on a width narrower than one glyph.
    """
    cut = 1
    while cut < len(word) and measure(word[: cut + 1]) <= width:
        cut += 1
    return cut


def _with_ellipsis(row: str, measure: Measure, width: int) -> str:
    """*row* cut back far enough that it still fits once the ellipsis is on it."""
    while row and measure(row + "…") > width:
        row = row[:-1]
    return row.rstrip() + "…"


def _state(enabled: bool) -> str:
    """The ttk state name for a button that is or is not available."""
    return "normal" if enabled else "disabled"


class Application(tk.Tk):
    """The window, and everything the buttons on it set in motion."""

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.resizable(False, False)
        # Before any widget exists: the theme switches ttk to clam, and a widget
        # built under the native theme would keep it.
        theme.apply(self)

        self.profiles, self.complaint = load_profiles(profiles_path())
        self.adapters = ethernet_adapters()
        self.chosen = tk.StringVar(self, value=load_adapter_choice(settings_path()) or "")
        self.adapter: AdapterState | None = None
        self._use_adapter()
        # Asked once: a process cannot gain an administrator token while it runs,
        # and this is a ctypes call into shell32.
        self.elevated = is_elevated()

        # Whether the opening line was said with a card present. The set of cards
        # moves while the window is open — a USB adapter goes into a dock, a dock
        # comes off a laptop — and that line has to be said again when it does.
        self.announced_card = self.adapter is not None
        # An apply in flight, the thread running it, the token that tells its
        # answer from an abandoned one, and the watchdog that gives the buttons
        # back if it never answers at all.
        self.busy = False
        self.worker: threading.Thread | None = None
        self.attempt = 0
        self.watchdog: str | None = None
        # The status line wraps itself, against the font it is drawn in.
        self.measure: Measure = tkfont.Font(root=self, font=theme.BODY_FONT).measure

        self._build()
        self.refresh_list()
        self._show_state()
        self._announce()
        # The list, not the first button: the operator arrives at a window whose
        # arrow keys already move through the profiles.
        self.listbox.focus_set()
        self.ticker = self.after(REFRESH_MS, self.on_tick)

    # -- what the window knows ----------------------------------------------------

    def selected_profile(self) -> Profile | None:
        """The profile on the selected row, or None for DHCP — and for no row at all.

        A row past the end of the list reads as None too: `refresh_list` keeps the
        selection where it was, and a caller that shortened `profiles` first would
        otherwise index off the end.
        """
        index = self._selected_index()
        if index is None or not 1 <= index <= len(self.profiles):
            return None
        return self.profiles[index - 1]

    # -- keeping the window in step with what it shows -----------------------------

    def refresh_list(self, select: int | None = None) -> None:
        """Rebuild the rows, then put the selection on *select* or leave it be.

        There is always a selected row: the list can never be empty, DHCP being a
        permanent first entry, and a window with nothing selected would have two
        of its three buttons disabled for no reason the operator can see.
        """
        if select is None:
            index = self._selected_index()
            select = 0 if index is None else index
        entries = list_entries(self.profiles)
        self.listbox.delete(0, tk.END)
        for entry in entries:
            self.listbox.insert(tk.END, entry)
        self._select(min(max(select, 0), len(entries) - 1))

    def refresh_current(self) -> None:
        """Re-read the adapters and show what the chosen one is carrying now.

        This is the whole point of the *Teraz* line: the effect of USTAW shows up
        here without the operator opening anything, and so does a cable pulled out
        of the card while the window sits there.

        The whole set is re-read, not one card's state, so everything the window
        says about which card it is using settles in one place: which card that
        is, whether there is a choice to show, and the opening line, which ranks
        a missing card above everything else it could be saying.
        """
        self.adapters = ethernet_adapters()
        self._use_adapter()
        self._sync_adapter_row()
        self._show_state()
        self._reannounce()

    def on_selection_changed(self, _event: tk.Event | None = None) -> None:
        """Decide what the three buttons may do, which is a question of one state.

        Editing belongs to the profile rows only — DHCP is not a profile. Applying
        needs a card to apply to and a token to do it with. An apply in flight
        takes all three away, so a second click cannot overlap the first and the
        list cannot change under the worker.

        The adapter picker goes with them, and so does the list. The worker holds
        the card and the profile it was given, so moving either mid-apply is safe
        — but the answer, when it lands, is about what was chosen when USTAW was
        pressed, and it would arrive under a highlighted row, a picker and a
        *Teraz* line naming something else. The highlight is what an operator
        reads to know which profile is on the card, so a success line for ROGER
        sitting under a highlighted BIURO is a wrong answer to the question they
        are actually asking.
        """
        index = self._selected_index()
        editable = index is not None and index > 0
        appliable = index is not None and self.adapter is not None and self.elevated
        self.add_button.configure(state=_state(not self.busy))
        self.edit_button.configure(state=_state(editable and not self.busy))
        self.apply_button.configure(state=_state(appliable and not self.busy))
        self.adapter_picker.configure(state=_state(not self.busy))
        self.listbox.configure(state=_state(not self.busy))

    def on_adapter_chosen(self) -> None:
        """Switch to the adapter the operator picked and remember it for next time."""
        save_adapter_choice(self.chosen.get(), settings_path())
        self.refresh_current()
        self.on_selection_changed()

    # -- the three buttons ---------------------------------------------------------
    #
    # The two that open a dialog may only be reached from a mapped window.
    # `ProfileDialog.show` waits on wait_visibility, and that never returns for a
    # transient child of a master that is minimised or withdrawn: the application
    # would hang with no window to close. Both are wired to a button and nothing
    # else, and anything that binds them to a key or a timer has to keep it true.

    def on_add(self) -> None:
        """Collect a new profile, store it, and leave the operator standing on it."""
        if self.busy:
            return
        result = ProfileDialog(self, other_names=self._names()).show()
        if result.action != "save" or result.profile is None:
            return
        self.profiles.append(result.profile)
        self._store()
        self.refresh_list(select=len(self.profiles))

    def on_edit(self) -> None:
        """Edit or delete the selected profile.

        The dialog is handed every name including the one being edited; it drops
        that one itself, and doing it here as well would be a second copy of a
        case-insensitive comparison that has already been got right once.
        """
        if self.busy:
            return
        index = self._selected_index()
        profile = self.selected_profile()
        if index is None or profile is None:
            return

        result = ProfileDialog(self, profile=profile, other_names=self._names()).show()
        if result.action == "save" and result.profile is not None:
            self.profiles[index - 1] = result.profile
            self._store()
            self.refresh_list(select=index)
        elif result.action == "delete":
            del self.profiles[index - 1]
            self._store()
            # Back to DHCP: it is the one row that is certainly still there, and
            # the one the operator most often wants next.
            self.refresh_list(select=0)

    def on_apply(self, _event: tk.Event | None = None) -> None:
        """Hand the selected row to the adapter, on a thread of its own.

        Every gate the buttons express is checked again here, because this also
        arrives from a double-click and from Enter, neither of which knows what a
        disabled button is.
        """
        if self.busy or self.adapter is None or not self.elevated:
            return
        index = self._selected_index()
        if index is None:
            return
        profile = self.selected_profile()
        if index > 0 and profile is None:
            return  # a row that no longer names anything

        self.attempt += 1
        self.busy = True
        self.on_selection_changed()
        self._set_status(f"Ustawiam {DHCP_LABEL if profile is None else profile.label}…")

        # Armed before the thread runs, not after. A start() that raises — the OS
        # refusing another thread — would otherwise leave the buttons disabled
        # with nothing scheduled to give them back, which is the one state the
        # watchdog exists to make impossible.
        self.watchdog = self.after(WATCHDOG_MS, self.on_no_answer, self.attempt)
        # daemon, so a netsh wedged inside a driver call cannot keep the process
        # alive after the operator closes the window.
        self.worker = threading.Thread(
            target=self._work, args=(self.attempt, self.adapter, profile), daemon=True
        )
        self.worker.start()

    # -- the worker and what comes back from it ------------------------------------

    def _work(self, token: int, target: AdapterState, profile: Profile | None) -> None:
        """The worker thread. Touches no widget, and never raises.

        An exception escaping here would kill the thread with the buttons still
        disabled and nothing on the status line, so every failure comes back as an
        Outcome like any other. `after` is the only Tk call in it, and the only one
        that is safe to make from here.
        """
        try:
            outcome = apply_profile(target, profile)
        except Exception as error:
            outcome = Outcome(False, f"{APPLY_FAILED}: {error}")
        # A window torn down while this ran has nothing left to be told, and a
        # worker thread has nobody to report that to either.
        with contextlib.suppress(RuntimeError, tk.TclError):
            self.after(0, self.on_applied, token, outcome)

    def on_applied(self, token: int, outcome: Outcome) -> None:
        """The worker's answer, back on the UI thread.

        A token from an abandoned attempt is dropped: the operator has started
        another one since, and its message is the one that describes the window.
        An answer that arrives after the watchdog gave up is not abandoned — it is
        the truth catching up with a guess — so it is shown.
        """
        if token != self.attempt:
            return
        self._cancel_watchdog()
        self.busy = False
        # The card first and the answer after it. Refreshing can put the opening
        # line back up when the set of cards moved while the worker was out, and
        # the answer is the thing USTAW was pressed to read: it goes on last so
        # that nothing else can land on top of it.
        self.refresh_current()
        self._set_status(outcome.message, error=not outcome.ok)
        self.on_selection_changed()

    def on_no_answer(self, token: int) -> None:
        """Give the window back to the operator when the worker has not answered.

        `run_netsh` times out at twenty seconds, but its own docstring says that is
        a budget and not a ceiling: on Windows the timeout kills the direct child
        and then waits on every inherited pipe handle, so a netsh that spawned
        anything, or wedged inside a driver call, outlives it. Without this the
        buttons would stay disabled for the rest of the session.

        The attempt keeps its token, so the real answer still lands if it ever
        comes — see `on_applied`.
        """
        if token != self.attempt or not self.busy:
            return
        self.watchdog = None
        self.busy = False
        self._set_status(NO_ANSWER, error=True)
        self.on_selection_changed()

    def _cancel_watchdog(self) -> None:
        if self.watchdog is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self.watchdog)
            self.watchdog = None

    # -- the timer -----------------------------------------------------------------

    def on_tick(self) -> None:
        """Every two seconds: what the card carries, and what the buttons may do.

        The second half matters as much as the first. A USB adapter unplugged mid
        session leaves USTAW pointing at nothing, and the window would go on
        offering it until something else happened to refresh the buttons.
        """
        self.refresh_current()
        self.on_selection_changed()
        self.ticker = self.after(REFRESH_MS, self.on_tick)

    # -- the bits the methods above lean on ----------------------------------------

    def _names(self) -> list[str]:
        return [profile.name for profile in self.profiles]

    def _selected_index(self) -> int | None:
        selection = self.listbox.curselection()
        return int(selection[0]) if selection else None

    def _select(self, index: int) -> None:
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)
        self.on_selection_changed()

    def _resolve_adapter(self) -> AdapterState | None:
        """The chosen adapter as it stands now, or the first one when it is gone.

        The choice is kept as a GUID rather than as the record itself: every
        refresh builds new records, and the friendly name — the thing netsh
        matches on — is the one part of an adapter that can change while the
        window is open.
        """
        wanted = self.chosen.get()
        for candidate in self.adapters:
            if candidate.guid == wanted:
                return candidate
        return self.adapters[0] if self.adapters else None

    def _use_adapter(self) -> None:
        """Point the window at the card it should be using, and say which that is.

        `chosen` is what the picker's radio buttons read and what the resolution
        above matches on. Leaving it naming a card that has gone would tick
        nothing in the menu, and would hand the card back the instant it
        returned — moving USTAW's target with nobody asking, between the moment a
        profile is chosen and the moment it is clicked. So the card in use
        changes on two events and no others: the operator picking another one,
        and the one in use disappearing.

        The choice on file is not touched here. That one is what the operator
        picked, and a cable out for a minute must not be what forgets it.
        """
        self.adapter = self._resolve_adapter()
        if self.adapter is not None:
            self.chosen.set(self.adapter.guid)

    def _store(self) -> None:
        """Write the list out, and say so when it did not get there.

        A silent failure here is the one that costs the operator the profile they
        just typed, and they would not find out until the next launch.
        """
        if not save_profiles(self.profiles, profiles_path()):
            self._set_status(SAVE_FAILED, error=True)

    def _announce(self) -> None:
        """The opening status line: the most blocking thing first."""
        if self.adapter is None:
            self._set_status(NO_ADAPTER, error=True)
        elif not self.elevated:
            self._set_status(NOT_ELEVATED, error=True)
        elif self.complaint is not None:
            self._set_status(self.complaint, error=True)
        else:
            self._set_status(READY)

    def _reannounce(self) -> None:
        """Say that line again when a card has arrived, or the last one has left.

        It is ranked by what stops the window working, and a card appearing or
        disappearing moves the top of that ranking. Without this, a window opened
        with no card goes on saying so while *Teraz* names an address and USTAW
        is live, and a window opened with one says nothing at all when it goes.

        Only that one fact is watched, and only while nothing is in flight. Every
        other line the window shows was put there by something the operator did —
        an apply's answer, a list that would not save — and speaking over one of
        those would take away the answer they are in the middle of reading.
        """
        present = self.adapter is not None
        if self.busy or present == self.announced_card:
            return
        self.announced_card = present
        self._announce()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        """Put *text* on the status line, in the tone that says how to take it."""
        self.status.configure(
            text=fit(text, self.measure, STATUS_WIDTH),
            foreground=theme.DANGER if error else theme.TEXT_SECONDARY,
        )

    def _show_state(self) -> None:
        """Show the adapter in use and what it is carrying."""
        name = self.adapter.name if self.adapter is not None else "—"
        # The chevron is part of the label. Dropdown.TMenubutton has no arrow
        # element of its own: clam's combobox keeps a light grey fill behind its
        # arrow that no styling reaches, so the theme dropped the element rather
        # than the colour.
        self.adapter_picker.configure(text=f"{name}   ▾")
        self.current.configure(text=state_text(self.adapter))

    # -- building it ---------------------------------------------------------------

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=PADDING)
        frame.pack(fill="both", expand=True)
        # One column, held at a width that no message can change. Every row that
        # has two ends is a frame of its own, so the status line wrapping cannot
        # move the *Teraz* value around.
        frame.columnconfigure(0, weight=1, minsize=CONTENT_WIDTH)

        self._build_adapter_row(frame, row=0, separator_row=1)
        self._build_list(frame, row=2)
        self._build_buttons(frame, row=3)
        ttk.Separator(frame).grid(row=4, column=0, sticky="ew", pady=(14, 10))
        self._build_current(frame, row=5)

        self.status = ttk.Label(
            frame,
            style="Secondary.TLabel",
            wraplength=CONTENT_WIDTH,
            justify="left",
            anchor="nw",
        )
        self.status.grid(row=6, column=0, sticky="ew", pady=(STATUS_GAP, 0))
        # Reserved from the label holding the tallest thing `fit` can ever hand
        # it — run through `fit` itself, so the worst case is the real one rather
        # than a guess at it — and measured off the label rather than off the
        # font, because ttk adds four pixels of its own that a font-based sum
        # leaves out. Either mistake is a window that grows the first time
        # something goes wrong.
        self.status.configure(text=fit("W" * 400, self.measure, STATUS_WIDTH))
        frame.rowconfigure(6, minsize=STATUS_GAP + self.status.winfo_reqheight())
        self.status.configure(text="")

    def _build_adapter_row(self, frame: ttk.Frame, *, row: int, separator_row: int) -> None:
        """The adapter picker and the rule under it, built once and shown as needed.

        Built whether or not there is anything to choose between, and taken out of
        sight by the grid rather than by not existing. A card plugged in after the
        window opened has to bring the picker out with it — a window that goes on
        applying profiles to whichever card it found at startup, and never names
        which one that is, is the failure this shape exists to prevent — and
        building it once means nothing is created or destroyed under a posted
        menu, and the picker's disabled-while-applying state has a single owner in
        `on_selection_changed` instead of being re-established by whatever built
        the widget last.
        """
        self.adapter_row = ttk.Frame(frame)
        self.adapter_row.grid(row=row, column=0, sticky="ew")
        ttk.Label(self.adapter_row, text="Karta", style="Secondary.TLabel").pack(side="left")

        self.adapter_picker = ttk.Menubutton(
            self.adapter_row, style="Dropdown.TMenubutton", direction="below"
        )
        self.adapter_menu = tk.Menu(self.adapter_picker, **theme.menu_options())
        # Filled again every time it opens, so an adapter that arrived or left
        # since the window did is in the list the operator is looking at.
        self.adapter_menu.configure(postcommand=self._fill_adapter_menu)
        self.adapter_picker["menu"] = self.adapter_menu
        self.adapter_picker.pack(side="left", padx=(12, 0))
        self._fill_adapter_menu()

        # Only under something. A rule at the top of a window with nothing above
        # it separates one thing from the title bar.
        self.adapter_separator = ttk.Separator(frame)
        self.adapter_separator.grid(row=separator_row, column=0, sticky="ew", pady=(14, 0))
        self._sync_adapter_row()

    def _sync_adapter_row(self) -> None:
        """Show the picker exactly while there is a choice to make.

        One Ethernet card is the normal machine, and a dropdown with one entry is
        a control that answers a question nobody asked. With none there is nothing
        to put in it, and the status line says so instead.

        grid_remove is what hides them, because it keeps the grid options it was
        given: showing the row again puts it back where it was rather than
        wherever a second set of options happened to say.
        """
        wanted = len(self.adapters) > 1
        if wanted == bool(self.adapter_row.grid_info()):
            return
        for widget in (self.adapter_row, self.adapter_separator):
            if wanted:
                widget.grid()
            else:
                widget.grid_remove()

    def _fill_adapter_menu(self) -> None:
        self.adapter_menu.delete(0, tk.END)
        for candidate in self.adapters:
            self.adapter_menu.add_radiobutton(
                label=candidate.name,
                value=candidate.guid,
                variable=self.chosen,
                command=self.on_adapter_chosen,
            )

    def _build_list(self, frame: ttk.Frame, *, row: int) -> None:
        # exportselection=False: without it the row loses its highlight the moment
        # the dialog takes the X selection, and EDYTUJ would come back to a window
        # that has forgotten what it was editing.
        self.listbox = tk.Listbox(
            frame, height=LIST_HEIGHT, exportselection=False, **theme.listbox_options()
        )
        self.listbox.grid(row=row, column=0, sticky="ew", pady=(12, 0))
        self.listbox.bind("<<ListboxSelect>>", self.on_selection_changed)
        # Double-click is the shortcut the sketch asked for; Enter is the same
        # gesture for a technician who is already on the keyboard, and the numeric
        # keypad's Enter is a different keysym.
        self.listbox.bind("<Double-Button-1>", self.on_apply)
        self.listbox.bind("<Return>", self.on_apply)
        self.listbox.bind("<KP_Enter>", self.on_apply)

    def _build_buttons(self, frame: ttk.Frame, *, row: int) -> None:
        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, sticky="ew", pady=(12, 0))
        # The empty middle column takes the slack, which pins the everyday pair
        # left and the one that changes the network right.
        buttons.columnconfigure(2, weight=1)

        self.add_button = ttk.Button(
            buttons, text="DODAJ", style="Secondary.TButton", command=self.on_add
        )
        self.add_button.grid(row=0, column=0)
        self.edit_button = ttk.Button(
            buttons, text="EDYTUJ", style="Secondary.TButton", command=self.on_edit
        )
        self.edit_button.grid(row=0, column=1, padx=(8, 0))
        self.apply_button = ttk.Button(
            buttons, text="USTAW", style="Accent.TButton", command=self.on_apply
        )
        self.apply_button.grid(row=0, column=3)

    def _build_current(self, frame: ttk.Frame, *, row: int) -> None:
        current_row = ttk.Frame(frame)
        current_row.grid(row=row, column=0, sticky="ew")
        current_row.columnconfigure(1, weight=1)
        ttk.Label(current_row, text="Teraz", style="Secondary.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        # Monospaced, through Value.TLabel: the operator compares this against an
        # address they typed, digit by digit.
        self.current = ttk.Label(current_row, style="Value.TLabel", anchor="e")
        self.current.grid(row=0, column=1, sticky="e")
