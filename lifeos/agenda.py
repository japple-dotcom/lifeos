"""The Sunday agenda: one page for the two of you, with the week ahead and the decisions to make.

Built from what Life OS already knows (calendars, money, school run, childcare, to-dos, trips, parties, the kids),
so it needs no AI. To-do > Week > Sunday agenda shows it (with Print), and a notification at 6:50pm on Sundays says
it's ready. Each section copes on its own: one part failing never stops the rest.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger("lifeos.agenda")


def _section(title: str, fn) -> dict | None:
    try:
        items = [i for i in fn() if i]
    except Exception as e:  # one broken source shouldn't spoil the page
        log.warning("agenda %s: %s", title, e)
        return None
    return {"title": title, "items": items} if items else None


def _i(text: str, link: str = "", strong: bool = False) -> dict:
    return {"text": text, "link": link, "strong": strong}


def build(svc, today: date | None = None) -> dict:
    from . import household
    today = today or household.today_local(svc.settings)
    start = today + timedelta(days=1) if today.weekday() == 6 else today  # on Sunday: Monday onwards
    end = start + timedelta(days=6)
    sections = [
        _section("Decisions to make", lambda: decisions(svc, today)),
        _section("The week ahead", lambda: week_ahead(svc, start, end)),
        _section("Who's doing what", lambda: who_does(svc, start)),
        _section("Money", lambda: money_items(svc, start, end, today)),
        _section("Overdue and stuck", lambda: stuck(svc, today)),
        _section("The kids", lambda: kids(svc)),
        _section("Coming up later", lambda: later(svc, end, today)),
    ]
    sections = [s for s in sections if s]
    count = next((len(s["items"]) for s in sections if s["title"] == "Decisions to make"), 0)
    return {"title": f"Week of {start.strftime('%-d %B')}", "made": datetime.now().strftime("%a %-d %b, %H:%M"),
            "sections": sections, "decisions": count}


def decisions(svc, today: date) -> list[dict]:
    from . import adminhour, trades
    out = []
    pend = svc.store.list_pending()
    if pend:
        out.append(_i(f"{len(pend)} thing{'s' if len(pend) != 1 else ''} waiting for an OK", "#today", True))
    for loop in trades.open_loops(svc)[:6] if svc.google else []:
        if loop.get("needs_you"):
            out.append(_i(loop["line"], f"#tasks/{loop['task_id']}" if loop.get("task_id") else "#today"))
    from . import money
    for s in money.subscriptions(svc, today)["items"]:
        if s.get("nudged_for") == s.get("next_date") and s.get("status") not in ("keep", "cancelling"):
            out.append(_i(f"Keep or cancel {s['service']}? (renews {date.fromisoformat(s['next_date']).strftime('%a %-d %b')})",
                          "#tasks?sec=money"))
    for b in _bills(svc):
        if b.get("recent_rise"):
            out.append(_i(f"{b['provider']} went up {b['rise']['pct']}%: haggle or switch?", "#tasks?sec=money"))
    try:
        p = adminhour.plan(svc, today) if svc.google else {"jobs": []}
        if p["jobs"] and not p.get("booked"):
            out.append(_i(f"Book the life admin hour ({p['slot']['label']})" if p.get("slot") else "Find an hour for life admin",
                          "#tasks?sec=week"))
    except Exception:
        pass
    from . import party
    for pt in party.parties(svc, today):
        if 0 <= pt["days_to_go"] <= 42 and pt["no_reply"]:
            out.append(_i(f"{pt['name']}: {pt['no_reply']} invite{'s' if pt['no_reply'] != 1 else ''} not answered yet",
                          "#tasks?sec=projects"))
    return out


def week_ahead(svc, start: date, end: date) -> list[dict]:
    if not svc.google:
        return []
    days = (end - date.today()).days + 1
    evs = [e for e in svc.google.events_all(days=max(days, 1)) if start.isoformat() <= e.get("start", "")[:10] <= end.isoformat()]
    out = []
    for e in sorted(evs, key=lambda e: e.get("start", ""))[:20]:
        s = e.get("start", "")
        d = date.fromisoformat(s[:10]).strftime("%a")
        when = f"{d} {s[11:16]}" if len(s) > 10 else f"{d} (all day)"
        who = ", ".join(e.get("who") or [])
        out.append(_i(f"{when}: {e.get('summary') or '(no title)'}" + (f" ({who})" if who else "")))
    from . import household
    meals = [m for m in household.week_meals(svc, start, 7) if not m["dinner"]]
    if meals:
        out.append(_i(f"{len(meals)} dinner{'s' if len(meals) != 1 else ''} not planned yet", "#tasks?sec=meals"))
    return out


def who_does(svc, start: date) -> list[dict]:
    from . import childcare, schoolrun
    out = []
    gaps = schoolrun.gaps(svc, start) if svc.google else []
    for g in gaps[:5]:
        out.append(_i(f"School run with nobody free: {g}", "#tasks?sec=week", True))
    for grp in childcare.grouped(childcare.gaps(svc, days=21))[:3] if svc.google else []:
        out.append(_i(f"Childcare needed: {grp['label']} ({grp['closed']})", "#tasks?sec=week", True))
    if svc.google and not gaps:
        out.append(_i("School runs: everyone's covered" if svc.settings.household.get("kids") else ""))
    return out


def _bills(svc) -> list[dict]:
    from . import bills
    return bills.bills(svc)["items"]


def money_items(svc, start: date, end: date, today: date) -> list[dict]:
    from . import money
    out = []
    due = [b for b in _bills(svc) if b.get("next_date") and start.isoformat() <= b["next_date"] <= end.isoformat()]
    if due:
        out.append(_i("Going out this week: " + ", ".join(f"{b['provider']} £{b['amount']:.2f}" for b in due)))
    subs = [s for s in money.subscriptions(svc, today)["items"] if s.get("days") is not None and 0 <= s["days"] <= 7]
    for s in subs[:4]:
        out.append(_i(f"{s['service']} renews {date.fromisoformat(s['next_date']).strftime('%a')} (£{s['amount']:.2f})",
                      "#tasks?sec=money"))
    sp = money.spending(svc, today=today)
    if sp.get("count"):
        diff = sp["total"] - sp["last_month_so_far"]
        out.append(_i(f"{sp['month']} so far: £{sp['total']:.0f} ({'up' if diff > 0 else 'down'} £{abs(diff):.0f} on last month)"))
    return out


def stuck(svc, today: date) -> list[dict]:
    from . import todos
    if not svc.google:
        return []
    ts = todos.list_todos(svc, include_projects=False)
    overdue = [t for t in ts if t.get("days") is not None and t["days"] < 0]
    out = []
    if overdue:
        out.append(_i(f"{len(overdue)} overdue: " + "; ".join(t["title"] for t in overdue[:4]), "#tasks", True))
    for t in todos.stalled(svc, now=today)[:3]:
        out.append(_i(f"Sat for weeks: {t['title']}", f"#tasks/{t['id']}"))
    return out


def kids(svc) -> list[dict]:
    from . import household
    out = []
    for k in household.chores_board(svc)["kids"]:
        line = f"{k['kid']}: {len(k['done_this_week'])} chores this week, £{k['owed']:.2f} owed"
        goals = [g for g in household.goals(svc, k["kid"]) if not g["done"]]
        if goals:
            line += f"; saving for {goals[0]['name']} ({goals[0]['pct']}%)"
        out.append(_i(line, "#home"))
    return out


def later(svc, end: date, today: date) -> list[dict]:
    from . import party, profiles, trips
    out = []
    horizon = (today + timedelta(days=35)).isoformat()
    for r in svc.store.renewals():
        if end.isoformat() < r["due_date"] <= horizon:
            out.append(_i(f"{r['name']}: {date.fromisoformat(r['due_date']).strftime('%-d %b')}", "#tasks?sec=renewals"))
    for t in trips.trips(svc, today):
        if t["days_to_go"] > 0 and t["start"] <= horizon:
            out.append(_i(f"Trip: {t['name']} in {t['days_to_go']} days (packed {t['packed']} of {t['to_pack']})",
                          "#tasks?sec=projects"))
    for p in party.parties(svc, today):
        if p["days_to_go"] > 0 and p["date"] <= horizon:
            out.append(_i(f"{p['name']} in {p['days_to_go']} days: {p['yes']} coming", "#tasks?sec=projects"))
    for p in profiles.profiles(svc):
        nb = profiles.next_birthday(p.get("birthday", ""), today) if p.get("birthday") else ""
        if nb and end.isoformat() < nb <= horizon:
            out.append(_i(f"{p['name']}'s birthday: {date.fromisoformat(nb).strftime('%-d %b')}", "#home"))
    return out


def text(agenda: dict) -> str:
    lines = [agenda["title"], ""]
    for s in agenda["sections"]:
        lines.append(s["title"])
        lines += [f"- {i['text']}" for i in s["items"]]
        lines.append("")
    return "\n".join(lines).strip()


def sunday(svc) -> dict:
    a = build(svc)
    if svc.notifier and a["sections"]:
        n = a["decisions"]
        svc.notifier.push(f"Your Sunday agenda is ready: {n} decision{'s' if n != 1 else ''} to make." if n
                          else "Your Sunday agenda is ready.", title="Sunday agenda",
                          actions=[{"action": "view", "label": "Open it", "url": f"{svc.settings.public_url}/app/#tasks?sec=week&agenda=1"}])
    return a
