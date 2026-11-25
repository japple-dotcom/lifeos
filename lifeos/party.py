"""Birthday party planner: the jobs, the invites, who's coming, the food and the party-day checklist.

Making a party adds: the jobs before it as to-dos with sensible dates (book the venue, send invites, the cake,
party bags, food, thank-you cards), a "Party: Ava's 8th" list in Google Tasks with the party-day checklist, and the
party in the family calendar. Invites are made as Gmail drafts, one per guest with an email address, for you to send
from Gmail; for everyone else there's the same invite as text to paste into WhatsApp. Replies to those emails are
read every 30 minutes and the answer ticked off (yes / no); you can also tap it yourself.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

CHECKLIST = ["Candles and a lighter", "Cake knife", "Plates, cups and napkins", "Bin bags", "Party bags",
             "Music and a speaker", "First aid kit", "Guest list with parents' phone numbers",
             "Somewhere to put presents (and a list of who gave what)", "Camera charged"]
YES = re.compile(r"\b(yes|yep|we'?d love|love to|can come|will come|will be there|be there|coming|count us in|see you)\b", re.I)
NO = re.compile(r"\b(can'?t|cannot|unable|won'?t be able|not able|sorry|unfortunately|away that|another party)\b", re.I)


def _d(v: str) -> date:
    return date.fromisoformat(v)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def jobs(p: dict) -> list[dict]:
    d = _d(p["date"])

    def before(n: int) -> str:
        return max(date.today(), d - timedelta(days=n)).isoformat()
    out = []
    if p.get("venue"):
        out.append({"title": f"Book {p['venue']} for {p['name']}", "due": before(42)})
    out += [{"title": f"Send the invites for {p['name']}", "due": before(21)},
            {"title": f"Order the cake for {p['name']}", "due": before(7)},
            {"title": f"Party bags for {p['name']}", "due": before(5)},
            {"title": f"Food and drinks for {p['name']}", "due": before(2)},
            {"title": f"Thank-you cards for {p['name']}", "due": (d + timedelta(days=3)).isoformat()}]
    return out


def invite_text(p: dict, guest: str = "") -> str:
    d = _d(p["date"])
    rsvp = _d(p.get("rsvp_by") or (d - timedelta(days=7)).isoformat())
    when = f"{d.strftime('%A %-d %B')}, {p.get('start', '')}" + (f" to {p['end']}" if p.get("end") else "")
    lines = [f"Hello{(' ' + guest) if guest else ''},", "",
             f"You're invited to {p['child']}'s {p['what']}!", "",
             f"When: {when.strip(', ')}"]
    if p.get("venue"):
        lines.append(f"Where: {p['venue']}")
    if p.get("theme"):
        lines.append(f"Theme: {p['theme']}")
    lines += ["", f"Please let us know if you can come by {rsvp.strftime('%A %-d %B')}.", "", "Thank you!"]
    return "\n".join(lines)


def create(svc, child: str, date_str: str, start: str = "", end: str = "", age: int | None = None, venue: str = "",
           theme: str = "", person: str = "") -> dict:
    from . import todos
    d = _d(date_str)
    if d < date.today():
        raise ValueError("That date has passed.")
    child = (child or "").strip()[:40]
    if not child:
        raise ValueError("Whose party is it?")
    what = f"{_ordinal(age)} birthday party" if age else "birthday party"
    name = f"{child}'s {_ordinal(age)}" if age else f"{child}'s party"
    p = {"child": child, "age": age or 0, "date": d.isoformat(), "start": start[:5], "end": end[:5], "venue": venue.strip()[:80],
         "theme": theme.strip()[:60], "name": name, "what": what, "guests": [], "todo_ids": [],
         "event_id": "", "list": f"Party: {name}", "rsvp_by": (d - timedelta(days=7)).isoformat()}
    g = svc.google.get() if svc.google else None
    if g:
        for item in CHECKLIST:
            g.add_task(p["list"], item)
        for j in jobs(p):
            r = todos.add_todo(svc, None, j["title"], person or None, judged=todos.normalise(svc, {
                "title": j["title"], "category": "Family and social", "priority": 2, "due": j["due"], "effort_min": 20}, j["title"]))
            p["todo_ids"].append(r.get("id"))
        try:
            if start:
                s_at = f"{d.isoformat()}T{start}:00"
                e_at = f"{d.isoformat()}T{(end or start)}:00" if end else f"{d.isoformat()}T{int(start[:2]) + 2:02d}{start[2:5]}:00"
                ev = g.add_event(f"{name} party", s_at, e_at, description="Added by Life OS", location=p["venue"],
                                 calendar_id=svc.settings.family_calendar_id or None)
            else:
                ev = g.add_event(f"{name} party", d.isoformat(), (d + timedelta(days=1)).isoformat(),
                                 description="Added by Life OS", calendar_id=svc.settings.family_calendar_id or None)
            p["event_id"] = ev.get("id", "")
        except Exception:
            pass
    return svc.store.put("party", secrets.token_hex(4), p, at=p["date"])


def _save(svc, pid: str, p: dict) -> dict:
    rec = {k: v for k, v in p.items() if k not in ("id", "at", "updated_at")}
    return svc.store.put("party", pid, rec, at=rec["date"])


def get(svc, pid: str) -> dict:
    p = svc.store.get("party", pid)
    if not p:
        raise ValueError("That party isn't there any more.")
    return p


def add_guest(svc, pid: str, name: str, email: str = "", phone: str = "") -> dict:
    p = get(svc, pid)
    name = (name or "").strip()[:60]
    email = (email or "").strip()[:120]
    if not name:
        raise ValueError("Give the guest's name.")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("That email address doesn't look right.")
    if any(x["name"].lower() == name.lower() for x in p["guests"]):
        raise ValueError(f"{name} is already on the list.")
    p["guests"].append({"name": name, "email": email, "phone": phone.strip()[:30], "rsvp": "", "draft_id": "",
                        "thread_id": "", "gift": ""})
    return _save(svc, pid, p)


def remove_guest(svc, pid: str, name: str) -> dict:
    p = get(svc, pid)
    p["guests"] = [x for x in p["guests"] if x["name"] != name]
    return _save(svc, pid, p)


def rsvp(svc, pid: str, name: str, answer: str, gift: str | None = None) -> dict:
    p = get(svc, pid)
    if answer not in ("yes", "no", "maybe", ""):
        raise ValueError("Answer yes, no or maybe.")
    for x in p["guests"]:
        if x["name"] == name:
            x["rsvp"] = answer
            if gift is not None:
                x["gift"] = gift.strip()[:80]
            return _save(svc, pid, p)
    raise ValueError("That guest isn't on the list.")


def make_invites(svc, pid: str, person: str) -> dict:
    """Gmail drafts, one per guest with an email (to send from Gmail). Never sent by Life OS."""
    p = get(svc, pid)
    g = svc.google.get(person)
    made = []
    for x in p["guests"]:
        if not x["email"] or x.get("draft_id") or x.get("thread_id"):
            continue
        d = g.create_draft(x["email"], f"Invitation: {p['name']} birthday party", invite_text(p, x["name"]))
        x["draft_id"] = d.get("id", "")
        x["thread_id"] = (d.get("message") or {}).get("threadId", "")
        x["by"] = person
        made.append(x["name"])
    _save(svc, pid, p)
    return {"made": made, "text": invite_text(p), "without_email": [x["name"] for x in p["guests"] if not x["email"]]}


def check_replies(svc) -> list[str]:
    """Every 30 minutes: answers to the invite emails, read as yes or no."""
    if not svc.google:
        return []
    told = []
    for p in svc.store.records("party"):
        if p["date"] < date.today().isoformat():
            continue
        changed = False
        for x in p["guests"]:
            if x["rsvp"] or not x.get("thread_id"):
                continue
            try:
                msgs = svc.google.get(x.get("by") or None).thread_messages(x["thread_id"])
            except Exception:
                continue
            for m in msgs:
                frm = (m.get("from") or "").lower()
                if x["email"].lower() not in frm:
                    continue
                words = f"{m.get('snippet', '')}"
                ans = "no" if NO.search(words) else "yes" if YES.search(words) else ""
                if ans:
                    x["rsvp"] = ans
                    changed = True
                    told.append(f"{x['name']}: {ans}")
                    break
        if changed:
            _save(svc, p["id"], p)
            if svc.notifier:
                svc.notifier.push(f"{p['name']} party: " + "; ".join(told[-5:]) + ".", title="Party replies",
                                  actions=[{"action": "view", "label": "See who's coming",
                                            "url": f"{svc.settings.public_url}/app/#tasks?sec=projects"}])
    return told


def food_to_shopping(svc, pid: str) -> dict:
    p = get(svc, pid)
    coming = sum(1 for x in p["guests"] if x["rsvp"] in ("yes", "maybe")) + 1  # and the birthday child
    n = max(coming, 6)
    items = [f"Party food for {n} children", f"Drinks for {n} (juice cartons)", f"Party bags x {n}",
             "Birthday candles", "Plates, cups and napkins"]
    added = []
    if svc.google:
        g = svc.google.get()
        for i in items:
            if not g.add_task("Shopping", i).get("duplicate"):
                added.append(i)
    return {"added": added, "for": n}


def parties(svc, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for p in svc.store.records("party"):
        if p["date"] < (today - timedelta(days=14)).isoformat():
            continue
        g = p["guests"]
        out.append({**p, "days_to_go": (_d(p["date"]) - today).days, "yes": sum(x["rsvp"] == "yes" for x in g),
                    "no": sum(x["rsvp"] == "no" for x in g), "maybe": sum(x["rsvp"] == "maybe" for x in g),
                    "no_reply": sum(not x["rsvp"] for x in g), "invite": invite_text(p)})
    return sorted(out, key=lambda p: p["date"])


def delete(svc, pid: str) -> bool:
    p = svc.store.get("party", pid)
    if not p:
        return False
    if p.get("event_id") and svc.google:
        try:
            svc.google.get().delete_event(p["event_id"], calendar_id=svc.settings.family_calendar_id or None)
        except Exception:
            pass
    return svc.store.remove("party", pid)
