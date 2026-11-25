"""Arranging tradespeople and appointments, end to end.

  1. Spot that a to-do needs a trade (electrician, plumber, gas engineer...).
  2. Offer the household's saved go-to first, plus local options (Claude web search) with phone/email.
  3. Draft an email offering three times that are free in the family calendars. The person can edit it.
  4. One tap sends it from their Gmail (or Call / Text buttons for phone-only contacts).
  5. Watch the email thread: tell them when a reply arrives; chase politely after a few days of silence.
  6. One tap puts the agreed appointment in the calendar and moves the to-do to that date.

Nothing is sent without the person tapping Send on the exact text shown.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

from . import scheduling

log = logging.getLogger("lifeos.trades")

TRADES = [  # trade, keywords, typical visit length (minutes)
    ("gas engineer", "boiler gas heating no hot water combi radiators cold thermostat flue", 90),
    ("electrician", "socket sockets electric electrics electrical fuse fuses consumer unit rewire wiring tripping trips "
                    "light fitting switch spotlights outside light ev charger", 120),
    ("plumber", "leak leaking leaks tap taps toilet cistern pipe pipes drain blocked sink shower bath plumbing", 90),
    ("roofer", "roof slates tiles slipped flashing chimney leak ceiling stain fascia", 120),
    ("locksmith", "lock locked locks key keys door lock", 60),
    ("carpenter", "door doors hinge skirting cupboard fitted shelves joinery floorboard", 180),
    ("decorator", "decorate decorating paint painting wallpaper", 240),
    ("gardener", "garden lawn hedge hedges trees tree overgrown", 180),
    ("window cleaner", "windows window cleaner", 60),
    ("cleaner", "clean cleaning cleaner tenancy", 180),
    ("appliance repair", "washing machine dishwasher oven cooker fridge freezer tumble dryer appliance", 60),
    ("pest control", "mice rats wasps wasp nest pest pests", 60),
    ("handyman", "handyman odd jobs flat pack assemble mount tv curtain pole", 120),
    # Appointments you go to (same flow: who to ask, times you're free, one-tap email or call)
    ("dentist", "dentist dental teeth tooth check-up checkup hygienist toothache", 45),
    ("optician", "optician opticians eye eyes test glasses lenses", 45),
    ("vet", "vet vets vaccination vaccinations booster jab jabs flea worming neuter spay", 30),
    ("garage", "mot car service tyre tyres brakes garage clutch exhaust", 60),
    ("hairdresser", "haircut hair cut hairdresser barber trim fringe", 45),
]
APPOINTMENTS = {"dentist", "optician", "vet", "garage", "hairdresser"}
TRADE_WORDS = {t for t, _, _ in TRADES}


ALIASES = {"sparky": "electrician", "boiler engineer": "gas engineer", "barber": "hairdresser", "haircut": "hairdresser",
           "mot": "garage", "eye test": "optician", "dental": "dentist"}
NAMED = sorted(TRADE_WORDS | set(ALIASES), key=len, reverse=True)
FAULT_WORDS = ("fix", "repair", "broken", "broke", "not working", "stopped working", "isn't working", "doesn't work",
               "won't", "dead", "no power", "leak", "drip", "need", "get someone", "get a", "book", "arrange",
               "service", "install", "replace", "tripping", "trips", "blocked", "sort", "quote", "keeps", "banging",
               "noise", "cracked", "smell", "flicker", "sparking", "overgrown", "infestation", "due", "overdue",
               "appointment", "check-up", "checkup", "needs a", "hurts", "ache")


def detect_trade(text: str) -> str:
    """Which trade a to-do needs, or "" if it's something people normally do themselves."""
    low = text.lower()
    named = next((t for t in NAMED if re.search(rf"\b{re.escape(t)}s?\b", low)), "")
    if named:
        return ALIASES.get(named, named)
    diy = any(w in low for w in ("myself", "diy", "i'll", "change the", "replace the bulb", "lightbulb", "light bulb"))
    if diy or any(w in low for w in ("bulb", "battery", "descale", "bleed")):
        return ""  # things people normally do themselves
    best, score = "", 0
    for trade, words, _ in TRADES:
        hits = sum(1 for w in words.split() if re.search(rf"\b{re.escape(w)}\b", low))
        if hits > score:
            best, score = trade, hits
    return best if score >= 1 and any(w in low for w in FAULT_WORDS) else ""


def visit_minutes(trade: str) -> int:
    return next((m for t, _, m in TRADES if t == trade), 120)


