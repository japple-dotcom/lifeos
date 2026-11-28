"""School holidays and childcare: spotting the days the children are off and both adults are busy, early.

School closures come from the school's emails (read automatically), from pasting the school's term dates into the
app, or from `school.closed` in settings.yaml. For each weekday the school is closed, Life OS looks at each adult's
calendar between `childcare.hours` (default 08:30-15:30): an adult counts as busy with 2+ hours booked then, or an
"away" all-day event. A day is a gap when every adult is busy and nothing on any calendar looks like childcare
("holiday club", "Grandma", "childminder"...). Only busy/free is used, never what the appointments are.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from . import scheduling
from .dates import fmt as _dfmt

COVER_WORDS = ("childcare", "holiday club", "club", "camp", "nanny", "childminder", "babysit", "grandma", "granny",
               "grandad", "grandpa", "nana", "nan's", "grandparents", "kids with", "children with", "playdate",
               "covering", "looking after")
HELPER_WORDS = ("childcare", "childminder", "babysit", "nanny", "family", "grandparent", "grandma", "grandad", "granny",
                "friend", "neighbour", "au pair")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def add_closed(svc, name: str, start: str, end: str = "", source: str = "") -> bool:
    a, b = _d(start), _d(end or start)
    if not a or not b or b < a or (b - a).days > 60:
        return False
    rid = f"{a.isoformat()}:{b.isoformat()}"
    if svc.store.get("school_day", rid):
        return False
    svc.store.put("school_day", rid, {"name": (name or "School closed").strip()[:60], "start": a.isoformat(),
                                      "end": b.isoformat(), "source": source}, at=a.isoformat())
    return True


def closed_periods(svc) -> list[dict]:
    out = list(svc.store.records("school_day"))
    for c in (svc.settings.behaviour.get("school") or {}).get("closed") or []:
        a, b = _d(c.get("start")), _d(c.get("end") or c.get("start"))
        if a and b:
            out.append({"id": f"{a}:{b}", "name": c.get("name", "School closed"), "start": a.isoformat(),
                        "end": b.isoformat(), "source": "settings"})
    return sorted(out, key=lambda c: c["start"])


def parse_term_dates(text: str, today: date | None = None) -> list[dict]:
    """Rule-based reading of pasted term dates: lines like 'Christmas holidays: 22 December 2026 - 2 January 2027'
    or 'INSET day 5 January'. Years missing are assumed to be the next time that date comes round."""
    today = today or date.today()
    found = []
    date_re = r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?(?:\s+(\d{4}))?"
    for line in text.splitlines():
        line = line.strip()
        ds = re.findall(date_re, line)
        days = []
        for d, mon, yr in ds:
            m = MONTHS.get(mon[:3].lower())
            if not m:
                continue
            y = int(yr) if yr else None
            try:
                when = date(y or today.year, m, int(d))
            except ValueError:
                continue
            if not y and when < today - timedelta(days=30):
                when = when.replace(year=when.year + 1)
            days.append(when)
        if not days:
            continue
        name = re.split(r"[:\-–]|\d", line, maxsplit=1)[0].strip(" :-–") or ("INSET day" if "inset" in line.lower() else "School closed")
        if len(days) >= 2 and re.search(r"\b(term (starts|begins|ends)|first day|last day)\b", line, re.I) \
                and not re.search(r"holiday|half[- ]term|break|inset|closed", line, re.I):
            continue  # term start/end lines aren't closures on their own
        a, b = days[0], days[-1] if len(days) >= 2 else days[0]
        if b < a:
            b = b.replace(year=b.year + 1)
        if re.search(r"holiday|half[- ]term|break|inset|training|closed|bank holiday", line, re.I):
            found.append({"name": name[:60] or "School closed", "start": a.isoformat(), "end": b.isoformat()})
    return found


def _hours(svc) -> tuple[time, time]:
    spec = (svc.settings.behaviour.get("childcare") or {}).get("hours", "08:30-15:30")
    a, b = scheduling._hours(spec) or (time(8, 30), time(15, 30))
    return a, b


def gaps(svc, days: int = 56, today: date | None = None) -> list[dict]:
    """Weekdays the school is closed and every adult is busy, with nothing that looks like childcare booked."""
    if not (svc.settings.household.get("kids")):
        return []
    today = today or date.today()
    horizon = today + timedelta(days=days)
    closed = {}
    for c in closed_periods(svc):
        d, end = max(_d(c["start"]), today), min(_d(c["end"]), horizon)
        while d <= end:
            if d.weekday() < 5:
                closed[d] = c["name"]
            d += timedelta(days=1)
    if not closed or not svc.google:
        return []
    tz = scheduling._tz(svc.settings)
    busy = scheduling.busy_times(svc, (horizon - today).days + 1)
    events = svc.google.events_all(days=(horizon - today).days + 1)
    names = svc.settings.household.get("names") or {}
    adults = [names.get(p, p.title()) for p in svc.settings.household.get("adults") or []]
    covered = {r["id"]: r for r in svc.store.records("covered")}
    h1, h2 = _hours(svc)
    out = []
    for d in sorted(closed):
        if d.isoformat() in covered:
            continue
        if any(e["start"][:10] <= d.isoformat() < (e.get("end") or e["start"])[:10] or e["start"][:10] == d.isoformat()
               for e in events if any(w in (e.get("summary") or "").lower() for w in COVER_WORDS)):
            continue
        a, b = datetime.combine(d, h1, tz), datetime.combine(d, h2, tz)
        who_busy = []
        for p in adults:
            booked = sum(max(0, (min(f, b) - max(s, a)).total_seconds()) for s, f in busy.get(p, []))
            if booked >= 2 * 3600:
                who_busy.append(p)
        if adults and len(who_busy) == len(adults):
            out.append({"date": d.isoformat(), "label": d.strftime(_dfmt("%a %-d %b")), "closed": closed[d], "busy": who_busy})
    return out


def grouped(gs: list[dict]) -> list[dict]:
    """Next to each other in the same holiday -> one card: 'Mon 22 - Wed 24 Dec (Christmas holidays)'."""
    groups: list[dict] = []
    for g in gs:
        d = date.fromisoformat(g["date"])
        if groups and groups[-1]["closed"] == g["closed"] and \
                (d - date.fromisoformat(groups[-1]["dates"][-1])).days <= 3:
            groups[-1]["dates"].append(g["date"])
        else:
            groups.append({"closed": g["closed"], "dates": [g["date"]], "busy": g["busy"]})
    for grp in groups:
        a, b = date.fromisoformat(grp["dates"][0]), date.fromisoformat(grp["dates"][-1])
        grp["label"] = a.strftime(_dfmt("%a %-d %b")) if a == b else f"{a.strftime(_dfmt('%a %-d'))} to {b.strftime(_dfmt('%a %-d %b'))}"
        grp["days"] = len(grp["dates"])
        grp["ask_text"] = _ask_text(grp, a, b)
    return groups


def _ask_text(grp: dict, a, b) -> str:
    when = a.strftime(_dfmt("%A %-d %B")) if a == b else f"{a.strftime(_dfmt('%A %-d'))} to {b.strftime(_dfmt('%A %-d %B'))}"
    return f"Hi, could you help with the children on {when}? We're both working and the school's closed. Thank you!"


def mark_covered(svc, dates: list[str], who: str, add_to_calendar: bool = True) -> int:
    n = 0
    kids = ", ".join(svc.settings.household.get("kids") or []) or "Kids"
    for ds in dates:
        d = _d(ds)
        if not d:
            continue
        svc.store.put("covered", d.isoformat(), {"who": who.strip()[:60] or "Sorted"}, at=d.isoformat())
        n += 1
    if add_to_calendar and svc.google and n:
        days = sorted(_d(x) for x in dates if _d(x))
        start, end = days[0], days[-1] + timedelta(days=1)
        try:
            svc.google.get().add_event(f"Childcare: {who.strip() or 'sorted'} ({kids})", start.isoformat(), end.isoformat(),
                                       description="Added by Life OS", calendar_id=svc.settings.family_calendar_id or None)
        except Exception:
            pass
    return n


def remind(svc, today: date | None = None) -> list[str]:
    """Weekly: a heads-up about gaps in the next eight weeks (each gap mentioned once a fortnight at most)."""
    today = today or date.today()
    gs = grouped(gaps(svc, today=today))
    fresh = [g for g in gs if svc.store.get_state(f"childcare_told:{g['dates'][0]}", "") <
             (today - timedelta(days=14)).isoformat()]
    if fresh and svc.notifier:
        lines = "; ".join(f"{g['label']} ({g['closed']})" for g in fresh[:3])
        svc.notifier.push(f"Childcare needed: {lines}. You're both busy and school's closed.", title="School holidays",
                          actions=[{"action": "view", "label": "Sort it", "url": f"{svc.settings.public_url}/app/#tasks?sec=week"}])
        for g in fresh:
            svc.store.set_state(f"childcare_told:{g['dates'][0]}", today.isoformat())
    return [g["label"] for g in fresh]
