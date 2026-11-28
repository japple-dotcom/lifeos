"""v0.20: the Google set-up check, brain dump, snap a letter, email to to-do, the "Tomorrow" message, the freezer,
storage boxes with QR labels, guests (Wi-Fi code), something's broken, the kids' routines, and date formats that
work on Windows."""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import ScriptedClaude, tool_use  # noqa: E402
from test_v016_features import client_for, ev, household_svc  # noqa: E402

from lifeos import household  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def today(svc) -> date:
    return household.today_local(svc.settings)


def next_weekday(d: date, wd: int) -> date:
    return d + timedelta(days=(wd - d.weekday()) % 7 or 7)


def decode(rows):
    """Reads a QR code (rows of 0/1) back: with ZXing (what phone scanners use) if installed, else OpenCV's two
    detectors at a few sizes (OpenCV's classic one misses about 1 in 50 valid codes that ZXing reads)."""
    np = pytest.importorskip("numpy")
    n = len(rows)

    def img(k):
        im = np.full(((n + 8) * k, (n + 8) * k), 255, np.uint8)
        for y, row in enumerate(rows):
            for x, on in enumerate(row):
                if on == "1":
                    im[(y + 4) * k:(y + 5) * k, (x + 4) * k:(x + 5) * k] = 0
        return im
    try:
        import zxingcpp
        found = zxingcpp.read_barcodes(img(4))
        return found[0].text if found else ""
    except ImportError:
        pass
    cv2 = pytest.importorskip("cv2")
    detectors = [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else [])
    for k in (8, 10, 6, 12):
        for d in detectors:
            got, _, _ = d.detectAndDecode(img(k))
            if got:
                return got
    return ""


def agent_with(svc, tool_name, args):
    from lifeos.agent import Agent
    claude = ScriptedClaude([([tool_use("t1", tool_name, args)], "tool_use"), ([{"type": "text", "text": "Saved."}], "end_turn")])
    return Agent(svc, client=claude), claude


# ---------- Google set-up check ----------
def test_google_setup_check_finds_each_missing_step(svc, tmp_path):
    from lifeos import googlesetup
    household_svc(svc)
    svc.settings.public_url = "https://lifeos.tail1234.ts.net"
    f = tmp_path / "client_secret.json"
    svc.settings.google_client_secret_file = str(f)
    steps = {s["key"]: s for s in googlesetup.check(svc)["steps"]}
    assert steps["client"]["status"] == "todo" and steps["address"]["status"] == "ok"
    assert steps["redirect"]["copy"] == "https://lifeos.tail1234.ts.net/auth/google/callback"
    f.write_text(json.dumps({"installed": {"client_id": "686327691400-abc.apps.googleusercontent.com", "client_secret": "x"}}))
    assert {s["key"]: s for s in googlesetup.check(svc)["steps"]}["client"]["status"] == "problem"
    f.write_text(json.dumps({"web": {"client_id": "686327691400-abcdefghij.apps.googleusercontent.com", "client_secret": "s"}}))

    class TasksOff:
        def __init__(self, g):
            self.g = g
            self.has_gmail = True

        def list_names(self):
            raise RuntimeError('<HttpError 403 "Google Tasks API has not been used in project 686327691400 before or it '
                               'is disabled." accessNotConfigured>')

        def __getattr__(self, name):
            return getattr(self.g, name)
    svc.google.people["me"] = TasksOff(svc.google.people["me"])
    r = googlesetup.check(svc)
    steps = {s["key"]: s for s in r["steps"]}
    assert r["project"] == "686327691400" and not r["ok"]
    assert steps["client"]["status"] == "ok"
    assert steps["tasks"]["status"] == "problem"
    assert steps["tasks"]["link"] == "https://console.cloud.google.com/apis/library/tasks.googleapis.com?project=686327691400"
    assert steps["calendar"]["status"] == "ok"
    assert steps["testers"]["link"] == "https://console.cloud.google.com/auth/audience?project=686327691400"
    assert "client_secret" not in json.dumps(r) and '"s"' not in json.dumps(r)