def household_area(svc) -> str:
    area = (svc.settings.household.get("area") or "").strip()
    if area:
        return area
    loc = next((d for d in svc.store.home_details() if d["area"].lower() == "home" and d["item"].lower() == "location"),
               None)
    return loc["details"] if loc else ""


def sender_name(svc, person: str) -> str:
    return (svc.settings.household.get("names") or {}).get(person, person.title())


def search_link(trade: str, area: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(f'{trade} near {area}' if area else trade)}"


def article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def compose_email(svc, person: str, trade: str, job: str, details: str, contact_name: str, slots: list[dict]) -> dict:
    area = household_area(svc)
    hello = f"Hello {contact_name}," if contact_name else "Hello,"
    times = "\n".join(f"- {s['label']}" for s in slots) or "- Any weekday morning in the next two weeks"
    job_text = job.strip().rstrip(".")
    first = (job_text.split() or [""])[0].strip(",.'s")
    hh = svc.settings.household
    names = {str(n) for n in (hh.get("kids") or [])} | {str(n) for n in (hh.get("names") or {}).values()} | {"I", "Mum", "Dad"}
    if first not in names and job_text[:2] != job_text[:2].upper():  # keep names and acronyms (MOT) as they are
        job_text = job_text[:1].lower() + job_text[1:]
    extra = (" " + details.strip()) if details.strip() else ""
    if trade in APPOINTMENTS:
        body = (f"{hello}\n\nI'd like to book an appointment for this: {job_text}.{extra}\n\n"
                f"Would any of these times suit?\n{times}\n\n"
                f"If none of those work, please suggest another time.\n\n"
                f"Many thanks,\n{sender_name(svc, person)}")
        subject = f"Appointment request: {job.rstrip('.')}"[:120]
        return {"subject": subject, "body": body}
    body = (f"{hello}\n\nWe're looking for {article(trade)} {trade} to help with this: "
            f"{job_text}.{extra}\n\n"
            f"Would any of these times suit you to come and have a look (or do the job)?\n{times}\n\n"
            f"If none of those work, please suggest another time.{f' We are in {area}.' if area else ''}\n\n"
            f"Many thanks,\n{sender_name(svc, person)}")
    subject = f"{trade.capitalize()} needed{f' in {area}' if area else ''}: {job.rstrip('.')}"[:120]
    return {"subject": subject, "body": body}


def sms_link(phone: str, body: str) -> str:
    return f"sms:{re.sub(r'[^0-9+]', '', phone)}?body={quote(body[:300])}"


def tel_link(phone: str) -> str:
    return f"tel:{re.sub(r'[^0-9+]', '', phone)}"


def arrange_plan(svc, todo: dict, trade: str, found: list[dict] | None = None, email: dict | None = None,
                 summary: str = "") -> dict:
    """Builds the 'arrange' suggestion: who to contact (saved go-to first), free times, and a ready email."""
    person = todo.get("who") if todo.get("who") in (svc.settings.household.get("adults") or []) else svc.google.owner
    names = svc.settings.household.get("names") or {}
    people = [names.get(person, person.title())] if todo.get("who") not in (None, "", "anyone") else None
    slots = scheduling.free_slots(svc, visit_minutes(trade), 3, svc.settings.behaviour.get("trade_notice_days", 2),
                                  people=people)
    saved = svc.store.contacts(trade)
    options = [{"contact_id": c["id"], "name": c["name"] if not c["company"] else f"{c['company']} ({c['name']})",
                "email": c["email"], "phone": c["phone"], "website": c["website"], "favourite": bool(c["favourite"]),
                "greet": c["name"].split()[0] if c["company"] else c["name"],
                "why": "Your go-to " + trade if c["favourite"] else "Saved " + trade, "saved": True} for c in saved[:3]]
    for f in (found or [])[: max(0, 4 - len(options))]:
        if not (f.get("email") or f.get("phone") or f.get("website")):
            continue
        if any(o["email"] and o["email"].lower() == (f.get("email") or "").lower() for o in options):
            continue
        options.append({"contact_id": None, "name": f.get("name", "")[:80], "greet": f.get("name", "")[:80],
                        "email": f.get("email", "")[:120],
                        "phone": f.get("phone", "")[:40], "website": f.get("website", "")[:200],
                        "why": f.get("why", "")[:160], "favourite": False, "saved": False})
    area = household_area(svc)
    greet = options[0]["greet"] if options else ""
    draft = email or compose_email(svc, person, trade, todo["title"], "", greet, slots)
    for o in options:
        if o["phone"]:
            o["tel"] = tel_link(o["phone"])
            ask = (f"Hi, I'd like to book an appointment: {todo['title']}." if trade in APPOINTMENTS
                   else f"Hi, we need {article(trade)} {trade} for: {todo['title']}.")
            o["sms"] = sms_link(o["phone"], ask + " Could you do " + (slots[0]["label"] if slots else "sometime soon")
                                + "? " + sender_name(svc, person))
    if not summary and trade in APPOINTMENTS:
        summary = (f"Book this with {'your ' + trade if options else article(trade) + ' ' + trade}. "
                   + (f"Your go-to is {options[0]['name']}. " if options and options[0]["favourite"] else "")
                   + ("Email or call; the message offers times you're free." if options else
                      f"No saved {trade} yet: search locally, then save one."))
    if not summary:
        summary = (f"This needs {article(trade)} {trade}. "
                   + (f"Your go-to is {options[0]['name']}. " if options and options[0]["favourite"] else "")
                   + ("Pick who to ask; the email offers times you're free." if options else
                      "No saved " + trade + " yet: search locally, then save one."))
    return {"kind": "arrange", "trade": trade, "appointment": trade in APPOINTMENTS, "summary": summary,
            "contacts": options, "slots": slots,
            "email": draft, "sender": person, "search_url": search_link(trade, area), "area": area}


