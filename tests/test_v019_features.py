"""v0.19: Christmas, sign in by QR code, phone access set up by Life OS, meter readings, school kit, garden nudges,
fridge photo to dinners, the weekly fridge sheet, the home contents list, documents, the Google sign-in file from
Settings, and 'this computer' meaning this computer (not a phone coming in through Tailscale Serve)."""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import FakeHA, ScriptedClaude, tool_use  # noqa: E402
from test_v016_features import client_for, ev, household_svc  # noqa: E402

from lifeos import household  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def today(svc) -> date:
    return household.today_local(svc.settings)


def next_weekday(d: date, wd: int) -> date:
    return d + timedelta(days=(wd - d.weekday()) % 7 or 7)


# ---------- this computer only ----------
def test_a_phone_through_tailscale_serve_is_not_this_computer(svc):
    from fastapi.testclient import TestClient

    from lifeos.app import create_app
    app = create_app(svc, run_scheduler=False)
    here = TestClient(app, base_url="http://localhost:8800")
    here.post("/api/login", json={"key": "test-key", "person": "me"})
    assert here.get("/api/system").json()["local"] is True
    # Tailscale Serve hands the phone's request over from 127.0.0.1, with the ts.net name and forwarding headers
    phone = TestClient(app, base_url="https://lifeos.tail1234.ts.net")
    phone.post("/api/login", json={"key": "test-key", "person": "me"})
    assert phone.get("/api/system").json()["local"] is False
    assert phone.post("/api/system/quit").status_code == 403
    assert phone.post("/api/system/phone-access").status_code == 403
    sneaky = TestClient(app, base_url="http://localhost:8800", headers={"X-Forwarded-For": "100.64.0.7"})
    sneaky.post("/api/login", json={"key": "test-key", "person": "me"})
    assert sneaky.get("/api/system").json()["local"] is False


# ---------- QR sign-in ----------
def test_qr_codes_scan_back_to_the_link():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from lifeos import qr
    for text in ("https://lifeos.tail1234.ts.net/app/?join=abcDEF123_-xyz789ABCdef", "HELLO", "x" * 120):
        m = qr.matrix(text)
        n = len(m)
        assert (n - 17) % 4 == 0 and all(len(r) == n for r in m)
        img = np.full(((n + 8) * 8, (n + 8) * 8), 255, np.uint8)
        for y, row in enumerate(m):
            for x, on in enumerate(row):
                if on:
                    img[(y + 4) * 8:(y + 5) * 8, (x + 4) * 8:(x + 5) * 8] = 0
        got, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        assert got == text
    svg = qr.svg("https://example.com", 200, "Sign in as Joe")
    assert svg.startswith("<svg") and 'aria-label="Sign in as Joe"' in svg and "<script" not in svg


def test_join_codes_sign_a_phone_in_once(svc, monkeypatch):
    from fastapi.testclient import TestClient

    from lifeos import phones
    from lifeos.app import create_app
    c = TestClient(create_app(svc, run_scheduler=False))
    code = phones.make_code("wife")
    r = c.post("/api/login", json={"join": code})
    assert r.status_code == 200
    assert c.get("/api/me").json()["person"] == "wife"
    again = TestClient(create_app(svc, run_scheduler=False))
    r = again.post("/api/login", json={"join": code})
    assert r.status_code == 401 and "10 minutes" in r.json()["detail"]
    old = phones.make_code("me")
    monkeypatch.setattr(phones.time, "time", lambda: 10 ** 11)
    assert phones.use_code(old) is None


def test_key_page_shows_a_code_for_each_grown_up(svc, monkeypatch):
    from fastapi.testclient import TestClient

    from lifeos import desktop
    from lifeos.app import create_app
    svc.settings.public_url = "https://lifeos.tail1234.ts.net"
    household_svc(svc)
    ctl = desktop.Controller("normal", 8800)
    monkeypatch.setattr(desktop, "controller", ctl)
    token = ctl.key_page().split("t=")[1]
    c = TestClient(create_app(svc, run_scheduler=False), base_url="http://localhost:8800")
    r = c.get(f"/local/key?t={token}")
    assert r.status_code == 200 and r.text.count("<svg") == 2 and "Joe's phone" in r.text and "Sue's phone" in r.text
    assert "img-src data:" in r.headers["content-security-policy"]
    # the same link from a phone (through Tailscale Serve) is refused
    phone = TestClient(create_app(svc, run_scheduler=False), base_url="https://lifeos.tail1234.ts.net")
    assert phone.get(f"/local/key?t={ctl.key_page().split('t=')[1]}").status_code == 404


