"""School kit: PE kit, packed lunches and other kit days, said the evening before; and lost property.

Home > School kit, per child: PE days, packed-lunch days, and anything else on a fixed day (swimming, recorder,
library book). At 6:30pm on a school night Life OS sends one notification ("Tomorrow: Ava PE kit, Leo packed
lunch") and Today shows it; on PE days the evening note adds "Wash Ava's PE kit". School closures (holidays and
INSET days from emails, pasted term dates or settings) are skipped. "Lost something?" adds a to-do to check lost
property on the next school day.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri"]


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def cfg(svc) -> dict:
    try:
        c = json.loads(svc.store.get_state("schoolkit_cfg", "") or "{}")
    except ValueError:
        c = {}
    kids = svc.settings.household.get("kids") or []
    out = {}
    for k in kids:
        x = c.get(k) or {}
        out[k] = {"pe": [d for d in x.get("pe") or [] if d in DAYS], "lunch": [d for d in x.get("lunch") or [] if d in DAYS],
                  "other": [{"what": " ".join(str(o.get("what") or "").split())[:40], "days": [d for d in o.get("days") or [] if d in DAYS]}
                            for o in x.get("other") or [] if str(o.get("what") or "").strip()][:6]}
    return out


def save(svc, data: dict) -> dict:
    kids = svc.settings.household.get("kids") or []
    clean = {}
    for k, v in (data or {}).items():
        if k not in kids:
            raise ValueError(f"{k} isn't one of the children.")
        clean[k] = {"pe": [d for d in v.get("pe") or [] if d in DAYS], "lunch": [d for d in v.get("lunch") or [] if d in DAYS],
                    "other": [{"what": " ".join(str(o.get("what") or "").split())[:40],
                               "days": [d for d in o.get("days") or [] if d in DAYS]}
                              for o in v.get("other") or [] if str(o.get("what") or "").strip()][:6]}
    svc.store.set_state("schoolkit_cfg", json.dumps(clean))
    return cfg(svc)


def school_day(svc, d: date) -> bool:
    if d.weekday() > 4:
        return False
    try:
        from . import childcare
        for p in childcare.closed_periods(svc):
            if p["start"] <= d.isoformat() <= (p.get("end") or p["start"]):
                return False
    except Exception:
        pass
    return True


def for_day(svc, d: date) -> list[dict]:
    """[{kid, what}] for that school day."""
    if not school_day(svc, d):
        return []
    day = DAYS[d.weekday()]
    out = []
    for kid, c in cfg(svc).items():
        if day in c["pe"]:
            out.append({"kid": kid, "what": "PE kit"})
        if day in c["lunch"]:
            out.append({"kid": kid, "what": "packed lunch"})
        for o in c["other"]:
            if day in o["days"]:
                out.append({"kid": kid, "what": o["what"]})
    return out


def wash_today(svc, d: date) -> list[str]:
    if d.weekday() > 4 or not school_day(svc, d):
        return []
    return [kid for kid, c in cfg(svc).items() if DAYS[d.weekday()] in c["pe"]]


def say(items: list[dict]) -> str:
    by: dict[str, list[str]] = {}
    for i in items:
        by.setdefault(i["kid"], []).append(i["what"])
    return "; ".join(f"{k} {', '.join(v)}" for k, v in by.items())


def tomorrow(svc, today: date | None = None) -> dict:
    today = today or _today(svc)
    nxt = today + timedelta(days=1)
    items = for_day(svc, nxt)
    wash = wash_today(svc, today)
    return {"date": nxt.isoformat(), "items": items, "text": say(items), "wash": wash}


def evening(svc, now: datetime | None = None) -> str:
    """6:30pm: tomorrow's kit, and washing tonight's PE kit (unless the one "Tomorrow" message is on)."""
    from . import tomorrow as tomorrow_msg
    if tomorrow_msg.digest_on(svc):
        return ""
    today = (now.date() if now else _today(svc))
    t = tomorrow(svc, today)
    if not t["items"] and not t["wash"]:
        return ""
    parts = []
    if t["items"]:
        parts.append(f"Tomorrow: {t['text']}.")
    if t["wash"]:
        parts.append("Wash " + " and ".join(f"{k}'s" for k in t["wash"]) + " PE kit tonight.")
    text = " ".join(parts)
    if svc.notifier:
        svc.notifier.push(text, title="School tomorrow", priority=2)
    return text


def lost(svc, kid: str, item: str, by: str = "") -> dict:
    from . import todos
    if kid not in (svc.settings.household.get("kids") or []):
        raise ValueError("Choose one of the children.")
    item = " ".join((item or "").split())[:60]
    if not item:
        raise ValueError("What's missing?")
    d = _today(svc) + timedelta(days=1)
    while not school_day(svc, d) and (d - _today(svc)).days < 21:
        d += timedelta(days=1)
    t = todos.guess(svc, f"Check lost property for {kid}'s {item}")
    t.update(title=f"Check lost property for {kid}'s {item}", due=d.isoformat(), timescale="this_week")
    return todos.file_todo(svc, t, by or "school kit")
