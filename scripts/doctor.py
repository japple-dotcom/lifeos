"""Life OS doctor: tests every real connection and says in plain English what to fix.

    python scripts/doctor.py            # everything, including one tiny AI request (costs a fraction of a penny)
    python scripts/doctor.py --no-ai    # skip the AI request

Run it after setup, and whenever something stops working. Exit code 0 = all good.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lifeos.doctor import FAIL, MARK, WARN, run  # noqa: E402  (the checks live in the app so Settings can run them)


def main() -> int:
    from lifeos.agent import Agent
    from lifeos.app import build_services
    from lifeos.config import load_settings

    svc = build_services(load_settings())
    try:
        agent = Agent(svc)
    except Exception as e:
        print(f"[ FAIL ] Assistant can't start: {e}")
        return 1
    results = run(svc, agent, skip_ai="--no-ai" in sys.argv)
    print("\nLife OS doctor\n")
    last = None
    for section, status, msg in results:
        if section != last:
            print(f"{section}")
            last = section
        print(f"  [{MARK[status]}] {msg}")
    fails = sum(1 for _, st, _ in results if st == FAIL)
    warns = sum(1 for _, st, _ in results if st == WARN)
    print(f"\n{'All good.' if not fails else f'{fails} problem(s) to fix.'}"
          f"{f' {warns} thing(s) worth a look.' if warns else ''}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