# ---------- phone access ----------
class Done:
    def __init__(self, stdout="", stderr=""):
        self.stdout, self.stderr = stdout, stderr


def test_phone_access_runs_tailscale_serve_and_saves_the_address(svc):
    from lifeos import phones
    calls, saved = [], {}

    def runner(args, **kw):
        calls.append(args)
        return Done("Available within your tailnet:\n\nhttps://lifeos-mac.tail1234.ts.net/\n|-- proxy http://127.0.0.1:8800\n")
    r = phones.setup(svc, 8800, runner=runner, save=saved.update)
    assert r == {"ok": True, "url": "https://lifeos-mac.tail1234.ts.net"}
    assert calls[0][1:] == ["serve", "--bg", "http://127.0.0.1:8800"]
    assert saved == {"LIFEOS_PUBLIC_URL": "https://lifeos-mac.tail1234.ts.net"}

    def needs_https(args, **kw):
        return Done("", "Serve is not enabled on your tailnet.\nTo enable, visit:\n\n    https://login.tailscale.com/f/serve?node=abc123\n")
    r = phones.setup(svc, 8800, runner=needs_https, save=saved.update)
    assert not r["ok"] and r["link"] == "https://login.tailscale.com/f/serve?node=abc123" and "HTTPS" in r["problem"]


# ---------- Christmas ----------
def test_christmas_budgets_cards_dates_and_todos(svc):
    from lifeos import christmas, profiles
    t = today(svc)
    profiles.save(svc, "Grandma", likes="gardening, crosswords", sizes="M", wishlist=[{"title": "Kindle"}])
    christmas.set_budget(svc, 300, t.year)
    christmas.add_person(svc, "Ava", 100, t.year)
    christmas.add_person(svc, "Grandma", 40, t.year)
    p = christmas.plan(svc, t.year)
    ava, gran = p["people"][0], p["people"][1]
    assert gran["likes"] == "gardening, crosswords" and gran["ideas"][0]["text"] == "Kindle"
    christmas.bought(svc, ava["id"], "Roller skates", "49.99", "Decathlon", t.year)
    p = christmas.bought(svc, ava["id"], "Book", 8, "", t.year)
    ava = p["people"][0]
    assert p["spent"] == 57.99 and ava["left"] == 42.01 and not ava["done"]
    for b in ava["bought"]:
        p = christmas.present(svc, ava["id"], b["id"], wrapped=True, year=t.year)
    assert p["people"][0]["done"]
    with pytest.raises(ValueError):
        christmas.bought(svc, ava["id"], "Bike", "lots", "", t.year)
    p = christmas.card(svc, "Auntie Kate\nThe Patels, Uncle Rob", year=t.year)
    assert [c["name"] for c in p["cards"]] == ["Auntie Kate", "The Patels", "Uncle Rob"]
    p = christmas.card(svc, cid=p["cards"][0]["id"], sent=True, year=t.year)
    assert p["cards_sent"] == 1
    # posting dates are Royal Mail's for 2026
    dates = christmas.posting_dates(2026)
    assert {"what": "Last posting: 2nd Class post", "date": "2026-12-18", "source": "Royal Mail"} in dates
    # school events come from the calendar; dates go to the to-dos once
    svc.google.me._events = [ev("Nativity play (Leo)", t + timedelta(days=5)), ev("Clinic", t + timedelta(days=5))]
    christmas.add_date(svc, "Order the turkey", (t + timedelta(days=10)).isoformat(), t.year)
    found = christmas.plan(svc, t.year)["dates"]
    assert any(d["what"] == "Nativity play (Leo)" and d["source"] == "calendar" for d in found)
    assert not any(d["what"] == "Clinic" for d in found)
    first = christmas.file_dates(svc, "me", t.year)["filed"]
    assert "Order the turkey" in first and "Nativity play (Leo)" in first
    assert sum(1 for f in first if f.startswith("Post Christmas cards")) <= 1
    assert christmas.file_dates(svc, "me", t.year)["filed"] == []


