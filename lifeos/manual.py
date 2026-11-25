"""The home manual: where the stopcock is, the fuse box, the boiler model, Wi-Fi, bin days, the go-to trades.

Kept in Life OS (Home > Home manual), answered by the assistant ("where's the stopcock?"), shown on the kitchen
display, and copied to a Google Tasks list called "Home manual" so Gemini can read it out: "Hey Google, what's on
my Home manual list?" Entries marked private (passwords, alarm codes) stay in Life OS only.
"""
from __future__ import annotations

import logging
import re
import secrets

log = logging.getLogger("lifeos.manual")
LIST = "Home manual"
SECTIONS = ["Water", "Electrics", "Heating", "Gas", "Internet", "Bins", "Appliances", "Safety", "Outside", "Other"]
PRIVATE_WORDS = re.compile(r"password|passcode|pin\b|alarm code|code is|key safe|lockbox", re.I)
STARTERS = [("Water", "Stopcock"), ("Electrics", "Fuse box"), ("Heating", "Boiler"), ("Gas", "Gas meter"),
            ("Internet", "Wi-Fi"), ("Bins", "Bin day")]


def entries(svc, include_private: bool = True) -> list[dict]:
    rows = [r for r in svc.store.records("manual") if include_private or not r.get("private")]
    order = {s: i for i, s in enumerate(SECTIONS)}
    return sorted(rows, key=lambda r: (order.get(r.get("section"), 99), r.get("title", "").lower()))


def save(svc, title: str, details: str, section: str = "Other", private: bool | None = None, rid: str = "") -> dict:
    title, details = title.strip()[:60], details.strip()[:600]
    if not title or not details:
        raise ValueError("Give it a name and say where it is or what to do.")
    section = section if section in SECTIONS else "Other"
    old = svc.store.get("manual", rid) if rid else None
    if private is None:
        private = bool(PRIVATE_WORDS.search(f"{title} {details}"))
    rec = {"title": title, "details": details, "section": section, "private": bool(private),
           "gtask_id": (old or {}).get("gtask_id", "")}
    rid = rid or secrets.token_hex(4)
    rec = _mirror(svc, rec)
    return svc.store.put("manual", rid, rec)


def delete(svc, rid: str) -> bool:
    old = svc.store.get("manual", rid)
    if not old:
        return False
    _unmirror(svc, old)
    return svc.store.remove("manual", rid)


def _mirror(svc, rec: dict) -> dict:
    """Keeps the Google Tasks copy in step (for Gemini). Private entries are never copied."""
    if not svc.google:
        return rec
    try:
        g = svc.google.get()
        if rec["private"]:
            _unmirror(svc, rec)
            return {**rec, "gtask_id": ""}
        notes = f"{rec['details']} ({rec['section']})"
        if rec.get("gtask_id"):
            try:
                g.update_task(LIST, rec["gtask_id"], title=rec["title"], notes=notes)
                return rec
            except Exception:
                pass  # deleted in Google Tasks: add it again
        t = g.add_task(LIST, rec["title"], notes=notes)
        return {**rec, "gtask_id": t.get("id", "")}
    except Exception as e:
        log.warning("couldn't copy the home manual to Google Tasks: %s", e)
        return rec


def _unmirror(svc, rec: dict) -> None:
    if svc.google and rec.get("gtask_id"):
        try:
            svc.google.get().delete_task(LIST, rec["gtask_id"])
        except Exception:
            pass


def search(svc, query: str) -> list[dict]:
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in ("where", "what", "the", "our")]
    rows = entries(svc)
    hits = [r for r in rows if any(w in f"{r['section']} {r['title']} {r['details']}".lower() for w in words)]
    return hits or rows


def for_display(svc) -> list[dict]:
    """Kitchen display: non-private entries only."""
    return [{"section": r["section"], "title": r["title"], "details": r["details"]} for r in entries(svc, False)][:12]


def trades(svc) -> list[dict]:
    """Go-to tradespeople, straight from saved contacts."""
    return [{"trade": c["trade"], "name": c["company"] or c["name"], "phone": c["phone"], "email": c["email"]}
            for c in svc.store.contacts() if c.get("favourite")]
