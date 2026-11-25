"""Smart to-dos and projects.

You say what needs doing in plain words; the AI works out the category, importance, timescale or due date,
how long it takes, the very next action, whether it repeats and who it's for, then files it in the matching
Google Tasks list. Google Tasks stays the place the tasks live (so they show in the Google Tasks app);
Life OS keeps the extra details alongside and sorts everything into Now / This week / This month / Later /
Someday. Projects are their own Google Tasks list ("Project: Hallway") with ordered steps.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from .household import add_months, today_local

log = logging.getLogger("lifeos.todos")

TIMESCALES = ["today", "this_week", "this_month", "later", "someday"]
BUCKET_LABELS = {"now": "Now", "this_week": "This week", "this_month": "This month", "later": "Later",
                 "someday": "Someday"}
PRIORITY_WORDS = {1: "Urgent", 2: "High", 3: "Normal", 4: "Low"}
DEFAULT_CATEGORIES = [
    {"name": "Home maintenance", "about": "repairs, servicing, appliances, boiler, leaks, cleaning jobs, safety checks"},
    {"name": "DIY and projects", "about": "decorating, improvements, building, fitting things, bigger jobs around the house"},
    {"name": "Garden", "about": "lawn, hedges, planting, shed, fences, outdoor furniture"},
    {"name": "Car", "about": "MOT, service, tyres, cleaning, parking permits, car admin"},
    {"name": "Admin and renewals", "about": "bills, banking, insurance, forms, passports, letters, paperwork"},
    {"name": "Health and appointments", "about": "GP, dentist, opticians, prescriptions, vet, haircuts"},
    {"name": "Kids", "about": "school, clubs, uniforms, forms, kids' activities and needs"},
    {"name": "Errands", "about": "returns, post office, collecting or dropping off, buying a specific item"},
    {"name": "Family and social", "about": "birthdays, gifts, cards, visits, plans with friends and family"},
]
SHOPPING = "Shopping"


def categories(settings) -> list[dict]:
    return settings.behaviour.get("todo_categories") or DEFAULT_CATEGORIES


def category_names(settings) -> list[str]:
    return [c["name"] for c in categories(settings)]


# ---------- the AI's sorting decision ----------
TRIAGE_TOOL = {
    "name": "save_triage",
    "description": "Save how this to-do should be filed. Call exactly once.",
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "Short, clear task name in the household's words, e.g. 'Fix dripping bath tap'"},
        "category": {"type": "string", "description": "One of the category names, or 'Shopping' for groceries/household consumables"},
        "priority": {"type": "integer", "description": "1 urgent (safety, damage, hard deadline within days), 2 high, 3 normal, 4 low"},
        "timescale": {"type": "string", "enum": TIMESCALES},
        "due": {"type": "string", "description": "YYYY-MM-DD if a date is stated or clearly implied, else empty"},
        "effort_min": {"type": "integer", "description": "Realistic minutes of hands-on work"},
        "next_action": {"type": "string", "description": "The very first concrete physical step, e.g. 'Find the tap washer size and buy two'"},
        "recurrence": {"type": "string", "description": "'' (one-off), weekly, fortnightly, monthly, quarterly, yearly, or every_N_months"},
        "who": {"type": "string", "description": "Person id or child's name if it's clearly theirs, else 'anyone'"},
        "project": {"type": "string", "description": "Name of an existing project this belongs to, else ''"},
        "is_project": {"type": "boolean", "description": "True if this is really a multi-step project (5+ steps or more than a day's work)"},
        "notes": {"type": "string", "description": "Anything useful to remember, else ''"},
    }, "required": ["title", "category", "priority", "timescale", "effort_min", "next_action"]},
}

PLAN_TOOL = {
    "name": "save_plan",
    "description": "Save the project plan. Call exactly once.",
    "input_schema": {"type": "object", "properties": {
        "category": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string", "description": "A concrete step, starting with a verb"},
            "effort_min": {"type": "integer"},
        }, "required": ["title"]}, "description": "5 to 12 ordered steps from first action to finished"},
    }, "required": ["steps"]},
}


def _context(svc) -> str:
    s = svc.settings
    today = today_local(s)
    cats = "\n".join(f"- {c['name']}: {c['about']}" for c in categories(s))
    names = s.household.get("names") or {}
    people = ", ".join(f"{p} ({names.get(p, p)})" for p in s.household.get("adults") or [])
    projects = ", ".join(p["name"] for p in svc.store.projects()) or "none"
    return (f"Today is {today.strftime('%A %d %B %Y')} ({today.isoformat()}), UK.\n"
            f"Adults (ids): {people}. Children: {', '.join(s.household.get('kids') or []) or 'none'}.\n"
            f"Categories:\n{cats}\n- Shopping: groceries and household consumables\n"
            f"Active projects: {projects}\n")


def _ask_ai_for_tool(svc, agent, tool: dict, system: str, prompt: str, web_search: bool = False) -> dict | None:
    """Runs one small AI request that must answer by calling `tool`. Returns its arguments, or None."""
    if agent is None or getattr(agent, "provider", None) is None:
        return None
    limit = svc.settings.max_runs_per_day
    if limit and hasattr(agent, "runs_today") and agent.runs_today() >= limit:
        return None
    captured: dict = {}

    def on_tool(name, args):
        if name == tool["name"]:
            captured.update(args or {})
            return json.dumps({"ok": True}), False
        return "Only save the result.", True

    svc.store.log_event("agent_start:sort", prompt[:300])  # counts towards the daily AI cap
    try:
        if web_search:
            agent.provider.chat(system, [tool], prompt, on_tool, web_search=True)
        else:
            agent.provider.chat(system, [tool], prompt, on_tool)
    except Exception as e:
        log.warning("AI sorting failed, using rules instead: %s", e)
        return None
    return captured or None


def triage(svc, agent, text: str) -> dict:
    system = ("You sort a UK family's to-dos. Judge what the person didn't say: how important it is, how soon, "
              "how long it takes, the first concrete step, and whether it repeats. Gas, electrics, leaks, damp, "
              "smoke/CO alarms and anything causing damage are priority 1. Prefer a realistic due date when a deadline "
              "is implied (e.g. 'before the school trip on Friday'). Things only bought at the supermarket are "
              "'Shopping'. Then call save_triage.\n\n" + _context(svc))
    result = _ask_ai_for_tool(svc, agent, TRIAGE_TOOL, system, f"To-do: {text}")
    return normalise(svc, result if result else guess(svc, text), text)


def normalise(svc, t: dict, original: str) -> dict:
    cats = category_names(svc.settings)
    cat = str(t.get("category") or "").strip()
    match = next((c for c in cats + [SHOPPING] if c.lower() == cat.lower()), None)
    out = {
        "title": (str(t.get("title") or original).strip() or original)[:200],
        "category": match or (cats[0] if not cat else _closest(cat, cats)),
        "priority": min(4, max(1, int(t.get("priority") or 3))),
        "timescale": t.get("timescale") if t.get("timescale") in TIMESCALES else "this_month",
        "due": "",
        "effort_min": min(60 * 24 * 7, max(5, int(t.get("effort_min") or 30))),
        "next_action": str(t.get("next_action") or "").strip()[:200],
        "recurrence": _recurrence(str(t.get("recurrence") or "")),
        "who": str(t.get("who") or "anyone").strip()[:40] or "anyone",
        "project": str(t.get("project") or "").strip(),
        "is_project": bool(t.get("is_project")),
        "notes": str(t.get("notes") or "").strip()[:500],
    }
    due = str(t.get("due") or "").strip()
    try:
        out["due"] = date.fromisoformat(due).isoformat() if due else ""
    except ValueError:
        out["due"] = ""
    if out["next_action"].lower() == out["title"].lower():
        out["next_action"] = ""
    return out


def _closest(cat: str, cats: list[str]) -> str:
    low = cat.lower()
    for c in cats:
        if any(w in c.lower() for w in low.split() if len(w) > 3):
            return c
    return cats[0]


def _recurrence(r: str) -> str:
    r = r.strip().lower().replace(" ", "_").replace("-", "_")
    if r in ("", "none", "no", "once", "one_off"):
        return ""
    if r in ("weekly", "fortnightly", "monthly", "quarterly", "yearly", "annually", "annual"):
        return "yearly" if r in ("annually", "annual") else r
    m = re.match(r"every_(\d+)_(week|weeks|month|months)", r)
    if m:
        return f"every_{int(m.group(1))}_{'weeks' if m.group(2).startswith('week') else 'months'}"
    return ""


# ---------- rules used when no AI is available (and in the demo) ----------
_KEYWORDS = [
    ("Home maintenance", "boiler leak drip tap toilet blocked drain gutter smoke alarm filter descale fuse bulb "
                         "radiator bleed damp mould extractor oven dishwasher washing machine fridge clean"),
    ("DIY and projects", "paint decorate shelf shelves wallpaper fit install build skirting tile tiles flooring "
                         "hang mirror cupboard wardrobe kitchen bathroom renovate"),
    ("Garden", "garden lawn mow hedge plant plants shed fence weed patio grass compost trim"),
    ("Car", "car mot tyre tyres tire service valet windscreen"),
    ("Admin and renewals", "bill insurance renew renewal passport form forms bank council tax pension letter "
                           "paperwork tax return energy broadband switch"),
    ("Health and appointments", "dentist doctor gp appointment opticians optician prescription vet haircut physio"),
    ("Kids", "school uniform club kit homework nursery parents ava leo"),
    ("Errands", "return post collect drop parcel pick up dry cleaning key cut"),
    ("Family and social", "birthday gift present card anniversary visit party christmas"),
    (SHOPPING, "milk bread eggs butter cheese bananas apples nappies toilet roll washing up liquid bin bags"),
]


def guess(svc, text: str) -> dict:
    low = text.lower()
    words = set(re.findall(r"[a-z]+", low))
    best, score = category_names(svc.settings)[0], 0
    for cat, keys in _KEYWORDS:
        hits = sum(1 for k in keys.split() if (k in words if " " not in k else k in low))
        if hits > score:
            best, score = cat, hits
    today = today_local(svc.settings)
    due, timescale = "", "this_month"
    if "today" in low or "tonight" in low:
        due, timescale = today.isoformat(), "today"
    elif "tomorrow" in low:
        due, timescale = (today + timedelta(days=1)).isoformat(), "this_week"
    elif "weekend" in low:
        due, timescale = (today + timedelta(days=(5 - today.weekday()) % 7)).isoformat(), "this_week"
    elif "this week" in low:
        timescale = "this_week"
    elif "next week" in low:
        due, timescale = (today + timedelta(days=7 - today.weekday())).isoformat(), "this_week"
    elif "someday" in low or "one day" in low or "eventually" in low:
        timescale = "someday"
    urgent = any(w in low for w in ("urgent", "asap", "leak", "smoke alarm", "broken", "gas", "no hot water"))
    recurrence = ""
    for word, rec in (("every week", "weekly"), ("weekly", "weekly"), ("every month", "monthly"), ("monthly", "monthly"),
                      ("every year", "yearly"), ("yearly", "yearly"), ("annual", "yearly"), ("every spring", "yearly"),
                      ("every autumn", "yearly"), ("every summer", "yearly"), ("every winter", "yearly"),
                      ("quarterly", "quarterly")):
        if word in low:
            recurrence = rec
            break
    title = re.sub(r"\b(today|tonight|tomorrow|this weekend|this week|next week|asap|urgently?|every (week|month|year|"
                   r"spring|summer|autumn|winter))\b", "", text, flags=re.I).strip(" ,.-") or text
    big = any(w in low for w in ("renovate", "redecorate", "new kitchen", "new bathroom", "extension", "loft"))
    return {"title": title[:1].upper() + title[1:], "category": best, "priority": 1 if urgent else 3,
            "timescale": "today" if urgent and not due else timescale, "due": due,
            "effort_min": 240 if big else 30, "next_action": "", "recurrence": recurrence, "who": "anyone",
            "is_project": big}


# ---------- adding, listing, completing ----------
def _account(svc, person: str | None = None):
    return svc.google.get(person) if person else svc.google.get()


def file_todo(svc, t: dict, added_by: str | None = None, original: str = "") -> dict:
    """Puts a sorted to-do into the household's Google Tasks and remembers its details."""
    owner = svc.google.owner
    project = next((p for p in svc.store.projects() if p["name"].lower() == t.get("project", "").lower()), None) \
        if t.get("project") else None
    list_name = project["list_name"] if project else t["category"]
    notes = "\n".join(x for x in (f"Next: {t['next_action']}" if t.get("next_action") else "", t.get("notes", "")) if x)
    g = _account(svc)
    if t["category"] == SHOPPING and not project:
        created = g.add_task(SHOPPING, t["title"])
        return {"id": created.get("id"), "list": SHOPPING, "title": t["title"], "category": SHOPPING,
                "duplicate": bool(created.get("duplicate"))}
    created = g.add_task(list_name, t["title"], notes, t.get("due") or None)
    if created.get("duplicate"):
        return {"id": created.get("id"), "list": list_name, "title": t["title"], "category": t["category"],
                "duplicate": True}
    step = None
    if project:
        steps = [m["step"] or 0 for m in svc.store.task_meta(owner).values() if m["project_id"] == project["id"]]
        step = max(steps, default=0) + 1
    svc.store.set_task_meta(owner, created["id"], list_name=list_name, category=t["category"],
                            project_id=project["id"] if project else None, step=step, priority=t["priority"],
                            timescale=t["timescale"], effort_min=t["effort_min"], next_action=t.get("next_action", ""),
                            recurrence=t.get("recurrence", ""), who=t.get("who", "anyone"), original=original[:300])
    svc.store.log_event("todo_added", f"{t['title']} -> {list_name} by {added_by or '?'}")
    return {"id": created["id"], "list": list_name, **t, "project_name": project["name"] if project else ""}