def test_christmas_countdown_on_today_and_the_display(svc, monkeypatch):
    from lifeos import christmas
    monkeypatch.setattr(christmas, "_today", lambda s: date(2026, 11, 26))
    c = client_for(svc)
    x = c.get("/api/today").json()["christmas"]
    assert x["days_to_go"] == 29 and x["next"]["what"] == "Last posting: 2nd Class post"
    assert c.get("/api/wall", headers={"X-LifeOS-Key": "test-key"}).json()["christmas"]["days_to_go"] == 29
    monkeypatch.setattr(christmas, "_today", lambda s: date(2026, 7, 1))
    assert c.get("/api/today").json()["christmas"] is None


# ---------- meter readings ----------
class MeterHA(FakeHA):
    def __init__(self, value="1250.5"):
        super().__init__()
        self.value = value

    def states(self, domain=None):
        items = [{"entity_id": "sensor.house_energy", "state": self.value,
                  "attributes": {"friendly_name": "House energy", "device_class": "energy",
                                 "state_class": "total_increasing", "unit_of_measurement": "kWh"}},
                 {"entity_id": "sensor.power", "state": "300", "attributes": {"device_class": "power"}}]
        return [s for s in items if not domain or s["entity_id"].startswith(domain + ".")]


def test_meter_readings_typed_and_from_home_assistant(svc):
    from lifeos import meters
    household_svc(svc)
    svc.ha = MeterHA()
    assert [s["id"] for s in meters.sensors(svc)] == ["sensor.house_energy"]
    with pytest.raises(ValueError):
        meters.save_cfg(svc, [{"name": "Electricity", "sensor": "switch.kettle"}])
    meters.save_cfg(svc, [{"id": "electricity", "name": "Electricity", "unit": "kWh", "sensor": "sensor.house_energy",
                           "supplier": "readings@energy.example"}, {"id": "gas", "name": "Gas", "unit": "m³"}], day=1)
    meters.add_reading(svc, "gas", 3000, "2026-09-01")
    meters.add_reading(svc, "gas", 3060, "2026-10-01")
    with pytest.raises(ValueError, match="lower than the last reading"):
        meters.add_reading(svc, "gas", 2990, "2026-10-15")
    assert meters.use_by_month(svc, "gas") == [{"month": "2026-09", "use": 60.0, "days": 30, "per_day": 2.0}]
    from datetime import datetime
    text = meters.monthly(svc, datetime(2026, 11, 1, 9, 0))
    assert "Electricity 1250.5 kWh" in text and "please type Gas" in text
    assert svc.notifier.pushes[-1]["title"] == "Meter readings"
    assert meters.monthly(svc, datetime(2026, 11, 1, 9, 0)) == ""  # once a month
    assert meters.monthly(svc, datetime(2026, 11, 2, 9, 0)) == ""


def test_meter_reading_email_is_only_a_draft(svc):
    from lifeos import meters
    household_svc(svc)
    meters.save_cfg(svc, [{"id": "electricity", "name": "Electricity", "unit": "kWh", "supplier": "readings@energy.example"}])
    with pytest.raises(ValueError):
        meters.email_draft(svc, "electricity", "me")
    meters.add_reading(svc, "electricity", 18342.6, "2026-11-01")
    r = meters.email_draft(svc, "electricity", "me")
    g = svc.google.get("me")
    assert r["ok"] and r["to"] == "readings@energy.example"
    d = g.get_draft(r["draft"])
    assert "18342.6 kWh, taken on 1 November 2026" in d["body"] and not getattr(g, "sent", [])


