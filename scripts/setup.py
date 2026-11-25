"""Life OS setup in a terminal: answers a few questions and writes your settings.

    python scripts/setup.py        (from a source folder)
    lifeos setup                   (the app download, on a machine without a screen)

On a computer with a screen the app opens a setup page instead. Safe to run again: it keeps existing values unless
you type new ones, and never prints your API keys.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lifeos.setup_core import cli  # noqa: E402

if __name__ == "__main__":
    cli()
