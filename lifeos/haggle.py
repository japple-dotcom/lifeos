"""Haggle helper: when a bill or a renewal goes up, everything needed to ask for a better price.

Last year's (or last month's) price against the new one, a short script for the phone call, an email to send from
your own Gmail (made as a draft: nothing is sent by Life OS), and where to compare prices. Rule-based: no AI needed.
"""
from __future__ import annotations

from datetime import date

from . import bills

COMPARE = {
    "energy": [("Uswitch: gas and electricity", "https://www.uswitch.com/gas-electricity/")],
    "broadband": [("Uswitch: broadband", "https://www.uswitch.com/broadband/")],
    "mobile": [("Uswitch: mobiles and SIM-only", "https://www.uswitch.com/mobiles/")],
    "insurance": [("Compare the Market", "https://www.comparethemarket.com/"),
                  ("MoneySuperMarket", "https://www.moneysupermarket.com/")],
    "car insurance": [("Compare the Market: car insurance", "https://www.comparethemarket.com/car-insurance/"),
                      ("MoneySuperMarket: car insurance", "https://www.moneysupermarket.com/car-insurance/")],
    "home insurance": [("Compare the Market: home insurance", "https://www.comparethemarket.com/home-insurance/"),
                       ("MoneySuperMarket: home insurance", "https://www.moneysupermarket.com/home-insurance/")],
    "council tax": [("GOV.UK: check your council tax band", "https://www.gov.uk/council-tax-bands")],
}
NO_SWITCH = {
    "water": "You can't switch water company in most of the UK, but you can ask about a water meter or a social tariff.",
    "council tax": "Council tax can't be haggled. If your band looks wrong compared with similar houses nearby, you "
                   "can ask for it to be checked. A single adult living alone gets 25% off.",
    "tv licence": "The TV licence is a fixed price.",
    "rent or mortgage": "For a mortgage, ask your lender for its product transfer rates and compare with a broker.",
}
PER_YEAR = {"monthly": 12, "quarterly": 4, "yearly": 1}


def _kind_for_renewal(name: str) -> str:
    low = name.lower()
    for word, kind in (("car insurance", "car insurance"), ("home insurance", "home insurance"),
                       ("contents", "home insurance"), ("buildings", "home insurance"), ("insurance", "insurance"),
                       ("broadband", "broadband"), ("mobile", "mobile"), ("phone", "mobile"), ("energy", "energy"),
                       ("breakdown", "insurance")):
        if word in low:
            return kind
    return "other"


def can_haggle_renewal(name: str) -> bool:
    return _kind_for_renewal(name) != "other"


def plan(provider: str, kind: str, old: float, new: float, period: str = "monthly", years_with: int = 0,
         account_holder: str = "") -> dict:
    per = {"monthly": "a month", "quarterly": "a quarter", "yearly": "a year"}.get(period, "")
    rise = round(new - old, 2) if old else 0.0
    pct = round(100 * rise / old) if old else 0
    yearly_extra = round(rise * PER_YEAR.get(period, 1)) if rise > 0 else 0
    price_line = (f"It's gone up from £{old:.2f} to £{new:.2f} {per}" + (f" ({pct}% more)" if pct else "")) if old \
        else f"The new price is £{new:.2f} {per}"
    loyal = (f"I've been with you {'over a year' if years_with == 1 else f'{years_with} years'}. ") if years_with else ""
    script = [
        "Ask for the retentions (or 'loyalty') team: \"I'm thinking of leaving because of the price.\"",
        f"Give the numbers: \"{price_line}.\"",
        f"\"{loyal}I've seen cheaper elsewhere. What's the best price you can do to keep me?\"",
        "If the offer isn't good enough: \"Is that the best you can do? I'll need to think about it.\" "
        "Then say no politely; a better offer often comes on a second call.",
        "Before agreeing: check the new price, how long it lasts, any exit fees, and ask for it in writing by email.",
    ]
    note = NO_SWITCH.get(kind, "")
    if kind in ("council tax", "tv licence"):
        script = []
    subject = f"My {kind if kind != 'other' else 'account'} price" + (f": {account_holder}" if account_holder else "")
    body = "\n".join([
        "Hello,",
        "",
        f"{price_line}." + (f" That's about £{yearly_extra} more a year." if yearly_extra else ""),
        "",
        f"{loyal}I'd like to stay, but I've seen lower prices elsewhere. Could you tell me the best price you can "
        "offer to keep me as a customer, and how long it would last?",
        "",
        "If I don't hear back I'll look at moving.",
        "",
        "Thank you,",
        account_holder or "",
    ]).strip()
    return {"provider": provider, "kind": kind, "old": old, "new": new, "rise": rise, "pct": pct,
            "yearly_extra": yearly_extra, "price_line": price_line, "script": script, "note": note,
            "email": {"subject": subject[:120], "body": body} if not note or kind not in ("council tax", "tv licence") else None,
            "compare": [{"name": n, "url": u} for n, u in COMPARE.get(kind, [])]}


def for_bill(svc, bill_id: str, person_name: str = "") -> dict:
    b = next((x for x in bills.bills(svc)["items"] if x["id"] == bill_id), None)
    if not b:
        raise ValueError("That bill isn't there any more.")
    old = (b.get("rise") or {}).get("from", 0.0)
    if not old:
        prev = [h["amount"] for h in b.get("history", [])[:-1] if h.get("period") == b["period"]]
        old = prev[-1] if prev else 0.0
    first = (b.get("history") or [{}])[0].get("date", "")
    years = max(0, (date.today() - date.fromisoformat(first)).days // 365) if first else 0
    return {**plan(b["provider"], b["kind"], float(old), float(b["amount"]), b["period"], years, person_name), "for": "bill",
            "id": bill_id}


def for_renewal(svc, renewal_id: int, quoted: float, last_year: float = 0.0, person_name: str = "") -> dict:
    r = next((x for x in svc.store.renewals() if int(x["id"]) == int(renewal_id)), None)
    if not r:
        raise ValueError("That renewal isn't there any more.")
    if quoted <= 0:
        raise ValueError("Put in the price they've quoted for next year.")
    p = plan(r["name"], _kind_for_renewal(r["name"]), float(last_year or 0), float(quoted), "yearly", 0, person_name)
    return {**p, "for": "renewal", "id": renewal_id, "due": r["due_date"]}


def make_draft(svc, person: str, h: dict) -> dict:
    """Puts the email in this person's Gmail drafts. Life OS never sends it."""
    if not h.get("email"):
        raise ValueError("There's no email to write for this one.")
    g = svc.google.get(person)
    d = g.create_draft("", h["email"]["subject"], h["email"]["body"])
    return {"ok": True, "draft_id": d.get("id", "")}


def make_todo(svc, person: str, h: dict) -> dict:
    from . import todos
    title = f"Ring {h['provider']} for a better price"
    notes = h["price_line"] + ".\n\n" + "\n".join(f"- {line}" for line in h["script"])
    if h.get("compare"):
        notes += "\n\nCompare first: " + ", ".join(c["url"] for c in h["compare"])
    judged = todos.normalise(svc, {"title": title, "category": "Admin and renewals", "priority": 2, "effort_min": 20}, title)
    return todos.add_todo(svc, None, title, person, judged=judged, notes=notes[:1500])
