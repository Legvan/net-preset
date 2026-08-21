"""The profile model: a named IPv4 configuration and the rules that make it valid.

This module is pure logic. It imports nothing outside the standard library and knows
nothing about Windows, so it can be exercised on any platform.
"""

from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_MASK = "255.255.255.0"
MAX_NAME_LENGTH = 32

_NOT_AN_IPV4 = "Nieprawidłowy adres IPv4"


@dataclass(frozen=True)
class Profile:
    """A named IPv4 configuration, as the user typed it.

    Every field is the raw text from the form: a profile may well be invalid, and
    `validate` is what decides. The optional fields are empty strings when unset.
    """

    name: str
    address: str
    mask: str
    gateway: str = ""
    dns: str = ""
    dns_alt: str = ""

    @property
    def label(self) -> str:
        """How the profile reads in a list: the address first, then the name."""
        return f"{self.address} ({self.name})"


@dataclass(frozen=True)
class FieldError:
    """One problem with one field, ready to be shown next to it."""

    field: str
    message: str


def parse_ipv4(text: str) -> int | None:
    """Return a dotted quad as a 32-bit integer, or None when the text is not one.

    Strict on purpose: no surrounding whitespace, no leading zeros (which some
    parsers read as octal), and no digits outside ASCII. Arabic-Indic digits pass
    `str.isdigit()` and `int()` accepts them, so ASCII is checked first.
    """
    parts = text.split(".")
    if len(parts) != 4:
        return None

    value = 0
    for part in parts:
        if not part.isascii() or not part.isdigit():
            return None
        if len(part) > 1 and part.startswith("0"):
            return None
        octet = int(part)
        if octet > 255:
            return None
        value = value << 8 | octet
    return value


def prefix_length(mask: str) -> int | None:
    """Return how many leading ones a subnet mask has, or None when it is not a mask.

    A mask is a run of ones followed by a run of zeros. Inverting it turns that into
    a run of zeros followed by a run of ones, and a value of the form 2**n - 1 is
    exactly one that clears when incremented and ANDed with itself.
    """
    value = parse_ipv4(mask)
    if value is None:
        return None

    inverted = ~value & 0xFFFFFFFF
    if inverted & (inverted + 1) != 0:
        return None
    return 32 - inverted.bit_length()


def subnet_mask(prefix: int) -> str | None:
    """Return the dotted mask a prefix length stands for, or None when it is not one.

    The inverse of `prefix_length`, and beside it because they are one idea read in
    two directions: the form takes a mask and the commands need a prefix, while the
    adapter reports a prefix and the operator reads a mask.

    Only 0 to 32 are prefix lengths, and the guard is not decoration: `_mask_bits`
    raises on anything above 32, Python refusing to shift by a negative count, and
    answers a negative prefix with 0.0.0.0 as though it had been given a /0.
    """
    if not 0 <= prefix <= 32:
        return None
    bits = _mask_bits(prefix)
    return ".".join(str(bits >> shift & 0xFF) for shift in (24, 16, 8, 0))


def _mask_bits(prefix: int) -> int:
    """The mask for a prefix length, as a 32-bit integer."""
    return (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF


def validate(profile: Profile, other_names: Iterable[str] = ()) -> list[FieldError]:
    """Report everything wrong with a profile at once.

    `other_names` are the names already in use by other profiles. An empty list means
    the profile is valid; this never raises, so the caller can validate on every
    keystroke.
    """
    errors: list[FieldError] = []

    name = profile.name.strip()
    if not name:
        errors.append(FieldError("name", "Nazwa nie może być pusta"))
    elif len(name) > MAX_NAME_LENGTH:
        errors.append(FieldError("name", f"Nazwa jest za długa (limit {MAX_NAME_LENGTH} znaków)"))
    elif name.casefold() in {other.strip().casefold() for other in other_names}:
        errors.append(FieldError("name", "Nazwa jest już zajęta"))

    address = parse_ipv4(profile.address)
    if address is None:
        errors.append(FieldError("address", _NOT_AN_IPV4))

    prefix = prefix_length(profile.mask)
    if prefix is None:
        errors.append(FieldError("mask", "Maska musi być ciągła"))
    elif not 1 <= prefix <= 31:
        errors.append(FieldError("mask", "Maska nie pozostawia miejsca na hosty"))
        prefix = None  # nothing below can be judged against a mask this wide or this narrow

    # A /31 is a point-to-point link: both of its addresses are usable, so it has
    # neither a network nor a broadcast address to collide with.
    if address is not None and prefix is not None and prefix <= 30:
        network = address & _mask_bits(prefix)
        broadcast = network | (0xFFFFFFFF >> prefix)
        if address == network:
            errors.append(FieldError("address", "To adres sieci, nie hosta"))
        elif address == broadcast:
            errors.append(FieldError("address", "To adres rozgłoszeniowy, nie hosta"))

    if profile.gateway:
        gateway = parse_ipv4(profile.gateway)
        if gateway is None:
            errors.append(FieldError("gateway", _NOT_AN_IPV4))
        elif address is not None and prefix is not None:
            bits = _mask_bits(prefix)
            if gateway & bits != address & bits:
                errors.append(FieldError("gateway", "Brama spoza podsieci"))

    if profile.dns and parse_ipv4(profile.dns) is None:
        errors.append(FieldError("dns", _NOT_AN_IPV4))

    if profile.dns_alt:
        if parse_ipv4(profile.dns_alt) is None:
            errors.append(FieldError("dns_alt", _NOT_AN_IPV4))
        elif not profile.dns:
            errors.append(FieldError("dns_alt", "Najpierw podaj podstawowy DNS"))

    return errors
