"""A fair cleaning rota for the grown-ups, with the robot vacuum run around it.

Each week (Sunday evening, or when you open Home > Cleaning rota) the jobs that are due are shared out so the
effort evens up over the last four weeks: each job has points (a bathroom is 3, the towels are 1), the bigger jobs
are shared first, and a job swaps hands from last time where that keeps things even. Tick a job when it's done;
the fairness line shows each person's share of the points over four weeks. "Put it in our to-do lists" files each
person's jobs into their own Google Tasks (and so Gemini).

The robot vacuum (a Home Assistant vacuum entity) runs on the days you choose, while the house is empty (Home
Assistant's people all away), and at the latest time you set if nobody's gone out. Never during quiet hours.
When it has run twice in the week, the downstairs hoover job drops to 1 point ("the bits the robot can't reach").
Starting the vacuum is the only thing this switches on.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta

log = logging.getLogger("lifeos.cleaning")

# id, name, every (days), points
JOBS = [
    ("bathroom", "Clean the bathroom", 7, 3),
    ("hoover_down", "Hoover downstairs", 7, 3),
    ("hoover_up", "Hoover upstairs", 7, 2),
    ("kitchen", "Wipe down the kitchen properly (tops, hob, fronts)", 7, 2),
    ("floors", "Mop the hard floors", 7, 2),
    ("towels", "Wash and dry the towels", 7, 1),
    ("fridge", "Clear out the fridge", 7, 1),
    ("beds", "Change the beds", 14, 3),
    ("dust", "Dust the living room and bedrooms", 14, 2),
    ("toilets", "Quick clean of the toilets", 4, 1),
    ("oven", "Clean the oven", 60, 4),
    ("kettle", "Descale the kettle", 30, 1),
]
WINDOW_DAYS = 28


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _cfg(svc) -> dict:
    base = dict(svc.settings.behaviour.get("cleaning") or {})
    try:
        saved = json.loads(svc.store.get_state("cleaning_cfg", "") or "{}")
    except ValueError:
        saved = {}
    base.update(saved)
    return base


def jobs(svc) -> list[dict]:
    c = _cfg(svc)
    off = set(c.get("off") or [])
    out = [{"id": j, "name": n, "every": e, "points": p} for j, n, e, p in JOBS if j not in off]
    for x in c.get("extra") or []:
        out.append({"id": x["id"], "name": x["name"], "every": int(x.get("every", 7)), "points": int(x.get("points", 2))})
    return out


def adults(svc) -> list[str]:
    return list(svc.settings.household.get("adults") or [])


def _done(svc, days: int = WINDOW_DAYS, today: date | None = None) -> list[dict]:
    today = today or _today(svc)
    return svc.store.records("clean_done", since=(today - timedelta(days=days)).isoformat())


def last_done(svc, today: date | None = None) -> dict[str, dict]:
    last: dict[str, dict] = {}
    for d in svc.store.records("clean_done", since=((today or _today(svc)) - timedelta(days=120)).isoformat()):
        if d["job"] not in last or d["date"] >= last[d["job"]]["date"]:
            last[d["job"]] = d
    return last


def vacuum_runs(svc, week_of: date) -> int:
    return len([r for r in svc.store.records("vacuum_run", since=week_of.isoformat(),
                                               until=(week_of + timedelta(days=6)).isoformat())])


def _points(svc, job: dict, week_of: date) -> int:
    if job["id"] == "hoover_down" and vacuum_runs(svc, week_of) >= 2:
        return 1
    return job["points"]


def fairness(svc, today: date | None = None) -> dict:
    totals = {a: 0 for a in adults(svc)}
    for d in _done(svc, today=today):
        if d.get("by") in totals:
            totals[d["by"]] += int(d.get("points", 0))
    all_pts = sum(totals.values())
    names = svc.settings.household.get("names") or {}
    return {"points": totals, "shares": [{"person": a, "name": names.get(a, a.title()), "points": p,
                                          "pct": round(100 * p / all_pts) if all_pts else 0} for a, p in totals.items()]}


def _assign(svc, due: list[dict], week_of: date, today: date) -> dict[str, str]:
    people = adults(svc)
    if not people:
        return {}
    load = dict(fairness(svc, today)["points"])
    last = last_done(svc, today)
    out = {}
    for j in sorted(due, key=lambda j: -_points(svc, j, week_of)):
        before = (last.get(j["id"]) or {}).get("by")
        pick = min(people, key=lambda a: (load.get(a, 0), a == before, people.index(a)))
        out[j["id"]] = pick
        load[pick] = load.get(pick, 0) + _points(svc, j, week_of)
    return out


def week(svc, today: date | None = None, refresh: bool = False) -> dict:
    """This week's rota: [{id, name, points, who, done, done_by}], made once per week and kept."""
    today = today or _today(svc)
    monday = _monday(today)
    key = f"cleaning_week:{monday.isoformat()}"
    try:
        plan = json.loads(svc.store.get_state(key, "") or "null")
    except ValueError:
        plan = None
    all_jobs = {j["id"]: j for j in jobs(svc)}
    if plan is None or refresh:
        last = last_done(svc, today)
        due = [j for j in all_jobs.values()
               if j["id"] not in last or date.fromisoformat(last[j["id"]]["date"]) + timedelta(days=j["every"]) <= monday + timedelta(days=6)]
        plan = {"assigned": _assign(svc, due, monday, today), "made": today.isoformat()}
        svc.store.set_state(key, json.dumps(plan))
    names = svc.settings.household.get("names") or {}
    done_this_week = {d["job"]: d for d in svc.store.records("clean_done", since=monday.isoformat())}
    rows = []
    for jid, who in plan["assigned"].items():
        j = all_jobs.get(jid)
        if not j:
            continue
        name = "Hoover the bits the robot can't reach" if jid == "hoover_down" and _points(svc, j, monday) == 1 else j["name"]
        d = done_this_week.get(jid)
        rows.append({"id": jid, "name": name, "points": _points(svc, j, monday), "who": who,
                     "who_name": names.get(who, who.title()), "done": bool(d), "done_by": (d or {}).get("by", ""),
                     "done_by_name": names.get((d or {}).get("by", ""), "")})
    rows.sort(key=lambda r: (r["done"], r["who"], -r["points"]))
    return {"week_of": monday.isoformat(), "jobs": rows, "fairness": fairness(svc, today)["shares"],
            "vacuum": vacuum_status(svc, today), "filed": plan.get("filed", False)}


