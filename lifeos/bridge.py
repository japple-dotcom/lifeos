"""Life OS through Gemini and the Google apps, without opening Life OS.

Gemini (in the UK) can't call Life OS directly yet, but it reads and writes Google Tasks, Gmail and Calendar. So
those are the shared surface:

- Voice inbox: to-dos Gemini adds to someone's main Google Tasks list ("add the kitchen socket to my to-dos")
  are picked up every few minutes, sorted into the household lists and given their next step. Reminders with
  a date are left where they are. On first run, whatever is already in that list is left alone.
- Ticks count wherever they happen: a to-do ticked off in Gemini or the Google Tasks app gets the same
  follow-ups as in Life OS (repeating jobs come back, tradesperson tracking closes).
- Waiting on: each tradesperson request has a plainly worded task in that person's "Waiting on" list, kept up
  to date, which Gemini can read out. Ticking it acts: "... replied. Tick to book Thursday 8am" books that time
  (calendar only, nothing is sent); "Did they sort it? Tick when done" ticks off the job.

Settings (config/settings.yaml, `gemini:`): pick_up_todos, people, leave_dated, waiting_on_tasks.
"""
from __future__ import annotations

import logging
import re
import json
from datetime import datetime, timedelta, timezone

from .integrations.google import DEFAULT_LIST

log = logging.getLogger("lifeos.bridge")

WAITING_LIST = "Waiting on"
MARK = "Kept up to date by Life OS."
ACTIVE = ("drafted", "sent", "chased", "replied", "booked", "visited")


