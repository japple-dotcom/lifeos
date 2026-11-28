"""Date formats that work everywhere: "%-d" (day without a leading zero) is a glibc/macOS extension that Windows
spells "%#d", and Python on Windows refuses the other spelling."""
from __future__ import annotations

import sys


def fmt(f: str) -> str:
    return f.replace("%-", "%#") if sys.platform == "win32" else f
