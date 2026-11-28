"""v0.18: Home Assistant conversation agent, deliveries, bin days, babysitter sheet, date night, kids' reading and
homework with a star chart, the cleaning rota and robot vacuum, car running costs, the live test, plain-English
assistant errors, the household key from the menu bar, and the sign-in cookie on this computer."""
from __future__ import annotations

import io
import py_compile
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import FakeHA, ScriptedClaude, text  # noqa: E402
from test_v016_features import client_for, ev, household_svc  # noqa: E402

from lifeos import household  # noqa: E402


def today(svc) -> date:
    return household.today_local(svc.settings)


class HouseHA(FakeHA):
    """People, a bin calendar and a robot vacuum."""

    def __init__(self, home=("Joe",), bins=None):
        super().__init__()
        self.home, self.bins = set(home), bins or []

    def states(self, domain=None):
        items = [{"entity_id": "person.joe", "state": "home" if "Joe" in self.home else "not_home",
                  "attributes": {"friendly_name": "Joe"}},
                 {"entity_id": "person.sue", "state": "home" if "Sue" in self.home else "not_home",
                  "attributes": {"friendly_name": "Sue"}},
                 {"entity_id": "calendar.bins", "state": "off", "attributes": {"friendly_name": "Bin collections"}},
                 {"entity_id": "calendar.family", "state": "off", "attributes": {"friendly_name": "Family"}},
                 {"entity_id": "vacuum.robot", "state": "docked", "attributes": {"friendly_name": "Robot"}}]
        return [s for s in items if not domain or s["entity_id"].startswith(domain + ".")]

    def calendar_events(self, entity_id, start, end):
        return [{"summary": n, "start": {"date": d}, "end": {"date": d}} for d, n in self.bins]


# ---------- bin days ----------
def test_bins_from_a_home_assistant_calendar_and_the_night_before(svc):
    t = today(svc)
    tomorrow = (t + timedelta(days=1)).isoformat()
    svc.ha = HouseHA(bins=[(tomorrow, "recycling"), (tomorrow, "Garden waste"), ((t + timedelta(days=8)).isoformat(), "Black bin")])
    from lifeos import bins
    o = bins.overview(svc, t)
    assert o["calendars"][0]["id"] == "calendar.bins" and o["source"] == "home assistant"
    assert o["next"] == {"date": tomorrow, "bins": ["Recycling", "Garden waste"], "source": "home assistant", "when": "tomorrow"}
    assert bins.spoken(svc, t) == "Tonight: recycling and garden waste go out for tomorrow."
    from lifeos import voice
    early = datetime.combine(t, datetime.min.time(), voice._tz(svc)).replace(hour=18)
    from lifeos import tomorrow
    tomorrow.save(svc, on=False)  # the separate bins reminder (v0.20 folds it into one "Tomorrow" message)
    assert bins.evening(svc, early) == ""  # before 19:30
    late = early.replace(hour=19, minute=40)
    assert bins.evening(svc, late) == "Bins tonight: recycling and garden waste."
    assert svc.notifier.pushes[-1]["title"] == "Bins"
    assert bins.evening(svc, late.replace(minute=50)) == ""  # once a night


def test_bins_from_a_typed_rota_every_other_week(svc):
    from lifeos import bins
    t = today(svc)
    nxt_tue = t + timedelta(days=(1 - t.weekday()) % 7 or 7)
    with pytest.raises(ValueError, match="isn't a Tuesday"):
        bins.save(svc, rota=[{"bin": "Black", "day": "tue", "every": 2, "from": (nxt_tue + timedelta(days=1)).isoformat()}])
    bins.save(svc, rota=[{"bin": "Black bin", "day": "tue", "every": 2, "from": nxt_tue.isoformat()},
                         {"bin": "Recycling", "day": "tue", "every": 2, "from": (nxt_tue + timedelta(days=7)).isoformat()},
                         {"bin": "Food caddy", "day": "tue", "every": 1}], remind_at="20:00")
    got = bins.collections(svc, days=15, today=t)
    first = next(c for c in got if c["date"] == nxt_tue.isoformat())
    second = next(c for c in got if c["date"] == (nxt_tue + timedelta(days=7)).isoformat())
    assert first["bins"] == ["Black bin", "Food caddy"] and second["bins"] == ["Recycling", "Food caddy"]
    assert bins.cfg(svc)["remind_at"] == "20:00"
    with pytest.raises(ValueError):
        bins.save(svc, remind_at="8pm")


