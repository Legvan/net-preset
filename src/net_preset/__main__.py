"""Entry point. Elevation is settled before a window is ever created."""

from __future__ import annotations

import sys

from net_preset.app import Application
from net_preset.elevation import ensure_elevated


def main() -> int:
    if not ensure_elevated():
        return 0
    Application().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
