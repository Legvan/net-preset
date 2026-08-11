"""Apply a profile to an adapter, then report what the adapter actually took.

netsh is not a trustworthy witness. It answers in the system language, it refuses with
a non-zero code for an address the adapter already has, and it prints a usage dump with
an exit code of zero when it dislikes an argument. So no exit code is believed on its
own: the commands run, the adapter is read back until it carries what was asked of it,
and the message names what it really carries rather than what was requested.

Every touch of the operating system — running a command, reading the adapter, waiting
between reads — goes through a parameter with a real default, so the decisions above can
be exercised without an adapter, without an administrator token and without a wait.
"""

from __future__ import annotations

import ctypes
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from net_preset.adapters import AdapterState, read_adapters
from net_preset.commands import commands_for
from net_preset.profile import Profile, prefix_length

ALREADY_EXISTS_CODE = 1
DEFAULT_ATTEMPTS = 10
POLL_SECONDS = 0.3
NETSH_TIMEOUT_SECONDS = 20.0

# How netsh says that the address it was told to set is the one already configured.
# Both languages are listed because the answer follows the system, not the app.
_ALREADY_EXISTS = ("już istnieje", "already exists")

# Not an exit code netsh can return: it stands for a netsh that never answered at all.
_NO_ANSWER_CODE = -1

_UNREADABLE = "Nie udało się odczytać stanu karty"

# What an empty list of gateways or servers is called when the status line names one.
_NOTHING = "brak"

# A command netsh would not carry out, and the same thing once the address has gone
# through: the first command of every run is the one that moves the address.
_REFUSED = "Nie udało się wykonać polecenia"
_REFUSED_LATE = "Adres już zmieniony, ale nie udało się wykonać polecenia"

# A connected card that took every value of a static profile and never left DHCP. Not
# phrased as the mirror of "DHCP się nie włączyło" further down: the two would differ by
# one letter, mean opposite things, and share a status line read at a glance.
_STILL_DHCP = "Adres ustawiony, ale karta nadal jest klientem DHCP"

# A static profile stored on a card with no cable in it. Everything but the flag is
# already on the card and the flag follows the link, so this is a success that says what
# it is waiting for — the same shape as the DHCP line that waits for the same thing.
_WAITING = "czekam na kabel"


@dataclass(frozen=True)
class CommandResult:
    """What one netsh invocation returned: its exit code and everything it printed."""

    returncode: int
    output: str


@dataclass(frozen=True)
class Outcome:
    """The verdict on an attempt, with the Polish line that says it in the window."""

    ok: bool
    message: str


def run_netsh(command: Sequence[str]) -> CommandResult:
    """Run one netsh invocation and return its exit code together with its output.

    CREATE_NO_WINDOW keeps netsh from flashing a console over the window on every click.
    Both streams are kept, because netsh writes its refusals to whichever it pleases, and
    a newline goes between them: stdout does not always end in one, and without it the
    last word of stdout runs into the first word of stderr.

    The timeout is what keeps a wedged netsh from taking the window with it. This runs on
    a worker thread the buttons wait on, so a call that never returns would leave them
    disabled for good. Twenty seconds is far longer than any of these commands needs —
    handing the adapter back to DHCP is the slowest and takes a couple — and short enough
    that the operator gets an answer instead of a dead window.

    It is a budget rather than a ceiling, and a caller sizing its own patience should know
    the difference. On Windows the timeout kills the direct child and then waits in
    communicate() for every writer to let go of the inherited pipe, so a child that started
    a child of its own holds the call open well past it: measured at 19 s against a 1.5 s
    budget, where a childless one came back at 1.5 s. None of these three commands forks,
    so the budget holds in practice — but a netsh wedged inside a driver call is precisely
    what TerminateProcess cannot finish, and nothing here can promise otherwise.
    """
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=NETSH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed it, so nothing is left running behind us.
        waited = f"{NETSH_TIMEOUT_SECONDS:g}"
        return CommandResult(_NO_ANSWER_CODE, f"netsh nie odpowiedział w ciągu {waited} s")
    except OSError as error:
        # A netsh that cannot even be started is still an answer the window can show.
        return CommandResult(_NO_ANSWER_CODE, str(error))

    streams = (_decode(completed.stdout), _decode(completed.stderr))
    return CommandResult(
        completed.returncode, "\n".join(part for part in streams if part.strip()).strip()
    )


