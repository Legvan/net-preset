import ctypes

import pytest

from net_preset.adapters import IF_TYPE_ETHERNET, AdapterState
from net_preset.apply import CommandResult, _decode, apply_profile, matches_request
from net_preset.profile import Profile

PROFILE = Profile(name="ROGER", address="192.168.11.2", mask="255.255.255.0")

# Captured verbatim from a real, un-elevated run of
# `netsh interface ipv4 set address name=NieMaTakiej source=dhcp` on this machine.
# netsh writes UTF-8 into a pipe: "ł" arrives as c5 82, whatever the console is set to.
NETSH_REFUSAL = (
    b"Nazwa pliku, nazwa katalogu lub sk\xc5\x82adnia etykiety woluminu"
    b" jest niepoprawna.\r\n\r\n\r\n"
)


def adapter(**overrides) -> AdapterState:
    fields = {
        "guid": "{GUID}",
        "name": "Ethernet",
        "description": "Contoso Gigabit Ethernet Adapter",
        "if_index": 17,
        "if_type": IF_TYPE_ETHERNET,
        "connected": True,
        "dhcp": False,
        "addresses": (("192.168.11.2", 24),),
        "gateways": (),
        "dns": (),
    }
    fields.update(overrides)
    return AdapterState(**fields)


class Runner:
    """Records what it was asked to run and answers from a scripted list."""

    def __init__(self, results=None):
        self.commands = []
        self.results = list(results or [])

    def __call__(self, command):
        self.commands.append(list(command))
        return self.results.pop(0) if self.results else CommandResult(0, "")


