"""Seasonal home maintenance: the jobs a UK house needs, each in the right month.

On the 1st of each month this month's jobs go on the to-do list (Home maintenance, due mid-month), unless you've
said a job doesn't apply to you (no chimney, no garden...). Home > Seasonal jobs shows this month and next.
Ticking one off is remembered for this month only; it comes back in its next season.
"""
from __future__ import annotations

import json
from datetime import date

# id, title, months, needs (a thing the house has; None = every house), minutes
JOBS = [
    ("alarms", "Test the smoke and carbon monoxide alarms", [1, 4, 7, 10], None, 10),
    ("rcd", "Press the test button on the fuse box (RCD)", [1, 7], None, 5),
    ("washer", "Clean the washing machine (hot empty wash, drawer and seal)", [1, 7], None, 30),
    ("loft", "Check the loft for leaks and damp", [2], None, 20),
    ("filters", "Clean the cooker hood and extractor fan filters", [2, 5, 8, 11], None, 20),
    ("descale", "Descale the showerheads and taps", [3, 9], None, 20),
    ("roof", "Look over the roof and chimney from the ground for slipped tiles", [3], None, 10),
    ("mower", "Service the lawnmower before the first cut", [3], "garden", 30),
    ("fences", "Check fences, gates and the shed after winter", [3], "garden", 30),
    ("gutters", "Clear the gutters and downpipes", [4, 11], None, 60),
    ("windows", "Clean the windows inside and out", [4, 9], None, 60),
    ("freezer", "Defrost and clean the freezer", [5], None, 60),
    ("decking", "Treat the decking and garden furniture", [5], "garden", 120),
    ("fridge", "Vacuum behind the fridge and clean its seals", [6], None, 20),
    ("dryer", "Deep clean the tumble dryer filter and vent", [6], "tumble dryer", 20),
    ("radiators", "Bleed the radiators before the heating goes on", [9], None, 30),
    ("boiler", "Book the boiler service", [9], "gas boiler", 10),
    ("chimney", "Book the chimney sweep", [9], "chimney", 10),
    ("hedges", "Cut the hedges (after nesting season)", [9], "garden", 90),
    ("pipes", "Check the loft insulation and pipe lagging", [10], None, 30),
    ("pressure", "Check the boiler pressure (1 to 1.5 bar)", [10], "gas boiler", 5),
    ("car", "Get the car ready for winter: tyres, screenwash, antifreeze", [10], "car", 30),
    ("taps", "Turn off and drain the outside tap", [11], "outside tap", 10),
]
THINGS = ["garden", "gas boiler", "chimney", "tumble dryer", "car", "outside tap"]


def _off(svc) -> set[str]:
    try:
        off = set(json.loads(svc.store.get_state("seasonal_off", "") or "[]"))
    except ValueError:
        off = set()
    return off | set((svc.settings.behaviour.get("seasonal") or {}).get("off") or [])


def _have(svc) -> set[str]:
    try:
        st = json.loads(svc.store.get_state("seasonal_have", "") or "null")
    except ValueError:
        st = None
    if st is not None:
        return set(st)
    return set((svc.settings.behaviour.get("seasonal") or {}).get("have") or ["garden", "gas boiler", "car", "outside tap"])


def jobs_for(svc, month: int) -> list[dict]:
    off, have = _off(svc), _have(svc)
    return [{"id": j, "title": t, "minutes": m, "needs": n or "", "off": j in off}
            for j, t, months, n, m in JOBS if month in months and (n is None or n in have)]


def overview(svc, today: date | None = None) -> dict:
    from . import household
    today = today or household.today_local(svc.settings)
    nxt = today.month % 12 + 1
    ym = f"{today.year}-{today.month:02d}"
    now = jobs_for(svc, today.month)
    for j in now:
        j["added"] = bool(svc.store.get_state(f"seasonal_added:{j['id']}:{ym}", ""))
    return {"month": today.strftime("%B"), "next_month": date(2000, nxt, 1).strftime("%B"), "now": now,
            "next": jobs_for(svc, nxt), "things": [{"name": t, "have": t in _have(svc)} for t in THINGS]}


def set_off(svc, job_id: str, off: bool) -> None:
    cur = _off(svc) - set((svc.settings.behaviour.get("seasonal") or {}).get("off") or [])
    cur = cur | {job_id} if off else cur - {job_id}
    svc.store.set_state("seasonal_off", json.dumps(sorted(cur)))


def set_have(svc, thing: str, have: bool) -> None:
    if thing not in THINGS:
        raise ValueError("Unknown thing.")
    cur = _have(svc)
    cur = cur | {thing} if have else cur - {thing}
    svc.store.set_state("seasonal_have", json.dumps(sorted(cur)))


def add(svc, job_id: str, person: str | None = None, today: date | None = None) -> dict:
    from . import household, todos
    today = today or household.today_local(svc.settings)
    job = next((j for j in jobs_for(svc, today.month) if j["id"] == job_id), None)
    if not job:
        raise ValueError("That job isn't due this month.")
    key = f"seasonal_added:{job_id}:{today.year}-{today.month:02d}"
    if svc.store.get_state(key, ""):
        return {"ok": True, "already": True}
    due = date(today.year, today.month, min(28, max(today.day, 15))).isoformat()
    judged = todos.normalise(svc, {"title": job["title"], "category": "Home maintenance", "priority": 3, "due": due,
                                   "effort_min": job["minutes"]}, job["title"])
    r = todos.add_todo(svc, None, job["title"], person, judged=judged)
    svc.store.set_state(key, "1")
    return {"ok": True, "id": r.get("id"), "title": job["title"]}


def monthly(svc, today: date | None = None) -> list[str]:
    """The 1st of the month: this month's jobs onto the to-do list."""
    if not svc.google:
        return []
    from . import household
    today = today or household.today_local(svc.settings)
    added = []
    for j in jobs_for(svc, today.month):
        if j["off"]:
            continue
        r = add(svc, j["id"], None, today)
        if not r.get("already"):
            added.append(j["title"])
    if added and svc.notifier:
        svc.notifier.push(f"This month's house jobs are on the list: {'; '.join(added)}.", title=f"{today.strftime('%B')} jobs",
                          actions=[{"action": "view", "label": "See them", "url": f"{svc.settings.public_url}/app/#home"}])
    return added
