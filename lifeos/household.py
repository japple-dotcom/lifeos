"""Household features that run on rules, not AI: renewal reminders and kids' chores / pocket money."""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .dates import fmt as _dfmt

log = logging.getLogger("lifeos.household")
RENEWALS_LIST = "Admin and renewals"


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def uk_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime(_dfmt("%-d %b %Y"))


def today_local(settings) -> date:
    return datetime.now(ZoneInfo(settings.tz)).date()


# ---------- renewals ----------
def check_renewals(svc, today: date | None = None) -> list[str]:
    """Run daily. Rolls repeating renewals forward once past, and creates one task + notification
    when a renewal comes within its reminder window. Returns what it did, for the log and tests."""
    today = today or today_local(svc.settings)
    did = []
    for r in svc.store.renewals():
        due = date.fromisoformat(r["due_date"])
        # A repeating renewal that has passed moves to its next date (e.g. car insurance every 12 months).
        if due < today and r["repeat_months"]:
            while due < today:
                due = add_months(due, r["repeat_months"])
            svc.store.update_renewal(r["id"], due_date=due.isoformat(), reminded_for=None)
            r = {**r, "due_date": due.isoformat(), "reminded_for": None}
            did.append(f"rolled {r['name']} to {due}")
        if due < today or r["reminded_for"] == r["due_date"]:
            continue
        if (due - today).days <= (r["remind_days"] or 42):
            title = f"Renew {r['name']} (due {uk_date(r['due_date'])})"
            notes = "Compare prices before it auto-renews. " + (r["notes"] or "")
            task_due = max(today, due - timedelta(days=14)).isoformat()
            try:
                if svc.google:
                    svc.google.get().add_task(RENEWALS_LIST, title, notes.strip(), task_due)
            except Exception as e:
                log.warning("renewal task for %s failed: %s", r["name"], e)
            if svc.notifier:
                try:
                    svc.notifier.push(f"{r['name']} is due on {uk_date(r['due_date'])}. "
                                      f"Added to {RENEWALS_LIST}.", title="Renewal coming up", tags=["calendar"])
                except Exception as e:
                    log.warning("renewal notification failed: %s", e)
            svc.store.update_renewal(r["id"], reminded_for=r["due_date"])
            svc.store.log_event("renewal_reminder", r["name"])
            did.append(f"reminded {r['name']}")
    return did


def upcoming_renewals(svc, days: int = 60) -> list[dict]:
    today = today_local(svc.settings)
    out = []
    for r in svc.store.renewals():
        left = (date.fromisoformat(r["due_date"]) - today).days
        if -7 <= left <= days:
            out.append({**r, "days_left": left})
    return out


# ---------- chores and pocket money ----------
def chore_catalogue(settings) -> list[dict]:
    """From settings.yaml `chores`: [{id, name, value (pounds), who: [kid names]}]."""
    return [{"id": c["id"], "name": c["name"], "value": float(c.get("value", 0) or 0),
             "who": list(c.get("who") or settings.household.get("kids") or [])}
            for c in settings.behaviour.get("chores", []) or []]


def chore_value(settings, chore_name: str) -> float:
    for c in chore_catalogue(settings):
        if c["name"].lower() == chore_name.lower() or c["id"] == chore_name:
            return c["value"]
    return 0.0


def week_start(settings) -> str:
    """Monday 00:00 this week, local time, as a UTC ISO string for comparing with the log."""
    now = datetime.now(ZoneInfo(settings.tz))
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")


MONEY_MOVES = ("Weekly allowance", "Saved towards ", "Savings back from ")


def is_money_move(name: str) -> bool:
    """Pocket money and savings are kept in the same log as chores (so 'owed' adds up), but they aren't chores."""
    return (name or "").startswith(MONEY_MOVES)


