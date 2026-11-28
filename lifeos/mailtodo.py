"""Email to to-do: in Gmail (on the phone too), give an email the label "Life OS" and within ten minutes it's a to-do
with a link back to the email. The label then changes to "Life OS/Added" so it's done once.

The email's subject and opening are untrusted data: they go through the normal sorting (forced tool or rules) and
never act as instructions. Nothing is sent or replied to.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("lifeos.mailtodo")
LABEL = "Life OS"
ADDED = "Life OS/Added"
QUERY = "label:life-os -label:life-os-added"


def _clean_subject(s: str) -> str:
    s = re.sub(r"^\s*((re|fwd?|fw)\s*:\s*)+", "", s or "", flags=re.I)
    return " ".join(s.split())[:150]


def _sender(f: str) -> str:
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', f or "")
    return (m.group(1) if m else (f or "")).strip()[:80]


def run(svc, agent, limit: int = 10) -> list[str]:
    """Each connected person's labelled emails -> to-dos. Returns the to-do titles added."""
    from . import todos
    accounts = svc.google
    if accounts is None:
        return []
    added: list[str] = []
    for st in accounts.status():
        if not (st["connected"] and st["gmail"]):
            continue
        person = st["person"]
        g = accounts.get(person)
        try:
            msgs = g.search_mail(QUERY, limit=limit)
        except Exception as e:  # noqa: BLE001 - one account failing mustn't stop the others
            log.warning("email to-do: couldn't search %s's Gmail: %s", person, e)
            continue
        done = []
        for m in msgs:
            if svc.store.is_seen(f"mailtodo:{person}", m["id"]):
                done.append(m["id"])
                continue
            subject = _clean_subject(m.get("subject", "")) or "Email to deal with"
            sender = _sender(m.get("from", ""))
            snippet = " ".join((m.get("snippet") or "").split())[:300]
            text = f"{subject} (email from {sender})" if sender else subject
            notes = "\n".join(x for x in (f"From: {sender}" if sender else "", f"Email: {m.get('link', '')}",
                                          f"It says: {snippet}" if snippet else "") if x)
            try:
                t = todos.add_todo(svc, agent, text, added_by=person, notes=notes)
            except Exception as e:  # noqa: BLE001
                log.warning("email to-do: couldn't add %r: %s", subject, e)
                continue
            svc.store.mark_seen(f"mailtodo:{person}", [m["id"]])
            done.append(m["id"])
            added.append(t.get("title", subject))
        if done:
            try:
                g.relabel(done, add=(ADDED,), remove=(LABEL,))
            except Exception as e:  # noqa: BLE001 - it's remembered as seen anyway
                log.warning("email to-do: couldn't relabel %s's emails: %s", person, e)
    if added:
        svc.store.log_event("mailtodo", f"added {len(added)}: " + "; ".join(added)[:300])
    return added
