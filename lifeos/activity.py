"""What the household does, and what it will probably need next.

Everything done through Life OS is recorded in one place: shopping ticked off (in the app, Gemini or Google
Tasks), things ordered through basket links, items read from supermarket and Amazon receipts, and to-dos ticked
off. From the gaps between them Life OS predicts when each will be needed again ("toilet roll, about every 3
weeks, last on 4 November"), plus check-ups that are due and birthdays coming up.

Predictions live on their own screen (To-do, Predicted). Ones that are very likely and due soon move across by
themselves: onto the Shopping list two days ahead, or into the to-do list a week ahead. Likely ones get a reminder
on the day instead. "Not yet", "Not needed" and "Stop predicting" teach it.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import date, datetime, timedelta

from .household import add_months, today_local

log = logging.getLogger("lifeos.activity")

SYNONYMS = {
    "toilet roll": ["loo roll", "toilet paper", "bog roll", "toilet tissue", "andrex", "cushelle"],
    "kitchen roll": ["paper towel", "kitchen towel", "plenty"],
    "dishwasher tablets": ["dishwasher tab", "dishwasher pod", "finish quantum", "dishwasher tablet"],
    "washing up liquid": ["fairy liquid", "dish soap", "washing-up liquid"],
    "laundry detergent": ["washing powder", "washing liquid", "laundry pod", "washing pod", "non bio", "bio liquid",
                          "laundry liquid", "persil", "ariel"],
    "fabric conditioner": ["fabric softener", "comfort", "lenor"],
    "bin bags": ["bin liner", "black bag", "rubbish bag", "refuse sack"],
    "nappies": ["nappy", "diaper", "pampers"],
    "baby wipes": ["wet wipes"],
    "milk": ["semi skimmed milk", "semi-skimmed milk", "whole milk", "skimmed milk", "oat milk"],
    "bread": ["loaf", "sliced bread", "sourdough"],
    "eggs": ["egg", "free range eggs"],
    "coffee": ["coffee bean", "ground coffee", "coffee pod", "nespresso"],
    "tea bags": ["teabag", "tea bag", "yorkshire tea", "pg tips"],
    "hand soap": ["handwash", "hand wash", "carex"],
    "toothpaste": ["colgate", "sensodyne"],
    "batteries": ["aa batteries", "aaa batteries", "battery"],
    "cat food": ["cat biscuits", "whiskas", "felix"],
    "dog food": ["dog biscuits", "bakers"],
}
UNITS = r"(?:x|pack|packs|pk|rolls?|tabs?|tablets?|pods?|ml|l|ltr|litres?|g|kg|pints?|pt|pcs?|pieces?|bottles?|tins?|cans?|bags?)"
LEAD_DAYS = {"shopping": 2, "job": 7, "checkup": 30, "gift": 14, "care": 2}
CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def normalise(title: str) -> str:
    """'2 x Andrex Toilet Rolls (16 pack)' -> 'toilet roll'; 'Buy more loo roll' -> 'toilet roll'."""
    t = title.lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = re.sub(r"^(buy|get|order|pick up|more|some|new)\b\s*", "", t)
    t = re.sub(r"^(more|some|new)\b\s*", "", t)
    t = re.sub(rf"\b\d+(?:\.\d+)?\s*{UNITS}\b", " ", t)
    t = re.sub(r"\b(?:x\s*\d+|\d+\s*x|pack of \d+|\d+)\b", " ", t)
    t = re.sub(r"[^a-z' -]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -'")
    for key, words in SYNONYMS.items():
        if t == key or any(re.search(rf"\b{re.escape(w)}s?\b", t) for w in words) or re.search(rf"\b{re.escape(key)}s?\b", t):
            return key
    words = t.split()
    if words:
        words[-1] = _singular(words[-1])
    return " ".join(words)[:80]


def _singular(w: str) -> str:
    if len(w) <= 3 or not w.endswith("s") or w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"           # berries -> berry
    if w.endswith(("oes", "ches", "shes", "xes", "sses")):
        return w[:-2]                 # tomatoes -> tomato, peaches -> peach
    return w[:-1]                     # rolls -> roll


# ---------- recording ----------
def record_bought(svc, title: str, person: str = "", source: str = "app", ref: str | None = None,
                  at: str | None = None, data: dict | None = None, list_name: str = "Shopping") -> None:
    key = normalise(title)
    if not key:
        return
    if svc.store.record_activity("bought", key, title, person, source, list_name, ref=ref, data=data, at=at):
        _new_cycle(svc, f"shopping:{key}")


def record_done(svc, title: str, category: str = "", person: str = "", source: str = "app",
                ref: str | None = None, at: str | None = None, data: dict | None = None) -> None:
    key = normalise(title)
    if not key:
        return
    if svc.store.record_activity("done", key, title, person, source, category, ref=ref, data=data, at=at):
        _new_cycle(svc, f"job:{key}")
        for c in checkups(svc):  # a check-up done (or booked) counts for that check-up
            if _matches_checkup(c, title):
                _new_cycle(svc, c["pid"])


def _new_cycle(svc, pid: str) -> None:
    """Bought or done again: the next prediction starts fresh (unless they said never)."""
    st = svc.store.prediction_states().get(pid)
    if st and st["status"] != "never":
        svc.store.clear_prediction_state(pid)


# ---------- predicting ----------
def _days(rows: list[dict]) -> list[date]:
    return sorted({datetime.fromisoformat(r["at"]).date() if "T" in r["at"] else date.fromisoformat(r["at"][:10])
                   for r in rows})


def _every(days: float) -> str:
    if days < 10:
        return f"{round(days)} days"
    if days < 60:
        w = round(days / 7)
        return "week" if w == 1 else f"{w} weeks"
    m = round(days / 30.4)
    return "month" if m == 1 else (f"{m} months" if m < 11 else "year")


def _pattern(days: list[date]) -> tuple[float, str] | None:
    """Typical gap between times, and how sure we are."""
    if len(days) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False) if (b - a).days > 0]
    if not gaps:
        return None
    med = float(statistics.median(gaps))
    if med < 2:
        return None
    spread = statistics.median([abs(g - med) for g in gaps]) / med
    conf = "high" if len(gaps) >= 3 and spread <= 0.35 else "medium" if spread <= 0.6 else "low"
    return med, conf


def checkups(svc) -> list[dict]:
    """Regular check-ups from settings, one per person where it applies to people."""
    hh = svc.settings.household
    names = hh.get("names") or {}
    adults = [names.get(p, p.title()) for p in hh.get("adults") or []]
    kids = [str(k) for k in hh.get("kids") or []]
    out = []
    has_car_dates = any(c.get("mot_due") for c in svc.store.records("car"))
    for c in svc.settings.behaviour.get("checkups") or []:
        if has_car_dates and re.search(r"\bmot\b", c["what"].lower()):
            continue  # the car's own MOT date is in Renewals (cars.py)
        who = c.get("who", "household")
        people = adults + kids if who == "everyone" else adults if who == "adults" else kids if who == "kids" \
            else [] if who == "household" else [w.strip() for w in str(who).split(",")]
        for person in people or [""]:
            what = c["what"] + (f" for {person}" if person else "")
            out.append({"pid": f"checkup:{normalise(what)}", "what": c["what"], "person": person, "title": what,
                        "every_months": int(c.get("every_months", 12)), "trade": c.get("trade", "")})
    return out


GENERIC = {"check", "up", "the", "a", "an", "service", "test", "annual", "car", "appointment", "visit"}


def _matches_checkup(c: dict, title: str) -> bool:
    """'Dentist check-up for Ava' matches 'Ava dentist appointment'; 'Car MOT' matches 'MOT booked'."""
    low = title.lower()
    words = [w for w in re.findall(r"[a-z]+", c["what"].lower()) if w not in GENERIC] or [c["what"].lower()]
    return bool(re.search(rf"\b{re.escape(words[0])}", low)) and (not c["person"] or c["person"].lower() in low)


def predict(svc, today: date | None = None, on_lists: set[str] | None = None, open_jobs: set[str] | None = None) -> list[dict]:
    """Everything Life OS expects the household to need, soonest first. Hidden: snoozed, dismissed, never."""
    today = today or today_local(svc.settings)
    states = svc.store.prediction_states()
    since = (today - timedelta(days=800)).isoformat()
    out: list[dict] = []

    def add(p: dict) -> None:
        st = states.get(p["pid"])
        if st:
            if st["status"] == "never" or (st["status"] in ("snoozed", "dismissed") and st["until"] > today.isoformat()):
                return
            p["status"] = st["status"]
        else:
            p["status"] = "new"
        p["days_until"] = (date.fromisoformat(p["due"]) - today).days
        out.append(p)

    regular = checkups(svc)
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in svc.store.activity(since=since):
        if r["kind"] in ("bought", "done"):
            groups.setdefault((r["kind"], r["key"]), []).append(r)
    for (kind, key), rows in groups.items():
        days = _days(rows)
        pat = _pattern(days)
        last, title = days[-1], rows[-1]["title"]
        if kind == "bought":
            if not pat or (on_lists and key in on_lists):
                continue
            med, conf = pat
            due = last + timedelta(days=round(med))
            add({"pid": f"shopping:{key}", "kind": "shopping", "key": key, "title": key.capitalize(),
                 "due": due.isoformat(), "confidence": conf, "interval_days": round(med), "last": last.isoformat(),
                 "why": f"Bought {len(days)} times, about every {_every(med)}. Last: {last.strftime('%-d %b')}."})
        else:
            if (open_jobs and key in open_jobs) or any(_matches_checkup(c, title) for c in regular):
                continue  # already on the list, or a regular check-up (predicted on its own schedule)
            if pat:
                med, conf = pat
                why = f"Done {len(days)} times, about every {_every(med)}. Last: {last.strftime('%-d %b %Y')}."
            elif 330 <= (today - last).days <= 420:  # once, about a year ago: probably a yearly job
                med, conf = 365.0, "low"
                why = f"Last year you did this on {last.strftime('%-d %B')}."
            else:
                continue
            due = last + timedelta(days=round(med))
            if (due - today).days < -60:
                continue  # long past: it's stopped being a thing
            add({"pid": f"job:{key}", "kind": "job", "key": key, "title": title, "due": due.isoformat(),
                 "confidence": conf, "interval_days": round(med), "last": last.isoformat(), "why": why,
                 "category": rows[-1]["list_name"]})
    done_rows = groups.items()
    for c in regular:
        if open_jobs and normalise(c["title"]) in open_jobs:
            continue
        last = max((d for (kind, _), rows in done_rows if kind == "done" for r in rows
                    if _matches_checkup(c, r["title"]) for d in _days([r])), default=None)
        if last:
            due = add_months(last, c["every_months"])
            add({"pid": c["pid"], "kind": "checkup", "key": normalise(c["title"]), "title": c["title"],
                 "due": due.isoformat(), "confidence": "high", "interval_days": c["every_months"] * 30, "last": last.isoformat(),
                 "why": f"Every {_every(c['every_months'] * 30.4)}. Last: {last.strftime('%-d %b %Y')}."})
        else:
            add({"pid": c["pid"], "kind": "checkup", "key": normalise(c["title"]), "title": c["title"],
                 "due": today.isoformat(), "confidence": "low", "interval_days": c["every_months"] * 30, "last": "",
                 "why": f"Every {_every(c['every_months'] * 30.4)}. When was the last one?", "needs_last": True})
    from . import care, profiles
    cal = birthdays(svc, today)
    for g in cal + profiles.birthday_predictions(svc, today, skip={b["key"] for b in cal}):
        who = g["key"]
        if open_jobs and any(who in j and re.search(r"present|gift|birthday", j) for j in open_jobs):
            continue  # a present is already on the to-do list
        add(g)
    for c in care.predictions(svc, today):
        if (on_lists and c["key"] in on_lists) or (open_jobs and c["key"] in open_jobs):
            continue
        add(c)
    out.sort(key=lambda p: (p["due"], -CONF_RANK[p["confidence"]]))
    return out


def birthdays(svc, today: date) -> list[dict]:
    """Birthdays in the calendar in the next six weeks: a present to sort two weeks ahead."""
    if not svc.google:
        return []
    try:
        events = svc.google.events_all(days=42)
    except Exception as e:
        log.debug("calendar unreadable for birthdays: %s", e)
        return []
    out = []
    for e in events:
        m = re.search(r"^(.*?)'?s? (birthday|bday)|^birthday[:\s-]+(.+)$", (e.get("summary") or "").strip(), re.I)
        if not m:
            continue
        who = (m.group(1) or m.group(3) or "").strip().rstrip("'") or "someone"
        day = date.fromisoformat(e["start"][:10])
        if day < today:
            continue
        out.append({"pid": f"gift:{normalise(who)}:{day.year}", "kind": "gift", "key": normalise(who),
                    "title": f"Present for {who}", "due": (day - timedelta(days=7)).isoformat(), "confidence": "high",
                    "interval_days": 365, "last": "", "why": f"{who}'s birthday is {day.strftime('%A %-d %B')}."})
    return out


# ---------- acting on predictions ----------
def current_context(svc) -> tuple[set[str], set[str]]:
    """Keys already on the Shopping list, and to-dos already open, so nothing is suggested twice."""
    on_lists, open_jobs = set(), set()
    if svc.google:
        try:
            on_lists = {normalise(t["title"]) for t in svc.google.get().list_tasks("Shopping")}
        except Exception as e:
            log.debug("shopping list unreadable: %s", e)
        try:
            from . import todos
            open_jobs = {normalise(t["title"]) for t in todos.list_todos(svc, include_projects=False)}
        except Exception as e:
            log.debug("to-dos unreadable: %s", e)
    return on_lists, open_jobs


def add_prediction(svc, p: dict, by: str = "Life OS") -> dict:
    """Moves a prediction across: onto the Shopping list, or into the to-do list (where its next step is prepared)."""
    from . import todos
    if p["kind"] == "shopping" or p.get("to") == "shopping":
        created = svc.google.get().add_task("Shopping", p["title"])
        result = {"list": "Shopping", "id": created.get("id"), "title": p["title"]}
    else:
        category = {"checkup": "Health and appointments", "gift": "Family and social"}.get(p["kind"]) \
            or p.get("category") or "Home maintenance"
        if category not in todos.category_names(svc.settings):
            category = "Home maintenance"
        low = p["title"].lower()
        if p["kind"] == "checkup" and (re.search(r"\bmot\b", low) or "car " in low):
            category = "Car"
        elif p["kind"] == "checkup" and "boiler" in low:
            category = "Home maintenance"
        judged = todos.normalise(svc, {"title": p["title"], "category": category, "priority": 3,
                                       "due": max(p["due"], today_local(svc.settings).isoformat())}, p["title"])
        r = todos.add_todo(svc, getattr(svc, "agent", None), p["title"], added_by=by, judged=judged,
                           notes=f"Predicted by Life OS: {p['why']}")
        result = {"list": r.get("list"), "id": r.get("id"), "title": r.get("title")}
    svc.store.set_prediction_state(p["pid"], "added", ref=str(result.get("id") or ""))
    svc.store.log_event("prediction_added", f"{p['pid']} by {by}")
    return result


def snooze(svc, p: dict, days: int = 7) -> None:
    until = (today_local(svc.settings) + timedelta(days=days)).isoformat()
    svc.store.set_prediction_state(p["pid"], "snoozed", until=until)


def dismiss(svc, p: dict) -> None:
    """Not needed this time: shown again around the next cycle."""
    gap = max(7, int(p.get("interval_days") or 30))
    until = (date.fromisoformat(p["due"]) + timedelta(days=max(7, gap // 2))).isoformat()
    svc.store.set_prediction_state(p["pid"], "dismissed", until=until)


def never(svc, p: dict) -> None:
    svc.store.set_prediction_state(p["pid"], "never")


def find(svc, pid: str) -> dict | None:
    on_lists, open_jobs = current_context(svc)
    return next((p for p in predict(svc, on_lists=on_lists, open_jobs=open_jobs) if p["pid"] == pid), None)


def daily(svc, today: date | None = None) -> dict:
    """Every morning: move very likely, due-soon needs across; remind about likely ones due today."""
    today = today or today_local(svc.settings)
    cfg = svc.settings.behaviour.get("predictions") or {}
    if not cfg.get("enabled", True) or not svc.google:
        return {"added": [], "reminded": []}
    min_conf = CONF_RANK.get(cfg.get("auto_add", "high"), 2) if cfg.get("auto_add", "high") != "off" else 99
    on_lists, open_jobs = current_context(svc)
    added, reminded = [], []
    from . import trips
    away = trips.away_now(svc, today)
    for p in predict(svc, today, on_lists, open_jobs):
        if p["status"] in ("added", "never") or p.get("needs_last"):
            continue
        if away and (p["kind"] in ("shopping", "care") or p.get("to") == "shopping"):
            continue  # nobody's home to use it: waits until you're back
        lead = int(cfg.get("lead_days", {}).get(p["kind"], LEAD_DAYS[p["kind"]])) if isinstance(cfg.get("lead_days"), dict) \
            else LEAD_DAYS[p["kind"]]
        if CONF_RANK[p["confidence"]] >= min_conf and p["days_until"] <= lead:
            try:
                r = add_prediction(svc, p)
            except Exception as e:
                log.warning("could not add prediction %s: %s", p["pid"], e)
                continue
            added.append(f"{p['title']} -> {r['list']}")
        elif CONF_RANK[p["confidence"]] >= 1 and p["days_until"] <= 0 and p["status"] != "reminded":
            svc.store.set_prediction_state(p["pid"], "reminded")
            reminded.append(p["title"])
    if svc.notifier and (added or reminded):
        lines = [f"Added: {a.split(' -> ')[0]} ({a.split(' -> ')[1]})" for a in added]
        if reminded:
            lines.append("Running low or due? " + ", ".join(reminded) + ". Check Predicted to add them.")
        svc.notifier.push("\n".join(lines), title="Life OS saw these coming", priority=2,
                          actions=[{"action": "view", "label": "Open",
                                    "url": f"{svc.settings.public_url}/app/#tasks?sec=predicted"}])
    return {"added": added, "reminded": reminded}


def summary_week(svc, today: date | None = None) -> dict:
    """For the week ahead screen: what got done and bought in the last 7 days."""
    today = today or today_local(svc.settings)
    since = (today - timedelta(days=7)).isoformat()
    rows = svc.store.activity(since=since)
    return {"done": [r["title"] for r in rows if r["kind"] == "done"][-20:],
            "bought": sorted({r["key"] for r in rows if r["kind"] == "bought"})}