def test_bins_answer_on_the_speaker(svc):
    from lifeos import bins, voice
    t = today(svc)
    svc.ha = HouseHA(bins=[((t + timedelta(days=3)).isoformat(), "Black bin")])
    assert voice.answer(svc, "bins").startswith("Next collection") and "black bin" in voice.answer(svc, "bins")
    assert bins.collections(svc, today=t)[0]["bins"] == ["Black bin"]


# ---------- deliveries ----------
def test_delivery_emails_without_ai_and_status_never_goes_backwards(svc):
    from lifeos import deliveries
    t = today(svc)
    g = deliveries.guess({"from": "Evri <noreply@evri.com>", "subject": "Your parcel is out for delivery today"}, t)
    assert g["status"] == "out_for_delivery" and g["carrier"] == "Evri" and g["expected_date"] == t.isoformat()
    assert deliveries.guess({"from": "Shop", "subject": "Your weekly newsletter"}, t) == {"is_delivery": False}
    assert deliveries.guess({"from": "Royal Mail", "subject": "Sorry we missed you"}, t)["status"] == "failed"
    p = deliveries.save(svc, {"is_delivery": True, "status": "delivered", "retailer": "Argos", "item": "Kettle",
                              "tracking_number": "AB 123", "tracking_url": "javascript:alert(1)"}, "m1")
    assert p["tracking_url"] == ""  # only https links are kept
    again = deliveries.save(svc, {"is_delivery": True, "status": "dispatched", "tracking_number": "AB123"}, "m2")
    assert again["status"] == "delivered" and again["item"] == "Kettle"
    deliveries.save(svc, {"is_delivery": True, "status": "dispatched", "retailer": "Amazon", "item": "Socks",
                          "expected_date": (t + timedelta(days=2)).isoformat()}, "m3")
    deliveries.save(svc, {"is_delivery": True, "status": "out_for_delivery", "retailer": "Amazon", "item": "Lego",
                          "window": "10:00 to 11:00"}, "m4")
    c = deliveries.current(svc, t)
    assert [x["item"] for x in c["today"]] == ["Lego"] and [x["item"] for x in c["soon"]] == ["Socks"]
    assert [x["item"] for x in c["recent"]] == ["Kettle"]


def test_delivery_emails_are_read_once_and_labelled(svc):
    from lifeos import deliveries
    household_svc(svc)
    g = svc.google.get("me")
    g.has_gmail = True
    g.search_mail = lambda q, limit=15: [{"id": "e1", "from": "DPD", "subject": "Your DPD parcel is on its way",
                                          "snippet": "", "link": "#"}]
    g.message_text = lambda mid: "Your parcel from John Lewis is arriving tomorrow between 10:00 and 11:00."
    did = deliveries.read_emails(svc, None)
    assert did and did[0].endswith(":dispatched")
    assert deliveries.read_emails(svc, None) == []  # read once
    p = svc.store.records("parcel")[0]
    assert p["carrier"] == "DPD" and p["expected"] == (today(svc) + timedelta(days=1)).isoformat()


