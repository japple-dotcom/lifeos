"""Finds free times in the household's calendars: for tradespeople to visit, or to block time for a job.

Busy = any timed event in that person's calendar (plus the family calendar). All-day events only block a day when
they look like being away (holiday, away, trip...). Slots are offered within the household's usual hours
(settings.yaml `availability`), at least `notice_days` ahead for trades, spread over different days.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_HOURS = {"mon": "08:00-18:00", "tue": "08:00-18:00", "wed": "08:00-18:00", "thu": "08:00-18:00",
                 "fri": "08:00-18:00", "sat": "09:00-13:00", "sun": ""}
DIY_HOURS = {"mon": "18:30-20:30", "tue": "18:30-20:30", "wed": "18:30-20:30", "thu": "18:30-20:30",
             "fri": "", "sat": "09:00-17:00", "sun": "10:00-16:00"}
AWAY_WORDS = ("holiday", "away", "trip", "abroad", "annual leave", "off work")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _tz(settings) -> ZoneInfo:
    return ZoneInfo(settings.tz)


def _parse(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _hours(spec: str) -> tuple[time, time] | None:
    if not spec or "-" not in spec:
        return None
    a, b = (x.strip() for x in spec.split("-", 1))
    return time.fromisoformat(a), time.fromisoformat(b)


def busy_times(svc, days: int, people: list[str] | None = None) -> dict[str, list[tuple[datetime, datetime]]]:
    """Busy intervals per person display name (and 'Family'), from the merged calendars."""
    tz = _tz(svc.settings)
    out: dict[str, list[tuple[datetime, datetime]]] = {}
    if not svc.google:
        return out
    for e in svc.google.events_all(days=days):
        start, end = e.get("start") or "", e.get("end") or ""
        if len(start) == 10:  # all-day
            if not any(w in (e.get("summary") or "").lower() for w in AWAY_WORDS):
                continue
            s = datetime.combine(date.fromisoformat(start), time(0), tz)
            f = datetime.combine(date.fromisoformat(end[:10]) if end else date.fromisoformat(start) + timedelta(days=1),
                                 time(0), tz)
        else:
            s, f = _parse(start, tz), _parse(end or start, tz)
        for who in e.get("who") or ["Family"]:
            out.setdefault(who, []).append((s, f))
    return out


def free_slots(svc, duration_min: int = 120, count: int = 3, notice_days: int = 2, horizon_days: int = 14,
               hours: dict | None = None, people: list[str] | None = None, one_per_day: bool = True,
               now: datetime | None = None) -> list[dict]:
    """Up to `count` free slots. A slot is free if at least one of `people` (display names; default: all adults)
    has nothing on, and the family calendar has nothing on."""
    s = svc.settings
    tz = _tz(s)
    now = now or datetime.now(tz)
    hours = hours or (s.behaviour.get("availability") or DEFAULT_HOURS)
    names = s.household.get("names") or {}
    people = people or [names.get(p, p.title()) for p in s.household.get("adults") or []] or ["Family"]
    busy = busy_times(svc, horizon_days + notice_days + 1)
    family = busy.get("Family", [])
    step = timedelta(minutes=30)
    length = timedelta(minutes=max(15, duration_min))
    slots: list[dict] = []
    first_day = (now + timedelta(days=notice_days)).date()
    for offset in range(horizon_days + 1):
        day = first_day + timedelta(days=offset)
        window = _hours(hours.get(DAYS[day.weekday()], ""))
        if not window:
            continue
        cur = datetime.combine(day, window[0], tz)
        day_end = datetime.combine(day, window[1], tz)
        while cur + length <= day_end:
            end = cur + length
            if cur > now and not any(a < end and cur < b for a, b in family):
                free_people = [p for p in people if not any(a < end and cur < b for a, b in busy.get(p, []))]
                if free_people:
                    slots.append({"start": cur.isoformat(), "end": end.isoformat(), "label": label(cur, end),
                                  "who_free": free_people, "in": free_people[0]})
                    if one_per_day:
                        break
            cur += step
        if len(slots) >= count:
            break
    return slots[:count]


def label(start: datetime, end: datetime) -> str:
    """'Tuesday 2 December, 9am to 11am'"""
    def t(d: datetime) -> str:
        h = d.strftime("%I").lstrip("0")
        return f"{h}{d.strftime(':%M') if d.minute else ''}{d.strftime('%p').lower()}"
    return f"{start.strftime('%A')} {start.day} {start.strftime('%B')}, {t(start)} to {t(end)}"


def who_free(svc, start: datetime, end: datetime, prefer: str = "") -> dict:
    """Which adults are free for a visit at this time: {'free': [...], 'busy': [...], 'in': who'll be home}.
    Only busy/free is looked at, never what the appointments are."""
    s = svc.settings
    tz = _tz(s)
    start = start.replace(tzinfo=tz) if start.tzinfo is None else start
    end = end.replace(tzinfo=tz) if end.tzinfo is None else end
    names = s.household.get("names") or {}
    adults = [names.get(p, p.title()) for p in s.household.get("adults") or []]
    days = max(1, (end.date() - datetime.now(tz).date()).days + 1)
    busy = busy_times(svc, min(days, 90)) if svc.google else {}
    free = [p for p in adults if not any(a < end and start < b for a, b in busy.get(p, []))]
    pick = prefer if prefer in free else (free[0] if free else "")
    return {"free": free, "busy": [p for p in adults if p not in free], "in": pick}