def add_todo(svc, agent, text: str, added_by: str | None = None, judged: dict | None = None, notes: str = "") -> dict:
    """Sort it (or take the assistant's own judgement), file it, and prepare the next step where that helps."""
    t = judged or triage(svc, agent, text.strip()[:500])
    if notes.strip():
        t["notes"] = "\n".join(x for x in (t.get("notes", ""), notes.strip()[:1000]) if x)
    filed = file_todo(svc, t, added_by, original=text)
    # Things to buy, book or give get their suggestions worked out straight away, in the background.
    if (agent is not None and filed.get("id") and filed.get("category") != SHOPPING and not filed.get("duplicate")
            and svc.settings.behaviour.get("auto_solve", True)):
        from . import solver
        if solver.worth_solving(filed):
            solver.solve_in_background(svc, agent, filed)
            filed["solving"] = True
    return filed


def _bucket(meta: dict, due: str, today: date) -> tuple[str, int]:
    """Which section a to-do shows in, and how many days until it's due (or None)."""
    if due:
        days = (date.fromisoformat(due) - today).days
        if days <= 0:
            return "now", days
        if days <= 7:
            return "this_week", days
        if days <= 31:
            return "this_month", days
        return "later", days
    return {"today": "now", "this_week": "this_week", "this_month": "this_month", "later": "later",
            "someday": "someday"}.get(meta.get("timescale") or "this_month", "this_month"), None