def chores_board(svc) -> dict:
    """Everything the Chores card needs: what each child can do, what they did this week, what is owed."""
    since = week_start(svc.settings)
    kids = list(svc.settings.household.get("kids") or [])
    done = svc.store.chores_since(since)
    owed = svc.store.unpaid_totals()
    from .kidsmode import tablet_ids
    tablet = tablet_ids(svc)
    board = []
    for kid in kids:
        mine = [d for d in done if d["who"] == kid and not is_money_move(d["chore"])]
        board.append({
            "kid": kid,
            "owed": owed.get(kid, 0.0),
            "done_this_week": [{"id": d["id"], "chore": d["chore"], "at": d["at"], "value": d["value"],
                                "tablet": d["id"] in tablet} for d in mine],
            "chores": [c for c in chore_catalogue(svc.settings) if kid in c["who"]],
        })
    return {"kids": board}


# ---------- meal plan ----------
DEFAULT_PANTRY = ["salt", "pepper", "oil", "olive oil", "water", "sugar", "flour", "butter", "stock cube",
                  "garlic", "onion", "rice", "pasta", "soy sauce", "dried herbs", "spices"]


def pantry(settings) -> set[str]:
    items = settings.behaviour.get("pantry_staples")
    return {i.strip().lower() for i in (items if items is not None else DEFAULT_PANTRY)}


def week_meals(svc, start: date | None = None, days: int = 7) -> list[dict]:
    start = start or today_local(svc.settings)
    end = start + timedelta(days=days - 1)
    saved = {m["day"]: m for m in svc.store.meals_between(start.isoformat(), end.isoformat())}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        m = saved.get(d.isoformat(), {})
        out.append({"day": d.isoformat(), "label": d.strftime(_dfmt("%A %-d %B")), "dinner": m.get("dinner", ""),
                    "ingredients": m.get("ingredients", []), "notes": m.get("notes", "")})
    return out


def save_meal_plan(svc, meals: list[dict], add_to_shopping: bool = True) -> dict:
    """meals: [{day: YYYY-MM-DD, dinner: str, ingredients: [str], notes: str}]. Ingredients that are not
    pantry staples go onto the Shopping list once each (exact duplicates are skipped by the list itself)."""
    staples = pantry(svc.settings)
    saved, shopping = [], []
    for m in meals:
        day = date.fromisoformat(str(m["day"])).isoformat()
        dinner = str(m.get("dinner", "")).strip()[:200]
        if not dinner:
            svc.store.delete_meal(day)
            continue
        ingredients = [str(i).strip()[:80] for i in (m.get("ingredients") or []) if str(i).strip()][:30]
        svc.store.set_meal(day, dinner, ingredients, str(m.get("notes", ""))[:300])
        saved.append(day)
        for i in ingredients:
            if i.lower() not in staples and i.lower() not in [s.lower() for s in shopping]:
                shopping.append(i)
    added = []
    if add_to_shopping and shopping and svc.google:
        g = svc.google.get()
        for item in shopping:
            try:
                if not g.add_task("Shopping", item).get("duplicate"):
                    added.append(item)
            except Exception as e:
                log.warning("could not add %s to Shopping: %s", item, e)
                break
    return {"saved_days": saved, "added_to_shopping": added}


INGREDIENTS_TOOL = {
    "name": "save_ingredients",
    "description": "Save the shopping ingredients for each dinner (for a family of four, UK supermarket names).",
    "input_schema": {"type": "object", "properties": {"meals": {"type": "array", "items": {"type": "object", "properties": {
        "dinner": {"type": "string"}, "ingredients": {"type": "array", "items": {"type": "string"}}},
        "required": ["dinner", "ingredients"]}}}, "required": ["meals"]},
}