# ---------- school kit ----------
def test_school_kit_the_evening_before_skipping_closures(svc):
    from datetime import datetime

    from lifeos import schoolkit
    household_svc(svc)
    with pytest.raises(ValueError):
        schoolkit.save(svc, {"Zed": {"pe": ["mon"]}})
    schoolkit.save(svc, {"Ava": {"pe": ["tue", "sun"], "lunch": ["fri"], "other": [{"what": "recorder", "days": ["tue"]}]},
                         "Leo": {"pe": ["wed"]}})
    assert schoolkit.cfg(svc)["Ava"]["pe"] == ["tue"]
    mon = next_weekday(today(svc), 0)
    t = schoolkit.tomorrow(svc, mon)
    assert t["text"] == "Ava PE kit, recorder" and t["wash"] == []
    tue = mon + timedelta(days=1)
    assert schoolkit.tomorrow(svc, tue) == {"date": (tue + timedelta(days=1)).isoformat(),
                                             "items": [{"kid": "Leo", "what": "PE kit"}], "text": "Leo PE kit", "wash": ["Ava"]}
    text = schoolkit.evening(svc, datetime.combine(mon, datetime.min.time()).replace(hour=18, minute=30))
    assert text == "Tomorrow: Ava PE kit, recorder." and svc.notifier.pushes[-1]["title"] == "School tomorrow"
    # Friday evening says nothing (no school at the weekend); an INSET day is skipped
    assert schoolkit.tomorrow(svc, mon + timedelta(days=4))["items"] == []
    svc.store.put("school_day", "inset", {"name": "INSET day", "start": tue.isoformat(), "end": tue.isoformat()})
    assert schoolkit.tomorrow(svc, mon)["items"] == []
    todo = schoolkit.lost(svc, "Leo", "blue water bottle", "me")
    assert todo["title"] == "Check lost property for Leo's blue water bottle"
    assert date.fromisoformat(todo["due"]).weekday() < 5 and todo["due"] != tue.isoformat()


def test_school_kit_on_today_the_display_and_the_speaker(svc, monkeypatch):
    from lifeos import schoolkit, voice
    household_svc(svc)
    sun = next_weekday(today(svc), 6)
    monkeypatch.setattr(schoolkit, "_today", lambda s: sun)
    schoolkit.save(svc, {"Leo": {"pe": [], "lunch": ["mon"], "other": [{"what": "swimming bag", "days": ["mon"]}]}})
    c = client_for(svc)
    assert c.get("/api/today").json()["school_tomorrow"]["text"] == "Leo packed lunch, swimming bag"
    assert c.get("/api/wall", headers={"X-LifeOS-Key": "test-key"}).json()["school_tomorrow"]["items"]
    assert voice.quick_answer(svc, "what PE kit do they need") == "Tomorrow: Leo packed lunch, swimming bag."


# ---------- garden ----------
class WeatherHA(FakeHA):
    def __init__(self, days):
        super().__init__()
        self.days = days

    def states(self, domain=None):
        items = [{"entity_id": "weather.home", "state": "sunny", "attributes": {"friendly_name": "Home"}}]
        return [s for s in items if not domain or s["entity_id"].startswith(domain + ".")]

    def service_response(self, domain_service, data):
        assert domain_service == "weather.get_forecasts" and data["type"] == "daily"
        return {data["entity_id"]: {"forecast": [{"datetime": f"2026-01-0{i + 1}T00:00:00", "temperature": hi,
                                                   "templow": lo, "precipitation": rn} for i, (hi, lo, rn) in enumerate(self.days)]}}


def test_garden_frost_and_watering_nudges(svc):
    from lifeos import garden
    household_svc(svc)
    garden.save(svc, have=["pots", "greenhouse", "lawn", "spaceship"])
    assert garden.cfg(svc)["have"] == ["pots", "greenhouse", "lawn"]
    svc.ha = WeatherHA([(5, -2, 0), (6, 1, 0), (8, 3, 2)])
    nov = date(2026, 11, 20)
    a = garden.advice(svc, garden.forecast(svc), nov)
    assert a[0]["kind"] == "frost" and "-2°C" in a[0]["text"] and "tender pots" in a[0]["text"] and "greenhouse" in a[0]["text"]
    assert garden.morning(svc, nov) and garden.morning(svc, nov + timedelta(days=1)) == []  # not again for 3 days
    assert garden.morning(svc, nov + timedelta(days=3))
    svc.ha = WeatherHA([(24, 13, 0), (26, 14, 0.2), (25, 15, 0)])
    a = garden.advice(svc, garden.forecast(svc), date(2026, 7, 10))
    assert [x["kind"] for x in a] == ["water"]
    assert garden.advice(svc, garden.forecast(svc), date(2026, 11, 10)) == []  # no watering nudge in November
    assert "Plant spring bulbs in pots" in garden.jobs(svc, date(2026, 10, 3))
    assert "Give hedges their main trim" not in garden.jobs(svc, date(2026, 9, 3))  # no hedge
    with pytest.raises(ValueError):
        garden.job_todo(svc, "Buy a pony")
    with pytest.raises(ValueError):
        garden.save(svc, postcode="not a postcode")


