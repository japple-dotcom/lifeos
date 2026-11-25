"""Who's in, from Home Assistant's people (the person.* entities that follow each phone).

Used by deliveries ("a parcel's coming and nobody's in") and the robot vacuum ("run it while the house is empty").
settings.yaml can name the entities to use (presence: people: [person.joe, person.sue]); otherwise every person.*
entity counts. Without Home Assistant nobody's whereabouts are known, and callers treat that as "don't know".
"""
from __future__ import annotations

import logging

log = logging.getLogger("lifeos.presence")


def people(svc) -> dict | None:
    """{'home': [names], 'away': [names]}, or None when it can't be known."""
    if not svc.ha:
        return None
    wanted = set((svc.settings.behaviour.get("presence") or {}).get("people") or [])
    try:
        found = [p for p in svc.ha.states("person") if not wanted or p["entity_id"] in wanted]
    except Exception as e:
        log.warning("presence: %s", e)
        return None
    if not found:
        return None
    home, away = [], []
    for p in found:
        name = (p.get("attributes") or {}).get("friendly_name") or p["entity_id"].split(".", 1)[1].title()
        (home if p.get("state") == "home" else away).append(name)
    return {"home": home, "away": away}


def anyone_home(svc) -> bool | None:
    """True, False, or None when there's no way to tell."""
    p = people(svc)
    return None if p is None else bool(p["home"])
