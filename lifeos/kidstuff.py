"""Kids' tablet extras: a reading log, a homework checklist and a weekly star chart.

On the kitchen tablet (kids.html) a child can log reading ("20 minutes of The Twits") and tick homework done, and
undo their own entry for 10 minutes, like chores. Grown-ups add homework (Home > Chores and pocket money > Homework,
or by asking: "Ava has spellings for Friday") and can remove anything. Stars are never money: pocket money still
only comes from chores, and only grown-ups pay out.

Stars each week (Monday to Sunday): 1 per chore, 1 for each day with 10+ minutes of reading, 1 per homework done
(2 if it was done before the day it was due). The weekly target (settings.yaml kids.star_target, 15 by default) is
shown as a line on the chart.
"""
from __future__ import annotations

import secrets
import time
from datetime import date, datetime, timedelta

UNDO_SECONDS = 600
READ_MIN = 10


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def _kids(svc) -> list[str]:
    return list(svc.settings.household.get("kids") or [])


def _check_kid(svc, kid: str) -> str:
    if kid not in _kids(svc):
        raise ValueError("Choose one of the children.")
    return kid


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


# ---------- reading ----------
def log_reading(svc, kid: str, minutes: int, book: str = "", by: str = "", day: str = "") -> dict:
    _check_kid(svc, kid)
    minutes = int(minutes or 0)
    if not 1 <= minutes <= 300:
        raise ValueError("Reading time is between 1 and 300 minutes.")
    d = date.fromisoformat(day) if day else _today(svc)
    if d > _today(svc) or (_today(svc) - d).days > 14:
        raise ValueError("Reading can be logged for today or the last two weeks.")
    rid = secrets.token_hex(6)
    rec = {"kid": kid, "minutes": minutes, "book": " ".join((book or "").split())[:80], "date": d.isoformat(),
           "by": by, "ts": time.time()}
    svc.store.put("reading", rid, rec, at=d.isoformat())
    svc.store.log_event("reading", f"{kid} read {minutes} min{(' of ' + rec['book']) if rec['book'] else ''}")
    return {**rec, "id": rid}


def reading(svc, kid: str = "", days: int = 28) -> list[dict]:
    since = (_today(svc) - timedelta(days=days)).isoformat()
    return [r for r in svc.store.records("reading", since=since) if not kid or r["kid"] == kid]


def books(svc, kid: str) -> list[str]:
    seen: list[str] = []
    for r in reversed(reading(svc, kid, 90)):
        if r.get("book") and r["book"] not in seen:
            seen.append(r["book"])
    return seen[:5]


# ---------- homework ----------
def add_homework(svc, kid: str, title: str, due: str = "", by: str = "") -> dict:
    _check_kid(svc, kid)
    title = " ".join((title or "").split())[:100]
    if not title:
        raise ValueError("Say what the homework is.")
    if due:
        due = date.fromisoformat(due).isoformat()
    hid = secrets.token_hex(6)
    rec = {"kid": kid, "title": title, "due": due, "done": "", "done_ts": 0, "done_by": "", "added_by": by}
    svc.store.put("homework", hid, rec, at=_today(svc).isoformat())
    return {**rec, "id": hid}


def homework(svc, kid: str = "", include_old: bool = False) -> list[dict]:
    today = _today(svc)
    out = []
    for h in svc.store.records("homework"):
        if kid and h["kid"] != kid:
            continue
        if not include_old and h.get("done") and (today - date.fromisoformat(h["done"])).days > 7:
            continue
        out.append(h)
    return sorted(out, key=lambda h: (bool(h.get("done")), h.get("due") or "9999", h.get("title", "")))


def _put(svc, kind: str, rec: dict) -> None:
    rid, at = rec["id"], rec.get("at", "")
    svc.store.put(kind, rid, {k: v for k, v in rec.items() if k not in ("id", "at", "updated_at")}, at=at)


def tick_homework(svc, hid: str, by: str = "") -> dict:
    h = svc.store.get("homework", hid)
    if not h:
        raise ValueError("That homework isn't on the list any more.")
    if h.get("done"):
        raise ValueError("That's already ticked.")
    h.update(done=_today(svc).isoformat(), done_ts=time.time(), done_by=by)
    _put(svc, "homework", h)
    svc.store.log_event("homework", f"{h['kid']}: {h['title']} done")
    return h


