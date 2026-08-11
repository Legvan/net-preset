import sys
import threading
import time
import tkinter as tk

import pytest
from conftest import new_root

from net_preset.adapters import AdapterState
from net_preset.app import (
    DHCP_LABEL,
    NO_ADDRESS,
    NO_ANSWER,
    NO_CARD,
    NO_LEASE,
    NOT_ELEVATED,
    Application,
    list_entries,
    shorten,
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
    """
    window.after(0, window.on_apply)
    pump(window, lambda: not window.busy, timeout)


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
        window.destroy()


def test_the_current_line_names_the_address():
    assert state_text(adapter(addresses=(("10.0.0.5", 16),))) == "10.0.0.5 /16"


def test_the_current_line_says_when_there_is_no_card():
    assert state_text(None) == NO_CARD


def test_the_current_line_says_when_the_card_has_no_address():
    assert state_text(adapter(addresses=())) == NO_ADDRESS


def test_the_current_line_calls_an_apipa_address_a_missing_lease():
    assert state_text(adapter(addresses=(("169.254.7.7", 16),), dhcp=True)) == NO_LEASE


def test_a_long_message_is_folded_onto_one_short_line():
    folded = shorten("x" * 200 + "\n" + "y" * 200)
    assert len(folded) <= 140
    assert "\n" not in folded
    assert folded.endswith("…")


def test_a_short_message_is_left_alone():
    assert shorten("Gotowy") == "Gotowy"


@windowed
def test_the_current_line_shows_the_live_adapter(make_app):
    window = make_app([adapter(addresses=(("10.0.0.5", 8),))])
    assert window.current.cget("text") == "10.0.0.5 /8"


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


@windowed
def test_a_complaint_from_the_stored_file_reaches_the_status_line(tmp_path, make_app):
    (tmp_path / "profiles.json").write_text('{"profiles": [{"name": "", "address": "x"}]}')
    window = make_app([adapter()])
    assert "Pominięto" in window.status.cget("text")
    assert str(window.status.cget("foreground")) == DANGER


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
    window = make_app([adapter()])
    assert window.adapter_picker is None


@windowed
def test_two_adapters_bring_out_the_picker(make_app):
    window = make_app([adapter(), adapter("{BBBB}", "Ethernet 2")])
    assert window.adapter_picker is not None
    assert window.adapter_picker.cget("text") == "Ethernet   ▾"


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