def read_by_guid(guid: str) -> AdapterState | None:
    """The adapter with this GUID as it stands right now, or None when it is gone.

    Matched on the GUID rather than the friendly name, because the name is the one
    thing about an adapter that can change while the window is open.
    """
    for adapter in read_adapters():
        if adapter.guid == guid:
            return adapter
    return None


def matches_request(adapter: AdapterState, profile: Profile | None) -> bool:
    """True when the adapter is now carrying what was asked of it.

    A static profile writes three values and a mode, and all four are read back, because
    netsh answers a syntax error with an exit code of zero and there is nothing else to
    trust. Checking the address alone passes a card that took the address and quietly kept
    the last site's name servers — an office machine on a perfectly good IP with no working
    name resolution, which is the failure that looks least like one.

    - **The address**, together with its prefix length, among the adapter's addresses.
      Another address kept alongside it does not spoil the match: a card can hold more
      than one, and only the one that was asked for is ours.
    - **The DHCP flag off, on a card that is connected.** Static is a mode as well as
      three values, and the values alone do not prove the mode changed. The link is what
      makes the flag readable at all — see `_dhcp_cleared`.
    - **The gateway.** A profile naming one needs it among the adapter's gateways —
      among, not equal to, because a card may list more than one default route and only
      the one asked for is the subject. A profile naming none needs the adapter to have
      none at all, `gateway=none` being a command whose whole purpose is to leave it bare.
    - **The DNS servers**, exactly and in order. The profile's primary is index 1 and its
      alternate index 2, which is the order netsh is told to set them in and the order the
      API reports them in. The expected list is deduplicated the way `AdapterState.dns`
      is, so a profile naming one server in both fields still matches the card that
      carries it once.
    - **The DHCP flag off.** Static is a mode, not three values, and the values alone do
      not prove the mode changed. Measured on a media-disconnected card: `set address
      source=static` writes the address, the gateway and the servers, and leaves
      EnableDHCP at 1. The card ends up a DHCP client with a manual address bolted
      alongside its APIPA one — every value the profile asked for is present, and a
      lease may replace all of them the moment a cable goes in. Asking only what the
      card carries reports that as a complete success, which is what it did.

    DHCP asks for more than the flag: an adapter whose lease never arrived has the flag
    set and a self-assigned address, which is exactly the case worth telling the operator
    about. Nothing is asked of its gateway or its servers — what a DHCP server hands out
    is not ours to predict, and an expectation invented here would report a working lease
    as a failure.
    """
    if profile is None:
        return adapter.dhcp and bool(adapter.addresses) and not adapter.apipa

    return (
        _address_matches(adapter, profile)
        and _dhcp_cleared(adapter)
        and _gateway_matches(adapter, profile)
        and _dns_matches(adapter, profile)
    )


def _dhcp_cleared(adapter: AdapterState) -> bool:
    """True unless the card is still a DHCP client on a link that has had its chance.

    Windows defers the DHCP-disable of a static apply until the media comes up. Measured
    twice on the same card: `set address … source=static` on a disconnected one writes the
    address, the gateway and the servers at once and leaves EnableDHCP at 1, so the card
    reads as a DHCP client carrying a manual address beside its APIPA one. Fifteen minutes
    later, with nothing run in between, a cable going in cleared the flag on its own. On a
    card that was connected to begin with, the whole thing lands in one go.

    So the flag is evidence only where the link gave it a chance to change. Asking it of a
    disconnected card would report a configuration that is stored, complete and about to
    work as a failure, and asking nothing of a connected one is the hole this closes.
    """
    return not (adapter.connected and adapter.dhcp)


def _address_matches(adapter: AdapterState, profile: Profile) -> bool:
    prefix = prefix_length(profile.mask)
    if prefix is None:
        return False
    return (profile.address, prefix) in adapter.addresses


def _gateway_matches(adapter: AdapterState, profile: Profile) -> bool:
    if not profile.gateway:
        return not adapter.gateways
    return profile.gateway in adapter.gateways


def _dns_matches(adapter: AdapterState, profile: Profile) -> bool:
    return adapter.dns == _wanted_dns(profile)


def _wanted_dns(profile: Profile) -> tuple[str, ...]:
    """The servers the profile asks for, in the order netsh is told to set them."""
    return tuple(dict.fromkeys(server for server in (profile.dns, profile.dns_alt) if server))


