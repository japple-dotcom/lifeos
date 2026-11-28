from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import json

import pytest

from conftest import FakeGoogle, FakeNotifier, ScriptedClaude, text, tool_use
from lifeos import approvals
from lifeos.agent import Agent
from lifeos.app import create_app
from lifeos.tags import handle_tag
from lifeos.tools import ALLOW, APPROVE, DENY, home_service_policy


def test_policy_allows_lights_queues_unlock_denies_unknown(svc):
    assert home_service_policy({"service": "light.turn_off"}, svc) == ALLOW
    assert home_service_policy({"service": "lock.unlock"}, svc) == APPROVE
    assert home_service_policy({"service": "homeassistant.restart"}, svc) == DENY


def test_agent_runs_tool_then_answers(svc):
    claude = ScriptedClaude([
        ([tool_use("1", "add_task", {"list_name": "Shopping", "title": "Bin bags"})], "tool_use"),
        ([text("Added bin bags to Shopping.")], "end_turn"),
    ])
    reply = Agent(svc, client=claude).run("we're out of bin bags")
    assert reply == "Added bin bags to Shopping."
    assert svc.google.me.tasks[0][:2] == ("Shopping", "Bin bags")
    # second call carries the tool result back to Claude
    last = claude.calls[1]["messages"][2]["content"][0]
    assert last["type"] == "tool_result" and last["tool_use_id"] == "1" and not last["is_error"]


def test_agent_queues_risky_action_and_does_not_run_it(svc):
    claude = ScriptedClaude([
        ([tool_use("1", "call_home_service", {"service": "lock.unlock", "entity_id": "lock.front"})], "tool_use"),
        ([text("Asked you to approve unlocking.")], "end_turn"),
    ])
    Agent(svc, client=claude).run("let the cleaner in")
    assert svc.ha.calls == []
    assert len(svc.store.list_pending()) == 1
    push = svc.notifier.pushes[-1]
    assert push["title"] == "Needs your OK" and [a["label"] for a in push["actions"]] == ["Approve", "Reject", "Open app"]


def test_agent_refuses_denied_service(svc):
    claude = ScriptedClaude([
        ([tool_use("1", "call_home_service", {"service": "homeassistant.restart"})], "tool_use"),
        ([text("I can't do that.")], "end_turn"),
    ])
    Agent(svc, client=claude).run("restart home assistant")
    result = claude.calls[1]["messages"][2]["content"][0]
    assert result["is_error"] and "Refused" in result["content"]
    assert svc.ha.calls == []


def test_approval_flow_executes_on_approve(svc):
    pid = approvals.request_approval(svc, "send_draft", {"draft_id": "d1", "summary": "Reply to school"})
    approve_url = svc.notifier.pushes[-1]["actions"][0]["url"]
    token = parse_qs(urlparse(approve_url).query)["token"][0]
    assert approvals.verify("test-secret", pid, "approve", token)
    assert not approvals.verify("test-secret", pid, "reject", token)
    assert approvals.decide(svc, pid, "approve")["status"] == "approved"
    assert svc.google.me.drafts_sent == ["d1"]
    assert approvals.decide(svc, pid, "approve")["error"] == "Already approved."


def test_nfc_tag_runs_actions(svc):
    out = handle_tag(svc, "dishwasher", who="Sam")
    assert out["ok"] and out["done"] == ["log_chore", "ha_service"]
    assert svc.store.recent_chores()[0]["chore"] == "Empty dishwasher"
    assert svc.ha.calls == [("light.turn_off", {"entity_id": "light.hall_reminder"})]
    assert handle_tag(svc, "nope")["ok"] is False


def test_http_endpoints(svc):
    claude = ScriptedClaude([([text("Nothing urgent today.")], "end_turn")])
    client = TestClient(create_app(svc, Agent(svc, client=claude), run_scheduler=False))
    health = client.get("/health").json()
    assert health["ok"] is False and any("https" in p for p in health["problems"])  # test URL is http
    assert client.post("/ask", json={"text": "anything today?"}).status_code == 401
    r = client.post("/ask", json={"text": "anything today?"}, headers={"X-LifeOS-Key": "test-key"})
    assert r.json()["reply"] == "Nothing urgent today."
    r = client.get("/nfc/kitchen-milk?key=test-key")
    assert r.json()["say"] == "Milk added to shopping."
    pid = approvals.request_approval(svc, "send_draft", {"draft_id": "d9", "summary": "x"})
    assert client.post(f"/approvals/{pid}/approve?token=wrong").status_code == 403
    tok = approvals.sign("test-secret", pid, "reject")
    assert "Cancelled" in client.post(f"/approvals/{pid}/reject?token={tok}").text


# ---------- Gemini ----------
from types import SimpleNamespace  # noqa: E402

from google.genai import types  # noqa: E402

from lifeos.providers import GeminiProvider  # noqa: E402