def list_todos(svc, include_projects: bool = True) -> list[dict]:
    """Every open household to-do with its details, sorted the way a person would tackle them."""
    if not svc.google:
        return []
    owner = svc.google.owner
    g = _account(svc)
    metas = svc.store.task_meta(owner)
    solutions = svc.store.solutions(owner)
    lists = list(category_names(svc.settings))
    projects = {p["list_name"]: p for p in svc.store.projects()} if include_projects else {}
    today = today_local(svc.settings)
    out = []
    for list_name in lists + list(projects):
        try:
            tasks = g.list_tasks(list_name)
        except Exception as e:
            log.warning("could not read %s: %s", list_name, e)
            continue
        project = projects.get(list_name)
        open_steps = sorted((metas.get((owner, t["id"]), {}).get("step") or 999, t["id"]) for t in tasks) if project else []
        next_step_id = open_steps[0][1] if open_steps else None
        for t in tasks:
            if t.get("status") == "completed":
                continue
            if project and t["id"] != next_step_id:
                continue  # projects show only their next step in the main list
            m = metas.get((owner, t["id"]), {})
            bucket, days = _bucket(m, t.get("due", ""), today)
            out.append({
                "id": t["id"], "list": list_name, "title": t["title"], "due": t.get("due", ""), "days": days,
                "bucket": bucket, "category": m.get("category") or (project["category"] if project else list_name),
                "project": project["name"] if project else "", "project_id": project["id"] if project else None,
                "priority": m.get("priority") or 3, "effort_min": m.get("effort_min") or 30,
                "next_action": m.get("next_action") or "", "recurrence": m.get("recurrence") or "",
                "who": m.get("who") or "anyone", "quick": (m.get("effort_min") or 30) <= 15,
                "solution": solutions.get(t["id"]),
                "request": _request_view(svc, svc.store.request_for_task(t["id"])),
                "quotes": _quotes(svc, t["id"]),
                "added": (m.get("created_at") or "")[:10],
            })
    order = {"now": 0, "this_week": 1, "this_month": 2, "later": 3, "someday": 4}
    out.sort(key=lambda x: (order[x["bucket"]], x["days"] if x["days"] is not None else 99, x["priority"],
                            x["effort_min"]))
    return out