def test_parcel_nudge_only_when_nobody_is_in(svc):
    from lifeos import deliveries
    t = today(svc)
    deliveries.save(svc, {"is_delivery": True, "status": "out_for_delivery", "item": "Lego", "carrier": "Evri",
                          "tracking_url": "https://evri.example/track/1"}, "m1")
    svc.ha = HouseHA(home=("Joe",))
    assert deliveries.nudge(svc, t) == []
    svc.ha = HouseHA(home=())
    sent = deliveries.nudge(svc, t)
    assert len(sent) == 1
    push = svc.notifier.pushes[-1]
    assert "nobody seems to be in" in push["message"] and push["actions"][0]["url"] == "https://evri.example/track/1"
    assert deliveries.nudge(svc, t) == []  # once a day


def test_today_and_display_show_parcels_and_bins(svc):
    from lifeos import deliveries
    household_svc(svc)
    t = today(svc)
    svc.ha = HouseHA(bins=[((t + timedelta(days=1)).isoformat(), "Recycling")])
    deliveries.save(svc, {"is_delivery": True, "status": "out_for_delivery", "item": "Lego"}, "m1")
    c = client_for(svc)
    day = c.get("/api/today").json()
    assert day["parcels"]["today"][0]["item"] == "Lego" and day["bins_tomorrow"] == ["Recycling"]
    wall = c.get("/api/wall").json()
    assert wall["parcels"][0]["name"] == "Lego" and wall["bins"]["bins"] == ["Recycling"]
    assert [k["kid"] for k in wall["stars"]] == ["Ava", "Leo"]
    r = c.post("/api/deliveries/mark", json={"id": day["parcels"]["today"][0]["id"], "status": "delivered"})
    assert r.json()["status"] == "delivered"


# ---------- babysitter sheet ----------
def test_babysitter_sheet_uses_the_manual_but_not_private_entries(svc):
    from lifeos import manual, sitter
    household_svc(svc)
    manual.save(svc, "Stopcock", "Under the sink", "Water")
    manual.save(svc, "Alarm", "Code is 1234", "Safety", private=True)
    svc.store.save_contact(None, name="Megan", trade="babysitter", phone="07700 900777")
    sh = sitter.sheet(svc)
    assert [k["name"] for k in sh["kids"]] == ["Ava", "Leo"] and [p["name"] for p in sh["parents"]] == ["Joe", "Sue"]
    assert [w["title"] for w in sh["where"]] == ["Stopcock"] and sh["sitters"][0]["name"] == "Megan"
    assert any("999" in e for e in sh["emergency"]) and any("111" in e for e in sh["emergency"])
    c = client_for(svc)
    r = c.post("/api/sitter", json={"kids": [{"name": "Ava", "bedtime": "19:30", "notes": "Night light"}],
                                    "include_manual": False, "rules": "No screens after 7"})
    assert r.json()["kids"][0]["bedtime"] == "19:30" and r.json()["where"] == []
    assert Path(__file__).resolve().parent.parent.joinpath("web", "sitter.html").exists()


# ---------- date night ----------
def test_date_night_finds_evenings_both_are_free_and_never_sends(svc):
    from lifeos import datenight
    household_svc(svc)
    t = today(svc)
    busy_day = t + timedelta(days=2)
    svc.google.get("wife")._events.append(ev("Book club", busy_day, "19:30", "21:30"))
    svc.google.get("me")._events.append(ev("Work drinks", t + timedelta(days=3), "18:00", "20:00"))
    nights = datenight.evenings(svc, weeks=1)
    dates = {n["date"] for n in nights}
    assert busy_day.isoformat() not in dates and (t + timedelta(days=3)).isoformat() not in dates and dates
    msg = datenight.message(svc, nights[0]["date"], "Megan Clarke")
    assert msg.startswith("Hi Megan, are you free to babysit with Ava and Leo") and "7pm to 11pm" in msg and "Joe and Sue" in msg
    cid = svc.store.save_contact(None, name="Megan Clarke", trade="babysitter", email="megan@example.com")
    c = client_for(svc)
    d = c.post("/api/date-night/draft", json={"date": nights[0]["date"], "contact_id": cid}).json()
    me = svc.google.get("me")
    assert d["ok"] and me.drafts[d["draft"]]["to"] == "megan@example.com" and not getattr(me, "sent_threads", {})
    assert c.post("/api/date-night/book", json={"date": nights[0]["date"]}).json()["ok"]
    assert any(e["summary"] == "Date night" for e in me._events)
    ideas = datenight.ideas(svc, None)
    assert ideas["ideas"][0]["link"].startswith("https://www.google.com/maps/search/")