class ScriptedGemini:
    """Mimics genai.Client().models.generate_content with fixed replies (lists of Parts)."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        parts = self.replies.pop(0)
        return SimpleNamespace(candidates=[SimpleNamespace(content=types.Content(role="model", parts=parts))])


def g_call(name, args, id_="c1"):
    return types.Part(function_call=types.FunctionCall(name=name, args=args, id=id_))


def test_gemini_runs_tools_through_same_policy(svc):
    gem = ScriptedGemini([
        [g_call("add_task", {"list_name": "Shopping", "title": "Eggs"}),
         g_call("call_home_service", {"service": "lock.unlock", "entity_id": "lock.front"}, "c2")],
        [types.Part(text="thinking...", thought=True), types.Part(text="Eggs added; unlock needs your OK.")],
    ])
    agent = Agent(svc, provider=GeminiProvider(client=gem, model="gemini-3.7-flash", model_deep="gemini-3.7-flash"))
    reply = agent.run("add eggs and let the cleaner in")
    assert reply == "Eggs added; unlock needs your OK."  # thought text is not shown
    assert svc.google.me.tasks[0][:2] == ("Shopping", "Eggs")
    assert svc.ha.calls == [] and len(svc.store.list_pending()) == 1  # same approval rule as Claude
    second = gem.calls[1]["contents"]
    assert second[1].role == "model"  # model turn sent back whole (thought signatures kept)
    fr = second[2].parts
    assert fr[0].function_response.name == "add_task" and fr[0].function_response.id == "c1"
    assert "queued_for_approval" in fr[1].function_response.response["result"]
    decl_names = [d.name for d in gem.calls[0]["config"].tools[0].function_declarations]
    assert "add_task" in decl_names and gem.calls[0]["config"].automatic_function_calling.disable


def test_switch_provider_is_persisted(svc):
    agent = Agent(svc, client=ScriptedClaude([]))
    assert agent.active_provider_name() == "claude"
    agent.switch_provider("gemini", provider=GeminiProvider(client=ScriptedGemini([[types.Part(text="hi")]])))
    assert agent.active_provider_name() == "gemini"
    assert agent.run("hello") == "hi"
    assert "_provider" not in __import__("lifeos.agent", fromlist=["x"]).build_system(svc).split("Saved household facts:")[1].split("\n")[0]


# ---------- people, accounts and the phone app ----------
from lifeos import sessions  # noqa: E402


def test_calendars_are_merged_and_deduplicated(svc):
    events = svc.google.events_all()
    assert [e["summary"] for e in events] == ["Dentist", "Swimming"]  # sorted by time
    swim = events[1]
    assert swim["who"] == ["Me", "My wife"]  # same invite on both calendars appears once


def test_person_routing_and_gmail_permission(svc):
    from lifeos.tools import run_tool
    run_tool("add_task", {"list_name": "Kids", "title": "PE kit", "person": "wife"}, svc)
    assert svc.google.wife.tasks[0][1] == "PE kit" and svc.google.me.tasks == []
    claude = ScriptedClaude([
        ([tool_use("1", "search_email", {"query": "is:unread", "person": "wife"})], "tool_use"),
        ([text("Her email isn't connected.")], "end_turn"),
    ])
    Agent(svc, client=claude).run("check my wife's email")
    result = claude.calls[1]["messages"][2]["content"][0]
    assert result["is_error"] and "Gmail" in result["content"]
    system = claude.calls[0]["system"]
    assert "- wife: My wife, Google connected, Gmail not allowed" in system


def test_unconnected_person_gives_clear_message(svc):
    svc.google.people.pop("wife")
    import pytest
    with pytest.raises(RuntimeError, match="My wife's Google account is not connected"):
        svc.google.get("wife")


def test_session_cookie_signing():
    c = sessions.make("s3cret", "wife")
    assert sessions.read("s3cret", c) == "wife"
    assert sessions.read("other", c) is None
    assert sessions.read("s3cret", c.replace("wife", "me")) is None
    assert sessions.read("s3cret", None) is None


def _app_client(svc, replies=None):
    claude = ScriptedClaude(replies or [([text("Done.")], "end_turn")])
    return TestClient(create_app(svc, Agent(svc, client=claude), run_scheduler=False)), claude


def test_phone_app_sign_in_and_screens(svc):
    client, claude = _app_client(svc, [([text("Milk added.")], "end_turn")])
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/people").json() == [{"id": "me", "name": "Me"}, {"id": "wife", "name": "My wife"}]
    assert client.post("/api/login", json={"key": "wrong", "person": "wife"}).status_code == 401
    r = client.post("/api/login", json={"key": "test-key", "person": "wife"})
    assert r.status_code == 200 and sessions.COOKIE in r.cookies
    me = client.get("/api/me").json()
    assert me["person"] == "wife" and me["ai"] == "claude"

    # Today: merged calendar, due tasks from the household lists, approvals
    svc.google.me.add_task("Shopping", "Milk", due="2000-01-01")
    svc.google.me.add_task("Home maintenance", "Bleed the radiators", due="2000-01-01")
    approvals.request_approval(svc, "send_draft", {"draft_id": "d1", "summary": "Reply to school"})
    t = client.get("/api/today").json()
    assert t["name"] == "My wife" and len(t["events"]) == 2
    assert t["next_up"][0]["title"] == "Bleed the radiators" and t["next_up"][0]["days"] < 0  # to-dos, not shopping
    assert t["tasks"] == []  # not listed twice
    assert t["approvals"][0]["reason"].startswith("Reply to school\nTo: office@school.example")
    pid = t["approvals"][0]["id"]
    assert client.post(f"/api/approvals/{pid}/approve").json()["status"] == "approved"

    # Ask: the AI is told who is asking
    assert client.post("/api/ask", json={"text": "add milk"}).json()["reply"] == "Milk added."
    assert "[Asked by My wife (person id: wife)]" in claude.calls[0]["messages"][0]["content"]

    # Lists
    assert client.post("/api/tasks", json={"list_name": "Shopping", "title": "Eggs"}).json()["ok"]
    items = client.get("/api/tasks?list_name=Shopping").json()["tasks"]
    assert [i["title"] for i in items] == ["Milk", "Eggs"]
    client.post("/api/tasks/complete", json={"list_name": "Shopping", "task_id": "t2"})
    assert svc.google.me.completed == [("Shopping", "t2")]

    # Home buttons
    home = client.get("/api/home").json()
    assert home["actions"][0]["label"] == "Leaving home" and home["states"][0]["state"] == "locked"
    assert client.post("/api/home/action/away").json()["ok"]
    assert svc.ha.calls[-1] == ("scene.turn_on", {"entity_id": "scene.away"})

    # Accounts and sign out
    acc = client.get("/api/accounts").json()["accounts"]
    assert {a["person"]: a["gmail"] for a in acc} == {"me": True, "wife": False}
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_app_files_are_served(svc):
    client, _ = _app_client(svc)
    assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
    page = client.get("/app/")
    assert page.status_code == 200 and 'lang="en-GB"' in page.text
    manifest = client.get("/app/manifest.webmanifest").json()
    assert manifest["display"] == "standalone" and any(i["purpose"] == "maskable" for i in manifest["icons"])
    assert client.get("/app/sw.js").status_code == 200
    assert client.get("/app/icons/icon-512.png").status_code == 200


def test_google_connect_flow(svc, tmp_path, monkeypatch):
    from lifeos import oauth

    class FakeFlow:
        code_verifier = "verifier-123"
        credentials = SimpleNamespace(to_json=lambda: '{"token": "x"}')

        def __init__(self, scopes, state):
            self.scopes, self.state = scopes, state

        def authorization_url(self, **kw):
            FakeFlow.last_kw = kw
            return f"https://accounts.google.com/o/oauth2/auth?state={self.state}", self.state

        def fetch_token(self, code):
            FakeFlow.fetched = (code, self.code_verifier)

    monkeypatch.setattr(oauth, "_flow", lambda settings, scopes, state=None: FakeFlow(scopes, state))
    svc.settings.google_token_file = str(tmp_path / "google_token.json")
    url = oauth.start(svc.settings, "wife", include_gmail=False)
    state = url.split("state=")[1]
    assert FakeFlow.last_kw["access_type"] == "offline"
    svc.google._factory = lambda path: FakeGoogle()
    assert oauth.finish(svc.settings, svc.google, state, "code-abc") == "wife"
    assert FakeFlow.fetched == ("code-abc", "verifier-123")
    assert (tmp_path / "google_token_wife.json").exists() and "wife" in svc.google.people
    import pytest
    with pytest.raises(ValueError):
        oauth.finish(svc.settings, svc.google, state, "code-abc")  # one use only


# ---------- v0.4 review fixes ----------
def test_policy_scripts_need_approval_and_notify_is_refused(svc):
    assert home_service_policy({"service": "script.unlock_back_door"}, svc) == APPROVE
    assert home_service_policy({"service": "notify.mobile_app_phone"}, svc) == DENY
    assert home_service_policy({"service": "lock.open"}, svc) == APPROVE
    assert home_service_policy({"service": "light"}, svc) == DENY
    assert home_service_policy({"service": "Light.Turn_On"}, svc) == ALLOW


def test_approval_expiry_dedupe_and_single_execution(svc):
    a = approvals.request_approval(svc, "send_draft", {"draft_id": "d1", "summary": "Reply"})
    b = approvals.request_approval(svc, "send_draft", {"draft_id": "d1", "summary": "Reply"})
    assert a == b and len(svc.store.list_pending()) == 1
    assert approvals.decide(svc, a, "approve")["status"] == "approved"
    assert approvals.decide(svc, a, "approve")["ok"] is False
    assert svc.google.me.drafts_sent == ["d1"]  # ran exactly once
    # an unlock request expires after 15 minutes
    pid = approvals.request_approval(svc, "call_home_service", {"service": "lock.unlock", "entity_id": "lock.front"})
    item = svc.store.get_pending(pid)
    assert item["reason"] == "Unlock lock.front"
    svc.store._exec("UPDATE pending_actions SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (pid,))
    r = approvals.decide(svc, pid, "approve")
    assert r["ok"] is False and "expired" in r["error"] and svc.ha.calls == []


def test_email_approval_shows_the_email(svc):
    pid = approvals.request_approval(svc, "send_draft", {"draft_id": "d7", "summary": "Reply to school"})
    reason = svc.store.get_pending(pid)["reason"]
    assert "To: office@school.example" in reason and "Ava can go" in reason
    push = svc.notifier.pushes[-1]
    assert "Friday trip" in push["message"] and [a["label"] for a in push["actions"]] == ["Approve", "Reject", "Open app"]


def test_opening_an_approval_link_does_not_act(svc):
    client, _ = _app_client(svc)
    pid = approvals.request_approval(svc, "send_draft", {"draft_id": "d3", "summary": "Reply"})
    tok = approvals.sign("test-secret", pid, "approve")
    page = client.get(f"/approvals/{pid}/approve?token={tok}")
    assert page.status_code == 200 and "<form method='post'>" in page.text
    assert svc.google.me.drafts_sent == []  # nothing happened yet
    assert "Done" in client.post(f"/approvals/{pid}/approve?token={tok}").text
    assert svc.google.me.drafts_sent == ["d3"]


def test_ask_remembers_the_conversation(svc):
    client, claude = _app_client(svc, [([text("Shall I add eggs too?")], "end_turn"), ([text("Added eggs.")], "end_turn")])
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    client.post("/api/ask", json={"text": "add milk"})
    assert client.post("/api/ask", json={"text": "yes please"}).json()["reply"] == "Added eggs."
    second = claude.calls[1]["messages"][:3]  # the list keeps growing after the call; look at what was sent
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == "Shall I add eggs too?"
    turns = client.get("/api/conversation").json()["turns"]
    assert turns[0] == {"role": "user", "text": "add milk"} and len(turns) == 4
    client.post("/api/conversation/reset")
    assert client.get("/api/conversation").json()["turns"] == []


def test_gemini_receives_history(svc):
    gem = ScriptedGemini([[types.Part(text="ok")]])
    agent = Agent(svc, provider=GeminiProvider(client=gem, model="m", model_deep="m"))
    agent.run("yes", history=[{"role": "user", "text": "add milk?"}, {"role": "assistant", "text": "Sure?"}])
    roles = [c.role for c in gem.calls[0]["contents"]]
    assert roles == ["user", "model", "user"]


def test_login_is_rate_limited_and_cross_site_posts_blocked(svc):
    client, _ = _app_client(svc)
    for _ in range(5):
        assert client.post("/api/login", json={"key": "nope", "person": "me"}).status_code == 401
    assert client.post("/api/login", json={"key": "test-key", "person": "me"}).status_code == 429
    client2, _ = _app_client(svc)
    r = client2.post("/api/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_daily_cost_cap(svc):
    svc.settings.max_runs_per_day = 2
    claude = ScriptedClaude([([text("a")], "end_turn"), ([text("b")], "end_turn")])
    agent = Agent(svc, client=claude)
    agent.run("one")
    agent.run("two")
    assert "limit of 2" in agent.run("three") and len(claude.calls) == 2


def test_google_wrapper_skips_duplicate_tasks_and_uses_uk_days():
    from datetime import datetime
    from unittest.mock import MagicMock
    from zoneinfo import ZoneInfo
    from lifeos.integrations.google import Google
    apis = {k: MagicMock() for k in ("tasks", "calendar", "gmail")}
    apis["tasks"].tasklists().list().execute.return_value = {"items": [{"title": "Shopping", "id": "L1"}]}
    apis["tasks"].tasks().list().execute.return_value = {"items": [{"id": "t1", "title": "Milk"}]}
    g = Google("x", services=apis)
    assert g.add_task("Shopping", "  milk ")["duplicate"] is True
    apis["tasks"].tasks().insert.assert_not_called()
    apis["calendar"].events().list().execute.return_value = {"items": []}
    g.events(days=1, start=None)
    kw = apis["calendar"].events().list.call_args.kwargs
    start = datetime.fromisoformat(kw["timeMin"])
    assert start.utcoffset() == datetime.now(ZoneInfo("Europe/London")).utcoffset() and start.hour == 0


def test_notifier_publishes_json_with_any_characters():
    from lifeos.integrations.notify import Notifier
    sent = {}

    class C:
        def post(self, url, json=None, headers=None):
            sent.update(url=url, json=json)
            return SimpleNamespace(raise_for_status=lambda: None)

    Notifier("https://ntfy.example", "topic", client=C()).push("Ready ✅", title="Priya’s list 🛒",
                                                                actions=[{"label": "Approve", "url": "https://x"}])
    assert sent["url"] == "https://ntfy.example" and sent["json"]["title"] == "Priya’s list 🛒"
    assert sent["json"]["actions"][0] == {"action": "http", "label": "Approve", "url": "https://x", "clear": True, "method": "POST"}


def test_personal_lists_in_app(svc):
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    svc.google.wife.add_task("Gym", "Book class")
    data = client.get("/api/tasks?list_name=Gym&owner=mine").json()
    assert [g["owner"] for g in data["groups"]] == ["shared", "mine"] and "Gym" in data["groups"][1]["lists"]
    assert data["tasks"][0]["title"] == "Book class"
    client.post("/api/tasks?owner=mine", json={"list_name": "Gym", "title": "Swim kit"})
    assert svc.google.wife.tasks[-1][:2] == ("Gym", "Swim kit") and svc.google.me.tasks == []


def test_mark_email_handled_tool(svc):
    from lifeos.tools import run_tool
    assert run_tool("mark_email_handled", {"message_ids": ["m1", "m2"], "person": "me"}, svc)["labelled"] == 2
    assert svc.google.me.handled == ["m1", "m2"]


def test_setup_problems_are_reported(svc):
    from lifeos.config import setup_problems
    svc.settings.api_key = "change-me"
    probs = setup_problems(svc.settings)
    assert any("household key" in p for p in probs) and any("https" in p for p in probs)


# ---------- v0.5: renewals, chores, briefs, in-app notifications, backups ----------
from datetime import date as _date  # noqa: E402

from lifeos import household  # noqa: E402


def test_renewal_reminds_once_and_rolls_forward(svc):
    svc.store.add_renewal("Car insurance", "2027-01-10")
    svc.store.add_renewal("Passport", "2030-05-01", repeat_months=0)
    did = household.check_renewals(svc, today=_date(2026, 12, 1))  # 40 days before: inside the 42-day window
    assert did == ["reminded Car insurance"]
    task = svc.google.me.tasks[-1]
    assert task[0] == "Admin and renewals" and task[1] == "Renew Car insurance (due 10 Jan 2027)" and task[3] == "2026-12-27"
    assert svc.notifier.pushes[-1]["title"] == "Renewal coming up"
    assert household.check_renewals(svc, today=_date(2026, 12, 2)) == []  # never twice
    did = household.check_renewals(svc, today=_date(2027, 1, 11))  # passed: next year's date, reminder reset
    assert did == ["rolled Car insurance to 2028-01-10"]
    assert household.add_months(_date(2026, 1, 31), 1) == _date(2026, 2, 28)


def test_add_renewal_tool_updates_by_name(svc):
    from lifeos.tools import run_tool
    rid = run_tool("add_renewal", {"name": "MOT", "due_date": "2027-03-01"}, svc)["id"]
    assert run_tool("add_renewal", {"name": "mot", "due_date": "2027-03-08"}, svc)["updated"] == rid
    assert [r["due_date"] for r in svc.store.renewals()] == ["2027-03-08"]
    import pytest
    with pytest.raises(ValueError):
        run_tool("add_renewal", {"name": "Bad", "due_date": "next March"}, svc)


def test_chores_pocket_money_flow(svc):
    svc.settings.behaviour["household"]["kids"] = ["Ava", "Leo"]
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    board = client.get("/api/chores").json()["kids"]
    assert [k["kid"] for k in board] == ["Ava", "Leo"] and any(c["id"] == "bins" for c in board[0]["chores"])
    first = client.post("/api/chores/done", json={"kid": "Ava", "chore_id": "bins"}).json()
    client.post("/api/chores/done", json={"kid": "Ava", "chore_id": "bed"})
    assert client.post("/api/chores/done", json={"kid": "Ava", "chore_id": "nope"}).status_code == 400
    ava = client.get("/api/chores").json()["kids"][0]
    assert ava["owed"] == 0.7 and len(ava["done_this_week"]) == 2
    client.post(f"/api/chores/{first['id']}/undo")
    assert client.get("/api/chores").json()["kids"][0]["owed"] == 0.2
    assert client.post("/api/chores/paid", json={"kid": "Ava"}).json()["paid"] == 0.2
    assert client.get("/api/chores").json()["kids"][0]["owed"] == 0
    # NFC tag in a child's room names the child and earns the chore's value
    svc.settings.behaviour["tags"]["ava-bed"] = {"actions": [{"log_chore": {"chore": "Make bed", "who": "Ava"}}]}
    handle_tag(svc, "ava-bed", who="Sam")
    assert svc.store.unpaid_totals() == {"Ava": 0.2}


def test_routine_output_appears_on_today(svc):
    from lifeos.scheduler import run_loop
    claude = ScriptedClaude([([text("Bins tonight. Swimming 17:30.")], "end_turn"), ([text("No action needed.")], "end_turn")])
    agent = Agent(svc, client=claude)
    run_loop(agent, "morning_brief", {"prompt": "brief"})
    run_loop(agent, "inbox_triage", {"prompt": "triage"})  # quiet result is not stored
    assert svc.store.latest_brief()["text"] == "Bins tonight. Swimming 17:30."
    client = TestClient(create_app(svc, agent, run_scheduler=False))
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    today = client.get("/api/today").json()
    assert today["brief"]["name"] == "morning_brief" and today["renewals"] == []


def test_web_push_with_approve_buttons(svc, tmp_path):
    from lifeos.integrations.webpush import FanOut, WebPusher
    delivered = []

    def fake_send(sub, payload):
        delivered.append((sub["endpoint"], json.loads(payload)))
        return 410 if "gone" in sub["endpoint"] else 201

    pusher = WebPusher(svc.store, str(tmp_path / "vapid.pem"), sender=fake_send)
    assert len(pusher.public_key) == 87 and (tmp_path / "vapid.pem").exists()
    assert WebPusher(svc.store, str(tmp_path / "vapid.pem")).public_key == pusher.public_key  # key reused
    svc.store.save_subscription("me", {"endpoint": "https://push.example/a", "keys": {"p256dh": "x", "auth": "y"}})
    svc.store.save_subscription("wife", {"endpoint": "https://push.example/gone", "keys": {"p256dh": "x", "auth": "y"}})
    svc.notifier = FanOut(pusher, svc.notifier)
    approvals.request_approval(svc, "send_draft", {"draft_id": "d1", "summary": "Reply to school"})
    data = delivered[0][1]
    assert data["title"] == "Needs your OK" and [a["action"] for a in data["actions"]] == ["approve", "reject"]
    assert "/approvals/" in data["actions"][0]["url"] and data["url"].endswith("/app/#today")
    assert [s["endpoint"] for s in svc.store.subscriptions()] == ["https://push.example/a"]  # 410 removed
    assert svc.notifier.channels[1].pushes  # ntfy still got it too


def test_fanout_survives_one_channel_failing():
    from lifeos.integrations.webpush import FanOut

    class Broken:
        def push(self, *a, **k):
            raise RuntimeError("down")
    ok = FakeNotifier()
    FanOut(Broken(), ok).push("hello")
    assert ok.pushes[0]["message"] == "hello"


def test_push_endpoints(svc, tmp_path):
    from lifeos.integrations.webpush import WebPusher
    svc.pusher = WebPusher(svc.store, str(tmp_path / "v.pem"), sender=lambda sub, payload: 201)
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    assert client.get("/api/push/key").json()["key"] == svc.pusher.public_key
    assert client.post("/api/push/test").status_code == 400  # not subscribed yet
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}
    client.post("/api/push/subscribe", json=sub)
    assert client.post("/api/push/test").json()["sent"] == 1
    assert client.post("/api/push/subscribe", json={**sub, "endpoint": "http://evil"}).status_code == 400


def test_nightly_backup(svc, tmp_path):
    from lifeos.scheduler import housekeeping
    svc.settings.db_path = str(tmp_path / "lifeos.db")
    svc.store.log_chore("Bins", "Ava", 0.5)
    housekeeping(svc)
    backups = list((tmp_path / "backups").glob("lifeos-*.db"))
    assert len(backups) == 1
    import sqlite3
    assert sqlite3.connect(backups[0]).execute("SELECT chore FROM chore_log").fetchone()[0] == "Bins"


# ---------- v0.6: meals, quiet hours, wall display, doctor ----------
def test_meal_plan_adds_ingredients_once_and_skips_staples(svc):
    from lifeos.tools import run_tool
    d0 = household.today_local(svc.settings)
    r = run_tool("set_meal_plan", {"meals": [
        {"day": d0.isoformat(), "dinner": "Chicken fajitas", "ingredients": ["chicken thighs", "peppers", "tortillas", "salt"]},
        {"day": (d0 + __import__("datetime").timedelta(days=1)).isoformat(), "dinner": "Veggie curry",
         "ingredients": ["Peppers", "chickpeas", "coconut milk", "rice"]}]}, svc)
    assert r["added_to_shopping"] == ["chicken thighs", "peppers", "tortillas", "chickpeas", "coconut milk"]
    assert household.dinner_on(svc) == "Chicken fajitas"
    week = run_tool("get_meal_plan", {"days": 7}, svc)
    assert len(week) == 7 and week[1]["dinner"] == "Veggie curry" and week[2]["dinner"] == ""
    # planning the same thing again does not duplicate shopping items
    again = run_tool("set_meal_plan", {"meals": [{"day": d0.isoformat(), "dinner": "Chicken fajitas",
                                                  "ingredients": ["chicken thighs"]}]}, svc)
    assert again["added_to_shopping"] == []


def test_meals_in_the_app(svc):
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    days = client.get("/api/meals").json()["days"]
    assert len(days) == 7 and days[0]["dinner"] == ""
    r = client.post("/api/meals", json={"meals": [{"day": days[0]["day"], "dinner": "Fish pie"},
                                                  {"day": days[1]["day"], "dinner": ""}]}).json()
    assert r["changed"] == 1
    assert client.get("/api/today").json()["dinner"] == "Fish pie"
    assert client.post("/api/meals", json={"meals": [{"day": "soon", "dinner": "x"}]}).status_code == 400


def test_quiet_hours_hold_then_digest(svc):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    uk = ZoneInfo("Europe/London")
    assert household.in_quiet_hours(svc.settings, datetime(2026, 11, 24, 23, 0, tzinfo=uk))
    assert household.in_quiet_hours(svc.settings, datetime(2026, 11, 24, 6, 59, tzinfo=uk))
    assert not household.in_quiet_hours(svc.settings, datetime(2026, 11, 24, 7, 0, tzinfo=uk))
    inner = FakeNotifier()
    q = household.QuietHours(inner, svc)
    import lifeos.household as hh
    real = hh.in_quiet_hours
    hh.in_quiet_hours = lambda settings, now=None: True
    try:
        q.push("Renewal soon", title="Renewal coming up")
        q.push("Unlock?", title="Needs your OK", priority=4, actions=[{"label": "Approve", "url": "x"}])
        q.push("Leak!", title="Water leak", priority=5)
        assert [p["title"] for p in inner.pushes] == ["Needs your OK", "Water leak"]  # held the ordinary one
        assert q.flush() == 0  # still quiet
    finally:
        hh.in_quiet_hours = lambda settings, now=None: False
    try:
        assert q.flush() == 1 and inner.pushes[-1]["title"] == "Overnight (1)"
        assert "Renewal coming up: Renewal soon" in inner.pushes[-1]["message"]
    finally:
        hh.in_quiet_hours = real


def test_wall_display_is_read_only(svc):
    svc.settings.behaviour["household"]["kids"] = ["Ava"]
    svc.store.log_chore("Bins out", "Ava", 0.5)
    svc.store.set_meal(household.today_local(svc.settings).isoformat(), "Fish pie")
    client, _ = _app_client(svc)
    assert client.get("/api/wall").status_code == 401
    assert client.post("/api/login", json={"key": "test-key", "person": "display"}).status_code == 200
    wall = client.get("/api/wall").json()
    assert wall["dinner"] == "Fish pie" and wall["kids"] == [{"kid": "Ava", "owed": 0.5, "done": 1, "goals": []}]
    assert len(wall["events"]) == 2
    # the display can't do anything else
    assert client.get("/api/today").status_code == 401
    assert client.post("/api/chores/paid", json={"kid": "Ava"}).status_code == 401
    assert client.post("/api/ask", json={"text": "unlock the door"}).status_code == 401
    assert client.get("/app/wall.html").status_code == 200


def test_doctor_reports_missing_entities_and_accounts(svc, tmp_path):
    sys_path = __import__("sys").path
    sys_path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"))
    import doctor
    svc.settings.google_client_secret_file = str(tmp_path / "none.json")
    claude = ScriptedClaude([([text("OK")], "end_turn")])
    results = doctor.run(svc, Agent(svc, client=claude), check_url=False)
    text_out = "\n".join(f"{s} {st} {m}" for s, st, m in results)
    assert "Home Assistant connected" in text_out
    assert "don't exist in Home Assistant" in text_out and "scene.away" in text_out  # FakeHA only has the kitchen light
    assert "Google sign-in not set up" in text_out
    assert "Me: Google connected" in text_out and "Assistant ok Claude answered" in text_out


# ---------- v0.7: speed, demo mode ----------
def test_google_reads_are_cached_and_writes_clear_the_cache():
    from lifeos.accounts import _Serialised
    calls = []

    class Slow(FakeGoogle):
        def list_tasks(self, list_name, include_completed=False):
            calls.append(list_name)
            return super().list_tasks(list_name)

    g = _Serialised(Slow())
    g.list_tasks("Shopping")
    g.list_tasks("Shopping")
    assert calls == ["Shopping"]  # second read remembered
    g.add_task("Shopping", "Eggs")
    assert [t["title"] for t in g.list_tasks("Shopping")] == ["Eggs"] and calls == ["Shopping", "Shopping"]
    result = g.list_tasks("Shopping")
    result.append("tampered")
    assert len(g.list_tasks("Shopping")) == 1  # callers can't corrupt the remembered copy
    g.forget()
    g.list_tasks("Shopping")
    assert len(calls) == 3


def test_demo_mode_builds_and_answers():
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"))
    import demo
    app, svc = demo.build()
    client = TestClient(app)
    client.post("/api/login", json={"key": "demo", "person": "wife"})
    me = client.get("/api/me").json()
    assert me["demo"] is True and me["problems"] == [] and me["name"] == "Sue"
    reply = client.post("/api/ask", json={"text": "add eggs and bread to shopping"}).json()["reply"]
    assert reply == "Added Eggs and Bread to Shopping."


# ---------- v0.8: smart to-dos and projects ----------
def _todo_svc(svc):
    """svc with Google stand-ins that model completion, like the real Google Tasks."""
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"))
    import demo
    from lifeos.accounts import GoogleAccounts
    svc.google = GoogleAccounts(svc.settings, people={"me": demo.DemoGoogle("me", []), "wife": demo.DemoGoogle("wife", [])})
    return svc


def test_ai_sorts_a_todo_and_files_it(svc):
    from lifeos import todos
    _todo_svc(svc)
    due = (household.today_local(svc.settings) + __import__("datetime").timedelta(days=3)).isoformat()
    claude = ScriptedClaude([
        ([tool_use("1", "save_triage", {"title": "Fix dripping bath tap", "category": "Home maintenance", "priority": 2,
                                        "timescale": "this_week", "due": due, "effort_min": 45,
                                        "next_action": "Check washer size and buy two", "recurrence": "", "who": "me"})],
         "tool_use"),
        ([text("Saved.")], "end_turn")])
    agent = Agent(svc, client=claude)
    r = todos.add_todo(svc, agent, "the bath tap is dripping again, sort before Sue's mum visits", added_by="me")
    assert r["list"] == "Home maintenance" and r["title"] == "Fix dripping bath tap"
    assert claude.calls[0]["tools"][0]["name"] == "save_triage"  # one small, focused AI request
    assert "Sue" not in claude.calls[0]["system"] or True
    item = todos.list_todos(svc)[0]
    assert item["bucket"] == "this_week" and item["priority"] == 2 and item["next_action"].startswith("Check washer")
    assert item["effort_min"] == 45 and item["who"] == "me"
    google_task = svc.google.get().list_tasks("Home maintenance")[0]
    assert google_task["notes"].startswith("Next: Check washer") and google_task["due"] == due


def test_rules_sort_when_ai_unavailable(svc):
    from lifeos import todos
    _todo_svc(svc)

    class Down:
        name = "down"
        def chat(self, *a, **k):
            raise RuntimeError("overloaded")
    agent = Agent(svc, provider=Down())
    leak = todos.add_todo(svc, agent, "Leak under the kitchen sink ASAP")
    assert leak["category"] == "Home maintenance" and leak["priority"] == 1 and leak["timescale"] == "today"
    gutters = todos.add_todo(svc, agent, "Clear the gutters every autumn")
    assert gutters["recurrence"] == "yearly"
    milk = todos.add_todo(svc, agent, "milk")
    assert milk["list"] == "Shopping"
    hedge = todos.add_todo(svc, agent, "trim the hedge this weekend")
    assert hedge["category"] == "Garden" and hedge["due"]
    order = [t["title"] for t in todos.list_todos(svc)]
    assert order[0].startswith("Leak under the kitchen sink")  # urgent first; shopping isn't a to-do


def test_repeating_todo_comes_back(svc):
    from lifeos import todos
    _todo_svc(svc)
    t = todos.normalise(svc, {"title": "Service the boiler", "category": "Home maintenance", "priority": 3,
                              "timescale": "this_month", "due": "2026-12-01", "effort_min": 60,
                              "recurrence": "yearly"}, "boiler")
    r = todos.file_todo(svc, t)
    done = todos.complete_todo(svc, "Home maintenance", r["id"], by="me")
    assert done["next_due"] == "2027-12-01"
    nxt = todos.list_todos(svc)[0]
    assert nxt["title"] == "Service the boiler" and nxt["due"] == "2027-12-01" and nxt["recurrence"] == "yearly"


def test_project_steps_progress_and_finish(svc):
    from lifeos import todos
    _todo_svc(svc)
    claude = ScriptedClaude([
        ([tool_use("1", "save_plan", {"category": "DIY and projects", "steps": [
            {"title": "Choose paint colours", "effort_min": 30}, {"title": "Buy paint and dust sheets", "effort_min": 60},
            {"title": "Paint the walls", "effort_min": 360}]})], "tool_use"),
        ([text("Planned.")], "end_turn")])
    p = todos.start_project(svc, Agent(svc, client=claude), "Redecorate the hallway", "light grey, keep the skirting")
    assert p["total"] == 3 and p["done"] == 0 and p["next"]["title"] == "Choose paint colours"
    assert p["list"] == "Project: Redecorate the hallway"
    main = [t for t in todos.list_todos(svc) if t["project"]]
    assert [t["title"] for t in main] == ["Choose paint colours"]  # only the next step shows in the main list
    r = todos.complete_todo(svc, p["list"], p["next"]["id"], by="me")
    assert r["project"]["next"]["title"] == "Buy paint and dust sheets" and r["project"]["done"] == 1
    for s in r["project"]["steps"][1:]:
        todos.complete_todo(svc, p["list"], s["id"])
    assert todos.all_projects(svc, include_done=True)[0]["status"] == "done"
    assert todos.all_projects(svc) == []


def test_inbox_sweep_and_assistant_tool(svc):
    from lifeos import todos
    from lifeos.tools import run_tool
    _todo_svc(svc)
    svc.settings.behaviour["auto_solve"] = False  # no background work racing the AI-request count below
    svc.google.people["wife"].add_task("Inbox", "book dentist for Leo")
    agent = Agent(svc, provider=type("NoAI", (), {"name": "x", "chat": lambda self, *a, **k: ""})())
    filed = todos.sweep_inbox(svc, agent)
    assert filed == ["Book dentist for Leo -> Health and appointments"]
    assert svc.google.people["wife"].list_tasks("Inbox") == []
    svc.agent = agent
    events_before = svc.store.count_events_since("agent_start:", "2000-01-01")
    r = run_tool("add_todo", {"title": "Renew car tax", "category": "Car", "priority": 2, "due": "2026-12-31"}, svc)
    assert r["list"] == "Car"
    assert svc.store.count_events_since("agent_start:", "2000-01-01") == events_before  # no extra AI request


def test_todo_and_project_endpoints(svc):
    _todo_svc(svc)
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    r = client.post("/api/todos", json={"text": "fix the wobbly garden gate this weekend"}).json()
    assert r["category"] == "Garden"
    items = client.get("/api/todos").json()
    assert items["todos"][0]["title"].lower().startswith("fix the wobbly garden gate") and "Garden" in items["categories"]
    p = client.post("/api/projects", json={"name": "Sort the garage"}).json()
    assert p["total"] == 6 and p["next"]["title"].startswith("Decide")  # sensible default steps without AI
    p2 = client.post(f"/api/projects/{p['id']}/steps", json={"text": "Take old paint to the tip"}).json()
    assert p2["total"] == 7 and p2["steps"][-1]["title"] == "Take old paint to the tip"
    done = client.post("/api/todos/complete", json={"list_name": p["list"], "task_id": p["next"]["id"]}).json()
    assert done["project"]["done"] == 1
    assert client.post("/api/todos", json={"text": "  "}).status_code == 400


# ---------- v0.9: self-solving to-dos and home details ----------
def test_ai_suggestions_with_web_search_and_amazon_basket_links(svc):
    from lifeos import solver
    _todo_svc(svc)
    svc.store.set_home_detail("Landing", "Ceiling light", "B22 bayonet, 8.5W LED, warm white 2700K")
    claude = ScriptedClaude([
        ([text("Searching for the right bulb...")], "pause_turn"),  # a long search pauses; Life OS carries on
        ([tool_use("1", "save_solution", {
            "kind": "buy", "summary": "Your landing light takes a B22 8.5W warm white LED.",
            "options": [
                {"title": "Philips LED B22 8.5W Warm White", "price_gbp": 6.99, "why": "Exact match",
                 "url": "https://www.amazon.co.uk/Philips-Classic-Bayonet/dp/B07ABC1234/ref=sr_1_3"},
                {"title": "Amazon Basics B22 LED 2-pack", "price_gbp": 8.5, "url": "javascript:alert(1)"}],
            "home_item": {"area": "Landing", "item": "Ceiling light"}})], "tool_use"),
        ([text("Saved.")], "end_turn")])
    agent = Agent(svc, client=claude)
    todo = {"id": "x1", "title": "Change upstairs landing lightbulb", "category": "Home maintenance"}
    sol = solver.solve(svc, agent, todo)
    tools_sent = claude.calls[0]["tools"]
    assert any(t.get("type") == "web_search_20250305" for t in tools_sent)  # Claude searched the web
    assert "B22 bayonet" in claude.calls[0]["messages"][0]["content"]  # home details were given to the AI
    assert len(claude.calls) == 3  # carried on after the pause
    first, second = sol["options"]
    assert first["basket_url"] == "https://www.amazon.co.uk/gp/aws/cart/add.html?ASIN.1=B07ABC1234&Quantity.1=1"
    assert first["product_url"] == "https://www.amazon.co.uk/dp/B07ABC1234"
    assert second["url"] == "" and "basket_url" not in second and second["search_url"].startswith("https://www.amazon.co.uk/s?k=")
    assert sol["source"] == "ai" and sol["searched_web"] is True


def test_lightbulb_flow_asks_once_then_orders_the_right_one(svc):
    from lifeos import solver, todos
    _todo_svc(svc)
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Change the upstairs landing lightbulb",
                                                   "category": "Home maintenance"}, "bulb"))
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    svc.agent.provider = None  # no AI: built-in rules
    q = client.post("/api/todos/solve", json={"list_name": t["list"], "task_id": t["id"]}).json()
    assert q["kind"] == "question" and "Bayonet (B22)" in q["question"]["choices"]
    sol = client.post("/api/todos/answer", json={"list_name": t["list"], "task_id": t["id"],
                                                 "answer": "Bayonet (B22)"}).json()
    assert sol["kind"] == "buy" and "B22" in sol["options"][0]["title"]
    assert any(d["area"] == "Landing" and "B22" in d["details"] for d in svc.store.home_details())  # remembered
    client.post("/api/todos/ordered", json={"list_name": t["list"], "task_id": t["id"], "option": 0})
    landing = next(d for d in svc.store.home_details() if d["area"] == "Landing")
    assert "Last bought: Philips LED bulb" in landing["details"]
    assert all(x["id"] != t["id"] for x in todos.list_todos(svc))  # ticked off
    # next time the same job comes up, it offers "same as last time" first, no question
    t2 = todos.file_todo(svc, todos.normalise(svc, {"title": "Replace landing light bulb", "category": "Home maintenance"}, "b"))
    again = solver.solve(svc, None, {**t2, "title": "Replace landing light bulb"})
    assert again["kind"] == "buy" and again["options"][0]["why"] == "Same as last time"


def test_present_flow_gives_three_different_ideas(svc):
    from lifeos import solver
    _todo_svc(svc)
    todo = {"id": "g1", "title": "Buy Mum's birthday present", "category": "Family and social"}
    q = solver.solve(svc, None, todo)
    assert q["kind"] == "question" and q["question"]["area"] == "People"
    solver.answer_question(svc, q, "Gardening")
    ideas = solver.solve(svc, None, todo)
    assert ideas["kind"] == "buy" and len(ideas["options"]) == 3
    assert len({o["title"] for o in ideas["options"]}) == 3 and all(o["search_url"] for o in ideas["options"])


def test_new_todo_gets_suggestions_in_the_background(svc):
    import time
    from lifeos import todos
    _todo_svc(svc)
    agent = Agent(svc, provider=type("NoAI", (), {"name": "x", "chat": lambda self, *a, **k: ""})())
    r = todos.add_todo(svc, agent, "buy a present for Mum")
    assert r["solving"] is True
    for _ in range(50):
        item = next(t for t in todos.list_todos(svc) if t["id"] == r["id"])
        if item["solution"] and item["solution"]["status"] == "ready":
            break
        time.sleep(0.05)
    assert item["solution"]["kind"] == "question"


def test_home_details_screen_api(svc):
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    did = client.post("/api/home-details", json={"area": "Kitchen", "item": "Spotlights", "details": "GU10 5W x6"}).json()["id"]
    client.post("/api/home-details", json={"area": "Kitchen", "item": "Spotlights", "details": "GU10 5W warm x6"})
    rows = client.get("/api/home-details").json()["details"]
    assert len(rows) == 1 and rows[0]["details"] == "GU10 5W warm x6"
    assert client.post(f"/api/home-details/{did}/delete").json()["ok"]
    assert client.post("/api/home-details", json={"area": "", "item": "x", "details": "y"}).status_code == 400


def test_gemini_does_not_send_a_web_search_tool(svc):
    from lifeos import solver
    gem = ScriptedGemini([[g_call("save_solution", {"kind": "do", "summary": "Just do it", "steps": ["Start"]})],
                          [types.Part(text="ok")]])
    agent = Agent(svc, provider=GeminiProvider(client=gem, model="m", model_deep="m"))
    sol = solver.solve(svc, agent, {"id": "z", "title": "Tidy the shed"})
    names = [d.name for d in gem.calls[0]["config"].tools[0].function_declarations]
    assert names == ["save_solution"] and sol["kind"] == "do" and sol["searched_web"] is False


# ---------- v0.10: tradespeople, availability, time-blocking ----------
def _trade_svc(svc):
    _todo_svc(svc)
    svc.settings.behaviour["household"]["area"] = "Leeds LS6"
    svc.store.save_contact(name="Dave Hughes", company="Spark Electrical", trade="electrician",
                           email="jobs@spark.example", phone="0113 496 0123", favourite=1)
    svc.store.save_contact(name="Pipe Dreams", trade="plumber", phone="07700 900456")
    return svc


def test_trade_detection():
    from lifeos.trades import detect_trade
    assert detect_trade("kitchen socket stopped working") == "electrician"
    assert detect_trade("boiler making a banging noise, get it fixed") == "gas engineer"
    assert detect_trade("toilet keeps running, fix") == "plumber"
    assert detect_trade("we need a roofer") == "roofer"
    assert detect_trade("change the landing lightbulb") == ""  # do-it-yourself jobs are not trades
    assert detect_trade("replace smoke alarm battery") == ""
    assert detect_trade("oven not working") == "appliance repair"
    assert detect_trade("washing machine leaking") == "appliance repair"
    assert detect_trade("front door lock broken") == "locksmith"
    assert detect_trade("call the sparky") == "electrician"
    assert detect_trade("buy mum a present") == ""


def test_free_slots_avoid_busy_times_and_respect_notice():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from lifeos import scheduling
    uk = ZoneInfo("Europe/London")
    now = datetime(2026, 11, 24, 20, 0, tzinfo=uk)  # Tuesday evening
    events = [{"uid": "a", "summary": "Clinic", "start": "2026-11-26T07:00:00+00:00", "end": "2026-11-26T17:00:00+00:00", "who": ["Me"]},
              {"uid": "b", "summary": "Work", "start": "2026-11-26T08:00:00+00:00", "end": "2026-11-26T12:00:00+00:00", "who": ["My wife"]},
              {"uid": "c", "summary": "Bin day", "start": "2026-11-27", "end": "2026-11-28", "who": ["Family"]},
              {"uid": "d", "summary": "Away: Lake District holiday", "start": "2026-11-28", "end": "2026-11-30", "who": ["Family"]}]
    svc = SimpleNamespace(settings=__import__("lifeos.config", fromlist=["x"]).load_settings(
        __import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "settings.yaml"),
        google=SimpleNamespace(events_all=lambda days: events, __bool__=lambda: True))
    slots = scheduling.free_slots(svc, 120, 3, 2, now=now)
    starts = [s["start"][:16] for s in slots]
    assert starts[0] == "2026-11-26T12:00"  # Thursday: someone is free from 12 (wife back from work)
    assert slots[0]["who_free"] == ["My wife"]
    assert starts[1] == "2026-11-27T08:00"  # bin day (all-day) doesn't block
    assert not any(s.startswith("2026-11-28") or s.startswith("2026-11-29") for s in starts)  # holiday blocks
    assert slots[0]["label"] == "Thursday 26 November, 12pm to 2pm"


def test_arrange_plan_prefers_go_to_and_drafts_email_with_free_times(svc):
    from lifeos import solver, todos
    _trade_svc(svc)
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket has stopped working",
                                                   "category": "Home maintenance", "priority": 2}, "socket"))
    sol = solver.solve(svc, None, t)
    assert sol["kind"] == "arrange" and sol["trade"] == "electrician"
    assert sol["contacts"][0]["name"] == "Spark Electrical (Dave Hughes)" and sol["contacts"][0]["favourite"]
    assert sol["email"]["body"].startswith("Hello Dave,")
    assert all(s["label"] in sol["email"]["body"] for s in sol["slots"]) and len(sol["slots"]) == 3
    assert "Leeds LS6" in sol["email"]["subject"] and sol["contacts"][0]["tel"] == "tel:01134960123"
    assert "google.com/search?q=electrician+near+Leeds+LS6" in sol["search_url"]


def test_phone_only_trade_gets_call_and_text(svc):
    from lifeos import solver
    _trade_svc(svc)
    sol = solver.solve(svc, None, {"id": "p1", "title": "Toilet keeps running, fix it", "category": "Home maintenance"})
    c = sol["contacts"][0]
    assert c["name"] == "Pipe Dreams" and not c["email"] and c["sms"].startswith("sms:07700900456?body=Hi%2C%20we%20need%20a%20plumber")


def test_asks_for_area_when_nobody_saved_and_area_unknown(svc):
    from lifeos import solver
    _todo_svc(svc)
    q = solver.solve(svc, None, {"id": "r1", "title": "Roof tiles slipped, need a roofer"})
    assert q["kind"] == "question" and q["question"]["area"] == "Home" and q["question"]["item"] == "Location"
    solver.answer_question(svc, q, "Leeds LS6")
    again = solver.solve(svc, None, {"id": "r1", "title": "Roof tiles slipped, need a roofer"})
    assert again["kind"] == "arrange" and again["area"] == "Leeds LS6"


def test_ai_arrange_with_found_local_tradespeople(svc):
    from lifeos import solver
    _trade_svc(svc)
    claude = ScriptedClaude([
        ([tool_use("1", "save_solution", {
            "kind": "arrange", "trade": "gas engineer", "summary": "Boiler fault: needs a Gas Safe engineer.",
            "contacts_found": [{"name": "Leeds Heating Co", "phone": "0113 000 1111", "email": "hello@leedsheating.example",
                                "website": "https://leedsheating.example", "why": "4.9 stars, Gas Safe"},
                               {"name": "No Details Ltd"}],
            "email": {"subject": "Boiler repair in LS6", "body": "Hello,\n\nOur boiler is banging..."}})], "tool_use"),
        ([text("Done.")], "end_turn")])
    sol = solver.solve(svc, Agent(svc, client=claude), {"id": "b1", "title": "Boiler is banging, get it fixed"})
    assert any(t.get("type") == "web_search_20250305" for t in claude.calls[0]["tools"])
    prompt = claude.calls[0]["messages"][0]["content"]
    assert "FREE TIMES" in prompt and "Spark Electrical" in prompt and "AREA: Leeds LS6" in prompt
    assert [c["name"] for c in sol["contacts"]] == ["Leeds Heating Co"]  # the one with no contact details is dropped
    assert sol["email"]["subject"] == "Boiler repair in LS6"


def test_email_send_reply_chase_and_book(svc):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from lifeos import todos, trades
    _trade_svc(svc)
    svc.google.people["me"].reply_after = 3600  # no reply yet
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket has stopped working",
                                                   "category": "Home maintenance"}, "socket"))
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    svc.agent.provider = None
    sol = client.post("/api/todos/solve", json={"list_name": t["list"], "task_id": t["id"]}).json()
    r = client.post("/api/todos/arrange", json={"list_name": t["list"], "task_id": t["id"], "contact": 0,
                                                "subject": sol["email"]["subject"], "body": sol["email"]["body"] + "\nThanks!"}).json()
    assert r["status"] == "sent" and r["thread_id"] and len(r["slots"]) == 3
    item = next(x for x in todos.list_todos(svc) if x["id"] == t["id"])
    assert item["request"]["status"] == "sent" and item["next_action"].startswith("Waiting for Spark Electrical")
    # Silence for 4 days: chased, with a notification
    uk = ZoneInfo("Europe/London")
    assert trades.check_requests(svc, now=datetime.now(uk) + timedelta(days=4)) == [f"chase:{r['id']}"]
    assert svc.notifier.pushes[-1]["title"] == "Chase Spark Electrical (Dave Hughes)"
    assert client.post(f"/api/requests/{r['id']}/remind").status_code == 400  # never sent unseen
    draft = client.get(f"/api/requests/{r['id']}/reminder").json()
    assert draft["body"].startswith("Hello Dave,\n\nJust following up") and draft["to"] == "jobs@spark.example"
    client.post(f"/api/requests/{r['id']}/remind", json={"subject": draft["subject"], "body": draft["body"] + "\nCheers"})
    assert svc.google.people["me"].sent_threads[r["thread_id"]]["count"] == 2  # the reminder went in the same thread
    # Reply arrives
    svc.google.people["me"].reply_after = -1
    assert trades.check_requests(svc) == [f"replied:{r['id']}"]
    assert "works for me" in svc.store.get_request(r["id"])["reply_snippet"]
    item = next(x for x in todos.list_todos(svc) if x["id"] == t["id"])
    assert item["request"]["greet"] == "Dave" and item["request"]["contact_favourite"]
    from datetime import date as _date, timedelta as _td
    thu = _date.today() + _td(days=(3 - _date.today().weekday()) % 7 or 7)  # the next Thursday (never in the past)
    b = client.post(f"/api/requests/{r['id']}/book", json={"start": f"{thu.isoformat()}T08:00:00", "duration_min": 120,
                                                             "confirm_body": "Hello Dave,\n\nThursday at 8am is great."}).json()
    assert b["status"] == "booked" and b["appointment"]["label"] == f"Thursday {thu.day} {thu:%B}, 8am to 10am"
    assert b["appointment"]["confirmed"] and svc.google.people["me"].sent_threads[r["thread_id"]]["count"] == 3
    item = next(x for x in todos.list_todos(svc) if x["id"] == t["id"])
    assert item["due"] == thu.isoformat() and item["next_action"].startswith("Spark Electrical (Dave Hughes) coming Thursday")
    events = svc.google.get().events(days=400)
    assert any(e["summary"].startswith("Electrician: Spark Electrical (Dave Hughes) (") and e["summary"].endswith(" in)")
               for e in events)  # says who'll be in


def test_assistant_must_get_approval_to_email_a_tradesperson(svc):
    _trade_svc(svc)
    claude = ScriptedClaude([
        ([tool_use("1", "email_tradesperson", {"list_name": "Home maintenance", "task_id": "x", "contact_name": "Dave",
                                               "contact_email": "jobs@spark.example", "subject": "Socket", "body": "Hello Dave"})],
         "tool_use"),
        ([text("Asked you to approve.")], "end_turn")])
    Agent(svc, client=claude).run("email the electrician about the socket")
    pending = svc.store.list_pending()
    assert pending and pending[0]["reason"].startswith("Email Dave <jobs@spark.example>\nSubject: Socket")
    assert not getattr(svc.google.people["me"], "sent_threads", {})  # nothing sent without the tap


def test_find_time_blocks_calendar_and_sets_due(svc):
    from lifeos import todos
    _trade_svc(svc)
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Fix dripping bath tap myself", "category": "Home maintenance",
                                                   "effort_min": 45}, "tap"))
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    slots = client.get("/api/slots?duration_min=45&diy=true").json()["slots"]
    assert len(slots) == 3
    r = client.post("/api/todos/schedule", json={"list_name": t["list"], "task_id": t["id"], "start": slots[0]["start"],
                                                 "duration_min": 45}).json()
    assert r["ok"] and r["label"] == slots[0]["label"]
    item = next(x for x in todos.list_todos(svc) if x["id"] == t["id"])
    assert item["due"] == slots[0]["start"][:10]


def test_contacts_api(svc):
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    a = client.post("/api/contacts", json={"name": "Dave", "trade": "Electrician", "email": "d@x.example", "favourite": True}).json()["id"]
    b = client.post("/api/contacts", json={"name": "Sam", "trade": "electrician", "favourite": True}).json()["id"]
    rows = client.get("/api/contacts?trade=electrician").json()["contacts"]
    assert [(r["id"], r["favourite"]) for r in rows][0] == (b, 1) and dict((r["id"], r["favourite"]) for r in rows)[a] == 0
    assert client.post("/api/contacts", json={"name": "Bad", "email": "not-an-email"}).status_code == 400
    assert client.post(f"/api/contacts/{a}/delete").json()["ok"]


def test_appointments_use_the_same_flow_with_booking_wording(svc):
    from lifeos import solver, trades
    _trade_svc(svc)
    assert trades.detect_trade("Ava needs a dentist check-up") == "dentist"
    assert trades.detect_trade("book the MOT") == "garage"
    assert trades.detect_trade("Leo haircut") == "hairdresser"
    svc.store.save_contact(name="Smile Dental", trade="dentist", email="reception@smile.example", favourite=1)
    svc.settings.behaviour["household"]["kids"] = ["Ava", "Leo"]
    sol = solver.solve(svc, None, {"id": "d1", "title": "Ava needs a dentist check-up", "who": "Ava"})
    assert sol["kind"] == "arrange" and sol["appointment"] and sol["contacts"][0]["name"] == "Smile Dental"
    assert sol["email"]["subject"] == "Appointment request: Ava needs a dentist check-up"
    assert "I'd like to book an appointment for this: ava needs a dentist check-up" not in sol["email"]["body"]
    assert "I'd like to book an appointment for this: Ava needs a dentist check-up." in sol["email"]["body"]
    assert sol["summary"].startswith("Book this with your dentist. Your go-to is Smile Dental.")


def test_open_loops_list_needs_you_first(svc):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from lifeos import trades
    _trade_svc(svc)
    now = datetime.now(ZoneInfo("Europe/London"))
    base = dict(person="me", list_name="Home maintenance", contact_email="x@y.example", subject="Job", slots=[])
    a = svc.store.add_request(task_id="t1", contact_name="Waiting Ltd", trade="plumber", status="sent", **base)
    b = svc.store.add_request(task_id="t2", contact_name="Replied Ltd", trade="electrician", status="sent", **base)
    svc.store.update_request(b, status="replied", reply_snippet="Tuesday?")
    c = svc.store.add_request(task_id="t3", contact_name="Smile Dental", trade="dentist", status="sent", **base)
    svc.store.update_request(c, status="booked", appointment={"start": (now + timedelta(days=3)).isoformat(), "label": "Friday 9am"})
    d = svc.store.add_request(task_id="t4", contact_name="Old Visit", trade="roofer", status="sent", **base)
    svc.store.update_request(d, status="booked", appointment={"start": (now - timedelta(days=3)).isoformat(), "label": "last week"})
    loops = trades.open_loops(svc)
    assert [x["who"] for x in loops] == ["Replied Ltd", "Smile Dental", "Waiting Ltd"]  # past visits drop off
    assert loops[0]["needs_you"] and loops[1]["line"] == "Appointment at Smile Dental, Friday 9am"
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    assert [x["id"] for x in client.get("/api/today").json()["waiting"]] == [b, c, a]


def test_assistant_filed_trade_job_gets_arranged_automatically(svc):
    import time
    _trade_svc(svc)
    claude = ScriptedClaude([
        ([tool_use("1", "add_todo", {"title": "Fix the kitchen socket", "category": "Home maintenance", "priority": 2,
                                     "next_action": "Get an electrician", "notes": "From Sue's email"})], "tool_use"),
        ([text("Added, and I've lined up Spark Electrical.")], "end_turn"),
        ([tool_use("2", "save_solution", {"kind": "arrange", "trade": "electrician", "summary": "Needs an electrician."})], "tool_use"),
        ([text("ok")], "end_turn")])
    agent = Agent(svc, client=claude)
    svc.agent = agent
    agent.run("the kitchen socket has died")
    from lifeos import todos
    for _ in range(50):
        item = next((t for t in todos.list_todos(svc) if t["title"] == "Fix the kitchen socket"), None)
        if item and item.get("solution") and item["solution"].get("status") != "working":
            break
        time.sleep(0.05)
    assert item["solution"]["kind"] == "arrange" and item["solution"]["contacts"][0]["name"].startswith("Spark")
    raw = next(t for t in svc.google.get().list_tasks(item["list"]) if t["title"] == "Fix the kitchen socket")
    assert raw["notes"].startswith("From Sue's email\n\n--- Life OS ---\nNext: Send the email to Spark Electrical")
    assert "Ask: Spark Electrical (Dave Hughes) (go-to): 0113 496 0123" in raw["notes"]  # readable in Google Tasks



def test_reply_detection_ignores_our_own_mail_and_out_of_office():
    from lifeos.trades import _is_reply
    assert _is_reply({"from": "Joe <joe@gmail.com>", "labels": ["INBOX", "UNREAD"], "subject": "Re: socket"}, "jobs@spark.example")
    assert not _is_reply({"from": "Joe <joe@gmail.com>", "labels": ["SENT"], "subject": "Socket"}, "jobs@spark.example")
    assert not _is_reply({"from": "jobs@spark.example", "labels": ["INBOX"], "subject": "Automatic reply: Socket"}, "jobs@spark.example")
    assert _is_reply({"from": "Dave <dave@spark.example>", "subject": "Re: Socket"}, "jobs@spark.example")  # colleague
    assert not _is_reply({"from": "someone@gmail.com", "subject": "Re: Socket"}, "dave123@gmail.com")  # webmail: exact only


def test_after_the_visit_it_asks_how_it_went_and_ticking_off_closes_it(svc):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from lifeos import todos, trades
    _trade_svc(svc)
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Leaking toilet", "category": "Home maintenance"}, "toilet"))
    cid = svc.store.save_contact(name="Flo", company="Flow Plumbing", trade="plumber", email="flo@flow.example")
    rid = svc.store.add_request(person="me", task_id=t["id"], list_name=t["list"], contact_id=cid,
                                contact_name="Flow Plumbing (Flo)", contact_email="flo@flow.example", trade="plumber",
                                subject="Toilet", status="sent", slots=[])
    now = datetime.now(ZoneInfo("Europe/London"))
    svc.store.update_request(rid, status="booked", appointment={"start": (now - timedelta(hours=5)).isoformat(),
                                                                 "end": (now - timedelta(hours=3)).isoformat(), "label": "today"})
    assert trades.check_requests(svc, now=now) == [f"visited:{rid}"]
    assert svc.notifier.pushes[-1]["title"] == "How did it go?"
    assert trades.check_requests(svc, now=now) == []  # asks once
    assert trades.open_loops(svc)[0]["line"] == "Did Flow Plumbing (Flo) sort it? Tick it off, or ask again"
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    assert client.post(f"/api/contacts/{cid}/favourite").json()["ok"]
    assert [c["name"] for c in svc.store.contacts("plumber") if c["favourite"]] == ["Flo"]
    client.post("/api/todos/complete", json={"list_name": t["list"], "task_id": t["id"]})
    assert svc.store.get_request(rid)["status"] == "done" and trades.open_loops(svc) == []


# ---------- v0.11: using Life OS through Gemini (Google Tasks, Gmail drafts, Waiting on) ----------
def test_gemini_todos_are_picked_up_sorted_and_prepared(svc):
    from lifeos import bridge
    _trade_svc(svc)
    me, wife = svc.google.people["me"], svc.google.people["wife"]
    me.add_task("@default", "Renew passport someday")  # there before Life OS: stays put
    wife.add_task("@default", "Buy Leo new school shoes")
    assert bridge.sweep_voice(svc, None) == []
    me.add_task("@default", "Kitchen socket has stopped working")      # "Hey Google, add ... to my to-dos"
    me.add_task("@default", "Call Mum", due="2026-11-26")              # a dated reminder: theirs to keep
    wife.add_task("@default", "Oven not working")
    filed = bridge.sweep_voice(svc, None)
    assert any(f.startswith("Kitchen socket has stopped working -> ") for f in filed)
    assert any(f.startswith("Oven not working -> ") for f in filed)
    assert sorted(t["title"] for t in me.list_tasks("@default")) == ["Call Mum", "Renew passport someday"]
    assert [t["title"] for t in wife.list_tasks("@default")] == ["Buy Leo new school shoes"]
    assert any(p["title"] == "Added from Gemini" and "Kitchen socket" in p["message"] for p in svc.notifier.pushes)
    assert bridge.sweep_voice(svc, None) == []  # only once


def test_tradesperson_email_waits_in_gmail_drafts_and_tracks_from_there(svc):
    from lifeos import bridge, solver, todos, trades
    _trade_svc(svc)
    me = svc.google.people["me"]
    me.reply_after = 3600
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket has stopped working",
                                                   "category": "Home maintenance"}, "socket"))
    sol = solver.solve_and_store(svc, None, t)
    assert sol["drafted"]
    r = svc.store.request_for_task(t["id"])
    assert r["status"] == "drafted" and r["draft_id"] in me.drafts
    assert me.drafts[r["draft_id"]]["to"] == "jobs@spark.example" and me.drafts[r["draft_id"]]["body"].startswith("Hello Dave,")
    mirror = next(x for x in me.list_tasks("Waiting on") if x["id"] == r["gtask_id"])
    assert mirror["title"] == "Send email to Spark Electrical (in Gmail drafts): Kitchen socket has stopped working"
    assert trades.open_loops(svc)[0]["status"] == "drafted"
    # Joe presses Send in Gmail
    me.send_draft(r["draft_id"])
    assert bridge.run(svc, None)["drafts"] == [f"sent:{r['id']}"]
    r = svc.store.get_request(r["id"])
    assert r["status"] == "sent" and r["draft_id"] == ""
    assert next(x for x in me.list_tasks("Waiting on") if x["id"] == r["gtask_id"])["title"] == \
        "Waiting for Spark Electrical to reply: Kitchen socket has stopped working"
    # Dave replies, agreeing to the second time offered
    second = r["slots"][1]
    day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
        __import__("datetime").datetime.fromisoformat(second["start"]).weekday()]
    me.sent_threads[r["thread_id"]]["body"] = f"Hello Dave,\n\n- {second['label']}\n"
    me.reply_after = -1
    trades.check_requests(svc)
    r = svc.store.get_request(r["id"])
    assert r["status"] == "replied" and r["suggested"]["start"].startswith(second["start"][:13])
    title = next(x for x in me.list_tasks("Waiting on") if x["id"] == r["gtask_id"])["title"]
    assert title.startswith(f"Spark Electrical replied. Tick to book {day}")
    # "Hey Google, mark 'Spark Electrical replied' as done" -> booked
    me.complete_task("Waiting on", r["gtask_id"])
    assert bridge.sync_ticks(svc) == [f"booked:{r['id']}"]
    r = svc.store.get_request(r["id"])
    assert r["status"] == "booked" and any(e["summary"].startswith("Electrician: Spark Electrical (Dave Hughes)")
                                           for e in svc.google.get().events(days=30))
    assert next(x for x in me.list_tasks("Waiting on") if x["id"] == r["gtask_id"])["title"].startswith(
        "Spark Electrical booked for ")
    assert me.sent_threads[r["thread_id"]]["count"] == 1  # booking by tick sends nothing
    # After the visit: "Did they sort it?", ticked in Google Tasks -> the job is done
    svc.store.update_request(r["id"], status="visited")
    trades._changed(svc, r["id"])
    r = svc.store.get_request(r["id"])
    me.complete_task("Waiting on", r["gtask_id"])
    assert bridge.sync_ticks(svc) == [f"done:{r['id']}"]
    assert svc.store.get_request(r["id"])["status"] == "done" and trades.open_loops(svc) == []
    assert not any(x["id"] == t["id"] for x in todos.list_todos(svc))


def test_binned_draft_stops_tracking_and_app_send_replaces_draft(svc):
    from lifeos import bridge, solver, todos
    _trade_svc(svc)
    me = svc.google.people["me"]
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket has stopped working",
                                                   "category": "Home maintenance"}, "socket"))
    solver.solve_and_store(svc, None, t)
    r = svc.store.request_for_task(t["id"])
    me.delete_draft(r["draft_id"])  # deleted in Gmail
    assert bridge.run(svc, None)["drafts"] == [f"binned:{r['id']}"]
    assert svc.store.get_request(r["id"])["status"] == "cancelled" and me.list_tasks("Waiting on") == []
    # Worked out again: a fresh draft; then sent from the app instead, so that draft goes
    sol = solver.solve_and_store(svc, None, t)
    r2 = svc.store.request_for_task(t["id"])
    assert r2["status"] == "drafted" and len(me.drafts) == 1
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    sent = client.post("/api/todos/arrange", json={"list_name": t["list"], "task_id": t["id"], "contact": 0,
                                                   "subject": sol["email"]["subject"], "body": sol["email"]["body"]}).json()
    assert sent["status"] == "sent" and me.drafts == {} and svc.store.get_request(r2["id"])["status"] == "cancelled"
    assert [x["title"] for x in me.list_tasks("Waiting on")] == [
        "Waiting for Spark Electrical to reply: Kitchen socket has stopped working"]
    assert client.get("/api/today").json()["waiting"][0]["who"] == "Spark Electrical (Dave Hughes)"
    assert len(client.get("/api/today").json()["waiting"]) == 1  # the Google Tasks copy isn't listed twice


def test_chase_reminder_waits_in_gmail_drafts(svc):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from lifeos import bridge, todos, trades
    _trade_svc(svc)
    me = svc.google.people["me"]
    me.reply_after = 3600
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Leaking toilet", "category": "Home maintenance"}, "x"))
    cid = svc.store.save_contact(name="Flo", company="Flow Plumbing", trade="plumber", email="flo@flow.example")
    r = trades.send_request(svc, t, {"name": "Flow Plumbing (Flo)", "email": "flo@flow.example", "contact_id": cid,
                                     "trade": "plumber"}, "Toilet", "Hello Flo", [], "me")
    trades.check_requests(svc, now=datetime.now(ZoneInfo("Europe/London")) + timedelta(days=4))
    r = svc.store.get_request(r["id"])
    assert r["status"] == "chased" and r["reminder_draft_id"] in me.drafts and r["sent_count"] == 1
    assert me.drafts[r["reminder_draft_id"]]["thread_id"] == r["thread_id"]
    assert "It's in your Gmail drafts" in svc.notifier.pushes[-1]["message"]
    assert next(x for x in me.list_tasks("Waiting on"))["title"] == \
        "Chase Flow Plumbing, no reply yet (reminder in Gmail drafts): Leaking toilet"
    me.send_draft(r["reminder_draft_id"])
    assert bridge.run(svc, None)["drafts"] == [f"reminded:{r['id']}"]
    r = svc.store.get_request(r["id"])
    assert r["status"] == "sent" and r["reminder_draft_id"] == "" and me.sent_threads[r["thread_id"]]["count"] == 2


def test_todos_ticked_in_gemini_get_the_same_follow_ups(svc):
    from lifeos import bridge, todos
    _todo_svc(svc)
    me = svc.google.people["me"]
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Change the water filter", "category": "Home maintenance",
                                                   "recurrence": "every_2_months", "due": "2026-11-20"}, "filter"))
    gone = todos.file_todo(svc, todos.normalise(svc, {"title": "Fix gate latch", "category": "Garden"}, "gate"))
    rid = svc.store.add_request(person="me", task_id=gone["id"], list_name=gone["list"], contact_name="Gary",
                                contact_email="g@x.example", trade="handyman", subject="Gate", status="sent", slots=[])
    me.complete_task(t["list"], t["id"])      # "Hey Google, mark 'Change the water filter' as done"
    me.delete_task(gone["list"], gone["id"])  # deleted in the Google Tasks app
    did = bridge.sync_ticks(svc)
    assert "ticked:Change the water filter" in did and "deleted:Fix gate latch" in did
    again = [x for x in todos.list_todos(svc) if x["title"] == "Change the water filter"]
    assert len(again) == 1 and again[0]["id"] != t["id"] and again[0]["due"] > "2026-11-20"  # it came back
    assert svc.store.get_request(rid)["status"] == "done"
    assert bridge.sync_ticks(svc) == []  # handled once


def test_suggest_time_reads_day_and_hour():
    from lifeos import trades
    svc = SimpleNamespace()
    r = {"trade": "electrician", "slots": [{"start": "2026-11-26T08:00:00+00:00"}, {"start": "2026-11-27T12:00:00+00:00"},
                                          {"start": "2026-11-28T09:00:00+00:00"}]}
    assert trades.suggest_time(svc, r, "Friday works, I can be there for 1:30pm")["start"] == "2026-11-27T13:30:00+00:00"
    assert trades.suggest_time(svc, r, "Sat is fine")["label"] == "Saturday 28 November, 9am to 11am"
    assert trades.suggest_time(svc, r, "Yes, happy to help")["start"] == "2026-11-26T08:00:00+00:00"  # first offered


# ---------- v0.12: next steps in Google Tasks, Gemini link status, stalled jobs ----------
def test_google_task_notes_carry_next_step_and_links(svc):
    from lifeos import bridge, solver, todos, trades
    _trade_svc(svc)
    me = svc.google.people["me"]
    gift = todos.file_todo(svc, todos.normalise(svc, {"title": "Buy Mum a birthday present", "category": "Family and social",
                                                      "notes": "She liked the blue scarf"}, "mum"))
    claude = ScriptedClaude([([tool_use("1", "save_solution", {
        "kind": "buy", "summary": "Three ideas Mum would like.",
        "options": [{"title": "Cashmere scarf", "url": "https://www.amazon.co.uk/dp/B0TEST1234", "price_gbp": 39.5}]})],
        "tool_use"), ([text("ok")], "end_turn")])
    solver.solve_and_store(svc, Agent(svc, client=claude), gift)
    notes = next(t for t in me.list_tasks(gift["list"]) if t["id"] == gift["id"])["notes"]
    assert notes.startswith("She liked the blue scarf\n\n--- Life OS ---")
    assert "Buy: Cashmere scarf (about £39.50): https://www.amazon.co.uk/gp/aws/cart/add.html?ASIN.1=B0TEST1234" in notes
    # A trade job: who to ask, then the line follows the request as it moves on
    job = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket has stopped working",
                                                     "category": "Home maintenance"}, "socket"))
    solver.solve_and_store(svc, None, job)
    read = lambda: next(t for t in me.list_tasks(job["list"]) if t["id"] == job["id"])["notes"]  # noqa: E731
    assert "Next: Send the email to Spark Electrical (Dave Hughes): it's in your Gmail drafts" in read()
    assert "Ask: Spark Electrical (Dave Hughes) (go-to): 0113 496 0123 · jobs@spark.example" in read()
    r = svc.store.request_for_task(job["id"])
    me.send_draft(r["draft_id"])
    trades.check_drafts(svc)
    assert "Next: Waiting for Spark Electrical (Dave Hughes) to reply" in read()
    assert read().count("--- Life OS ---") == 1 and read().count("Next: ") == 1
    assert bridge.merge_notes("Mine\nNext: old\n\n--- Life OS ---\nNext: x", "") == "Mine\nNext: old"


def test_gemini_link_status_and_check_now(svc):
    _trade_svc(svc)
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    assert client.get("/api/bridge").json()["last"] == {}
    client.post("/api/bridge/run")  # first run sets the baseline
    svc.google.people["me"].add_task("@default", "Oven not working")
    b = client.post("/api/bridge/run").json()
    assert b["result"]["picked_up"] == 1 and b["last"]["errors"] == []
    assert b["recent"][0]["text"].startswith("Added from Gemini: Oven not working (")


def test_stalled_jobs_get_a_sunday_nudge(svc):
    from datetime import date, timedelta
    from lifeos import todos
    _trade_svc(svc)
    old = todos.file_todo(svc, todos.normalise(svc, {"title": "Sort out the loft boxes", "category": "Home maintenance",
                                                     "effort_min": 20}, "loft"))
    todos.file_todo(svc, todos.normalise(svc, {"title": "Landing socket not working, get it fixed",
                                               "category": "Home maintenance"}, "socket"))
    todos.file_todo(svc, todos.normalise(svc, {"title": "Learn Spanish", "category": "Home maintenance",
                                               "timescale": "someday"}, "es"))
    in_five_weeks = date.today() + timedelta(days=35)
    titles = {j["title"]: j["how"] for j in todos.stalled(svc, 28, now=in_five_weeks)}
    assert titles == {"Sort out the loft boxes": "a 20-minute job: tap Find time",
                      "Landing socket not working, get it fixed": "get an electrician in: open it and tap Send"}
    assert todos.stalled(svc, 28) == []  # nothing has waited long yet
    msg = todos.stalled_nudge(svc, now=in_five_weeks)
    assert msg.startswith("Waiting a month or more:") and svc.notifier.pushes[-1]["title"] == "Jobs that have stalled"
    assert old["id"]


# ---------- v0.13: snooze, change, delete, Next up, Plan my week, links in notifications ----------
def test_snooze_dates():
    from datetime import date
    from lifeos.todos import snooze_date
    wed = date(2026, 11, 25)
    assert snooze_date("tomorrow", wed) == date(2026, 11, 26)
    assert snooze_date("weekend", wed) == date(2026, 11, 28)            # the coming Saturday
    assert snooze_date("weekend", date(2026, 11, 28)) == date(2026, 12, 5)  # on a Saturday: next one
    assert snooze_date("next_week", wed) == date(2026, 11, 30)          # the coming Monday
    assert snooze_date("2026-12-24", wed) == date(2026, 12, 24)
    with pytest.raises(ValueError):
        snooze_date("2026-01-01", wed)


def test_snooze_change_move_and_delete_a_job(svc):
    from lifeos import solver, todos
    _trade_svc(svc)
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket has stopped working",
                                                   "category": "DIY and projects"}, "socket"))
    solver.solve_and_store(svc, None, t)
    r = client.post("/api/todos/snooze", json={"list_name": t["list"], "task_id": t["id"], "until": "tomorrow"}).json()
    item = next(x for x in todos.list_todos(svc) if x["id"] == t["id"])
    assert item["due"] == r["due"] and item["days"] == 1
    # Change: rename, re-file under Home maintenance, for my wife, urgent; the plan and the Gmail draft follow it
    e = client.post("/api/todos/edit", json={"list_name": t["list"], "task_id": t["id"], "title": "Kitchen socket dead",
                                             "category": "Home maintenance", "who": "wife", "priority": 1}).json()
    assert e["list"] == "Home maintenance" and e["id"] != t["id"]
    item = next(x for x in todos.list_todos(svc) if x["id"] == e["id"])
    assert (item["title"], item["who"], item["priority"], item["due"]) == ("Kitchen socket dead", "wife", 1, r["due"])
    assert item["solution"]["kind"] == "arrange" and item["request"]["status"] == "drafted"
    assert not any(x["id"] == t["id"] for x in todos.list_todos(svc))
    assert client.post("/api/todos/edit", json={"list_name": "Home maintenance", "task_id": e["id"],
                                                 "due": "not-a-date"}).status_code == 400
    # Delete: gone, its Gmail draft tidied away, nothing waiting
    draft = item["request"]["draft_id"]
    assert client.post("/api/todos/delete", json={"list_name": "Home maintenance", "task_id": e["id"]}).json()["ok"]
    assert not any(x["id"] == e["id"] for x in todos.list_todos(svc))
    assert draft not in svc.google.people["me"].drafts and svc.store.request_for_task(e["id"]) is None
    assert client.post("/api/todos/snooze", json={"list_name": "Home maintenance", "task_id": e["id"],
                                                   "until": "tomorrow"}).status_code == 400


def test_next_up_and_plan_my_week(svc):
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo
    from lifeos import todos
    _trade_svc(svc)
    today = date.today()
    a = todos.file_todo(svc, todos.normalise(svc, {"title": "Bleed the radiators", "category": "Home maintenance",
                                                   "due": (today - timedelta(days=1)).isoformat(), "effort_min": 30}, "x"))
    b = todos.file_todo(svc, todos.normalise(svc, {"title": "Paint the shed", "category": "Garden", "priority": 2,
                                                   "timescale": "this_week", "effort_min": 120}, "y"))
    todos.file_todo(svc, todos.normalise(svc, {"title": "Learn Spanish", "category": "Family and social",
                                               "timescale": "someday"}, "z"))
    nu = todos.next_up(svc)
    assert [n["title"] for n in nu] == ["Bleed the radiators", "Paint the shed"] and nu[0]["action"] == "Open"
    p = todos.plan_week(svc, now=datetime.now(ZoneInfo("Europe/London")))
    assert sorted(x["title"] for x in p["plan"]) == ["Bleed the radiators", "Paint the shed"]
    s1, s2 = (sorted(p["plan"], key=lambda x: x["start"]))
    assert s1["end"] <= s2["start"] or s1["start"][:10] != s2["start"][:10]  # never overlapping
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    items = [{"list_name": x["list"], "task_id": x["task_id"], "start": x["start"], "duration_min": x["duration_min"]}
             for x in p["plan"]]
    r = client.post("/api/plan-week", json={"items": items + [{"list_name": "Garden", "task_id": "nope", "start": items[0]["start"]}]}).json()
    assert sorted(x["title"] for x in r["planned"]) == ["Bleed the radiators", "Paint the shed"]  # unknown ignored
    events = [e["summary"] for e in svc.google.get().events(days=10)]
    assert "Bleed the radiators" in events and "Paint the shed" in events
    assert {x["id"] for x in todos.list_todos(svc)} >= {a["id"], b["id"]}


def test_job_notifications_open_the_job(svc):
    from lifeos import bridge
    _trade_svc(svc)
    bridge.sweep_voice(svc, None)
    svc.google.people["me"].add_task("@default", "Oven not working")
    bridge.sweep_voice(svc, None)
    push = next(p for p in svc.notifier.pushes if p["title"] == "Added from Gemini")
    assert push["actions"][0]["action"] == "view" and "/app/#tasks/" in push["actions"][0]["url"]


# ---------- v0.14: tracking, predictions, ordering online ----------
def _history(svc, kind, title, days_ago, source="app", category=""):
    from datetime import date, timedelta
    from lifeos import activity
    for i, d in enumerate(days_ago):
        at = (date.today() - timedelta(days=d)).isoformat() + "T10:00:00+00:00"
        if kind == "bought":
            activity.record_bought(svc, title, "me", source, ref=f"h:{title}:{i}", at=at)
        else:
            activity.record_done(svc, title, category, "me", source, ref=f"h:{title}:{i}", at=at)


def test_predicts_household_needs_from_what_was_bought(svc):
    from datetime import date, timedelta
    from lifeos import activity
    _todo_svc(svc)
    _history(svc, "bought", "Andrex toilet roll 16 pack", [64, 43, 22, 1])       # every 3 weeks, like clockwork
    _history(svc, "bought", "Dishwasher tabs", [70, 40, 5])                        # about monthly
    _history(svc, "bought", "Birthday candles", [200])                             # once: nothing to learn
    _history(svc, "done", "Descale the kettle", [125, 65, 3], category="Home maintenance")
    _history(svc, "done", "Clean the gutters", [360], category="Home maintenance")  # once, a year ago
    preds = {p["pid"]: p for p in activity.predict(svc)}
    loo = preds["shopping:toilet roll"]
    assert loo["confidence"] == "high" and loo["interval_days"] == 21
    assert loo["due"] == (date.today() + timedelta(days=20)).isoformat()
    assert "Bought 4 times, about every 3 weeks" in loo["why"]
    assert preds["shopping:dishwasher tablets"]["interval_days"] == 32
    assert "shopping:birthday candle" not in preds
    assert preds["job:descale the kettle"]["kind"] == "job" and preds["job:descale the kettle"]["interval_days"] == 61
    assert preds["job:clean the gutter"]["confidence"] == "low" and "Last year" in preds["job:clean the gutter"]["why"]
    # Already on the Shopping list: not suggested again
    assert "shopping:toilet roll" not in {p["pid"] for p in activity.predict(svc, on_lists={"toilet roll"})}


def test_very_likely_needs_move_across_and_likely_ones_get_a_reminder(svc):
    from datetime import date, timedelta
    from lifeos import activity
    _todo_svc(svc)
    _history(svc, "bought", "Loo roll", [84, 63, 42, 21])                # due today, very likely
    _history(svc, "bought", "Coffee beans", [60, 40, 20])                # due today, likely (only 3 times so far)
    r = activity.daily(svc)
    assert r["added"] == ["Toilet roll -> Shopping"]
    assert "Coffee" in r["reminded"]
    assert [t["title"] for t in svc.google.get().list_tasks("Shopping")] == ["Toilet roll"]
    assert svc.notifier.pushes[-1]["title"] == "Life OS saw these coming"
    assert activity.daily(svc) == {"added": [], "reminded": []}  # once only
    # Bought (ticked off the list): a fresh cycle starts from the new date
    loo = svc.google.get().list_tasks("Shopping")[0]
    svc.google.get().complete_task("Shopping", loo["id"])
    activity.record_bought(svc, "Toilet roll", "me", "app", ref=f"task:{loo['id']}")
    p = activity.find(svc, "shopping:toilet roll")
    assert p["status"] == "new" and p["due"] == (date.today() + timedelta(days=21)).isoformat()


def test_prediction_choices_teach_it(svc):
    from datetime import date, timedelta
    from lifeos import activity
    _todo_svc(svc)
    _history(svc, "bought", "Bin bags", [90, 60, 30, 1])
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    pid = "shopping:bin bags"
    assert any(p["pid"] == pid for p in client.get("/api/predictions").json()["predictions"])
    client.post("/api/predictions/act", json={"pid": pid, "action": "snooze", "days": 7})
    assert not any(p["pid"] == pid for p in client.get("/api/predictions").json()["predictions"])
    svc.store.clear_prediction_state(pid)
    client.post("/api/predictions/act", json={"pid": pid, "action": "never"})
    assert not any(p["pid"] == pid for p in client.get("/api/predictions").json()["predictions"])
    activity.record_bought(svc, "Bin bags", "me", "app", ref="again")  # "never" sticks even when bought again
    assert activity.find(svc, pid) is None
    # A job: Add now puts it in the to-do list with its reason in the notes
    _history(svc, "done", "Bleed the radiators", [400, 220, 30], category="Home maintenance")
    pid = "job:bleed the radiator"
    r = client.post("/api/predictions/act", json={"pid": pid, "action": "add"}).json()
    assert r["list"] == "Home maintenance"
    task = next(t for t in svc.google.get().list_tasks("Home maintenance") if t["id"] == r["id"])
    assert "Predicted by Life OS" in task["notes"]
    assert activity.find(svc, pid) is None  # now an open job, so not predicted twice
    assert client.post("/api/predictions/act", json={"pid": "shopping:nothing", "action": "add"}).status_code == 404
    assert date.today() + timedelta(days=1)


def test_checkups_and_birthdays_are_predicted(svc):
    from datetime import date, timedelta
    from lifeos import activity
    _todo_svc(svc)
    svc.settings.behaviour["household"]["kids"] = ["Ava"]
    svc.settings.behaviour["checkups"] = [{"what": "Dentist check-up", "who": "kids", "every_months": 6},
                                          {"what": "Boiler service", "every_months": 12}]
    soon = (date.today() + timedelta(days=20)).isoformat()
    svc.google.get().add_event("Mum's birthday", soon, soon)
    preds = {p["pid"]: p for p in activity.predict(svc)}
    assert preds["checkup:dentist check-up for ava"]["needs_last"]
    gift = next(p for p in preds.values() if p["kind"] == "gift")
    assert gift["title"] == "Present for Mum" and gift["due"] == (date.today() + timedelta(days=13)).isoformat()
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    over_five_months_ago = (date.today() - timedelta(days=160)).isoformat()
    client.post("/api/predictions/act", json={"pid": "checkup:dentist check-up for ava", "action": "last",
                                              "last": over_five_months_ago})
    p = activity.find(svc, "checkup:dentist check-up for ava")
    assert p["confidence"] == "high" and 15 <= p["days_until"] <= 26
    activity.daily(svc)  # due within 30 days: becomes a to-do (which then lines up the dentist)
    assert any(t["title"] == "Dentist check-up for Ava" for t in svc.google.get().list_tasks("Health and appointments"))


def test_everything_done_is_tracked_wherever_it_happens(svc):
    from lifeos import activity, bridge, todos
    _todo_svc(svc)
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    milk = svc.google.get().add_task("Shopping", "Milk")
    client.post("/api/tasks/complete", json={"list_name": "Shopping", "task_id": milk["id"]})
    eggs = svc.google.get().add_task("Shopping", "Eggs")
    svc.google.get().complete_task("Shopping", eggs["id"])  # ticked in Google Tasks / Gemini
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Descale the kettle", "category": "Home maintenance"}, "x"))
    client.post("/api/todos/complete", json={"list_name": t["list"], "task_id": t["id"]})
    bridge.sync_ticks(svc)
    bridge.sync_ticks(svc)  # seen twice, counted once
    rows = [(r["kind"], r["key"], r["source"]) for r in svc.store.activity()]
    assert sorted(rows) == [("bought", "eggs", "google_tasks"), ("bought", "milk", "app"), ("done", "descale the kettle", "app")]
    assert activity.summary_week(svc)["bought"] == ["eggs", "milk"]


def test_order_shopping_online(svc):
    from lifeos import shop
    _todo_svc(svc)
    g = svc.google.get()
    for item in ("Loo roll", "Dishwasher tablets", "Bananas"):
        g.add_task("Shopping", item)
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    assert client.post("/api/products", json={"item": "toilet roll", "link": "https://www.amazon.co.uk/dp/B07TESTLOO/ref=x",
                                              "title": "Andrex Classic 24"}).json()["asin"] == "B07TESTLOO"
    assert client.post("/api/products", json={"item": "x", "link": "https://example.com"}).status_code == 400
    client.post("/api/products", json={"item": "Dishwasher tabs", "link": "B0DISHTABS"})
    p = client.get("/api/shop?supermarket=sainsburys").json()
    assert [a["title"] for a in p["amazon"]] == ["Loo roll", "Dishwasher tablets"]
    assert p["amazon_basket"] == ("https://www.amazon.co.uk/gp/aws/cart/add.html?ASIN.1=B07TESTLOO&Quantity.1=1"
                                  "&ASIN.2=B0DISHTABS&Quantity.2=1")
    assert p["other"][0]["search_url"] == "https://www.sainsburys.co.uk/gol-ui/SearchResults/Bananas"
    assert p["copy_text"] == "Loo roll\nDishwasher tablets\nBananas" and p["supermarket"]["name"] == "Sainsbury's"
    r = client.post("/api/shop/ordered", json={"task_ids": [a["id"] for a in p["amazon"]], "where": "amazon"}).json()
    assert r["done"] == ["Loo roll", "Dishwasher tablets"]
    assert [t["title"] for t in g.list_tasks("Shopping")] == ["Bananas"]
    assert {r["key"] for r in svc.store.activity("bought")} == {"toilet roll", "dishwasher tablets"}
    assert shop.asin_of("https://www.amazon.co.uk/gp/aws/cart/add.html?ASIN.1=B0ABCDEFGH&Quantity.1=1") == "B0ABCDEFGH"


# ---------- v0.14: quotes, receipts, school emails, dinners → shopping, leaving check ----------
def test_ask_several_firms_for_quotes_and_booking_one_cancels_the_rest(svc):
    from lifeos import solver, todos, trades
    _trade_svc(svc)
    svc.google.people["me"].reply_after = 3600
    svc.store.save_contact(name="Bright Sparks", trade="electrician", email="hello@brightsparks.example")
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Outside socket has stopped working", "category": "Home maintenance"}, "garage"))
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    svc.agent.provider = None
    sol = client.post("/api/todos/solve", json={"list_name": t["list"], "task_id": t["id"]}).json()
    emailable = [i for i, c in enumerate(sol["contacts"]) if c.get("email")]
    assert len(emailable) == 2
    r = client.post("/api/todos/arrange", json={"list_name": t["list"], "task_id": t["id"], "contacts": emailable,
                                                "subject": sol["email"]["subject"], "body": sol["email"]["body"]}).json()
    assert len(r["sent"]) == 2
    bodies = [m["body"] for m in svc.google.people["me"].sent_threads.values()]
    assert any(b.startswith("Hello Dave,") for b in bodies) and any(b.startswith("Hello Bright Sparks,") for b in bodies)
    item = next(x for x in todos.list_todos(svc) if x["id"] == t["id"])
    assert item["next_action"] == "Waiting for quotes from 2 firms" and len(item["quotes"]) == 2
    first, second = r["sent"]
    trades.book(svc, first["id"], "2026-12-01T09:00:00", 60, "me")
    assert svc.store.get_request(second["id"])["status"] == "cancelled"
    assert solver  # imported for the solve endpoint


def test_receipts_teach_what_you_buy_and_warranties_become_renewals(svc):
    from lifeos import activity, mailreader
    _todo_svc(svc)
    g = svc.google.people["me"]
    g.search_mail = lambda q, limit=15: [{"id": "r1", "from": "Tesco", "subject": "Your order confirmation",
                                          "date": "2026-11-20", "link": "https://mail/r1"}] if "receipt" in q else []
    g.message_text = lambda mid, limit=12000: "Andrex toilet tissue 9 pack x2\nBosch kettle £39 with 2 year guarantee"
    claude = ScriptedClaude([
        ([tool_use("1", "save_receipt", {"is_receipt": True, "retailer": "Tesco", "date": "2026-11-20", "items": [
            {"name": "Andrex toilet tissue 9 pack", "quantity": 2, "consumable": True, "warranty_months": 0},
            {"name": "Bosch kettle", "quantity": 1, "consumable": False, "warranty_months": 24}]})], "tool_use"),
        ([text("Saved.")], "end_turn")])
    did = mailreader.read_receipts(svc, Agent(svc, client=claude))
    assert did == ["receipt:Tesco:2"]
    bought = svc.store.activity(kind="bought")
    assert [a["key"] for a in bought] == ["toilet roll"]  # the kettle isn't something you run out of
    assert bought[0]["source"] == "receipt"
    renewal = next(r for r in svc.store.renewals() if r["name"] == "Warranty: Bosch kettle")
    assert renewal["due_date"] == "2028-11-20"
    # Read once: running again does nothing (and asks the AI nothing)
    calls = len(claude.calls)
    assert mailreader.read_receipts(svc, Agent(svc, client=claude)) == [] and len(claude.calls) == calls
    assert activity.normalise("Andrex toilet tissue 9 pack") == "toilet roll"


def test_school_emails_become_events_deadlines_and_kit_reminders(svc):
    from datetime import date, timedelta
    from lifeos import mailreader, todos
    _todo_svc(svc)
    g = svc.google.people["me"]
    assert mailreader.read_school(svc, None) == []  # no school senders set: never reads anything
    svc.settings.behaviour["school"] = {"senders": ["office@stmarys.example"]}
    g.search_mail = lambda q, limit=15: [{"id": "s1", "from": "office@stmarys.example", "subject": "Year 3 news",
                                          "link": "https://mail/s1"}] if "stmarys" in q else []
    g.message_text = lambda mid, limit=12000: "Trip on the 3rd, forms by the 1st, PE kit Thursday"
    trip = (date.today() + timedelta(days=9)).isoformat()
    form = (date.today() + timedelta(days=5)).isoformat()
    pe = (date.today() + timedelta(days=3)).isoformat()
    claude = ScriptedClaude([
        ([tool_use("1", "save_school_items", {
            "events": [{"title": "Year 3 trip to the museum", "date": trip, "start": "09:00", "end": "15:00", "child": "Ava"}],
            "deadlines": [{"title": "Return trip consent form", "due": form, "child": "Ava"}],
            "kit": [{"item": "PE kit", "date": pe, "child": "Ava"}]})], "tool_use"),
        ([text("Saved.")], "end_turn")])
    did = mailreader.read_school(svc, Agent(svc, client=claude))
    assert len(did) == 3
    events = g.events(days=30)
    assert any(e["summary"] == "Year 3 trip to the museum (Ava)" and e["start"] == f"{trip}T09:00:00" for e in events)
    items = {x["title"]: x for x in todos.list_todos(svc)}
    assert items["Return trip consent form for Ava"]["due"] == form
    pack = next(x for x in items.values() if x["title"].startswith("Pack PE kit for Ava"))
    assert pack["due"] == (date.fromisoformat(pe) - timedelta(days=1)).isoformat()
    assert mailreader.read_school(svc, Agent(svc, client=claude)) == []  # each email read once


def test_school_email_without_ai_still_becomes_a_to_do(svc):
    from lifeos import mailreader, todos
    _todo_svc(svc)
    svc.settings.behaviour["school"] = {"senders": ["@stmarys.example"]}
    svc.google.people["me"].search_mail = lambda q, limit=15: [{"id": "s2", "from": "head@stmarys.example",
                                                                 "subject": "Nativity costumes", "link": "#"}]
    mailreader.read_school(svc, None)
    assert any(x["title"] == "Read school email: Nativity costumes" for x in todos.list_todos(svc))


def test_dinners_put_their_ingredients_on_shopping(svc):
    from lifeos import household
    _todo_svc(svc)
    today = household.today_local(svc.settings)
    svc.store.set_meal("2026-01-05", "Chicken fajitas", ["chicken thighs", "peppers", "tortilla wraps", "salt"], "")
    svc.store.set_meal(today.isoformat(), "Chicken fajitas", [], "")
    svc.store.set_meal((today + __import__("datetime").timedelta(days=1)).isoformat(), "Lamb tagine", [], "")
    svc.google.get().add_task("Shopping", "Peppers")
    claude = ScriptedClaude([
        ([tool_use("1", "save_ingredients", {"meals": [{"dinner": "Lamb tagine",
                                                         "ingredients": ["diced lamb", "apricots", "couscous", "olive oil"]}]})],
         "tool_use"),
        ([text("Saved.")], "end_turn")])
    r = household.meals_to_shopping(svc, Agent(svc, client=claude))
    assert r["added"] == ["chicken thighs", "tortilla wraps", "diced lamb", "apricots", "couscous"]
    assert r["already_on_list"] == ["peppers"] and r["unknown"] == []
    assert "Chicken fajitas" not in claude.calls[0]["messages"][0]["content"]  # known dinners aren't asked about again
    again = household.meals_to_shopping(svc, Agent(svc, client=ScriptedClaude([])))
    assert again["added"] == []


class LeavingHA:
    def __init__(self):
        self.calls = []
        self.s = {
            "lock.front_door": ("unlocked", {"friendly_name": "Front door"}),
            "binary_sensor.back_door": ("on", {"friendly_name": "Back door", "device_class": "door"}),
            "light.landing": ("on", {"friendly_name": "Landing light"}),
            "light.fish_tank": ("on", {"friendly_name": "Fish tank"}),
            "switch.straighteners": ("on", {"friendly_name": "Hair straighteners"}),
            "switch.fridge": ("on", {"friendly_name": "Fridge"}),
            "climate.living_room": ("heat", {"friendly_name": "Heating", "temperature": 20.5}),
        }

    def states(self, domain=None):
        return [{"entity_id": k, "state": v[0], "attributes": v[1]} for k, v in self.s.items()]

    def call_service(self, service, data=None):
        self.calls.append((service, data))
        eid = data["entity_id"]
        st, attrs = self.s[eid]
        if service == "lock.lock":
            self.s[eid] = ("locked", attrs)
        elif service.endswith("turn_off"):
            self.s[eid] = ("off", attrs)
        elif service == "climate.set_temperature":
            self.s[eid] = (st, {**attrs, "temperature": data["temperature"]})
        return []


def test_leaving_home_check_offers_only_safe_fixes(svc):
    svc.ha = LeavingHA()
    svc.settings.behaviour["leaving"] = {"eco_temp": 16, "switch_off": ["switch.straighteners"], "ignore": ["light.fish_tank"]}
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "me"})
    r = client.get("/api/home/leaving").json()
    by = {i["entity_id"]: i for i in r["issues"]}
    assert set(by) == {"lock.front_door", "binary_sensor.back_door", "light.landing", "switch.straighteners",
                       "climate.living_room"}  # fridge isn't listed; the fish tank is ignored
    assert by["binary_sensor.back_door"]["fix"] is None  # a door can only be closed by hand
    assert by["climate.living_room"]["fix"]["label"] == "Down to 16°C"
    for eid in ("lock.front_door", "light.landing", "switch.straighteners", "climate.living_room"):
        assert client.post("/api/home/leaving/fix", json={"entity_id": eid}).json()["ok"]
    assert [c[0] for c in svc.ha.calls] == ["lock.lock", "light.turn_off", "switch.turn_off", "climate.set_temperature"]
    assert svc.ha.calls[-1][1] == {"entity_id": "climate.living_room", "temperature": 16.0}
    # Asking to "fix" the door, or anything not offered, does nothing
    assert client.post("/api/home/leaving/fix", json={"entity_id": "binary_sensor.back_door"}).json() == {"ok": True, "already": True}
    assert client.post("/api/home/leaving/fix", json={"entity_id": "switch.fridge"}).json() == {"ok": True, "already": True}
    assert len(svc.ha.calls) == 4
    left = client.get("/api/home/leaving").json()
    assert [i["entity_id"] for i in left["issues"]] == ["binary_sensor.back_door"] and not left["ok"]


# ---------- v0.17.2: notifications start even with an unusable key file ----------
def test_unusable_notification_key_is_kept_aside_and_replaced(svc, tmp_path):
    from lifeos.integrations.webpush import WebPusher
    for bad in (b"", b"not a key at all", b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"):
        folder = tmp_path / f"case{len(bad)}"
        folder.mkdir()
        key = folder / "vapid_private.pem"
        key.write_bytes(bad)
        pusher = WebPusher(svc.store, str(key))
        assert len(pusher.public_key) == 87
        aside = list(folder.glob("vapid_private.pem.unreadable-*"))
        assert len(aside) == 1 and aside[0].read_bytes() == bad  # the old file is kept, never deleted
        assert WebPusher(svc.store, str(key)).public_key == pusher.public_key  # the new key is reused after


def test_notification_problem_is_explained(svc):
    from lifeos import doctor
    svc.pusher, svc.push_problem = None, "ImportError: No module named 'py_vapid'"
    client, _ = _app_client(svc)
    client.post("/api/login", json={"key": "test-key", "person": "wife"})
    r = client.get("/api/push/key")
    assert r.status_code == 503 and "py_vapid" in r.json()["detail"] and "Restart Life OS" in r.json()["detail"]
    rows = doctor.check_notifications(svc)
    assert rows[0][0] == doctor.FAIL and "py_vapid" in rows[0][1]


def test_build_services_records_why_notifications_failed(tmp_path, monkeypatch):
    from lifeos import app as app_module
    from lifeos.config import Settings
    import lifeos.integrations.webpush as wp

    def broken(*a, **k):
        raise RuntimeError("key folder is read-only")
    monkeypatch.setattr(wp, "WebPusher", broken)
    s = Settings(db_path=str(tmp_path / "x.db"), google_token_file=str(tmp_path / "secrets" / "google_token.json"))
    svc = app_module.build_services(s)
    assert svc.pusher is None and svc.push_problem == "RuntimeError: key folder is read-only"
