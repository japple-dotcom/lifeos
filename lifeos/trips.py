"""Trips and holidays: a packing list, the jobs to do before you go, and the house in "away" mode while you're off.

Adding a trip (To-do > Projects > Trips) makes:
- a Google Tasks list "Packing: <trip>" (so it's on everyone's phone and Gemini can read it), built from the trip:
  abroad, flying or driving, children, the time of year, how long;
- to-dos with sensible dates: travel insurance, passports, someone to keep an eye on the house, parking...;
- an all-day "Away: <place>" calendar entry, so tradespeople aren't offered those days and childcare isn't flagged;
- a check that no passport in Renewals runs out within six months of coming home.
While a trip is on, predictions don't move things onto Shopping. On the morning you go, a reminder to do the
leaving home check.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

from . import files

BASE = ["Phone chargers", "Toothbrushes and toothpaste", "Medicines", "Glasses or contact lenses", "Pyjamas",
        "Toiletries", "Clothes for {days} days", "Underwear and socks"]
KIDS = ["Favourite toy or comforter", "Snacks for the journey", "Tablet and headphones", "Books or colouring", "Spare clothes in the day bag"]
ABROAD = ["Passports", "GHIC cards", "Travel insurance details", "Plug adapters", "Bank card that works abroad", "Some local currency"]
PLANE = ["Boarding passes", "Liquids in a clear bag (100 ml each)", "Neck pillows"]
CAR = ["Car snacks and water", "Phone mount and charger for the car", "Breakdown cover number"]
WINTER = ["Warm coats", "Hats, scarves and gloves", "Wellies"]
SUMMER = ["Sun cream", "Swimwear", "Sunglasses and sun hats", "After-sun"]
LONG = ["Laundry bag", "Washing powder tablets"]


def _d(v: str) -> date:
    return date.fromisoformat(str(v)[:10])


def packing(trip: dict, kids: list[str]) -> list[str]:
    start, end = _d(trip["start"]), _d(trip["end"])
    days = max(1, (end - start).days + 1)
    items = [i.format(days=days) for i in BASE]
    if kids and trip.get("kids", True):
        items += KIDS
    if trip.get("abroad"):
        items += ABROAD
    if trip.get("travel") == "plane":
        items += PLANE
    if trip.get("travel") == "car":
        items += CAR
    if start.month in (11, 12, 1, 2, 3):
        items += WINTER
    if start.month in (6, 7, 8) or (trip.get("abroad") and start.month in (5, 9)):
        items += SUMMER
    if days >= 7:
        items += LONG
    return items


def jobs(trip: dict) -> list[dict]:
    start = _d(trip["start"])

    def before(n: int) -> str:
        return max(date.today(), start - timedelta(days=n)).isoformat()
    out = [{"title": f"Ask someone to keep an eye on the house ({trip['name']})", "due": before(7), "category": "Family and social"},
           {"title": f"Pack for {trip['name']} (list in Google Tasks)", "due": before(1), "category": "Family and social"}]
    if trip.get("abroad"):
        out += [{"title": f"Travel insurance for {trip['name']}", "due": before(21), "category": "Admin and renewals"},
                {"title": "Check everyone's passports and GHIC cards", "due": before(28), "category": "Admin and renewals"}]
    if trip.get("travel") == "plane":
        out += [{"title": f"Book airport parking or a lift ({trip['name']})", "due": before(14), "category": "Errands"},
                {"title": f"Online check-in ({trip['name']})", "due": before(1), "category": "Errands"}]
    if trip.get("travel") == "car":
        out.append({"title": f"Check tyres, oil and screenwash before {trip['name']}", "due": before(3), "category": "Car"})
    if trip.get("pets"):
        out.append({"title": f"Book pet care for {trip['name']}", "due": before(30), "category": "Family and social"})
    return out


def passport_warnings(svc, trip: dict) -> list[str]:
    """Passports in Renewals that run out within six months of coming home (many countries ask for this)."""
    if not trip.get("abroad"):
        return []
    limit = _d(trip["end"]) + timedelta(days=183)
    return [f"{r['name']} runs out on {_d(r['due_date']).strftime('%-d %B %Y')}: renew it before {trip['name']}."
            for r in svc.store.renewals() if "passport" in r["name"].lower() and _d(r["due_date"]) <= limit]


def create(svc, name: str, destination: str, start: str, end: str, abroad: bool = False, travel: str = "car",
           kids: bool = True, pets: bool = False, person: str = "") -> dict:
    from . import todos
    name = (name or destination or "Trip").strip()[:50]
    a, b = _d(start), _d(end)
    if b < a:
        raise ValueError("The trip ends before it starts.")
    if a < date.today():
        raise ValueError("That trip has already started.")
    trip = {"name": name, "destination": (destination or name).strip()[:60], "start": a.isoformat(), "end": b.isoformat(),
            "abroad": bool(abroad), "travel": travel if travel in ("car", "plane", "train", "other") else "other",
            "kids": bool(kids), "pets": bool(pets), "todo_ids": [], "event_id": ""}
    kids_names = svc.settings.household.get("kids") or []
    list_name = f"Packing: {name}"
    g = svc.google.get() if svc.google else None
    if g:
        for item in packing(trip, kids_names):
            g.add_task(list_name, item)
        for j in jobs(trip):
            r = todos.add_todo(svc, None, j["title"], person or None, judged=todos.normalise(svc, {
                "title": j["title"], "category": j["category"], "priority": 2, "due": j["due"], "effort_min": 20}, j["title"]))
            trip["todo_ids"].append(r.get("id"))
        try:
            ev = g.add_event(f"Away: {trip['destination']}", a.isoformat(), (b + timedelta(days=1)).isoformat(),
                             description="Added by Life OS", calendar_id=svc.settings.family_calendar_id or None)
            trip["event_id"] = ev.get("id", "")
        except Exception:
            pass
    trip["list"] = list_name
    trip["warnings"] = passport_warnings(svc, trip)
    rid = secrets.token_hex(4)
    return svc.store.put("trip", rid, trip, at=trip["start"])


def trips(svc, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for t in svc.store.records("trip"):
        if t["end"] < (today - timedelta(days=3)).isoformat():
            continue
        packed = []
        if svc.google:
            try:
                packed = svc.google.get().list_tasks(t["list"], include_completed=True)
            except Exception:
                packed = []
        done = sum(1 for p in packed if p.get("status") == "completed")
        out.append({**t, "packed": done, "to_pack": len(packed), "days_to_go": (_d(t["start"]) - today).days,
                    "files": files.for_item(svc, "trip", t["id"]),
                    "on": t["start"] <= today.isoformat() <= t["end"]})
    return sorted(out, key=lambda t: t["start"])


def away_now(svc, today: date | None = None) -> dict | None:
    today = today or date.today()
    return next((t for t in svc.store.records("trip") if t["start"] <= today.isoformat() <= t["end"]), None)


def delete(svc, rid: str) -> bool:
    t = svc.store.get("trip", rid)
    if not t:
        return False
    if svc.google and t.get("event_id"):
        try:
            svc.google.get().delete_event(t["event_id"], svc.settings.family_calendar_id or None)
        except Exception:
            pass
    return svc.store.remove("trip", rid)


def morning(svc, today: date | None = None) -> list[str]:
    """On the day you go: a reminder to do the leaving check (once)."""
    today = today or date.today()
    said = []
    for t in svc.store.records("trip"):
        if t["start"] == today.isoformat() and svc.store.get_state(f"trip_go:{t['id']}", "") != "1":
            if svc.notifier:
                svc.notifier.push(f"Off to {t['destination']} today. Before you go: Home > Check before I go.",
                                  title="Have a good trip", actions=[{"action": "view", "label": "Leaving check",
                                                                       "url": f"{svc.settings.public_url}/app/#home"}])
            svc.store.set_state(f"trip_go:{t['id']}", "1")
            said.append(t["name"])
    return said


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