# ---------- brain dump ----------
def test_brain_dump_rules_split_and_save(svc):
    from lifeos import braindump
    household_svc(svc)
    t = today(svc)
    r = braindump.split(svc, None, "Book Leo's dentist check-up\nbuy oat milk; parents evening thursday 6pm\n"
                                   "fix the wobbly shelf and then ring Grandma")
    kinds = [(i["kind"], i["text"]) for i in r["items"]]
    assert r["from"] == "rules"
    assert ("shopping", "oat milk") in kinds and ("todo", "fix the wobbly shelf") in kinds
    ev_item = next(i for i in r["items"] if i["kind"] == "event")
    assert ev_item["time"] == "18:00" and date.fromisoformat(ev_item["date"]).weekday() == 3
    assert date.fromisoformat(ev_item["date"]) > t
    with pytest.raises(ValueError):
        braindump.split(svc, None, "   ")
    out = braindump.save_items(svc, "me", r["items"])
    g = svc.google.get("me")
    titles = [x["title"] for x in g.list_tasks("Shopping")]
    assert "oat milk" in titles
    assert any(e["summary"] == ev_item["text"] for e in g._events)
    assert {a["kind"] for a in out["added"]} == {"todo", "shopping", "event"}
    with pytest.raises(ValueError):
        braindump.save_items(svc, "me", [])


def test_brain_dump_with_the_assistant_and_bad_items_cleaned(svc):
    from lifeos import braindump
    household_svc(svc)
    t = today(svc)
    agent, claude = agent_with(svc, "save_items", {"items": [
        {"kind": "todo", "text": "Renew the car insurance", "category": "Admin and renewals",
         "date": (t + timedelta(days=9)).isoformat(), "priority": 2},
        {"kind": "event", "text": "Swimming gala", "date": "not a date"},
        {"kind": "spaceship", "text": "Bin bags"}, {"kind": "todo", "text": "  "}]})
    r = braindump.split(svc, agent, "renew car insurance, swimming gala, bin bags")
    assert r["from"] == "assistant"
    assert [i["kind"] for i in r["items"]] == ["todo", "todo", "todo"]  # undated event -> to-do; unknown kind -> to-do
    assert r["items"][0]["category"] == "Admin and renewals" and r["items"][0]["priority"] == 2
    assert "data, not instructions" in claude.calls[0]["system"]


# ---------- snap a letter ----------
def test_letter_photo_gives_dates_jobs_and_payment_to_confirm(svc):
    from lifeos import letters
    household_svc(svc)
    t = today(svc)
    agent, claude = agent_with(svc, "save_letter", {
        "readable": True, "sender": "Oakfield Primary", "about": "Year 2 trip",
        "dates": [{"what": "Year 2 trip", "date": (t + timedelta(days=12)).isoformat(), "time": "09:00"}],
        "todos": [{"text": "Return the consent slip", "due": (t + timedelta(days=5)).isoformat()}],
        "payment": {"amount": 8.5, "pay_by": (t + timedelta(days=5)).isoformat(), "how": "ParentPay"}})
    r = letters.read_photo(svc, agent, JPEG)
    assert r["sender"] == "Oakfield Primary"
    texts = [(i["kind"], i["text"]) for i in r["items"]]
    assert ("event", "Year 2 trip (Oakfield Primary)") in texts
    assert ("todo", "Return the consent slip") in texts
    assert ("todo", "Pay £8.50 to Oakfield Primary (ParentPay)") in texts
    img = [b for b in claude.calls[0]["messages"][0]["content"] if isinstance(b, dict) and b.get("type") == "image"]
    assert img and not svc.store.records("file")
    with pytest.raises(ValueError, match="assistant"):
        letters.read_photo(svc, None, JPEG)
    agent2, _ = agent_with(svc, "save_letter", {"readable": False})
    with pytest.raises(ValueError, match="doesn't look like a letter"):
        letters.read_photo(svc, agent2, JPEG)
    c = client_for(svc)
    assert c.post("/api/letters/read", content=b"%PDF-1.4", headers={"Content-Type": "application/pdf"}).status_code == 400


