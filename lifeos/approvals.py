"""Approval queue: risky actions wait for a tap on your phone.

- Every request says exactly what will happen (for emails: who it goes to, the subject and the text).
- Requests expire: home security actions after 15 minutes, everything else after 24 hours.
- The same request is never queued twice, and an approval can only ever run once.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from .tools import Services, run_tool

SECURITY_TTL_MIN = 15
DEFAULT_TTL_MIN = 24 * 60


def sign(secret: str, pid: str, decision: str) -> str:
    return hmac.new(secret.encode(), f"{pid}:{decision}".encode(), hashlib.sha256).hexdigest()[:32]


def verify(secret: str, pid: str, decision: str, token: str) -> bool:
    return bool(secret) and hmac.compare_digest(sign(secret, pid, decision), token or "")


def describe(svc: Services, tool: str, args: dict) -> tuple[str, int]:
    """A plain-English description of the action, and how long the request stays valid (minutes)."""
    if tool == "call_home_service":
        service = args.get("service", "")
        target = args.get("entity_id") or (args.get("data") or {}).get("entity_id") or ""
        words = {"lock.unlock": "Unlock", "lock.open": "Open", "alarm_control_panel.alarm_disarm": "Disarm the alarm",
                 "cover.open_cover": "Open", "valve.open_valve": "Open valve"}.get(service, f"Run {service}")
        return f"{words} {target}".strip(), SECURITY_TTL_MIN
    if tool == "send_draft":
        summary = args.get("summary") or "Send an email"
        try:
            d = svc.google.get(args.get("person")).get_draft(args["draft_id"])
            body = (d.get("body") or "").strip()
            if len(body) > 600:
                body = body[:600].rstrip() + "…"
            return f"{summary}\nTo: {d.get('to', '?')}\nSubject: {d.get('subject', '')}\n\n{body}", DEFAULT_TTL_MIN
        except Exception:
            return summary, DEFAULT_TTL_MIN
    if tool == "email_tradesperson":
        body = str(args.get("body", ""))[:800]
        return (f"Email {args.get('contact_name', '')} <{args.get('contact_email', '')}>\nSubject: {args.get('subject', '')}"
                f"\n\n{body}"), DEFAULT_TTL_MIN
    summary = args.get("summary") or f"{tool.replace('_', ' ')}: {json.dumps(args)[:200]}"
    return summary, DEFAULT_TTL_MIN


def request_approval(svc: Services, tool: str, args: dict, reason: str = "") -> str:
    description, ttl = describe(svc, tool, args)
    if reason and reason not in description:
        description = f"{reason}\n{description}"
    pid = svc.store.add_pending(tool, args, description, ttl_minutes=ttl)
    s = svc.settings
    base = f"{s.public_url}/approvals/{pid}"
    if svc.notifier:
        svc.notifier.push(
            description,
            title="Needs your OK",
            priority=4,
            tags=["question"],
            actions=[
                {"action": "http", "label": "Approve",
                 "url": f"{base}/approve?token={sign(s.signing_secret, pid, 'approve')}"},
                {"action": "http", "label": "Reject",
                 "url": f"{base}/reject?token={sign(s.signing_secret, pid, 'reject')}"},
                {"action": "view", "label": "Open app", "url": f"{s.public_url}/app/#today"},
            ],
        )
    svc.store.log_event("approval_requested", f"{pid} {tool} {description[:300]}")
    return pid


def decide(svc: Services, pid: str, decision: str) -> dict:
    item = svc.store.get_pending(pid)
    if not item:
        return {"ok": False, "error": "This request no longer exists."}
    if item["status"] == "expired":
        return {"ok": False, "error": "This request has expired. Ask again if it is still needed."}
    if item["status"] != "pending":
        return {"ok": False, "error": f"Already {item['status']}."}
    if decision == "reject":
        svc.store.decide(pid, "rejected", only_if_pending=True)
        return {"ok": True, "status": "rejected"}
    # Claim it atomically so two taps (or phone + app) can never run it twice.
    if not svc.store.decide(pid, "running", only_if_pending=True):
        return {"ok": False, "error": "Already being handled."}
    try:
        result = run_tool(item["tool"], item["args"], svc)
        svc.store.decide(pid, "approved", json.dumps(result, default=str)[:4000])
        return {"ok": True, "status": "approved", "result": result}
    except Exception as e:  # report failure back rather than crash the web request
        svc.store.decide(pid, "failed", str(e))
        return {"ok": False, "status": "failed", "error": str(e)}