# ---------- kids: reading, homework, stars ----------
def test_kids_tablet_logs_reading_and_ticks_homework_but_cannot_add_it(svc):
    from lifeos import kidstuff
    household_svc(svc)
    t = today(svc)
    hw = kidstuff.add_homework(svc, "Ava", "Spellings", (t + timedelta(days=2)).isoformat(), "me")
    from fastapi.testclient import TestClient

    from lifeos.app import create_app
    tab = TestClient(create_app(svc, run_scheduler=False))
    tab.post("/api/login", json={"key": "test-key", "person": "kids"})
    r = tab.post("/api/kids/reading", json={"kid": "Ava", "minutes": 20, "book": "The Twits"})
    assert r.status_code == 200
    assert tab.post("/api/kids/homework/done", json={"id": hw["id"]}).status_code == 200
    assert tab.post("/api/kids/homework", json={"kid": "Ava", "title": "More homework"}).status_code == 401
    assert tab.post("/api/kids/homework/remove", json={"id": hw["id"]}).status_code == 401
    assert tab.post("/api/chores/paid", json={"who": "Ava"}).status_code == 401
    board = tab.get("/api/kids/extras").json()
    ava = next(k for k in board["kids"] if k["kid"] == "Ava")
    assert ava["reading"][0]["undo"] and ava["homework"][0]["done"] and ava["books"] == ["The Twits"]
    stars = next(k for k in board["stars"]["kids"] if k["kid"] == "Ava")
    assert stars["total"] == 3  # 1 for reading 20 minutes, 2 for homework done early
    assert tab.post("/api/kids/extras/undo", json={"kind": "homework", "id": hw["id"]}).json()["ok"]
    rid = ava["reading"][0]["id"]
    rec = svc.store.get("reading", rid)
    svc.store.put("reading", rid, {**{k: v for k, v in rec.items() if k not in ("id", "at", "updated_at")}, "ts": 0},
                  at=rec["at"])
    r = tab.post("/api/kids/extras/undo", json={"kind": "reading", "id": rid})
    assert r.status_code == 400 and "grown-up" in r.json()["detail"]
    with pytest.raises(ValueError):
        kidstuff.log_reading(svc, "Ava", 0)
    with pytest.raises(ValueError):
        kidstuff.log_reading(svc, "Bob", 10)


# ---------- cleaning rota and robot vacuum ----------
def test_cleaning_rota_shares_points_fairly_and_swaps(svc):
    from lifeos import cleaning
    household_svc(svc)
    t = today(svc)
    w = cleaning.week(svc, t)
    pts: dict[str, int] = {}
    for j in w["jobs"]:
        pts[j["who"]] = pts.get(j["who"], 0) + j["points"]
    assert set(pts) == {"me", "wife"} and abs(pts["me"] - pts["wife"]) <= 2
    first = w["jobs"][0]
    cleaning.tick(svc, first["id"], "wife", t)
    with pytest.raises(ValueError, match="already ticked"):
        cleaning.tick(svc, first["id"], "me", t)
    f = cleaning.fairness(svc, t)
    assert next(s for s in f["shares"] if s["person"] == "wife")["pct"] == 100
    other = next(j for j in w["jobs"] if not j["done"] and j["id"] != first["id"])
    swapped = cleaning.swap(svc, other["id"], t)
    assert next(j for j in swapped["jobs"] if j["id"] == other["id"])["who"] != other["who"]
    assert cleaning.untick(svc, first["id"], t)
    filed = cleaning.file_todos(svc, t)
    assert filed["filed"] == len(w["jobs"])


