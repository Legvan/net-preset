"""Keep the profile list between runs.

A profile is a handful of addresses typed once and used for months. Losing the list
to a half-written file, or to one entry the reader chokes on, would mean typing them
all again — on a machine whose network is presumably already misbehaving.

Nothing here may raise. A file that is missing, unreadable, corrupt or half-written
costs the operator profiles, never the app: what survives is loaded, what does not is
reported in a note for the status line.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

from net_preset.profile import Profile, validate

FILENAME = "profiles.json"
VERSION = 1

_UNREADABLE = "Nie udało się odczytać zapisanych ustawień"


def data_directory() -> Path:
    """Where the application keeps its files.

    Under LOCALAPPDATA rather than beside the executable, so the installed copy, the
    portable copy and a run from source all read the same files — and so a per-machine
    install never needs a writable program directory.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "net-preset"


def profiles_path() -> Path:
    """Where the profile list is stored."""
    return data_directory() / FILENAME


def load_profiles(path: Path | None = None) -> tuple[list[Profile], str | None]:
    """Read the stored profiles, with a note when anything was lost on the way.

    No file is the ordinary first run: no profiles and nothing to report. Anything
    else — an unreadable file, malformed JSON, an entry that no longer validates —
    yields whatever could be salvaged and a Polish note explaining the rest.
    """
    target = path or profiles_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None
    except OSError, ValueError:
        return [], _UNREADABLE
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
        return [], _UNREADABLE

    profiles: list[Profile] = []
    dropped = 0
    for entry in raw["profiles"]:
        profile = _profile_from(entry)
        # Judged against the names accepted so far, so a file that somehow holds the
        # same name twice keeps the first entry and drops the second.
        if profile is None or validate(profile, [kept.name for kept in profiles]):
            dropped += 1
            continue
        profiles.append(profile)

    return profiles, _dropped_note(dropped) if dropped else None


def save_profiles(profiles: Sequence[Profile], path: Path | None = None) -> bool:
    """Store the profile list, in the order given. True when it reached the disk.

    Whatever is handed over is written as it stands: deciding what is valid belongs to
    the form the operator typed it into, not to the file it ends up in.
    """
    payload = {
        "version": VERSION,
        "profiles": [
            {
                "name": profile.name,
                "address": profile.address,
                "mask": profile.mask,
                "gateway": profile.gateway,
                "dns": profile.dns,
                "dns_alt": profile.dns_alt,
            }
            for profile in profiles
        ],
    }
    return _write_json(path or profiles_path(), payload)


def _profile_from(entry: object) -> Profile | None:
    """Turn one stored entry into a Profile, or None when it is not one.

    Only the three required fields have to be present and textual. The optional ones
    fall back to empty, which is how an unset field is spelled everywhere else.
    """
    if not isinstance(entry, dict):
        return None
    name, address, mask = entry.get("name"), entry.get("address"), entry.get("mask")
    if not all(isinstance(value, str) for value in (name, address, mask)):
        return None
    return Profile(
        name=name,
        address=address,
        mask=mask,
        gateway=_text(entry.get("gateway")),
        dns=_text(entry.get("dns")),
        dns_alt=_text(entry.get("dns_alt")),
    )


def _text(value: object) -> str:
    """A stored optional field as text: anything that is not a string counts as unset."""
    return value if isinstance(value, str) else ""


def _dropped_note(count: int) -> str:
    """The note about skipped entries, in the number Polish expects.

    One takes the singular, two through four their own plural, everything else the
    genitive — and the teens take the last of those, so twelve is not two.
    """
    if count == 1:
        return "Pominięto 1 nieprawidłowe ustawienie"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"Pominięto {count} nieprawidłowe ustawienia"
    return f"Pominięto {count} nieprawidłowych ustawień"


def _write_json(target: Path, payload: dict[str, object]) -> bool:
    """Write *payload* to *target* as UTF-8 JSON. True when it reached the disk.

    Written beside the target and moved into place, so a crash or a full disk cannot
    leave a half-written file that the next run has to make sense of. The temporary
    keeps the whole target name and adds a suffix: shortening it to profiles.tmp would
    put it in the same namespace as the files this directory already owns.
    """
    temporary = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False so a Polish name stays readable to anyone who opens the
        # file in an editor rather than arriving as a row of escapes.
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        # The failure may well have been the move, which leaves the temporary sitting
        # in a directory the operator can see. Take it back out before giving up.
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        return False
    return True