# ---------- sending, replies, chasing, booking ----------
def send_request(svc, todo: dict, contact: dict, subject: str, body: str, slots: list[dict], person: str) -> dict:
    """Sends the email (the person has just tapped Send on this exact text) and starts watching for a reply."""
    if not contact.get("email"):
        raise ValueError("This contact has no email address. Use Call or Text instead.")
    _save_new_contact(svc, contact)
    drafted = svc.store.request_for_task(todo["id"])
    if drafted and drafted["status"] == "drafted":  # sending from the app instead: the Gmail draft isn't needed
        cancel_request(svc, drafted["id"], "sent from Life OS instead")
    sent = svc.google.get(person).send_email(contact["email"], subject[:200], body[:5000])
    rid = svc.store.add_request(person=person, task_id=todo["id"], list_name=todo["list"],
                                contact_id=contact["contact_id"], contact_name=contact["name"],
                                contact_email=contact["email"], trade=contact.get("trade", ""), subject=subject[:200],
                                task_title=todo.get("title", "")[:200],
                                thread_id=sent.get("threadId", ""), slots=slots)
    svc.store.touch_contact(contact["contact_id"])
    owner = svc.google.owner
    svc.store.set_task_meta(owner, todo["id"], next_action=f"Waiting for {contact['name']} to reply")
    svc.store.log_event("trade_request_sent", f"{contact['name']} <{contact['email']}> for '{todo['title']}' by {person}")
    _changed(svc, rid)
    return svc.store.get_request(rid)


def ask_for_quotes(svc, todo: dict, contacts: list[dict], subject: str, body: str, slots: list[dict],
                   person: str) -> list[dict]:
    """The same email to several firms (each greeted by name), each tracked on its own so replies can be compared."""
    sent = []
    for c in [c for c in contacts if c.get("email")][:4]:
        greet = c.get("greet") or c["name"]
        personal = re.sub(r"^Hello [^,\n]+,", f"Hello {greet},", body, count=1)
        sent.append(send_request(svc, todo, c, subject, personal, slots, person))
    if not sent:
        raise ValueError("None of those have an email address. Call or text them instead.")
    svc.store.set_task_meta(svc.google.owner, todo["id"], next_action=f"Waiting for quotes from {len(sent)} firms")
    return sent


def requests_for_task(svc, task_id: str) -> list[dict]:
    """Every live request for a job (more than one when asking several firms for quotes)."""
    return [r for r in svc.store.requests(("drafted", "sent", "chased", "replied", "booked", "visited"))
            if r["task_id"] == task_id]


def _save_new_contact(svc, contact: dict) -> None:
    if not contact.get("contact_id"):  # a newly found tradesperson: save them for next time
        contact["contact_id"] = svc.store.save_contact(name=contact["name"], trade=contact.get("trade", ""),
                                                       email=contact["email"], phone=contact.get("phone", ""),
                                                       website=contact.get("website", ""))


def _changed(svc, rid: int) -> None:
    """Keep the Google Tasks 'Waiting on' copy in step (so Gemini and the Tasks app show the same)."""
    try:
        from . import bridge
        bridge.mirror_request(svc, rid)
        r = svc.store.get_request(rid)
        if r and r["status"] not in ("done", "cancelled"):
            bridge.write_back_quietly(svc, r["task_id"])
    except Exception as e:  # never let the mirror break the real work
        log.warning("waiting-on mirror for request %s failed: %s", rid, e)