def stalled(svc, days: int = 28, now: date | None = None) -> list[dict]:
    """Jobs that have sat for weeks with nothing booked or under way, each with a suggested way to close it."""
    from . import trades
    today = now or today_local(svc.settings)
    out = []
    for t in list_todos(svc, include_projects=False):
        if not t["added"] or t["bucket"] == "someday" or t["recurrence"] or t["request"]:
            continue
        if (t["due"] and t["due"] >= today.isoformat()) or (today - date.fromisoformat(t["added"])).days < days:
            continue
        trade = trades.detect_trade(t["title"])
        if trade:
            how = f"get {trades.article(trade)} {trade} in: open it and tap Send"
        elif (t["solution"] or {}).get("kind") == "buy":
            how = "it's ready to order"
        elif t["effort_min"] <= 30:
            how = f"a {t['effort_min']}-minute job: tap Find time"
        else:
            how = "still needed? Find time for it, or tick it off"
        out.append({**t, "stalled_days": (today - date.fromisoformat(t["added"])).days, "how": how})
    return sorted(out, key=lambda x: (x["priority"], -x["stalled_days"]))


def stalled_nudge(svc, now: date | None = None) -> str:
    """Sunday evening: a short nudge about up to three stalled jobs (nothing if none)."""
    jobs = stalled(svc, int(svc.settings.behaviour.get("stalled_after_days", 28)), now)
    if not jobs or not svc.notifier:
        return ""
    lines = [f"{j['title']}: {j['how']}" for j in jobs[:3]]
    more = f" (and {len(jobs) - 3} more)" if len(jobs) > 3 else ""
    msg = "Waiting a month or more" + more + ":\n" + "\n".join(lines)
    svc.notifier.push(msg, title="Jobs that have stalled", priority=2)
    return msg


