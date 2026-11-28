"""One "Tomorrow" message in the evening instead of several: bins to put out, school kit, each grown-up's first
appointment, what to wear for the weather, dinner, parcels and birthdays. Optionally said on the kitchen speaker
(never in quiet hours). While it's on, the separate bins and school kit evening notifications stay quiet.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger("lifeos.tomorrow")
DEFAULTS = {"on": True, "at": "19:00", "speak": False}



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

def cfg(svc) -> dict:
    return {**DEFAULTS, **_jget(svc, "tomorrow_cfg")}


def digest_on(svc) -> bool:
    return bool(cfg(svc)["on"])


def save(svc, on: bool | None = None, at: str | None = None, speak: bool | None = None) -> dict:
    import re
    c = cfg(svc)
    if on is not None:
        c["on"] = bool(on)
    if at is not None:
        if not re.fullmatch(r"(1[6-9]|2[0-1]):[0-5]\d", at or ""):
            raise ValueError("Choose a time between 4pm and 9:59pm.")
        c["at"] = at
    if speak is not None:
        c["speak"] = bool(speak)
    _jset(svc, "tomorrow_cfg", c)
    return c


def _today(svc) -> date:
    from .household import today_local
    return today_local(svc.settings)


def _wear(day: dict | None) -> str:
    if not day:
        return ""
    hi, lo, rain = day.get("high"), day.get("low"), day.get("rain") or 0
    bits = []
    if hi is not None and (hi <= 8 or (lo is not None and lo <= 2)):
        bits.append(f"cold ({round(lo if lo is not None else hi)}°C first thing): coats, hats and gloves")
    elif hi is not None and hi >= 24:
        bits.append(f"hot ({round(hi)}°C): sun cream, hats and water bottles")
    if rain >= 2:
        bits.append(f"rain likely ({round(rain)}mm): umbrellas or waterproofs")
    return "; ".join(bits)


def build(svc, today: date | None = None) -> dict:
    """The parts of tomorrow's message, each isolated so one failing can't stop the rest."""
    from . import bins, deliveries, garden, household, profiles, schoolkit
    today = today or _today(svc)
    tom = today + timedelta(days=1)
    parts: list[dict] = []

    def part(key, fn):
        try:
            text = fn()
        except Exception as e:  # noqa: BLE001 - one part failing mustn't stop the message
            log.warning("tomorrow %s: %s", key, e)
            return
        if text:
            parts.append({"key": key, "text": text})

    def bins_text():
        b = bins.tomorrow(svc, today)
        return f"Put the {bins.say_list(b)} out tonight." if b else ""

    def kit_text():
        t = schoolkit.tomorrow(svc, today)
        bits = [f"School: {t['text']}." if t["items"] else ""]
        if t["wash"]:
            bits.append("Wash " + " and ".join(f"{k}'s" for k in t["wash"]) + " PE kit tonight.")
        return " ".join(b for b in bits if b)

    def diary_text():
        if not svc.google:
            return ""
        ahead = max(2, (tom - _today(svc)).days + 1)
        events = [e for e in svc.google.events_all(days=ahead) if str(e.get("start", ""))[:10] == tom.isoformat()]
        timed = sorted((e for e in events if len(str(e.get("start", ""))) > 10), key=lambda e: e["start"])
        out, said = [], set()
        for e in timed:
            for who in e.get("who") or ["Family"]:
                if who in said:
                    continue
                said.add(who)
                t = datetime.fromisoformat(str(e["start"])[:19])
                when = t.strftime("%I:%M%p").lstrip("0").replace(":00", "").lower()
                out.append(f"{who}: {e.get('summary', 'something')} at {when}")
        allday = [e.get("summary", "") for e in events if len(str(e.get("start", ""))) == 10]
        if allday:
            out.append("All day: " + ", ".join(a for a in allday[:3] if a))
        return ("First things: " + "; ".join(out) + ".") if out else ""

    def weather_text():
        days = garden.forecast(svc)
        w = _wear(next((d for d in days if d.get("date") == tom.isoformat()), None))
        return f"Weather: {w}." if w else ""

    def dinner_text():
        d = household.dinner_on(svc, tom)
        return f"Dinner: {d}." if d else ""

    def parcel_text():
        due = [p["name"] for p in deliveries.current(svc, today)["soon"] if p.get("expected") == tom.isoformat()]
        return ("Parcels due: " + ", ".join(due[:3]) + ".") if due else ""

    def birthday_text():
        names = [p["name"] for p in profiles.profiles(svc)
                 if p.get("birthday") and profiles.next_birthday(p["birthday"], today) == tom.isoformat()]
        return ("Birthday: " + " and ".join(names) + "!") if names else ""

    for key, fn in (("bins", bins_text), ("kit", kit_text), ("diary", diary_text), ("weather", weather_text),
                    ("dinner", dinner_text), ("parcels", parcel_text), ("birthdays", birthday_text)):
        part(key, fn)
    return {"date": tom.isoformat(), "day": tom.strftime("%A"), "parts": parts,
            "text": " ".join(p["text"] for p in parts)}


def evening(svc, now: datetime | None = None) -> str:
    """Every 10 minutes in the evening: once the chosen time has passed, send tomorrow's message once."""
    from . import household, voice
    c = cfg(svc)
    if not c["on"]:
        return ""
    now = now or datetime.now(voice._tz(svc))
    if now.strftime("%H:%M") < c["at"]:
        return ""
    key = f"tomorrow_sent:{now.date().isoformat()}"
    if svc.store.get_state(key, ""):
        return ""
    svc.store.set_state(key, "1")
    msg = build(svc, now.date())
    if not msg["parts"]:
        return ""
    text = msg["text"]
    if svc.notifier:
        svc.notifier.push(text, title=f"Tomorrow ({msg['day']})", priority=3)
    if c["speak"] and not household.in_quiet_hours(svc.settings, now):
        try:
            voice.speak(svc, f"For tomorrow. {text}")
        except Exception as e:  # noqa: BLE001
            log.warning("tomorrow speak: %s", e)
    svc.store.log_event("tomorrow", text[:300])
    return text