def open_job(svc, task_id: str) -> list[dict]:
    """Tapping the notification opens that to-do, highlighted."""
    return [{"action": "view", "label": "Open", "url": f"{svc.settings.public_url}/app/#tasks/{task_id}"}]


def gmail_person(svc, prefer: str | None = None) -> str | None:
    """Whose Gmail to draft from: the preferred person if they gave Life OS Gmail access, else the first who did."""
    order = ([prefer] if prefer else []) + [svc.google.owner] + list(svc.google.people)
    for p in order:
        if p in svc.google.people and getattr(svc.google.people[p], "has_gmail", True):
            return p
    return None


def draft_request(svc, todo: dict, contact: dict, subject: str, body: str, slots: list[dict], person: str) -> dict:
    """Saves the email to the person's Gmail drafts (nothing is sent). Sending it from Gmail starts the tracking."""
    if not contact.get("email"):
        raise ValueError("This contact has no email address.")
    _save_new_contact(svc, contact)
    d = svc.google.get(person).create_draft(contact["email"], subject[:200], body[:5000])
    rid = svc.store.add_request(person=person, task_id=todo["id"], list_name=todo["list"],
                                contact_id=contact["contact_id"], contact_name=contact["name"],
                                contact_email=contact["email"], trade=contact.get("trade", ""), subject=subject[:200],
                                task_title=todo.get("title", "")[:200], status="drafted", draft_id=d.get("id", ""),
                                thread_id=(d.get("message") or {}).get("threadId", ""), slots=slots)
    svc.store.set_task_meta(svc.google.owner, todo["id"],
                            next_action=f"Send the email to {contact['name']}: it's in your Gmail drafts")
    svc.store.log_event("trade_request_drafted", f"{contact['name']} for '{todo['title']}' in {person}'s Gmail")
    _changed(svc, rid)
    return svc.store.get_request(rid)


def auto_draft(svc, todo: dict, plan: dict) -> dict | None:
    """When a job's next step is emailing a tradesperson, have that email waiting in Gmail drafts."""
    if not svc.settings.behaviour.get("draft_trade_emails", True) or plan.get("kind") != "arrange":
        return None
    contact = next((c for c in plan.get("contacts") or [] if c.get("email")), None)
    email = plan.get("email") or {}
    if not contact or not email.get("body") or not todo.get("id") or svc.store.request_for_task(todo["id"]):
        return None
    person = gmail_person(svc, plan.get("sender"))
    if not person:
        return None
    body = email["body"]
    if contact is not (plan.get("contacts") or [None])[0] and contact.get("greet"):  # greet the one we're writing to
        body = re.sub(r"^Hello [^,\n]+,", f"Hello {contact['greet']},", body, count=1)
    c = {**contact, "trade": plan.get("trade", "")}
    try:
        r = draft_request(svc, todo, c, email.get("subject", ""), body, plan.get("slots") or [], person)
        if svc.notifier:
            svc.notifier.push(f"An email to {c['name']} about \"{todo.get('title', 'the job')}\" is in your Gmail drafts, "
                              "offering times you're free. Send it from Gmail, or change it in Life OS.",
                              title="Ready to send", priority=2, actions=open_job(svc, todo["id"]))
        return r
    except Exception as e:
        log.warning("could not draft email for '%s': %s", todo.get("title"), e)
        return None


def cancel_request(svc, rid: int, why: str = "") -> None:
    r = svc.store.get_request(rid)
    if not r:
        return
    g = svc.google.get(r["person"]) if r["person"] in svc.google.people else None
    for did in (r.get("draft_id"), r.get("reminder_draft_id")):
        if did and g:
            try:
                g.delete_draft(did)
            except Exception as e:
                log.debug("draft %s not deleted: %s", did, e)
    svc.store.update_request(rid, status="cancelled", draft_id="", reminder_draft_id="")
    svc.store.log_event("trade_request_cancelled", f"{r['contact_name']}: {why}")
    _changed(svc, rid)


def close_for_task(svc, task_id: str) -> None:
    """The job is done: stop watching for replies and tidy away drafts and the Waiting on copy."""
    for r in requests_for_task(svc, task_id):
        if r["status"] == "drafted":
            cancel_request(svc, r["id"], "job done")
            continue
        if r.get("reminder_draft_id") and r["person"] in svc.google.people:
            try:
                svc.google.get(r["person"]).delete_draft(r["reminder_draft_id"])
            except Exception as e:
                log.debug("reminder draft not deleted: %s", e)
        svc.store.update_request(r["id"], status="done", reminder_draft_id="")
        _changed(svc, r["id"])