def next_due(recurrence: str, from_day: date) -> date | None:
    if not recurrence:
        return None
    if recurrence == "weekly":
        return from_day + timedelta(weeks=1)
    if recurrence == "fortnightly":
        return from_day + timedelta(weeks=2)
    if recurrence == "monthly":
        return add_months(from_day, 1)
    if recurrence == "quarterly":
        return add_months(from_day, 3)
    if recurrence == "yearly":
        return add_months(from_day, 12)
    m = re.match(r"every_(\d+)_(weeks|months)", recurrence)
    if m:
        n = int(m.group(1))
        return from_day + timedelta(weeks=n) if m.group(2) == "weeks" else add_months(from_day, n)
    return None


def _request_view(svc, r: dict | None) -> dict | None:
    """The request as the app shows it: who to greet, who it's from, whether they're already the go-to."""
    if not r:
        return None
    from . import trades
    contact = svc.store.get_contact(r["contact_id"]) if r.get("contact_id") else None
    return {**r, "greet": trades.greet_for(svc, r), "sender_name": trades.sender_name(svc, r["person"]),
            "contact_favourite": bool(contact and contact["favourite"]), "contact_id": r.get("contact_id")}


# ---------- changing a to-do: snooze, edit, delete ----------
def _find_open(svc, list_name: str, task_id: str) -> dict:
    current = next((t for t in _account(svc).list_tasks(list_name) if t["id"] == task_id), None)
    if not current:
        raise ValueError("That to-do has already been ticked off or removed.")
    return current