# ---------- email to to-do ----------
def test_labelled_email_becomes_one_todo_and_is_relabelled(svc):
    from lifeos import mailtodo, todos
    household_svc(svc)
    g = svc.google.get("me")
    added = mailtodo.run(svc, None)
    assert added and "Updating your wills" in added[0] and "Hartley Solicitors" in added[0]
    t = next(x for x in todos.list_todos(svc) if "wills" in x["title"])
    tasks = [x for x in svc.google.get().tasks.values() for x in x] if isinstance(svc.google.get().tasks, dict) else []
    assert t and g.relabelled == ["lm1"]
    assert mailtodo.run(svc, None) == []  # once
    assert mailtodo._clean_subject("RE: Fwd: re:  Parents evening") == "Parents evening"
    del tasks


# ---------- the Tomorrow message ----------
def test_tomorrow_message_puts_it_all_in_one(svc, monkeypatch):
    from lifeos import bins, garden, schoolkit, tomorrow
    household_svc(svc)
    sun = next_weekday(today(svc), 6)
    mon = sun + timedelta(days=1)
    schoolkit.save(svc, {"Ava": {"pe": ["mon"]}})
    monkeypatch.setattr(bins, "tomorrow", lambda s, d=None: ["Recycling"])
    monkeypatch.setattr(garden, "forecast", lambda s, http=None: [{"date": mon.isoformat(), "high": 6, "low": 1, "rain": 4}])
    svc.google.get("wife")._events.append(ev("Dentist (Leo)", mon, "10:30", "11:00"))
    svc.store.set_meal(mon.isoformat(), "Fish pie", [], "")
    m = tomorrow.build(svc, sun)
    keys = [p["key"] for p in m["parts"]]
    assert keys == ["bins", "kit", "diary", "weather", "dinner"]
    assert "Put the recycling out tonight." in m["text"] and "School: Ava PE kit." in m["text"]
    assert "Sue: Dentist (Leo) at 10:30am" in m["text"] and "coats, hats and gloves" in m["text"] and "umbrellas" in m["text"]
    tz = __import__("lifeos.voice", fromlist=["_tz"])._tz(svc)
    early = datetime.combine(sun, datetime.min.time(), tz).replace(hour=18)
    assert tomorrow.evening(svc, early) == ""
    late = early.replace(hour=19, minute=10)
    assert tomorrow.evening(svc, late).startswith("Put the recycling out tonight.")
    assert svc.notifier.pushes[-1]["title"] == "Tomorrow (Monday)"
    assert tomorrow.evening(svc, late.replace(minute=20)) == ""  # once a night
    # with it on, the separate bins and school kit reminders keep quiet
    assert bins.evening(svc, late) == "" and schoolkit.evening(svc, late) == ""
    with pytest.raises(ValueError):
        tomorrow.save(svc, at="23:30")


# ---------- freezer ----------
def test_freezer_portions_old_food_and_dinners(svc):
    from lifeos import freezer, fridge
    household_svc(svc)
    t = today(svc)
    with pytest.raises(ValueError):
        freezer.add(svc, "Soup", 1, added=(t + timedelta(days=2)).isoformat())
    a = freezer.add(svc, "Chicken curry", 2, "Top drawer", (t - timedelta(days=100)).isoformat())
    freezer.add(svc, "Peas")
    assert freezer.use(svc, a["id"])["portions"] == 1
    assert freezer.use(svc, a["id"])["gone"] is True
    freezer.add(svc, "Bolognese", 3, added=(t - timedelta(days=95)).isoformat())
    o = freezer.overview(svc)
    assert o["count"] == 4 and [i["name"] for i in o["old"]] == ["Bolognese"]
    first = t.replace(day=1)
    assert "Bolognese (3 months)" in freezer.monthly(svc, first)
    assert freezer.monthly(svc, first + timedelta(days=1)) == ""
    svc.store.set_meal((t - timedelta(days=9)).isoformat(), "Spag bol", ["bolognese", "spaghetti"], "")
    r = fridge.from_text(svc, "", freezer=True)
    assert r["dinners"][0]["name"] == "Spag bol" and "Bolognese" in r["seen"]