def test_robot_vacuum_runs_when_the_house_is_empty_once_a_day(svc):
    from lifeos import cleaning, voice
    household_svc(svc)
    svc.ha = HouseHA(home=("Joe",))
    cleaning.save_settings(svc, vacuum={"entity": "vacuum.robot", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                        "latest": "16:00"})
    tz = voice._tz(svc)
    ten = datetime.combine(today(svc), datetime.min.time(), tz).replace(hour=10)
    assert cleaning.vacuum_tick(svc, ten) == ""  # Joe's in
    svc.ha.home = set()
    assert cleaning.vacuum_tick(svc, ten.replace(hour=11)) == "the house is empty"
    assert ("vacuum.start", {"entity_id": "vacuum.robot"}) in svc.ha.calls
    assert cleaning.vacuum_tick(svc, ten.replace(hour=12)) == ""  # once a day
    with pytest.raises(ValueError):
        cleaning.save_settings(svc, vacuum={"entity": "lock.front_door"})


# ---------- car running costs ----------
MOT_JSON = {"registration": "KX19ABC", "motTests": [
    {"completedDate": "2025-12-01T10:00:00.000Z", "testResult": "PASSED", "odometerValue": "44120", "odometerUnit": "MI",
     "odometerResultType": "READ", "defects": [{"text": "Nearside front tyre worn close to legal limit", "type": "ADVISORY"},
                                               {"text": "Brake disc worn", "type": "MINOR"}]},
    {"completedDate": "2024-11-20T10:00:00.000Z", "testResult": "FAILED", "odometerValue": "60000", "odometerUnit": "KM",
     "odometerResultType": "READ", "defects": [{"text": "Headlamp aim too high", "type": "MAJOR"}]}]}


def test_car_costs_per_mile_service_and_mot_advisories(svc, monkeypatch):
    from lifeos import carcosts, cars
    household_svc(svc)
    cars.add_car(svc, "KX19 ABC", "The Golf", mot_due="2027-01-10")
    t = today(svc)
    carcosts.add_cost(svc, "KX19ABC", "fuel", 60, (t - timedelta(days=100)).isoformat(), 40000, 40)
    carcosts.add_cost(svc, "KX19ABC", "fuel", 60, (t - timedelta(days=10)).isoformat(), 42000, 40)
    carcosts.add_cost(svc, "KX19ABC", "service", 180, (t - timedelta(days=50)).isoformat(), 41000)
    with pytest.raises(ValueError):
        carcosts.add_cost(svc, "KX19ABC", "sweets", 3)
    s = carcosts.summary(svc, "KX19ABC", t)
    assert s["total_12m"] == 300 and s["per_mile"] == 0.15 and s["pence_per_litre"] == 150.0
    assert s["service"]["why"] in ("time", "mileage") and s["miles_a_year"]
    tests = carcosts.parse_mot(MOT_JSON)
    assert tests[0]["advisories"] == ["Nearside front tyre worn close to legal limit", "Brake disc worn"]
    assert tests[1]["failures"] == ["Headlamp aim too high"] and tests[1]["miles"] == 37282

    class FakeHttp:
        def post(self, url, data=None, timeout=None):
            assert data["grant_type"] == "client_credentials" and data["scope"] == carcosts.MOT_SCOPE
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"access_token": "tok", "expires_in": 3600})

        def get(self, url, headers=None, timeout=None):
            assert url.endswith("/KX19ABC") and headers["X-API-Key"] == "k" and headers["Authorization"] == "Bearer tok"
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: MOT_JSON)
    for k, v in (("CLIENT_ID", "i"), ("CLIENT_SECRET", "s"), ("API_KEY", "k"), ("TOKEN_URL", "https://login.example/token")):
        monkeypatch.setenv(f"MOT_{k}", v)
    carcosts._token.clear()
    got = carcosts.fetch_mot(svc, "KX19ABC", client=FakeHttp())
    assert got["mot_api"] and got["advisories"][0]["text"].startswith("Nearside front tyre")
    filed = carcosts.advisory_todo(svc, "KX19ABC", got["advisories"][0]["id"], "me")
    assert "nearside front tyre" in filed["title"].lower()
    assert carcosts.summary(svc, "KX19ABC")["advisories"][0]["added"]


