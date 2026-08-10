"""Remember which adapter the operator picked.

A machine has one Ethernet port that matters and the operator points at it once.
Asking again at every launch is the small friction that ends with the wrong adapter
reconfigured.

Nothing here may raise. A settings file that is missing, unreadable, corrupt or
half-written costs the operator their choice, never the app: forgetting the adapter
only means the next run asks.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

FILENAME = "settings.json"


def settings_path() -> Path:
    """Where the remembered choice lives.

    Beside the profiles, under LOCALAPPDATA rather than the program directory, so the
    installed copy, the portable copy and a run from source all read the same file.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "net-preset" / FILENAME


def load_adapter_choice(path: Path | None = None) -> str | None:
    """The GUID of the adapter chosen last time, or None when there is no usable one.

    A missing file, an unreadable one, malformed JSON, a value that is not text or is
    empty all read the same way: no choice, so the app falls back to asking. The GUID
    is returned as stored — whether an adapter still answers to it is for the caller
    listing the adapters to decide.
    """
    target = path or settings_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    adapter = raw.get("adapter")
    return adapter if isinstance(adapter, str) and adapter else None


def save_adapter_choice(guid: str | None, path: Path | None = None) -> None:
    """Remember *guid* as the chosen adapter; None forgets the choice.

    Failure is silent and costs only the convenience, so there is nothing here worth
    interrupting the operator over.
    """
    _write_json(path or settings_path(), {"adapter": guid})


def _write_json(target: Path, payload: dict[str, object]) -> bool:
    """Write *payload* to *target* as UTF-8 JSON. True when it reached the disk.

    Written beside the target and moved into place, so a crash or a full disk cannot
    leave a half-written file that the next run has to make sense of. The temporary
    keeps the whole target name and adds a suffix: shortening it to settings.tmp would
    put it in the same namespace as the files this directory already owns.
    """
    temporary = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        # The failure may well have been the move, which leaves the temporary sitting
        # in a directory the operator can see. Take it back out before giving up.
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        return False
    return True
