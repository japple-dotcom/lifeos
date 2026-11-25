"""v0.16: voice (Google speakers through Home Assistant, Assist, Gemini shopping), the first-day checklist, bills,
trips, attachments, the school run, pocket money goals, the energy report and the life admin hour."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import ScriptedClaude, text, tool_use  # noqa: E402

from lifeos import household  # noqa: E402
from lifeos.agent import Agent  # noqa: E402

TODAY = date.today()


def household_svc(svc, kids=("Ava", "Leo")):
    from lifeos.accounts import GoogleAccounts
    from lifeos.demo import DemoGoogle
    svc.settings.household["names"] = {"me": "Joe", "wife": "Sue"}
    svc.settings.household["kids"] = list(kids)
    svc.google = GoogleAccounts(svc.settings, people={"me": DemoGoogle("me", []), "wife": DemoGoogle("wife", [])})
    return svc


def ev(summary, day: date, start="09:00", end="17:00"):
    return {"id": f"{summary}{day}{start}", "uid": f"{summary}{day}{start}", "summary": summary,
            "start": f"{day}T{start}:00" if start else day.isoformat(),
            "end": f"{day}T{end}:00" if start else (day + timedelta(days=1)).isoformat()}


class SpeakerHA:
    def __init__(self):
        self.calls = []

    def states(self, domain=None):
        return [{"entity_id": "media_player.kitchen", "state": "idle", "attributes": {"friendly_name": "Kitchen speaker"}}]

    def call_service(self, service, data=None):
        self.calls.append((service, data))
        return []


def client_for(svc):
    from lifeos.app import create_app
    c = TestClient(create_app(svc, run_scheduler=False))
    c.post("/api/login", json={"key": "test-key", "person": "me"})
    return c


# ---------- voice ----------
def test_google_speaker_phrase_is_answered_out_loud(svc):
    from lifeos import voice
    household_svc(svc)
    svc.ha = SpeakerHA()
    svc.store.set_meal(household.today_local(svc.settings).isoformat(), "Fish pie", [], "")
    svc.google.get().add_task("Shopping", "Milk")
    svc.google.get().add_task("Shopping", "Loo roll")
    c = client_for(svc)
    assert c.post("/api/voice/settings", json={"speaker": "media_player.kitchen", "tts_entity": "tts.home_assistant_cloud"}).status_code == 200
    anon = TestClient(c.app)
    assert anon.post("/api/voice/command", json={"command": "dinner"}).status_code == 401  # Home Assistant's key only
    r = anon.post("/api/voice/command", json={"command": "dinner"}, headers={"X-LifeOS-Key": "test-key"}).json()
    assert r == {"say": "Tonight it's Fish pie.", "spoken": True}
    assert svc.ha.calls[-1] == ("tts.speak", {"entity_id": "tts.home_assistant_cloud", "media_player_entity_id": "media_player.kitchen",
                                              "message": "Tonight it's Fish pie.", "language": "en-GB"})
    r = anon.post("/api/voice/command", json={"command": "shopping"}, headers={"X-LifeOS-Key": "test-key"}).json()
    assert r["say"] == "2 things on the shopping list: Milk and Loo roll."
    assert anon.post("/api/voice/command", json={"command": "rm -rf"}, headers={"X-LifeOS-Key": "test-key"}).status_code == 400
    assert voice.answer(svc, "manual:stopcock") == "The home manual doesn't say where the stopcock is yet."


def test_home_assistant_package_for_voice_is_valid_and_complete(svc):
    from lifeos import voice
    c = client_for(svc)
    v = c.get("/api/voice").json()
    pkg = yaml.safe_load(v["package"].replace("!secret lifeos_key", "KEY"))
    assert set(pkg["input_button"]) == {f"lifeos_{k}" for k in voice.COMMANDS}
    assert pkg["rest_command"]["lifeos_voice_ask"]["url"].endswith("/api/voice/ask")
    assert "action_response" in pkg["intent_script"]["LifeOSAsk"]["speech"]["text"]
    assert yaml.safe_load(v["sentences"])["lists"]["request"] == {"wildcard": True}
    assert v["routines"][1] == {"phrase": "what's for dinner", "scene": "Life OS what's for dinner", "command": "dinner"}


def test_saying_anything_through_assist_reaches_the_assistant(svc):
    from lifeos import voice
    household_svc(svc)
    svc.store.set_meal(household.today_local(svc.settings).isoformat(), "Lasagne", [], "")
    assert voice.ask(svc, None, "what's for dinner tonight")["say"] == "Tonight it's Lasagne."  # no AI needed
    claude = ScriptedClaude([([text("Done. I've moved the dentist to Friday at 9.\n\n- See the app")], "end_turn")])
    r = voice.ask(svc, Agent(svc, client=claude), "move the dentist to Friday")
    assert r["say"] == "Done. I've moved the dentist to Friday at 9. See the app"
    sent = str(claude.calls[0]["messages"][0]["content"])
    assert "answering out loud" in sent and "move the dentist to Friday" in sent


def test_announcements_respect_quiet_hours(svc, monkeypatch):
    from lifeos import voice
    household_svc(svc)
    svc.ha = SpeakerHA()
    svc.settings.behaviour["voice"] = {"speaker": "media_player.kitchen"}
    svc.store.set_meal(household.today_local(svc.settings).isoformat(), "Curry", [], "")
    monkeypatch.setattr(household, "in_quiet_hours", lambda settings, now=None: True)
    assert voice.announce(svc, {"say": "dinner", "at": "17:30"}) is False and svc.ha.calls == []
    monkeypatch.setattr(household, "in_quiet_hours", lambda settings, now=None: False)
    assert voice.announce(svc, {"say": "dinner", "at": "17:30"}) is True
    assert svc.ha.calls[0][0] == "tts.google_translate_say" and svc.ha.calls[0][1]["message"] == "Tonight it's Curry."


@pytest.mark.parametrize("said,item", [("buy loo roll", "Loo roll"), ("We're out of milk", "Milk"),
                                       ("add bin bags to the shopping list", "Bin bags"), ("get some batteries", "Batteries"),
                                       ("book the dentist", ""), ("buy a present for Mum", ""), ("fix the gate", "")])
def test_spoken_shopping_is_recognised(svc, said, item):
    from lifeos import voice
    assert voice.shopping_item(svc, said) == item


def test_gemini_shopping_goes_to_the_shopping_list(svc):
    from lifeos import bridge
    household_svc(svc)
    g = svc.google.people["me"]
    bridge.sweep_voice(svc, None)  # first run: baseline
    g.add_task(bridge.DEFAULT_LIST, "We're out of milk")
    g.add_task(bridge.DEFAULT_LIST, "Fix the garden gate")
    filed = bridge.sweep_voice(svc, None)
    assert "Milk -> Shopping" in filed and any(f.startswith("Fix the garden gate") for f in filed)
    assert [t["title"] for t in svc.google.get().list_tasks("Shopping")] == ["Milk"]
    assert svc.notifier.pushes[0]["message"] == "Added to Shopping: Milk"


# ---------- first real day checklist ----------
def test_first_day_checklist_ticks_itself_off(svc):
    from lifeos import checklist, manual
    household_svc(svc)
    c = checklist.summary(svc, None)
    by = {i["id"]: i for i in c["items"]}
    assert by["names"]["done"] and by["google"]["done"] and not by["phones"]["done"] and not by["manual"]["done"]
    for t in ("Stopcock", "Fuse box", "Boiler"):
        manual.save(svc, t, "Under the stairs", "Other")
    assert {i["id"]: i for i in checklist.summary(svc, None)["items"]}["manual"]["done"]
    assert c["next"][0]["id"] == "ai" or c["next"][0]["group"] == "essentials"
    assert client_for(svc).get("/api/checklist").json()["total"] == len(c["items"])


# ---------- bills ----------
def test_bills_from_emails_and_price_rise_alert(svc):
    from lifeos import bills, mailreader
    household_svc(svc)
    g = svc.google.people["me"]
    emails = [{"id": "b1", "from": "Octopus Energy", "subject": "Your new payment", "link": "#"}]
    g.search_mail = lambda q, limit=15: emails if "direct debit" in q else []
    g.message_text = lambda mid, limit=12000: "Your monthly Direct Debit is changing from £142 to £168 from 1 January."
    claude = ScriptedClaude([
        ([tool_use("1", "save_bill", {"is_bill": True, "provider": "Octopus Energy", "kind": "energy", "amount": 168,
                                      "period": "monthly", "direct_debit": True, "next_date": "2027-01-01",
                                      "previous_amount": 142, "date": "2026-12-01"})], "tool_use"),
        ([text("Saved.")], "end_turn")])
    assert mailreader.read_bills(svc, Agent(svc, client=claude)) == ["bill:Octopus Energy:168.0"]
    assert svc.notifier.pushes[-1]["title"] == "Bill gone up"
    assert "Octopus Energy has gone up 18%: £142.00 to £168.00 a month (£312 more a year)" in svc.notifier.pushes[-1]["message"]
    assert mailreader.read_bills(svc, Agent(svc, client=ScriptedClaude([]))) == []  # read once
    bills.record(svc, "Leeds City Council", "council tax", 2100, "yearly", next_date="2027-04-01")
    b = bills.bills(svc, date(2026, 12, 5))
    assert b["per_month"] == round(168 + 2100 / 12, 2) and b["items"][0]["provider"] == "Octopus Energy"
    assert b["items"][0]["recent_rise"]
    # A small change isn't worth a notification
    n = len(svc.notifier.pushes)
    bills.record(svc, "Octopus Energy", "energy", 170, "monthly", when="2027-01-02")
    assert len(svc.notifier.pushes) == n


# ---------- trips ----------
def test_a_trip_makes_a_packing_list_jobs_and_an_away_entry(svc):
    from lifeos import activity, todos, trips
    household_svc(svc)
    start = TODAY + timedelta(days=40)
    svc.store.add_renewal("Passport: Joe", (start + timedelta(days=60)).isoformat(), category="Admin and renewals")
    t = trips.create(svc, "Spain", "Malaga", start.isoformat(), (start + timedelta(days=9)).isoformat(), abroad=True,
                     travel="plane", person="me")
    packing = [x["title"] for x in svc.google.get().list_tasks("Packing: Spain")]
    assert "Passports" in packing and "Plug adapters" in packing and "Liquids in a clear bag (100 ml each)" in packing
    assert "Clothes for 10 days" in packing and "Laundry bag" in packing and "Favourite toy or comforter" in packing
    titles = {x["title"] for x in todos.list_todos(svc)}
    assert "Travel insurance for Spain" in titles and "Book airport parking or a lift (Spain)" in titles
    assert any(e["summary"] == "Away: Malaga" and e["start"] == start.isoformat() for e in svc.google.people["me"]._events)
    assert t["warnings"] and "Passport: Joe runs out" in t["warnings"][0]
    listed = trips.trips(svc)[0]
    assert listed["to_pack"] == len(packing) and listed["days_to_go"] == 40
    # While away, predicted shopping waits
    for i, n in enumerate((60, 45, 30, 15)):
        activity.record_bought(svc, "Milk", "me", "app", ref=f"m{i}", at=(start + timedelta(days=2 - n)).isoformat() + "T10:00:00+00:00")
    assert activity.daily(svc, start + timedelta(days=2))["added"] == []
    assert trips.morning(svc, start) == ["Spain"] and trips.morning(svc, start) == []
    assert trips.delete(svc, t["id"]) and not any(e["summary"] == "Away: Malaga" for e in svc.google.people["me"]._events)
    with pytest.raises(ValueError):
        trips.create(svc, "Back", "x", start.isoformat(), (start - timedelta(days=1)).isoformat())


# ---------- attachments ----------
def test_photos_and_pdfs_attach_to_renewals(svc, tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path))
    rid = svc.store.add_renewal("Boiler warranty", "2028-01-01")
    c = client_for(svc)
    jpg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = c.post(f"/api/files?for_kind=renewal&for_id={rid}&name=Warranty card.jpg", content=jpg,
               headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200
    fid = r.json()["id"]
    ren = c.get("/api/renewals").json()["renewals"][0]
    assert ren["files"][0]["name"] == "Warranty card.jpg"
    got = c.get(f"/api/files/{fid}")
    assert got.content == jpg and got.headers["content-type"] == "image/jpeg" and "sandbox" in got.headers["content-security-policy"]
    assert c.post(f"/api/files?for_kind=renewal&for_id={rid}", content=b"<html>", headers={"Content-Type": "text/html"}).status_code == 400
    assert c.post(f"/api/files?for_kind=renewal&for_id={rid}", content=b"not a pdf", headers={"Content-Type": "application/pdf"}).status_code == 400
    assert c.get("/api/files/../../etc/passwd").status_code == 404
    assert TestClient(c.app).get(f"/api/files/{fid}").status_code == 401
    assert c.post(f"/api/files/{fid}/delete").json()["ok"] and c.get(f"/api/files/{fid}").status_code == 404


# ---------- school run ----------
def test_school_run_shares_out_drop_offs_and_flags_gaps(svc):
    from lifeos import schoolrun
    household_svc(svc)
    mon = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
    joe, sue = svc.google.people["me"], svc.google.people["wife"]
    joe._events.append(ev("Clinic", mon, "08:00", "18:00"))           # Monday: Joe busy all day
    sue._events.append(ev("Meeting", mon, "14:30", "16:30"))         # Sue busy at pick-up
    sue._events.append(ev("Ava swimming", mon + timedelta(days=1), "16:30", "17:30"))
    sue._events.append(ev("School run: pick-up (Sue)", mon + timedelta(days=2), "15:15", "15:45"))
    week = {d["date"]: d for d in schoolrun.week(svc, mon, days=5)}
    m = {r["kind"]: r for r in week[mon.isoformat()]["runs"]}
    assert m["Drop-off"]["who"] == "Sue" and m["Pick-up"]["gap"]
    tue = week[(mon + timedelta(days=1)).isoformat()]["runs"]
    assert [r["kind"] for r in tue][-1] == "Pick-up from Ava swimming" and tue[-1]["at"] == "17:30"
    wed = {r["kind"]: r for r in week[(mon + timedelta(days=2)).isoformat()]["runs"]}
    assert wed["Pick-up"]["who"] == "Sue" and wed["Pick-up"]["claimed"]
    assert schoolrun.gaps(svc, mon) == [f"{mon.strftime('%a %-d %b')} pick-up at 15:15"]
    schoolrun.claim(svc, (mon + timedelta(days=3)).isoformat(), "Drop-off", "08:30", "Joe")
    thu = {r["kind"]: r for r in schoolrun.week(svc, mon, days=5)[3]["runs"]}
    assert thu["Drop-off"]["who"] == "Joe" and thu["Drop-off"]["claimed"]


# ---------- pocket money ----------
def test_allowance_and_savings_goals(svc):
    household_svc(svc)
    household.set_allowance(svc, "Ava", 2)
    sat = TODAY + timedelta(days=(5 - TODAY.weekday()) % 7)
    assert household.pay_allowances(svc, sat) == ["Ava:2.00"] and household.pay_allowances(svc, sat) == []
    svc.store.log_chore("Make bed", "Ava", 0.5)
    g = household.add_goal(svc, "Ava", "Lego set", 10)
    r = household.save_towards(svc, g["id"], 5)
    assert r["moved"] == 2.5 and svc.store.unpaid_totals().get("Ava", 0) == 0  # only what she's owed
    assert household.goals(svc, "Ava")[0]["pct"] == 25
    with pytest.raises(ValueError):
        household.save_towards(svc, g["id"], 1)
    household.delete_goal(svc, g["id"])
    assert svc.store.unpaid_totals()["Ava"] == 2.5  # savings back
    assert svc.store.mark_paid("Ava") == 2.5 and svc.store.unpaid_totals().get("Ava", 0) == 0
    with pytest.raises(ValueError):
        household.add_goal(svc, "Nobody", "Bike", 100)


# ---------- energy report ----------
class HistoryHA:
    def __init__(self, now):
        self.now = now

    def history(self, entity_id, start, end, attributes=False):
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        this_week = b > self.now - timedelta(days=1)
        if entity_id.startswith("sensor."):
            per_day = 14.0 if this_week else 10.0
            return [{"state": str(1000 + per_day * i), "last_changed": (a + timedelta(days=i)).isoformat()} for i in range(8)]
        hours = 30 if this_week else 20
        out = []
        for i in range(7):
            on = a + timedelta(days=i, hours=6)
            out += [{"state": "heat", "attributes": {"hvac_action": "heating"}, "last_changed": on.isoformat()},
                    {"state": "heat", "attributes": {"hvac_action": "idle"}, "last_changed": (on + timedelta(hours=hours / 7)).isoformat()}]
        return out


def test_weekly_energy_report_with_one_suggestion(svc, monkeypatch):
    from lifeos import energy
    from datetime import timezone as tzu
    now = datetime(2026, 11, 29, 18, 0, tzinfo=tzu.utc)
    monkeypatch.delenv("OCTOPUS_TARIFF", raising=False)
    svc.ha = HistoryHA(now)
    svc.settings.behaviour["energy"] = {"usage_entity": "sensor.energy_import", "heating_entity": "climate.home", "unit_rate": 25}
    r = energy.report(svc, now)
    assert r["kwh"] == 98.0 and r["kwh_last"] == 70.0 and r["cost"] == 24.5 and r["cost_last"] == 17.5
    assert r["heating_hours"] == 30.0 and r["heating_hours_last"] == 20.0
    assert r["suggestion"].startswith("The heating ran 10 hours more than last week")
    assert energy.suggest({"kwh": 98, "kwh_last": 70}, False).startswith("Electricity use was up 40%")
    svc.settings.behaviour["energy"] = {}
    assert energy.report(svc, now) == {"available": False}


# ---------- life admin hour ----------
def test_life_admin_hour_picks_three_jobs_and_a_free_hour(svc):
    from lifeos import adminhour, todos
    household_svc(svc)
    for title, cat, pri in (("Renew the TV licence", "Admin and renewals", 1), ("Return Ava's shoes", "Errands", 2),
                            ("Send the school trip form", "Kids", 2), ("Paint the fence", "Garden", 1),
                            ("Claim the boiler refund", "Admin and renewals", 3)):
        todos.file_todo(svc, todos.normalise(svc, {"title": title, "category": cat, "priority": pri}, title))
    svc.store.add_renewal("Home insurance", (TODAY + timedelta(days=12)).isoformat())
    p = adminhour.plan(svc)
    titles = [j["title"] for j in p["jobs"]]
    assert len(titles) == 3 and "Paint the fence" not in titles and "Renew the TV licence" in titles
    assert any(x.startswith("Sort out Home insurance") for x in titles)  # due in 12 days: pressing
    assert p["slot"] and p["slot"]["end"] > p["slot"]["start"]
    other = adminhour.plan(svc, skip=1)["slot"]
    assert other and other["start"] != p["slot"]["start"]
    b = adminhour.book(svc, p["slot"]["start"][:19], titles, "Joe")
    ev_ = next(e for e in svc.google.people["me"]._events if e["summary"] == "Life admin hour (Joe)")
    assert b["ok"] and ev_ and adminhour.plan(svc)["booked"]


# ---------- found while building the screens ----------
def test_store_is_safe_with_many_requests_at_once(tmp_path):
    """The Home screen asks for six things at once; each must get its own rows back."""
    import threading

    from lifeos.store import Store
    st = Store(str(tmp_path / "t.db"))
    for i in range(30):
        st.put("manual", f"m{i}", {"title": f"Entry {i}", "section": "Water"})
        st.log_chore(f"Chore {i}", "Ava", 0.1)
    problems = []

    def work(n):
        try:
            for _ in range(40):
                assert len(st.records("manual")) == 30
                assert st.unpaid_totals()["Ava"] > 0
                st.set_state(f"k{n}", "v")
                assert st.get_state(f"k{n}", "") == "v"
        except Exception as e:  # noqa: BLE001 - any failure in a thread is the bug
            problems.append(repr(e))
    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert problems == []


def test_pocket_money_and_savings_are_not_shown_as_chores(svc):
    household_svc(svc)
    svc.store.log_chore("Make bed", "Ava", 0.2)
    household.set_allowance(svc, "Ava", 3)
    household.pay_allowances(svc)
    g = household.add_goal(svc, "Ava", "Lego", 10)
    household.save_towards(svc, g["id"], 2)
    ava = next(k for k in household.chores_board(svc)["kids"] if k["kid"] == "Ava")
    assert [d["chore"] for d in ava["done_this_week"]] == ["Make bed"]
    assert ava["owed"] == pytest.approx(1.2)


def test_bills_and_trips_can_have_attachments(svc, tmp_path, monkeypatch):
    from lifeos import bills, files
    monkeypatch.setenv("LIFEOS_FILES_DIR", str(tmp_path / "files"))
    b = bills.record(svc, "Octopus Energy", "energy", 120, "monthly")
    assert b["id"] == "octopus-energy"
    f = files.save(svc, b"%PDF-1.4 test", "application/pdf", "bill", b["id"], "statement.pdf")
    assert (tmp_path / "files" / f"{f['id']}.pdf").exists()
    assert bills.bills(svc)["items"][0]["files"][0]["name"] == "statement.pdf"


def test_peak_share_counts_each_hour_once():
    from datetime import timezone

    from lifeos import energy
    start = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    pts, meter = [], 0.0
    for h in range(48):  # 1 kWh an hour, except 2 kWh in each hour from 4pm to 7pm
        pts.append({"state": str(meter), "last_changed": (start + timedelta(hours=h)).isoformat()})
        meter += 2 if (start + timedelta(hours=h)).hour in (16, 17, 18) else 1
    assert energy._peak_kwh(pts) == 12.0  # two days of 4pm, 5pm and 6pm at 2 kWh each, nothing else
    assert energy._kwh(pts) == 47 + 6
