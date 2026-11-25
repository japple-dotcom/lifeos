"""Away mode for a trip: the house looks after itself while you're gone, set up with one tap on the trip.

While the trip is on:
- the heating goes down to frost protection (default 12°C) once a day,
- listed lights come on when it gets dark (Home Assistant's sun) and go off at a set time,
- one short "house check" notification each evening: anything open or unlocked, and the temperature.
On the day you come back, the heating goes back up at the warm-up time.

Only the devices in settings `away:` are touched. Nothing is unlocked or opened, and the alarm isn't touched.
"""
from __future__ import annotations

from datetime import datetime, time

from . import leaving, scheduling

DEFAULTS = {"frost_temp": 12, "normal_temp": 20, "lights": [], "lights_off": "22:30", "check_at": "18:00",
            "warm_up": "14:00", "heating_entity": ""}


def cfg(svc) -> dict:
    c = {**DEFAULTS, **(svc.settings.behaviour.get("away") or {})}
    if not c["heating_entity"]:
        c["heating_entity"] = (svc.settings.behaviour.get("energy") or {}).get("heating_entity", "")
    return c


def options(svc) -> dict:
    """What away mode would do here, for the trip card."""
    c = cfg(svc)
    return {"available": bool(svc.ha), "heating": c["heating_entity"], "frost_temp": c["frost_temp"],
            "lights": list(c["lights"]), "lights_off": c["lights_off"], "check_at": c["check_at"], "warm_up": c["warm_up"]}


def set_mode(svc, trip_id: str, on: bool) -> dict:
    t = svc.store.get("trip", trip_id)
    if not t:
        raise ValueError("That trip isn't there any more.")
    if on and not svc.ha:
        raise ValueError("Away mode needs Home Assistant (Settings > Keys and addresses).")
    rec = {k: v for k, v in t.items() if k not in ("id", "at", "updated_at")}
    rec["away"] = bool(on)
    return svc.store.put("trip", trip_id, rec, at=t.get("at") or rec["start"])


def _hm(v: str) -> time:
    return time.fromisoformat(v if len(v) == 5 else "00:00")


def tick(svc, now: datetime | None = None) -> list[str]:
    """Every 10 minutes. Returns what it did (for the log and tests)."""
    if not svc.ha:
        return []
    tz = scheduling._tz(svc.settings)
    now = now.astimezone(tz) if now and now.tzinfo else (now.replace(tzinfo=tz) if now else datetime.now(tz))
    today = now.date().isoformat()
    c = cfg(svc)
    did = []
    for t in svc.store.records("trip"):
        if not t.get("away") or not (t["start"] <= today <= t["end"]):
            continue
        key = f"away:{t['id']}:{today}"

        def once(what: str, key: str = key) -> bool:
            k = f"{key}:{what}"
            if svc.store.get_state(k, ""):
                return False
            svc.store.set_state(k, "1")
            return True

        heat = c["heating_entity"]
        if today < t["end"] and heat and once("heat"):
            svc.ha.call_service("climate.set_temperature", {"entity_id": heat, "temperature": float(c["frost_temp"])})
            did.append(f"heating to {c['frost_temp']}")
        if today == t["end"] and heat and now.time() >= _hm(c["warm_up"]) and once("warm"):
            svc.ha.call_service("climate.set_temperature", {"entity_id": heat, "temperature": float(c["normal_temp"])})
            did.append(f"heating back to {c['normal_temp']}")
        lights = list(c["lights"])
        if lights:
            dark = (svc.ha.state("sun.sun") or {}).get("state") == "below_horizon"
            off_at = _hm(c["lights_off"])
            if dark and time(12, 0) <= now.time() < off_at and once("lights_on"):
                for e in lights:
                    svc.ha.call_service("light.turn_on", {"entity_id": e})
                did.append("lights on")
            if now.time() >= off_at and svc.store.get_state(f"{key}:lights_on", "") and once("lights_off"):
                for e in lights:
                    svc.ha.call_service("light.turn_off", {"entity_id": e})
                did.append("lights off")
        if now.time() >= _hm(c["check_at"]) and once("check"):
            note = house_note(svc, c)
            if svc.notifier:
                svc.notifier.push(note, title="House check", actions=[{"action": "view", "label": "Home",
                                                                       "url": f"{svc.settings.public_url}/app/#home"}])
            did.append("house check")
    return did


def house_note(svc, c: dict | None = None) -> str:
    c = c or cfg(svc)
    chk = leaving.check(svc)
    if chk.get("error"):
        return "Couldn't check the house: Home Assistant isn't answering."
    worries = [i["text"] for i in chk.get("issues", [])
               if not i["entity_id"].startswith(("light.", "climate.")) and i["entity_id"] not in c["lights"]]
    temp = ""
    if c["heating_entity"]:
        try:
            a = (svc.ha.state(c["heating_entity"]) or {}).get("attributes") or {}
            if a.get("current_temperature") is not None:
                temp = f" Inside it's {float(a['current_temperature']):g}°C."
        except Exception:
            temp = ""
    if worries:
        return "Worth a look: " + "; ".join(worries[:4]) + "." + temp
    return "All fine at home: doors shut and locked." + temp
