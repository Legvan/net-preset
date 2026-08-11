import contextlib
import sys
import threading
import time
import tkinter as tk

import pytest
from conftest import new_root

from net_preset.adapters import AdapterState
from net_preset.app import (
    CONTENT_WIDTH,
    DHCP_LABEL,
    NO_ADAPTER,
    NO_ADDRESS,
    NO_ANSWER,
    NO_CARD,
    NO_LEASE,
    NOT_ELEVATED,
    READY,
    STATUS_LINES,
    Application,
    fit,
    list_entries,
    state_text,
)
from net_preset.apply import Outcome
from net_preset.dialog import DialogResult
from net_preset.profile import Profile
from net_preset.settings import save_adapter_choice
from net_preset.theme import DANGER, TEXT_SECONDARY

ONE = Profile("ROGER", "192.168.11.2", "255.255.255.0")
TWO = Profile("BIURO", "10.0.0.5", "255.255.255.0")
APPLIED = Outcome(True, "Ustawiono 192.168.11.2 /24")


def test_dhcp_heads_the_list():
    assert list_entries([]) == [DHCP_LABEL]


def test_profiles_follow_dhcp_in_order():
    assert list_entries([ONE, TWO]) == [
        DHCP_LABEL,
        "192.168.11.2 (ROGER)",
        "10.0.0.5 (BIURO)",
    ]