def apply_profile(
    adapter: AdapterState,
    profile: Profile | None,
    *,
    runner: Callable[[Sequence[str]], CommandResult] = run_netsh,
    reader: Callable[[str], AdapterState | None] = read_by_guid,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = DEFAULT_ATTEMPTS,
) -> Outcome:
    """Put *adapter* into *profile* — or back onto DHCP when it is None — and say what came of it.

    The commands run in order and the first genuine refusal ends the attempt: there is
    no point setting DNS servers on an address that was rejected. What follows is a
    read-back, because netsh reporting success is not the same as the adapter having
    changed.

    Where the refusal fell is carried into the message. The address is always the first
    command, so a refusal anywhere after it means the card has already moved — and a
    message that named only the refusal would leave the operator thinking nothing had.
    """
    for position, command in enumerate(commands_for(adapter.name, profile)):
        result = runner(command)
        if not _succeeded(result):
            return _refusal(result, position)

    return _report(_poll(adapter.guid, profile, reader, sleep, attempts), profile)


def _decode(raw: bytes) -> str:
    """Turn netsh output into text, in whichever of the two encodings netsh used.

    Measured, rather than reasoned about: netsh writes UTF-8 down a pipe. A refusal naming
    a Polish word came back with the two bytes c5 82 for "ł" — UTF-8 — with this process's
    console at 852, at 65001 and at 1250 alike, and from a process with no console at all.
    What code page a console would have used says nothing about what a program writes into
    a pipe, so there is nothing to ask Windows about here.

    UTF-8 therefore comes first, and strictly: the failure to decode is the whole signal.
    Text in the system OEM page is almost never valid UTF-8 — cp852 spells "ż" as a lone
    be, which is not a legal sequence — so the two cases can be told apart without
    guessing. Only then are undecodable bytes replaced rather than raised over: a mangled
    character costs a letter of a message, an exception would cost the whole attempt.

    Getting this backwards is not cosmetic. A mangled "już istnieje" stops matching, and an
    address that was already correct is reported to the operator as a failure.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}", errors="replace")


def _succeeded(result: CommandResult) -> bool:
    """True when netsh did what was asked, including when it was already done.

    netsh refuses with code 1 and "the object already exists" when the address being set
    is the one the adapter already holds. Nothing is wrong and nothing needs doing, so
    that answer counts as success. Code 1 saying anything else is a real refusal.
    """
    if result.returncode == 0:
        return True
    if result.returncode != ALREADY_EXISTS_CODE:
        return False
    said = result.output.casefold()
    return any(phrase in said for phrase in _ALREADY_EXISTS)


def _refusal(result: CommandResult, position: int) -> Outcome:
    """The verdict on a command netsh would not carry out, in netsh's own words.

    The output is folded onto one line: it lands in a status line, and netsh answers
    some refusals with several lines of them.

    *position* is where the refused command stood in the run. Anything past the first
    means the address command has already been carried out, so the card is not where it
    was: restoring DHCP with `set dnsservers` refused leaves it on a leased address while
    still pointing at the site's static servers, and in an office that reads as no
    internet on a perfectly good IP. The operator is told the address moved, because
    nothing else in the window will tell them the half that did work.
    """
    said = " ".join(result.output.split())
    if not said:
        said = f"netsh zakończył się kodem {result.returncode}"
    if position:
        return Outcome(False, f"{_REFUSED_LATE}: {said}")
    return Outcome(False, f"{_REFUSED}: {said}")


def _poll(
    guid: str,
    profile: Profile | None,
    reader: Callable[[str], AdapterState | None],
    sleep: Callable[[float], None],
    attempts: int,
) -> AdapterState | None:
    """Read the adapter until it carries the request, or until the attempts run out.

    The first read comes before any sleep, so an adapter that already holds what was
    asked costs one read and no waiting. A DHCP lease is what the rest of the attempts
    are for: it arrives a moment after netsh returns, not with it.

    The last state read is returned even when it never matched — it is what the message
    has to describe.
    """
    state = None
    for attempt in range(attempts):
        if attempt:
            sleep(POLL_SECONDS)
        state = reader(guid)
        if state is not None and matches_request(state, profile):
            return state
    return state


def _report(state: AdapterState | None, profile: Profile | None) -> Outcome:
    """Turn the state the adapter settled on into the line the operator reads.

    A static profile that matched on a card with no cable in it is a success with a
    caveat, and it gets the caveat DHCP already had. The configuration is stored and the
    address, the gateway and the servers are on the card; only the DHCP flag is still
    waiting on the link, and it clears itself when the cable goes in. Saying just
    "Ustawiono" would leave a technician who is configuring ahead of the site visit with
    no idea that anything is outstanding, and saying nothing about the cable would have
    them hunting for a fault that is not there.
    """
    if state is None:
        return Outcome(False, _UNREADABLE)

    if matches_request(state, profile):
        if profile is None:
            # matches_request has already established that there is a real address.
            address, prefix = state.primary
            return Outcome(True, f"DHCP: {address} /{prefix}")
        # The adapter carries exactly what was asked, or the match would have failed.
        set_text = f"Ustawiono {_request_text(profile)}"
        if not state.connected:
            return Outcome(True, f"{set_text} — {_WAITING}")
        return Outcome(True, set_text)

    return _dhcp_mismatch(state) if profile is None else _static_mismatch(state, profile)


def _dhcp_mismatch(state: AdapterState) -> Outcome:
    """Why the card is not on a leased address, in the order the causes rule each other out.

    The flag is asked about first, because every other answer here begins by claiming DHCP
    is on. netsh can report success for a command it did not carry out, and a card that
    never took the flag is still sitting on the configuration it had — telling its operator
    it was waiting for a cable would be false, and false as a success at that.

    Once the flag is on, an unplugged card cannot be leased anything, and that is not a
    failure of what the operator just did: it is worth waiting for. A card that is plugged
    in and still has no lease is the one thing here that went wrong on its own.
    """
    if not state.dhcp:
        address = _address_text(state)
        if address is None:
            return Outcome(False, "DHCP się nie włączyło")
        return Outcome(False, f"DHCP się nie włączyło, karta ma {address}")
    if not state.connected:
        return Outcome(True, "DHCP włączone — czekam na kabel")
    return Outcome(False, "DHCP włączone, brak dzierżawy")


def _static_mismatch(state: AdapterState, profile: Profile) -> Outcome:
    """Why the card is not carrying the profile, naming what it has instead.

    Naming it is the point of the read-back: it turns "it did not work" into something the
    operator can act on without opening a console. The address is asked about first
    because a card that never took it makes the other three beside the point; the three
    after it open by saying the address is set, so a card that is reachable but has no
    route, no name resolution or no static mode does not read as a card that never changed
    at all. What it has is named on both sides, and the address it settled on is not
    repeated — the *Teraz* line directly above the status is already showing it.

    The flag comes straight after the address and ahead of the other two, and the order is
    a ranking of what the operator cannot do. A wrong address is a card that is not
    reachable now; a card still leasing is a card that is reachable now and will not stay
    that way, because a lease can replace everything the profile wrote. Not reachable
    outranks not durable — and while the flag is on, the gateway and the servers are not
    ours to judge either, which is why they wait behind it.

    Its own line has to be read against the *Teraz* line above it, which shows an address
    without saying where it came from. "Karta ma X, nie Y" would be a lie here — X and Y
    are the same address — so the line says the address landed and names the one thing
    that did not. It is only reachable on a connected card; on a disconnected one the flag
    is not yet the card's answer, and `_report` has already called that a success.
    """
    wanted = _request_text(profile)
    if not _address_matches(state, profile):
        address = _address_text(state)
        if address is None:
            return Outcome(False, f"Karta nie ma adresu, oczekiwano {wanted}")
        return Outcome(False, f"Karta ma {address}, nie {wanted}")

    if not _dhcp_cleared(state):
        return Outcome(False, _STILL_DHCP)

    if not _gateway_matches(state, profile):
        have, want = _servers_text(state.gateways), _servers_text(_wanted_gateways(profile))
        return Outcome(False, f"Adres ustawiony, ale brama: {have} zamiast {want}")

    have, want = _servers_text(state.dns), _servers_text(_wanted_dns(profile))
    return Outcome(False, f"Adres ustawiony, ale DNS: {have} zamiast {want}")


def _wanted_gateways(profile: Profile) -> tuple[str, ...]:
    """The gateway the profile asks for, as a list so it reads like the adapter's."""
    return (profile.gateway,) if profile.gateway else ()


def _servers_text(servers: Sequence[str]) -> str:
    """A list of addresses as the status line names it, or a word for an empty one."""
    return ", ".join(servers) if servers else _NOTHING


def _address_text(state: AdapterState) -> str | None:
    """The address worth naming with its prefix, or None when the card has none."""
    primary = state.primary
    return None if primary is None else f"{primary[0]} /{primary[1]}"


def _request_text(profile: Profile) -> str:
    """The address the profile asks for, with its prefix when the mask yields one."""
    prefix = prefix_length(profile.mask)
    return profile.address if prefix is None else f"{profile.address} /{prefix}"