def undo(svc, kind: str, rid: str, tablet: bool) -> bool:
    """The tablet can undo its own reading entry or homework tick for 10 minutes; grown-ups can undo any."""
    if kind == "reading":
        r = svc.store.get("reading", rid)
        if not r:
            raise ValueError("That's already gone.")
        if tablet and (r.get("by") != "kids" or time.time() - r.get("ts", 0) > UNDO_SECONDS):
            raise ValueError("That can only be undone by a grown-up now.")
        return svc.store.remove("reading", rid)
    if kind == "homework":
        h = svc.store.get("homework", rid)
        if not h or not h.get("done"):
            raise ValueError("That isn't ticked.")
        if tablet and (h.get("done_by") != "kids" or time.time() - h.get("done_ts", 0) > UNDO_SECONDS):
            raise ValueError("That can only be undone by a grown-up now.")
        h.update(done="", done_ts=0, done_by="")
        _put(svc, "homework", h)
        return True
    raise ValueError("Nothing to undo.")


def remove_homework(svc, hid: str) -> bool:
    return svc.store.remove("homework", hid)


# ---------- stars ----------
def stars(svc, day: date | None = None) -> dict:
    """This week's star chart: per child, per day, with where each star came from."""
    today = day or _today(svc)
    start = week_start(today)
    days = [start + timedelta(days=i) for i in range(7)]
    target = int((svc.settings.behaviour.get("kids") or {}).get("star_target", 15) or 15)
    since = datetime.combine(start, datetime.min.time()).isoformat()
    chores = svc.store.chores_since(since) if hasattr(svc.store, "chores_since") else []
    from . import household
    out = []
    for kid in _kids(svc):
        per = {d.isoformat(): {"chores": 0, "reading": 0, "homework": 0} for d in days}
        for c in chores:
            if c.get("who") == kid and not household.is_money_move(c.get("chore", "")):
                d = (c.get("at") or "")[:10]
                if d in per:
                    per[d]["chores"] += 1
        mins: dict[str, int] = {}
        for r in reading(svc, kid, 8):
            mins[r["date"]] = mins.get(r["date"], 0) + int(r.get("minutes", 0))
        for d, m in mins.items():
            if d in per and m >= READ_MIN:
                per[d]["reading"] = 1
        for h in homework(svc, kid, include_old=True):
            if h.get("done") in per:
                per[h["done"]]["homework"] += 2 if h.get("due") and h["done"] < h["due"] else 1
        row = [{"date": d, "day": date.fromisoformat(d).strftime("%a"), "stars": sum(v.values()), **v}
               for d, v in per.items()]
        total = sum(x["stars"] for x in row)
        out.append({"kid": kid, "days": row, "total": total, "target": target,
                    "reading_minutes": sum(m for d, m in mins.items() if d >= start.isoformat()),
                    "reached": total >= target})
    return {"week_of": start.isoformat(), "kids": out, "target": target}


def board(svc) -> dict:
    """What the tablet shows on the Reading, Homework and Stars tabs."""
    today = _today(svc)
    tablet_cut = time.time() - UNDO_SECONDS
    kids = []
    for kid in _kids(svc):
        week = [r for r in reading(svc, kid, 7) if r["date"] >= week_start(today).isoformat()]
        kids.append({
            "kid": kid,
            "reading": [{"id": r["id"], "minutes": r["minutes"], "book": r.get("book", ""), "date": r["date"],
                         "undo": r.get("by") == "kids" and r.get("ts", 0) > tablet_cut} for r in week][-8:],
            "books": books(svc, kid),
            "homework": [{"id": h["id"], "title": h["title"], "due": h.get("due", ""), "done": h.get("done", ""),
                          "undo": bool(h.get("done")) and h.get("done_by") == "kids" and h.get("done_ts", 0) > tablet_cut}
                         for h in homework(svc, kid)],
        })
    return {"kids": kids, "stars": stars(svc, today)}