# ---------- storage boxes ----------
def test_boxes_find_things_and_print_labels_that_scan(svc):
    from lifeos import boxes, voice
    household_svc(svc)
    svc.settings.public_url = "https://lifeos.tail1234.ts.net"
    with pytest.raises(ValueError):
        boxes.save(svc, "Stuff", "", ["x"])
    b1 = boxes.save(svc, "Christmas decorations", "Loft", "Christmas lights, Tree baubles\nWreath, wreath")
    b2 = boxes.save(svc, "", "Garage", ["Tent", "Sleeping bags"])
    assert b1["number"] == 1 and b2["number"] == 2 and b2["name"] == "Box 2" and b1["items"] == ["Christmas lights", "Tree baubles", "Wreath"]
    assert boxes.where_is(svc, "Where are the Christmas lights?") == "Christmas lights: box 1 (Christmas decorations), loft."
    assert boxes.where_is(svc, "where's the sleeping bag") == "Sleeping bags: Box 2, garage."
    assert boxes.where_is(svc, "where's the moon") == ""
    assert voice.quick_answer(svc, "where are the baubles") == "Tree baubles: box 1 (Christmas decorations), loft."
    boxes.take_out(svc, b1["id"], "wreath")
    assert "Wreath" not in boxes.get(svc, b1["id"])["items"]
    label = boxes.labels(svc, [b1["id"]])
    assert len(label) == 1 and set("".join(label[0]["qr"])) <= {"0", "1"}
    assert decode(label[0]["qr"]) == f"https://lifeos.tail1234.ts.net/app/box.html?id={b1['id']}"


def test_qr_svg_labels_are_escaped():
    from lifeos import qr
    svg = qr.svg("x", 100, 'Box "1" <script>')
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "&quot;1&quot;" in svg


# ---------- guests ----------
def test_guest_wifi_code_visits_and_the_display(svc):
    from lifeos import guests
    household_svc(svc)
    t = today(svc)
    with pytest.raises(ValueError):
        guests.save(svc, ssid="Home", password="", security="WPA")
    guests.save(svc, ssid='Our;Home"Net', password="p:ss,word\\1", security="WPA", notes="Heating: hall\n\nBins: Wednesday")
    assert guests.wifi_text(guests.cfg(svc)) == 'WIFI:T:WPA;S:Our\\;Home\\"Net;P:p\\:ss\\,word\\\\1;;'
    c = client_for(svc)
    over = c.get("/api/guests").json()
    assert "p:ss" not in json.dumps(over) and over["wifi"]["has_password"] is True
    assert guests.wall(svc) is None  # not during a visit: nothing on the display
    with pytest.raises(ValueError):
        guests.add_visit(svc, "Pat", (t + timedelta(days=5)).isoformat(), (t + timedelta(days=2)).isoformat())
    v = guests.add_visit(svc, "Grandma Pat", (t + timedelta(days=4)).isoformat(), (t + timedelta(days=6)).isoformat(), "Spare room")
    assert v["checklist"][0]["text"] == "Make up the bed in the spare room"
    guests.tick(svc, v["id"], "c0")
    r = guests.file_jobs(svc, v["id"], "me")
    assert r["filed"] == len(guests.CHECKLIST) - 1 and r["calendar"] is True
    assert guests.file_jobs(svc, v["id"], "me")["filed"] == 0  # once
    assert any(e["summary"] == "Staying: Grandma Pat" for e in svc.google.get()._events)
    guests.set_mode(svc, 2)
    w = guests.wall(svc)
    assert w and w["wifi"]["password"] == "p:ss,word\\1"
    assert c.get("/api/wall", headers={"X-LifeOS-Key": "test-key"}).json()["guests"]["wifi"]["ssid"] == 'Our;Home"Net'
    guests.set_mode(svc, 0)
    assert guests.wall(svc) is None
    sheet = guests.sheet(svc)
    assert sheet["notes"] == ["Heating: hall", "Bins: Wednesday"] and sheet["visit"]["name"] == "Grandma Pat"
    assert decode(sheet["wifi"]["qr"]) == guests.wifi_text(guests.cfg(svc))