def meals_to_shopping(svc, agent=None, days: int = 7) -> dict:
    """This week's dinners: missing ingredients onto the Shopping list. Ingredients are remembered per dinner,
    so the AI only works out new dinners; pantry staples and things already on the list are skipped."""
    from . import activity, todos
    week = [m for m in week_meals(svc, days=days) if m["dinner"]]
    unknown = []
    for m in week:
        if not m["ingredients"]:
            m["ingredients"] = svc.store.known_ingredients(m["dinner"])
            if m["ingredients"]:
                svc.store.set_meal(m["day"], m["dinner"], m["ingredients"], m["notes"])
            else:
                unknown.append(m)
    if unknown:
        r = todos._ask_ai_for_tool(
            svc, agent, INGREDIENTS_TOOL,
            "You plan family dinners in the UK. List the ingredients to buy for each dinner, short supermarket names "
            "(e.g. 'chicken thighs', 'peppers', 'tortilla wraps'). Leave out salt, pepper, oil and water.",
            "Dinners: " + "; ".join(m["dinner"] for m in unknown)) or {}
        found = {str(x.get("dinner", "")).strip().lower(): [str(i).strip()[:60] for i in x.get("ingredients") or [] if str(i).strip()][:20]
                 for x in r.get("meals") or []}
        for m in unknown:
            m["ingredients"] = found.get(m["dinner"].lower(), [])
            if m["ingredients"]:
                svc.store.set_meal(m["day"], m["dinner"], m["ingredients"], m["notes"])
    staples = pantry(svc.settings)
    g = svc.google.get()
    have = {activity.normalise(t["title"]) for t in g.list_tasks("Shopping")}
    added, skipped = [], []
    for m in week:
        for i in m["ingredients"]:
            key = activity.normalise(i)
            if i.lower().strip() in staples:  # exact only: "peppers" (to buy) isn't "pepper" (in the cupboard)
                continue
            if key in have:
                skipped.append(i)
                continue
            if not g.add_task("Shopping", i).get("duplicate"):
                added.append(i)
            have.add(key)
    return {"added": added, "already_on_list": skipped,
            "unknown": [m["dinner"] for m in week if not m["ingredients"]]}


def dinner_on(svc, day: date | None = None) -> str:
    day = day or today_local(svc.settings)
    m = svc.store.meals_between(day.isoformat(), day.isoformat())
    return m[0]["dinner"] if m else ""


# ---------- quiet hours ----------
def in_quiet_hours(settings, now: datetime | None = None) -> bool:
    """settings.yaml notifications.quiet_hours: "21:30-07:00" (wraps past midnight). Empty = never quiet."""
    span = (settings.behaviour.get("notifications") or {}).get("quiet_hours") or ""
    if "-" not in span:
        return False
    now = now or datetime.now(ZoneInfo(settings.tz))
    start_s, end_s = [x.strip() for x in span.split("-", 1)]
    to_min = lambda t: int(t.split(":")[0]) * 60 + int(t.split(":")[1])  # noqa: E731
    start, end, cur = to_min(start_s), to_min(end_s), now.hour * 60 + now.minute
    return (start <= cur or cur < end) if start > end else (start <= cur < end)


class QuietHours:
    """Wraps the notifier: during quiet hours ordinary notifications are held and sent as one morning digest.
    Urgent ones (priority 5) and anything with Approve / Reject buttons always go straight through."""

    def __init__(self, inner, svc):
        self.inner, self.svc = inner, svc

    def push(self, message: str, title: str = "Life OS", priority: int = 3, actions=None, tags=None) -> None:
        if priority < 5 and not actions and in_quiet_hours(self.svc.settings):
            self.svc.store.hold_notification(title, message)
            return
        self.inner.push(message, title=title, priority=priority, actions=actions, tags=tags)

    def flush(self) -> int:
        """Send anything held overnight as one notification. Call when quiet hours end."""
        if in_quiet_hours(self.svc.settings):
            return 0
        held = self.svc.store.take_held_notifications()
        if not held:
            return 0
        lines = [f"• {h['title']}: {h['message'].splitlines()[0][:160]}" for h in held]
        self.inner.push("\n".join(lines[:12]), title=f"Overnight ({len(held)})")
        return len(held)

    def __bool__(self) -> bool:
        return bool(self.inner)


# ---------- dinners that learn (v0.15) ----------
def rate_dinner(svc, day: str, rating: int) -> bool:
    """1 = loved it, -1 = not again, 0 = no opinion."""
    return svc.store.rate_meal(day, rating)