windowed = pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows display")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from net_preset import app as module

    monkeypatch.setattr(module, "profiles_path", lambda: tmp_path / "profiles.json")
    monkeypatch.setattr(module, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(module, "ethernet_adapters", lambda: [])
    # new_root carries the retry that tells a display-less machine apart from the
    # transient Tcl read failure this one throws; see tests/conftest.py.
    window = new_root(Application)
    window.withdraw()
    yield window
    window.destroy()


@windowed
def test_the_window_opens_with_dhcp_selected(app):
    assert app.listbox.get(0) == DHCP_LABEL
    assert app.listbox.curselection() == (0,)


@windowed
def test_dhcp_is_selected_as_no_profile(app):
    assert app.selected_profile() is None


@windowed
def test_editing_is_refused_on_the_dhcp_row(app):
    assert str(app.edit_button.cget("state")) == "disabled"


@windowed
def test_applying_is_offered_on_the_dhcp_row(app):
    # With no adapter present it stays disabled; that is the next test.
    assert app.apply_button is not None


@windowed
def test_without_an_adapter_the_buttons_that_touch_it_are_disabled(app):
    assert str(app.apply_button.cget("state")) == "disabled"
    assert "Ethernet" in app.status.cget("text") or "karty" in app.status.cget("text")


@windowed
def test_a_saved_profile_appears_in_the_list(app):
    app.profiles = [ONE]
    app.refresh_list()
    assert app.listbox.get(1) == "192.168.11.2 (ROGER)"


@windowed
def test_selecting_a_profile_enables_editing(app):
    app.profiles = [ONE]
    app.refresh_list()
    app.listbox.selection_clear(0, tk.END)
    app.listbox.selection_set(1)
    app.on_selection_changed()
    assert str(app.edit_button.cget("state")) == "normal"
    assert app.selected_profile() == ONE


@windowed
def test_the_adapter_picker_is_hidden_with_a_single_adapter(app, monkeypatch):
    assert app.adapter_picker is None or not app.adapter_picker.winfo_ismapped()


# -- everything below is beyond the brief's set, which leaves the adapter, the
# -- dialog, saving and the worker thread untested -----------------------------


def adapter(
    guid="{AAAA}",
    name="Ethernet",
    *,
    addresses=(("192.168.11.9", 24),),
    connected=True,
    dhcp=False,
):
    """One fabricated adapter, shaped the way GetAdaptersAddresses reports them."""
    return AdapterState(
        guid=guid,
        name=name,
        description=name,
        if_index=7,
        if_type=6,
        connected=connected,
        dhcp=dhcp,
        addresses=tuple(addresses),
        gateways=(),
        dns=(),
    )


class FakeApply:
    """A stand-in for apply_profile that records the call and runs no netsh."""

    def __init__(self, outcome=APPLIED, gate=None):
        self.outcome = outcome
        self.gate = gate
        self.calls = []

    def __call__(self, target, profile, **_kwargs):
        self.calls.append((target, profile))
        if self.gate is not None:
            assert self.gate.wait(timeout=5), "the test never released the worker"
        return self.outcome


def pump(window, until, timeout=5.0):
    """Run the window's own event loop until *until* holds, or time runs out.

    One uninterrupted loop, quit from inside itself. The worker hands its answer
    over with `after(0, ...)`, and Tkinter only lets a call from another thread
    through while the loop is actually running -- so a loop run in slices would
    drop the answer whenever it landed in a gap between two of them, and update()
    alone would never see it at all.
    """
    deadline = time.monotonic() + timeout

    def check():
        if until() or time.monotonic() >= deadline:
            window.quit()
        else:
            window.after(10, check)

    window.after(10, check)
    window.mainloop()


def apply_and_settle(window, timeout=5.0):
    """Press USTAW from inside the running loop, and wait for the answer.

    From inside, because that is where a button command runs, and because the
    worker needs the loop running to hand anything back to it.

    A few turns in, not on the first. A worker with nothing to wait for -- the
    fakes here answer instantly -- can reach its `after(0, ...)` before the loop
    has settled into servicing events, and a cross-thread `after` that lands then
    never comes back: the worker blocks in Tcl_ConditionWait for an event nobody
    runs, `pump` spends its whole timeout, and the answer is simply lost. It cost
    an existing test a deterministic failure once four more worker tests had
    shifted the timing around it. Nothing in the shipped window can meet this:
    its loop has been running since before the operator saw it. Tests that hold
    the worker on a gate are safe already, the gate being released from inside
    the loop; this is the path with no gate to do that.

    `pressed` is in the condition because `not window.busy` is true before USTAW
    has been pressed at all, and waiting would otherwise end before it started.
    """
    pressed = []

    def press():
        window.on_apply()
        pressed.append(True)

    window.after(30, press)
    pump(window, lambda: pressed and not window.busy, timeout)


def fake_dialog(answer):
    """A stand-in for ProfileDialog that opens nothing and answers *answer*."""
    opened = []

    class Dialog:
        def __init__(self, parent, *, profile=None, other_names=()):
            opened.append((parent, profile, tuple(other_names)))

        def show(self):
            return answer

    Dialog.opened = opened
    return Dialog


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Build a window over a fabricated adapter set, a temporary file and no netsh."""
    from net_preset import app as module

    built = []

    def build(adapters=(), *, elevated=True, applier=None):
        monkeypatch.setattr(module, "profiles_path", lambda: tmp_path / "profiles.json")
        monkeypatch.setattr(module, "settings_path", lambda: tmp_path / "settings.json")
        monkeypatch.setattr(module, "ethernet_adapters", lambda: list(adapters))
        monkeypatch.setattr(module, "is_elevated", lambda: elevated)
        # Never the real one: a test that reached it would reconfigure the machine
        # this suite is running on.
        monkeypatch.setattr(module, "apply_profile", applier or FakeApply())
        window = new_root(Application)
        window.withdraw()
        built.append(window)
        return window

    yield build
    for window in built:
        # A test may close its own window; a second destroy is a TclError.
        with contextlib.suppress(tk.TclError):
            window.destroy()


def test_the_current_line_names_the_address():
    assert state_text(adapter(addresses=(("10.0.0.5", 16),))) == "10.0.0.5 /16"


def test_the_current_line_says_when_there_is_no_card():
    assert state_text(None) == NO_CARD


def test_the_current_line_says_when_the_card_has_no_address():
    assert state_text(adapter(addresses=())) == NO_ADDRESS


def test_the_current_line_calls_an_apipa_address_a_missing_lease():
    assert state_text(adapter(addresses=(("169.254.7.7", 16),), dhcp=True)) == NO_LEASE


# -- fitting a message to the status line --------------------------------------
#
# A fake font, seven pixels to the character, so these say what `fit` decides
# rather than what Segoe measures. The window tests further down use the real one.


def seven(text):
    return 7 * len(text)


def test_a_short_message_is_left_alone():
    assert fit("Gotowy", seven, 700) == "Gotowy"


def test_whitespace_is_folded_before_anything_else():
    assert fit("Nie udało\n  się   wykonać", seven, 700) == "Nie udało się wykonać"


def test_a_message_wraps_at_the_width_it_is_given():
    assert fit("aaa bbb ccc ddd", seven, 7 * 7) == "aaa bbb\nccc ddd"


def test_what_does_not_fit_in_the_last_line_is_cut():
    folded = fit("aaa bbb ccc ddd eee", seven, 7 * 7, lines=2)
    assert folded == "aaa bbb\nccc dd…"
    assert all(seven(row) <= 7 * 7 for row in folded.splitlines())


def test_a_token_too_wide_for_a_line_is_broken_rather_than_left_to_overflow():
    # Tk would leave this one whole and make the label wider than the window.
    folded = fit("a" * 20, seven, 7 * 7, lines=3)
    assert folded == "aaaaaaa\naaaaaaa\naaaaaa"


def test_a_long_unbroken_token_is_cut_like_anything_else():
    folded = fit("C:/" + "x" * 400, seven, 7 * 7, lines=2)
    assert folded.count("\n") == 1
    assert folded.endswith("…")
    assert all(seven(row) <= 7 * 7 for row in folded.splitlines())


def test_a_width_narrower_than_a_glyph_still_terminates():
    # _longest_prefix never answers zero, or this would never come back. The
    # ellipsis eats the whole last line here, which is all a width of one pixel
    # can be given.
    assert fit("abc", seven, 1, lines=2) == "a\n…"


@windowed
def test_the_current_line_shows_the_live_adapter(make_app):
    window = make_app([adapter(addresses=(("10.0.0.5", 8),))])
    assert window.current.cget("text") == "10.0.0.5 /8"


# Every one of these grew the window when the status line was cut by character
# count: 110 characters of Polish capitals and of wide words each took a third
# line, and an unbroken token took a third line and made the label wider than the
# window as well. The last is the reachable production route — an OSError with a
# long path in it, folded into a message by the worker's own except branch.
GROWERS = [
    "ŁÓDŹŻŚĆŃ" * 14,
    "WMWM " * 22,
    "W" * 110,
    "Nie udało się zmienić ustawień: [WinError 3] " + "C:/" + "katalogu/" * 40 + "plik.json",
]


@windowed
@pytest.mark.parametrize("message", GROWERS)
def test_a_message_that_used_to_grow_the_window_no_longer_does(make_app, message):
    window = make_app([adapter()])
    window.update_idletasks()
    at_rest = (window.winfo_reqwidth(), window.winfo_reqheight())

    # The route a netsh refusal really takes into the window.
    window.on_applied(window.attempt, Outcome(False, message))
    window.update_idletasks()

    assert (window.winfo_reqwidth(), window.winfo_reqheight()) == at_rest
    assert window.status.winfo_reqwidth() <= CONTENT_WIDTH
    assert window.status.cget("text").count("\n") + 1 <= STATUS_LINES


@windowed
def test_an_adapter_and_a_token_are_what_applying_needs(make_app):
    window = make_app([adapter()])
    assert str(window.apply_button.cget("state")) == "normal"
    assert window.status.cget("text") == "Gotowy"


@windowed
def test_without_administrator_rights_applying_is_refused(make_app):
    window = make_app([adapter()], elevated=False)
    assert str(window.apply_button.cget("state")) == "disabled"
    assert window.status.cget("text") == NOT_ELEVATED


@windowed
def test_going_back_to_the_dhcp_row_disables_editing_again(make_app):
    window = make_app([adapter()])
    window.profiles = [ONE]
    window.refresh_list(select=1)
    assert str(window.edit_button.cget("state")) == "normal"
    window.refresh_list(select=0)
    assert str(window.edit_button.cget("state")) == "disabled"


@windowed
def test_rebuilding_the_list_leaves_the_selection_where_it_was(make_app):
    window = make_app([adapter()])
    window.profiles = [ONE, TWO]
    window.refresh_list(select=2)
    window.refresh_list()
    assert window.listbox.curselection() == (2,)


@windowed
def test_a_selection_past_the_end_of_the_list_names_no_profile(make_app):
    window = make_app([adapter()])
    window.profiles = [ONE]
    window.refresh_list(select=1)
    window.profiles = []
    assert window.selected_profile() is None


BAD_FILE = '{"profiles": [{"name": "", "address": "x"}]}'


@windowed
def test_a_complaint_from_the_stored_file_reaches_the_status_line(tmp_path, make_app):
    (tmp_path / "profiles.json").write_text(BAD_FILE)
    window = make_app([adapter()])
    assert "Pominięto" in window.status.cget("text")
    assert str(window.status.cget("foreground")) == DANGER


@windowed
def test_a_missing_card_is_said_before_a_complaint_about_the_file(tmp_path, make_app):
    # Both are true at once here. The one that stops the window doing anything
    # goes first; the note describes something already lost.
    (tmp_path / "profiles.json").write_text(BAD_FILE)
    window = make_app([])
    assert window.status.cget("text") == NO_ADAPTER


@windowed
def test_a_missing_token_is_said_before_a_complaint_about_the_file(tmp_path, make_app):
    (tmp_path / "profiles.json").write_text(BAD_FILE)
    window = make_app([adapter()], elevated=False)
    assert window.status.cget("text") == NOT_ELEVATED


@windowed
def test_the_row_that_is_showing_is_the_row_that_is_active(make_app):
    # Where the keyboard picks up from: Tk moves the active row with the arrow
    # keys, and leaving it behind the selection means the first Down key jumps
    # somewhere the operator was not.
    window = make_app([adapter()])
    window.profiles = [ONE, TWO]
    window.refresh_list(select=2)
    assert window.listbox.index("active") == 2


# -- the adapter picker --------------------------------------------------------


@windowed
def test_one_adapter_needs_no_picker(make_app):
    # The brief's test above is answered by the fixture's empty adapter list, so
    # it cannot tell "fewer than two" from "none at all". This one can.
    #
    # The picker is built either way and hidden by the grid, so what is asked
    # here is whether it is laid out -- grid_info() is empty for a widget that
    # grid_remove took away. winfo_ismapped cannot answer it: this window is
    # withdrawn, so nothing in it is mapped and the question passes vacuously.
    window = make_app([adapter()])
    assert window.adapter_row.grid_info() == {}


@windowed
def test_two_adapters_bring_out_the_picker(make_app):
    window = make_app([adapter(), adapter("{BBBB}", "Ethernet 2")])
    assert window.adapter_row.grid_info() != {}
    assert window.adapter_picker.cget("text") == "Ethernet   ▾"


@windowed
def test_a_card_arriving_brings_the_picker_out_with_it(make_app):
    # The window is open from the last site and a USB adapter goes in. Without
    # this the picker never appears, USTAW keeps pointing at the built-in card,
    # and the window reports success on a card the controller is not on.
    cards = [adapter()]
    window = make_app(cards)
    assert window.adapter_row.grid_info() == {}

    cards.append(adapter("{BBBB}", "Ethernet 2"))
    window.on_tick()

    assert window.adapter_row.grid_info() != {}
    assert window.adapter_separator.grid_info() != {}
    # And it names the card the click would go to. What the menu behind it holds
    # is settled when it opens, by the postcommand, not here.
    assert window.adapter_picker.cget("text") == "Ethernet   ▾"


@windowed
def test_the_last_of_two_cards_leaving_puts_the_picker_away(make_app):
    cards = [adapter(), adapter("{BBBB}", "Ethernet 2")]
    window = make_app(cards)
    assert window.adapter_row.grid_info() != {}

    del cards[1]
    window.on_tick()

    assert window.adapter_row.grid_info() == {}
    assert window.adapter_separator.grid_info() == {}


@windowed
def test_the_card_in_use_disappearing_takes_the_choice_with_it(tmp_path, make_app):
    # The case to get right. The picker, the menu's tick and the card USTAW acts
    # on have to name the same thing afterwards, and the choice on file has to
    # survive: a cable out for a minute must not be what forgets it.
    save_adapter_choice("{BBBB}", tmp_path / "settings.json")
    cards = [adapter(), adapter("{BBBB}", "Ethernet 2")]
    window = make_app(cards)
    assert window.adapter.guid == "{BBBB}"

    del cards[1]
    window.on_tick()

    assert window.adapter.guid == "{AAAA}"
    assert window.chosen.get() == "{AAAA}"
    assert window.adapter_picker.cget("text") == "Ethernet   ▾"
    assert "{BBBB}" in (tmp_path / "settings.json").read_text(encoding="utf-8")


@windowed
def test_a_card_that_comes_back_does_not_take_the_choice_off_the_one_in_use(make_app):
    # USTAW's target moves when the operator picks another card, and when the one
    # in use goes away. Never on its own: a card reappearing between choosing a
    # profile and clicking USTAW would send the click somewhere nobody asked for.
    cards = [adapter(), adapter("{BBBB}", "Ethernet 2")]
    window = make_app(cards)
    window.adapter_menu.invoke(1)
    assert window.adapter.guid == "{BBBB}"

    del cards[1]
    window.on_tick()
    assert window.adapter.guid == "{AAAA}"

    cards.append(adapter("{BBBB}", "Ethernet 2"))
    window.on_tick()
    assert window.adapter.guid == "{AAAA}"
    assert window.adapter_picker.cget("text") == "Ethernet   ▾"


@windowed
def test_the_last_card_leaving_keeps_its_name_for_when_it_comes_back(tmp_path, make_app):
    # A dock comes off a laptop and both cards go with it. `chosen` is deliberately left
    # naming the card that has gone -- `_use_adapter` writes it only when there is one to
    # write -- and that is what hands the same card back when the dock returns, instead
    # of whichever one the API happens to list first. The two-card half of that property
    # is pinned above; clearing it on the way to nothing was not, and survived the suite.
    save_adapter_choice("{BBBB}", tmp_path / "settings.json")
    cards = [adapter(), adapter("{BBBB}", "Ethernet 2")]
    window = make_app(cards)
    assert window.adapter.guid == "{BBBB}"

    cards.clear()
    window.on_tick()
    assert window.adapter is None
    assert window.chosen.get() == "{BBBB}"

    cards.extend([adapter(), adapter("{BBBB}", "Ethernet 2")])
    window.on_tick()
    assert window.adapter.guid == "{BBBB}"
    assert window.adapter_picker.cget("text") == "Ethernet 2   ▾"


@windowed
def test_a_card_arriving_replaces_the_line_that_said_there_was_none(make_app):
    # The second contradiction the startup-only announcement produced: the status
    # line still refusing the card that Teraz is already reading an address off.
    cards = []
    window = make_app(cards)
    assert window.status.cget("text") == NO_ADAPTER

    cards.append(adapter())
    window.on_tick()

    assert window.status.cget("text") == READY
    assert str(window.status.cget("foreground")) == TEXT_SECONDARY
    assert str(window.apply_button.cget("state")) == "normal"


@windowed
def test_a_card_arriving_says_whatever_it_was_outranking(tmp_path, make_app):
    # The complaint about the stored file lost to there being no card at all.
    # Once there is one, the complaint is what is left to say.
    (tmp_path / "profiles.json").write_text(BAD_FILE)
    cards = []
    window = make_app(cards)
    assert window.status.cget("text") == NO_ADAPTER

    cards.append(adapter())
    window.on_tick()

    assert "Pominięto" in window.status.cget("text")


@windowed
def test_the_last_card_leaving_says_so(make_app):
    cards = [adapter()]
    window = make_app(cards)
    assert window.status.cget("text") == READY

    cards.clear()
    window.on_tick()

    assert window.status.cget("text") == NO_ADAPTER
    assert str(window.status.cget("foreground")) == DANGER


@windowed
def test_a_card_arriving_speaks_over_a_note_that_the_list_would_not_save(monkeypatch, make_app):
    # Documented rather than prevented; see `_reannounce`. Only an apply in flight holds
    # the line, and SAVE_FAILED is written with `busy` already false, so the ranking
    # takes it on the next tick that moves a card. Pinned so that the docstring and the
    # window cannot drift apart, and so the cost is a decision rather than a surprise.
    from net_preset import app as module

    monkeypatch.setattr(module, "ProfileDialog", fake_dialog(DialogResult("save", ONE)))
    monkeypatch.setattr(module, "save_profiles", lambda profiles, path=None: False)
    cards = []
    window = make_app(cards)
    window.on_add()
    assert "zapisać" in window.status.cget("text")

    cards.append(adapter())
    window.on_tick()

    assert window.status.cget("text") == READY


@windowed
def test_a_card_going_mid_apply_leaves_the_line_the_worker_was_given(make_app):
    # The tick runs all the way through an apply, and the line saying what is
    # being set is what the operator is waiting on. Announcing over it would take
    # it away before the answer arrived to replace it.
    gate = threading.Event()
    cards = [adapter()]
    window = make_app(cards, applier=FakeApply(gate=gate))
    try:
        window.on_apply()
        cards.clear()
        window.on_tick()
        assert window.status.cget("text") == f"Ustawiam {DHCP_LABEL}…"
    finally:
        gate.set()
        window.worker.join(timeout=5)


@windowed
def test_the_answer_outlives_a_card_that_went_while_it_was_being_fetched(make_app):
    # Same collision, resolved the other way round once the worker has answered:
    # the answer is the last thing written, so the re-announcement cannot land on
    # top of it.
    gate = threading.Event()
    cards = [adapter()]
    window = make_app(cards, applier=FakeApply(Outcome(False, "Karta ma 10.0.0.5 /24"), gate))
    window.after(0, window.on_apply)
    window.after(20, cards.clear)
    window.after(30, gate.set)
    try:
        pump(window, lambda: not window.busy)
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert window.status.cget("text") == "Karta ma 10.0.0.5 /24"
    assert window.current.cget("text") == NO_CARD


@windowed
def test_the_remembered_adapter_is_the_one_that_opens(tmp_path, make_app):
    save_adapter_choice("{BBBB}", tmp_path / "settings.json")
    window = make_app([adapter(), adapter("{BBBB}", "Ethernet 2")])
    assert window.adapter.guid == "{BBBB}"
    assert window.adapter_picker.cget("text") == "Ethernet 2   ▾"


@windowed
def test_a_remembered_adapter_that_is_gone_falls_back_to_the_first(tmp_path, make_app):
    save_adapter_choice("{GONE}", tmp_path / "settings.json")
    window = make_app([adapter(), adapter("{BBBB}", "Ethernet 2")])
    assert window.adapter.guid == "{AAAA}"


@windowed
def test_the_picker_is_out_of_reach_while_an_apply_is_in_flight(make_app):
    # The worker holds the card it was handed, so a switch mid-apply cannot go to
    # the wrong card — but the answer would land under a picker naming another
    # one, which reads as a result about the card the operator is now looking at.
    gate = threading.Event()
    window = make_app([adapter(), adapter("{BBBB}", "Ethernet 2")], applier=FakeApply(gate=gate))
    seen = []
    window.after(0, window.on_apply)
    window.after(10, lambda: seen.append(str(window.adapter_picker.cget("state"))))
    window.after(20, gate.set)
    try:
        pump(window, lambda: not window.busy)
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert seen == ["disabled"]
    assert str(window.adapter_picker.cget("state")) == "normal"


@windowed
def test_the_picker_row_does_not_move_under_an_apply(make_app):
    # The wave froze the list and suppressed the re-announcement so that nothing shifts
    # while the operator waits. The picker row was the one thing left that could: it
    # appears and disappears on the one-to-two boundary, and it takes the window's
    # height with it -- 49 pixels of it, measured here. A dock coming off mid-apply
    # would move the buttons and the answer down the screen under a waiting hand.
    gate = threading.Event()
    cards = [adapter(), adapter("{BBBB}", "Ethernet 2")]
    window = make_app(cards, applier=FakeApply(gate=gate))
    window.update_idletasks()
    tall = window.winfo_reqheight()
    assert window.adapter_row.grid_info() != {}

    seen = []

    def disturb():
        cards.pop()
        window.on_tick()
        window.update_idletasks()
        seen.append((window.adapter_row.grid_info() != {}, window.winfo_reqheight()))

    window.after(0, window.on_apply)
    window.after(10, disturb)
    window.after(20, gate.set)
    try:
        pump(window, lambda: not window.busy)
    finally:
        gate.set()
        window.worker.join(timeout=5)

    assert seen == [(True, tall)]
    # And the answer's own refresh puts the row away, which is also what proves the
    # assertion above is about something: this window really does change height here.
    window.update_idletasks()
    assert window.adapter_row.grid_info() == {}
    assert window.winfo_reqheight() < tall


def show_invisibly(window):
    """Map the window without putting it on the screen of whoever runs the suite.

    A click means nothing to an unmapped listbox. With no geometry computed,
    `index @x,y` answers the same row whatever the coordinates, so a click that
    was delivered cannot be told from one that was refused -- measured, and it
    would have made the test below pass against a window that did nothing.
    Mapping makes the coordinates real; alpha 0 keeps the window out of sight.
    """
    window.attributes("-alpha", 0.0)
    window.deiconify()
    window.update()


def click_a_row(window):
    """Click the top row the way an operator does, and answer where the highlight is.

    A real click, because the freeze works by taking Tk's own Listbox bindings
    out of the widget's tag list: anything that goes round the bindings --
    selection_set, for one -- keeps working and would pass whatever the window
    did about it.

    One click per window and no more. Tk replays the first synthetic Button-1
    faithfully and then stops moving the selection for any that follow, on a
    window that was never frozen at all.
    """
    window.listbox.event_generate("<Button-1>", x=10, y=4)
    window.listbox.event_generate("<ButtonRelease-1>", x=10, y=4)
    return window.listbox.curselection()


@windowed
def test_a_click_moves_the_highlight_when_nothing_is_in_flight(make_app):
    # The control for the test below. Without it that one would pass just as well
    # on a click that never reached the list at all.
    window = make_app([adapter()])
    window.profiles = [ONE, TWO]
    window.refresh_list(select=2)
    show_invisibly(window)
    assert click_a_row(window) == (0,)


@windowed
def test_a_click_cannot_move_the_highlight_out_from_under_an_answer_in_flight(make_app):
    # BIURO is what was handed to the worker, so BIURO's address is what the
    # answer will name. A highlight that had moved by the time it landed would
    # have the operator reading a success line against the wrong row -- and
    # walking away believing the wrong profile is on the card.
    gate = threading.Event()
    window = make_app([adapter()], applier=FakeApply(gate=gate))
    window.profiles = [ONE, TWO]
    window.refresh_list(select=2)
    show_invisibly(window)
    seen = []
    window.after(0, window.on_apply)
    window.after(10, lambda: seen.append(click_a_row(window)))
    window.after(20, gate.set)
    try:
        pump(window, lambda: not window.busy)
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert seen == [(2,)]
    # and the list is out of the freeze once the answer is in
    assert window.listbox.winfo_class() in window.listbox.bindtags()


@windowed
def test_the_frozen_list_still_shows_which_row_is_being_applied(make_app):
    # Disabling the widget was tried first and photographed. Tk draws no
    # selection at all on a disabled listbox -- the highlight goes with
    # everything else -- so the row being applied lost its highlight for exactly
    # as long as the status line was talking about it.
    gate = threading.Event()
    window = make_app([adapter()], applier=FakeApply(gate=gate))
    window.profiles = [ONE, TWO]
    window.refresh_list(select=1)
    seen = []
    window.after(0, window.on_apply)
    window.after(
        10,
        lambda: seen.append((str(window.listbox.cget("state")), window.listbox.curselection())),
    )
    window.after(20, gate.set)
    try:
        pump(window, lambda: not window.busy)
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert seen == [("normal", (1,))]


@windowed
def test_choosing_an_adapter_remembers_it(tmp_path, make_app):
    window = make_app([adapter(), adapter("{BBBB}", "Ethernet 2")])
    window.adapter_menu.invoke(1)
    assert window.adapter.guid == "{BBBB}"
    assert (tmp_path / "settings.json").read_text(encoding="utf-8").count("{BBBB}") == 1


# -- the dialog ----------------------------------------------------------------


@windowed
def test_adding_a_profile_stores_it_and_selects_it(tmp_path, monkeypatch, make_app):
    from net_preset import app as module

    monkeypatch.setattr(module, "ProfileDialog", fake_dialog(DialogResult("save", ONE)))
    window = make_app([adapter()])
    window.on_add()
    assert window.profiles == [ONE]
    assert window.listbox.get(1) == ONE.label
    assert window.listbox.curselection() == (1,)
    assert "ROGER" in (tmp_path / "profiles.json").read_text(encoding="utf-8")


@windowed
def test_a_cancelled_dialog_changes_nothing(monkeypatch, make_app):
    from net_preset import app as module

    monkeypatch.setattr(module, "ProfileDialog", fake_dialog(DialogResult("cancel")))
    window = make_app([adapter()])
    window.on_add()
    assert window.profiles == []
    assert window.listbox.size() == 1


@windowed
def test_editing_replaces_the_profile_where_it_stood(tmp_path, monkeypatch, make_app):
    from net_preset import app as module

    changed = Profile("ROGER", "192.168.11.3", "255.255.255.0")
    dialog = fake_dialog(DialogResult("save", changed))
    monkeypatch.setattr(module, "ProfileDialog", dialog)
    window = make_app([adapter()])
    window.profiles = [ONE, TWO]
    window.refresh_list(select=1)
    window.on_edit()
    assert window.profiles == [changed, TWO]
    assert window.listbox.get(1) == changed.label
    assert dialog.opened[0][1] == ONE
    assert dialog.opened[0][2] == ("ROGER", "BIURO")
    assert "192.168.11.3" in (tmp_path / "profiles.json").read_text(encoding="utf-8")


@windowed
def test_deleting_removes_the_profile_and_returns_to_dhcp(monkeypatch, make_app):
    from net_preset import app as module

    monkeypatch.setattr(module, "ProfileDialog", fake_dialog(DialogResult("delete", ONE)))
    window = make_app([adapter()])
    window.profiles = [ONE, TWO]
    window.refresh_list(select=1)
    window.on_edit()
    assert window.profiles == [TWO]
    assert window.listbox.curselection() == (0,)


@windowed
def test_editing_the_dhcp_row_opens_nothing(monkeypatch, make_app):
    from net_preset import app as module

    dialog = fake_dialog(DialogResult("save", ONE))
    monkeypatch.setattr(module, "ProfileDialog", dialog)
    window = make_app([adapter()])
    window.profiles = [ONE]
    window.refresh_list(select=0)
    window.on_edit()
    assert dialog.opened == []


@windowed
def test_a_list_that_cannot_be_written_says_so(monkeypatch, make_app):
    from net_preset import app as module

    monkeypatch.setattr(module, "ProfileDialog", fake_dialog(DialogResult("save", ONE)))
    monkeypatch.setattr(module, "save_profiles", lambda profiles, path=None: False)
    window = make_app([adapter()])
    window.on_add()
    assert "zapisać" in window.status.cget("text")
    assert str(window.status.cget("foreground")) == DANGER


# -- applying ------------------------------------------------------------------


@windowed
def test_applying_disables_the_buttons_and_says_what_it_is_doing(make_app):
    gate = threading.Event()
    applier = FakeApply(gate=gate)
    window = make_app([adapter()], applier=applier)
    try:
        window.on_apply()
        assert window.status.cget("text") == f"Ustawiam {DHCP_LABEL}…"
        for button in (window.add_button, window.edit_button, window.apply_button):
            assert str(button.cget("state")) == "disabled"
        assert window.watchdog is not None
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert applier.calls == [(window.adapter, None)]


@windowed
def test_a_second_click_while_one_is_in_flight_is_ignored(make_app):
    gate = threading.Event()
    applier = FakeApply(gate=gate)
    window = make_app([adapter()], applier=applier)
    try:
        window.on_apply()
        window.on_apply()
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert len(applier.calls) == 1


@windowed
def test_the_selected_profile_is_the_one_applied(make_app):
    applier = FakeApply()
    window = make_app([adapter()], applier=applier)
    window.profiles = [ONE]
    window.refresh_list(select=1)
    apply_and_settle(window)
    assert applier.calls == [(window.adapter, ONE)]


@windowed
def test_the_outcome_reaches_the_status_line_from_the_worker(make_app):
    window = make_app([adapter()], applier=FakeApply(Outcome(True, "Ustawiono 10.0.0.5 /24")))
    apply_and_settle(window)
    assert window.status.cget("text") == "Ustawiono 10.0.0.5 /24"
    assert str(window.status.cget("foreground")) == TEXT_SECONDARY
    assert str(window.apply_button.cget("state")) == "normal"
    assert str(window.add_button.cget("state")) == "normal"
    assert window.watchdog is None


@windowed
def test_a_failure_is_shown_in_the_danger_colour(make_app):
    window = make_app([adapter()], applier=FakeApply(Outcome(False, "Karta ma 10.0.0.5 /24")))
    apply_and_settle(window)
    assert window.status.cget("text") == "Karta ma 10.0.0.5 /24"
    assert str(window.status.cget("foreground")) == DANGER


@windowed
def test_a_worker_that_throws_does_not_leave_the_buttons_dead(make_app):
    def explode(target, profile, **_kwargs):
        raise OSError("iphlpapi zniknęło")

    window = make_app([adapter()], applier=explode)
    apply_and_settle(window)
    assert "iphlpapi zniknęło" in window.status.cget("text")
    assert str(window.apply_button.cget("state")) == "normal"


@windowed
def test_a_thread_that_refuses_to_start_still_leaves_a_watchdog(monkeypatch, make_app):
    # The one case where the worker never runs at all. If the watchdog were armed
    # after start(), the buttons would stay disabled for the rest of the session.
    from net_preset import app as module

    window = make_app([adapter()])

    class Refuses:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module.threading, "Thread", Refuses)
    with pytest.raises(RuntimeError):
        window.on_apply()

    assert window.watchdog is not None
    window.on_no_answer(window.attempt)
    assert str(window.apply_button.cget("state")) == "normal"
    assert window.status.cget("text") == NO_ANSWER


@windowed
def test_the_tick_takes_ustaw_away_when_the_card_goes(make_app):
    # What the tick is for: an adapter unplugged mid-session leaves USTAW pointing
    # at nothing, and nothing else would refresh the buttons.
    cards = [adapter()]
    window = make_app(cards)
    assert str(window.apply_button.cget("state")) == "normal"
    scheduled = window.ticker

    cards.clear()
    window.on_tick()

    assert str(window.apply_button.cget("state")) == "disabled"
    assert window.current.cget("text") == NO_CARD
    assert window.ticker != scheduled  # and it comes round again


@windowed
def test_a_worker_that_never_answers_gives_the_buttons_back(make_app):
    gate = threading.Event()
    window = make_app([adapter()], applier=FakeApply(gate=gate))
    try:
        window.on_apply()
        window.on_no_answer(window.attempt)
        assert str(window.apply_button.cget("state")) == "normal"
        assert str(window.add_button.cget("state")) == "normal"
        assert window.status.cget("text") == NO_ANSWER
    finally:
        gate.set()
        window.worker.join(timeout=5)


@windowed
def test_a_window_closed_mid_apply_does_not_take_the_worker_down_with_it(make_app, monkeypatch):
    """The suppress at the end of _work, which nothing else asserts anything about.

    An operator who closes the window while netsh is still running leaves the worker
    holding an answer for an interpreter that no longer exists. Its last act is
    `after(0, ...)` against that interpreter, and Tkinter answers with `RuntimeError:
    main thread is not in main loop` -- not the TclError the name of the failure would
    suggest, which is why the suppress names both. Without it the worker dies through
    threading.excepthook and a traceback lands on stderr from a window that is gone.
    """
    escaped = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    gate = threading.Event()
    window = make_app([adapter()], applier=FakeApply(gate=gate))
    window.on_apply()
    worker = window.worker

    window.destroy()
    gate.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert escaped == [], [entry.exc_value for entry in escaped]


@windowed
def test_a_late_answer_still_replaces_the_watchdog_guess(make_app):
    # The whole sequence runs inside the loop: the watchdog gives up while the
    # worker is still out, and the answer that turns up afterwards is the truth
    # catching up with a guess, so it replaces it rather than being dropped.
    gate = threading.Event()
    window = make_app([adapter()], applier=FakeApply(Outcome(False, "Karta ma 10.0.0.5 /24"), gate))
    window.after(0, window.on_apply)
    window.after(20, lambda: window.on_no_answer(window.attempt))
    window.after(40, gate.set)
    try:
        pump(window, lambda: window.status.cget("text") == "Karta ma 10.0.0.5 /24")
    finally:
        gate.set()
        window.worker.join(timeout=5)
    assert window.status.cget("text") == "Karta ma 10.0.0.5 /24"


@windowed
def test_an_answer_from_an_abandoned_attempt_is_ignored(make_app):
    window = make_app([adapter()], applier=FakeApply(Outcome(True, "Ustawiono 10.0.0.5 /24")))
    apply_and_settle(window)
    window.on_applied(window.attempt - 1, Outcome(True, "Ustawienia sprzed chwili"))
    assert window.status.cget("text") == "Ustawiono 10.0.0.5 /24"


@windowed
def test_a_shortcut_cannot_apply_without_administrator_rights(make_app):
    # The button is disabled, but Enter and a double-click arrive at on_apply
    # without going anywhere near it.
    applier = FakeApply()
    window = make_app([adapter()], elevated=False, applier=applier)
    window.on_apply()
    assert applier.calls == []
    assert window.worker is None


def test_the_entry_point_opens_no_window_when_an_elevated_copy_took_over(monkeypatch):
    # The only branch of main() that can be run here: the other one opens the
    # window and does not come back until it is closed.
    from net_preset import __main__ as entry

    monkeypatch.setattr(entry, "ensure_elevated", lambda: False)
    monkeypatch.setattr(entry, "Application", _never)
    assert entry.main() == 0


def _never(*_args, **_kwargs):
    raise AssertionError("a window was opened after handing over to an elevated copy")


@windowed
@pytest.mark.parametrize("gesture", ["<Double-Button-1>", "<Return>", "<KP_Enter>"])
def test_the_shortcuts_on_a_row_are_wired_to_applying(make_app, gesture):
    # Read rather than driven. Tk refuses to generate an event with a Double
    # modifier at all, and it delivers a generated key event only to a window
    # that really holds the focus, which a test has no business taking. Both
    # gestures are driven by hand against the running window instead.
    #
    # Tkinter names the Tcl command it registers after the Python function
    # behind it, which is what makes the target legible in the binding.
    window = make_app([adapter()])
    assert "on_apply" in window.listbox.bind(gesture)