def test_garden_forecast_without_home_assistant_uses_the_postcode(svc):
    from lifeos import garden
    household_svc(svc)

    class Http:
        def __init__(self):
            self.urls = []

        def get(self, url, **kw):
            self.urls.append(url)
            from types import SimpleNamespace
            if "postcodes.io" in url:
                return SimpleNamespace(status_code=200, json=lambda: {"result": {"latitude": 53.8123, "longitude": -1.5678}})
            return SimpleNamespace(status_code=200, json=lambda: {"daily": {"time": ["2026-11-26"], "temperature_2m_max": [6],
                                                                            "temperature_2m_min": [-1], "precipitation_sum": [0]}})
    http = Http()
    svc.ha = None
    c = garden.save(svc, postcode="ls6 1aa", http=http)
    assert c["postcode"] == "LS61AA" and c["lat"] == 53.812
    assert garden.forecast(svc, http) == [{"date": "2026-11-26", "high": 6, "low": -1, "rain": 0}]


# ---------- the fridge ----------
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def test_fridge_photo_goes_to_the_assistant_once_and_is_not_kept(svc, tmp_path):
    from lifeos import fridge
    from lifeos.agent import Agent
    household_svc(svc)
    claude = ScriptedClaude([([tool_use("t1", "save_dinners", {
        "seen": ["chicken thighs", "peppers", "cream"],
        "dinners": [{"name": "Chicken fajitas", "uses": ["chicken thighs", "peppers"], "missing": ["tortilla wraps"], "minutes": 25}]})], "tool_use"),
        ([{"type": "text", "text": "Saved."}], "end_turn")])
    agent = Agent(svc, client=claude)
    r = fridge.from_photo(svc, agent, JPEG, "we have rice too")
    assert r["dinners"][0]["name"] == "Chicken fajitas" and r["from"] == "photo"
    content = claude.calls[0]["messages"][0]["content"]
    img = [b for b in content if isinstance(b, dict) and b.get("type") == "image"]
    assert img and img[0]["source"]["media_type"] == "image/jpeg"
    assert not any(svc.store.records(k) for k in ("file", "photo"))
    with pytest.raises(ValueError, match="HEIC"):
        fridge.from_photo(svc, agent, b"\x00\x00\x00\x18ftypheic" + b"\x00" * 50)
    with pytest.raises(ValueError):
        fridge.from_photo(svc, None, JPEG)


def test_fridge_text_have_it_and_shopping(svc):
    from lifeos import fridge
    household_svc(svc)
    t = today(svc)
    svc.store.set_meal((t - timedelta(days=20)).isoformat(), "Chicken fajitas", ["chicken", "peppers", "tortilla wraps", "salt"], "")
    svc.store.set_meal((t - timedelta(days=40)).isoformat(), "Fish pie", ["fish", "potatoes", "peas"], "")
    r = fridge.from_text(svc, "chicken, Peppers")
    assert r["dinners"][0] == {"name": "Chicken fajitas", "uses": ["chicken", "peppers"], "missing": ["tortilla wraps"]}
    with pytest.raises(ValueError):
        fridge.from_text(svc, "  ")
    fridge.have_it(svc, "Chicken fajitas", ["chicken"], ["tortilla wraps"])
    assert household.dinner_on(svc) == "Chicken fajitas"
    with pytest.raises(ValueError):
        fridge.have_it(svc, "Soup", [], [], (t + timedelta(days=30)).isoformat())
    svc.google.get().add_task("Shopping", "Tortilla wraps")
    s = fridge.to_shopping(svc, ["tortilla wraps", "salt", "limes"])
    assert s["added"] == ["limes"] and set(s["skipped"]) == {"tortilla wraps", "salt"}


