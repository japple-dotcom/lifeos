"""Appliance care from real use: dishwasher salt, the washing machine drum clean, filters, vacuum brushes.

Home Assistant says when an appliance finishes a cycle (or keeps a count, or reports filter life). Life OS counts
the cycles since that job was last done (or that thing last bought) and predicts when it's due, on the Predicted
tab like everything else. Set up in settings.yaml:

  appliance_care:
    - {what: Dishwasher salt, entity: sensor.dishwasher_status, cycle_end: finished, every: 30, buy: true}
    - {what: Clean the washing machine drum, entity: sensor.washing_machine_status, cycle_end: finished, every: 40}
    - {what: Robot vacuum filter, entity: sensor.robot_vacuum_filter_remaining, below: 10, buy: true}

cycle_end: the state the appliance shows when a cycle finishes. Or `counter: true` for a sensor that counts cycles.
Or `below: N` for a sensor reporting how much life is left (per cent).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from .activity import normalise
from .dates import fmt as _dfmt

log = logging.getLogger("lifeos.care")


def items(svc) -> list[dict]:
    return [c for c in svc.settings.behaviour.get("appliance_care") or [] if c.get("what") and c.get("entity")]


def poll(svc, now: datetime | None = None) -> list[str]:
    """Every few minutes: a finished cycle (or a counter going up) is recorded against that appliance."""
    if not svc.ha or not items(svc):
        return []
    now = now or datetime.now(timezone.utc)
    try:
        states = {s["entity_id"]: s for s in svc.ha.states()}
    except Exception as e:
        log.debug("Home Assistant unreadable: %s", e)
        return []
    did = []
    for c in items(svc):
        s = states.get(c["entity"])
        if not s or "below" in c:
            continue
        cur = str(s.get("state", ""))
        prev = svc.store.get_state(f"care_last:{c['entity']}", "")
        svc.store.set_state(f"care_last:{c['entity']}", cur)
        n = 0
        if c.get("counter"):
            try:
                n = int(float(cur) - float(prev)) if prev not in ("", "unknown", "unavailable") else 0
            except ValueError:
                n = 0
        elif cur.lower() == str(c.get("cycle_end", "finished")).lower() and prev and prev.lower() != cur.lower():
            n = 1
        for i in range(max(0, min(n, 10))):
            svc.store.record_activity("cycle", c["entity"], c["what"], "", "home_assistant", "",
                                      ref=f"cycle:{c['entity']}:{now.isoformat()}:{i}", at=now.isoformat())
            did.append(c["entity"])
    return did


def _last_reset(svc, key: str) -> str:
    rows = [r for r in svc.store.activity(key=key) if r["kind"] in ("done", "bought")]
    return max((r["at"] for r in rows), default="")


def predictions(svc, today: date) -> list[dict]:
    out = []
    states = {}
    if svc.ha and any("below" in c for c in items(svc)):
        try:
            states = {s["entity_id"]: s for s in svc.ha.states()}
        except Exception:
            states = {}
    for c in items(svc):
        key = normalise(c["what"])
        base = {"pid": f"care:{key}", "kind": "care", "key": key, "title": c["what"], "interval_days": 0, "last": "",
                "to": "shopping" if c.get("buy") else "job", "category": "Home maintenance"}
        if "below" in c:
            try:
                left = float(states.get(c["entity"], {}).get("state"))
            except (TypeError, ValueError):
                continue
            if left <= float(c["below"]) + 10:
                out.append({**base, "due": (today if left <= float(c["below"]) else today + timedelta(days=7)).isoformat(),
                            "confidence": "high" if left <= float(c["below"]) else "medium",
                            "why": f"{left:g}% left, says Home Assistant."})
            continue
        since = _last_reset(svc, key)
        cycles = [r for r in svc.store.activity(kind="cycle", key=c["entity"]) if r["at"] > since]
        if not cycles:
            continue
        every = int(c.get("every", 30))
        recent = [r for r in svc.store.activity(kind="cycle", key=c["entity"],
                                                since=(today - timedelta(days=28)).isoformat())]
        per_day = len(recent) / 28 if recent else 0
        left = every - len(cycles)
        if per_day <= 0 and left > 0:
            continue
        days = 0 if left <= 0 else round(left / per_day)
        if days > 21:
            continue
        since_txt = f"since {date.fromisoformat(since[:10]).strftime(_dfmt('%-d %b'))}" if since else "counted so far"
        out.append({**base, "due": (today + timedelta(days=days)).isoformat(),
                    "confidence": "high" if left <= 3 else "medium",
                    "why": f"{len(cycles)} cycles {since_txt}; it's needed about every {every}."})
    return out


def status(svc) -> list[dict]:
    """For Settings: what's being counted."""
    out = []
    for c in items(svc):
        key = normalise(c["what"])
        since = _last_reset(svc, key)
        n = len([r for r in svc.store.activity(kind="cycle", key=c["entity"]) if r["at"] > since])
        out.append({"what": c["what"], "entity": c["entity"], "cycles": n, "every": c.get("every"),
                    "since": since[:10]})
    return out