class Reader:
    """Answers with each scripted state in turn, repeating the last one."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = 0

    def __call__(self, guid):
        self.calls += 1
        return self.states[min(self.calls - 1, len(self.states) - 1)]


def apply(profile, runner, reader, attempts=10):
    return apply_profile(
        adapter(),
        profile,
        runner=runner,
        reader=reader,
        sleep=lambda _: None,
        attempts=attempts,
    )


def test_a_static_profile_runs_the_commands_in_order():
    runner = Runner()
    apply(PROFILE, runner, Reader([adapter()]))
    assert [command[3:5] for command in runner.commands] == [
        ["set", "address"],
        ["set", "dnsservers"],
    ]


def test_the_interface_name_is_the_friendly_name():
    runner = Runner()
    apply(PROFILE, runner, Reader([adapter()]))
    assert "name=Ethernet" in runner.commands[0]


def test_a_matching_result_is_reported_as_success():
    outcome = apply(PROFILE, Runner(), Reader([adapter()]))
    assert outcome.ok is True
    assert "192.168.11.2" in outcome.message


def test_polling_stops_as_soon_as_the_state_matches():
    reader = Reader([adapter(addresses=(("169.254.1.1", 16),)), adapter()])
    apply(PROFILE, Runner(), reader)
    assert reader.calls == 2


def test_a_state_that_never_matches_is_reported_as_a_mismatch():
    reader = Reader([adapter(addresses=(("10.0.0.9", 24),))])
    outcome = apply(PROFILE, Runner(), reader, attempts=3)
    assert outcome.ok is False
    assert "10.0.0.9" in outcome.message


def test_a_card_left_without_any_address_is_reported_as_a_mismatch():
    # What a static apply looks like on an unplugged card: the request is gone and there
    # is no address at all to name in its place.
    reader = Reader([adapter(addresses=())])
    outcome = apply(PROFILE, Runner(), reader, attempts=3)
    assert outcome.ok is False
    assert "192.168.11.2" in outcome.message
    # Naming the absence, rather than reading an address out of a card that has none.
    assert "nie ma adresu" in outcome.message


def test_a_failing_command_stops_the_run_and_is_reported():
    runner = Runner([CommandResult(87, "Nieprawidłowy parametr.")])
    outcome = apply(PROFILE, runner, Reader([adapter()]))
    assert outcome.ok is False
    assert "Nieprawidłowy parametr." in outcome.message
    assert len(runner.commands) == 1


def test_an_address_that_is_already_set_is_not_a_failure():
    # netsh answers "the object already exists" when the address is unchanged.
    runner = Runner([CommandResult(1, "Obiekt już istnieje."), CommandResult(0, "")])
    outcome = apply(PROFILE, runner, Reader([adapter()]))
    assert outcome.ok is True
    assert len(runner.commands) == 2


def test_code_one_without_the_already_exists_phrase_is_a_failure():
    # 1 is also netsh's operational failure code, and it is the one an un-elevated run
    # comes back with. Only the phrase makes it benign.
    runner = Runner([CommandResult(1, "Odmowa dostępu.")])
    outcome = apply(PROFILE, runner, Reader([adapter()]))
    assert outcome.ok is False
    assert "Odmowa dostępu." in outcome.message
    assert len(runner.commands) == 1


def test_dhcp_runs_the_two_restoring_commands():
    runner = Runner()
    apply(None, runner, Reader([adapter(dhcp=True, addresses=(("192.168.1.50", 24),))]))
    assert all("source=dhcp" in command for command in runner.commands)
    assert len(runner.commands) == 2


def test_dhcp_with_a_lease_is_a_success():
    reader = Reader([adapter(dhcp=True, addresses=(("192.168.1.50", 24),))])
    outcome = apply(None, Runner(), reader)
    assert outcome.ok is True
    assert "192.168.1.50" in outcome.message


def test_dhcp_on_a_disconnected_card_is_reported_as_waiting_not_failure():
    reader = Reader([adapter(dhcp=True, connected=False, addresses=())])
    outcome = apply(None, Runner(), reader, attempts=2)
    assert outcome.ok is True
    assert "kabel" in outcome.message.lower()


def test_dhcp_that_only_reaches_apipa_says_no_lease():
    reader = Reader([adapter(dhcp=True, connected=True, addresses=(("169.254.9.9", 16),))])
    outcome = apply(None, Runner(), reader, attempts=2)
    assert outcome.ok is False
    assert "dzierżaw" in outcome.message.lower()


def test_a_card_still_on_a_static_address_is_not_reported_as_missing_a_lease():
    # netsh can report success for a command it did not carry out, so a card that never
    # took the DHCP flag must not be told it is waiting for a lease.
    reader = Reader([adapter(dhcp=False, connected=True, addresses=(("192.168.11.2", 24),))])
    outcome = apply(None, Runner(), reader, attempts=2)
    assert outcome.ok is False
    assert "dzierżaw" not in outcome.message.lower()
    assert "192.168.11.2" in outcome.message


def test_a_disconnected_card_that_never_took_the_dhcp_flag_is_not_called_waiting():
    # The cable is only half the story. A card that never took the flag is still on its old
    # configuration whether or not it is plugged in, and "czekam na kabel" would be a false
    # statement about the hardware reported as a success.
    for addresses in ((), (("192.168.11.2", 24),), (("169.254.9.9", 16),)):
        reader = Reader([adapter(dhcp=False, connected=False, addresses=addresses)])
        outcome = apply(None, Runner(), reader, attempts=2)
        assert outcome.ok is False, addresses
        assert "kabel" not in outcome.message.lower(), addresses


def test_an_adapter_that_disappears_is_reported():
    outcome = apply(PROFILE, Runner(), Reader([None]), attempts=2)
    assert outcome.ok is False
    assert outcome.message


def test_matches_request_compares_address_and_prefix():
    assert matches_request(adapter(), PROFILE) is True
    assert matches_request(adapter(addresses=(("192.168.11.3", 24),)), PROFILE) is False
    assert matches_request(adapter(addresses=(("192.168.11.2", 16),)), PROFILE) is False


def test_matches_request_ignores_extra_addresses():
    extra = adapter(addresses=(("192.168.11.2", 24), ("169.254.1.1", 16)))
    assert matches_request(extra, PROFILE) is True


def test_matches_request_for_dhcp_wants_the_flag_and_a_real_lease():
    assert matches_request(adapter(dhcp=True, addresses=(("192.168.1.50", 24),)), None) is True
    assert matches_request(adapter(dhcp=False, addresses=(("192.168.1.50", 24),)), None) is False
    assert matches_request(adapter(dhcp=True, addresses=(("169.254.1.1", 16),)), None) is False


# -- the two thirds of the request the read-back used to ignore ------------------
#
# netsh returns 0 on a syntax error, so the read-back is the only witness there is.
# An apply that set the address and did nothing about the servers leaves the card on
# the right subnet with the last site's DNS: a working IP with no name resolution.

ROUTED = Profile(
    name="ROGER",
    address="192.168.11.2",
    mask="255.255.255.0",
    gateway="192.168.11.1",
    dns="192.168.11.1",
    dns_alt="8.8.8.8",
)
CARRIED = {"gateways": ("192.168.11.1",), "dns": ("192.168.11.1", "8.8.8.8")}


def test_matches_request_wants_everything_the_profile_named():
    assert matches_request(adapter(**CARRIED), ROUTED) is True


def test_a_gateway_the_profile_named_has_to_be_there():
    assert matches_request(adapter(**CARRIED | {"gateways": ()}), ROUTED) is False
    assert matches_request(adapter(**CARRIED | {"gateways": ("10.0.0.1",)}), ROUTED) is False


def test_a_second_gateway_alongside_the_one_asked_for_does_not_spoil_the_match():
    # A card may list more than one default route, and only the one asked for is ours.
    kept = CARRIED | {"gateways": ("192.168.11.1", "192.168.11.254")}
    assert matches_request(adapter(**kept), ROUTED) is True


def test_a_profile_with_no_gateway_wants_the_card_to_have_none():
    # gateway=none is a command whose whole purpose is to leave the card bare, so a
    # route left behind by the profile before it is the apply not having taken.
    assert matches_request(adapter(gateways=(), dns=()), PROFILE) is True
    assert matches_request(adapter(gateways=("192.168.11.1",), dns=()), PROFILE) is False


def test_the_dns_servers_have_to_be_the_ones_the_profile_named():
    assert matches_request(adapter(**CARRIED | {"dns": ()}), ROUTED) is False
    assert matches_request(adapter(**CARRIED | {"dns": ("192.168.11.1",)}), ROUTED) is False
    extra = CARRIED | {"dns": ("192.168.11.1", "8.8.8.8", "1.1.1.1")}
    assert matches_request(adapter(**extra), ROUTED) is False


def test_the_dns_servers_have_to_be_in_the_order_they_were_set():
    # The primary is index 1 and the alternate index 2. A card answering with them
    # the other way round is resolving through the wrong one first.
    swapped = CARRIED | {"dns": ("8.8.8.8", "192.168.11.1")}
    assert matches_request(adapter(**swapped), ROUTED) is False


def test_one_server_named_twice_matches_the_card_that_carries_it_once():
    # AdapterState.dns drops repeats, so the expectation has to drop them too or a
    # profile with the same address in both fields could never match anything.
    twice = Profile("ROGER", "192.168.11.2", "255.255.255.0", dns="9.9.9.9", dns_alt="9.9.9.9")
    assert matches_request(adapter(dns=("9.9.9.9",)), twice) is True


def test_a_profile_with_no_dns_wants_the_card_to_have_none():
    assert matches_request(adapter(dns=()), PROFILE) is True
    assert matches_request(adapter(dns=("8.8.8.8",)), PROFILE) is False


def test_dhcp_expects_nothing_of_the_gateway_or_the_servers():
    # What a DHCP server hands out is not ours to predict, and an expectation invented
    # here would report a working lease as a failure.
    leased = adapter(
        dhcp=True,
        addresses=(("192.168.1.50", 24),),
        gateways=("192.168.1.1",),
        dns=("192.168.1.1", "8.8.8.8"),
    )
    assert matches_request(leased, None) is True
    assert matches_request(adapter(dhcp=True, addresses=(("192.168.1.50", 24),)), None) is True


def test_a_gateway_that_never_landed_is_named_on_both_sides():
    reader = Reader([adapter(**CARRIED | {"gateways": ("10.0.0.1",)})])
    outcome = apply(ROUTED, Runner(), reader, attempts=2)
    assert outcome.ok is False
    assert "brama" in outcome.message
    assert "10.0.0.1" in outcome.message
    assert "192.168.11.1" in outcome.message
    # And it says the address did take, so this does not read as a card that never moved.
    assert "Adres ustawiony" in outcome.message


def test_a_missing_gateway_is_named_as_an_absence():
    reader = Reader([adapter(**CARRIED | {"gateways": ()})])
    outcome = apply(ROUTED, Runner(), reader, attempts=2)
    assert "brama: brak zamiast 192.168.11.1" in outcome.message


def test_servers_that_never_landed_are_named_on_both_sides():
    reader = Reader([adapter(**CARRIED | {"dns": ("1.1.1.1",)})])
    outcome = apply(ROUTED, Runner(), reader, attempts=2)
    assert outcome.ok is False
    assert "DNS: 1.1.1.1 zamiast 192.168.11.1, 8.8.8.8" in outcome.message


def test_servers_left_on_a_card_that_should_have_none_are_reported():
    # The failure that looks least like one: the right address, the last site's DNS.
    reader = Reader([adapter(dns=("10.0.0.53",))])
    outcome = apply(PROFILE, Runner(), reader, attempts=2)
    assert outcome.ok is False
    assert "DNS: 10.0.0.53 zamiast brak" in outcome.message


def test_a_wrong_address_is_still_reported_as_an_address_problem():
    # The address is asked about first: a card that never took it makes the rest moot.
    reader = Reader([adapter(addresses=(("10.0.0.9", 24),), gateways=(), dns=())])
    outcome = apply(ROUTED, Runner(), reader, attempts=2)
    assert "Karta ma 10.0.0.9 /24" in outcome.message
    assert "brama" not in outcome.message and "DNS" not in outcome.message


def test_polling_waits_for_the_servers_as_well_as_the_address():
    # The address lands first and the servers a moment later, which is the ordinary
    # shape of a successful apply and must not be reported as a failure.
    reader = Reader([adapter(**CARRIED | {"dns": ()}), adapter(**CARRIED)])
    outcome = apply(ROUTED, Runner(), reader)
    assert outcome.ok is True
    assert reader.calls == 2


def test_real_netsh_output_is_decoded_as_utf_8():
    """The bytes in NETSH_REFUSAL are what netsh really emitted, not what we assumed.

    Read as cp852 they come out as "sk┼éadnia": mojibake in the one place the operator is
    being told why their card did not change.
    """
    assert _decode(NETSH_REFUSAL).strip() == (
        "Nazwa pliku, nazwa katalogu lub składnia etykiety woluminu jest niepoprawna."
    )


def test_output_that_is_not_utf_8_falls_back_to_the_system_oem_page():
    """The other direction: a console program that does not write UTF-8 writes the OEM page.

    cp852 spells "ż" as a lone be, which is not a legal UTF-8 start byte — which is what
    makes the two cases tellable apart without guessing at any console's state.
    """
    if ctypes.windll.kernel32.GetOEMCP() != 852:
        pytest.skip("this pins the fallback against the Polish OEM page")
    assert _decode(b"Obiekt ju\xbe istnieje.") == "Obiekt już istnieje."
