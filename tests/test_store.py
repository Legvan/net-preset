import json

import pytest

from net_preset.profile import Profile
from net_preset.store import _dropped_note, load_profiles, profiles_path, save_profiles

ONE = Profile(name="ROGER", address="192.168.11.2", mask="255.255.255.0")
TWO = Profile(
    name="BIURO",
    address="10.0.0.5",
    mask="255.255.255.0",
    gateway="10.0.0.1",
    dns="10.0.0.1",
    dns_alt="8.8.8.8",
)


def test_saved_profiles_come_back_unchanged(tmp_path):
    target = tmp_path / "profiles.json"
    assert save_profiles([ONE, TWO], target) is True
    assert load_profiles(target) == ([ONE, TWO], None)


def test_order_is_preserved(tmp_path):
    target = tmp_path / "profiles.json"
    save_profiles([TWO, ONE], target)
    loaded, _ = load_profiles(target)
    assert [profile.name for profile in loaded] == ["BIURO", "ROGER"]


def test_a_missing_file_is_an_empty_list_and_no_complaint(tmp_path):
    assert load_profiles(tmp_path / "absent.json") == ([], None)


def test_a_corrupt_file_yields_no_profiles_and_a_note(tmp_path):
    target = tmp_path / "profiles.json"
    target.write_text("{ this is not json", encoding="utf-8")
    profiles, note = load_profiles(target)
    assert profiles == []
    assert note


def test_a_file_that_is_not_an_object_yields_a_note(tmp_path):
    target = tmp_path / "profiles.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    profiles, note = load_profiles(target)
    assert profiles == []
    assert note


def test_one_invalid_entry_is_dropped_and_the_rest_survive(tmp_path):
    target = tmp_path / "profiles.json"
    payload = {
        "version": 1,
        "profiles": [
            {"name": "ROGER", "address": "192.168.11.2", "mask": "255.255.255.0"},
            {"name": "ZŁY", "address": "999.1.1.1", "mask": "255.255.255.0"},
            {"name": "BIURO", "address": "10.0.0.5", "mask": "255.255.255.0"},
        ],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    profiles, note = load_profiles(target)
    assert [profile.name for profile in profiles] == ["ROGER", "BIURO"]
    assert note


def test_a_name_the_file_uses_twice_survives_once(tmp_path):
    target = tmp_path / "profiles.json"
    payload = {
        "version": 1,
        "profiles": [
            {"name": "ROGER", "address": "192.168.11.2", "mask": "255.255.255.0"},
            {"name": "roger", "address": "192.168.11.3", "mask": "255.255.255.0"},
        ],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    profiles, note = load_profiles(target)
    # Each entry is judged against the names accepted before it, not against the whole
    # file: comparing against the whole file would find the clash from both sides and
    # drop the pair, leaving the operator with neither.
    assert [(profile.name, profile.address) for profile in profiles] == [("ROGER", "192.168.11.2")]
    assert note


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "Pominięto 1 nieprawidłowe ustawienie"),
        (2, "Pominięto 2 nieprawidłowe ustawienia"),
        (4, "Pominięto 4 nieprawidłowe ustawienia"),
        (5, "Pominięto 5 nieprawidłowych ustawień"),
        (12, "Pominięto 12 nieprawidłowych ustawień"),  # a teen, despite ending in 2
        (13, "Pominięto 13 nieprawidłowych ustawień"),  # a teen, despite ending in 3
        (14, "Pominięto 14 nieprawidłowych ustawień"),  # a teen, despite ending in 4
        (22, "Pominięto 22 nieprawidłowe ustawienia"),
        (23, "Pominięto 23 nieprawidłowe ustawienia"),  # ends in 3 and is not a teen
        (112, "Pominięto 112 nieprawidłowych ustawień"),  # the teens repeat every hundred
        (122, "Pominięto 122 nieprawidłowe ustawienia"),
    ],
)
def test_the_note_counts_the_dropped_entries_in_polish(count, expected):
    assert _dropped_note(count) == expected


def test_an_entry_missing_a_required_key_is_dropped(tmp_path):
    target = tmp_path / "profiles.json"
    payload = {"version": 1, "profiles": [{"name": "ROGER"}]}
    target.write_text(json.dumps(payload), encoding="utf-8")
    profiles, note = load_profiles(target)
    assert profiles == []
    assert note


def test_optional_fields_default_to_empty(tmp_path):
    target = tmp_path / "profiles.json"
    payload = {
        "version": 1,
        "profiles": [{"name": "ROGER", "address": "192.168.11.2", "mask": "255.255.255.0"}],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    profiles, _ = load_profiles(target)
    assert profiles[0].gateway == "" and profiles[0].dns == "" and profiles[0].dns_alt == ""


def test_the_file_is_written_as_utf8_with_a_version(tmp_path):
    target = tmp_path / "profiles.json"
    save_profiles([Profile(name="HALA Ł", address="10.0.0.5", mask="255.255.255.0")], target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["profiles"][0]["name"] == "HALA Ł"


def test_saving_creates_the_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "profiles.json"
    assert save_profiles([ONE], target) is True
    assert target.exists()


def test_no_temporary_file_is_left_behind(tmp_path):
    target = tmp_path / "profiles.json"
    save_profiles([ONE], target)
    assert [item.name for item in tmp_path.iterdir()] == ["profiles.json"]


def test_saving_over_an_unwritable_path_reports_failure_without_raising(tmp_path):
    target = tmp_path / "profiles.json"
    target.mkdir()  # a directory where the file should be
    assert save_profiles([ONE], target) is False


def test_the_default_path_sits_under_the_application_directory():
    assert profiles_path().name == "profiles.json"
    assert profiles_path().parent.name == "net-preset"
