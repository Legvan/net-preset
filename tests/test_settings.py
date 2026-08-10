from net_preset.settings import load_adapter_choice, save_adapter_choice, settings_path

GUID = "{2965BC8E-BEB8-42BF-9D34-EC5C080515D0}"


def test_a_saved_choice_comes_back(tmp_path):
    target = tmp_path / "settings.json"
    save_adapter_choice(GUID, target)
    assert load_adapter_choice(target) == GUID


def test_no_file_means_no_choice(tmp_path):
    assert load_adapter_choice(tmp_path / "absent.json") is None


def test_a_corrupt_file_means_no_choice(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("not json at all", encoding="utf-8")
    assert load_adapter_choice(target) is None


def test_a_non_string_choice_is_ignored(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text('{"adapter": 17}', encoding="utf-8")
    assert load_adapter_choice(target) is None


def test_clearing_the_choice_is_allowed(tmp_path):
    target = tmp_path / "settings.json"
    save_adapter_choice(GUID, target)
    save_adapter_choice(None, target)
    assert load_adapter_choice(target) is None


def test_saving_never_raises_on_an_unwritable_path(tmp_path):
    target = tmp_path / "settings.json"
    target.mkdir()
    save_adapter_choice(GUID, target)  # must not raise


def test_the_default_path_sits_beside_the_profiles():
    assert settings_path().name == "settings.json"
    assert settings_path().parent.name == "net-preset"