def _sent_count(msgs: list[dict], email: str) -> int:
    """Our own messages in the thread (sent, not drafts)."""
    return sum(1 for m in msgs if "SENT" in (m.get("labels") or [])
               or (not m.get("labels") and not _is_reply(m, email) and m.get("from") == "me"))


def check_drafts(svc) -> list[str]:
    """Every few minutes: notice emails sent (or binned) from Gmail drafts."""
    did = []
    for r in svc.store.requests(("drafted", "chased")):
        did_key = "draft_id" if r["status"] == "drafted" else "reminder_draft_id"
        draft = r.get(did_key)
        if not draft or r["person"] not in svc.google.people:
            continue
        g = svc.google.get(r["person"])
        try:
            if g.draft_exists(draft):
                continue
            msgs = g.thread_messages(r["thread_id"]) if r["thread_id"] else []
        except Exception as e:
            log.warning("could not check draft for request %s: %s", r["id"], e)
            continue
        sent_now = _sent_count(msgs, r["contact_email"])
        now = _uk_now(svc).isoformat()
        if r["status"] == "drafted":
            if sent_now >= 1:
                svc.store.update_request(r["id"], status="sent", sent_at=now, draft_id="")
                svc.store.set_task_meta(svc.google.owner, r["task_id"], next_action=f"Waiting for {r['contact_name']} to reply")
                svc.store.log_event("trade_request_sent", f"{r['contact_name']} from Gmail drafts")
                did.append(f"sent:{r['id']}")
            else:  # the draft was deleted: they've decided not to send it
                svc.store.update_request(r["id"], status="cancelled", draft_id="")
                svc.store.set_task_meta(svc.google.owner, r["task_id"], next_action="")
                did.append(f"binned:{r['id']}")
        else:
            if sent_now > (r.get("sent_count") or 0):  # the reminder went from Gmail: restart the chase clock
                svc.store.update_request(r["id"], status="sent", chased_at=now, reminder_draft_id="")
                svc.store.set_task_meta(svc.google.owner, r["task_id"], next_action=f"Waiting for {r['contact_name']} to reply")
                did.append(f"reminded:{r['id']}")
            else:
                svc.store.update_request(r["id"], reminder_draft_id="")
                did.append(f"reminder-binned:{r['id']}")
        _changed(svc, r["id"])
    return did


DAYS_FULL = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def suggest_time(svc, r: dict, reply: str) -> dict | None:
    """The offered time their reply seems to agree to: the day they name (and the hour, if they give one)."""
    slots = r.get("slots") or []
    if not slots:
        return None
    low = reply.lower()
    pick = next((s for s in slots if DAYS_FULL[datetime.fromisoformat(s["start"]).weekday()] in low), None)
    if not pick:
        pick = next((s for s in slots if DAYS_FULL[datetime.fromisoformat(s["start"]).weekday()][:3] in low), slots[0])
    start = datetime.fromisoformat(pick["start"])
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        start = start.replace(hour=hour, minute=int(m.group(2) or 0))
    minutes = visit_minutes(r["trade"])
    return {"start": start.isoformat(), "duration_min": minutes,
            "label": scheduling.label(start, start + timedelta(minutes=minutes))}


GENERIC_MAIL = {"gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "outlook.com", "live.co.uk", "yahoo.com",
                "yahoo.co.uk", "icloud.com", "me.com", "btinternet.com", "aol.com", "sky.com", "virginmedia.com"}
AUTO_REPLY = ("automatic reply", "auto-reply", "autoreply", "out of office", "out of the office", "auto:")


def _is_from(sender: str, email: str) -> bool:
    return bool(email) and email.lower() in (sender or "").lower()


def _is_reply(m: dict, email: str) -> bool:
    """A real reply from them: not our own sent message, not an out-of-office."""
    if any(m.get("subject", "").lower().startswith(a) for a in AUTO_REPLY):
        return False
    labels = m.get("labels") or []
    if labels:  # Gmail marks what we sent with SENT
        return "SENT" not in labels and "DRAFT" not in labels
    sender = (m.get("from") or "").lower()
    domain = email.lower().rsplit("@", 1)[-1] if email and "@" in email else ""
    # Same address, or someone else at the same company (jobs@ -> dave@)
    return _is_from(sender, email) or bool(domain and domain not in GENERIC_MAIL and f"@{domain}" in sender)


def greet_for(svc, r: dict) -> str:
    """'Dave' for 'Spark Electrical (Dave Hughes)'; the name as saved otherwise."""
    contact = svc.store.get_contact(r["contact_id"]) if r.get("contact_id") else None
    if contact:
        return contact["name"].split()[0] if contact["company"] else contact["name"]
    m = re.search(r"\(([^)\s]+)[^)]*\)", r.get("contact_name", ""))
    return m.group(1) if m else r.get("contact_name", "")