def dinner_to_rate(svc, now: datetime | None = None) -> dict | None:
    """Today's dinner after 7.30pm, otherwise yesterday's, if it hasn't been rated."""
    now = now or datetime.now(ZoneInfo(svc.settings.tz))
    day = now.date() if now.hour * 60 + now.minute >= 19 * 60 + 30 else now.date() - timedelta(days=1)
    rows = svc.store.meal_history(day.isoformat())
    m = next((r for r in rows if r["day"] == day.isoformat()), None)
    if not m or m.get("rating"):
        return None
    if svc.store.get_state(f"meal_rate_skip:{day}", ""):
        return None
    return {"day": day.isoformat(), "dinner": m["dinner"], "when": "tonight" if day == now.date() else "last night"}


def _quick(dinner: str, svc) -> bool:
    quick = [q.lower() for q in svc.settings.behaviour.get("quick_meals") or []]
    low = dinner.lower()
    return any(q in low for q in quick) or any(w in low for w in ("pasta", "stir fry", "stir-fry", "omelette", "fajitas",
                                                                 "wraps", "beans on toast", "pizza", "soup", "noodles"))


def suggest_dinners(svc, start: date | None = None, days: int = 7) -> list[dict]:
    """Fills empty evenings: dinners you liked, not had for a while, quick ones on busy evenings, and ones that share
    ingredients with the rest of the week (so the leftover half pack of peppers gets used). No AI needed."""
    start = start or today_local(svc.settings)
    week = week_meals(svc, start, days)
    history = svc.store.meal_history((start - timedelta(days=365)).isoformat())
    stats: dict[str, dict] = {}
    for h in history:
        k = h["dinner"].strip().lower()
        s = stats.setdefault(k, {"name": h["dinner"].strip(), "count": 0, "liked": 0, "disliked": 0, "last": "",
                                 "ingredients": set()})
        s["count"] += 1
        s["liked"] += h.get("rating", 0) == 1
        s["disliked"] += h.get("rating", 0) == -1
        s["last"] = max(s["last"], h["day"])
        s["ingredients"] |= {i.lower() for i in h["ingredients"]}
    for f in svc.settings.behaviour.get("favourite_meals") or []:
        stats.setdefault(f.lower(), {"name": f, "count": 0, "liked": 0, "disliked": 0, "last": "", "ingredients": set()})
        stats[f.lower()]["favourite"] = True
    planned = {m["dinner"].strip().lower() for m in week if m["dinner"]}
    week_ingredients = {i.lower() for m in week for i in (m["ingredients"] or svc.store.known_ingredients(m["dinner"]) or [])
                        if m["dinner"]}
    busy_evenings = set()
    if svc.google:
        try:
            for e in svc.google.events_all(days=days + 1):
                st = e.get("start") or ""
                if len(st) > 10 and "16:30" <= st[11:16] <= "19:30":
                    busy_evenings.add(st[:10])
        except Exception:
            pass
    out = []
    for m in week:
        if m["dinner"]:
            continue
        best, best_score, why = None, -99.0, ""
        for k, s in stats.items():
            if k in planned or (s["disliked"] and s["disliked"] >= s["liked"]):
                continue
            days_since = (start - date.fromisoformat(s["last"])).days if s["last"] else 60
            if days_since < 10:
                continue
            score = 3 * s["liked"] + 2 * bool(s.get("favourite")) + min(s["count"], 5) * 0.5 + min(days_since, 60) / 30
            shared = sorted(s["ingredients"] & week_ingredients)
            score += 1.5 * min(len(shared), 3)
            busy = m["day"] in busy_evenings
            if busy:
                score += 3 if _quick(s["name"], svc) else -2
            if score > best_score:
                best, best_score = s, score
                reasons = []
                if s["liked"]:
                    reasons.append("you loved it")
                elif s.get("favourite"):
                    reasons.append("a favourite")
                if s["last"]:
                    reasons.append(f"not had since {date.fromisoformat(s['last']).strftime(_dfmt('%-d %b'))}")
                if shared:
                    reasons.append(f"uses up the {', '.join(shared[:2])}")
                if busy and _quick(s["name"], svc):
                    reasons.append("quick, for a busy evening")
                why = "; ".join(reasons).capitalize()
        if best:
            out.append({"day": m["day"], "label": m["label"], "dinner": best["name"], "why": why})
            planned.add(best["name"].lower())
            week_ingredients |= best["ingredients"]
    return out


