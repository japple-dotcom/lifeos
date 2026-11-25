"""Live test day: every Gmail, Tasks and Calendar feature tried against the real accounts, nothing sent.

Settings > Check everything > "Live test with our real accounts". For each grown-up whose Google account is
connected, Life OS:
- Tasks: makes its own list "Life OS live test", adds a to-do, reads it back, changes its notes (as the Gemini
  bridge does), ticks it, then deletes the to-do and the list. It reads the main list Gemini adds to.
- Calendar: adds a 15-minute "Life OS live test (safe to delete)" entry at 3am in 30 days' time, then deletes it,
  and reads the merged calendars.
- Gmail (if allowed): searches the last two days (reading one subject line only), checks it can label emails,
  makes a draft with no recipient, checks it's there, then deletes it. Nothing is ever sent.
Then Home Assistant (reads the house) and the assistant (one tiny question, if switched on).

Every step is timed, and the report says what worked and, in plain English, what didn't. Everything the test makes
is removed again, even when a later step fails. The report is kept so it can be compared next time.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger("lifeos.livetest")
TEST_LIST = "Life OS live test"
TITLE = "Life OS live test (safe to delete)"


def _plain(e: Exception) -> str:
    msg = str(e) or type(e).__name__
    low = msg.lower()
    if "invalid_grant" in low or "token has been expired" in low or "revoked" in low:
        return "Google says the sign-in has expired. Reconnect this account in Settings > Google accounts."
    if "insufficient" in low and "scope" in low or "403" in low and "permission" in low:
        return "Google didn't give Life OS permission for this. Reconnect the account and tick every box."
    if "has not been used in project" in low or "accessnotconfigured" in low or "api has not been used" in low:
        return "This Google API isn't switched on in your Google Cloud project (README step 4)."
    if "quota" in low or "rate limit" in low or "429" in low:
        return "Google is limiting requests just now. Try again in a few minutes."
    if "connecterror" in low or "timed out" in low or "name or service not known" in low:
        return "Couldn't reach Google. Check this computer's internet connection."
    return msg[:200]


def _fail(message: str):
    raise RuntimeError(message)


class _Run:
    def __init__(self):
        self.steps: list[dict] = []

    def step(self, area: str, what: str, fn):
        t0 = time.monotonic()
        try:
            out = fn()
            self.steps.append({"area": area, "step": what, "ok": True, "detail": out if isinstance(out, str) else "",
                               "ms": int((time.monotonic() - t0) * 1000)})
            return out, True
        except Exception as e:
            log.warning("live test %s / %s: %s", area, what, e)
            self.steps.append({"area": area, "step": what, "ok": False, "detail": _plain(e),
                               "ms": int((time.monotonic() - t0) * 1000)})
            return None, False


def _tasks(r: _Run, g, who: str) -> None:
    area = f"{who}: Google Tasks"
    made, ok = r.step(area, "Add a to-do to a test list", lambda: g.add_task(TEST_LIST, TITLE, "Made by the live test."))
    if not ok:
        return
    tid = (made or {}).get("id", "")
    try:
        r.step(area, "Read it back", lambda: "found" if any(t["id"] == tid for t in g.list_tasks(TEST_LIST))
               else _fail("The to-do wasn't there when read back."))
        r.step(area, "Change its notes (as the Gemini bridge does)",
               lambda: g.update_task(TEST_LIST, tid, notes="Made by the live test.\n--- Life OS ---\nChecked."))
        r.step(area, "Tick it off", lambda: g.complete_task(TEST_LIST, tid))
    finally:
        def clean():
            g.delete_task(TEST_LIST, tid)
            g.delete_list(TEST_LIST)
        r.step(area, "Delete the test to-do and list", clean)
    r.step(area, "Read the main list (where Gemini adds to-dos)", lambda: f"{len(g.list_tasks('@default'))} open")


def _calendar(r: _Run, g, who: str, tz) -> None:
    area = f"{who}: Google Calendar"
    start = (datetime.now(tz) + timedelta(days=30)).replace(hour=3, minute=0, second=0, microsecond=0)
    ev, ok = r.step(area, "Add a test entry in 30 days (3am)", lambda: g.add_event(
        TITLE, start.strftime("%Y-%m-%dT%H:%M:%S"), (start + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S"),
        description="Made by the Life OS live test. Safe to delete."))
    if ok:
        r.step(area, "Delete the test entry", lambda: g.delete_event((ev or {}).get("id", "")))


def _gmail(r: _Run, g, who: str) -> None:
    area = f"{who}: Gmail"
    if not getattr(g, "has_gmail", False):
        r.steps.append({"area": area, "step": "Email not shared with Life OS", "ok": True,
                        "detail": "Skipped: this account was connected without email (that's fine).", "ms": 0})
        return
    r.step(area, "Search the last two days", lambda: f"{len(g.search_mail('newer_than:2d', limit=3))} found")
    if hasattr(g, "_label_id"):
        r.step(area, "Labels work (Life OS/Handled)", lambda: "ready" if g._label_id("Life OS/Handled")
               else _fail("Gmail wouldn't make the label."))
    d, ok = r.step(area, "Make a draft with no recipient", lambda: g.create_draft(
        "", TITLE, "This draft was made by the Life OS live test and is deleted straight away. Nothing is sent."))
    if ok:
        did = (d or {}).get("id", "")
        try:
            r.step(area, "Check the draft is there", lambda: "there" if g.draft_exists(did)
                   else _fail("The draft wasn't found."))
        finally:
            r.step(area, "Delete the draft", lambda: g.delete_draft(did))


def run(svc, agent=None, ask_ai: bool = False) -> dict:
    from . import scheduling
    r = _Run()
    if not svc.google:
        r.steps.append({"area": "Google", "step": "Connected accounts", "ok": False,
                        "detail": "No Google account is connected yet (Settings > Google accounts).", "ms": 0})
    else:
        tz = scheduling._tz(svc.settings)
        for acc in svc.google.status():
            if not acc["connected"]:
                r.steps.append({"area": f"{acc['name']}: Google", "step": "Connected", "ok": False,
                                "detail": f"{acc['name']} hasn't connected Google yet (Settings > Google accounts).", "ms": 0})
                continue
            g = svc.google.get(acc["person"])
            _tasks(r, g, acc["name"])
            _calendar(r, g, acc["name"], tz)
            _gmail(r, g, acc["name"])
        r.step("Google", "Read everyone's calendars together", lambda: f"{len(svc.google.events_all(days=7))} entries this week")
    if svc.ha:
        r.step("Home Assistant", "Read the house", lambda: f"{len(svc.ha.states())} things")
    if ask_ai and agent is not None and getattr(agent, "provider", None) is not None:
        def ai():
            from . import aierrors
            try:
                reply = agent.provider.chat("Reply with the single word: ready", [], "Are you there?", lambda *_: ("", True))
            except Exception as e:
                raise RuntimeError(aierrors.explain(e)) from e
            return (reply or "").strip()[:40]
        r.step("Assistant", f"{agent.active_provider_name().title()} answers", ai)
    report = {"when": datetime.now().isoformat(timespec="seconds"), "steps": r.steps,
              "ok": sum(1 for s in r.steps if s["ok"]), "failed": sum(1 for s in r.steps if not s["ok"]),
              "sent": 0}
    svc.store.put("livetest", "last", report)
    svc.store.log_event("live_test", f"{report['ok']} ok, {report['failed']} failed")
    return report


def last(svc) -> dict | None:
    rec = svc.store.get("livetest", "last")
    if not rec:
        return None
    return {k: v for k, v in rec.items() if k not in ("id", "at", "updated_at")}


def text(report: dict) -> str:
    lines = [f"Life OS live test, {report['when'].replace('T', ' ')}: {report['ok']} worked, {report['failed']} didn't. "
             "Nothing was sent."]
    area = None
    for s in report["steps"]:
        if s["area"] != area:
            area = s["area"]
            lines.append(f"\n{area}")
        lines.append(f"  {'OK ' if s['ok'] else 'NO '} {s['step']}{(': ' + s['detail']) if s['detail'] else ''}")
    return "\n".join(lines)
