"""Money, lightly: what's being spent each month (from receipts) and the subscriptions that keep renewing.

Everything here comes from receipts and order emails Life OS already reads, and stays on the home box. It isn't
a bank feed: it shows what receipts show.

Subscriptions get a "still using this?" nudge a few days before they renew (a week before for yearly ones):
Keep (asked again in a year), Cancel it (a to-do with the cancel link), or Not a subscription.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .mailreader import CATEGORIES

PER_MONTH = {"weekly": 52 / 12, "monthly": 1.0, "yearly": 1 / 12}


def _pounds(x) -> float:
    try:
        return round(max(0.0, float(x or 0)), 2)
    except (TypeError, ValueError):
        return 0.0


def sub_key(service: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (service or "").lower()).strip()[:40]


# ---------- recording ----------
def record_receipt(svc, r: dict, when: str, shop: str, person: str, ref: str, link: str = "") -> None:
    lines = 0
    for i, item in enumerate((r.get("items") or [])[:80]):
        price = _pounds(item.get("price"))
        if not price or not item.get("name"):
            continue
        cat = item.get("category") if item.get("category") in CATEGORIES else "other"
        svc.store.add_new("purchase", f"{ref}:{i}", {"item": str(item["name"])[:120], "category": cat, "amount": price,
                                                    "shop": shop, "person": person, "link": link}, at=when)
        lines += 1
    total = _pounds(r.get("total"))
    if not lines and total:  # no line prices: the order total, under its most common kind
        cats = [it.get("category") for it in r.get("items") or [] if it.get("category") in CATEGORIES]
        cat = max(set(cats), key=cats.count) if cats else ("subscriptions" if r.get("subscription") else "other")
        svc.store.add_new("purchase", f"{ref}:total", {"item": f"Order from {shop}", "category": cat, "amount": total,
                                                       "shop": shop, "person": person, "link": link}, at=when)
    sub = r.get("subscription") or {}
    if sub.get("service") and _pounds(sub.get("amount")):
        record_subscription(svc, sub["service"], _pounds(sub["amount"]), sub.get("period") or "monthly",
                            sub.get("next_date") or "", sub.get("manage_url") or "", when, link)


def record_subscription(svc, service: str, amount: float, period: str, next_date: str = "", manage_url: str = "",
                        seen: str = "", link: str = "") -> dict:
    key = sub_key(service)
    old = svc.store.get("subscription", key) or {}
    period = period if period in PER_MONTH else "monthly"
    if not next_date and seen:
        step = {"weekly": timedelta(days=7), "monthly": timedelta(days=30), "yearly": timedelta(days=365)}[period]
        next_date = (date.fromisoformat(seen) + step).isoformat()
    rec = {"service": service.strip()[:60], "amount": amount, "period": period,
           "next_date": next_date or old.get("next_date", ""),
           "manage_url": manage_url if manage_url.startswith("https://") else old.get("manage_url", ""),
           "last_seen": seen or old.get("last_seen", date.today().isoformat()), "link": link or old.get("link", ""),
           "status": old.get("status", "active"), "decided": old.get("decided", ""), "nudged_for": old.get("nudged_for", "")}
    if old.get("amount") and abs(old["amount"] - amount) >= 0.01 and old.get("period") == period:
        rec["was"] = old["amount"]  # price went up (or down) since last time
    svc.store.put("subscription", key, rec)
    return rec


# ---------- subscriptions ----------
def subscriptions(svc, today: date | None = None) -> dict:
    today = today or date.today()
    out = []
    for s in svc.store.records("subscription"):
        if s.get("status") in ("not", "cancelled"):
            continue
        nd = s.get("next_date", "")
        # Rolls the renewal date forward if it's passed without a new email
        while nd and nd < today.isoformat():
            step = {"weekly": 7, "monthly": 30, "yearly": 365}[s.get("period", "monthly")]
            nd = (date.fromisoformat(nd) + timedelta(days=step)).isoformat()
        per_month = round(s["amount"] * PER_MONTH.get(s.get("period", "monthly"), 1), 2)
        out.append({**s, "next_date": nd, "per_month": per_month,
                    "days": (date.fromisoformat(nd) - today).days if nd else None})
    out.sort(key=lambda s: (s["days"] if s["days"] is not None else 999, -s["per_month"]))
    return {"items": out, "per_month": round(sum(s["per_month"] for s in out), 2),
            "per_year": round(sum(s["per_month"] for s in out) * 12, 2)}


def decide(svc, key: str, choice: str, person: str = "") -> dict:
    s = svc.store.get("subscription", key)
    if not s:
        raise ValueError("That subscription isn't on the list.")
    s.pop("id", None)
    s.pop("at", None)
    s.pop("updated_at", None)
    today = date.today().isoformat()
    if choice == "keep":
        s.update(status="keep", decided=today)
    elif choice == "not":
        s.update(status="not", decided=today)
    elif choice == "cancel":
        from . import todos
        when = s.get("next_date") or ""
        title = f"Cancel {s['service']}" + (f" before {date.fromisoformat(when).strftime('%-d %b')}" if when else "")
        how = s.get("manage_url") or ("https://www.google.com/search?q=" + "+".join(f"how to cancel {s['service']} UK".split()))
        todos.add_todo(svc, None, title, person or None, judged=todos.normalise(svc, {
            "title": title, "category": "Admin and renewals", "priority": 2, "effort_min": 10,
            "due": (date.fromisoformat(when) - timedelta(days=1)).isoformat() if when else "",
            "next_action": "Open the cancel page and cancel it"}, title), notes=f"Cancel or manage: {how}")
        s.update(status="cancelling", decided=today)
    else:
        raise ValueError("Choose keep, cancel or not.")
    svc.store.put("subscription", key, s)
    return s


def nudges(svc, today: date | None = None) -> list[str]:
    """Daily: 'Still using Disney+? £8.99 a month, renews Friday.' Once per renewal; kept ones asked yearly."""
    today = today or date.today()
    sent = []
    for s in subscriptions(svc, today)["items"]:
        if s["days"] is None or s.get("status") == "cancelling":
            continue
        ahead = 7 if s.get("period") == "yearly" else 3
        if not 0 <= s["days"] <= ahead or s.get("nudged_for") == s["next_date"]:
            continue
        if s.get("status") == "keep" and s.get("decided") and \
                date.fromisoformat(s["decided"]) > today - timedelta(days=330):
            continue
        cost = f"£{s['amount']:.2f} a {dict(weekly='week', monthly='month', yearly='year')[s['period']]}"
        when = "today" if s["days"] == 0 else "tomorrow" if s["days"] == 1 else date.fromisoformat(s["next_date"]).strftime("%A")
        msg = f"Still using {s['service']}? {cost}, renews {when}."
        if s.get("was"):
            msg += f" It went {'up' if s['amount'] > s['was'] else 'down'} from £{s['was']:.2f}."
        if svc.notifier:
            svc.notifier.push(msg, title="Subscription renewing", actions=[
                {"action": "view", "label": "Keep or cancel", "url": f"{svc.settings.public_url}/app/#tasks?sec=money"}])
        rec = svc.store.get("subscription", s["id"])
        rec = {k: v for k, v in rec.items() if k not in ("id", "at", "updated_at")}
        rec["nudged_for"] = s["next_date"]
        svc.store.put("subscription", s["id"], rec)
        sent.append(s["service"])
    return sent


# ---------- spending ----------
def month_bounds(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt - timedelta(days=1)


def spending(svc, month: str = "", today: date | None = None) -> dict:
    """This month (or 'YYYY-MM') by category, compared with the month before, plus the last six months' totals."""
    today = today or date.today()
    first, last = month_bounds(date.fromisoformat(f"{month}-01") if month else today)
    prev_first, prev_last = month_bounds(first - timedelta(days=1))

    def tally(a: date, b: date) -> tuple[dict, dict, float]:
        cats, shops, total = {}, {}, 0.0
        for p in svc.store.records("purchase", a.isoformat(), b.isoformat()):
            cats[p["category"]] = cats.get(p["category"], 0) + p["amount"]
            shops[p["shop"]] = shops.get(p["shop"], 0) + p["amount"]
            total += p["amount"]
        return cats, shops, total
    cats, shops, total = tally(first, last)
    pcats, _, ptotal = tally(prev_first, prev_last)
    # Same point last month, so a half-finished month is compared fairly
    day_n = min((today - first).days, (prev_last - prev_first).days) if first <= today <= last else None
    so_far_prev = tally(prev_first, prev_first + timedelta(days=day_n))[2] if day_n is not None else ptotal
    rows = [{"category": c, "amount": round(cats.get(c, 0), 2), "last": round(pcats.get(c, 0), 2),
             "change": round(cats.get(c, 0) - pcats.get(c, 0), 2)} for c in sorted(set(cats) | set(pcats))]
    rows.sort(key=lambda r: -r["amount"])
    changes = sorted((r for r in rows if abs(r["change"]) >= 10), key=lambda r: -abs(r["change"]))[:3]
    months = []
    for i in range(5, -1, -1):
        d = first
        for _ in range(i):
            d = (d - timedelta(days=1)).replace(day=1)
        a, b = month_bounds(d)
        months.append({"month": a.strftime("%b"), "total": round(tally(a, b)[2], 2)})
    return {"month": first.strftime("%B %Y"), "total": round(total, 2), "last_month": round(ptotal, 2),
            "last_month_so_far": round(so_far_prev, 2), "by_category": rows, "biggest_changes": changes,
            "shops": sorted(({"shop": k, "amount": round(v, 2)} for k, v in shops.items()), key=lambda x: -x["amount"])[:6],
            "months": months, "count": len(svc.store.records("purchase", first.isoformat(), last.isoformat()))}