def _uk_now(svc) -> datetime:
    return datetime.now(ZoneInfo(svc.settings.tz))


def check_requests(svc, now: datetime | None = None) -> list[str]:
    """Run every 30 minutes: spot replies, and chase silence after `chase_after_days`."""
    now = now or _uk_now(svc)
    chase_days = int(svc.settings.behaviour.get("chase_after_days", 3))
    did = check_drafts(svc)
    for r in svc.store.requests(("sent", "chased")):
        try:
            msgs = svc.google.get(r["person"]).thread_messages(r["thread_id"]) if r["thread_id"] else []
        except Exception as e:  # a deleted thread or lost access shouldn't stop the others
            log.warning("could not read thread for request %s: %s", r["id"], e)
            continue
        replies = [m for m in msgs if _is_reply(m, r["contact_email"])]
        if replies:
            snippet = replies[-1]["snippet"][:300]
            try:  # the inbox routine skips handled mail, so the reply doesn't become a second to-do
                svc.google.get(r["person"]).mark_handled([m["id"] for m in replies])
            except Exception as e:
                log.debug("could not label reply: %s", e)
            svc.store.update_request(r["id"], status="replied", replied_at=now.isoformat(), reply_snippet=snippet,
                                     suggested=suggest_time(svc, r, snippet), reminder_draft_id="")
            if r.get("reminder_draft_id"):  # they replied: the reminder isn't needed
                try:
                    svc.google.get(r["person"]).delete_draft(r["reminder_draft_id"])
                except Exception as e:
                    log.debug("reminder draft not deleted: %s", e)
            svc.store.set_task_meta(svc.google.owner, r["task_id"], next_action=f"{r['contact_name']} replied: agree a time")
            if svc.notifier:
                svc.notifier.push(f"{r['contact_name']} replied: \"{snippet[:140]}\"", title="Reply from " + r["contact_name"],
                                  priority=4, actions=open_job(svc, r["task_id"]))
            did.append(f"replied:{r['id']}")
            _changed(svc, r["id"])
            continue
        sent = datetime.fromisoformat(r["chased_at"] or r["sent_at"])
        if r["status"] == "sent" and now - sent.astimezone(now.tzinfo) > timedelta(days=chase_days):
            reminder_id = ""
            if svc.settings.behaviour.get("draft_trade_emails", True) and getattr(svc.google.get(r["person"]), "has_gmail", True):
                try:  # the reminder waits in Gmail drafts, in the same thread: one tap to send from Gmail
                    m = reminder_email(svc, r)
                    reminder_id = svc.google.get(r["person"]).create_draft(
                        r["contact_email"], m["subject"], m["body"], r["thread_id"] or None).get("id", "")
                except Exception as e:
                    log.warning("could not draft reminder for request %s: %s", r["id"], e)
            svc.store.update_request(r["id"], status="chased", chased_at=now.isoformat(), reminder_draft_id=reminder_id,
                                     sent_count=_sent_count(msgs, r["contact_email"]))
            svc.store.set_task_meta(svc.google.owner, r["task_id"],
                                    next_action=f"No reply from {r['contact_name']}: send a reminder or try someone else")
            if svc.notifier:
                where = "It's in your Gmail drafts, or" if reminder_id else ""
                svc.notifier.push(f"No reply from {r['contact_name']} after {chase_days} days. A reminder is ready: "
                                  f"{where} open Life OS to send it or ask someone else.".replace(":  ", ": "),
                                  title="Chase " + r["contact_name"], actions=open_job(svc, r["task_id"]))
            did.append(f"chase:{r['id']}")
            _changed(svc, r["id"])
    # After a booked visit: ask once whether it got sorted, so the to-do doesn't linger
    for r in svc.store.requests(("booked",)):
        appt = r.get("appointment") or {}
        try:
            end = datetime.fromisoformat(appt.get("end") or appt.get("start", ""))
        except ValueError:
            continue
        if end.astimezone(now.tzinfo) + timedelta(hours=2) < now:
            svc.store.update_request(r["id"], status="visited")
            svc.store.set_task_meta(svc.google.owner, r["task_id"],
                                    next_action=f"Did {r['contact_name']} sort it? Tick it off, or ask again")
            if svc.notifier:
                svc.notifier.push(f"Did {r['contact_name']} sort it? Tick it off in Life OS, or ask again.",
                                  title="How did it go?", actions=open_job(svc, r["task_id"]))
            did.append(f"visited:{r['id']}")
            _changed(svc, r["id"])
    return did


