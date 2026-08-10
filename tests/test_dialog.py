import sys
import tkinter as tk

import pytest

from net_preset.dialog import FIELDS, ProfileDialog
from net_preset.profile import DEFAULT_MASK, Profile

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows display")


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    window.withdraw()
    yield window
    window.destroy()


def test_the_fields_are_in_the_agreed_order():
    assert [attribute for attribute, _ in FIELDS] == [
        "name",
        "address",
        "mask",
        "gateway",
        "dns",
        "dns_alt",
    ]


def test_a_new_profile_starts_with_the_default_mask(root):
    dialog = ProfileDialog(root)
    assert dialog.value_of("mask") == DEFAULT_MASK
    assert dialog.value_of("name") == ""
    dialog.destroy()


def test_editing_fills_every_field(root):
    profile = Profile(
        name="BIURO",
        address="10.0.0.5",
        mask="255.255.0.0",
        gateway="10.0.0.1",
        dns="10.0.0.1",
        dns_alt="8.8.8.8",
    )
    dialog = ProfileDialog(root, profile=profile)
    assert dialog.value_of("name") == "BIURO"
    assert dialog.value_of("gateway") == "10.0.0.1"
    assert dialog.value_of("dns_alt") == "8.8.8.8"
    dialog.destroy()


def test_the_delete_button_appears_only_when_editing(root):
    adding = ProfileDialog(root)
    assert adding.delete_button is None
    adding.destroy()

    editing = ProfileDialog(root, profile=Profile("X", "10.0.0.5", DEFAULT_MASK))
    assert editing.delete_button is not None
    editing.destroy()


def test_confirming_a_valid_form_produces_a_profile(root):
    dialog = ProfileDialog(root)
    dialog.set_value("name", "ROGER")
    dialog.set_value("address", "192.168.11.2")
    dialog.on_confirm()
    assert dialog.result.action == "save"
    assert dialog.result.profile == Profile("ROGER", "192.168.11.2", DEFAULT_MASK)
    dialog.destroy()


def test_whitespace_around_a_value_is_trimmed(root):
    dialog = ProfileDialog(root)
    dialog.set_value("name", "  ROGER  ")
    dialog.set_value("address", " 192.168.11.2 ")
    dialog.on_confirm()
    assert dialog.result.profile == Profile("ROGER", "192.168.11.2", DEFAULT_MASK)
    dialog.destroy()


def test_an_invalid_form_stays_open_and_marks_the_field(root):
    dialog = ProfileDialog(root)
    dialog.set_value("name", "ROGER")
    dialog.set_value("address", "192.168.11")
    dialog.on_confirm()
    assert dialog.result is None
    assert dialog.error_of("address")
    assert dialog.entry_of("address").cget("style") == "Invalid.TEntry"
    dialog.destroy()


def test_a_corrected_field_loses_its_mark(root):
    dialog = ProfileDialog(root)
    dialog.set_value("name", "ROGER")
    dialog.set_value("address", "192.168.11")
    dialog.on_confirm()
    dialog.set_value("address", "192.168.11.2")
    dialog.on_confirm()
    assert dialog.result.action == "save"
    assert dialog.error_of("address") == ""
    dialog.destroy()


def test_a_duplicate_name_is_refused(root):
    dialog = ProfileDialog(root, other_names=["ROGER"])
    dialog.set_value("name", "roger")
    dialog.set_value("address", "192.168.11.2")
    dialog.on_confirm()
    assert dialog.result is None
    assert dialog.error_of("name")
    dialog.destroy()


def test_editing_does_not_collide_with_its_own_name(root):
    profile = Profile("ROGER", "192.168.11.2", DEFAULT_MASK)
    dialog = ProfileDialog(root, profile=profile, other_names=["BIURO"])
    dialog.on_confirm()
    assert dialog.result.action == "save"
    dialog.destroy()


def test_editing_does_not_collide_with_its_own_name_written_differently(root):
    # `validate` compares names ignoring case and surrounding space, so dropping
    # the profile's own name from the list has to do the same. Compare them
    # literally and an unchanged profile is refused as a duplicate of itself.
    profile = Profile("ROGER", "192.168.11.2", DEFAULT_MASK)
    dialog = ProfileDialog(root, profile=profile, other_names=[" roger "])
    dialog.on_confirm()
    assert dialog.result.action == "save"
    dialog.destroy()


def test_cancelling_reports_a_cancel(root):
    dialog = ProfileDialog(root)
    dialog.on_cancel()
    assert dialog.result.action == "cancel"
    dialog.destroy()


def test_deleting_asks_first_and_reports_a_delete(root):
    profile = Profile("ROGER", "192.168.11.2", DEFAULT_MASK)
    dialog = ProfileDialog(root, profile=profile)
    dialog.confirm_delete = lambda: True
    dialog.on_delete()
    assert dialog.result.action == "delete"
    assert dialog.result.profile == profile
    dialog.destroy()


def test_a_refused_deletion_changes_nothing(root):
    dialog = ProfileDialog(root, profile=Profile("ROGER", "192.168.11.2", DEFAULT_MASK))
    dialog.confirm_delete = lambda: False
    dialog.on_delete()
    assert dialog.result is None
    dialog.destroy()
