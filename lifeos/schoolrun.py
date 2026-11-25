"""The school run and clubs: who's doing drop-off and pick-up each day this week, from both calendars.

For each school day (not holidays or INSET days), Life OS looks at who's free around drop-off and pick-up time
(settings `school_run`, default 08:30 and 15:15, 30 minutes either side; busy/free only). A calendar entry like
"School run: Sue" or "Pick up Ava" settles it; otherwise it suggests whoever's free, sharing them out, and flags
a run nobody's free for. Clubs in the calendar ("Leo football", "Ava swimming 4pm") add a pick-up at the end.
"Claim it" puts "School run: <name>" in the family calendar so it's agreed.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from . import childcare, scheduling

CLUB_WORDS = ("club", "football", "swimming", "dance", "ballet", "rugby", "cricket", "tennis", "gymnastics", "brownies",
              "beavers", "cubs", "scouts", "rainbows", "guides", "karate", "judo", "music lesson", "piano", "drama",
              "netball", "hockey", "athletics")
RUN_WORDS = re.compile(r"school run|drop[- ]?off|pick[- ]?up|collect", re.I)


def _cfg(svc) -> dict:
    return {"drop_off": "08:30", "pick_up": "15:15", "minutes": 30, **(svc.settings.behaviour.get("school_run") or {})}


def week(svc, start: date | None = None, days: int = 7) -> list[dict]:
    """Each school day: [{date, runs: [{kind, at, who, suggested, free, claimed}]}]."""
    kids = svc.settings.household.get("kids") or []
    if not kids or not svc.google:
        return []
    start = start or date.today()
    c = _cfg(svc)
    tz = scheduling._tz(svc.settings)
    closed = set()
    for p in childcare.closed_periods(svc):
        d = date.fromisoformat(p["start"])
        while d <= date.fromisoformat(p["end"]):
            closed.add(d)
            d += timedelta(days=1)
    span = max(0, (start - date.today()).days) + days + 1
    busy = scheduling.busy_times(svc, span)
    events = svc.google.events_all(days=span)
    names = svc.settings.household.get("names") or {}
    adults = [names.get(p, p.title()) for p in svc.settings.household.get("adults") or []]
    load = {a: 0 for a in adults}
    margin = timedelta(minutes=int(c["minutes"]))
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        if d.weekday() >= 5 or d in closed:
            continue
        todays = [e for e in events if e.get("start", "")[:10] == d.isoformat()]
        runs = [("Drop-off", c["drop_off"], ""), ("Pick-up", c["pick_up"], "")]
        for e in todays:  # clubs: a pick-up when they finish
            summ = (e.get("summary") or "")
            if len(e.get("start", "")) > 10 and any(w in summ.lower() for w in CLUB_WORDS) and \
                    any(k.lower() in summ.lower() for k in kids) and (e.get("end") or "")[11:16] >= "15:30":
                runs.append((f"Pick-up from {summ}", e["end"][11:16], summ))
        day_runs = []
        for kind, at, club in runs:
            t = datetime.combine(d, time.fromisoformat(at), tz)
            free = [a for a in adults if not any(s < t + margin and t - margin < f for s, f in busy.get(a, []))]
            claim = next((e for e in todays if _is_claim(e, kind, t, tz)), None)
            claimed_by = next((a for a in adults if claim and a.lower() in (claim.get("summary") or "").lower()), "")
            if claim and not claimed_by:
                claimed_by = (claim.get("who") or [""])[0]
            # a claim entry itself makes that person "busy": they're still the one doing it
            who = claimed_by or (min(free, key=lambda a: load[a]) if free else "")
            if who:
                load[who] = load.get(who, 0) + 1
            day_runs.append({"kind": kind, "at": at, "who": who, "claimed": bool(claimed_by), "free": free,
                             "gap": not who, "club": club})
        out.append({"date": d.isoformat(), "label": d.strftime("%a %-d %b"), "runs": day_runs})
    return out


def _is_claim(e: dict, kind: str, t: datetime, tz) -> bool:
    """'School run: drop-off (Joe)' or 'Pick up Ava' within three hours of this run."""
    summ = (e.get("summary") or "").lower()
    if len(e.get("start", "")) <= 10 or not RUN_WORDS.search(summ):
        return False
    word = kind.split()[0].lower().replace("-", "")
    if word not in summ.replace("-", "").replace(" ", "") and "school run" not in summ:
        return False
    start = datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
    start = start.replace(tzinfo=tz) if start.tzinfo is None else start
    return abs((start - t).total_seconds()) <= 3 * 3600


def gaps(svc, start: date | None = None) -> list[str]:
    return [f"{d['label']} {r['kind'].lower()} at {r['at']}" for d in week(svc, start) for r in d["runs"] if r["gap"]]


def claim(svc, day: str, kind: str, at: str, who: str) -> dict:
    d = date.fromisoformat(day)
    a = datetime.combine(d, time.fromisoformat(at))
    title = f"School run: {kind.lower()} ({who})"
    ev = svc.google.get().add_event(title, a.strftime("%Y-%m-%dT%H:%M:%S"), (a + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S"),
                                    description="Agreed in Life OS", calendar_id=svc.settings.family_calendar_id or None)
    return {"ok": True, "title": title, "event_id": ev.get("id", "")}


def sunday_note(svc, start: date | None = None) -> str:
    g = gaps(svc, start)
    if g and svc.notifier:
        svc.notifier.push("School run this week, nobody free for: " + "; ".join(g[:4]) + ".", title="School run",
                          actions=[{"action": "view", "label": "Sort it", "url": f"{svc.settings.public_url}/app/#tasks?sec=week"}])
    return "; ".join(g)