def reminder_email(svc, r: dict) -> dict:
    slots = scheduling.free_slots(svc, visit_minutes(r["trade"]), 3, 1)
    times = "\n".join(f"- {s['label']}" for s in slots)
    return {"subject": "Re: " + r["subject"],
            "body": (f"Hello {greet_for(svc, r)},\n\nJust following up on my email below. Are you able to help? "
                     f"These times would still suit us:\n{times}\n\nMany thanks,\n{sender_name(svc, r['person'])}"),
            "slots": slots}


def send_reminder(svc, rid: int, subject: str = "", body: str = "") -> dict:
    """Sends the reminder the person has just read (and maybe edited) and tapped Send on."""
    r = svc.store.get_request(rid)
    if not r or r["status"] not in ("sent", "chased"):
        raise ValueError("Nothing to chase.")
    m = reminder_email(svc, r) if not body.strip() else {"subject": subject or "Re: " + r["subject"], "body": body}
    g = svc.google.get(r["person"])
    g.send_email(r["contact_email"], m["subject"], m["body"], r["thread_id"] or None)
    if r.get("reminder_draft_id"):  # sent from the app, so the copy in Gmail drafts isn't needed
        try:
            g.delete_draft(r["reminder_draft_id"])
        except Exception as e:
            log.debug("reminder draft not deleted: %s", e)
    svc.store.update_request(rid, status="sent", chased_at=_uk_now(svc).isoformat(), reminder_draft_id="")
    svc.store.log_event("trade_request_chased", r["contact_name"])
    _changed(svc, rid)
    return svc.store.get_request(rid)


def book(svc, rid: int, start_iso: str, duration_min: int, by: str, confirm_body: str = "") -> dict:
    """Puts the agreed visit in the family calendar and moves the to-do to that day.

    confirm_body: a short confirmation the person has read and ticked to send, in the same email thread."""
    r = svc.store.get_request(rid)
    if not r:
        raise ValueError("Not found.")
    tz = ZoneInfo(svc.settings.tz)
    start = datetime.fromisoformat(start_iso)
    start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
    end = start + timedelta(minutes=max(15, duration_min))
    contact = svc.store.get_contact(r["contact_id"]) if r["contact_id"] else None
    appointment = r["trade"] in APPOINTMENTS
    home = scheduling.who_free(svc, start, end, prefer=svc.google.display_name(r["person"]) if r["person"] else "")
    title = (f"{_todo_title(svc, r)} ({r['contact_name']})" if appointment
             else f"{(r['trade'] or 'Visit').capitalize()}: {r['contact_name']}"
             + (f" ({home['in']} in)" if home["in"] else ""))
    desc = "\n".join(x for x in (f"Booked through Life OS for: {_todo_title(svc, r)}",
                                 (f"{home['in']} is home for this." if home["in"] and not appointment else
                                  "Nobody's free then in the calendars: check who can be in." if not appointment else ""),
                                 f"Phone: {contact['phone']}" if contact and contact["phone"] else "",
                                 f"Email: {r['contact_email']}" if r["contact_email"] else "") if x)
    g = svc.google.get()
    cal = svc.settings.family_calendar_id or None
    ev = g.add_event(title, start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"), desc,
                     calendar_id=None if cal == "primary" else cal)
    appt = {"start": start.isoformat(), "end": end.isoformat(), "event_id": ev.get("id", ""),
            "label": scheduling.label(start, end), "in": "" if appointment else home["in"]}
    if confirm_body.strip() and r["contact_email"]:
        try:
            svc.google.get(r["person"]).send_email(r["contact_email"], "Re: " + r["subject"], confirm_body.strip()[:3000],
                                                   r["thread_id"] or None)
            appt["confirmed"] = True
        except Exception as e:  # the booking still stands; say the email didn't go
            log.warning("confirmation email failed: %s", e)
            appt["confirm_error"] = str(e)[:200]
    svc.store.update_request(rid, status="booked", appointment=appt)
    owner = svc.google.owner
    svc.store.set_task_meta(owner, r["task_id"], next_action=(f"Appointment at {r['contact_name']}, {appt['label']}" if appointment
                                                             else f"{r['contact_name']} coming {appt['label']}"
                                                             + (f" ({appt['in']} in)" if appt["in"] else "")))
    try:
        g.move_task_due(r["list_name"], r["task_id"], start.date().isoformat())
    except Exception as e:
        log.warning("could not move due date: %s", e)
    if r["contact_id"]:
        svc.store.touch_contact(r["contact_id"])
    svc.store.log_event("trade_booked", f"{r['contact_name']} {appt['label']} by {by}")
    _changed(svc, rid)
    for other in requests_for_task(svc, r["task_id"]):  # went with this firm: stop chasing the rest
        if other["id"] != rid:
            cancel_request(svc, other["id"], f"went with {r['contact_name']}")
    return svc.store.get_request(rid)


