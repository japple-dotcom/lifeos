"""The weekly life admin hour: the three admin jobs that matter most, with an hour in the calendar to do them.

Every Sunday evening Life OS picks the three most pressing bits of paperwork (forms, renewals, bills, things to
cancel, letters, payments), finds a free hour in the coming week for whoever's freer, and suggests it. "Book it"
puts "Life admin hour" in the calendar with the three jobs listed; "Another time" offers the next free hour.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import scheduling

ADMIN_CATEGORIES = {"Admin and renewals", "Errands"}
ADMIN_WORDS = ("form", "pay", "cancel", "renew", "insurance", "passport", "letter", "claim", "tax", "bill", "bank",
               "return", "post", "council", "licence", "register", "sign")


def candidates(svc, today: date | None = None) -> list[dict]:
    from . import todos
    today = today or date.today()
    out = []
    for t in todos.list_todos(svc, include_projects=False) if svc.google else []:
        low = t["title"].lower()
        if t.get("category") not in ADMIN_CATEGORIES and not any(w in low for w in ADMIN_WORDS):
            continue
        due = t.get("due") or ""
        days = (date.fromisoformat(due) - today).days if due else 30
        score = (4 - int(t.get("priority") or 3)) * 10 + max(0, 30 - days)
        out.append({"title": t["title"], "id": t["id"], "list": t["list"], "due": due, "score": score, "kind": "to-do"})
    for r in svc.store.renewals():
        d = date.fromisoformat(r["due_date"])
        if 0 <= (d - today).days <= 30 and not any(r["name"].lower() in c["title"].lower() for c in out):
            out.append({"title": f"Sort out {r['name']} (due {d.strftime('%-d %b')})", "id": f"renewal:{r['id']}", "list": "",
                        "due": r["due_date"], "score": 40 - (d - today).days // 2, "kind": "renewal"})
    return sorted(out, key=lambda c: -c["score"])


def plan(svc, today: date | None = None, skip: int = 0) -> dict:
    """The three jobs and a free hour (skip = how many slots to pass over, for 'Another time')."""
    today = today or date.today()
    jobs = candidates(svc, today)[:3]
    if not jobs:
        return {"jobs": [], "slot": None}
    names = svc.settings.household.get("names") or {}
    adults = [names.get(p, p.title()) for p in svc.settings.household.get("adults") or []]
    slots = scheduling.free_slots(svc, 60, count=6 + skip, notice_days=1, horizon_days=7, hours=scheduling.DIY_HOURS,
                                  people=adults or None, one_per_day=True) if svc.google else []
    slot = slots[skip] if len(slots) > skip else None
    return {"jobs": jobs, "slot": slot, "booked": svc.store.get_state(f"admin_hour:{_week(today)}", "")}


def _week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-{w}"


def book(svc, start: str, jobs: list[str], who: str = "") -> dict:
    a = datetime.fromisoformat(start)
    desc = "Three jobs for this hour:\n" + "\n".join(f"- {j}" for j in jobs[:3]) + "\n\nPlanned by Life OS"
    title = "Life admin hour" + (f" ({who})" if who else "")
    ev = svc.google.get().add_event(title, a.strftime("%Y-%m-%dT%H:%M:%S"), (a + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                                    description=desc, calendar_id=svc.settings.family_calendar_id or None)
    svc.store.set_state(f"admin_hour:{_week(a.date())}", a.isoformat())
    return {"ok": True, "title": title, "event_id": ev.get("id", ""), "start": a.isoformat()}


def sunday(svc, today: date | None = None) -> dict:
    today = today or date.today()
    p = plan(svc, today + timedelta(days=1))
    if p["jobs"] and p["slot"] and svc.notifier:
        when = p["slot"]["label"]
        svc.notifier.push(f"Life admin hour, {when}: " + "; ".join(j["title"] for j in p["jobs"]) + ".",
                          title="This week's admin", actions=[{"action": "view", "label": "Book it",
                                                               "url": f"{svc.settings.public_url}/app/#tasks?sec=week"}])
    return p
