"""Important documents: where each one is kept, and its expiry date in Renewals.

Home > Documents. Passports, birth and marriage certificates, driving licences, the will, house deeds, policies,
the car's V5C logbook... For each: whose it is, where it's kept ("the grey folder in the study drawer"), an expiry
date if it has one, and a photo or PDF if you like (kept on this computer only). Life OS never asks for or keeps
document numbers. An expiry date puts a renewal on the list ("Passport: Ava") with a reminder well ahead (passports
9 months, since some countries want 6 months left; others 2 months). "Where's Leo's passport?" works in the search
box there and on the speakers; document details never go to the assistant or Google.
"""
from __future__ import annotations

import re
import secrets
from datetime import date

KINDS = ["Passport", "Birth certificate", "Driving licence", "Marriage certificate", "Will", "Power of attorney",
         "House deeds", "Mortgage papers", "Home insurance policy", "Life insurance policy", "Pension papers",
         "Car logbook (V5C)", "National Insurance letter", "Exam certificates", "Other"]
REMIND_DAYS = {"Passport": 270, "Driving licence": 60}


def save(svc, kind: str, whose: str, where: str, expires: str = "", notes: str = "", did: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError("Choose what kind of document it is.")
    whose = " ".join((whose or "").split())[:40]
    where = " ".join((where or "").split())[:160]
    if not where:
        raise ValueError("Say where it's kept.")
    if re.search(r"\b[A-Z0-9]{8,}\b", (notes or "") + " " + where) and re.search(r"\d", (notes or "") + where):
        raise ValueError("Life OS doesn't keep document numbers. Leave the number off and just say where it is.")
    if expires:
        expires = date.fromisoformat(expires).isoformat()
    did = did or secrets.token_hex(6)
    old = svc.store.get("document", did) or {}
    rec = {"kind": kind, "whose": whose, "where": where, "expires": expires, "notes": (notes or "").strip()[:300],
           "renewal": old.get("renewal")}
    rec["renewal"] = _sync_renewal(svc, rec)
    svc.store.put("document", did, rec)
    return {**rec, "id": did}


def _title(rec: dict) -> str:
    return f"{rec['kind']}: {rec['whose']}" if rec.get("whose") else rec["kind"]


def _sync_renewal(svc, rec: dict) -> int | None:
    rid = rec.get("renewal")
    if not rec.get("expires"):
        if rid:
            svc.store.delete_renewal(int(rid))
        return None
    remind = REMIND_DAYS.get(rec["kind"], 60)
    if rid and svc.store.update_renewal(int(rid), name=_title(rec), due_date=rec["expires"], remind_days=remind):
        return int(rid)
    return svc.store.add_renewal(_title(rec), rec["expires"], "Admin and renewals",
                                 f"Kept: {rec['where']}", remind_days=remind, repeat_months=0)


def remove(svc, did: str) -> bool:
    from . import files
    rec = svc.store.get("document", did)
    if rec and rec.get("renewal"):
        svc.store.delete_renewal(int(rec["renewal"]))
    for f in files.for_item(svc, "document", did):
        files.delete(svc, f["id"])
    return svc.store.remove("document", did)


def listing(svc) -> dict:
    from . import files
    today = date.today()
    docs = []
    for d in svc.store.records("document"):
        days = (date.fromisoformat(d["expires"]) - today).days if d.get("expires") else None
        docs.append({**{k: d.get(k, "") for k in ("id", "kind", "whose", "where", "expires", "notes")},
                     "days": days, "files_list": files.for_item(svc, "document", d["id"]),
                     "soon": days is not None and days <= REMIND_DAYS.get(d["kind"], 60)})
    docs.sort(key=lambda d: (d["whose"].lower(), KINDS.index(d["kind"]) if d["kind"] in KINDS else 99))
    return {"documents": docs, "kinds": KINDS}


def where_is(svc, question: str) -> str:
    """'where's Leo's passport' -> 'Leo's passport: in the grey folder in the study drawer.'"""
    q = question.lower()
    hits = []
    for d in svc.store.records("document"):
        kind_said = d["kind"].lower().split(" (")[0] in q or d["kind"].lower().split()[0] in q
        if kind_said and (not d.get("whose") or d["whose"].lower() in q):
            hits.append(d)
    if not hits:
        return ""
    return " ".join(f"{(d['whose'] + chr(39) + 's ') if d.get('whose') else ''}{d['kind'].lower()}: {d['where']}." for d in hits[:3])