# ---------- live test ----------
def test_live_test_tries_everything_cleans_up_and_sends_nothing(svc):
    from lifeos import livetest
    household_svc(svc)
    me, wife = svc.google.get("me"), svc.google.get("wife")
    me._label_id = lambda name: "Label_1"
    r = livetest.run(svc)
    assert r["failed"] == 0 and r["sent"] == 0 and r["ok"] >= 12
    areas = {s["area"] for s in r["steps"]}
    assert {"Joe: Google Tasks", "Joe: Google Calendar", "Joe: Gmail", "Sue: Gmail"} <= areas
    assert not me.drafts and not getattr(me, "sent_threads", {})
    assert not any(t["list"] == livetest.TEST_LIST for t in me.tasks + wife.tasks)
    assert not any(e["summary"] == livetest.TITLE for e in me._events)
    wife.add_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("invalid_grant: Token has been expired"))
    r2 = livetest.run(svc)
    bad = [s for s in r2["steps"] if not s["ok"]]
    assert bad and "Reconnect" in bad[0]["detail"]
    assert "Nothing was sent" in livetest.text(r2)
    c = client_for(svc)
    assert c.get("/api/system/live-test").json()["report"]["failed"] == len(bad)


# ---------- Home Assistant conversation agent ----------
def test_converse_keeps_the_conversation_and_asks_follow_ups(svc):
    from lifeos.agent import Agent
    from lifeos.app import create_app
    from fastapi.testclient import TestClient
    claude = ScriptedClaude([([text("Which day, Saturday or Sunday?")], "end_turn"), ([text("Booked for Saturday.")], "end_turn")])
    c = TestClient(create_app(svc, Agent(svc, client=claude), run_scheduler=False))
    assert c.post("/api/voice/converse", json={"text": "hi"}).status_code == 401
    h = {"X-LifeOS-Key": "test-key"}
    a = c.post("/api/voice/converse", json={"text": "book the plumber", "conversation_id": "abc"}, headers=h).json()
    assert a["continue"] and a["say"] == "Which day, Saturday or Sunday?"
    b = c.post("/api/voice/converse", json={"text": "Saturday", "conversation_id": "abc"}, headers=h).json()
    assert not b["continue"] and b["say"] == "Booked for Saturday."
    sent = claude.calls[1]["messages"]
    assert [m["content"] for m in sent[:2]] == ["book the plumber", "Which day, Saturday or Sunday?"]


