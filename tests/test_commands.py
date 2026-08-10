from net_preset.commands import commands_for, dhcp_commands, static_commands
from net_preset.profile import Profile

MINIMAL = Profile(name="ROGER", address="192.168.11.2", mask="255.255.255.0")
FULL = Profile(
    name="BIURO",
    address="10.0.0.5",
    mask="255.255.255.0",
    gateway="10.0.0.1",
    dns="10.0.0.1",
    dns_alt="8.8.8.8",
)


def test_every_command_invokes_netsh_on_ipv4():
    for command in static_commands("Ethernet", FULL) + dhcp_commands("Ethernet"):
        assert command[:3] == ["netsh", "interface", "ipv4"]


def test_a_minimal_profile_sets_the_address_and_clears_the_gateway():
    assert static_commands("Ethernet", MINIMAL)[0] == [
        "netsh",
        "interface",
        "ipv4",
        "set",
        "address",
        "name=Ethernet",
        "source=static",
        "address=192.168.11.2",
        "mask=255.255.255.0",
        "gateway=none",
    ]


def test_a_gateway_is_passed_through_when_present():
    assert "gateway=10.0.0.1" in static_commands("Ethernet", FULL)[0]


def test_a_profile_without_dns_clears_the_servers():
    commands = static_commands("Ethernet", MINIMAL)
    assert commands[1] == [
        "netsh",
        "interface",
        "ipv4",
        "set",
        "dnsservers",
        "name=Ethernet",
        "source=static",
        "address=none",
    ]
    assert len(commands) == 2


def test_a_primary_dns_is_registered():
    assert static_commands("Ethernet", FULL)[1] == [
        "netsh",
        "interface",
        "ipv4",
        "set",
        "dnsservers",
        "name=Ethernet",
        "source=static",
        "address=10.0.0.1",
        "register=primary",
        "validate=no",
    ]


def test_an_alternate_dns_is_added_at_index_two():
    assert static_commands("Ethernet", FULL)[2] == [
        "netsh",
        "interface",
        "ipv4",
        "add",
        "dnsservers",
        "name=Ethernet",
        "address=8.8.8.8",
        "index=2",
        "validate=no",
    ]


def test_only_a_primary_dns_produces_no_second_command():
    profile = Profile(name="X", address="10.0.0.5", mask="255.255.255.0", dns="10.0.0.1")
    assert len(static_commands("Ethernet", profile)) == 2


def test_dns_commands_never_validate():
    for command in static_commands("Ethernet", FULL):
        if "dnsservers" in command:
            assert "validate=no" in command or "address=none" in command


def test_dhcp_restores_both_the_address_and_the_servers():
    assert dhcp_commands("Ethernet") == [
        ["netsh", "interface", "ipv4", "set", "address", "name=Ethernet", "source=dhcp"],
        ["netsh", "interface", "ipv4", "set", "dnsservers", "name=Ethernet", "source=dhcp"],
    ]


def test_commands_for_none_means_dhcp():
    assert commands_for("Ethernet", None) == dhcp_commands("Ethernet")


def test_commands_for_a_profile_means_static():
    assert commands_for("Ethernet", MINIMAL) == static_commands("Ethernet", MINIMAL)


def test_an_interface_name_with_spaces_stays_one_argument():
    command = static_commands("Ethernet 2", MINIMAL)[0]
    assert "name=Ethernet 2" in command


def test_an_interface_name_with_polish_characters_survives():
    command = static_commands("Połączenie lokalne", MINIMAL)[0]
    assert "name=Połączenie lokalne" in command