# ---------- pocket money: weekly allowance and savings goals (v0.16) ----------
def allowance(svc, kid: str) -> float:
    v = svc.store.get_state(f"allowance:{kid}", "")
    if v:
        return float(v)
    return float(((svc.settings.behaviour.get("pocket_money") or {}).get("allowance") or {}).get(kid, 0) or 0)


def set_allowance(svc, kid: str, amount: float) -> float:
    if kid not in (svc.settings.household.get("kids") or []):
        raise ValueError("That isn't one of the children.")
    amount = round(max(0.0, min(50.0, float(amount))), 2)
    svc.store.set_state(f"allowance:{kid}", str(amount))
    return amount


def pay_allowances(svc, today: date | None = None) -> list[str]:
    """Saturday morning: each child's weekly allowance goes onto what they're owed (once a week)."""
    today = today or today_local(svc.settings)
    week = today.isocalendar()
    did = []
    for kid in svc.settings.household.get("kids") or []:
        amt = allowance(svc, kid)
        key = f"allowance_paid:{kid}:{week[0]}-{week[1]}"
        if amt > 0 and not svc.store.get_state(key, ""):
            svc.store.log_chore("Weekly allowance", kid, amt)
            svc.store.set_state(key, "1")
            did.append(f"{kid}:{amt:.2f}")
    return did


def goals(svc, kid: str | None = None) -> list[dict]:
    out = []
    for g in svc.store.records("goal"):
        if kid and g["kid"] != kid:
            continue
        out.append({**g, "pct": min(100, round(100 * g["saved"] / g["target"])) if g["target"] else 0,
                    "left": round(max(0.0, g["target"] - g["saved"]), 2)})
    return sorted(out, key=lambda g: (g["kid"], g.get("done", False), g["name"]))


def add_goal(svc, kid: str, name: str, target: float) -> dict:
    import secrets
    if kid not in (svc.settings.household.get("kids") or []):
        raise ValueError("That isn't one of the children.")
    if not name.strip() or not 0 < float(target) <= 2000:
        raise ValueError("Say what they're saving for and how much it costs.")
    return svc.store.put("goal", secrets.token_hex(4), {"kid": kid, "name": name.strip()[:60], "target": round(float(target), 2),
                                                        "saved": 0.0, "done": False})


def save_towards(svc, goal_id: str, amount: float) -> dict:
    """Moves money from what they're owed into the goal. Can't save more than they're owed."""
    g = svc.store.get("goal", goal_id)
    if not g:
        raise ValueError("That goal isn't there any more.")
    owed = svc.store.unpaid_totals().get(g["kid"], 0.0)
    amount = round(min(float(amount), owed, max(0.0, g["target"] - g["saved"])), 2)
    if amount <= 0:
        raise ValueError(f"{g['kid']} hasn't got any pocket money owed to save." if owed <= 0 else "That goal is reached.")
    svc.store.log_chore(f"Saved towards {g['name']}", g["kid"], -amount)
    rec = {k: v for k, v in g.items() if k not in ("id", "at", "updated_at")}
    rec["saved"] = round(rec["saved"] + amount, 2)
    rec["done"] = rec["saved"] >= rec["target"]
    svc.store.put("goal", goal_id, rec)
    return {**rec, "id": goal_id, "moved": amount}


def delete_goal(svc, goal_id: str) -> bool:
    """Taking a goal away gives its savings back to what they're owed."""
    g = svc.store.get("goal", goal_id)
    if not g:
        return False
    if g["saved"] > 0 and not g.get("done"):
        svc.store.log_chore(f"Savings back from {g['name']}", g["kid"], g["saved"])
    return svc.store.remove("goal", goal_id)
