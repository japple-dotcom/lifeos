"""v0.17: check everything, encrypted off-computer backups (and restore), the kids' tablet, the haggle helper, away
mode, recipe links, seasonal house jobs, the Sunday agenda and the party planner."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v016_features import client_for, household_svc  # noqa: E402

from lifeos import household  # noqa: E402

TODAY = date.today()


# ---------- check everything ----------
def test_check_everything_runs_in_the_app_with_links(svc, monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path))
    household_svc(svc)
    r = client_for(svc).post("/api/system/check", json={"ai": False})
    assert r.status_code == 200
    rep = r.json()
    sections = {x["section"] for x in rep["results"]}
    assert {"Settings", "Google", "Backups", "This computer"} <= sections
    backups = next(x for x in rep["results"] if x["section"] == "Backups")
    assert backups["status"] == "warn" and backups["link"] == "#settings?sec=backups"
    assert rep["summary"]


# ---------- backups ----------
def test_backup_is_encrypted_checked_and_restorable(svc, monkeypatch, tmp_path):
    from lifeos import offsite
    home, usb = tmp_path / "home", tmp_path / "usb"
    home.mkdir()
    usb.mkdir()
    monkeypatch.setenv("LIFEOS_HOME", str(home))
    (home / ".env").write_text("LIFEOS_API_KEY=secret-key-in-env\n")
    (home / "secrets").mkdir()
    (home / "secrets" / "google-me.json").write_text('{"token": "abc"}')
    svc.store.put("manual", "m1", {"title": "Stopcock", "section": "Water", "details": "under the sink"})
    with pytest.raises(offsite.BackupError):
        offsite.configure(svc, str(tmp_path / "missing"))
    with pytest.raises(offsite.BackupError):
        offsite.configure(svc, str(usb), "short")
    offsite.configure(svc, str(usb), "correct horse battery staple")
    r = offsite.run(svc, datetime(2026, 11, 25, 2, 40))
    assert r["ok"], r
    f = usb / r["file"]
    blob = f.read_bytes()
    assert b"secret-key-in-env" not in blob and b"Stopcock" not in blob and b"under the sink" not in blob
    with pytest.raises(offsite.BackupError):
        offsite.stage_restore(blob, "wrong passphrase here")
    assert offsite.status(svc)["last"]["ok"]
    # restore: staged, then swapped in at the next start; the old data is kept
    (home / "data").mkdir(exist_ok=True)
    (home / "data" / "lifeos.db").write_bytes(b"old database")
    offsite.stage_restore(blob, "correct horse battery staple")
    kept = offsite.apply_pending(home)
    assert kept and (Path(kept) / "data" / "lifeos.db").read_bytes() == b"old database"
    import sqlite3
    con = sqlite3.connect(home / "data" / "lifeos.db")
    assert con.execute("SELECT COUNT(*) FROM records WHERE kind='manual'").fetchone()[0] == 1
    con.close()
    assert (home / "secrets" / "google-me.json").exists() and (home / "secrets" / "backup-passphrase").exists()
    assert offsite.apply_pending(home) == ""  # nothing left staged


def test_backup_problem_warns_once_a_day(svc, monkeypatch, tmp_path):
    from lifeos import offsite
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path))
    usb = tmp_path / "usb"
    usb.mkdir()
    offsite.configure(svc, str(usb), "correct horse battery staple")
    usb.rmdir()  # the stick was unplugged
    assert not offsite.run(svc, datetime(2026, 11, 25, 2, 40))["ok"]
    offsite.run(svc, datetime(2026, 11, 25, 3, 40))
    assert len([p for p in svc.notifier.pushes if p["title"] == "Backup problem"]) == 1


# ---------- kids' tablet ----------
def test_kids_tablet_can_tick_chores_and_nothing_else(svc):
    household_svc(svc)
    from lifeos.app import create_app
    from fastapi.testclient import TestClient
    c = TestClient(create_app(svc, run_scheduler=False))
    assert c.post("/api/login", json={"key": "test-key", "person": "kids"}).status_code == 200
    board = c.get("/api/kids/board").json()
    assert [k["kid"] for k in board["kids"]] == ["Ava", "Leo"]
    r = c.post("/api/kids/done", json={"kid": "Ava", "chore_id": "bed"})
    assert r.status_code == 200
    assert c.post("/api/kids/done", json={"kid": "Ava", "chore_id": "bed"}).status_code == 400  # double tap
    assert c.get("/api/wall").status_code == 200  # the wall display still works on the same tablet
    # but nothing an adult does
    for method, path, body in (("post", "/api/chores/paid", {"kid": "Ava"}), ("get", "/api/today", None),
                               ("post", "/api/todos", {"text": "buy sweets"}), ("get", "/api/bills", None)):
        assert getattr(c, method)(path, json=body).status_code == 401 if body else getattr(c, method)(path).status_code == 401
    assert c.post("/api/kids/undo", json={"id": r.json()["id"]}).status_code == 200
    # parents see tablet ticks marked
    c.post("/api/kids/done", json={"kid": "Leo", "chore_id": "table"})
    leo = next(k for k in household.chores_board(svc)["kids"] if k["kid"] == "Leo")
    assert leo["done_this_week"][0]["tablet"] is True


def test_display_session_still_cannot_tick_chores(svc):
    household_svc(svc)
    from lifeos.app import create_app
    from fastapi.testclient import TestClient
    c = TestClient(create_app(svc, run_scheduler=False))
    c.post("/api/login", json={"key": "test-key", "person": "display"})
    assert c.post("/api/kids/done", json={"kid": "Ava", "chore_id": "bed"}).status_code == 401


# ---------- haggle ----------
def test_haggle_helper_for_a_bill_that_went_up(svc):
    from lifeos import bills, haggle
    household_svc(svc)
    bills.record(svc, "Sky Broadband", "broadband", 32.0, when=(TODAY - timedelta(days=400)).isoformat())
    b = bills.record(svc, "Sky Broadband", "broadband", 41.0, when=TODAY.isoformat())
    h = haggle.for_bill(svc, b["id"], "Joe")
    assert h["old"] == 32.0 and h["new"] == 41.0 and h["pct"] == 28 and h["yearly_extra"] == 108
    assert "from £32.00 to £41.00 a month" in h["price_line"]
    assert any("retentions" in line for line in h["script"]) and h["compare"][0]["url"].startswith("https://www.uswitch.com")
    assert "1 years" not in h["email"]["body"] and h["email"]["body"].endswith("Joe")
    c = client_for(svc)
    r = c.post("/api/haggle/draft", json={"for": "bill", "id": b["id"]})
    assert r.status_code == 200
    draft = next(iter(svc.google.people["me"].drafts.values()))
    assert draft["to"] == "" and "best price" in draft["body"]
    t = c.post("/api/haggle/todo", json={"for": "bill", "id": b["id"]}).json()
    assert t["title"] == "Ring Sky Broadband for a better price"


def test_haggle_for_renewal_and_things_that_cant_be_haggled(svc):
    from lifeos import haggle
    rid = svc.store.add_renewal("Car insurance", (TODAY + timedelta(days=20)).isoformat())
    h = haggle.for_renewal(svc, rid, 640, 512)
    assert h["kind"] == "car insurance" and h["pct"] == 25 and "comparethemarket" in h["compare"][0]["url"]
    tax = haggle.plan("Leeds City Council", "council tax", 180, 190)
    assert tax["script"] == [] and tax["email"] is None and "band" in tax["note"]
    assert not haggle.can_haggle_renewal("Passport")


# ---------- away mode ----------
class AwayHA:
    def __init__(self, dark=False):
        self.calls, self.dark = [], dark

    def state(self, eid):
        if eid == "sun.sun":
            return {"state": "below_horizon" if self.dark else "above_horizon"}
        return {"entity_id": eid, "state": "heat", "attributes": {"current_temperature": 13.5}}

    def states(self, domain=None):
        return [{"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
                {"entity_id": "binary_sensor.back_door", "state": "off", "attributes": {"device_class": "door"}}]

    def call_service(self, service, data=None):
        self.calls.append((service, data))
        return []


def test_away_mode_runs_the_house_while_away(svc):
    from lifeos import away, trips
    household_svc(svc)
    svc.ha = AwayHA()
    svc.settings.behaviour["away"] = {"heating_entity": "climate.home", "lights": ["light.living_room"]}
    start = TODAY + timedelta(days=10)
    t = trips.create(svc, "Cornwall", "Cornwall", start.isoformat(), (start + timedelta(days=3)).isoformat())
    away.set_mode(svc, t["id"], True)
    tz = __import__("lifeos.scheduling", fromlist=["x"])._tz(svc.settings)
    day1 = datetime.combine(start, datetime.min.time()).replace(hour=9, tzinfo=tz)
    assert away.tick(svc, day1) == ["heating to 12"]
    assert away.tick(svc, day1 + timedelta(minutes=10)) == []  # once a day
    svc.ha.dark = True
    did = away.tick(svc, day1.replace(hour=16, minute=40))
    assert "lights on" in did and ("light.turn_on", {"entity_id": "light.living_room"}) in svc.ha.calls
    did = away.tick(svc, day1.replace(hour=22, minute=40))
    assert "lights off" in did and "house check" in did
    note = svc.notifier.pushes[-1]
    assert note["title"] == "House check" and "All fine" in note["message"] and "13.5°C" in note["message"]
    last = datetime.combine(start + timedelta(days=3), datetime.min.time()).replace(hour=14, minute=5, tzinfo=tz)
    assert "heating back to 20" in away.tick(svc, last)
    # nothing is ever unlocked or opened
    assert not any(s in ("lock.unlock", "cover.open_cover", "alarm_control_panel.alarm_disarm") for s, _ in svc.ha.calls)


# ---------- recipes ----------
PAGE = """<html><head><title>Easy chicken curry | Good Food</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [{"@type": "WebPage"},
{"@type": "Recipe", "name": "Easy chicken curry", "recipeYield": "4",
 "recipeIngredient": ["1 tbsp olive oil", "2 onions, chopped", "3 garlic cloves, crushed", "6 chicken thighs",
 "2 x 400g tins chopped tomatoes", "200ml coconut milk", "salt and pepper, to taste", "300g basmati rice"]}]}
