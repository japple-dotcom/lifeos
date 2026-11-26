"""The Christmas planner: presents per person with a budget, the cards list, and the key dates.

Home > Christmas (shown from October to Christmas). For each person: a budget, ideas (from the gift memory: likes,
sizes, wishlist), what's been bought and what it cost, and whether it's wrapped. Spending is added up against
each budget and the whole. The cards list ticks off sent and received. Key dates: Royal Mail's recommended last
posting dates, the food order (turkey, supermarket delivery slot) and school events found in the calendar
(nativity, carol concert, Christmas jumper day, fair). "Put the dates in our to-dos" files each one once.

Buying stays with the person: ideas link to an Amazon search; nothing is ordered.
"""
from __future__ import annotations

import re
import secrets
import urllib.parse
from datetime import date, timedelta

SCHOOL_WORDS = re.compile(r"nativity|carol|christmas|xmas|jumper day|santa|panto|winter fair|festive|nursery party", re.I)
# Royal Mail's recommended last UK posting dates (Christmas 2026); other years fall back to the usual pattern
POSTING = {
    2026: [("2nd Class post", "2026-12-18"), ("Tracked 48", "2026-12-19"), ("1st Class post", "2026-12-21"),
           ("Special Delivery", "2026-12-23")],
}


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def _key(year: int) -> str:
    return f"xmas-{year}"


def _load(svc, year: int) -> dict:
    rec = svc.store.get("christmas", _key(year)) or {}
    rec = {k: v for k, v in rec.items() if k not in ("id", "at", "updated_at")}
    rec.setdefault("budget", 0)
    rec.setdefault("people", [])
    rec.setdefault("cards", [])
    rec.setdefault("dates", [])
    rec.setdefault("filed", [])
    return rec


def _save(svc, year: int, rec: dict) -> None:
    svc.store.put("christmas", _key(year), rec, at=f"{year}-12-25")


def _money(v) -> float:
    try:
        n = round(float(v or 0), 2)
    except (TypeError, ValueError) as e:
        raise ValueError("Give money as a number of pounds, for example 25 or 12.50.") from e
    if not 0 <= n < 100000:
        raise ValueError("That amount looks wrong.")
    return n


def posting_dates(year: int) -> list[dict]:
    if year in POSTING:
        return [{"what": f"Last posting: {w}", "date": d, "source": "Royal Mail"} for w, d in POSTING[year]]
    return [{"what": "Last posting dates: check royalmail.com/christmas", "date": f"{year}-12-01", "source": "check"}]


def school_events(svc, year: int) -> list[dict]:
    if not svc.google:
        return []
    today = _today(svc)
    end = date(year, 12, 24)
    days = max(0, (end - today).days + 1)
    if not days:
        return []
    try:
        events = svc.google.events_all(days=min(days, 62))
    except Exception:
        return []
    out = []
    for e in events:
        title = e.get("summary") or ""
        if SCHOOL_WORDS.search(title):
            out.append({"what": title[:80], "date": (e.get("start") or "")[:10], "source": "calendar"})
    return out


def plan(svc, year: int | None = None) -> dict:
    from . import profiles
    today = _today(svc)
    year = year or today.year
    rec = _load(svc, year)
    people = []
    total_spent = 0.0
    names = {p.get("name") for p in rec["people"]}
    known = {}
    try:
        known = {p["name"]: p for p in profiles.profiles(svc)}
    except Exception:
        known = {}
    for p in rec["people"]:
        spent = round(sum(float(b.get("cost") or 0) for b in p.get("bought") or []), 2)
        total_spent += spent
        prof = known.get(p["name"]) or {}
        own = list(p.get("ideas") or [])
        ideas = [{"text": i, "link": "https://www.amazon.co.uk/s?k=" + urllib.parse.quote(i)} for i in own]
        for w in prof.get("wishlist") or []:
            if w.get("title") and w["title"] not in own:
                url = w.get("url") if str(w.get("url", "")).startswith("https://") else \
                    "https://www.amazon.co.uk/s?k=" + urllib.parse.quote(w["title"])
                ideas.append({"text": w["title"], "link": url, "wishlist": True})
        people.append({**p, "spent": spent, "left": round(float(p.get("budget") or 0) - spent, 2),
                       "ideas": ideas[:8], "likes": prof.get("likes") or "", "sizes": prof.get("sizes") or "",
                       "done": bool(p.get("bought")) and all(b.get("wrapped") for b in p["bought"])})
    suggestions = [n for n in known if n not in names][:12]
    dates = sorted(posting_dates(year) + school_events(svc, year) +
                   [{**d, "source": "you"} for d in rec["dates"]], key=lambda d: d["date"])
    for d in dates:
        try:
            d["days"] = (date.fromisoformat(d["date"]) - today).days
        except ValueError:
            d["days"] = None
        d["filed"] = f"{d['what']}|{d['date']}" in rec["filed"]
    return {"year": year, "budget": rec["budget"], "spent": round(total_spent, 2),
            "people": people, "suggest_people": suggestions,
            "cards": rec["cards"], "cards_sent": sum(1 for c in rec["cards"] if c.get("sent")),
            "dates": [d for d in dates if d["days"] is None or d["days"] >= -1],
            "days_to_go": (date(year, 12, 25) - today).days, "show": today.month >= 10 or today.month == 1 and today.day <= 6}


def set_budget(svc, total, year: int | None = None) -> dict:
    year = year or _today(svc).year
    rec = _load(svc, year)
    rec["budget"] = _money(total)
    _save(svc, year, rec)
    return plan(svc, year)