def test_fridge_photo_route_takes_the_raw_photo(svc):
    c = client_for(svc)
    r = c.post("/api/fridge/photo", content=b"GIF89a....", headers={"Content-Type": "image/gif"})
    assert r.status_code == 400 and "JPEG" in r.json()["detail"]


# ---------- the weekly fridge sheet ----------
def test_week_sheet_has_each_days_calendar_dinner_bins_and_kit(svc, monkeypatch):
    from lifeos import bins, schoolkit, weeksheet
    household_svc(svc)
    sat = next_weekday(today(svc), 5)
    assert weeksheet.week_of(svc, sat) == sat + timedelta(days=2)
    assert weeksheet.week_of(svc, sat + timedelta(days=3)) == sat + timedelta(days=2)
    monday = sat + timedelta(days=2)
    svc.store.set_meal((monday + timedelta(days=1)).isoformat(), "Fish pie", [], "")
    schoolkit.save(svc, {"Ava": {"pe": ["tue"]}})
    monkeypatch.setattr(bins, "collections", lambda s, days, today=None: [{"date": (monday + timedelta(days=3)).isoformat(),
                                                                          "bins": ["Recycling"]}])
    w = weeksheet.build(svc, monday)
    tue, wed = w["days"][1], w["days"][2]
    assert w["title"].startswith("Week of") and len(w["days"]) == 7
    assert tue["dinner"] == "Fish pie" and tue["kit"] == "Ava PE kit"
    assert wed["bins_out"] == ["Recycling"] and w["days"][3]["bins"] == ["Recycling"]
    c = client_for(svc)
    assert c.get(f"/api/week-sheet?start={(monday + timedelta(days=2)).isoformat()}").json()["week_of"] == monday.isoformat()
    assert c.get("/api/week-sheet?start=soon").status_code == 400


# ---------- contents list ----------
def test_contents_list_totals_warnings_and_safe_spreadsheet(svc):
    from lifeos import inventory
    household_svc(svc)
    with pytest.raises(ValueError):
        inventory.save_item(svc, "TV", "Moon base", 500)
    inventory.save_item(svc, "TV", "Living room", 900, "2025-01-10", "SN-1")
    ring = inventory.save_item(svc, "Ring", "Jewellery and watches", 2400)
    inventory.save_item(svc, "=HYPERLINK(\"http://x\")", "Study", 10, notes="+1 cmd")
    o = inventory.set_cover(svc, 3000)
    assert o["total"] == 3310 and o["count"] == 3 and o["without_photos"] == 3
    assert any("more than your contents cover" in w for w in o["warnings"])
    assert any("Ring" in w and "£1,500" in w for w in o["warnings"])
    csv_text = inventory.as_csv(svc)
    assert "'=HYPERLINK" in csv_text and "'+1 cmd" in csv_text and "\n=" not in csv_text
    c = client_for(svc)
    r = c.get("/api/inventory.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    up = c.post(f"/api/files?for_kind=item&for_id={ring['id']}&name=receipt.jpg", content=JPEG + b"\x00" * 100,
                headers={"Content-Type": "image/jpeg"})
    assert up.status_code == 200
    assert c.get("/api/inventory").json()["without_photos"] == 2
    c.post("/api/inventory/remove", json={"id": ring["id"]})
    assert not svc.store.records("file") or all(f.get("for_id") != ring["id"] for f in svc.store.records("file"))