</script></head><body>...</body></html>"""


def test_recipe_link_goes_to_shopping(svc):
    from lifeos import recipes
    household_svc(svc)
    day = (TODAY + timedelta(days=2)).isoformat()
    r = recipes.add(svc, "https://www.example.com/curry", day, page=PAGE)
    assert r["dinner"] == "Easy chicken curry"
    # onions aren't in this household's cupboard list (settings pantry_staples), so they're bought
    assert r["added"] == ["onions", "chicken thighs", "chopped tomatoes", "coconut milk", "basmati rice"]
    assert set(r["in_cupboard"]) == {"olive oil", "garlic cloves", "salt and pepper"}
    meal = next(m for m in household.week_meals(svc, days=7) if m["day"] == day)
    assert meal["dinner"] == "Easy chicken curry" and meal["notes"] == "https://www.example.com/curry"
    again = recipes.add(svc, "https://www.example.com/curry", day, page=PAGE)
    assert again["added"] == [] and "coconut milk" in again["already_on_list"]
    with pytest.raises(recipes.RecipeError):
        recipes.parse("<html>no recipe here</html>")


def test_recipe_fetch_refuses_the_home_network():
    from lifeos import recipes
    for url in ("http://192.168.1.10/recipe", "http://localhost:8123/", "file:///etc/passwd", "http://10.0.0.5/x"):
        with pytest.raises(recipes.RecipeError):
            recipes.fetch(url)


def test_recipe_microdata_fallback():
    from lifeos import recipes
    page = '<meta property="og:title" content="Pancakes"><li itemprop="recipeIngredient">100g plain flour</li>' \
           '<li itemprop="recipeIngredient">2 large eggs</li>'
    r = recipes.parse(page)
    assert r["name"] == "Pancakes" and [recipes.clean(i) for i in r["ingredients"]] == ["plain flour", "eggs"]


# ---------- seasonal ----------
def test_seasonal_jobs_for_the_month(svc):
    from lifeos import seasonal
    household_svc(svc)
    sept = date(TODAY.year, 9, 1)
    titles = [j["title"] for j in seasonal.jobs_for(svc, 9)]
    assert "Bleed the radiators before the heating goes on" in titles and "Book the chimney sweep" not in titles
    seasonal.set_have(svc, "chimney", True)
    assert "Book the chimney sweep" in [j["title"] for j in seasonal.jobs_for(svc, 9)]
    seasonal.set_off(svc, "windows", True)
    added = seasonal.monthly(svc, sept)
    assert "Bleed the radiators before the heating goes on" in added and not any("windows" in a for a in added)
    assert seasonal.monthly(svc, sept) == []  # once a month
    ov = seasonal.overview(svc, sept)
    assert ov["month"] == "September" and ov["next_month"] == "October"
    assert all(j["added"] for j in ov["now"] if not j["off"])


# ---------- Sunday agenda ----------
def test_sunday_agenda_pulls_it_together(svc):
    from lifeos import agenda, bills, trips
    household_svc(svc)
    bills.record(svc, "Octopus Energy", "energy", 100, when=(TODAY - timedelta(days=30)).isoformat())
    bills.record(svc, "Octopus Energy", "energy", 120, when=TODAY.isoformat(),
                 next_date=(TODAY + timedelta(days=2)).isoformat())
    trips.create(svc, "Lake District", "Lake District", (TODAY + timedelta(days=20)).isoformat(),
                 (TODAY + timedelta(days=23)).isoformat())
    svc.store.log_chore("Make bed", "Ava", 0.2)
    a = agenda.build(svc, TODAY)
    titles = [s["title"] for s in a["sections"]]
    assert titles[0] == "Decisions to make" and "The kids" in titles
    words = agenda.text(a)
    assert "Octopus Energy went up 20%" in words and "Trip: Lake District in" in words and "Ava: 1 chores" in words
    assert client_for(svc).get("/api/agenda").json()["text"].startswith("Week of")
    agenda.sunday(svc)
    assert svc.notifier.pushes[-1]["title"] == "Sunday agenda"


# ---------- parties ----------
def test_party_planner_end_to_end(svc):
    from lifeos import party
    household_svc(svc)
    d = TODAY + timedelta(days=40)
    p = party.create(svc, "Ava", d.isoformat(), "14:00", "16:00", 8, "Jump Zone", "Unicorns", "me")
    g = svc.google.get()
    assert p["name"] == "Ava's 8th" and "Candles and a lighter" in [t["title"] for t in g.list_tasks("Party: Ava's 8th")]
    assert any(e["summary"] == "Ava's 8th party" for e in g._events)
    with pytest.raises(ValueError):
        party.add_guest(svc, p["id"], "Mia", "not-an-email")
    party.add_guest(svc, p["id"], "Mia", "mia.mum@example.com")
    party.add_guest(svc, p["id"], "Noah", "")
    r = party.make_invites(svc, p["id"], "me")
    assert r["made"] == ["Mia"] and r["without_email"] == ["Noah"]
    draft = next(v for v in g.drafts.values() if v["to"] == "mia.mum@example.com")
    assert "You're invited to Ava's 8th birthday party!" in draft["body"] and "Jump Zone" in draft["body"]
    assert party.make_invites(svc, p["id"], "me")["made"] == []  # not twice
    party.rsvp(svc, p["id"], "Noah", "yes")
    pt = party.parties(svc)[0]
    assert pt["yes"] == 1 and pt["no_reply"] == 1
    food = party.food_to_shopping(svc, p["id"])
    assert "Party bags x 6" in food["added"]
    assert party.delete(svc, p["id"]) and not any(e["summary"] == "Ava's 8th party" for e in g._events)


def test_party_reply_is_read_from_gmail(svc):
    from lifeos import party
    household_svc(svc)
    p = party.create(svc, "Leo", (TODAY + timedelta(days=30)).isoformat(), "10:00", age=5)
    party.add_guest(svc, p["id"], "Ollie", "ollie.dad@example.com")
    party.make_invites(svc, p["id"], "me")
    g = svc.google.get()
    tid = party.get(svc, p["id"])["guests"][0]["thread_id"]
    g.thread_messages = lambda t: [{"from": "Ollie's Dad <ollie.dad@example.com>", "snippet": "Yes we'd love to come!"}] \
        if t == tid else []
    assert party.check_replies(svc) == ["Ollie: yes"]
    assert party.get(svc, p["id"])["guests"][0]["rsvp"] == "yes"
    assert svc.notifier.pushes[-1]["title"] == "Party replies"


def test_voice_can_send_everything_to_life_os():
    import yaml

    from lifeos import voice
    s = yaml.safe_load(voice.ha_sentences(catch_all=True))
    assert "{request}" in s["intents"]["LifeOSAsk"]["data"][0]["sentences"]
    assert "{request}" not in yaml.safe_load(voice.ha_sentences())["intents"]["LifeOSAsk"]["data"][0]["sentences"]
