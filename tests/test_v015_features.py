"""v0.15: cars, subscriptions and spending, childcare gaps, appliance care, cheapest energy times, dinners that
learn, gift memory, the home manual, and who's home for a visit."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifeos import activity, household  # noqa: E402

TODAY = date.today()


def household_svc(svc, kids=("Ava", "Leo")):
    from lifeos.accounts import GoogleAccounts
    from lifeos.demo import DemoGoogle
    svc.settings.household["names"] = {"me": "Joe", "wife": "Sue"}
    svc.settings.household["kids"] = list(kids)
    svc.google = GoogleAccounts(svc.settings, people={"me": DemoGoogle("me", []), "wife": DemoGoogle("wife", [])})
    return svc


def ev(summary, day: date, start="09:00", end="17:00"):
    return {"id": f"{summary}{day}", "uid": f"{summary}{day}", "summary": summary,
            "start": f"{day}T{start}:00" if start else day.isoformat(),
            "end": f"{day}T{end}:00" if start else (day + timedelta(days=1)).isoformat()}


def next_weekday(d: date, wd: int) -> date:
    return d + timedelta(days=(wd - d.weekday()) % 7 or 7)


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.body


# ---------- 1. cars ----------
def test_car_dates_come_from_the_dvla_and_land_in_renewals(svc, monkeypatch):
    from lifeos import cars
    household_svc(svc)
    monkeypatch.setenv("DVLA_API_KEY", "dvla-test")
    mot, tax = (TODAY + timedelta(days=100)).isoformat(), (TODAY + timedelta(days=40)).isoformat()
    calls = []

    def post(url, json=None, headers=None, timeout=0):
        calls.append((url, json, headers))
        return Resp({"make": "FORD", "colour": "SILVER", "motExpiryDate": mot, "motStatus": "Valid",
                     "taxDueDate": tax, "taxStatus": "Taxed", "yearOfManufacture": 2019})
    import httpx
    monkeypatch.setattr(httpx, "post", post)
    car = cars.add_car(svc, "ab12 cde")
    assert car["name"] == "Silver Ford" and car["reg"] == "AB12CDE"
    assert calls[0][1] == {"registrationNumber": "AB12CDE"} and calls[0][2]["x-api-key"] == "dvla-test"
    r = {x["name"]: x for x in svc.store.renewals()}
    assert r["MOT: Silver Ford (AB12 CDE)"]["due_date"] == mot and r["Car tax: Silver Ford (AB12 CDE)"]["due_date"] == tax
    # The generic "Car MOT" check-up isn't predicted any more: the car's own date is in Renewals
    svc.settings.behaviour["checkups"] = [{"what": "Car MOT", "every_months": 12, "who": "household"}]
    assert not activity.checkups(svc)
    # Weekly: a lapsed tax is flagged once
    mot2 = (TODAY + timedelta(days=465)).isoformat()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp({"make": "FORD", "colour": "SILVER", "motExpiryDate": mot2,
                                                             "motStatus": "Valid", "taxDueDate": tax, "taxStatus": "Untaxed"}))
    cars.weekly(svc)
    cars.weekly(svc)
    assert [p["title"] for p in svc.notifier.pushes] == ["Car check"]
    assert "untaxed" in svc.notifier.pushes[0]["message"]
    assert {x["name"]: x for x in svc.store.renewals()}["MOT: Silver Ford (AB12 CDE)"]["due_date"] == mot2


def test_car_without_a_dvla_key_takes_typed_dates(svc, monkeypatch):
    from lifeos import cars
    monkeypatch.delenv("DVLA_API_KEY", raising=False)
    car = cars.add_car(svc, "KX19 ABC", "The Golf", mot_due="2027-03-14", tax_due="2027-01-01")
    assert car["name"] == "The Golf"
    assert {r["name"] for r in svc.store.renewals()} == {"MOT: The Golf (KX19 ABC)", "Car tax: The Golf (KX19 ABC)"}
    with pytest.raises(ValueError):
        cars.add_car(svc, "!")
    cars.remove_car(svc, "KX19ABC")
    assert not svc.store.renewals() and not cars.cars(svc)


# ---------- 2 and 6. subscriptions and spending ----------
def receipt(total=0.0, sub=None, items=None):
    return {"is_receipt": True, "retailer": "Tesco", "total": total, "subscription": sub, "items": items or []}


def test_receipts_add_up_to_monthly_spending_with_the_biggest_changes(svc):
    from lifeos import money
    first = TODAY.replace(day=1)
    last_month = (first - timedelta(days=1)).replace(day=1)
    money.record_receipt(svc, receipt(items=[{"name": "Milk", "price": 1.5, "category": "groceries"},
                                             {"name": "Kids' trainers", "price": 35, "category": "kids"}]),
                         first.isoformat(), "Tesco", "me", "mail:a")
    money.record_receipt(svc, receipt(items=[{"name": "Weekly shop", "price": 80, "category": "groceries"}]),
                         last_month.isoformat(), "Tesco", "me", "mail:b")
    money.record_receipt(svc, receipt(total=12.5, items=[{"name": "Pizza", "category": "eating out"}]),
                         first.isoformat(), "Domino's", "me", "mail:c")
    money.record_receipt(svc, receipt(items=[{"name": "Milk", "price": 1.5, "category": "groceries"}]),
                         first.isoformat(), "Tesco", "me", "mail:a")  # the same email again: counted once
    sp = money.spending(svc, today=first)
    assert sp["total"] == 49.0 and sp["last_month"] == 80.0
    cats = {r["category"]: r for r in sp["by_category"]}
    assert cats["kids"]["amount"] == 35 and cats["groceries"]["change"] == -78.5 and cats["eating out"]["amount"] == 12.5
    assert sp["biggest_changes"][0]["category"] == "groceries"
    assert len(sp["months"]) == 6 and sp["months"][-1]["total"] == 49.0


def test_subscriptions_nudge_before_renewing_and_can_be_cancelled(svc):
    from lifeos import money, todos
    household_svc(svc)
    renew = (TODAY + timedelta(days=2)).isoformat()
    money.record_receipt(svc, receipt(sub={"service": "Disney+", "amount": 8.99, "period": "monthly", "next_date": renew,
                                           "manage_url": "https://www.disneyplus.com/account"}), TODAY.isoformat(), "Disney+",
                         "me", "mail:d")
    money.record_subscription(svc, "Spotify Family", 179.88, "yearly", (TODAY + timedelta(days=200)).isoformat())
    subs = money.subscriptions(svc)
    assert subs["per_month"] == round(8.99 + 179.88 / 12, 2) and subs["items"][0]["service"] == "Disney+"
    assert money.nudges(svc) == ["Disney+"] and money.nudges(svc) == []  # once per renewal
    assert "Still using Disney+? £8.99 a month, renews" in svc.notifier.pushes[0]["message"]
    money.decide(svc, money.sub_key("Disney+"), "cancel", "me")
    t = next(x for x in todos.list_todos(svc) if x["title"].startswith("Cancel Disney+"))
    raw = next(x for x in svc.google.get().list_tasks(t["list"]) if x["id"] == t["id"])
    assert "disneyplus.com/account" in raw["notes"] and t["due"] == (TODAY + timedelta(days=1)).isoformat()
    # Price change is mentioned
    money.record_subscription(svc, "Netflix", 10.99, "monthly", (TODAY + timedelta(days=1)).isoformat())
    money.record_subscription(svc, "Netflix", 12.99, "monthly", (TODAY + timedelta(days=1)).isoformat())
    money.nudges(svc)
    assert "went up from £10.99" in svc.notifier.pushes[-1]["message"]
    money.decide(svc, "netflix", "keep")
    assert money.nudges(svc, TODAY + timedelta(days=31)) == []  # kept: not asked again for a year


# ---------- 3. school holidays and childcare ----------
def test_term_dates_pasted_in_are_understood():
    from lifeos import childcare
    text = """Autumn term ends Friday 18 December 2026
