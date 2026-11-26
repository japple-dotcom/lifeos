"""A weekly A4 for the fridge: the week's calendar, dinners, bins, school kit, the cleaning rota and birthdays.

/app/week.html (Home > Print this week's sheet). Landscape A4, big type, one column per day. Sunday at 6:40pm the
household gets a notification with the link, so it can be printed for the week ahead. Shows calendar entry titles
the same way the kitchen display does.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

log = logging.getLogger("lifeos.weeksheet")


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def week_of(svc, today: date | None = None) -> date:
    """Monday of this week, or of next week from Saturday on (so Sunday's sheet is for the coming week)."""
    today = today or _today(svc)
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=7) if today.weekday() >= 5 else monday


def build(svc, start: date | None = None) -> dict:
    from . import bins, cleaning, household, profiles, schoolkit
    start = start or week_of(svc)
    days = [start + timedelta(days=i) for i in range(7)]
    today = _today(svc)
    events: dict[str, list[dict]] = {d.isoformat(): [] for d in days}
    if svc.google:
        try:
            ahead = (days[-1] - today).days + 1
            for e in svc.google.events_all(days=max(1, ahead)):
                d = (e.get("start") or "")[:10]
                if d in events:
                    t = (e.get("start") or "")[11:16] if "T" in (e.get("start") or "") else ""
                    events[d].append({"time": t, "title": (e.get("summary") or "Busy")[:60], "who": e.get("who", "")})
        except Exception as e:
            log.warning("week sheet calendar: %s", e)
    meals = {m["day"]: m["dinner"] for m in household.week_meals(svc, start=start, days=7)}
    bin_days = {}
    try:
        for c in bins.collections(svc, days=(days[-1] - today).days + 2, today=min(today, start)):
            bin_days[c["date"]] = c["bins"]
    except Exception as e:
        log.warning("week sheet bins: %s", e)
    birthdays = {}
    try:
        for p in profiles.profiles(svc):
            nb = profiles.next_birthday(p.get("birthday", ""), start)
            if nb and nb in events:
                birthdays.setdefault(nb, []).append(p["name"])
    except Exception:
        pass
    out_days = []
    for d in days:
        k = d.isoformat()
        kit = schoolkit.for_day(svc, d)
        out_days.append({"date": k, "day": d.strftime("%A"), "short": d.strftime("%d %b").lstrip("0"),
                         "events": sorted(events[k], key=lambda e: e["time"] or "00:00"),
                         "dinner": meals.get(k, ""), "bins": bin_days.get(k, []), "kit": schoolkit.say(kit) if kit else "",
                         "bins_out": bin_days.get((d + timedelta(days=1)).isoformat(), []),
                         "birthdays": birthdays.get(k, [])})
    rota = []
    try:
        for j in cleaning.week(svc, start)["jobs"]:
            rota.append({"who": j["who_name"], "job": j["name"]})
    except Exception as e:
        log.warning("week sheet cleaning: %s", e)
    chores = []
    try:
        board = household.chores_board(svc)
        for kid in board.get("kids") or []:
            chores.append({"kid": kid.get("kid") or kid.get("name", ""), "chores": [c.get("name") for c in kid.get("chores", [])][:6]})
    except Exception:
        pass
    return {"week_of": start.isoformat(), "title": f"Week of {start.strftime('%d %B').lstrip('0')}", "days": out_days,
            "rota": rota, "chores": chores}


def sunday(svc) -> str:
    if not svc.notifier:
        return ""
    text = "Next week's fridge sheet is ready to print: Home, Print this week's sheet."
    svc.notifier.push(text, title="This week", priority=2,
                      actions=[{"action": "view", "label": "Open it", "url": svc.settings.public_url.rstrip("/") + "/app/week.html"}]
                      if svc.settings.public_url else None)
    return text