# ---------- documents ----------
def test_documents_where_they_are_expiry_in_renewals_and_no_numbers(svc):
    from lifeos import documents, voice
    household_svc(svc)
    t = today(svc)
    with pytest.raises(ValueError, match="document numbers"):
        documents.save(svc, "Passport", "Leo", "Study drawer", notes="Number 533912345")
    with pytest.raises(ValueError):
        documents.save(svc, "Passport", "Leo", "")
    d = documents.save(svc, "Passport", "Leo", "Grey folder, study drawer", (t + timedelta(days=200)).isoformat())
    r = svc.store.renewals()
    assert [x["name"] for x in r] == ["Passport: Leo"] and r[0]["remind_days"] == 270 and r[0]["repeat_months"] == 0
    documents.save(svc, "Passport", "Leo", "Fireproof box", (t + timedelta(days=300)).isoformat(), did=d["id"])
    r = svc.store.renewals()
    assert len(r) == 1 and r[0]["due_date"] == (t + timedelta(days=300)).isoformat()
    documents.save(svc, "Passport", "Leo", "Fireproof box", "", did=d["id"])
    assert svc.store.renewals() == []
    documents.save(svc, "Will", "", "With the solicitor")
    assert documents.where_is(svc, "where's Leo's passport?") == "Leo's passport: Fireproof box."
    assert documents.where_is(svc, "where's the will") == "will: With the solicitor."
    assert voice.quick_answer(svc, "Where is Leo's passport") == "Leo's passport: Fireproof box."
    listing = documents.listing(svc)
    assert {x["kind"] for x in listing["documents"]} == {"Passport", "Will"}
    documents.remove(svc, d["id"])
    assert [x["kind"] for x in documents.listing(svc)["documents"]] == ["Will"]


def test_documents_never_go_to_the_assistant(svc):
    from lifeos import documents
    from lifeos.agent import Agent
    household_svc(svc)
    documents.save(svc, "Passport", "Ava", "Grey folder in the study")
    claude = ScriptedClaude([([{"type": "text", "text": "OK"}], "end_turn")])
    agent = Agent(svc, client=claude)
    agent.run("What's going on this week?")
    assert "Grey folder" not in json.dumps(claude.calls[0], default=str)


# ---------- the Google sign-in file ----------
def test_google_sign_in_file_from_settings(svc, tmp_path):
    from lifeos import oauth
    svc.settings.google_client_secret_file = str(tmp_path / "secrets" / "client_secret.json")
    c = client_for(svc)
    assert c.get("/api/accounts").json()["client_ready"] is False
    desktop_file = json.dumps({"installed": {"client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
                                             "client_secret": "GOCSPX-abcdefghijklmnop"}})
    r = c.post("/api/accounts/client", json={"file": desktop_file})
    assert r.status_code == 400 and "Web application" in r.json()["detail"]
    assert c.post("/api/accounts/client", json={"client_id": "nope", "client_secret": "GOCSPX-abcdefghijklmnop"}).status_code == 400
    web = json.dumps({"web": {"client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
                              "client_secret": "GOCSPX-abcdefghijklmnop", "project_id": "lifeos"}})
    r = c.post("/api/accounts/client", json={"file": web})
    assert r.status_code == 200 and r.json()["client_id"].startswith("1234567890-")
    p = Path(svc.settings.google_client_secret_file)
    saved = json.loads(p.read_text())
    assert saved["web"]["client_secret"] == "GOCSPX-abcdefghijklmnop"
    assert saved["web"]["redirect_uris"] == ["http://lifeos.test/auth/google/callback"]
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert c.get("/api/accounts").json()["client_ready"] is True
    oauth.save_client(svc.settings, client_id="98765432100-zyxwvutsrqponm.apps.googleusercontent.com",
                      client_secret="GOCSPX-another-one_123")
    assert "98765432100" in p.read_text()


# ---------- the app ----------
def test_v019_screens_ship_with_the_app():
    from lifeos import __version__
    assert __version__ == "0.19.0"
    index = (ROOT / "web" / "index.html").read_text()
    for needed in ('src="extra.js"', 'id="xmas-block"', 'id="kit-block"', 'id="garden-block"', 'id="meters-block"',
                   'id="docs-block"', 'id="contents-block"', 'id="fridge-form"', 'href="week.html"', 'id="phone-access"',
                   'id="google-client"'):
        assert needed in index
    sw = (ROOT / "web" / "sw.js").read_text()
    assert '"lifeos-shell-v23"' in sw and all(f'"{f}"' in sw for f in ("extra.js", "week.html", "week.js", "week.css"))
    extra = (ROOT / "web" / "extra.js").read_text()
    assert ".innerHTML" not in extra and "window.LifeOSExtra" in extra
    assert "window.LifeOSExtra" in (ROOT / "web" / "app.js").read_text()
