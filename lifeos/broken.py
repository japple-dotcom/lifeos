"""Something's broken: find what it is among warranties, purchases from receipts and the contents list; say where the
family stands under the Consumer Rights Act 2015 by how long ago it was bought; then either write the claim to the
shop as a Gmail draft (the person sends it from Gmail), or turn it into a to-do for a repairer (the tradespeople flow
takes it from there). Claims are tracked with a chase-up to-do a week later.

UK rules used (goods bought from a business since 1 Oct 2015): up to 30 days, reject for a full refund; up to six
months, one repair or replacement, and the fault is taken to have been there from the start; after that, still a
repair or replacement, but the buyer may need to show the fault was there; claims can go back six years (five in
Scotland). The rights are against the shop, not the maker. A guarantee is extra, on top. This is general information,
not legal advice.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

KIND = "claim"


def _today(svc) -> date:
    from .household import today_local
    return today_local(svc.settings)


def _words(s: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(w) > 2 and w not in {"the", "and", "for", "with", "our", "from", "broken", "stopped", "working"}}


def find(svc, what: str) -> list[dict]:
    """Things it might be: [{source, id, name, bought, shop, link, warranty_ends, value, files}] best first."""
    want = _words(what)
    if not want:
        raise ValueError("Say what's broken, e.g. 'washing machine' or 'kettle'.")
    out: list[tuple[int, dict]] = []
    for r in svc.store.renewals():
        name = r["name"]
        if not name.lower().startswith("warranty:"):
            continue
        score = len(want & _words(name))
        if score:
            notes = r.get("notes") or ""
            m = re.search(r"Bought from (.+?) on (\d{4}-\d{2}-\d{2})", notes)
            link = re.search(r"Receipt: (\S+)", notes)
            out.append((score + 1, {"source": "warranty", "id": str(r["id"]), "name": name.split(":", 1)[1].strip(),
                                    "shop": m.group(1) if m else "", "bought": m.group(2) if m else "",
                                    "link": link.group(1) if link and link.group(1).startswith("https://") else "",
                                    "warranty_ends": r["due_date"]}))
    for p in svc.store.records("purchase"):
        score = len(want & _words(p.get("item", "")))
        if score and not p.get("item", "").startswith("Order from"):
            out.append((score, {"source": "receipt", "id": p["id"], "name": p["item"], "shop": p.get("shop", ""),
                                "bought": (p.get("at") or "")[:10], "price": p.get("amount"),
                                "link": p.get("link", "") if str(p.get("link", "")).startswith("https://") else ""}))
    from . import files
    for i in svc.store.records("item"):
        score = len(want & _words(i.get("name", "")))
        if score:
            out.append((score, {"source": "contents", "id": i["id"], "name": i["name"], "bought": i.get("bought", ""),
                                "value": i.get("value"), "shop": "", "link": "",
                                "files": len(files.for_item(svc, "item", i["id"]))}))
    out.sort(key=lambda x: (-x[0], x[1].get("bought") or ""), reverse=False)
    seen, best = set(), []
    for _, r in out:
        key = (r["name"].lower(), r.get("bought"))
        if key not in seen:
            seen.add(key)
            best.append(r)
    return best[:6]


def rights(bought: str, today: date, scotland: bool = False, warranty_ends: str = "") -> dict:
    """Where the family stands, in plain words, by how long ago it was bought."""
    if not bought:
        return {"stage": "unknown", "text": "Find the receipt or order email to see the date: your rights depend on "
                                            "how long ago you bought it. A bank statement can prove it too."}
    d = date.fromisoformat(bought)
    days = (today - d).days
    limit_years = 5 if scotland else 6
    w = ""
    if warranty_ends:
        w = (f" The maker's guarantee runs until {date.fromisoformat(warranty_ends):%d %B %Y}"
             " as well, on top of these rights." if warranty_ends >= today.isoformat() else "")
    if days <= 30:
        return {"stage": "refund", "days": days, "text": f"Bought {days} days ago: within 30 days you can reject it "
                "and ask the shop for a full refund." + w}
    if days <= 182:
        return {"stage": "repair", "days": days, "text": "Bought less than six months ago: the shop must repair or "
                "replace it (one try), and the fault is taken to have been there from the start. If that fails, you "
                "can ask for a refund or a price cut." + w}
    if days <= 365 * limit_years:
        return {"stage": "later", "days": days, "text": f"Bought {days // 30} months ago: you can still ask the shop "
                "to repair or replace it if it hasn't lasted as long as it reasonably should, though you may need to "
                f"show the fault was there from the start (a repairer's report helps). Claims can go back {limit_years} "
                "years." + w}
    return {"stage": "old", "days": days, "text": f"Bought more than {limit_years} years ago: it's past the time for "
            "a claim against the shop, so a repair is the way to go." + w}


def claim_draft(svc, person: str, name: str, fault: str, bought: str = "", shop: str = "", to: str = "",
                link: str = "") -> dict:
    """The claim to the shop as a Gmail draft (never sent from here) + a chase-up to-do in a week."""
    from . import todos
    name = " ".join((name or "").split())[:80]
    fault = " ".join((fault or "").split())[:400]
    if not name or not fault:
        raise ValueError("Say what it is and what's wrong with it.")
    if to and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", to, re.I):
        raise ValueError("That email address doesn't look right (or leave it blank and add it in Gmail).")
    g = svc.google.get(person)
    has = getattr(g, "has_gmail", True)
    if not (has() if callable(has) else has):
        raise ValueError("Gmail isn't connected for you: reconnect in Settings and tick Gmail.")
    today = _today(svc)
    r = rights(bought, today)
    when = f" on {date.fromisoformat(bought):%d %B %Y}" if bought else ""
    ask = {"refund": "I'd like to reject it and have a full refund, as is my right within 30 days under the Consumer "
                     "Rights Act 2015.",
           "repair": "Under the Consumer Rights Act 2015 I'm asking you to repair or replace it. As it's less than six "
                     "months since I bought it, the fault is presumed to have been present when it was delivered.",
           "later": "Under the Consumer Rights Act 2015 goods must be of satisfactory quality and last a reasonable "
                    "time. I'm asking you to repair or replace it."}.get(r["stage"], "I'm asking you to repair or replace it.")
    who = svc.google.display_name(person) if hasattr(svc.google, "display_name") else ""
    body = (f"Hello,\n\nI bought a {name} from {shop or 'you'}{when}. It has developed a fault: {fault}\n\n{ask}\n\n"
            f"{('My receipt: ' + link + chr(10) + chr(10)) if link else ''}"
            "Please let me know how you'd like to arrange this. I look forward to hearing from you within 14 days.\n\n"
            f"Thank you,\n{who}".rstrip())
    subject = f"Faulty {name}: request for {'a refund' if r['stage'] == 'refund' else 'repair or replacement'}"
    d = g.create_draft(to, subject, body)
    cid = secrets.token_hex(4)
    t = todos.guess(svc, f"Chase the claim for the {name}")
    t.update(title=f"Chase {shop or 'the shop'} about the faulty {name} (if no reply)",
             due=(today + timedelta(days=7)).isoformat(), timescale="this_week",
             notes="Life OS put the claim email in your Gmail drafts. Send it from Gmail first.")
    todo = todos.file_todo(svc, todos.normalise(svc, t, t["title"]), person)
    rec = {"id": cid, "name": name, "fault": fault, "bought": bought, "shop": shop, "to": to, "stage": r["stage"],
           "draft": d.get("id", ""), "todo": todo.get("id", ""), "status": "drafted", "opened": today.isoformat()}
    svc.store.put(KIND, cid, rec)
    svc.store.log_event("claim", f"{person}: claim drafted for {name}")
    return {"ok": True, "claim": rec, "rights": r}


def repairer(svc, agent, person: str, name: str, fault: str) -> dict:
    """A to-do to get it repaired; the tradespeople flow (detect_trade) offers to find and email someone."""
    from . import todos
    name = " ".join((name or "").split())[:80]
    if not name:
        raise ValueError("Say what's broken.")
    text = f"Get the {name} repaired: {' '.join((fault or '').split())[:200]}".rstrip(": ")
    return todos.add_todo(svc, agent, text, added_by=person)


def claims(svc) -> list[dict]:
    return sorted(svc.store.records(KIND), key=lambda c: c.get("opened", ""), reverse=True)


def set_status(svc, cid: str, status: str) -> dict:
    if status not in ("drafted", "sent", "replied", "sorted", "dropped"):
        raise ValueError("Unknown status.")
    c = svc.store.get(KIND, cid)
    if not c:
        raise ValueError("That claim isn't on the list any more.")
    c["status"] = status
    svc.store.put(KIND, cid, c)
    return c


def look(svc, what: str) -> dict:
    today = _today(svc)
    found = find(svc, what)
    return {"found": [{**f, "rights": rights(f.get("bought", ""), today, warranty_ends=f.get("warranty_ends", ""))}
                      for f in found],
            "general": rights("", today)["text"] if not found else ""}