def tick(svc, job_id: str, by: str, today: date | None = None) -> dict:
    today = today or _today(svc)
    all_jobs = {j["id"]: j for j in jobs(svc)}
    j = all_jobs.get(job_id)
    if not j:
        raise ValueError("That job isn't on the rota.")
    if by not in adults(svc):
        raise ValueError("Only the grown-ups are on the cleaning rota.")
    monday = _monday(today)
    if any(d["job"] == job_id for d in svc.store.records("clean_done", since=monday.isoformat())):
        raise ValueError("That's already ticked this week.")
    rec = {"job": job_id, "by": by, "date": today.isoformat(), "points": _points(svc, j, monday)}
    rid = secrets.token_hex(6)
    svc.store.put("clean_done", rid, rec, at=today.isoformat())
    return {**rec, "id": rid}


def untick(svc, job_id: str, today: date | None = None) -> bool:
    monday = _monday(today or _today(svc))
    gone = False
    for d in svc.store.records("clean_done", since=monday.isoformat()):
        if d["job"] == job_id:
            gone = svc.store.remove("clean_done", d["id"]) or gone
    return gone


def swap(svc, job_id: str, today: date | None = None) -> dict:
    """Give this week's job to the other person (or the next one along)."""
    today = today or _today(svc)
    key = f"cleaning_week:{_monday(today).isoformat()}"
    week(svc, today)  # makes this week's rota if there isn't one yet
    plan = json.loads(svc.store.get_state(key, "") or "{}")
    people = adults(svc)
    who = plan.get("assigned", {}).get(job_id)
    if not who or len(people) < 2:
        raise ValueError("There's no one to swap with.")
    plan["assigned"][job_id] = people[(people.index(who) + 1) % len(people)] if who in people else people[0]
    svc.store.set_state(key, json.dumps(plan))
    return week(svc, today)


def file_todos(svc, today: date | None = None) -> dict:
    """Each person's jobs for this week into their own Google Tasks, due Saturday."""
    from . import todos
    today = today or _today(svc)
    w = week(svc, today)
    saturday = _monday(today) + timedelta(days=5)
    filed = 0
    for r in w["jobs"]:
        if r["done"]:
            continue
        t = todos.guess(svc, r["name"])
        t.update(title=r["name"], who=r["who"], due=max(saturday, today).isoformat(), timescale="this_week", effort_min=30 * r["points"])
        try:
            if not todos.file_todo(svc, t, "cleaning rota").get("duplicate"):
                filed += 1
        except Exception as e:
            log.warning("cleaning to-do %s: %s", r["name"], e)
    key = f"cleaning_week:{_monday(today).isoformat()}"
    plan = json.loads(svc.store.get_state(key, "") or "{}")
    plan["filed"] = True
    svc.store.set_state(key, json.dumps(plan))
    return {"filed": filed}


