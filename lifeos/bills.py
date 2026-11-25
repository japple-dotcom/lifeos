"""Bills and direct debits, from the emails that announce them, with a warning when one goes up.

Energy, water, council tax, broadband, mobile, insurance, TV licence: each provider's latest amount, how often,
when it's next taken, and its history. When a bill comes in noticeably higher than the last one for the same
period (5% and at least £3), a notification says so, with the old and new amounts. Shown under To-do > Money.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import files

KINDS = ["energy", "water", "council tax", "broadband", "mobile", "insurance", "tv licence", "rent or mortgage",
         "childcare", "other"]
PER_MONTH = {"monthly": 1.0, "quarterly": 1 / 3, "yearly": 1 / 12}
BILL_QUERY = ('newer_than:45d -label:"Life OS/Handled" (subject:("your bill" OR "bill is ready" OR "statement is ready" OR '
              '"direct debit" OR "payment schedule" OR "price change" OR "your new price" OR "council tax" OR '
              '"annual statement" OR "renewal notice") OR from:(octopus OR "british gas" OR edf OR eon OR ovo OR '
              'water OR council OR bt OR virgin OR sky OR vodafone OR o2 OR ee OR three OR tvlicensing))')
BILL_TOOL = {
    "name": "save_bill",
    "description": "Save what this bill, statement or direct debit email says the household pays.",
    "input_schema": {"type": "object", "properties": {
        "is_bill": {"type": "boolean", "description": "False unless it's a household bill, statement or direct debit notice"},
        "provider": {"type": "string", "description": "e.g. 'Octopus Energy', 'Leeds City Council'"},
        "kind": {"type": "string", "enum": KINDS},
        "amount": {"type": "number", "description": "Pounds per period (the regular payment, or this bill's total)"},
        "period": {"type": "string", "enum": ["monthly", "quarterly", "yearly"]},
        "direct_debit": {"type": "boolean"},
        "next_date": {"type": "string", "description": "When it's next taken or due, YYYY-MM-DD, or ''"},
        "previous_amount": {"type": "number", "description": "If the email states the old amount, else 0"},
        "date": {"type": "string", "description": "Date of the bill, YYYY-MM-DD"}},
        "required": ["is_bill"]},
}


def _key(provider: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-")[:40] or "bill"


def record(svc, provider: str, kind: str, amount: float, period: str = "monthly", direct_debit: bool = True,
           next_date: str = "", when: str = "", previous: float = 0.0, ref: str = "", link: str = "") -> dict:
    """Adds this bill to the provider's history; tells the household if it went up."""
    if not provider.strip() or amount <= 0:
        raise ValueError("Give the provider and the amount.")
    key = _key(provider)
    when = when or date.today().isoformat()
    old = svc.store.get("bill", key) or {}
    history = [h for h in old.get("history", []) if h.get("ref") != ref or not ref]
    if ref and any(h.get("ref") == ref for h in old.get("history", [])):
        return {**old, "id": key}  # this email was read before
    period = period if period in PER_MONTH else "monthly"
    prev = previous or next((h["amount"] for h in reversed(history) if h.get("period") == period), 0.0)
    history.append({"date": when, "amount": round(amount, 2), "period": period, "ref": ref, "link": link})
    rec = {"provider": provider.strip()[:60], "kind": kind if kind in KINDS else "other", "amount": round(amount, 2),
           "period": period, "direct_debit": bool(direct_debit), "next_date": next_date or old.get("next_date", ""),
           "history": history[-24:], "alerted": old.get("alerted", "")}
    rise = round(amount - prev, 2) if prev else 0.0
    if prev and rise >= 3 and rise >= 0.05 * prev and rec["alerted"] != f"{when}:{amount}":
        pct = round(100 * rise / prev)
        per = {"monthly": "a month", "quarterly": "a quarter", "yearly": "a year"}[period]
        if svc.notifier:
            svc.notifier.push(f"{rec['provider']} has gone up {pct}%: £{prev:.2f} to £{amount:.2f} {per} "
                              f"(£{rise * {'monthly': 12, 'quarterly': 4, 'yearly': 1}[period]:.0f} more a year).",
                              title="Bill gone up", actions=[{"action": "view", "label": "See bills",
                                                              "url": f"{svc.settings.public_url}/app/#tasks?sec=money"}])
        rec["alerted"] = f"{when}:{amount}"
        rec["rise"] = {"from": prev, "to": amount, "pct": pct, "when": when}
    elif old.get("rise"):
        rec["rise"] = old["rise"]
    return svc.store.put("bill", key, rec)


def from_email(svc, r: dict, ref: str, link: str = "") -> dict | None:
    if not r.get("is_bill") or not r.get("provider"):
        return None
    try:
        amount = float(r.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    d = (r.get("date") or "")[:10]
    nd = (r.get("next_date") or "")[:10]
    return record(svc, r["provider"], r.get("kind", "other"), amount, r.get("period", "monthly"),
                  bool(r.get("direct_debit", True)), nd if re.fullmatch(r"\d{4}-\d{2}-\d{2}", nd) else "",
                  d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else "", float(r.get("previous_amount") or 0), ref, link)


def bills(svc, today: date | None = None) -> dict:
    today = today or date.today()
    out = []
    for b in svc.store.records("bill"):
        nd = b.get("next_date", "")
        step = {"monthly": 30, "quarterly": 91, "yearly": 365}[b.get("period", "monthly")]
        while nd and nd < today.isoformat():
            nd = (date.fromisoformat(nd) + timedelta(days=step)).isoformat()
        out.append({**b, "next_date": nd, "per_month": round(b["amount"] * PER_MONTH[b.get("period", "monthly")], 2),
                    "files": files.for_item(svc, "bill", b["id"]),
                    "recent_rise": bool(b.get("rise")) and b["rise"]["when"] >= (today - timedelta(days=60)).isoformat()})
    out.sort(key=lambda b: (b["next_date"] or "9999", -b["per_month"]))
    return {"items": out, "per_month": round(sum(b["per_month"] for b in out), 2),
            "per_year": round(sum(b["per_month"] for b in out) * 12, 2)}


def delete(svc, key: str) -> bool:
    return svc.store.remove("bill", key)