def _cfg(svc) -> dict:
    return svc.settings.behaviour.get("gemini") or {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- voice inbox ----------
def sweep_voice(svc, agent, limit: int = 10) -> list[str]:
    """New undated to-dos in each person's main Google Tasks list: sort, file, prepare the next step."""
    cfg = _cfg(svc)
    if not cfg.get("pick_up_todos", True) or not svc.google:
        return []
    from . import todos
    people = cfg.get("people") or list(svc.google.people)
    filed = []
    for person in people:
        if person not in svc.google.people:
            continue
        g = svc.google.get(person)
        try:
            items = [t for t in g.list_tasks(DEFAULT_LIST) if t.get("status") != "completed"]
        except Exception as e:
            log.warning("main task list for %s unreadable: %s", person, e)
            continue
        baseline = f"voice_baseline:{person}"
        if not svc.store.get_state(baseline):  # first run: what's already there stays theirs
            svc.store.mark_seen(person, [t["id"] for t in items])
            svc.store.set_state(baseline, _utc_now().isoformat())
            continue
        for t in [t for t in items if not svc.store.is_seen(person, t["id"])][:limit]:
            if not t["title"].strip() or (t.get("due") and cfg.get("leave_dated", True)):
                svc.store.mark_seen(person, [t["id"]])  # a dated reminder: leave it with them
                continue
            text = t["title"] + (f" ({t['notes']})" if t.get("notes") else "")
            from . import voice
            item = voice.shopping_item(svc, t["title"])
            if item:  # "buy loo roll", "we're out of milk": that's shopping, not a job
                try:
                    svc.google.get().add_task("Shopping", item)
                    g.delete_task(DEFAULT_LIST, t["id"])
                except Exception as e:
                    log.warning("could not move '%s' to Shopping: %s", t["title"], e)
                    continue
                svc.store.mark_seen(person, [t["id"]])
                filed.append(f"{item} -> Shopping")
                if svc.notifier:
                    svc.notifier.push(f"Added to Shopping: {item}", title="Added from Gemini", priority=2)
                continue
            try:
                r = todos.add_todo(svc, agent, text, added_by=person)
                g.delete_task(DEFAULT_LIST, t["id"])
            except Exception as e:  # tried again next sweep
                log.warning("could not pick up '%s': %s", t["title"], e)
                continue
            svc.store.mark_seen(person, [t["id"]])
            filed.append(f"{r['title']} -> {r['list']}")
            if svc.notifier:
                nxt = r.get("next_action") or ""
                more = " Working out the next step." if r.get("solving") else (f" Next: {nxt}." if nxt else "")
                from .trades import open_job
                svc.notifier.push(f"Got it: {r['title']} ({r['list']}).{more}", title="Added from Gemini", priority=2,
                                  actions=open_job(svc, r["id"]) if r.get("id") else None)
    return filed


# ---------- ticks made outside Life OS ----------
def sync_ticks(svc, now: datetime | None = None) -> list[str]:
    """To-dos ticked off or deleted in Gemini / the Google Tasks app, and ticks on the Waiting on copies."""
    if not svc.google:
        return []
    from . import todos, trades
    now = now or _utc_now()
    since = svc.store.get_state("ticks_since") or (now - timedelta(days=3)).isoformat()
    owner = svc.google.owner
    did, ok = [], True
    by_list: dict[str, list[dict]] = {}
    for m in svc.store.open_task_meta(owner):
        by_list.setdefault(m["list_name"], []).append(m)
    g = svc.google.get()
    for list_name, metas in by_list.items():
        try:
            changed = {t["id"]: t for t in g.list_changed(list_name, since)}
        except Exception as e:
            log.warning("could not check '%s' for ticks: %s", list_name, e)
            ok = False
            continue
        for m in metas:
            t = changed.get(m["task_id"])
            if not t:
                continue
            if t.get("deleted"):
                svc.store.mark_task_done(owner, m["task_id"])
                trades.close_for_task(svc, m["task_id"])
                did.append(f"deleted:{t['title']}")
            elif t.get("status") == "completed":
                todos.after_done(svc, list_name, m["task_id"], m, t, by="Google Tasks")
                svc.store.log_event("todo_done", f"{list_name}/{m['task_id']} in Google Tasks")
                did.append(f"ticked:{t['title']}")
    # Shopping ticked off in Gemini or the Google Tasks app: remembered as bought, for predictions
    try:
        from . import activity
        for t in g.list_changed("Shopping", since):
            if t.get("status") == "completed" and not t.get("deleted") and t.get("title"):
                activity.record_bought(svc, t["title"], "", "google_tasks", ref=f"task:{t['id']}")
    except Exception as e:
        log.debug("shopping list changes unreadable: %s", e)
    # Ticks on Life OS's own Waiting on copies, in whichever account holds them
    mirrors: dict[str, dict[str, dict]] = {}
    for r in svc.store.requests(ACTIVE):
        if r.get("gtask_id"):
            mirrors.setdefault(r["person"], {})[r["gtask_id"]] = r
    for person, by_id in mirrors.items():
        if person not in svc.google.people:
            continue
        try:
            changed = svc.google.get(person).list_changed(WAITING_LIST, since)
        except Exception as e:
            log.warning("could not check Waiting on for %s: %s", person, e)
            ok = False
            continue
        for t in changed:
            r = by_id.get(t["id"])
            if r and (t.get("deleted") or t.get("status") == "completed"):
                did.append(_act_on_tick(svc, r, deleted=bool(t.get("deleted"))))
    if ok:  # a little overlap, so nothing slips between two checks
        svc.store.set_state("ticks_since", (now - timedelta(minutes=2)).isoformat())
    return did


def _act_on_tick(svc, r: dict, deleted: bool) -> str:
    from . import todos, trades
    svc.store.update_request(r["id"], gtask_id="")
    if deleted:  # they removed the reminder; the request itself carries on
        return f"mirror-removed:{r['id']}"
    status = r["status"]
    if status == "replied":
        s = r.get("suggested")
        if s:
            b = trades.book(svc, r["id"], s["start"], int(s.get("duration_min") or 60), by="Google Tasks")
            if svc.notifier:
                svc.notifier.push(f"Booked {r['contact_name']}: {b['appointment']['label']}. It's in the calendar.",
                                  title="Booked", actions=trades.open_job(svc, r["task_id"]))
            return f"booked:{r['id']}"
        mirror_request(svc, r["id"])  # no time to book: put it back, asking for a time
        return f"needs-time:{r['id']}"
    if status == "visited":
        todos.complete_todo(svc, r["list_name"], r["task_id"], by="Google Tasks")
        return f"done:{r['id']}"
    if status == "booked":
        return f"noted:{r['id']}"
    # drafted / sent / chased ticked: they've sorted it another way. Their Gmail draft, if any, stays put.
    svc.store.update_request(r["id"], draft_id="", reminder_draft_id="")
    trades.cancel_request(svc, r["id"], "ticked off in Google Tasks")
    if svc.notifier:
        svc.notifier.push(f"Stopped waiting on {r['contact_name']} for {r.get('task_title') or 'that job'}.",
                          title="Waiting on", priority=2)
    return f"stopped:{r['id']}"


# ---------- Waiting on copies ----------
def _short(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", name).strip() or name


def mirror_text(r: dict) -> tuple[str, str, str]:
    """Title (what Gemini reads out), notes and due date for a request's Waiting on task."""
    who, job, st = _short(r["contact_name"]), (r.get("task_title") or r.get("subject") or "the job"), r["status"]
    appt, sug = r.get("appointment") or {}, r.get("suggested") or {}
    due, extra = "", ""
    if st == "drafted":
        title = f"Send email to {who} (in Gmail drafts): {job}"
        extra = "The email is in your Gmail drafts. Once it's sent, Life OS watches for the reply."
    elif st == "sent":
        title = f"Waiting for {who} to reply: {job}"
    elif st == "chased":
        title = f"Chase {who}, no reply yet{' (reminder in Gmail drafts)' if r.get('reminder_draft_id') else ''}: {job}"
    elif st == "replied" and sug:
        title = f"{who} replied. Tick to book {sug['label']}: {job}"
        extra = f"They said: \"{r.get('reply_snippet', '')}\"\nTicking this puts that time in the calendar. Nothing is sent."
    elif st == "replied":
        title = f"{who} replied, agree a time in Life OS: {job}"
        extra = f"They said: \"{r.get('reply_snippet', '')}\""
    elif st == "booked":
        title = f"{who} booked for {appt.get('label', 'the agreed time')}: {job}"
        due = (appt.get("start") or "")[:10]
    else:  # visited
        title = f"Did {who} sort it? Tick when done: {job}"
    notes = "\n".join(x for x in (MARK, extra, f"Email: {r['contact_email']}" if r.get("contact_email") else "") if x)
    return title[:250], notes, due


def mirror_request(svc, rid: int) -> None:
    """Create, update or remove the request's task in the sender's Google Tasks 'Waiting on' list."""
    if not _cfg(svc).get("waiting_on_tasks", True) or not svc.google:
        return
    r = svc.store.get_request(rid)
    if not r or r["person"] not in svc.google.people:
        return
    g = svc.google.get(r["person"])
    if r["status"] not in ACTIVE:
        if r.get("gtask_id"):
            try:
                g.delete_task(WAITING_LIST, r["gtask_id"])
            except Exception as e:
                log.debug("waiting-on task already gone: %s", e)
            svc.store.update_request(rid, gtask_id="")
        return
    title, notes, due = mirror_text(r)
    if r.get("gtask_id"):
        try:
            g.update_task(WAITING_LIST, r["gtask_id"], title=title, notes=notes, due=due)
            return
        except Exception as e:  # deleted by hand: make a fresh one
            log.debug("waiting-on task %s not updated: %s", r["gtask_id"], e)
    created = g.add_task(WAITING_LIST, title, notes, due or None)
    svc.store.update_request(rid, gtask_id=created.get("id", ""))


def mirror_ids(svc) -> set[str]:
    """Ids of Life OS's own Waiting on copies (so Today doesn't list them twice)."""
    return {r["gtask_id"] for r in svc.store.requests(ACTIVE) if r.get("gtask_id")}


def run(svc, agent) -> dict:
    """The 5-minute job: pick up Gemini to-dos, notice Gmail drafts sent, and act on ticks."""
    from . import trades
    out, errors = {"picked_up": [], "drafts": [], "ticks": []}, []
    for key, fn in (("picked_up", lambda: sweep_voice(svc, agent)), ("drafts", lambda: trades.check_drafts(svc)),
                    ("ticks", lambda: sync_ticks(svc))):
        try:
            out[key] = fn()
        except Exception as e:
            log.exception("bridge step %s failed", key)
            errors.append(f"{key}: {str(e)[:120]}")
    _remember_run(svc, out, errors)
    return out


DESCRIBE = {"booked": "Booked from a tick", "done": "Job ticked off", "sent": "Email sent from Gmail drafts",
            "reminded": "Reminder sent from Gmail drafts", "binned": "Gmail draft deleted: stopped tracking",
            "stopped": "Stopped waiting (ticked off)", "ticked": "Ticked off in Google Tasks",
            "deleted": "Deleted in Google Tasks"}


def _remember_run(svc, out: dict, errors: list[str]) -> None:
    """What the last check did, for Settings: so you can see the Gemini link is working."""
    now = _utc_now().isoformat()
    recent = json.loads(svc.store.get_state("bridge_recent", "[]") or "[]")
    for item in out["picked_up"]:
        recent.append({"at": now, "text": f"Added from Gemini: {item.replace(' -> ', ' (') + ')'}"})
    for item in out["drafts"] + out["ticks"]:
        kind, _, rest = str(item).partition(":")
        if kind in DESCRIBE:
            recent.append({"at": now, "text": DESCRIBE[kind] + (f": {rest}" if kind in ("ticked", "deleted") else "")})
    svc.store.set_state("bridge_recent", json.dumps(recent[-10:]))
    svc.store.set_state("bridge_last", json.dumps({"at": now, "errors": errors,
                                                   "counts": {k: len(v) for k, v in out.items()}}))


def status(svc) -> dict:
    cfg = _cfg(svc)
    last = json.loads(svc.store.get_state("bridge_last", "{}") or "{}")
    return {"pick_up_todos": cfg.get("pick_up_todos", True), "waiting_on_tasks": cfg.get("waiting_on_tasks", True),
            "notes_in_tasks": cfg.get("notes_in_tasks", True),
            "drafts": svc.settings.behaviour.get("draft_trade_emails", True), "connected": bool(svc.google),
            "last": last, "recent": list(reversed(json.loads(svc.store.get_state("bridge_recent", "[]") or "[]")))}


# ---------- next steps written into Google Tasks ----------
BLOCK = "--- Life OS ---"


def life_os_block(meta: dict, sol: dict | None, req: dict | None) -> str:
    """The part of a task's notes Life OS keeps up to date: next step, suggestion, links, who to call."""
    lines = []
    if meta.get("next_action"):
        lines.append(f"Next: {meta['next_action']}")
    if req and req.get("status") in ACTIVE and not meta.get("next_action"):
        lines.append(mirror_text(req)[0].rsplit(": ", 1)[0])  # e.g. "Waiting for Spark Electrical to reply"
    if sol and sol.get("status") == "ready":
        if sol.get("summary"):
            lines.append(sol["summary"])
        kind = sol.get("kind")
        if kind == "buy":
            for o in (sol.get("options") or [])[:3]:
                price = f" (about £{o['price_gbp']:.2f})" if o.get("price_gbp") else ""
                lines.append(f"Buy: {o['title']}{price}: {o.get('basket_url') or o.get('url') or o.get('search_url', '')}")
            if sol.get("options"):
                lines.append("(The link opens Amazon with it in the basket; you press Buy.)")
        elif kind == "arrange":
            for c in (sol.get("contacts") or [])[:2]:
                how = " · ".join(x for x in (c.get("phone"), c.get("email"), c.get("website")) if x)
                lines.append(f"Ask: {c['name']}{' (go-to)' if c.get('favourite') else ''}{': ' + how if how else ''}")
            if sol.get("search_url") and not sol.get("contacts"):
                lines.append(f"Find one: {sol['search_url']}")
        elif kind in ("do", "book"):
            lines += [f"{i}. {s}" for i, s in enumerate((sol.get("steps") or [])[:6], 1)]
        elif kind == "question" and sol.get("question"):
            lines.append(f"Question: {sol['question']['text']} (answer in Life OS)")
        lines += [f"{lk['label']}: {lk['url']}" for lk in (sol.get("links") or [])[:3]]
    return "\n".join([BLOCK, *lines]) if lines else ""


def merge_notes(existing: str, block: str) -> str:
    """Keep the person's own notes; replace Life OS's section (and the old 'Next:' line it supersedes)."""
    own = (existing or "").split(BLOCK)[0].rstrip()
    if block:
        own = "\n".join(line for line in own.splitlines() if not line.startswith("Next: ")).rstrip()
    return "\n\n".join(x for x in (own, block) if x)[:8000]


def write_back(svc, task_id: str) -> bool:
    """Bring the task's notes in Google Tasks in line with what Life OS knows (Gemini and the Tasks app show it)."""
    if not _cfg(svc).get("notes_in_tasks", True) or not svc.google:
        return False
    owner = svc.google.owner
    meta = svc.store.task_meta(owner).get((owner, task_id))
    if not meta or meta.get("done_at") or not meta.get("list_name"):
        return False
    g = svc.google.get()
    current = next((t for t in g.list_tasks(meta["list_name"]) if t["id"] == task_id), None)
    if not current:
        return False
    sol = svc.store.solutions(owner).get(task_id)
    notes = merge_notes(current.get("notes", ""), life_os_block(meta, sol, svc.store.request_for_task(task_id)))
    if notes.strip() == (current.get("notes") or "").strip():
        return False
    g.update_task(meta["list_name"], task_id, notes=notes)
    return True


def write_back_quietly(svc, task_id: str) -> None:
    try:
        write_back(svc, task_id)
    except Exception as e:  # the notes are a convenience; never break the real work
        log.warning("could not update notes for task %s: %s", task_id, e)