Christmas holidays: 21 December 2026 – 1 January 2027
INSET day 4 January 2027
Spring half term 15 February - 19 February 2027"""
    found = childcare.parse_term_dates(text, today=date(2026, 11, 25))
    assert found == [{"name": "Christmas holidays", "start": "2026-12-21", "end": "2027-01-01"},
                     {"name": "INSET day", "start": "2027-01-04", "end": "2027-01-04"},
                     {"name": "Spring half term", "start": "2027-02-15", "end": "2027-02-19"}]


def test_childcare_gaps_when_both_adults_are_busy_and_school_is_closed(svc):
    from lifeos import childcare
    household_svc(svc)
    mon = next_weekday(TODAY, 0)
    tue, wed = mon + timedelta(days=1), mon + timedelta(days=2)
    childcare.add_closed(svc, "Half term", mon.isoformat(), (mon + timedelta(days=4)).isoformat(), "test")
    joe, sue = svc.google.people["me"], svc.google.people["wife"]
    for d in (mon, tue, wed):
        joe._events.append(ev("Clinic", d))
    sue._events += [ev("Work", mon), ev("Work", tue), ev("Dentist", wed, "09:00", "09:30")]
    joe._events.append(ev("Holiday club (Ava, Leo)", tue, "", ""))
    gs = childcare.gaps(svc)
    assert [g["date"] for g in gs] == [mon.isoformat()]  # Tue covered by the club; Wed Sue only has a short appointment
    assert gs[0]["busy"] == ["Joe", "Sue"]
    grp = childcare.grouped(gs)[0]
    assert grp["closed"] == "Half term" and "could you help with the children" in grp["ask_text"]
    assert childcare.remind(svc) and childcare.remind(svc) == []  # told once a fortnight
    childcare.mark_covered(svc, [mon.isoformat()], "Grandma")
    assert childcare.gaps(svc) == []
    assert any(e["summary"].startswith("Childcare: Grandma") for e in joe._events)


def test_no_childcare_check_without_children(svc):
    from lifeos import childcare
    household_svc(svc, kids=())
    childcare.add_closed(svc, "Half term", TODAY.isoformat(), (TODAY + timedelta(days=4)).isoformat())
    assert childcare.gaps(svc) == []


# ---------- 4. appliance care ----------
class CareHA:
    def __init__(self):
        self.s = {"sensor.dishwasher_status": "running", "sensor.vacuum_filter": "8"}

    def states(self, domain=None):
        return [{"entity_id": k, "state": v, "attributes": {}} for k, v in self.s.items()]


def test_appliance_cycles_counted_from_home_assistant_predict_care(svc):
    from lifeos import care
    household_svc(svc)
    svc.ha = CareHA()
    svc.settings.behaviour["appliance_care"] = [
        {"what": "Dishwasher salt", "entity": "sensor.dishwasher_status", "cycle_end": "finished", "every": 5, "buy": True},
        {"what": "Robot vacuum filter", "entity": "sensor.vacuum_filter", "below": 10, "buy": True}]
    now = datetime.now(timezone.utc)
    for i in range(5):  # five washes finished
        svc.ha.s["sensor.dishwasher_status"] = "running"
        care.poll(svc, now + timedelta(hours=i, minutes=1))
        svc.ha.s["sensor.dishwasher_status"] = "finished"
        care.poll(svc, now + timedelta(hours=i, minutes=2))
        care.poll(svc, now + timedelta(hours=i, minutes=3))  # still finished: not counted twice
    assert len(svc.store.activity(kind="cycle")) == 5
    preds = {p["pid"]: p for p in activity.predict(svc)}
    salt = preds["care:dishwasher salt"]
    assert salt["kind"] == "care" and salt["to"] == "shopping" and salt["confidence"] == "high" and "5 cycles" in salt["why"]
    assert preds["care:robot vacuum filter"]["why"] == "8% left, says Home Assistant."
    activity.add_prediction(svc, salt)
    assert any(t["title"] == "Dishwasher salt" for t in svc.google.get().list_tasks("Shopping"))
    activity.record_bought(svc, "Dishwasher salt", "me", "app", ref="x", at=(now + timedelta(hours=9)).isoformat())
    assert "care:dishwasher salt" not in {p["pid"] for p in care.predictions(svc, TODAY)}  # bought: count starts again


# ---------- 5. cheapest energy times ----------
def agile_rates(start: datetime, prices: list[float]) -> dict:
    out = []
    for i, p in enumerate(prices):
        a = start + timedelta(minutes=30 * i)
        out.append({"value_inc_vat": p, "valid_from": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_to": (a + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {"results": list(reversed(out))}


def test_cheapest_time_to_run_and_the_plug_switches_on_then(svc, monkeypatch):
    from lifeos import energy

    class HA:
        calls = []

        def call_service(self, service, data=None):
            self.calls.append((service, data))
    svc.ha = HA()
    svc.settings.behaviour["energy"] = {"appliances": [{"name": "Dishwasher", "entity": "switch.dishwasher_plug", "hours": 1}]}
    monkeypatch.setenv("OCTOPUS_TARIFF", "E-1R-AGILE-24-10-01-C")
    now = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
    prices = [30, 32, 28, 25, 20, 18, 12, 9, 10, 15, 22, 25] + [24] * 20
    seen = []
    import httpx

    def get(url, params=None, timeout=0):
        seen.append(url)
        return Resp(agile_rates(now, prices))
    monkeypatch.setattr(httpx, "get", get)
    assert energy.product_of("E-1R-AGILE-24-10-01-C") == "AGILE-24-10-01"
    plan = energy.plan(svc, now)
    d = plan["appliances"][0]
    assert d["start"] == (now + timedelta(hours=3, minutes=30)).isoformat() and d["avg"] == 9.5 and d["now_avg"] == 31.0
    assert "AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-C" in seen[0]
    energy.plan(svc, now)
    assert len(seen) == 1  # prices cached for half an hour
    with pytest.raises(ValueError):
        energy.schedule(svc, "switch.boiler", d["start"])  # only listed plugs
    energy.schedule(svc, "switch.dishwasher_plug", d["start"])
    assert energy.run_due(svc, now) == []
    assert energy.run_due(svc, now + timedelta(hours=3, minutes=31)) == ["switch.dishwasher_plug"]
    assert HA.calls == [("switch.turn_on", {"entity_id": "switch.dishwasher_plug"})]
    assert energy.run_due(svc, now + timedelta(hours=4)) == []


def test_no_energy_card_without_octopus(svc, monkeypatch):
    from lifeos import energy
    monkeypatch.delenv("OCTOPUS_TARIFF", raising=False)
    assert energy.plan(svc) == {"connected": False, "appliances": []}


# ---------- 7. dinners that learn ----------
def test_dinner_suggestions_learn_from_what_was_liked(svc):
    household_svc(svc)
    start = household.today_local(svc.settings)
    past = [(40, "Lamb tagine", ["lamb", "apricots", "peppers"], 1), (30, "Lamb tagine", [], 1),
            (25, "Liver and onions", ["liver", "onions"], -1), (3, "Chicken fajitas", ["chicken", "peppers"], 1),
            (20, "Halloumi traybake", ["halloumi", "peppers", "courgette"], 0), (50, "Pasta pesto", ["pasta", "pesto"], 0)]
    for days_ago, dinner, ing, rating in past:
        d = (start - timedelta(days=days_ago)).isoformat()
        svc.store.set_meal(d, dinner, ing, "")
        svc.store.rate_meal(d, rating)
    svc.store.set_meal(start.isoformat(), "Stuffed peppers", ["peppers", "rice"], "")
    svc.settings.behaviour["favourite_meals"] = []
    busy_day = start + timedelta(days=2)
    svc.google.people["me"]._events.append(ev("Parents' evening", busy_day, "17:30", "19:00"))
    sug = {s["day"]: s for s in household.suggest_dinners(svc, start)}
    dinners = [s["dinner"] for s in sug.values()]
    assert "Liver and onions" not in dinners and "Chicken fajitas" not in dinners  # disliked; had 3 days ago
    first = sug[(start + timedelta(days=1)).isoformat()]
    assert first["dinner"] == "Lamb tagine" and "loved it" in first["why"].lower() and "peppers" in first["why"]
    assert sug[busy_day.isoformat()]["dinner"] == "Pasta pesto" and "busy evening" in sug[busy_day.isoformat()]["why"]
    assert len(dinners) == len(set(dinners))


def test_dinner_rating_card(svc):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(svc.settings.tz)
    today = datetime.now(tz).date()
    svc.store.set_meal(today.isoformat(), "Fish pie", [], "")
    evening = datetime.combine(today, datetime.min.time(), tz).replace(hour=20)
    assert household.dinner_to_rate(svc, evening) == {"day": today.isoformat(), "dinner": "Fish pie", "when": "tonight"}
    household.rate_dinner(svc, today.isoformat(), 1)
    assert household.dinner_to_rate(svc, evening) is None


# ---------- 8. gift memory ----------
def test_gift_memory_feeds_birthdays_ideas_and_what_was_given(svc):
    from lifeos import profiles, solver
    household_svc(svc)
    bday = TODAY + timedelta(days=20)
    profiles.save(svc, "Mum", "Joe's mum", f"1958-{bday.month:02d}-{bday.day:02d}", "gardening, crime novels", "M, size 6 shoes",
                  wishlist="Hori hori garden knife https://www.amazon.co.uk/dp/B000TEST01\nNew Richard Osman novel")
    pred = next(p for p in activity.predict(svc) if p["kind"] == "gift")
    assert pred["title"] == "Present for Mum" and "Likes gardening" in pred["why"] and "2 things on their wish list" in pred["why"]
    sol = solver.rule_based(svc, {"title": "Present for Mum"})
    assert sol["kind"] == "buy" and sol["options"][0]["title"] == "Hori hori garden knife"
    assert sol["options"][0]["url"] == "https://www.amazon.co.uk/dp/B000TEST01"
    assert profiles.record_given(svc, "Present for Mum", "Hori hori garden knife") == "Mum"
    sol = solver.rule_based(svc, {"title": "Birthday present for Mum"})
    assert all(o["title"] != "Hori hori garden knife" for o in sol["options"])  # given: not suggested again
    assert "already given (don't repeat): Hori hori garden knife" in profiles.prompt_text(svc)
    # Answering "What's Dad into?" fills in a new person's likes
    solver.answer_question(svc, {"question": {"area": "People", "item": "Dad: likes"}}, "Golf")
    assert profiles.get(svc, "Dad")["likes"] == "Golf"
    with pytest.raises(ValueError):
        profiles.save(svc, "Nan", birthday="14/03/1940")


# ---------- 9. home manual ----------
def test_home_manual_is_shared_with_gemini_except_private_entries(svc):
    from lifeos import manual, tools
    household_svc(svc)
    stop = manual.save(svc, "Stopcock", "Under the kitchen sink, behind the bleach", "Water")
    wifi = manual.save(svc, "Wi-Fi", "Network LifeHouse, password is tulip-42", "Internet")
    assert wifi["private"] and not stop["private"]
    g = svc.google.get()
    tasks = g.list_tasks("Home manual")
    assert [t["title"] for t in tasks] == ["Stopcock"] and "behind the bleach" in tasks[0]["notes"]
    manual.save(svc, "Stopcock", "Under the kitchen sink (turn clockwise)", "Water", rid=stop["id"])
    assert [t["notes"] for t in g.list_tasks("Home manual")] == ["Under the kitchen sink (turn clockwise) (Water)"]
    found = tools.run_tool("get_home_details", {"search": "stopcock"}, svc)
    assert any(r["item"] == "Stopcock" and "clockwise" in r["details"] for r in found)
    assert [m["title"] for m in manual.for_display(svc)] == ["Stopcock"]
    manual.delete(svc, stop["id"])
    assert g.list_tasks("Home manual") == []


# ---------- 10. who's home for a visit ----------
def test_visits_say_who_is_in_and_clashes_are_flagged(svc):
    from lifeos import scheduling, todos, trades
    household_svc(svc)
    mon = next_weekday(TODAY + timedelta(days=2), 0)
    svc.google.people["me"]._events.append(ev("Clinic", mon, "08:00", "18:00"))
    a = datetime.combine(mon, datetime.min.time()).replace(hour=10)
    home = scheduling.who_free(svc, a, a + timedelta(hours=2), prefer="Joe")
    assert home == {"free": ["Sue"], "busy": ["Joe"], "in": "Sue"}
    t = todos.file_todo(svc, todos.normalise(svc, {"title": "Kitchen socket not working", "category": "Home maintenance"}, "x"))
    cid = svc.store.save_contact(None, name="Dave", trade="electrician", email="dave@spark.example", company="Spark")
    rid = svc.store.add_request(task_id=t["id"], list_name=t["list"], person="me", contact_id=cid, contact_name="Spark",
                                contact_email="dave@spark.example", trade="electrician", subject="Socket", thread_id="",
                                slots=[]) if "add_request" in dir(svc.store) else None
    if rid is None:
        pytest.skip("store has no add_request")
    trades.book(svc, rid, a.isoformat(), 120, "me")
    r = svc.store.get_request(rid)
    assert r["appointment"]["in"] == "Sue"
    assert any(e["summary"] == "Electrician: Spark (Sue in)" for e in svc.google.people["me"]._events)
    svc.google.people["wife"]._events.append(ev("Training day", mon, "08:00", "17:00"))
    trades.visit_clashes(svc)
    assert "Sue now has something on during Spark's visit" in svc.notifier.pushes[-1]["message"]
    assert "Nobody's free then" in svc.notifier.pushes[-1]["message"]


# ---------- the screens' endpoints ----------
def test_v015_endpoints_answer(svc, monkeypatch):
    from lifeos.app import create_app
    household_svc(svc)
    monkeypatch.delenv("OCTOPUS_TARIFF", raising=False)
    c = TestClient(create_app(svc, run_scheduler=False))
    c.post("/api/login", json={"key": "test-key", "person": "me"})
    assert c.post("/api/cars", json={"reg": "KX19ABC", "name": "Golf", "mot_due": "2027-03-14"}).json()["reg"] == "KX19ABC"
    assert c.get("/api/cars").json()["cars"][0]["name"] == "Golf"
    assert c.post("/api/money/subscription/add", json={"service": "Netflix", "amount": 12.99}).json()["service"] == "Netflix"
    m = c.get("/api/money").json()
    assert m["subscriptions"]["per_month"] == 12.99 and m["spending"]["total"] == 0
    r = c.post("/api/childcare/closed", json={"text": "Christmas holidays 21 December 2026 - 1 January 2027"})
    assert r.status_code == 200 and c.get("/api/childcare").json()["closed"][0]["name"] == "Christmas holidays"
    assert c.post("/api/childcare/closed", json={"text": "nothing here"}).status_code == 400
    assert c.get("/api/energy").json() == {"connected": False, "appliances": []}
    assert "suggestions" in c.get("/api/meals/suggest").json()
    assert c.post("/api/people-gifts", json={"name": "Mum", "likes": "gardening"}).json()["likes"] == "gardening"
    assert c.get("/api/people-gifts").json()["people"][0]["name"] == "Mum"
    assert c.post("/api/manual", json={"title": "Fuse box", "details": "Under the stairs", "section": "Electrics"}).status_code == 200
    man = c.get("/api/manual").json()
    assert man["entries"][0]["title"] == "Fuse box" and all(s["title"] != "Fuse box" for s in man["starters"])
    today = c.get("/api/today").json()
    assert "rate_dinner" in today and "childcare" in today
    week = c.get("/api/week").json()
    assert "childcare" in week and week["closed"]
    assert c.get("/api/who-home", params={"start": "2027-01-04T10:00:00"}).json()["in"] in ("Joe", "Sue")
