"""Leaving home check: one tap shows what's open, on or unlocked, with a button to fix each.

Fixes only ever make the house safer or cheaper to leave: lights and listed plugs off, heating down to the
eco temperature, front door locked. Nothing is ever unlocked or opened from here.
"""
from __future__ import annotations

import logging

log = logging.getLogger("lifeos.leaving")

DOOR_WORDS = ("door", "window", "garage", "gate")


def check(svc) -> dict:
    cfg = svc.settings.behaviour.get("leaving") or {}
    eco = float(cfg.get("eco_temp", 16))
    plugs = set(cfg.get("switch_off") or [])
    ignore = set(cfg.get("ignore") or [])
    if not svc.ha:
        return {"connected": False, "issues": [], "ok": False}
    try:
        states = svc.ha.states()
    except Exception as e:
        return {"connected": True, "issues": [], "ok": False, "error": f"Couldn't reach Home Assistant: {e}"}
    issues = []
    for s in states:
        eid, st = s["entity_id"], s.get("state", "")
        if eid in ignore:
            continue
        a = s.get("attributes") or {}
        name = a.get("friendly_name") or eid
        domain = eid.split(".")[0]
        if domain == "binary_sensor" and st == "on" and (a.get("device_class") in ("door", "window", "garage_door", "opening")
                                                         or any(w in eid for w in DOOR_WORDS)):
            issues.append({"entity_id": eid, "name": name, "text": f"{name} is open", "fix": None,
                           "hint": "Close it before you go."})
        elif domain == "lock" and st == "unlocked":
            issues.append({"entity_id": eid, "name": name, "text": f"{name} is unlocked",
                           "fix": {"service": "lock.lock", "label": "Lock it"}})
        elif domain == "light" and st == "on":
            issues.append({"entity_id": eid, "name": name, "text": f"{name} on",
                           "fix": {"service": "light.turn_off", "label": "Turn off"}})
        elif domain == "switch" and st == "on" and eid in plugs:
            issues.append({"entity_id": eid, "name": name, "text": f"{name} is on",
                           "fix": {"service": "switch.turn_off", "label": "Turn off"}})
        elif domain == "climate" and st in ("heat", "heat_cool", "auto"):
            target = a.get("temperature")
            target = float(target) if target is not None else None
            if target is None or target > eco:
                issues.append({"entity_id": eid, "name": name,
                               "text": f"{name} is set to {target:g}°C" if target is not None else f"{name} is on",
                               "fix": {"service": "climate.set_temperature", "label": f"Down to {eco:g}°C",
                                       "data": {"temperature": eco}}})
    return {"connected": True, "issues": issues, "ok": not issues}


SAFE = {"lock.lock", "light.turn_off", "switch.turn_off", "climate.set_temperature"}


def fix(svc, entity_id: str, person: str) -> dict:
    """Applies the fix the check offered for this device (and nothing else)."""
    issue = next((i for i in check(svc)["issues"] if i["entity_id"] == entity_id and i.get("fix")), None)
    if not issue:
        return {"ok": True, "already": True}
    service = issue["fix"]["service"]
    if service not in SAFE:
        raise ValueError("Not something Life OS changes from here.")
    svc.ha.call_service(service, {"entity_id": entity_id, **(issue["fix"].get("data") or {})})
    svc.store.log_event("leaving_fix", f"{service} {entity_id} by {person}")
    label = issue['fix']['label']
    return {"ok": True, "done": f"{issue['name']}: {label[0].lower() + label[1:]}"}
