"""Turn a profile into the netsh invocations that apply it.

Pure functions: they build argument lists and run nothing. Every argument is a
separate list element, so the list can be handed to subprocess without a shell
and an interface name containing spaces or Polish characters survives intact.
"""

from __future__ import annotations

from net_preset.profile import Profile

_NETSH = ["netsh", "interface", "ipv4"]


def static_commands(interface: str, profile: Profile) -> list[list[str]]:
    """Commands that give *interface* the fixed configuration in *profile*.

    The address comes first, then the DNS servers: cleared when the profile names
    none, otherwise set, with the alternate added as a second entry after it.
    """
    name = f"name={interface}"
    commands = [
        [
            *_NETSH,
            "set",
            "address",
            name,
            "source=static",
            f"address={profile.address}",
            f"mask={profile.mask}",
            f"gateway={profile.gateway or 'none'}",
        ]
    ]

    # validate=no matters: without it netsh tries to reach each server before accepting
    # it and blocks for many seconds on a subnet with no reachable DNS.
    if profile.dns:
        commands.append(
            [
                *_NETSH,
                "set",
                "dnsservers",
                name,
                "source=static",
                f"address={profile.dns}",
                "register=primary",
                "validate=no",
            ]
        )
        if profile.dns_alt:
            commands.append(
                [
                    *_NETSH,
                    "add",
                    "dnsservers",
                    name,
                    f"address={profile.dns_alt}",
                    "index=2",
                    "validate=no",
                ]
            )
    else:
        commands.append([*_NETSH, "set", "dnsservers", name, "source=static", "address=none"])

    return commands


def dhcp_commands(interface: str) -> list[list[str]]:
    """Commands that hand *interface* back to DHCP, addresses and servers alike."""
    name = f"name={interface}"
    return [
        [*_NETSH, "set", "address", name, "source=dhcp"],
        [*_NETSH, "set", "dnsservers", name, "source=dhcp"],
    ]


def commands_for(interface: str, profile: Profile | None) -> list[list[str]]:
    """Commands that put *interface* into *profile*, or into DHCP when None."""
    return dhcp_commands(interface) if profile is None else static_commands(interface, profile)
