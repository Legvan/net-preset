"""Turn a profile into the netsh invocations that apply it.

Pure functions: they build argument lists and start nothing. Every argument is a
separate list element, so the list can be handed to subprocess without a shell
and an interface name containing spaces or Polish characters survives intact.

The one value not built out of the profile is the path to netsh, which is asked
of `system` -- and so of Windows -- each time a list is built.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

from net_preset.profile import Profile
from net_preset.system import system_directory

_NETSH_EXE = "netsh.exe"


def netsh_path() -> str:
    """The full path of the netsh that ships with Windows.

    Naming it in full is a security property rather than a tidiness one. subprocess
    hands a list to CreateProcess with no application name, and CreateProcess resolves
    a bare name by searching -- the directory this program was loaded from first, then
    the current directory, and only then System32. These commands run with the
    administrator token the operator granted at the UAC prompt, and the portable build
    is meant to be carried on a USB stick and run from a Downloads folder or a desktop,
    none of which need any token to write to. A netsh.exe dropped beside the program
    would be started in preference to Windows' own and would inherit that token.

    There is deliberately no check that the file is there and no falling back to the
    bare name when it is not: the bare name is the hole. A netsh that cannot be started
    raises an OSError that `apply.run_netsh` already turns into a line in the window,
    naming the path it could not start.

    Resolved on every call rather than bound to a constant at import. The answer cannot
    change while the process runs, so a constant would have been correct -- but a
    constant is computed before any test can reach it, and on a clean machine
    %SystemRoot% and the API agree, so nothing else could tell a path built from the
    environment apart from this one. Substituting the resolver and re-deriving is the
    only way to pin where the path comes from. It also means a resolver that somehow
    failed would cost one apply rather than the window, and it costs one memcpy out of
    kernel32 per command built.
    """
    return str(PureWindowsPath(system_directory()) / _NETSH_EXE)


def _netsh() -> list[str]:
    """The three arguments every one of these invocations opens with."""
    return [netsh_path(), "interface", "ipv4"]


def static_commands(interface: str, profile: Profile) -> list[list[str]]:
    """Commands that give *interface* the fixed configuration in *profile*.

    The address comes first, then the DNS servers: cleared when the profile names
    none, otherwise set, with the alternate added as a second entry after it.
    """
    name = f"name={interface}"
    commands = [
        [
            *_netsh(),
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
                *_netsh(),
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
                    *_netsh(),
                    "add",
                    "dnsservers",
                    name,
                    f"address={profile.dns_alt}",
                    "index=2",
                    "validate=no",
                ]
            )
    else:
        commands.append([*_netsh(), "set", "dnsservers", name, "source=static", "address=none"])

    return commands


def dhcp_commands(interface: str) -> list[list[str]]:
    """Commands that hand *interface* back to DHCP, addresses and servers alike."""
    name = f"name={interface}"
    return [
        [*_netsh(), "set", "address", name, "source=dhcp"],
        [*_netsh(), "set", "dnsservers", name, "source=dhcp"],
    ]


def commands_for(interface: str, profile: Profile | None) -> list[list[str]]:
    """Commands that put *interface* into *profile*, or into DHCP when None."""
    return dhcp_commands(interface) if profile is None else static_commands(interface, profile)
