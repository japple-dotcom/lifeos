"""The freezer list: what's in there, how many portions, which drawer, and since when. "What can we make?" counts it
as food in the house, and the 1st of the month nudges about anything in for more than three months.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date

log = logging.getLogger("lifeos.freezer")
KIND = "freezer"
OLD_DAYS = 90


def _today(svc) -> date:
    from .household import today_local
    return today_local(svc.settings)


def items(svc) -> list[dict]:
    today = _today(svc)
    out = []
    for r in svc.store.records(KIND):
        days = (today - date.fromisoformat(r.get("added") or today.isoformat())).days
        out.append({**r, "days": days, "old": days >= OLD_DAYS})
    out.sort(key=lambda r: (r.get("where") or "", -r["days"], r["name"].lower()))
    return out


def overview(svc) -> dict:
    it = items(svc)
    return {"items": it, "count": sum(int(i.get("portions") or 1) for i in it), "old": [i for i in it if i["old"]],
            "wheres": sorted({i["where"] for i in it if i.get("where")})}


def add(svc, name: str, portions=1, where: str = "", added: str = "", notes: str = "", fid: str = "") -> dict:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("What's going in the freezer?")
    try:
        portions = int(portions or 1)
    except (TypeError, ValueError) as e:
        raise ValueError("Portions should be a number.") from e
    if not 1 <= portions <= 99:
        raise ValueError("Portions should be between 1 and 99.")
    today = _today(svc)
    if added:
        try:
            d = date.fromisoformat(added)
        except ValueError as e:
            raise ValueError("Give the date it went in as a date.") from e
        if d > today:
            raise ValueError("It can't have gone in the freezer in the future.")
        added = d.isoformat()
    old = svc.store.get(KIND, fid) if fid else None
    if fid and not old:
        raise ValueError("That isn't on the freezer list any more.")
    fid = fid or secrets.token_hex(4)
    rec = {"id": fid, "name": name, "portions": portions, "where": " ".join((where or "").split())[:30],
           "added": added or (old or {}).get("added") or today.isoformat(), "notes": " ".join((notes or "").split())[:120]}
    svc.store.put(KIND, fid, rec)
    return rec


def use(svc, fid: str, portions: int = 1) -> dict:
    """Took some out: one fewer portion; the last one takes it off the list."""
    r = svc.store.get(KIND, fid)
    if not r:
        raise ValueError("That isn't on the freezer list any more.")
    left = int(r.get("portions") or 1) - max(1, int(portions or 1))
    if left <= 0:
        svc.store.remove(KIND, fid)
        return {"id": fid, "gone": True, "name": r["name"]}
    r["portions"] = left
    svc.store.put(KIND, fid, r)
    return {**r, "gone": False}


def remove(svc, fid: str) -> None:
    svc.store.remove(KIND, fid)


def food_words(svc) -> list[str]:
    """For "What can we make?": what's in the freezer, as ingredients."""
    return [i["name"] for i in items(svc)]


def monthly(svc, today: date | None = None) -> str:
    """The 1st of the month: one nudge listing what's been in for three months or more."""
    today = today or _today(svc)
    if today.day != 1:
        return ""
    old = [i for i in items(svc) if i["old"]]
    if not old:
        return ""
    names = ", ".join(f"{i['name']} ({i['days'] // 30} months)" for i in old[:6])
    text = f"Use these up from the freezer: {names}."
    if svc.notifier:
        svc.notifier.push(text, title="Freezer", priority=2)
    return text



def since(days: int) -> str:
    return "today" if days == 0 else f"{days} days" if days < 14 else f"{days // 7} weeks" if days < 60 else \
        f"{days // 30} months"