def add_person(svc, name: str, budget=0, year: int | None = None) -> dict:
    year = year or _today(svc).year
    name = " ".join((name or "").split())[:40]
    if not name:
        raise ValueError("Who's the present for?")
    rec = _load(svc, year)
    if any(p["name"].lower() == name.lower() for p in rec["people"]):
        raise ValueError(f"{name} is already on the list.")
    rec["people"].append({"id": secrets.token_hex(4), "name": name, "budget": _money(budget), "ideas": [], "bought": []})
    _save(svc, year, rec)
    return plan(svc, year)


def _person(rec: dict, pid: str) -> dict:
    p = next((p for p in rec["people"] if p["id"] == pid), None)
    if not p:
        raise ValueError("That person isn't on the list any more.")
    return p


def update_person(svc, pid: str, budget=None, idea: str = "", remove: bool = False, year: int | None = None) -> dict:
    year = year or _today(svc).year
    rec = _load(svc, year)
    p = _person(rec, pid)
    if remove:
        rec["people"] = [x for x in rec["people"] if x["id"] != pid]
    if budget is not None:
        p["budget"] = _money(budget)
    if idea.strip():
        p.setdefault("ideas", []).append(" ".join(idea.split())[:80])
    _save(svc, year, rec)
    return plan(svc, year)


def bought(svc, pid: str, what: str, cost=0, where: str = "", year: int | None = None) -> dict:
    year = year or _today(svc).year
    rec = _load(svc, year)
    p = _person(rec, pid)
    what = " ".join((what or "").split())[:80]
    if not what:
        raise ValueError("What did you get?")
    p.setdefault("bought", []).append({"id": secrets.token_hex(4), "what": what, "cost": _money(cost),
                                       "where": " ".join((where or "").split())[:40], "wrapped": False})
    p["ideas"] = [i for i in p.get("ideas") or [] if i.lower() != what.lower()]
    _save(svc, year, rec)
    return plan(svc, year)


def present(svc, pid: str, bid: str, wrapped: bool | None = None, remove: bool = False, year: int | None = None) -> dict:
    year = year or _today(svc).year
    rec = _load(svc, year)
    p = _person(rec, pid)
    b = next((b for b in p.get("bought") or [] if b["id"] == bid), None)
    if not b:
        raise ValueError("That present isn't on the list any more.")
    if remove:
        p["bought"] = [x for x in p["bought"] if x["id"] != bid]
    elif wrapped is not None:
        b["wrapped"] = bool(wrapped)
    _save(svc, year, rec)
    return plan(svc, year)


def card(svc, name: str = "", cid: str = "", sent: bool | None = None, received: bool | None = None,
         remove: bool = False, year: int | None = None) -> dict:
    year = year or _today(svc).year
    rec = _load(svc, year)
    if cid:
        c = next((c for c in rec["cards"] if c["id"] == cid), None)
        if not c:
            raise ValueError("That card isn't on the list any more.")
        if remove:
            rec["cards"] = [x for x in rec["cards"] if x["id"] != cid]
        if sent is not None:
            c["sent"] = bool(sent)
        if received is not None:
            c["received"] = bool(received)
    else:
        names = [n.strip()[:60] for n in re.split(r"[\n,;]+", name or "") if n.strip()]
        if not names:
            raise ValueError("Type a name (or several, one per line).")
        have = {c["name"].lower() for c in rec["cards"]}
        for n in names:
            if n.lower() not in have:
                rec["cards"].append({"id": secrets.token_hex(4), "name": n, "sent": False, "received": False})
    rec["cards"].sort(key=lambda c: (c.get("sent", False), c["name"].lower()))
    _save(svc, year, rec)
    return plan(svc, year)


def add_date(svc, what: str, day: str, year: int | None = None) -> dict:
    year = year or _today(svc).year
    what = " ".join((what or "").split())[:80]
    if not what:
        raise ValueError("Say what the date is for, for example 'Order the turkey'.")
    d = date.fromisoformat(day).isoformat()
    rec = _load(svc, year)
    rec["dates"].append({"what": what, "date": d})
    _save(svc, year, rec)
    return plan(svc, year)


def file_dates(svc, by: str = "", year: int | None = None) -> dict:
    """Each key date still ahead into the to-dos, once (posting dates a couple of days early)."""
    from . import todos
    year = year or _today(svc).year
    rec = _load(svc, year)
    p = plan(svc, year)
    filed: list[str] = []
    posting = False  # only the next posting date ahead: one "post the cards" job, not four
    for d in p["dates"]:
        key = f"{d['what']}|{d['date']}"
        if d["source"] == "Royal Mail" and (d.get("filed") or posting):
            posting = True
            continue
        if d.get("filed") or d["days"] is None or d["days"] < 0 or d["source"] == "check":
            continue
        due = date.fromisoformat(d["date"])
        title = d["what"]
        if d["source"] == "Royal Mail":
            posting = True
            title = f"Post Christmas cards and presents ({d['what'].removeprefix('Last posting: ')} by {due.strftime('%d %b').lstrip('0')})"
            due = max(_today(svc), due - timedelta(days=2))
        t = todos.guess(svc, title)
        t.update(title=title, due=due.isoformat(), category=t.get("category"))
        todos.file_todo(svc, t, by or "christmas")
        rec["filed"].append(key)
        filed.append(title)
    _save(svc, year, rec)
    return {"filed": filed}


def wall(svc) -> dict | None:
    p = plan(svc)
    if not p["show"] or p["days_to_go"] < 0:
        return None
    nxt = next((d for d in p["dates"] if d["days"] is not None and d["days"] >= 0), None)
    return {"days_to_go": p["days_to_go"], "next": nxt}
