import ctypes
import sys
import winreg

import pytest

from net_preset import adapters
from net_preset.adapters import (
    AF_INET,
    IF_TYPE_ETHERNET,
    IP_ADAPTER_ADDRESSES,
    IP_ADAPTER_DNS_SERVER_ADDRESS,
    NDIS_MEDIUM_802_3,
    SOCKADDR_IN,
    AdapterState,
    _adapter_state,
    ethernet_adapters,
    is_ethernet,
    physical_media_types,
    read_adapters,
)

ETHERNET_GUID = "{2965BC8E-BEB8-42BF-9D34-EC5C080515D0}"
BLUETOOTH_GUID = "{D97D2449-EA62-4798-AA11-0DEC980E2CCD}"


def state(**overrides) -> AdapterState:
    fields = {
        "guid": ETHERNET_GUID,
        "name": "Ethernet",
        "description": "Contoso Gigabit Ethernet Adapter",
        "if_index": 17,
        "if_type": IF_TYPE_ETHERNET,
        "connected": False,
        "dhcp": True,
        "addresses": (("169.254.42.17", 16),),
        "gateways": (),
        "dns": (),
    }
    fields.update(overrides)
    return AdapterState(**fields)


MEDIA = {ETHERNET_GUID: NDIS_MEDIUM_802_3, BLUETOOTH_GUID: 10}


def test_a_real_ethernet_card_passes_the_filter():
    assert is_ethernet(state(), MEDIA) is True


def test_bluetooth_pan_is_rejected_despite_claiming_ethernet():
    # The Bluetooth PAN adapter reports IfType 6, exactly like a real card.
    # Only the NDIS physical medium tells them apart.
    adapter = state(guid=BLUETOOTH_GUID, name="Połączenie sieciowe Bluetooth")
    assert is_ethernet(adapter, MEDIA) is False


def test_wifi_is_rejected_on_its_interface_type():
    assert is_ethernet(state(if_type=71), MEDIA) is False


def test_loopback_is_rejected_on_its_interface_type():
    assert is_ethernet(state(if_type=24), MEDIA) is False


def test_an_adapter_missing_from_the_registry_is_kept():
    # Fail open: an unknown medium must not hide a card the operator can see.
    assert is_ethernet(state(guid="{UNKNOWN}"), MEDIA) is True


def test_the_media_lookup_ignores_guid_casing():
    assert is_ethernet(state(guid=ETHERNET_GUID.lower()), MEDIA) is True
    # The rejection is what pins the lookup. Acceptance alone cannot fail: drop the
    # casing defence and the miss lands on the fail-open branch, which also says True.
    # A casing miss on the Bluetooth GUID is precisely how a PAN adapter would slip
    # through as a real Ethernet card.
    assert is_ethernet(state(guid=BLUETOOTH_GUID.lower()), MEDIA) is False


def test_an_apipa_address_is_recognised():
    assert state().apipa is True


def test_a_real_lease_is_not_apipa():
    assert state(addresses=(("192.168.11.2", 24),)).apipa is False


def test_no_address_at_all_is_not_apipa():
    assert state(addresses=()).apipa is False


def test_the_primary_address_skips_apipa_when_a_real_one_exists():
    adapter = state(addresses=(("169.254.1.1", 16), ("192.168.11.2", 24)))
    assert adapter.primary == ("192.168.11.2", 24)


def test_the_primary_address_falls_back_to_apipa():
    assert state().primary == ("169.254.42.17", 16)


def test_no_addresses_means_no_primary():
    assert state(addresses=()).primary is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads the live Windows IP stack")
def test_the_live_read_returns_the_loopback_interface():
    adapters = read_adapters()
    assert any(adapter.if_type == 24 for adapter in adapters)
    assert all(isinstance(adapter.name, str) and adapter.guid for adapter in adapters)


@pytest.mark.skipif(sys.platform != "win32", reason="reads the live Windows registry")
def test_the_media_map_is_populated():
    media = physical_media_types()
    assert media
    assert all(isinstance(value, int) for value in media.values())
    assert all(key == key.upper() for key in media)


@pytest.mark.skipif(sys.platform != "win32", reason="reads the live Windows IP stack")
def test_the_filtered_list_excludes_bluetooth_and_wifi():
    for adapter in ethernet_adapters():
        assert adapter.if_type == IF_TYPE_ETHERNET
        assert "Bluetooth" not in adapter.description


