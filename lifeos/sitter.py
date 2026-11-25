"""The babysitter sheet: one printable page with who to call, bedtimes, house rules and where things are.

Filled in once (Home > Babysitter sheet) and printed or opened on a phone from /app/sitter.html. It pulls in the
children's names from settings, go-to contacts, and the home manual's non-private entries (stopcock, fuse box,
first aid kit...). Nothing on it is sent anywhere: it's for the person standing in your kitchen. The Wi-Fi password
only appears if you type it here.
"""
from __future__ import annotations

import re

KEY = "sheet"
SITTER_WORDS = re.compile(r"babysit|sitter|nanny|childmind|grandparent|granny|grandma|grandad|nan\b", re.I)


def _clip(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _people(rows, n: int = 6) -> list[dict]:
    out = []
    for r in rows or []:
        name = _clip(r.get("name"), 40)
        if name:
            out.append({"name": name, "role": _clip(r.get("role"), 40), "phone": _clip(r.get("phone"), 24)})
    return out[:n]


def saved(svc) -> dict:
    return svc.store.get("sitter", KEY) or {}


def save(svc, data: dict) -> dict:
    kids = []
    for k in data.get("kids") or []:
        name = _clip(k.get("name"), 30)
        if name:
            kids.append({"name": name, "bedtime": _clip(k.get("bedtime"), 10), "notes": str(k.get("notes") or "").strip()[:400]})
    sheet = {
        "address": _clip(data.get("address"), 160),
        "parents": _people(data.get("parents"), 4),
        "contacts": _people(data.get("contacts"), 6),
        "kids": kids[:6],
        "wifi_name": _clip(data.get("wifi_name"), 60),
        "wifi_password": _clip(data.get("wifi_password"), 80),
        "rules": str(data.get("rules") or "").strip()[:1200],
        "notes": str(data.get("notes") or "").strip()[:1200],
        "include_manual": bool(data.get("include_manual", True)),
    }
    svc.store.put("sitter", KEY, sheet)
    return sheet


def sheet(svc) -> dict:
    """Everything the printable page shows, with sensible defaults from what Life OS already knows."""
    from . import manual
    s = svc.settings
    names = s.household.get("names") or {}
    have = saved(svc)
    kids = have.get("kids") or [{"name": k, "bedtime": "", "notes": ""} for k in s.household.get("kids") or []]
    parents = have.get("parents") or [{"name": names.get(a, a.title()), "role": "", "phone": ""}
                                      for a in s.household.get("adults") or []]
    where = []
    if have.get("include_manual", True):
        where = [{"section": m["section"], "title": m["title"], "details": m["details"]}
                 for m in manual.entries(svc, include_private=False)][:14]
    sitters = [{"name": c.get("company") or c.get("name"), "phone": c.get("phone", ""), "role": c.get("trade", "")}
               for c in svc.store.contacts() if SITTER_WORDS.search(c.get("trade") or "")]
    return {**{k: have.get(k, "") for k in ("address", "wifi_name", "wifi_password", "rules", "notes")},
            "include_manual": have.get("include_manual", True), "kids": kids, "parents": parents,
            "contacts": have.get("contacts") or [], "where": where, "sitters": sitters,
            "emergency": ["Emergency: call 999 (police, fire, ambulance).",
                          "Urgent medical advice that isn't an emergency: call 111.",
                          "Poisons or swallowed something: call 111, or 999 if they're drowsy or struggling to breathe."],
            "saved": bool(have)}
