import pytest

from net_preset.profile import (
    DEFAULT_MASK,
    FieldError,
    Profile,
    parse_ipv4,
    prefix_length,
    validate,
)


def make(**overrides) -> Profile:
    fields = {"name": "ROGER", "address": "192.168.11.2", "mask": DEFAULT_MASK}
    fields.update(overrides)
    return Profile(**fields)


def fields_with_errors(profile: Profile, other_names=()) -> set[str]:
    return {error.field for error in validate(profile, other_names)}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.0.0.0", 0),
        ("255.255.255.255", 0xFFFFFFFF),
        ("192.168.11.2", 0xC0A80B02),
        ("10.0.0.1", 0x0A000001),
    ],
)
def test_parse_ipv4_accepts_dotted_quads(text, expected):
    assert parse_ipv4(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "192.168.11",
        "192.168.11.2.3",
        "192.168.11.256",
        "192.168.11.-1",
        "192.168.11.a",
        "192.168.11.02",  # a leading zero reads as octal to some parsers
        " 192.168.11.2",
        "192.168.11.2 ",
        "192.168.11.٢",  # non-ASCII digits satisfy str.isdigit()
    ],
)
def test_parse_ipv4_rejects_malformed_input(text):
    assert parse_ipv4(text) is None


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        ("128.0.0.0", 1),
        ("255.0.0.0", 8),
        ("255.255.0.0", 16),
        ("255.255.255.0", 24),
        ("255.255.255.252", 30),
        ("255.255.255.254", 31),
        ("0.0.0.0", 0),
        ("255.255.255.255", 32),
    ],
)
def test_prefix_length_counts_leading_ones(mask, expected):
    assert prefix_length(mask) == expected


@pytest.mark.parametrize("mask", ["255.255.0.255", "255.0.255.0", "0.0.0.1", "nonsense"])
def test_prefix_length_rejects_non_contiguous_masks(mask):
    assert prefix_length(mask) is None


def test_a_minimal_profile_validates():
    assert validate(make()) == []


def test_a_full_profile_validates():
    profile = make(gateway="192.168.11.1", dns="192.168.11.1", dns_alt="8.8.8.8")
    assert validate(profile) == []


def test_name_must_not_be_blank():
    assert fields_with_errors(make(name="   ")) == {"name"}


def test_name_must_be_short_enough():
    assert fields_with_errors(make(name="X" * 33)) == {"name"}


def test_name_must_be_unique_ignoring_case():
    assert fields_with_errors(make(name="roger"), other_names=["ROGER"]) == {"name"}


def test_name_may_repeat_a_name_that_is_not_taken():
    assert validate(make(name="BIURO"), other_names=["ROGER"]) == []


def test_address_must_be_an_ipv4_address():
    assert fields_with_errors(make(address="192.168.11")) == {"address"}


def test_mask_must_be_contiguous():
    assert fields_with_errors(make(mask="255.255.0.255")) == {"mask"}


@pytest.mark.parametrize("mask", ["0.0.0.0", "255.255.255.255"])
def test_mask_must_leave_room_for_a_host(mask):
    assert "mask" in fields_with_errors(make(mask=mask))


def test_address_must_not_be_the_network_address():
    assert fields_with_errors(make(address="192.168.11.0")) == {"address"}


def test_address_must_not_be_the_broadcast_address():
    assert fields_with_errors(make(address="192.168.11.255")) == {"address"}


def test_both_addresses_of_a_slash_31_are_usable():
    assert validate(make(address="192.168.11.0", mask="255.255.255.254")) == []
    assert validate(make(address="192.168.11.1", mask="255.255.255.254")) == []


def test_gateway_must_lie_inside_the_subnet():
    assert fields_with_errors(make(gateway="10.0.0.1")) == {"gateway"}


def test_gateway_inside_the_subnet_is_accepted():
    assert validate(make(gateway="192.168.11.1")) == []


def test_gateway_must_be_an_ipv4_address_when_given():
    assert fields_with_errors(make(gateway="brama")) == {"gateway"}


def test_dns_must_be_an_ipv4_address_when_given():
    assert fields_with_errors(make(dns="8.8.8")) == {"dns"}


def test_alternate_dns_must_be_an_ipv4_address_when_given():
    assert fields_with_errors(make(dns="8.8.8.8", dns_alt="8.8.4")) == {"dns_alt"}


def test_an_alternate_dns_without_a_primary_is_rejected():
    assert fields_with_errors(make(dns="", dns_alt="8.8.4.4")) == {"dns_alt"}


def test_every_broken_field_is_reported_at_once():
    profile = Profile(name="", address="x", mask="y", gateway="z", dns="q", dns_alt="w")
    assert fields_with_errors(profile) == {"name", "address", "mask", "gateway", "dns", "dns_alt"}


def test_errors_carry_a_message():
    errors = validate(make(address="192.168.11"))
    assert errors and all(isinstance(error, FieldError) and error.message for error in errors)


def test_the_label_reads_as_address_then_name():
    assert make().label == "192.168.11.2 (ROGER)"