def dns_chain(servers: list[str]) -> tuple[object, list[object]]:
    """Build a DNS server chain shaped like the one the API hands back.

    Returns the head pointer and every object that owns a piece of the chain's
    memory: the caller has to keep those alive, because the nodes reach each
    other through raw pointers that ctypes does not track.
    """
    nodes = [IP_ADAPTER_DNS_SERVER_ADDRESS() for _ in servers]
    sockaddrs = []
    for node, text in zip(nodes, servers, strict=True):
        sockaddr = SOCKADDR_IN()
        sockaddr.sin_family = AF_INET
        sockaddr.sin_addr = (ctypes.c_ubyte * 4)(*(int(part) for part in text.split(".")))
        sockaddrs.append(sockaddr)
        node.Address.lpSockaddr = ctypes.cast(ctypes.pointer(sockaddr), ctypes.c_void_p)
        node.Address.iSockaddrLength = ctypes.sizeof(SOCKADDR_IN)
    for node, following in zip(nodes, nodes[1:], strict=False):
        node.Next = ctypes.pointer(following)
    return ctypes.pointer(nodes[0]), [*nodes, *sockaddrs]


def test_a_repeated_dns_server_is_reported_once():
    # The live Wi-Fi adapter answers with the same server three times over.
    head, anchors = dns_chain(["192.168.11.1", "192.168.11.1", "1.1.1.1"])
    raw = IP_ADAPTER_ADDRESSES()
    raw.FirstDnsServerAddress = head
    assert _adapter_state(raw).dns == ("192.168.11.1", "1.1.1.1")
    assert anchors  # the chain has to stay referenced until the read above is done


def test_an_adapter_naming_nothing_reads_as_empty_text():
    # The name and description fields are pointers, and a null one must not
    # reach the window as None.
    empty = _adapter_state(IP_ADAPTER_ADDRESSES())
    assert (empty.name, empty.description, empty.guid) == ("", "", "")


def test_the_adapter_structure_matches_the_win32_layout():
    # One wrong field silently shifts every value that follows it.
    assert ctypes.sizeof(IP_ADAPTER_ADDRESSES) == 448


class FakeRegistryKey:
    """The little of a registry key that `physical_media_types` actually touches."""

    def __init__(self, values=None, children=None):
        self.values = values or {}
        self.children = children or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def install_fake_registry(monkeypatch, root: FakeRegistryKey) -> None:
    """Answer the three winreg calls behind `physical_media_types` out of `root`."""

    def open_key(parent, name):
        if not isinstance(parent, FakeRegistryKey):
            return root  # the only real handle it opens is HKEY_LOCAL_MACHINE
        try:
            return parent.children[name]
        except KeyError:
            raise OSError(f"no subkey named {name}") from None

    def enum_key(key, index):
        try:
            return list(key.children)[index]
        except IndexError:
            raise OSError("no more subkeys") from None

    def query_value(key, name):
        try:
            return key.values[name], winreg.REG_SZ
        except KeyError:
            raise OSError(f"no value named {name}") from None

    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "EnumKey", enum_key)
    monkeypatch.setattr(winreg, "QueryValueEx", query_value)


def test_the_media_map_upper_cases_the_guid_the_registry_stored(monkeypatch):
    # Every GUID this machine's registry stores is upper-cased already, so no live
    # read can tell whether the map normalises them; a registry that stores one in
    # lower case is the only way to pin it. is_ethernet joins on that assumption,
    # and a Bluetooth GUID that misses the join fails open as a real card.
    install_fake_registry(
        monkeypatch,
        FakeRegistryKey(
            children={
                "0001": FakeRegistryKey(
                    {"NetCfgInstanceId": BLUETOOTH_GUID.lower(), "*PhysicalMediaType": 10}
                ),
                "0002": FakeRegistryKey(
                    {"NetCfgInstanceId": ETHERNET_GUID, "*PhysicalMediaType": "14"}
                ),
                "0003": FakeRegistryKey({"DriverDesc": "a subkey with no medium to report"}),
            }
        ),
    )
    assert physical_media_types() == {BLUETOOTH_GUID: 10, ETHERNET_GUID: NDIS_MEDIUM_802_3}


def test_a_refusal_from_the_ip_stack_costs_the_list_not_the_window(monkeypatch):
    def refuse():
        raise OSError("GetAdaptersAddresses failed with 5")

    monkeypatch.setattr(adapters, "_read_raw", refuse)
    assert read_adapters() == []