# ---------- something's broken ----------
def test_rights_by_how_long_ago_it_was_bought():
    from lifeos import broken
    t = date(2026, 11, 28)
    assert broken.rights((t - timedelta(days=20)).isoformat(), t)["stage"] == "refund"
    assert broken.rights((t - timedelta(days=90)).isoformat(), t)["stage"] == "repair"
    r = broken.rights((t - timedelta(days=400)).isoformat(), t, warranty_ends="2027-06-01")
    assert r["stage"] == "later" and "6 years" in r["text"] and "guarantee runs until 01 June 2027" in r["text"]
    assert "5 years" in broken.rights((t - timedelta(days=400)).isoformat(), t, scotland=True)["text"]
    assert broken.rights((t - timedelta(days=365 * 7)).isoformat(), t)["stage"] == "old"
    assert broken.rights("", t)["stage"] == "unknown"


def test_broken_finds_the_receipt_drafts_the_claim_and_never_sends(svc):
    from lifeos import broken, todos
    household_svc(svc)
    t = today(svc)
    svc.store.add_renewal("Warranty: Bosch washing machine", (t + timedelta(days=500)).isoformat(), category="Warranty",
                          notes=f"Bought from AO.com on {(t - timedelta(days=60)).isoformat()}. Receipt: https://mail.google.com/x",
                          remind_days=30, repeat_months=0)
    svc.store.add_new("purchase", "k1", {"item": "Russell Hobbs kettle", "amount": 39.99, "shop": "Argos", "link": "javascript:x"},
                      at=(t - timedelta(days=10)).isoformat())
    found = broken.look(svc, "the washing machine has stopped working")["found"]
    assert found[0]["source"] == "warranty" and found[0]["shop"] == "AO.com" and found[0]["rights"]["stage"] == "repair"
    kettle = broken.look(svc, "kettle")["found"][0]
    assert kettle["link"] == "" and kettle["rights"]["stage"] == "refund"
    with pytest.raises(ValueError):
        broken.claim_draft(svc, "me", "kettle", "")
    with pytest.raises(ValueError):
        broken.claim_draft(svc, "me", "kettle", "dead", to="not-an-email")
    r = broken.claim_draft(svc, "me", "Russell Hobbs kettle", "It won't switch on.", kettle["bought"], "Argos", "help@argos.example")
    g = svc.google.get("me")
    d = g.drafts[r["claim"]["draft"]] if isinstance(g.drafts, dict) else None
    body = json.dumps(d) if d else json.dumps(g.drafts, default=str)
    assert "reject it and have a full refund" in body and "Consumer Rights Act 2015" in body
    assert not getattr(g, "sent_threads", {})  # a draft, never sent
    assert any("Chase Argos about the faulty Russell Hobbs kettle" in x["title"] for x in todos.list_todos(svc))
    assert broken.set_status(svc, r["claim"]["id"], "sent")["status"] == "sent"
    with pytest.raises(ValueError):
        broken.set_status(svc, r["claim"]["id"], "won")


