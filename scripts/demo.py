"""Try Life OS with made-up sample data: no Google, Home Assistant or AI keys needed.

    python scripts/demo.py            # then open http://localhost:8800/app/ (household key: demo)

The sample household lives in lifeos/demo.py (so the app download can show it too: `lifeos --demo`).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lifeos.demo import *  # noqa: E402,F401,F403
from lifeos.demo import DemoAI, DemoGoogle, DemoHA, build, main  # noqa: E402,F401

if __name__ == "__main__":
    main()