def settings_view(svc) -> dict:
    c = _cfg(svc)
    vac = c.get("vacuum") or {}
    vacuums = []
    if svc.ha:
        try:
            vacuums = [{"id": v["entity_id"], "name": (v.get("attributes") or {}).get("friendly_name") or v["entity_id"]}
                       for v in svc.ha.states("vacuum")]
        except Exception as e:
            log.warning("vacuums: %s", e)
    return {"jobs": [{"id": j, "name": n, "every": e, "points": p, "on": j not in set(c.get("off") or [])} for j, n, e, p in JOBS],
            "vacuum": {"entity": vac.get("entity", ""), "days": vac.get("days") or [], "latest": vac.get("latest", "16:00"),
                       "even_if_home": bool(vac.get("even_if_home", False))},
            "vacuums": vacuums}


def save_settings(svc, off: list[str] | None = None, vacuum: dict | None = None) -> dict:
    try:
        saved = json.loads(svc.store.get_state("cleaning_cfg", "") or "{}")
    except ValueError:
        saved = {}
    if off is not None:
        known = {j for j, *_ in JOBS}
        saved["off"] = sorted(set(off) & known)
    if vacuum is not None:
        ent = str(vacuum.get("entity") or "")
        if ent and not re.fullmatch(r"vacuum\.[a-z0-9_]+", ent):
            raise ValueError("Choose one of the vacuums listed.")
        days = [d for d in (vacuum.get("days") or []) if d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
        latest = str(vacuum.get("latest") or "16:00")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", latest):
            raise ValueError("Give the latest time as HH:MM, for example 16:00.")
        saved["vacuum"] = {"entity": ent, "days": days, "latest": latest, "even_if_home": bool(vacuum.get("even_if_home"))}
    svc.store.set_state("cleaning_cfg", json.dumps(saved))
    return settings_view(svc)


def vacuum_status(svc, today: date | None = None) -> dict:
    vac = (_cfg(svc).get("vacuum") or {})
    today = today or _today(svc)
    if not vac.get("entity"):
        return {"set": False}
    ran = [r for r in svc.store.records("vacuum_run", since=today.isoformat(), until=today.isoformat())]
    day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][today.weekday()]
    return {"set": True, "entity": vac["entity"], "today": day in (vac.get("days") or []), "ran_today": bool(ran),
            "this_week": vacuum_runs(svc, _monday(today)), "days": vac.get("days") or []}


def vacuum_tick(svc, now: datetime | None = None) -> str:
    """Every 15 minutes in the day: start the vacuum on its days once the house is empty (or at the latest time)."""
    from . import household, presence, voice
    vac = (_cfg(svc).get("vacuum") or {})
    if not svc.ha or not vac.get("entity"):
        return ""
    now = now or datetime.now(voice._tz(svc))
    today = now.date()
    day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][today.weekday()]
    if day not in (vac.get("days") or []) or household.in_quiet_hours(svc.settings, now) or now.hour < 8:
        return ""
    if svc.store.records("vacuum_run", since=today.isoformat(), until=today.isoformat()):
        return ""
    home = presence.anyone_home(svc)
    late = now.strftime("%H:%M") >= (vac.get("latest") or "16:00")
    if home is False or (late and (vac.get("even_if_home") or home is None)):
        svc.ha.call_service("vacuum.start", {"entity_id": vac["entity"]})
        why = "the house is empty" if home is False else "it's the latest time set"
        svc.store.put("vacuum_run", secrets.token_hex(4), {"date": today.isoformat(), "why": why}, at=today.isoformat())
        svc.store.log_event("vacuum", f"started {vac['entity']}: {why}")
        return why
    return ""


def sunday(svc) -> str:
    """Sunday evening: make next week's rota and say who's got what."""
    if len(adults(svc)) < 1:
        return ""
    nxt = _today(svc) + timedelta(days=1)
    w = week(svc, nxt)
    if not w["jobs"] or not svc.notifier:
        return ""
    by: dict[str, list[str]] = {}
    for r in w["jobs"]:
        by.setdefault(r["who_name"], []).append(r["name"].lower())
    text = "This week's cleaning: " + "; ".join(f"{n}: {', '.join(j)}" for n, j in by.items()) + "."
    svc.notifier.push(text, title="Cleaning rota", priority=2)
    return text
