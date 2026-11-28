"""Bin days, straight from the council, and a reminder the night before.

Where the dates come from, in this order:
1. A Home Assistant calendar of collections. The Waste Collection Schedule integration (install it from HACS and
   pick your council) makes one; Life OS finds it by itself, or you choose it in Home > Bin days.
2. Your own rota, typed once in Home > Bin days (which bin, which day, every week or every other week).

The night before a collection (19:30 by default) Life OS sends a notification and, if a speaker is set up, says it
out loud ("Bins tonight: recycling and garden waste"). The kitchen display and "Hey Google, which bin goes out"
use the same dates. Nothing here controls anything in the house.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta

log = logging.getLogger("lifeos.bins")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FULL = dict(zip(DAYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], strict=True))
LOOKS_LIKE_BINS = re.compile(r"waste|bin|refuse|recycl|rubbish|garbage|collection|landfill|compost|garden", re.I)


def cfg(svc) -> dict:
    base = dict(svc.settings.behaviour.get("bins") or {})
    try:
        saved = json.loads(svc.store.get_state("bins_cfg", "") or "{}")
    except ValueError:
        saved = {}
    base.update({k: v for k, v in saved.items() if v is not None})
    base.setdefault("remind_at", "19:30")
    base.setdefault("rota", [])
    base.setdefault("calendar", "")
    base.setdefault("speak", True)
    return base


def save(svc, calendar: str | None = None, rota: list[dict] | None = None, remind_at: str | None = None,
         speak: bool | None = None) -> dict:
    try:
        saved = json.loads(svc.store.get_state("bins_cfg", "") or "{}")
    except ValueError:
        saved = {}
    if calendar is not None:
        if calendar and not re.fullmatch(r"calendar\.[a-z0-9_]+", calendar):
            raise ValueError("Choose one of the calendars listed.")
        saved["calendar"] = calendar
    if rota is not None:
        saved["rota"] = [_clean_rota_item(r) for r in rota if (r.get("bin") or "").strip()]
    if remind_at is not None:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", remind_at):
            raise ValueError("Give the reminder time as HH:MM, for example 19:30.")
        saved["remind_at"] = remind_at
    if speak is not None:
        saved["speak"] = bool(speak)
    svc.store.set_state("bins_cfg", json.dumps(saved))
    svc.store.set_state("bins_cache", "")
    return cfg(svc)


def _clean_rota_item(r: dict) -> dict:
    name = " ".join(str(r.get("bin") or "").split())[:40]
    day = str(r.get("day") or "").lower()[:3]
    if day not in DAYS:
        raise ValueError(f"Choose the collection day for {name}.")
    every = int(r.get("every") or 1)
    if every not in (1, 2, 3, 4):
        raise ValueError("Bins go every 1, 2, 3 or 4 weeks.")
    start = str(r.get("from") or "")
    if every > 1:
        try:
            d = date.fromisoformat(start)
        except ValueError as e:
            raise ValueError(f"For {name}, give the date of its next collection so Life OS knows which weeks.") from e
        if DAYS[d.weekday()] != day:
            raise ValueError(f"{d.strftime('%d %B').lstrip('0')} isn't a {FULL[day]}, the day you gave for {name}.")
        start = d.isoformat()
    return {"bin": name, "day": day, "every": every, "from": start}


def calendars(svc) -> list[dict]:
    """Home Assistant calendars, the likely bin ones first."""
    if not svc.ha:
        return []
    try:
        cals = svc.ha.states("calendar")
    except Exception as e:
        log.warning("bins: %s", e)
        return []
    out = [{"id": c["entity_id"], "name": (c.get("attributes") or {}).get("friendly_name") or c["entity_id"],
            "likely": bool(LOOKS_LIKE_BINS.search(c["entity_id"] + " " + str((c.get("attributes") or {}).get("friendly_name", ""))))}
           for c in cals]
    return sorted(out, key=lambda c: (not c["likely"], c["name"].lower()))


def _calendar_id(svc, c: dict) -> str:
    if c.get("calendar"):
        return c["calendar"]
    likely = [x for x in calendars(svc) if x["likely"]]
    return likely[0]["id"] if len(likely) == 1 else ""


def _from_rota(rota: list[dict], start: date, days: int) -> list[tuple[date, str]]:
    out = []
    for r in rota:
        wd = DAYS.index(r["day"])
        first = start + timedelta(days=(wd - start.weekday()) % 7)
        for k in range(0, days, 7):
            d = first + timedelta(days=k)
            if d >= start + timedelta(days=days):
                break
            if r.get("every", 1) > 1 and r.get("from"):
                weeks = (d - date.fromisoformat(r["from"])).days // 7
                if weeks % r["every"]:
                    continue
            out.append((d, r["bin"]))
    return out


def _tidy(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip()
    return name[:1].upper() + name[1:] if name else "Bins"


def collections(svc, days: int = 21, today: date | None = None) -> list[dict]:
    """[{'date': 'YYYY-MM-DD', 'bins': [...]}] from today, soonest first."""
    from . import household
    today = today or household.today_local(svc.settings)
    c = cfg(svc)
    found: list[tuple[date, str]] = []
    source = "none"
    cal = _calendar_id(svc, c) if svc.ha else ""
    if cal:
        try:
            for e in svc.ha.calendar_events(cal, f"{today.isoformat()}T00:00:00", f"{(today + timedelta(days=days)).isoformat()}T00:00:00"):
                st = e.get("start") or {}
                raw = st.get("date") or (st.get("dateTime") or "")[:10] if isinstance(st, dict) else str(st)[:10]
                try:
                    found.append((date.fromisoformat(raw[:10]), _tidy(e.get("summary") or "")))
                except ValueError:
                    continue
            source = "home assistant"
        except Exception as e:
            log.warning("bins calendar %s: %s", cal, e)
    if not found and c["rota"]:
        found, source = _from_rota(c["rota"], today, days), "rota"
    by_day: dict[date, list[str]] = {}
    for d, b in found:
        if today <= d < today + timedelta(days=days) and b not in by_day.setdefault(d, []):
            by_day[d].append(b)
    return [{"date": d.isoformat(), "bins": bins, "source": source} for d, bins in sorted(by_day.items())]


def say_list(bins: list[str]) -> str:
    low = [b[:1].lower() + b[1:] if not b[:2].isupper() else b for b in bins]
    return low[0] if len(low) == 1 else ", ".join(low[:-1]) + " and " + low[-1]


def when(d: date, today: date) -> str:
    n = (d - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else d.strftime("%A") if n < 7 else d.strftime("%A %d %B").replace(" 0", " ")


def overview(svc, today: date | None = None) -> dict:
    from . import household
    today = today or household.today_local(svc.settings)
    items = collections(svc, today=today)
    for i in items:
        i["when"] = when(date.fromisoformat(i["date"]), today)
    c = cfg(svc)
    return {"next": items[0] if items else None, "upcoming": items[:6], "calendars": calendars(svc),
            "calendar": c.get("calendar", ""), "rota": c["rota"], "remind_at": c["remind_at"], "speak": c["speak"],
            "source": items[0]["source"] if items else ("rota" if c["rota"] else "none"), "ha": bool(svc.ha)}


def spoken(svc, today: date | None = None) -> str:
    from . import household
    today = today or household.today_local(svc.settings)
    items = collections(svc, days=14, today=today)
    if not items:
        return ""
    first = items[0]
    d = date.fromisoformat(first["date"])
    lead = "goes out" if len(first["bins"]) == 1 else "go out"
    if (d - today).days == 1:
        return f"Tonight: {say_list(first['bins'])} {lead} for tomorrow."
    return f"Next collection {when(d, today)}: {say_list(first['bins'])}."


def tomorrow(svc, today: date | None = None) -> list[str]:
    from . import household
    today = today or household.today_local(svc.settings)
    for i in collections(svc, days=2, today=today):
        if i["date"] == (today + timedelta(days=1)).isoformat():
            return i["bins"]
    return []


def evening(svc, now: datetime | None = None) -> str:
    """Run every 10 minutes in the evening: once the reminder time has passed, remind about tomorrow's bins once."""
    from . import household, tomorrow as tomorrow_msg, voice
    if tomorrow_msg.digest_on(svc):
        return ""  # it's in the one "Tomorrow" message instead
    tz = voice._tz(svc)
    now = now or datetime.now(tz)
    c = cfg(svc)
    if now.strftime("%H:%M") < c["remind_at"]:
        return ""
    today = now.date()
    key = f"bins_reminded:{today.isoformat()}"
    if svc.store.get_state(key, ""):
        return ""
    bins = tomorrow(svc, today)
    svc.store.set_state(key, "1")
    if not bins:
        return ""
    text = f"Bins tonight: {say_list(bins)}."
    if svc.notifier:
        svc.notifier.push(text, title="Bins", priority=3)
    if c.get("speak") and not household.in_quiet_hours(svc.settings, now):
        try:
            voice.speak(svc, text)
        except Exception as e:
            log.warning("bins speak: %s", e)
    svc.store.log_event("bins", text)
    return text