def snooze_date(until: str, today: date) -> date:
    """'tomorrow', 'weekend' (the coming Saturday), 'next_week' (the coming Monday) or a YYYY-MM-DD date."""
    if until == "tomorrow":
        return today + timedelta(days=1)
    if until == "weekend":
        return today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    if until == "next_week":
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    d = date.fromisoformat(until)
    if d < today:
        raise ValueError("Pick a day from today onwards.")
    return d


def snooze_todo(svc, list_name: str, task_id: str, until: str, by: str | None = None) -> dict:
    _find_open(svc, list_name, task_id)
    d = snooze_date(until, today_local(svc.settings))
    _account(svc).move_task_due(list_name, task_id, d.isoformat())
    svc.store.log_event("todo_snoozed", f"{list_name}/{task_id} to {d} by {by or '?'}")
    return {"ok": True, "due": d.isoformat()}


def edit_todo(svc, list_name: str, task_id: str, by: str | None = None, title: str | None = None,
              due: str | None = None, category: str | None = None, who: str | None = None,
              priority: int | None = None, next_action: str | None = None) -> dict:
    """Change what the person changed; moving to another list keeps its details, plan and requests."""
    owner, g = svc.google.owner, _account(svc)
    current = _find_open(svc, list_name, task_id)
    title = (title or "").strip()[:200] or None
    if due:
        date.fromisoformat(due)  # a clear error for a bad date
    if title or due is not None:
        g.update_task(list_name, task_id, title=title, due=due)
    fields = {k: v for k, v in (("who", who), ("priority", max(1, min(4, int(priority))) if priority else None),
                                 ("next_action", next_action)) if v is not None}
    if fields:
        svc.store.set_task_meta(owner, task_id, **fields)
    new_id, new_list = task_id, list_name
    projects = {p["list_name"] for p in svc.store.projects()}
    if category and category != list_name and category in category_names(svc.settings) and list_name not in projects:
        moved = g.add_task(category, title or current["title"], current.get("notes", ""),
                           (due if due is not None else current.get("due")) or None)
        g.delete_task(list_name, task_id)
        new_id, new_list = moved["id"], category
        svc.store.move_task_refs(owner, task_id, new_id, new_list)
        svc.store.set_task_meta(owner, new_id, category=category, list_name=category)
    svc.store.log_event("todo_edited", f"{new_list}/{new_id} by {by or '?'}")
    from . import bridge
    bridge.write_back_quietly(svc, new_id)
    return {"ok": True, "id": new_id, "list": new_list}


def delete_todo(svc, list_name: str, task_id: str, by: str | None = None) -> dict:
    """Not needed after all: gone from Google Tasks, drafts and Waiting on tidied, nothing comes back."""
    from . import trades
    _account(svc).delete_task(list_name, task_id)
    svc.store.mark_task_done(svc.google.owner, task_id)
    for r in trades.requests_for_task(svc, task_id):
        trades.cancel_request(svc, r["id"], "to-do deleted")
    svc.store.log_event("todo_deleted", f"{list_name}/{task_id} by {by or '?'}")
    return {"ok": True}


ACTION_WORDS = {"arrange": "Send the email", "buy": "Choose and buy", "question": "Answer a question",
                "do": "See the steps", "book": "See how to book", "info": "Open"}


def next_up(svc, limit: int = 3) -> list[dict]:
    """The few jobs worth doing next: due or this week, not waiting on someone else, most important first."""
    out = []
    for t in list_todos(svc):
        if t["bucket"] not in ("now", "this_week"):
            continue
        r = t["request"]
        if r and r["status"] not in ("drafted", "replied", "visited"):
            continue  # waiting on them: that's on the Waiting on card
        sol = t["solution"] or {}
        if r and r["status"] == "replied":
            action = "Book the time"
        elif r and r["status"] == "visited":
            action = "Did they sort it?"
        else:
            action = ACTION_WORDS.get(sol.get("kind") if sol.get("status") == "ready" else "", "Open")
        out.append({"id": t["id"], "list": t["list"], "title": t["title"], "next_action": t["next_action"],
                    "due": t["due"], "days": t["days"], "priority": t["priority"], "quick": t["quick"], "action": action})
        if len(out) >= limit:
            break
    return out


