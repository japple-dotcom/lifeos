"""Kids' tablet mode: the children tick their own chores on the kitchen tablet (kids.html).

The tablet can see the wall display and tick a chore for a child, and undo its own tick for 10 minutes. It can't
mark money as paid, change chores, or do anything else: paying out stays with the adults (Home > Chores, where
ticks from the tablet are marked so they can be checked).
"""
from __future__ import annotations

import json
import time

from . import household

UNDO_SECONDS = 600
REPEAT_SECONDS = 600  # the same chore for the same child again within 10 minutes is a double tap


def _logs(svc) -> dict:
    try:
        data = json.loads(svc.store.get_state("tablet_logs", "") or "{}")
    except ValueError:
        data = {}
    cutoff = time.time() - 8 * 86400
    return {k: v for k, v in data.items() if v.get("at", 0) > cutoff}


def tablet_ids(svc) -> set[int]:
    return {int(k) for k in _logs(svc)}


def board(svc) -> dict:
    b = household.chores_board(svc)
    logs = _logs(svc)
    now = time.time()
    out = []
    for k in b["kids"]:
        goals = [{"name": g["name"], "pct": g["pct"], "left": g["left"]} for g in household.goals(svc, k["kid"]) if not g["done"]]
        done = [{"id": d["id"], "chore": d["chore"], "at": d["at"],
                 "undo": str(d["id"]) in logs and now - logs[str(d["id"])]["at"] < UNDO_SECONDS}
                for d in k["done_this_week"]]
        out.append({"kid": k["kid"], "owed": k["owed"], "chores": [{"id": c["id"], "name": c["name"], "value": c["value"]}
                                                                    for c in k["chores"]],
                    "done": done[-12:], "goals": goals})
    return {"kids": out}


def tick(svc, kid: str, chore_id: str) -> dict:
    chore = next((c for c in household.chore_catalogue(svc.settings) if c["id"] == chore_id), None)
    if not chore or kid not in chore["who"]:
        raise ValueError("That chore isn't on the list for that child.")
    logs = _logs(svc)
    now = time.time()
    if any(v["kid"] == kid and v["chore_id"] == chore_id and now - v["at"] < REPEAT_SECONDS for v in logs.values()):
        raise ValueError(f"{kid} has just ticked {chore['name']}.")
    cid = svc.store.log_chore(chore["name"], kid, chore["value"])
    logs[str(cid)] = {"at": now, "kid": kid, "chore_id": chore_id}
    svc.store.set_state("tablet_logs", json.dumps(logs))
    svc.store.log_event("chore", f"{chore['name']} by {kid} (on the kids' tablet)")
    return {"ok": True, "id": cid, "chore": chore["name"], "value": chore["value"]}


def undo(svc, cid: int) -> bool:
    logs = _logs(svc)
    entry = logs.get(str(cid))
    if not entry or time.time() - entry["at"] > UNDO_SECONDS:
        raise ValueError("That can only be undone by a grown-up now.")
    if not svc.store.undo_chore(cid):
        raise ValueError("That one can't be undone.")
    logs.pop(str(cid), None)
    svc.store.set_state("tablet_logs", json.dumps(logs))
    return True