# ---------- kids' routines ----------
def test_routines_tick_count_down_and_give_a_star(svc):
    from lifeos import kidstuff, routines
    household_svc(svc)
    now = datetime.combine(today(svc), datetime.min.time()).replace(hour=8, minute=5)
    routines.save(svc, "Ava", morning=["🪥 Teeth", {"icon": "🎒", "text": "Bag"}], leave_at="08:20")
    b = routines.board(svc, now)
    ava = next(k for k in b["kids"] if k["kid"] == "Ava")
    assert b["now"] == "morning" and ava["leave_in"] == 15 and ava["morning"]["total"] == 2
    assert routines.tick(svc, "Ava", "morning", 0, now=now) == {"finished": False, "done": 1, "total": 2}
    assert routines.tick(svc, "Ava", "morning", 1, now=now)["finished"] is True
    assert routines.stars_for(svc, "Ava", now.date()) == 1
    assert next(d for k in kidstuff.stars(svc, now.date())["kids"] if k["kid"] == "Ava" for d in k["days"]
                if d["date"] == now.date().isoformat())["routines"] == 1
    with pytest.raises(ValueError):
        routines.tick(svc, "Zed", "morning", 0)
    with pytest.raises(ValueError):
        routines.tick(svc, "Ava", "lunch", 0)
    with pytest.raises(ValueError):
        routines.save(svc, "Ava", leave_at="8.20am")


def test_the_kids_tablet_may_tick_routines_but_not_change_them(svc):
    from fastapi.testclient import TestClient

    from lifeos.app import create_app
    household_svc(svc)
    tab = TestClient(create_app(svc, run_scheduler=False))
    assert tab.post("/api/login", json={"key": "test-key", "person": "kids"}).status_code == 200
    assert tab.get("/api/routines").status_code == 200
    assert tab.post("/api/routines/tick", json={"kid": "Leo", "when": "bedtime", "n": 0}).status_code == 200
    assert tab.post("/api/routines/settings", json={"kid": "Leo", "bedtime": ["Sweets"]}).status_code == 401
    assert tab.get("/api/guests/sheet").status_code == 401  # no Wi-Fi password for the tablet's own pages


# ---------- Windows ----------
def test_day_numbers_in_dates_work_on_windows(monkeypatch):
    from lifeos import dates
    bare = re.compile(r"strftime\((['\"])[^'\"]*%-")
    for p in (ROOT / "lifeos").glob("*.py"):
        assert not bare.search(p.read_text()), f"{p.name} uses %-d without dates.fmt (fails on Windows)"
    monkeypatch.setattr(dates.sys, "platform", "win32")
    assert dates.fmt("%a %-d %b") == "%a %#d %b"
    monkeypatch.setattr(dates.sys, "platform", "darwin")
    assert dates.fmt("%a %-d %b") == "%a %-d %b"


# ---------- the app ----------
def test_v020_screens_ship_with_the_app():
    from lifeos import __version__
    assert __version__ == "0.20.0"
    index = (ROOT / "web" / "index.html").read_text()
    for needed in ('src="next.js"', 'src="qrdraw.js"', 'id="dump-dialog"', 'id="letter-photo"', 'id="freezer-block"',
                   'id="boxes-block"', 'id="guests-block"', 'id="broken-block"', 'id="routines-block"', 'id="gsetup-run"',
                   'id="tomorrow-form"', 'id="mailtodo-run"'):
        assert needed in index
    sw = (ROOT / "web" / "sw.js").read_text()
    assert '"lifeos-shell-v24"' in sw
    for f in ("next.js", "qrdraw.js", "pages.js", "box.html", "box.js", "labels.html", "labels.js", "guest.html",
              "guest.js", "sheets.css"):
        assert f'"{f}"' in sw and (ROOT / "web" / f).exists()
    for f in ("next.js", "qrdraw.js", "pages.js", "box.js", "labels.js", "guest.js", "kids.js", "wall.js"):
        assert ".innerHTML" not in (ROOT / "web" / f).read_text(), f
    assert "window.LifeOSNext" in (ROOT / "web" / "app.js").read_text()