def plan_week(svc, now=None) -> dict:
    """Free evening and weekend slots this week for the jobs you'll do yourself, none overlapping."""
    from datetime import datetime as _dt

    from . import scheduling, trades
    chosen, plan, skipped = [], [], []
    candidates = [t for t in list_todos(svc) if t["bucket"] in ("now", "this_week") and not t["request"]
                  and not trades.detect_trade(t["title"])
                  and (t["solution"] or {}).get("kind") not in ("buy", "arrange", "question")]
    for t in candidates[:8]:
        minutes = max(15, min(240, int(t["effort_min"] or 30)))
        slots = scheduling.free_slots(svc, minutes, count=20, notice_days=0, horizon_days=7, hours=scheduling.DIY_HOURS,
                                      one_per_day=False, now=now)
        free = [s for s in slots if all(_dt.fromisoformat(s["end"]) <= _dt.fromisoformat(c["start"])
                                        or _dt.fromisoformat(s["start"]) >= _dt.fromisoformat(c["end"]) for c in chosen)]
        # spread the jobs out: an evening with nothing planned yet comes first
        free.sort(key=lambda s: (sum(c["start"][:10] == s["start"][:10] for c in chosen), s["start"]))
        pick = free[0] if free else None
        if not pick:
            skipped.append(t["title"])
            continue
        chosen.append(pick)
        plan.append({"task_id": t["id"], "list": t["list"], "title": t["title"], "next_action": t["next_action"],
                     "duration_min": minutes, "start": pick["start"], "end": pick["end"], "label": pick["label"]})
    return {"plan": sorted(plan, key=lambda p: p["start"]), "skipped": skipped}


def _quotes(svc, task_id: str) -> list[dict]:
    """When several firms were asked: each one's state, so the replies can be compared side by side."""
    from . import trades
    rs = trades.requests_for_task(svc, task_id)
    return [_request_view(svc, r) for r in rs] if len(rs) > 1 else []


def complete_todo(svc, list_name: str, task_id: str, by: str | None = None) -> dict:
    """Ticks a to-do off. A repeating one comes straight back with its next date."""
    owner = svc.google.owner
    g = _account(svc)
    meta = svc.store.task_meta(owner).get((owner, task_id), {})
    current = next((t for t in g.list_tasks(list_name) if t["id"] == task_id), None)
    g.complete_task(list_name, task_id)
    svc.store.log_event("todo_done", f"{list_name}/{task_id} by {by or '?'}")
    return after_done(svc, list_name, task_id, meta, current, by)


def after_done(svc, list_name: str, task_id: str, meta: dict, current: dict | None, by: str | None = None) -> dict:
    """What follows a tick, wherever it happened (Life OS, Gemini, the Google Tasks app):
    a repeating job comes back, tradesperson tracking closes, the project moves on."""
    from . import trades
    owner = svc.google.owner
    g = _account(svc)
    svc.store.mark_task_done(owner, task_id)
    trades.close_for_task(svc, task_id)
    title = (current or {}).get("title") or meta.get("original") or ""
    if title:  # remembered, so Life OS learns how often jobs like this come round
        from . import activity
        activity.record_done(svc, title, meta.get("category") or list_name, by or "",
                             "google_tasks" if by == "Google Tasks" else "app", ref=f"task:{task_id}")
    result = {"ok": True}
    rec = meta.get("recurrence") or ""
    if rec and current:
        today = today_local(svc.settings)
        base = max(today, date.fromisoformat(current["due"])) if current.get("due") else today
        nd = next_due(rec, base)
        if nd:
            notes = f"Next: {meta['next_action']}" if meta.get("next_action") else ""
            new = g.add_task(list_name, current["title"], notes, nd.isoformat())
            if not new.get("duplicate"):
                svc.store.set_task_meta(owner, new["id"], **{k: meta[k] for k in svc.store.META_FIELDS if k in meta})
            result["next_due"] = nd.isoformat()
    if meta.get("project_id"):
        result["project"] = project_summary(svc, svc.store.get_project(meta["project_id"]))
    return result


# ---------- projects ----------
GENERIC_STEPS = ["Decide exactly what you want and set a budget", "Measure up and list what's needed",
                 "Get quotes or buy materials", "Book the time (or the tradesperson)", "Do the work",
                 "Tidy up and check it's finished"]


