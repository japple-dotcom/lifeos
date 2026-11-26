"""Meter readings: a monthly nudge, readings from Home Assistant where there's a meter sensor, typed otherwise,
the use each month, and an email to the supplier as a Gmail draft (never sent by Life OS).

Home > Meter readings. Meters are Electricity and Gas to start with (add Water if you're metered). For each, pick
the Home Assistant sensor that counts the meter (an energy, gas or water total), or leave it to type in. On the day
of the month you choose (the 1st by default) Life OS reads the sensors, then sends one notification listing what
it read and what still needs typing.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date, datetime

log = logging.getLogger("lifeos.meters")
DEFAULT = [{"id": "electricity", "name": "Electricity", "unit": "kWh", "sensor": "", "supplier": ""},
           {"id": "gas", "name": "Gas", "unit": "m³", "sensor": "", "supplier": ""}]


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def cfg(svc) -> dict:
    try:
        c = json.loads(svc.store.get_state("meters_cfg", "") or "{}")
    except ValueError:
        c = {}
    c.setdefault("meters", [dict(m) for m in DEFAULT])
    c.setdefault("day", 1)
    return c


def save_cfg(svc, meters: list[dict] | None = None, day: int | None = None) -> dict:
    c = cfg(svc)
    if meters is not None:
        out = []
        for m in meters:
            name = " ".join(str(m.get("name") or "").split())[:30]
            if not name:
                continue
            sensor = str(m.get("sensor") or "")
            if sensor and not re.fullmatch(r"sensor\.[a-z0-9_]+", sensor):
                raise ValueError(f"Choose one of the sensors listed for {name}.")
            supplier = str(m.get("supplier") or "").strip()
            if supplier and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", supplier):
                raise ValueError(f"The supplier email for {name} doesn't look right.")
            mid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or secrets.token_hex(3)
            out.append({"id": m.get("id") or mid, "name": name, "unit": str(m.get("unit") or "")[:8],
                        "sensor": sensor, "supplier": supplier})
        c["meters"] = out
    if day is not None:
        if not 1 <= int(day) <= 28:
            raise ValueError("Choose a day from 1 to 28.")
        c["day"] = int(day)
    svc.store.set_state("meters_cfg", json.dumps(c))
    return c


def sensors(svc) -> list[dict]:
    if not svc.ha:
        return []
    try:
        out = []
        for s in svc.ha.states("sensor"):
            a = s.get("attributes") or {}
            if a.get("state_class") in ("total", "total_increasing") and a.get("device_class") in ("energy", "gas", "water"):
                out.append({"id": s["entity_id"], "name": a.get("friendly_name") or s["entity_id"],
                            "unit": a.get("unit_of_measurement") or "", "kind": a.get("device_class")})
        return out
    except Exception as e:
        log.warning("meter sensors: %s", e)
        return []


def add_reading(svc, meter: str, value: float, day: str = "", source: str = "typed") -> dict:
    c = cfg(svc)
    m = next((x for x in c["meters"] if x["id"] == meter), None)
    if not m:
        raise ValueError("Choose a meter.")
    try:
        v = round(float(value), 3)
    except (TypeError, ValueError) as e:
        raise ValueError("Type the number on the meter.") from e
    if v < 0:
        raise ValueError("A meter reading can't be negative.")
    d = date.fromisoformat(day) if day else _today(svc)
    last = latest(svc, meter)
    if last and last["date"] <= d.isoformat() and v < last["value"]:
        raise ValueError(f"That's lower than the last reading ({last['value']:g} on {last['date']}). Check the number.")
    rid = secrets.token_hex(6)
    svc.store.put("meter_reading", rid, {"meter": meter, "value": v, "date": d.isoformat(), "source": source},
                  at=d.isoformat())
    return {"id": rid, "meter": meter, "value": v, "date": d.isoformat(), "source": source}


def readings(svc, meter: str) -> list[dict]:
    return sorted([r for r in svc.store.records("meter_reading") if r["meter"] == meter], key=lambda r: r["date"])


def latest(svc, meter: str) -> dict | None:
    r = readings(svc, meter)
    return r[-1] if r else None


def use_by_month(svc, meter: str) -> list[dict]:
    """Use between consecutive readings, given to the month the earlier one was taken in (1 Sep to 1 Oct is September)."""
    r = readings(svc, meter)
    out = []
    for a, b in zip(r, r[1:], strict=False):
        days = (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
        if days <= 0:
            continue
        out.append({"month": a["date"][:7], "use": round(b["value"] - a["value"], 1), "days": days,
                    "per_day": round((b["value"] - a["value"]) / days, 2)})
    return out[-12:]


def overview(svc) -> dict:
    c = cfg(svc)
    today = _today(svc)
    meters = []
    for m in c["meters"]:
        last = latest(svc, m["id"])
        meters.append({**m, "latest": last, "use": use_by_month(svc, m["id"]),
                       "due": not last or last["date"][:7] < today.isoformat()[:7] and today.day >= c["day"]})
    return {"meters": meters, "day": c["day"], "sensors": sensors(svc), "ha": bool(svc.ha)}


def read_sensors(svc) -> list[dict]:
    done = []
    for m in cfg(svc)["meters"]:
        if not m["sensor"] or not svc.ha:
            continue
        try:
            st = next((s for s in svc.ha.states("sensor") if s["entity_id"] == m["sensor"]), None)
            if st and st.get("state") not in (None, "unknown", "unavailable"):
                done.append(add_reading(svc, m["id"], float(st["state"]), source="home assistant"))
        except Exception as e:
            log.warning("meter %s: %s", m["name"], e)
    return done


def monthly(svc, now: datetime | None = None) -> str:
    """Daily at 9: on the chosen day, read what Home Assistant can and say what needs typing."""
    today = (now.date() if now else _today(svc))
    c = cfg(svc)
    if today.day != c["day"] or svc.store.get_state(f"meters_done:{today.isoformat()[:7]}", ""):
        return ""
    svc.store.set_state(f"meters_done:{today.isoformat()[:7]}", "1")
    got = read_sensors(svc)
    names = {m["id"]: m for m in c["meters"]}
    read = [f"{names[r['meter']]['name']} {r['value']:g} {names[r['meter']]['unit']}".strip() for r in got]
    need = [m["name"] for m in c["meters"] if m["id"] not in {r["meter"] for r in got}]
    text = "Meter readings: " + "; ".join(filter(None, [
        ("read from Home Assistant: " + ", ".join(read)) if read else "",
        ("please type " + " and ".join(need) + " in Home > Meter readings") if need else ""])) + "."
    if svc.notifier:
        svc.notifier.push(text, title="Meter readings", priority=2)
    return text


def email_draft(svc, meter: str, by: str) -> dict:
    """The latest reading as an email to the supplier, in Gmail drafts for the person to check and send."""
    m = next((x for x in cfg(svc)["meters"] if x["id"] == meter), None)
    last = latest(svc, meter) if m else None
    if not m or not last:
        raise ValueError("Add a reading first.")
    names = svc.settings.household.get("names") or {}
    body = (f"Hello,\n\nHere is my {m['name'].lower()} meter reading: {last['value']:g} {m['unit']}, taken on "
            f"{date.fromisoformat(last['date']).strftime('%d %B %Y').lstrip('0')}.\n\n"
            f"Thank you,\n{names.get(by, by.title())}")
    d = svc.google.get(by).create_draft(m.get("supplier", ""), f"{m['name']} meter reading", body)
    return {"ok": True, "draft": (d or {}).get("id", ""), "to": m.get("supplier", "")}