def test_home_assistant_integration_files_are_valid(tmp_path, svc):
    from lifeos import assist
    z = zipfile.ZipFile(io.BytesIO(assist.integration_zip("0.18.0")))
    names = set(z.namelist())
    assert {"lifeos/manifest.json", "lifeos/__init__.py", "lifeos/config_flow.py", "lifeos/conversation.py",
            "lifeos/strings.json", "lifeos/translations/en.json"} <= names
    import json
    m = json.loads(z.read("lifeos/manifest.json"))
    assert m["domain"] == "lifeos" and m["config_flow"] and m["version"] == "0.18.0"
    for n in names:
        if n.endswith(".py"):
            f = tmp_path / n.replace("/", "_")
            f.write_bytes(z.read(n))
            py_compile.compile(str(f), doraise=True)
    conv = z.read("lifeos/conversation.py").decode()
    assert "_async_handle_message" in conv and "continue_conversation" in conv and "/api/voice/converse" in conv
    c = client_for(svc)
    r = c.get("/api/voice/integration.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"


# ---------- plain-English assistant errors, the household key page, the sign-in cookie, Settings ----------
def test_assistant_errors_in_plain_english():
    from lifeos import aierrors

    class BadRequestError(Exception):
        status_code = 400
    e = BadRequestError("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': "
                        "'Your credit balance is too low to access the Anthropic API.'}}")
    assert "no credit" in aierrors.explain(e, "claude") and "Gemini" in aierrors.explain(e, "claude")

    class ClientError(Exception):
        code = 429
    assert "free daily allowance" in aierrors.explain(ClientError("429 RESOURCE_EXHAUSTED quota PerDay free_tier"), "gemini")
    assert "isn't included" in aierrors.explain(ClientError("429 RESOURCE_EXHAUSTED limit: 0, free_tier"), "gemini")

    class AuthenticationError(Exception):
        status_code = 401
    assert "API key isn't right" in aierrors.explain(AuthenticationError("invalid x-api-key"), "claude")


def test_ask_shows_the_plain_reason(svc):
    from fastapi.testclient import TestClient

    from lifeos.agent import Agent
    from lifeos.app import create_app

    class Broken:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **k):
            err = Exception("Your credit balance is too low to access the Anthropic API.")
            err.status_code = 400
            raise err
    c = TestClient(create_app(svc, Agent(svc, client=Broken()), run_scheduler=False))
    c.post("/api/login", json={"key": "test-key", "person": "me"})
    r = c.post("/api/ask", json={"text": "hello"})
    assert r.status_code == 502 and "no credit" in r.json()["detail"]


def test_household_key_page_only_from_the_menu_bar(svc, monkeypatch):
    from lifeos import desktop
    c = client_for(svc)
    assert c.get("/local/key?t=anything").status_code == 404
    ctl = desktop.Controller("normal", 8800)
    monkeypatch.setattr(desktop, "controller", ctl)
    link = ctl.key_page()
    token = link.split("t=")[1]
    assert c.get("/local/key?t=wrong").status_code == 404
    r = c.get(f"/local/key?t={token}")
    assert r.status_code == 200 and "test-key" in r.text and r.headers["cache-control"] == "no-store"
    ctl.key_token_until = 0
    assert c.get(f"/local/key?t={token}").status_code == 404


def test_sign_in_cookie_is_not_secure_on_this_computers_http_page(svc, monkeypatch):
    from fastapi.testclient import TestClient

    from lifeos.app import create_app
    svc.settings.public_url = "https://lifeos.tail1234.ts.net"
    local = TestClient(create_app(svc, run_scheduler=False), base_url="http://localhost:8800")
    r = local.post("/api/login", json={"key": "test-key", "person": "me"})
    assert "secure" not in r.headers["set-cookie"].lower()
    remote = TestClient(create_app(svc, run_scheduler=False), base_url="https://lifeos.tail1234.ts.net")
    r = remote.post("/api/login", json={"key": "test-key", "person": "me"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_gemini_model_and_mot_keys_from_settings(svc, monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("LIFEOS_API_KEY=test-key\n")
    from lifeos import paths, setup_core
    monkeypatch.setattr(paths, "env_file", lambda: tmp_path / ".env")
    c = client_for(svc)
    assert c.post("/api/system/keys", json={"gemini_model": "gpt-4"}).status_code == 400
    r = c.post("/api/system/keys", json={"gemini_model": "gemini-3.8-flash", "mot_api_key": "abc",
                                         "mot_token_url": "https://login.example/token"})
    assert r.status_code == 200 and {"GEMINI_MODEL", "GEMINI_MODEL_DEEP", "MOT_API_KEY", "MOT_TOKEN_URL"} <= set(r.json()["changed"])
    env = setup_core.read_env(tmp_path / ".env")
    assert env["GEMINI_MODEL"] == env["GEMINI_MODEL_DEEP"] == "gemini-3.8-flash"
    assert c.post("/api/system/keys", json={"mot_token_url": "http://login.example"}).status_code == 400
