"""Read the IPv4 configuration of every adapter, straight from the IP helper API.

GetAdaptersAddresses answers in one call with everything the window needs: the
friendly name netsh matches on, the GUID that survives a rename, the interface
type, the link state, the addresses with their prefix lengths, the gateways and
the DNS servers. No process is started, so the current-state line can refresh on
a timer without stuttering.

The one thing the API will not tell us is what the adapter physically is. A
Bluetooth personal-area-network adapter reports IfType 6, the same as a real
Ethernet card. The NDIS physical medium separates them, and it lives in the
registry rather than in the API, so it is read from there and joined on the GUID.

This module reads and reports. It writes nothing and runs no subprocess.
"""

from __future__ import annotations

import contextlib
import ctypes
import winreg
from collections.abc import Iterable, Mapping
from ctypes import wintypes
from dataclasses import dataclass

__all__ = [
    "IF_TYPE_ETHERNET",
    "NDIS_MEDIUM_802_3",
    "AdapterState",
    "ethernet_adapters",
    "is_ethernet",
    "physical_media_types",
    "read_adapters",
]

ULONG = wintypes.ULONG
DWORD = wintypes.DWORD
BYTE = ctypes.c_ubyte

AF_INET = 2
NO_ERROR = 0
ERROR_BUFFER_OVERFLOW = 111

GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_INCLUDE_GATEWAYS = 0x0080

IF_TYPE_ETHERNET = 6
IF_OPER_STATUS_UP = 1
FLAG_DHCP_V4_ENABLED = 0x0004

NDIS_MEDIUM_802_3 = 14

_NETWORK_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
)
_APIPA_PREFIX = "169.254."

# The API asks for a buffer and reports how much it actually wanted; this is only
# the opening guess, comfortably above what a normal machine needs.
_FIRST_GUESS_BYTES = 15000
_BUFFER_ATTEMPTS = 4


class SOCKADDR_IN(ctypes.Structure):
    _fields_ = [
        ("sin_family", ctypes.c_ushort),
        ("sin_port", ctypes.c_ushort),
        ("sin_addr", BYTE * 4),
        ("sin_zero", ctypes.c_char * 8),
    ]


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ULONG),
    ("Flags", DWORD),
    ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ULONG),
    ("PreferredLifetime", ULONG),
    ("LeaseLifetime", ULONG),
    ("OnLinkPrefixLength", BYTE),
]


class IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
    ("Length", ULONG),
    ("Reserved", DWORD),
    ("Next", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_DNS_SERVER_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_DNS_SERVER_ADDRESS._fields_ = [
    ("Length", ULONG),
    ("Reserved", DWORD),
    ("Next", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


# Every field of the Win32 structure, in order and to the end: the tail cannot be
# left out, because the API strides from one adapter to the next by this size.
# Flags is a plain ULONG masked by hand rather than a ctypes bitfield, whose
# packing rules are one more thing that can silently differ.
IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ULONG),
    ("IfIndex", DWORD),
    ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", BYTE * 8),
    ("PhysicalAddressLength", ULONG),
    ("Flags", ULONG),
    ("Mtu", ULONG),
    ("IfType", DWORD),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", DWORD),
    ("ZoneIndices", ULONG * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_ulonglong),
    ("ReceiveLinkSpeed", ctypes.c_ulonglong),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
    ("Ipv4Metric", ULONG),
    ("Ipv6Metric", ULONG),
    ("Luid", ctypes.c_ulonglong),
    ("Dhcpv4Server", SOCKET_ADDRESS),
    ("CompartmentId", DWORD),
    ("NetworkGuid", BYTE * 16),
    ("ConnectionType", ctypes.c_int),
    ("TunnelType", ctypes.c_int),
    ("Dhcpv6Server", SOCKET_ADDRESS),
    ("Dhcpv6ClientDuid", BYTE * 130),
    ("Dhcpv6ClientDuidLength", ULONG),
    ("Dhcpv6Iaid", ULONG),
    ("FirstDnsSuffix", ctypes.c_void_p),
]


@dataclass(frozen=True)
class AdapterState:
    """What one adapter looks like right now, as the IP helper API describes it.

    `addresses` pairs each IPv4 address with its prefix length. `gateways` and
    `dns` hold addresses only. All three keep the order the API returned.
    """

    guid: str
    name: str
    description: str
    if_index: int
    if_type: int
    connected: bool
    dhcp: bool
    addresses: tuple[tuple[str, int], ...]
    gateways: tuple[str, ...]
    dns: tuple[str, ...]

    @property
    def primary(self) -> tuple[str, int] | None:
        """The address worth showing, or None when the adapter has none.

        The first real address wins; an APIPA one is a last resort, because it
        says what went wrong and the window would otherwise have nothing to show.
        """
        for entry in self.addresses:
            if not entry[0].startswith(_APIPA_PREFIX):
                return entry
        return self.addresses[0] if self.addresses else None

    @property
    def apipa(self) -> bool:
        """True when the adapter has nothing but a self-assigned 169.254 address.

        That is what Windows falls back to when a DHCP server never answered, so
        it means the profile did not take rather than that the link is dead.
        """
        return bool(self.addresses) and all(
            address.startswith(_APIPA_PREFIX) for address, _ in self.addresses
        )


def _ipv4(socket_address: SOCKET_ADDRESS) -> str | None:
    """The dotted quad behind a SOCKET_ADDRESS, or None when it is not IPv4."""
    if not socket_address.lpSockaddr:
        return None
    head = ctypes.cast(socket_address.lpSockaddr, ctypes.POINTER(SOCKADDR_IN)).contents
    if head.sin_family != AF_INET:
        return None
    return ".".join(str(octet) for octet in head.sin_addr)


def _unicast_addresses(first) -> tuple[tuple[str, int], ...]:
    """Walk a unicast chain into (address, prefix length) pairs."""
    found = []
    node = first
    while node:
        entry = node.contents
        address = _ipv4(entry.Address)
        if address is not None:
            found.append((address, int(entry.OnLinkPrefixLength)))
        node = entry.Next
    return tuple(found)


def _server_addresses(first) -> list[str]:
    """Walk a gateway or DNS chain into addresses; both have the same layout."""
    found = []
    node = first
    while node:
        entry = node.contents
        address = _ipv4(entry.Address)
        if address is not None:
            found.append(address)
        node = entry.Next
    return found


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """The values with repeats dropped, in the order they first appeared."""
    return tuple(dict.fromkeys(values))


def _adapter_state(raw: IP_ADAPTER_ADDRESSES) -> AdapterState:
    """Copy one API structure into a plain frozen record.

    Everything is copied rather than referenced: once the call returns, the
    buffer the strings point into is free to go.
    """
    return AdapterState(
        guid=raw.AdapterName.decode("ascii") if raw.AdapterName else "",
        name=raw.FriendlyName or "",
        description=raw.Description or "",
        if_index=int(raw.IfIndex),
        if_type=int(raw.IfType),
        connected=raw.OperStatus == IF_OPER_STATUS_UP,
        dhcp=bool(raw.Flags & FLAG_DHCP_V4_ENABLED),
        addresses=_unicast_addresses(raw.FirstUnicastAddress),
        gateways=tuple(_server_addresses(raw.FirstGatewayAddress)),
        # The same server comes back several times over on some adapters.
        dns=_unique(_server_addresses(raw.FirstDnsServerAddress)),
    )


def _walk(buffer) -> list[AdapterState]:
    """Follow the Next pointers from the head of the buffer to the last adapter."""
    adapters = []
    node = ctypes.pointer(buffer[0])
    while node:
        adapters.append(_adapter_state(node.contents))
        node = node.contents.Next
    return adapters


def _read_raw() -> list[AdapterState]:
    """Ask the IP helper API for every IPv4-bound adapter, growing the buffer as told.

    The buffer is an array of the structure rather than a string buffer, so that
    ctypes aligns it the way the structure needs.
    """
    size = ULONG(_FIRST_GUESS_BYTES)
    flags = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_INCLUDE_GATEWAYS
    for _ in range(_BUFFER_ATTEMPTS):
        count = size.value // ctypes.sizeof(IP_ADAPTER_ADDRESSES) + 2
        buffer = (IP_ADAPTER_ADDRESSES * count)()
        size = ULONG(ctypes.sizeof(buffer))
        result = ctypes.windll.iphlpapi.GetAdaptersAddresses(
            ULONG(AF_INET), ULONG(flags), None, buffer, ctypes.byref(size)
        )
        if result == NO_ERROR:
            return _walk(buffer)
        if result != ERROR_BUFFER_OVERFLOW:
            raise OSError(f"GetAdaptersAddresses failed with {result}")
    raise OSError("GetAdaptersAddresses kept asking for a larger buffer")


def read_adapters() -> list[AdapterState]:
    """Every IPv4-bound adapter, or an empty list when the read fails.

    A failed read costs the list, never the window: this runs on a timer behind
    the current-state line, and there is nothing the user could do about a
    refusal from the IP stack anyway.
    """
    # OSError is the DLL refusing to load and the failures raised above; ValueError
    # covers what ctypes raises over a pointer or a byte string it cannot follow.
    try:
        return _read_raw()
    except OSError, ValueError:
        return []


def _media_entry(root: winreg.HKEYType, name: str) -> tuple[str, int] | None:
    """The (GUID, NDIS medium) pair one network class subkey publishes.

    None when the subkey publishes neither, which is the normal shape of the
    bookkeeping subkeys that sit alongside the real drivers.
    """
    with contextlib.suppress(OSError, ValueError, TypeError), winreg.OpenKey(root, name) as sub:
        guid, _ = winreg.QueryValueEx(sub, "NetCfgInstanceId")
        medium, _ = winreg.QueryValueEx(sub, "*PhysicalMediaType")
        return str(guid).upper(), int(medium)
    return None


def physical_media_types() -> dict[str, int]:
    """Map every adapter GUID the registry knows to its NDIS physical medium.

    This is the only place the truth about a Bluetooth PAN adapter is written
    down: the API calls it Ethernet, the registry calls it medium 10. Any
    registry failure yields an empty map, and `is_ethernet` then keeps
    everything the API offered.
    """
    media: dict[str, int] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _NETWORK_CLASS_KEY) as root:
            index = 0
            while True:
                try:
                    child = winreg.EnumKey(root, index)
                except OSError:
                    break  # EnumKey is how the subkeys announce that they ran out
                index += 1
                entry = _media_entry(root, child)
                if entry is not None:
                    media[entry[0]] = entry[1]
    except OSError:
        return {}
    return media


def is_ethernet(adapter: AdapterState, media: Mapping[str, int]) -> bool:
    """True when the adapter is a cabled Ethernet card the operator could plug into.

    The interface type rules out Wi-Fi, loopback and tunnels. It does not rule out
    a Bluetooth personal-area-network adapter, which claims IfType 6 like the real
    thing; the NDIS medium from `physical_media_types` is what separates them.

    An adapter the registry says nothing about is kept. Failing open is deliberate:
    an unknown driver must not hide a card the operator can see in Windows.
    """
    if adapter.if_type != IF_TYPE_ETHERNET:
        return False
    medium = media.get(adapter.guid.upper())
    return medium is None or medium == NDIS_MEDIUM_802_3


def ethernet_adapters() -> list[AdapterState]:
    """The Ethernet cards among the adapters, in the order the API returned them."""
    media = physical_media_types()
    return [adapter for adapter in read_adapters() if is_ethernet(adapter, media)]