def open_loops(svc, now: datetime | None = None, days_ahead: int = 14) -> list[dict]:
    """Everything still in motion with tradespeople and appointments, for the Today screen."""
    now = now or datetime.now(ZoneInfo(svc.settings.tz))
    out = []
    for r in svc.store.requests(("drafted", "sent", "chased", "replied", "booked", "visited")):
        item = {"id": r["id"], "task_id": r["task_id"], "status": r["status"], "who": r["contact_name"], "trade": r["trade"],
                "title": _todo_title(svc, r), "sent_at": r["sent_at"], "thread_id": r["thread_id"]}
        if r["status"] == "booked":
            appt = r.get("appointment") or {}
            try:
                start = datetime.fromisoformat(appt.get("start", ""))
            except ValueError:
                continue
            if not (now <= start <= now + timedelta(days=days_ahead)):
                continue
            item.update(when=appt.get("label", ""), start=appt.get("start"),
                        line=f"{'Appointment at' if r['trade'] in APPOINTMENTS else 'Coming:'} {r['contact_name']}, {appt.get('label', '')}")
        elif r["status"] == "drafted":
            item.update(line=f"Email to {r['contact_name']} is ready: send it from Gmail drafts or here", needs_you=True)
        elif r["status"] == "replied":
            when = (r.get("suggested") or {}).get("label")
            item.update(line=f"{r['contact_name']} replied" + (f": book {when}?" if when else ": agree a time"), needs_you=True)
        elif r["status"] == "visited":
            item.update(line=f"Did {r['contact_name']} sort it? Tick it off, or ask again", needs_you=True)
        elif r["status"] == "chased":
            item.update(line=f"No reply from {r['contact_name']} yet: send a reminder or ask someone else", needs_you=True)
        else:
            item.update(line=f"Waiting for {r['contact_name']} to reply")
        out.append(item)
    order = {"replied": 0, "visited": 1, "drafted": 2, "chased": 3, "booked": 4, "sent": 5}
    return sorted(out, key=lambda i: (order[i["status"]], i.get("start") or i["sent_at"]))


def _todo_title(svc, r: dict) -> str:
    if r.get("task_title"):
        return r["task_title"]
    meta = svc.store.task_meta(svc.google.owner).get((svc.google.owner, r["task_id"]), {})
    return meta.get("original") or r["subject"]


# ---------- time-blocking for jobs you do yourself ----------
def block_time(svc, todo: dict, start_iso: str, duration_min: int, person: str) -> dict:
    tz = ZoneInfo(svc.settings.tz)
    start = datetime.fromisoformat(start_iso)
    start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
    end = start + timedelta(minutes=max(15, duration_min))
    g = svc.google.get(person) if person in svc.google.people else svc.google.get()
    ev = g.add_event(f"{todo['title']}", start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"),
                     f"Time set aside by Life OS. Next: {todo.get('next_action') or 'make a start'}")
    svc.google.get().move_task_due(todo["list"], todo["id"], start.date().isoformat())
    svc.store.log_event("time_blocked", f"{todo['title']} {start.isoformat()} by {person}")
    return {"ok": True, "event_id": ev.get("id", ""), "label": scheduling.label(start, end)}


def visit_clashes(svc, now: datetime | None = None) -> list[str]:
    """Daily: a booked visit where whoever was going to be in is now busy. Says who else is free, if anyone."""
    tz = ZoneInfo(svc.settings.tz)
    now = now or datetime.now(tz)
    told = []
    for r in svc.store.requests(("booked",)):
        a = r.get("appointment") or {}
        if not a.get("in") or not a.get("start"):
            continue
        start, end = datetime.fromisoformat(a["start"]), datetime.fromisoformat(a["end"])
        if start < now or start > now + timedelta(days=14):
            continue
        home = scheduling.who_free(svc, start, end, prefer=a["in"])
        if a["in"] in home["free"]:
            continue
        key = f"clash:{r['id']}:{a['start']}"
        if svc.store.get_state(key, ""):
            continue
        other = f" {home['free'][0]} is free then." if home["free"] else " Nobody's free then: rearrange or ask someone to be in."
        if svc.notifier:
            svc.notifier.push(f"{a['in']} now has something on during {r['contact_name']}'s visit, {a['label']}.{other}",
                              title="Visit clash", priority=4)
        svc.store.set_state(key, "told")
        told.append(r["contact_name"])
    return told
