"""Kids' morning and bedtime routines on the tablet: a picture checklist each child ticks (teeth, uniform, bag...), a
"leave in 10 minutes" countdown in the morning, and a star for finishing a routine. Stars are never money.

Grown-ups set the steps and times; the tablet (KIDS role) may only tick and untick today's steps.
"""
from __future__ import annotations

import re
from datetime import date, datetime

MORNING = [("🪥", "Brush teeth"), ("👕", "Get dressed"), ("🥣", "Breakfast"), ("👟", "Shoes and coat on"),
           ("🎒", "Bag packed")]
BEDTIME = [("🛁", "Bath or wash"), ("🩲", "Pyjamas on"), ("🪥", "Brush teeth"), ("📚", "Read a story"),
           ("🎒", "Bag ready for tomorrow")]
WHEN = ("morning", "bedtime")



def _jget(svc, key: str) -> dict:
    import json
    try:
        v = json.loads(svc.store.get_state(key, "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _jset(svc, key: str, value: dict) -> None:
    import json
    svc.store.set_state(key, json.dumps(value))

def _today(svc) -> date:
    from .household import today_local
    return today_local(svc.settings)


def _kids(svc) -> list[str]:
    return list(svc.settings.household.get("kids") or [])


def _default(kid: str) -> dict:
    return {"morning": [{"icon": i, "text": t} for i, t in MORNING], "bedtime": [{"icon": i, "text": t} for i, t in BEDTIME],
            "leave_at": "08:20", "bed_at": "19:30"}


def cfg(svc) -> dict:
    saved = _jget(svc, "routines_cfg")
    return {k: {**_default(k), **(saved.get(k) or {})} for k in _kids(svc)}


def _steps(steps) -> list[dict]:
    out = []
    for s in (steps or [])[:12]:
        if isinstance(s, str):
            s = {"text": s}
        text = " ".join(str(s.get("text") or "").split())[:40]
        if text:
            icon = str(s.get("icon") or "").strip()[:4]
            out.append({"icon": icon or "⭐", "text": text})
    return out


def save(svc, kid: str, morning=None, bedtime=None, leave_at: str = "", bed_at: str = "") -> dict:
    if kid not in _kids(svc):
        raise ValueError("Choose one of the children.")
    saved = _jget(svc, "routines_cfg")
    c = {**_default(kid), **(saved.get(kid) or {})}
    if morning is not None:
        c["morning"] = _steps(morning)
    if bedtime is not None:
        c["bedtime"] = _steps(bedtime)
    for key, val in (("leave_at", leave_at), ("bed_at", bed_at)):
        if val:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", val):
                raise ValueError("Times look like 08:20.")
            c[key] = val
    saved[kid] = c
    _jset(svc, "routines_cfg", saved)
    return c


def _ticks(svc, day: date) -> dict:
    return _jget(svc, f"routine_ticks:{day.isoformat()}")


def _now(svc) -> datetime:
    from . import voice
    return datetime.now(voice._tz(svc)).replace(tzinfo=None)


def which_now(svc, now: datetime | None = None) -> str:
    """The morning routine until 11am, then bedtime's."""
    now = now or _now(svc)
    return "morning" if now.hour < 11 else "bedtime"


def board(svc, now: datetime | None = None) -> dict:
    now = now or _now(svc)
    today = now.date()
    ticks = _ticks(svc, today)
    out = []
    for kid, c in cfg(svc).items():
        rows = {}
        for when in WHEN:
            done = set(ticks.get(kid, {}).get(when, []))
            steps = [{**s, "n": i, "done": i in done} for i, s in enumerate(c[when])]
            rows[when] = {"steps": steps, "done": sum(s["done"] for s in steps), "total": len(steps),
                          "finished": bool(steps) and all(s["done"] for s in steps)}
        leave = datetime.combine(today, datetime.strptime(c["leave_at"], "%H:%M").time())
        mins = int((leave - now).total_seconds() // 60)
        out.append({"kid": kid, **rows, "leave_at": c["leave_at"], "bed_at": c["bed_at"],
                    "leave_in": mins if 0 <= mins <= 120 else None})
    return {"kids": out, "now": which_now(svc, now), "date": today.isoformat()}


def tick(svc, kid: str, when: str, n: int, done: bool = True, now: datetime | None = None) -> dict:
    if kid not in _kids(svc):
        raise ValueError("Choose who you are.")
    if when not in WHEN:
        raise ValueError("Morning or bedtime?")
    c = cfg(svc)[kid]
    if not 0 <= int(n) < len(c[when]):
        raise ValueError("That step isn't on the list.")
    today = (now or _now(svc)).date()
    ticks = _ticks(svc, today)
    mine = set(ticks.setdefault(kid, {}).get(when, []))
    (mine.add if done else mine.discard)(int(n))
    ticks[kid][when] = sorted(mine)
    _jset(svc, f"routine_ticks:{today.isoformat()}", ticks)
    b = board(svc, now)
    row = next(k for k in b["kids"] if k["kid"] == kid)[when]
    return {"finished": row["finished"], "done": row["done"], "total": row["total"]}


def stars_for(svc, kid: str, day: date) -> int:
    """One star for each routine finished that day (for the star chart)."""
    ticks = _ticks(svc, day).get(kid, {})
    c = cfg(svc).get(kid)
    if not c:
        return 0
    return sum(1 for when in WHEN if c[when] and len(set(ticks.get(when, []))) >= len(c[when]))
