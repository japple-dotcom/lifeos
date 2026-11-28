"""Guests: the Wi-Fi as a QR code (scan to join), a guest sheet to print (Wi-Fi, house notes), and visits with a
room-ready checklist, a calendar entry and jobs filed the day before. While a visit is on (or guest mode is switched
on), the kitchen display shows the Wi-Fi code.

The Wi-Fi password stays on the Life OS computer: never sent to the assistant or Google, and only shown on the display
during a visit.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

KIND = "guest_visit"
SECURITY = ("WPA", "WEP", "nopass")
CHECKLIST = ["Make up the spare bed", "Put out clean towels", "Clear a drawer and some hangers", "Tidy the bathroom",
             "Spare key ready", "Print the guest sheet for the fridge"]



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

def _today(svc) -> date:
    from .household import today_local
    return today_local(svc.settings)


def cfg(svc) -> dict:
    c = _jget(svc, "guests_cfg")
    return {"ssid": c.get("ssid", ""), "password": c.get("password", ""), "security": c.get("security", "WPA"),
            "hidden": bool(c.get("hidden")), "notes": c.get("notes", ""), "mode_until": c.get("mode_until", "")}


def save(svc, ssid=None, password=None, security=None, hidden=None, notes=None) -> dict:
    c = cfg(svc)
    if ssid is not None:
        c["ssid"] = str(ssid).strip()[:32]
    if password is not None:
        if len(str(password)) > 63:
            raise ValueError("A Wi-Fi password is at most 63 characters.")
        c["password"] = str(password)
    if security is not None:
        if security not in SECURITY:
            raise ValueError("Choose WPA (most home Wi-Fi), WEP or no password.")
        c["security"] = security
    if hidden is not None:
        c["hidden"] = bool(hidden)
    if notes is not None:
        c["notes"] = str(notes).strip()[:1500]
    if c["ssid"] and c["security"] != "nopass" and not c["password"]:
        raise ValueError("Type the Wi-Fi password (or choose no password).")
    _jset(svc, "guests_cfg", c)
    return c


def _esc(v: str) -> str:
    return re.sub(r'([\\;,":])', r"\\\1", v)


def wifi_text(c: dict) -> str:
    """The standard 'WIFI:' text phones understand when they scan it."""
    if not c.get("ssid"):
        return ""
    t = c.get("security") or "WPA"
    pw = "" if t == "nopass" else f"P:{_esc(c.get('password', ''))};"
    return f"WIFI:T:{t};S:{_esc(c['ssid'])};{pw}{'H:true;' if c.get('hidden') else ''};"


def wifi_qr(svc) -> dict | None:
    from . import qr
    c = cfg(svc)
    text = wifi_text(c)
    if not text:
        return None
    rows = ["".join("1" if d else "0" for d in row) for row in qr.matrix(text)]
    return {"ssid": c["ssid"], "password": c["password"] if c["security"] != "nopass" else "", "qr": rows}


# ---------- visits ----------
def visits(svc, include_past: bool = False) -> list[dict]:
    today = _today(svc)
    out = []
    for v in svc.store.records(KIND):
        if not include_past and v["leave"] < today.isoformat():
            continue
        done = sum(1 for c in v["checklist"] if c["done"])
        out.append({**v, "done": done, "now": v["arrive"] <= today.isoformat() <= v["leave"],
                    "days_to_go": (date.fromisoformat(v["arrive"]) - today).days})
    return sorted(out, key=lambda v: v["arrive"])


def add_visit(svc, name: str, arrive: str, leave: str, room: str = "", notes: str = "") -> dict:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("Who's coming?")
    try:
        a, b = date.fromisoformat(arrive), date.fromisoformat(leave or arrive)
    except ValueError as e:
        raise ValueError("Pick the day they arrive and the day they leave.") from e
    if b < a:
        raise ValueError("They can't leave before they arrive.")
    if b < _today(svc):
        raise ValueError("That visit is already over.")
    vid = secrets.token_hex(4)
    room = " ".join((room or "").split())[:40]
    items = [c.replace("spare bed", f"bed in the {room.lower()}") if room else c for c in CHECKLIST]
    rec = {"id": vid, "name": name, "arrive": a.isoformat(), "leave": b.isoformat(), "room": room,
           "notes": " ".join((notes or "").split())[:300], "filed": False, "calendar": False,
           "checklist": [{"id": f"c{i}", "text": t, "done": False} for i, t in enumerate(items)]}
    svc.store.put(KIND, vid, rec)
    return rec


def _get(svc, vid: str) -> dict:
    v = svc.store.get(KIND, vid)
    if not v:
        raise ValueError("That visit isn't on the list any more.")
    return v


def tick(svc, vid: str, cid: str, done: bool = True) -> dict:
    v = _get(svc, vid)
    for c in v["checklist"]:
        if c["id"] == cid:
            c["done"] = bool(done)
    svc.store.put(KIND, vid, v)
    return v


def add_check(svc, vid: str, text: str) -> dict:
    v = _get(svc, vid)
    text = " ".join((text or "").split())[:80]
    if not text:
        raise ValueError("What else needs doing?")
    v["checklist"].append({"id": f"c{secrets.token_hex(3)}", "text": text, "done": False})
    svc.store.put(KIND, vid, v)
    return v


def remove_visit(svc, vid: str) -> None:
    svc.store.remove(KIND, vid)


def file_jobs(svc, vid: str, person: str) -> dict:
    """The unticked checklist into the to-dos (due the day before they arrive) and the visit into the calendar, once."""
    from . import todos
    v = _get(svc, vid)
    filed = 0
    if not v.get("filed"):
        due = max(_today(svc), date.fromisoformat(v["arrive"]) - timedelta(days=1)).isoformat()
        for c in v["checklist"]:
            if c["done"]:
                continue
            t = todos.guess(svc, c["text"])
            t.update(title=f"{c['text']} ({v['name']} coming)", due=due, timescale="this_week")
            todos.file_todo(svc, todos.normalise(svc, t, c["text"]), person or "guests")
            filed += 1
        v["filed"] = True
    if not v.get("calendar") and svc.google:
        leave = date.fromisoformat(v["leave"]) + timedelta(days=1)
        svc.google.get().add_event(f"Staying: {v['name']}", v["arrive"], leave.isoformat(),
                                   description="Added by Life OS (Guests).")
        v["calendar"] = True
    svc.store.put(KIND, vid, v)
    return {"filed": filed, "calendar": v["calendar"]}


def set_mode(svc, days: int) -> dict:
    """Show the Wi-Fi code on the kitchen display for a few days (0 switches it off)."""
    c = _jget(svc, "guests_cfg")
    days = max(0, min(30, int(days or 0)))
    c["mode_until"] = (_today(svc) + timedelta(days=days - 1)).isoformat() if days else ""
    _jset(svc, "guests_cfg", c)
    return cfg(svc)


def mode_on(svc) -> bool:
    today = _today(svc).isoformat()
    c = cfg(svc)
    return bool((c["mode_until"] and c["mode_until"] >= today) or any(v["now"] for v in visits(svc)))


def overview(svc) -> dict:
    c = cfg(svc)
    return {"wifi": {"ssid": c["ssid"], "security": c["security"], "hidden": c["hidden"], "has_password": bool(c["password"])},
            "notes": c["notes"], "mode_until": c["mode_until"], "mode_on": mode_on(svc), "visits": visits(svc)}


def sheet(svc) -> dict:
    """The printable guest sheet: Wi-Fi (with the code) and the house notes."""
    c = cfg(svc)
    return {"wifi": wifi_qr(svc), "notes": [ln.strip() for ln in c["notes"].splitlines() if ln.strip()],
            "visit": next((v for v in visits(svc) if v["now"] or v["days_to_go"] <= 7), None)}


def wall(svc) -> dict | None:
    if not mode_on(svc):
        return None
    w = wifi_qr(svc)
    now = next((v["name"] for v in visits(svc) if v["now"]), "")
    return {"wifi": w, "welcome": f"Welcome, {now}!" if now else "Welcome!"} if w else None