def start_project(svc, agent, name: str, goal: str = "", by: str | None = None) -> dict:
    name = name.strip()[:80]
    existing = next((p for p in svc.store.projects() if p["name"].lower() == name.lower()), None)
    if existing:
        return project_summary(svc, existing)
    system = ("You plan household projects for a UK family. Break the project into 5-12 concrete, ordered steps "
              "from the very first action to finished, each something one person can do in one go. Include "
              "decisions, measuring, quotes or buying, booking trades, the work, and tidying up. Then call save_plan."
              "\n\n" + _context(svc))
    plan = _ask_ai_for_tool(svc, agent, PLAN_TOOL, system, f"Project: {name}\nDetails: {goal or 'none given'}")
    steps = [s for s in (plan or {}).get("steps", []) if str(s.get("title", "")).strip()][:15]
    if not steps:
        steps = [{"title": s, "effort_min": 60} for s in GENERIC_STEPS]
    cats = category_names(svc.settings)
    category = (plan or {}).get("category") or guess(svc, f"{name} {goal}")["category"]
    category = next((c for c in cats if c.lower() == str(category).lower()), "DIY and projects" if "DIY and projects" in cats else cats[0])
    owner = svc.google.owner
    list_name = f"Project: {name}"[:100]
    pid = svc.store.add_project(name, list_name, owner, goal[:500], category)
    g = _account(svc)
    # Google Tasks shows the newest first, so add the steps last-to-first to keep them in order there too.
    for i, step in reversed(list(enumerate(steps, start=1))):
        t = g.add_task(list_name, str(step["title"]).strip()[:200])
        svc.store.set_task_meta(owner, t["id"], list_name=list_name, category=category, project_id=pid, step=i,
                                priority=3, timescale="this_week" if i == 1 else "later",
                                effort_min=int(step.get("effort_min") or 60), who="anyone")
    svc.store.log_event("project_started", f"{name} ({len(steps)} steps) by {by or '?'}")
    return project_summary(svc, svc.store.get_project(pid))


def project_summary(svc, project: dict | None) -> dict | None:
    if not project:
        return None
    owner = project["person"]
    g = svc.google.get(owner)
    metas = svc.store.task_meta(owner)
    try:
        tasks = g.list_tasks(project["list_name"], include_completed=True)
    except Exception as e:
        log.warning("project %s unreadable: %s", project["name"], e)
        tasks = []
    steps = sorted(({"id": t["id"], "title": t["title"], "done": t.get("status") == "completed",
                     "step": metas.get((owner, t["id"]), {}).get("step") or 999,
                     "effort_min": metas.get((owner, t["id"]), {}).get("effort_min") or 60} for t in tasks),
                   key=lambda s: s["step"])
    done = sum(1 for s in steps if s["done"])
    nxt = next((s for s in steps if not s["done"]), None)
    if steps and not nxt and project["status"] == "active":
        svc.store.set_project_status(project["id"], "done")
        project = {**project, "status": "done"}
    return {"id": project["id"], "name": project["name"], "goal": project["goal"], "category": project["category"],
            "list": project["list_name"], "status": project["status"], "total": len(steps), "done": done,
            "next": nxt, "steps": steps}


def all_projects(svc, include_done: bool = False) -> list[dict]:
    items = svc.store.projects(None if include_done else "active")
    return [s for s in (project_summary(svc, p) for p in items) if s]


# ---------- Inbox sweep ----------
def sweep_inbox(svc, agent, limit: int = 10) -> list[str]:
    """Anything typed straight into a Google Tasks 'Inbox' list (either person's) gets sorted and filed."""
    if not svc.google:
        return []
    filed = []
    for person, g in list(svc.google.people.items()):
        try:
            items = [t for t in g.list_tasks("Inbox") if t.get("status") != "completed"]
        except Exception as e:
            log.warning("inbox for %s unreadable: %s", person, e)
            continue
        for t in items[:limit]:
            text = t["title"] + (f" ({t['notes']})" if t.get("notes") else "")
            try:
                r = add_todo(svc, agent, text, added_by=person)
                g.delete_task("Inbox", t["id"])
                filed.append(f"{r['title']} -> {r['list']}")
            except Exception as e:
                log.warning("could not file %s: %s", t["title"], e)
    return filed
