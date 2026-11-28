"""Try Life OS with made-up sample data: no Google, Home Assistant or AI keys needed.

    python scripts/demo.py            # then open http://localhost:8800/app/ (household key: demo)
    lifeos --demo                     # the same from the app download

Everything happens in memory and disappears when you stop it. The "assistant" here is a simple stand-in
that understands a few phrases ("add milk to shopping", "what's on today", "plan dinners"), so you can see
how the app feels before connecting the real thing.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

DEMO_KEY = "demo"


def port() -> int:
    return int(os.getenv("LIFEOS_DEMO_PORT", "8800"))


# ---------- stand-ins for the real services ----------
class DemoGoogle:
    def __init__(self, person: str, events: list[dict]):
        self.person, self._events, self.tasks, self.has_gmail = person, events, [], person == "me"
        # In the demo, the tradesperson "replies" about 20 seconds after you send.
        self.reply_after = float(os.getenv("LIFEOS_DEMO_REPLY_SECONDS", "20"))
        self._n = 0
        self.drafts: dict = {}   # Gmail drafts: id -> {to, subject, body, thread_id}
        self.deleted: list = []  # deleted tasks, reported by list_changed like the real Google Tasks

    def list_names(self):
        return sorted({t["list"] for t in self.tasks} | {"Gym" if self.person == "wife" else "Inbox"})

    def add_task(self, list_name, title, notes="", due=None):
        for t in self.tasks:
            if t["list"] == list_name and t["title"].lower() == title.lower() and t["status"] != "completed":
                return {**t, "duplicate": True}
        self._n += 1
        t = {"id": f"{self.person}-{self._n}", "list": list_name, "title": title, "notes": notes,
             "due": due or "", "status": "needsAction"}
        self.tasks.append(t)
        return t

    def list_tasks(self, list_name, include_completed=False):
        return [t for t in self.tasks if t["list"] == list_name and (include_completed or t["status"] != "completed")]

    def complete_task(self, list_name, task_id):
        for t in self.tasks:
            if t["id"] == task_id:
                t["status"] = "completed"
        return {}

    def delete_task(self, list_name, task_id):
        self.deleted += [{**t, "deleted": True} for t in self.tasks if t["id"] == task_id]
        self.tasks = [t for t in self.tasks if t["id"] != task_id]

    def delete_list(self, list_name):
        self.tasks = [t for t in self.tasks if t["list"] != list_name]

    def update_task(self, list_name, task_id, title=None, notes=None, due=None):
        for t in self.tasks:
            if t["id"] == task_id:
                t.update({k: v for k, v in (("title", title), ("notes", notes), ("due", due)) if v is not None})
                return t
        raise KeyError(task_id)

    def list_changed(self, list_name, since_iso):
        """Ticked-off and deleted tasks too (the demo ignores 'since': callers only act on real changes)."""
        return [dict(t, deleted=False) for t in self.tasks if t["list"] == list_name] + \
            [dict(t) for t in self.deleted if t["list"] == list_name]

    def move_task_due(self, list_name, task_id, due):
        for t in self.tasks:
            if t["id"] == task_id:
                t["due"] = due
        return {}

    def events(self, days=1, start=None, calendar_id=None):
        today = date.today().isoformat()
        end = (date.today() + timedelta(days=days)).isoformat()
        return [e for e in self._events if today <= e["start"][:10] < end]

    def add_event(self, summary, start, end, description="", location="", calendar_id=None):
        e = {"id": f"ev{len(self._events)}", "uid": f"{self.person}:ev{len(self._events)}", "summary": summary, "start": start,
             "end": end, "location": location}
        self._events.append(e)
        return e

    def delete_event(self, event_id, calendar_id=None):
        self._events = [e for e in self._events if e.get("id") != event_id]

    def relabel(self, ids, add=(), remove=()):
        self.relabelled = getattr(self, "relabelled", []) + list(ids)
        return len(ids)

    def search_mail(self, query, limit=15):
        if "life-os" in query:  # email to to-do: one labelled email the first time
            if getattr(self, "relabelled", None):
                return []
            return [{"id": "lm1", "from": "Hartley Solicitors <office@hartley.example>",
                     "subject": "Re: Updating your wills", "snippet": "Please could you both sign and return the "
                     "enclosed pages by the end of the month.", "link": "#"}]
        return [{"id": "m1", "from": "School Office", "subject": "Friday trip: reply needed",
                 "snippet": "Please confirm whether your child can attend the trip on Friday.", "link": "#"}]

    def create_draft(self, to, subject, body, thread_id=None):
        n = len(self.drafts) + len(getattr(self, "sent_threads", {})) + 1
        did, tid = f"draft-{self.person}-{n}", thread_id or f"thread-{self.person}-{n}"
        self.drafts[did] = {"to": to, "subject": subject, "body": body, "thread_id": tid}
        return {"id": did, "message": {"id": f"msg-{did}", "threadId": tid}}

    def get_draft(self, draft_id):
        d = self.drafts.get(draft_id)
        if d:
            return {"to": d["to"], "subject": d["subject"], "body": d["body"]}
        return {"to": "office@school.example", "subject": "Re: Friday trip", "body": "Yes, Ava can go on the trip. Thanks!"}

    def draft_exists(self, draft_id):
        return draft_id in self.drafts

    def delete_draft(self, draft_id):
        self.drafts.pop(draft_id, None)

    def send_draft(self, draft_id):
        d = self.drafts.pop(draft_id, None)
        if d:  # as if you pressed Send in Gmail
            return self.send_email(d["to"], d["subject"], d["body"], d["thread_id"])
        return {"id": draft_id}

    def mark_handled(self, ids):
        return len(ids)

    # Emails to tradespeople: see reply_after in __init__.

    def send_email(self, to, subject, body, thread_id=None):
        import time
        if not hasattr(self, "sent_threads"):
            self.sent_threads = {}
        tid = thread_id or f"thread-{self.person}-{len(self.sent_threads) + 1}"
        self.sent_threads.setdefault(tid, {"to": to, "subject": subject, "body": body, "at": time.time(), "count": 0})
        self.sent_threads[tid]["count"] += 1
        print(f"[demo email] to {to}: {subject}")
        return {"id": f"msg-{tid}", "threadId": tid}

    def thread_messages(self, thread_id):
        import time
        t = getattr(self, "sent_threads", {}).get(thread_id)
        drafts = [{"id": f"msg-{k}", "from": "me", "snippet": d["subject"], "date": "", "labels": ["DRAFT"]}
                  for k, d in self.drafts.items() if d["thread_id"] == thread_id]
        if not t:
            return drafts
        msgs = [{"id": f"m0-{i}", "from": "me", "snippet": t["subject"], "date": "", "labels": ["SENT"]}
                for i in range(t["count"])] + drafts
        if time.time() - t["at"] > self.reply_after:
            import re  # agree to the first time offered in the email, as a real tradesperson might
            m = re.search(r"^- (\w+day) [^,\n]+, (\S+?) to ", t.get("body", ""), re.M)
            name = (re.match(r"Hello ([^,\n]+),", t.get("body", "")) or [None, "Dave"])[1]
            when = f"{m.group(1)} at {m.group(2)}" if m else "Thursday at 8am"
            msgs.append({"id": "m1", "from": f"{name} <{t['to']}>", "date": "", "labels": ["INBOX"],
                         "snippet": f"Hi, {when} works for me. I'll bring what's needed. {name}"})
        return msgs


class DemoHA:
    def __init__(self):
        self.devices = {
            "lock.front_door": ("Front door", "locked"), "binary_sensor.back_door": ("Back door", "off"),
            "sensor.washer_status": ("Washing machine", "Finished 20 min ago"), "vacuum.robot": ("Robot vacuum", "docked"),
            "climate.living_room": ("Living room heating", "heat"), "weather.home": ("Home", "partlycloudy"),
            "scene.away": ("Away", "scening"), "scene.home": ("Home", "scening"), "scene.goodnight": ("Goodnight", "scening"),
            "light.kitchen": ("Kitchen lights", "on"), "light.hall_reminder": ("Hall reminder", "off"), "light.porch": ("Porch", "off"),
            "sensor.dishwasher_status": ("Dishwasher", "finished"), "sensor.vacuum_filter_left": ("Vacuum filter left", "7"),
            "switch.dishwasher_plug": ("Dishwasher plug", "off"), "switch.washing_machine_plug": ("Washing machine plug", "off"),
            "media_player.kitchen_speaker": ("Kitchen speaker", "idle"), "media_player.living_room_speaker": ("Living room speaker", "off"),
            "person.joe": ("Joe", "home"), "person.sue": ("Sue", "not_home"),
            "calendar.bin_collections": ("Bin collections", "off"),
            "sensor.electricity_meter": ("Electricity meter", "18342.6"), "sensor.gas_meter": ("Gas meter", "4021.38"),
        }
        self.spoken: list[str] = []  # what Life OS said on the speakers (shown in the demo's log)

    def state(self, entity_id):
        name, st = self.devices.get(entity_id, (entity_id, "unavailable"))
        attrs = {"friendly_name": name}
        if entity_id.startswith("weather."):
            attrs.update(temperature=9, temperature_unit="°C")
        if entity_id.startswith("climate."):
            attrs.update(temperature=self.target)
        if entity_id == "binary_sensor.back_door":
            attrs.update(device_class="door")
        if entity_id == "sensor.electricity_meter":
            attrs.update(device_class="energy", state_class="total_increasing", unit_of_measurement="kWh")
        if entity_id == "sensor.gas_meter":
            attrs.update(device_class="gas", state_class="total_increasing", unit_of_measurement="m³")
        return {"entity_id": entity_id, "state": st, "attributes": attrs}

    def service_response(self, domain_service, data):
        """weather.get_forecasts: a cold, dry few days (so the garden shows a frost warning)."""
        if domain_service != "weather.get_forecasts":
            return {}
        today = date.today()
        days = [{"datetime": (today + timedelta(days=i)).isoformat(), "temperature": t, "templow": lo, "precipitation": r}
                for i, (t, lo, r) in enumerate(((7, -1, 0), (6, 0, 0.2), (9, 3, 4.5), (10, 5, 1.2), (8, 2, 0)))]
        return {(data or {}).get("entity_id", "weather.home"): {"forecast": days}}

    def states(self, domain=None):
        return [self.state(e) for e in self.devices if not domain or e.startswith(domain + ".")]

    def summary(self):
        return [{"entity_id": s["entity_id"], "name": s["attributes"]["friendly_name"], "state": s["state"]}
                for s in self.states()]

    def calendar_events(self, entity_id, start, end):
        """Bins every Thursday: general waste and recycling on alternate weeks, garden waste with recycling."""
        a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
        out, day = [], a + timedelta(days=(3 - a.weekday()) % 7)
        while day < b:
            names = ["Recycling (green bin)", "Garden waste (brown bin)"] if day.isocalendar()[1] % 2 else ["General waste (black bin)"]
            out += [{"summary": n, "start": {"date": day.isoformat()}, "end": {"date": (day + timedelta(days=1)).isoformat()}}
                    for n in names]
            day += timedelta(days=7)
        return out

    target = 20.5  # living room heating target, °C

    def history(self, entity_id, start, end, attributes=False):
        """A made-up week: an electricity meter going up through the day, and the heating on mornings and evenings."""
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        older = (datetime.now(timezone.utc) - b).days >= 6  # last week used a bit more
        out, t, meter = [], a, 5000.0
        while t < b:
            if entity_id.startswith("sensor."):
                use = 0.9 if t.hour in (7, 8, 17, 18, 19) else 0.25 if 9 <= t.hour < 23 else 0.12
                meter += use * (1.12 if older else 1.0)
                out.append({"state": f"{meter:.2f}", "last_changed": t.isoformat()})
            else:
                on = t.hour in (6, 7, 17, 18, 19, 20) or (older and t.hour == 21)
                out.append({"state": "heat", "attributes": {"hvac_action": "heating" if on else "idle"}, "last_changed": t.isoformat()})
            t += timedelta(hours=1)
        return out

    def call_service(self, service, data=None):
        entity = (data or {}).get("entity_id")
        if service == "climate.set_temperature":
            self.target = (data or {}).get("temperature", self.target)
        elif service == "lock.lock" and entity in self.devices:
            self.devices[entity] = (self.devices[entity][0], "locked")
        if service.startswith("tts."):
            msg = (data or {}).get("message", "")
            self.spoken.append(msg)
            print(f"[speaker] {msg}")
        if service == "vacuum.start":
            self.devices["vacuum.robot"] = ("Robot vacuum", "cleaning")
        elif entity in self.devices and service.endswith(("turn_on", "turn_off")):
            self.devices[entity] = (self.devices[entity][0], "on" if service.endswith("turn_on") else "off")
        return [{"entity_id": entity}]


class DemoAI:
    """A tiny rule-based stand-in for Claude / Gemini that uses the same tools and safety rules."""
    name = "demo"

    def chat(self, system, tools, prompt, on_tool, deep=False, history=None, **kw):
        forced = tools[0]["name"] if len(tools) == 1 else ""
        if forced == "save_letter":  # a sample school letter, as if read from the photo
            d = date.today()
            on_tool(forced, {"readable": True, "sender": "Oakfield Primary School",
                             "about": "Year 2 trip to the Royal Armouries: consent slip and £8.50 by next week.",
                             "dates": [{"what": "Year 2 trip to the Royal Armouries (packed lunch)",
                                        "date": (d + timedelta(days=12)).isoformat(), "time": "09:00"}],
                             "todos": [{"text": "Return Ava's trip consent slip", "due": (d + timedelta(days=5)).isoformat()}],
                             "payment": {"amount": 8.5, "pay_by": (d + timedelta(days=5)).isoformat(), "how": "ParentPay"}})
            return "Saved."
        if forced == "save_dinners" and kw.get("image"):
            on_tool(forced, {"seen": ["chicken thighs", "peppers", "half a tub of cream", "spinach", "cheddar"],
                             "dinners": [{"name": "Creamy chicken and spinach pasta", "uses": ["chicken thighs", "cream",
                                          "spinach"], "missing": ["penne"], "minutes": 25},
                                         {"name": "Chicken fajitas", "uses": ["chicken thighs", "peppers", "cheddar"],
                                          "missing": ["tortilla wraps"], "minutes": 20}]})
            return "Saved."
        text = prompt.split("] ", 1)[-1].strip()
        low = text.lower()
        m = re.search(r"add (.+?) to (?:the )?(shopping|kids|inbox|home maintenance)", low)
        if m:
            items = [i.strip().capitalize() for i in re.split(r",| and ", m.group(1)) if i.strip()]
            for i in items:
                on_tool("add_task", {"list_name": m.group(2).title() if m.group(2) != "home maintenance"
                                     else "Home maintenance", "title": i})
            listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
            return f"Added {listed} to {m.group(2).title()}."
        if "what" in low and ("on" in low or "today" in low):
            import json
            events = json.loads(on_tool("get_calendar", {"days": 1})[0])
            if not events:
                return "Nothing in the calendar today."
            lines = [f"{e['start'][11:16] or 'All day'} {e['summary']} ({' and '.join(e['who'])})" for e in events]
            return "Today:\n" + "\n".join(lines)
        if "plan" in low and "dinner" in low:
            meals, start = ["Chicken fajitas", "Fish pie", "Veggie curry", "Spaghetti bolognese", "Roast chicken",
                            "Pizza night", "Leftovers"], date.today()
            plan = [{"day": (start + timedelta(days=i)).isoformat(), "dinner": meals[i],
                     "ingredients": [["chicken thighs", "peppers", "tortillas"], ["white fish", "potatoes"],
                                     ["chickpeas", "coconut milk", "spinach"], ["beef mince", "chopped tomatoes"],
                                     ["whole chicken", "carrots"], ["pizza bases", "mozzarella"], []][i]}
                    for i in range(7)]
            on_tool("set_meal_plan", {"meals": plan})
            return "Planned: " + ", ".join(m["dinner"] for m in plan) + ". The ingredients are on Shopping."
        if "unlock" in low:
            on_tool("call_home_service", {"service": "lock.unlock", "entity_id": "lock.front_door"})
            return "I've asked for approval on your phone to unlock the front door. It expires in 15 minutes."
        if "insurance" in low and re.search(r"\d", low):
            return "In the real assistant I'd track that renewal for you. In this demo, add it under Lists > Renewals."
        return ("This is the demo assistant, so I only understand a few things: 'add eggs to shopping', "
                "'what's on today', 'plan dinners', 'unlock the front door'. The real Claude or Gemini understands "
                "anything.")


def build():
    from lifeos import approvals
    from lifeos.accounts import GoogleAccounts
    from lifeos.agent import Agent
    from lifeos.app import create_app
    from lifeos.config import load_settings
    from lifeos.store import Store
    from lifeos.tools import Services

    from lifeos import paths
    os.environ.update(LIFEOS_API_KEY="demo-household-key", LIFEOS_SIGNING_SECRET="demo-signing-secret-not-for-real-use",
                      LIFEOS_PUBLIC_URL=f"http://localhost:{port()}")
    import atexit
    import shutil
    import tempfile
    files_dir = tempfile.mkdtemp(prefix="lifeos-demo-files-")  # attachments vanish with the demo
    os.environ["LIFEOS_FILES_DIR"] = files_dir
    atexit.register(shutil.rmtree, files_dir, True)
    s = load_settings(paths.default_settings_file())
    s.public_url = f"http://localhost:{port()}"
    s.api_key = DEMO_KEY
    s.demo = True
    s.household.update(names={"me": "Joe", "wife": "Sue"}, kids=["Ava", "Leo"], area="Leeds LS6")
    s.behaviour.setdefault("wall", {})["weather_entity"] = "weather.home"
    s.provider = "demo"
    d = date.today()
    at = lambda day, hm: f"{(d + timedelta(days=day)).isoformat()}T{hm}:00"  # noqa: E731
    sam = DemoGoogle("me", [
        {"id": "a", "uid": "a", "summary": "School run", "start": at(0, "08:15"), "end": at(0, "08:45")},
        {"id": "b", "uid": "b", "summary": "Clinic", "location": "Practice", "start": at(0, "09:00"), "end": at(0, "17:00")},
        {"id": "c", "uid": "c", "summary": "Swimming lesson", "location": "Leisure centre", "start": at(0, "17:30"), "end": at(0, "18:15")},
        {"id": "f", "uid": "f", "summary": "Bin collection", "start": (d + timedelta(days=1)).isoformat(),
         "end": (d + timedelta(days=2)).isoformat()}])
    priya = DemoGoogle("wife", [
        {"id": "c", "uid": "c", "summary": "Swimming lesson", "location": "Leisure centre", "start": at(0, "17:30"), "end": at(0, "18:15")},
        {"id": "e", "uid": "e", "summary": "Parents' evening", "start": at(0, "19:00"), "end": at(0, "19:30")},
        {"id": "g", "uid": "g", "summary": "Dentist (Leo)", "start": at(1, "10:30"), "end": at(1, "11:00")}])
    for item in ("Milk", "Bin bags", "Dishwasher tablets"):
        sam.add_task("Shopping", item)
    sam.add_task("Admin and renewals", "Renew car insurance", "Quote from current insurer arrived",
                 (d - timedelta(days=1)).isoformat())
    sam.add_task("Home maintenance", "Descale the kettle", due=d.isoformat())
    priya.add_task("Gym", "Book Saturday class")

    store = Store(":memory:")
    svc = Services(settings=s, store=store, ha=DemoHA(), google=GoogleAccounts(s, people={"me": sam, "wife": priya}))

    class Log:  # notifications just print in the demo
        def push(self, message, title="Life OS", priority=3, actions=None, tags=None):
            print(f"[notification] {title}: {message.splitlines()[0]}")
    svc.notifier = Log()

    for chore, kid, value in (("Empty dishwasher", "Ava", 0.5), ("Make bed", "Ava", 0.2), ("Lay the table", "Leo", 0.3)):
        store.log_chore(chore, kid, value)
    store.add_renewal("Car insurance", (d + timedelta(days=19)).isoformat())
    store.add_renewal("MOT", (d + timedelta(days=47)).isoformat())
    store.add_renewal("Home insurance", (d + timedelta(days=240)).isoformat())
    store.set_meal(d.isoformat(), "Chicken fajitas", ["chicken thighs", "peppers", "tortillas"])
    store.set_meal((d + timedelta(days=1)).isoformat(), "Fish pie")
    store.add_brief("morning_brief", "Swimming at 17:30 (Ava's kit is in the hall).\nParents' evening at 19:00.\n"
                                     "Car insurance renews in 19 days: a renewal task is in Admin.\nBins go out tonight.")
    approvals.request_approval(svc, "send_draft", {"draft_id": "draft-1", "summary": "Reply to school about Friday's trip"})

    # Sample to-dos, already sorted, and a project under way
    from lifeos import todos
    plus = lambda n: (d + timedelta(days=n)).isoformat()  # noqa: E731
    for t in (
        {"title": "Replace smoke alarm battery (landing)", "category": "Home maintenance", "priority": 1, "timescale": "today", "effort_min": 10, "next_action": "Get a 9V battery from the drawer"},
        {"title": "Fix dripping bath tap", "category": "Home maintenance", "priority": 2, "timescale": "this_week", "effort_min": 45, "next_action": "Check the washer size and buy two"},
        {"title": "Return Ava's school shoes", "category": "Errands", "priority": 2, "due": plus(2), "effort_min": 20, "who": "wife", "next_action": "Find the receipt"},
        {"title": "Book car service", "category": "Car", "priority": 3, "due": plus(12), "effort_min": 10, "next_action": "Call the garage for a date"},
        {"title": "Renew Sue's passport", "category": "Admin and renewals", "priority": 3, "timescale": "this_month", "effort_min": 40, "who": "wife", "next_action": "Get two passport photos"},
        {"title": "Clear the gutters", "category": "Home maintenance", "priority": 3, "timescale": "later", "effort_min": 120, "recurrence": "yearly"},
        {"title": "Cut back the hedge", "category": "Garden", "priority": 4, "timescale": "someday", "effort_min": 90},
    ):
        todos.file_todo(svc, todos.normalise(svc, t, t["title"]), "demo")
    # Home details Life OS uses to pick the right things
    for area, item, details in (
        ("Landing", "Ceiling light", "B22 bayonet, 8.5W LED, warm white (2700K), 1 bulb"),
        ("Kitchen", "Spotlights", "GU10 LED, 5W, warm white, 6 bulbs"),
        ("Boiler", "Boiler", "Worcester Bosch Greenstar 30i, last serviced Dec 2025"),
        ("Car", "VW Golf (2019)", "Tyres 205/55 R16; wiper blades 24in + 19in"),
        ("Kitchen", "Dishwasher", "Bosch Serie 4; all-in-one tablets"),
        ("Hall", "Smoke alarm", "Kidde 10Y29, sealed 10-year battery, fitted 2019"),
    ):
        store.set_home_detail(area, item, details)
    # Saved tradespeople (your go-to electrician has an email; the plumber is phone-only)
    store.save_contact(name="Dave Hughes", company="Spark Electrical", trade="electrician",
                       email="jobs@spark-electrical.example", phone="0113 496 0123", favourite=1,
                       notes="NICEIC registered; did the kitchen spotlights")
    store.save_contact(name="Pipe Dreams Plumbing", trade="plumber", phone="07700 900456")
    store.save_contact(name="Heat Right", trade="gas engineer", email="bookings@heatright.example", phone="0113 496 0999",
                       favourite=1, notes="Services the boiler every December")

    # Two jobs that sort themselves out: one Life OS can order straight away, one where it needs to ask
    from lifeos import solver
    for t in ({"title": "Change the upstairs landing lightbulb", "category": "Home maintenance", "priority": 3,
               "timescale": "this_week", "effort_min": 10},
              {"title": "Buy Mum's birthday present", "category": "Family and social", "priority": 2, "due": plus(9),
               "effort_min": 30, "who": "me", "project": ""},
              {"title": "Kitchen socket by the kettle has stopped working", "category": "Home maintenance", "priority": 2,
               "timescale": "this_week", "effort_min": 60, "next_action": "Get an electrician to check it"}):
        filed = todos.file_todo(svc, todos.normalise(svc, t, t["title"]), "demo")
        if not filed.get("duplicate"):
            solver.solve_and_store(svc, None, filed)

    project = todos.start_project(svc, None, "Redecorate the hallway", "Light grey walls, new skirting boards")
    todos.complete_todo(svc, project["list"], project["next"]["id"])

    # A few months of history, so predictions have something to learn from
    from lifeos import activity
    ago = lambda n: (d - timedelta(days=n)).isoformat() + "T10:00:00+00:00"  # noqa: E731
    for title, days in (("Andrex toilet roll 16 pack", (85, 64, 43, 22, 2)), ("Loo roll", ()),
                        ("Washing up liquid", (70, 46, 23)), ("Coffee beans", (40, 26, 13)),
                        ("Laundry detergent", (90, 58, 30)), ("Nappies", ()), ("Kitchen roll", (60, 31, 1)),
                        ("Toothpaste", (95, 50, 6))):
        for i, n in enumerate(days):
            activity.record_bought(svc, title, "me", "demo", ref=f"demo:{title}:{i}", at=ago(n))
    for title, cat, days in (("Bleed the radiators", "Home maintenance", (400, 215, 30)),
                             ("Clean the oven", "Home maintenance", (190, 100, 12)),
                             ("Wash the car", "Car", (50, 29, 8))):
        for i, n in enumerate(days):
            activity.record_done(svc, title, cat, "me", "demo", ref=f"demo:{title}:{i}", at=ago(n))
    s.behaviour["checkups"] = [{"what": "Dentist check-up", "who": "kids", "every_months": 6},
                               {"what": "Boiler service", "every_months": 12}]
    activity.record_done(svc, "Boiler service", "Home maintenance", "me", "demo", ref="demo:boiler", at=ago(350))
    sam.add_event("Mum's birthday", plus(9), plus(10))
    store.save_product("toilet roll", "Andrex Classic Clean Toilet Roll, 24 rolls", asin="B07DEMO001")
    store.save_product("dishwasher tablets", "Finish Ultimate All in One, 60 tablets", asin="B07DEMO002")

    # v0.15: a car, subscriptions and spending, school holidays, appliance care, cheap energy, dinners, people, manual
    from lifeos import cars, childcare, manual, money, profiles
    cars.add_car(svc, "KX19 ABC", "The Golf", mot_due=plus(38), tax_due=plus(70))
    money.record_subscription(svc, "Netflix", 10.99, "monthly", plus(3), "https://www.netflix.com/account", ago(60)[:10])
    money.record_subscription(svc, "Netflix", 12.99, "monthly", plus(3), "https://www.netflix.com/account", ago(30)[:10])
    money.record_subscription(svc, "Spotify Family", 19.99, "monthly", plus(12), "", ago(18)[:10])
    money.record_subscription(svc, "Disney+", 89.90, "yearly", plus(5), "https://www.disneyplus.com/account", ago(360)[:10])
    money.record_subscription(svc, "Amazon Prime", 95.00, "yearly", plus(140), "", ago(225)[:10])
    first = d.replace(day=1)
    lastm = (first - timedelta(days=1)).replace(day=1)
    spend = [(first, "Tesco", [("Weekly shop", 96.40, "groceries"), ("Kids' wellies", 18.00, "kids")]),
             (first + timedelta(days=min(6, max(0, (d - first).days))), "Amazon", [("Dishwasher tablets", 14.99, "household"),
                                                                                    ("Lego set for Leo's party", 24.99, "gifts")]),
             (lastm, "Tesco", [("Weekly shop", 102.10, "groceries"), ("Weekly shop", 88.70, "groceries")]),
             (lastm + timedelta(days=9), "Pizza Express", [("Family dinner", 54.20, "eating out")]),
             (lastm + timedelta(days=12), "Clarks", [("School shoes x2", 76.00, "kids")])]
    for i, (when, shop, lines) in enumerate(spend):
        money.record_receipt(svc, {"items": [{"name": n, "price": p, "category": c} for n, p, c in lines]}, when.isoformat(),
                             shop, "me", f"demo:receipt:{i}")
    mon = d + timedelta(days=(7 - d.weekday()) % 7 or 7) + timedelta(days=14)
    childcare.add_closed(svc, "Christmas holidays", (mon + timedelta(days=7)).isoformat(), (mon + timedelta(days=18)).isoformat(), "demo")
    childcare.add_closed(svc, "INSET day", mon.isoformat(), mon.isoformat(), "demo")
    for k in (0, 7, 8):  # both working on the INSET day and the first two days of the holidays
        day = (mon + timedelta(days=k)).isoformat()
        sam.add_event("Clinic", f"{day}T08:30:00", f"{day}T17:30:00")
        priya.add_event("Work", f"{day}T09:00:00", f"{day}T17:00:00")
    s.behaviour["appliance_care"] = [
        {"what": "Dishwasher salt", "entity": "sensor.dishwasher_status", "cycle_end": "finished", "every": 30, "buy": True},
        {"what": "Robot vacuum filter", "entity": "sensor.vacuum_filter_left", "below": 10, "buy": True}]
    for i in range(27):
        at = (datetime.now(timezone.utc) - timedelta(days=26 - i, hours=2)).isoformat()
        store.record_activity("cycle", "sensor.dishwasher_status", "Dishwasher salt", "", "demo", "", ref=f"demo:cycle:{i}", at=at)
    s.behaviour["voice"] = {"speaker": "media_player.kitchen_speaker", "announce": []}
    s.behaviour["energy"] = {"octopus_tariff": "E-1R-AGILE-24-10-01-C", "appliances": [
        {"name": "Dishwasher", "entity": "switch.dishwasher_plug", "hours": 2},
        {"name": "Washing machine", "entity": "switch.washing_machine_plug", "hours": 2}],
        "usage_entity": "sensor.electricity_kwh", "heating_entity": "climate.living_room"}
    # v0.16: bills (one just went up), pocket money and a saving goal, a half-term trip
    from lifeos import bills, household, trips
    last_q = (d - timedelta(days=92)).isoformat()
    bills.record(svc, "Octopus Energy", "energy", 112.0, "monthly", when=(d - timedelta(days=35)).isoformat(),
                 next_date=(d + timedelta(days=6)).isoformat())
    bills.record(svc, "Octopus Energy", "energy", 126.0, "monthly", when=(d - timedelta(days=4)).isoformat(),
                 next_date=(d + timedelta(days=6)).isoformat())
    bills.record(svc, "Leeds City Council", "council tax", 187.0, "monthly", when=last_q, next_date=(d + timedelta(days=12)).isoformat())
    bills.record(svc, "Yorkshire Water", "water", 41.5, "monthly", when=last_q, next_date=(d + timedelta(days=20)).isoformat())
    bills.record(svc, "BT Broadband", "broadband", 38.99, "monthly", when=last_q, next_date=(d + timedelta(days=3)).isoformat())
    household.set_allowance(svc, "Ava", 3.0)
    household.set_allowance(svc, "Leo", 2.0)
    store.log_chore("Weekly allowance", "Ava", 3.0)
    goal = household.add_goal(svc, "Ava", "Lego treehouse", 25.0)
    household.save_towards(svc, goal["id"], 3.0)
    household.add_goal(svc, "Leo", "Football boots", 30.0)
    trip_start = d + timedelta(days=(4 - d.weekday()) % 7 + 21)
    trips.create(svc, "Cornwall", "Cornwall", trip_start.isoformat(), (trip_start + timedelta(days=4)).isoformat(),
                 travel="car", kids=True, pets=False, person="me")
    # v0.17: away mode settings, a party with a few guests, and what the house has for seasonal jobs
    s.behaviour["away"] = {"heating_entity": "climate.living_room", "lights": ["light.kitchen", "light.porch"]}
    from lifeos import party
    pday = d + timedelta(days=(5 - d.weekday()) % 7 + 35)
    pt = party.create(svc, "Ava", pday.isoformat(), "14:00", "16:00", 8, "Jump Zone", "Unicorns", "me")
    for name, email, answer in (("Mia", "mia.parent@example.com", "yes"), ("Noah", "", "yes"), ("Isla", "isla.parent@example.com", ""),
                                ("Oscar", "", "no"), ("Freya", "freya.parent@example.com", "")):
        party.add_guest(svc, pt["id"], name, email)
        if answer:
            party.rsvp(svc, pt["id"], name, answer)
    store.set_state("seasonal_have", '["car", "garden", "gas boiler", "outside tap"]')
    for n, dinner, ing, rating in ((40, "Lamb tagine", ["diced lamb", "apricots", "peppers", "couscous"], 1),
                                   (26, "Halloumi traybake", ["halloumi", "peppers", "courgette"], 1),
                                   (19, "Liver and onions", ["liver", "onions"], -1),
                                   (15, "Thai green curry", ["chicken", "green curry paste", "coconut milk"], 1),
                                   (12, "Sausage and mash", ["sausages", "potatoes"], 0)):
        day = (d - timedelta(days=n)).isoformat()
        store.set_meal(day, dinner, ing, "")
        store.rate_meal(day, rating)
    yday = (d - timedelta(days=1)).isoformat()
    if not any(m["day"] == yday for m in store.meal_history(yday)):
        store.set_meal(yday, "Chicken fajitas", ["chicken", "peppers", "tortilla wraps"], "")
    store.save_contact(None, name="Grandma Pat", trade="family (childcare)", phone="07700 900123")
    bd = d + timedelta(days=9)
    profiles.save(svc, "Mum", "Joe's mum", f"1958-{bd.month:02d}-{bd.day:02d}", "Gardening, crime novels, gin",
                  "Medium, size 6 shoes", wishlist="Hori hori garden knife https://www.amazon.co.uk/dp/B000DEMO01\n"
                  "The new Richard Osman novel", given=[{"year": d.year - 1, "what": "Burgon & Ball gardening gloves and kneeler set"}])
    profiles.save(svc, "Grandad", "Sue's dad", "1950-06-02", "Football (Leeds United), history books, whisky", "Large")
    for sec, title, details in (("Water", "Stopcock", "Under the kitchen sink, behind the bleach. Turn clockwise to close."),
                                ("Electrics", "Fuse box", "Cupboard under the stairs, top shelf. Torch hangs on the door."),
                                ("Heating", "Boiler", "Worcester Bosch Greenstar 30i, in the utility room. Pressure 1 to 1.5 bar."),
                                ("Bins", "Bin day", "Thursday morning. Green (recycling) and black alternate; brown every other Monday."),
                                ("Internet", "Wi-Fi", "Network: LifeHouse. Password: maple-orbit-42 (guest network: LifeGuest)")):
        manual.save(svc, title, details, sec)

    # v0.18: parcels, the babysitter sheet, reading and homework, car costs and MOT advisories, a babysitter, the vacuum
    from lifeos import carcosts, cleaning, deliveries, kidstuff, sitter
    deliveries.save(svc, {"is_delivery": True, "status": "out_for_delivery", "retailer": "Amazon", "item": "Lego treehouse",
                          "carrier": "Amazon", "expected_date": d.isoformat(), "window": "10:15 to 11:15",
                          "tracking_url": "https://www.amazon.co.uk/gp/your-account/order-history"}, "demo-1", "me")
    deliveries.save(svc, {"is_delivery": True, "status": "dispatched", "retailer": "John Lewis", "item": "School shoes (Leo)",
                          "carrier": "DPD", "expected_date": (d + timedelta(days=2)).isoformat()}, "demo-2", "wife")
    deliveries.save(svc, {"is_delivery": True, "status": "ready_to_collect", "retailer": "Boots", "item": "Prescription bag",
                          "carrier": "InPost", "collect_from": "InPost locker, Co-op car park"}, "demo-3", "wife")
    sitter.save(svc, {"address": "12 Example Road, Leeds LS6 1AA",
                      "parents": [{"name": "Joe", "phone": "07700 900001"}, {"name": "Sue", "phone": "07700 900002"}],
                      "contacts": [{"name": "Grandma Pat", "role": "Nearby, has a key", "phone": "07700 900123"}],
                      "kids": [{"name": "Ava", "bedtime": "19:30", "notes": "Story and a glass of water. Night light on."},
                               {"name": "Leo", "bedtime": "19:00", "notes": "Needs Bunny. Calpol is in the high cupboard if he's poorly; check with us first."}],
                      "wifi_name": "LifeGuest", "wifi_password": "", "rules": "No screens after 7pm. Help yourself to anything in the fridge.",
                      "notes": "The back door sticks: lift the handle while you turn the key.", "include_manual": True})
    store.save_contact(None, name="Megan Clarke", trade="babysitter", phone="07700 900777", email="megan.sitter@example.com")
    for kid, mins, book, back in (("Ava", 20, "The Twits", 0), ("Ava", 15, "The Twits", 1), ("Leo", 10, "Oi Frog!", 0),
                                  ("Ava", 25, "The Twits", 3)):
        if (d - timedelta(days=back)) >= kidstuff.week_start(d):
            kidstuff.log_reading(svc, kid, mins, book, "me", (d - timedelta(days=back)).isoformat())
    kidstuff.add_homework(svc, "Ava", "Spellings: words ending in -tion", (d + timedelta(days=(4 - d.weekday()) % 7)).isoformat(), "wife")
    kidstuff.add_homework(svc, "Ava", "Times tables: 7s", (d + timedelta(days=3)).isoformat(), "me")
    kidstuff.add_homework(svc, "Leo", "Read with an adult and sign the diary", d.isoformat(), "wife")
    for kind, amt, back, miles, litres in (("fuel", 68.40, 6, 48210, 47.5), ("fuel", 61.20, 27, 47805, 42.0),
                                           ("service", 189.00, 140, 45900, None), ("parking", 4.50, 3, None, None),
                                           ("insurance", 412.00, 200, None, None), ("tyres", 96.00, 80, 46850, None)):
        carcosts.add_cost(svc, "KX19ABC", kind, amt, (d - timedelta(days=back)).isoformat(), miles, litres)
    golf = store.get("car", "KX19ABC")
    golf["mot_tests"] = [{"date": (d - timedelta(days=330)).isoformat(), "result": "Passed", "miles": 44120,
                          "advisories": ["Nearside front tyre worn close to legal limit", "Brake pipe slightly corroded"],
                          "failures": []},
                         {"date": (d - timedelta(days=695)).isoformat(), "result": "Passed", "miles": 36400,
                          "advisories": [], "failures": []}]
    store.put("car", "KX19ABC", {k: v for k, v in golf.items() if k not in ("id", "at", "updated_at")})
    cleaning.save_settings(svc, vacuum={"entity": "vacuum.robot", "days": ["mon", "wed", "fri"], "latest": "16:00"})
    store.put("clean_done", "demo-c1", {"job": "bathroom", "by": "wife", "date": (d - timedelta(days=8)).isoformat(), "points": 3},
              at=(d - timedelta(days=8)).isoformat())
    store.put("clean_done", "demo-c2", {"job": "beds", "by": "me", "date": (d - timedelta(days=9)).isoformat(), "points": 3},
              at=(d - timedelta(days=9)).isoformat())

    # v0.19: Christmas, school kit, the garden, meter readings, documents and the contents list
    from lifeos import christmas, documents, garden, inventory, meters, schoolkit
    year = d.year if d.month >= 2 else d.year - 1
    christmas.set_budget(svc, 600, year)
    for name, budget in (("Ava", 150), ("Leo", 150), ("Grandma Pat", 40), ("Megan (teacher)", 10)):
        christmas.add_person(svc, name, budget, year)
    xp = {p["name"]: p["id"] for p in christmas.plan(svc, year)["people"]}
    christmas.update_person(svc, xp["Ava"], idea="Art set with easel", year=year)
    christmas.update_person(svc, xp["Leo"], idea="Dinosaur torch", year=year)
    christmas.bought(svc, xp["Ava"], "Roller skates", 49.99, "Decathlon", year)
    christmas.bought(svc, xp["Leo"], "Lego treehouse", 45, "Amazon", year)
    christmas.bought(svc, xp["Grandma Pat"], "Garden centre voucher", 25, "Online", year)
    p = christmas.plan(svc, year)
    skates = next(b for x in p["people"] if x["name"] == "Ava" for b in x["bought"])
    christmas.present(svc, xp["Ava"], skates["id"], wrapped=True, year=year)
    christmas.card(svc, "Grandma Pat\nUncle Rob and Jen\nThe Patels next door\nMrs Hughes (Ava's teacher)\nAuntie Kate", year=year)
    first = christmas.plan(svc, year)["cards"][0]
    christmas.card(svc, cid=first["id"], sent=True, year=year)
    christmas.add_date(svc, "Order the turkey from the butcher", f"{year}-12-12", year)
    christmas.add_date(svc, "Book the supermarket delivery slot", f"{year}-12-05", year)
    schoolkit.save(svc, {"Ava": {"pe": ["tue", "thu"], "lunch": ["fri"], "other": [{"what": "recorder", "days": ["wed"]}]},
                         "Leo": {"pe": ["wed"], "lunch": [], "other": [{"what": "swimming bag", "days": ["mon"]}]}})
    garden.save(svc, have=["lawn", "hedge", "pots", "trees"], weather="weather.home")
    meters.save_cfg(svc, [{"id": "electricity", "name": "Electricity", "unit": "kWh", "sensor": "sensor.electricity_meter",
                           "supplier": "readings@example-energy.co.uk"},
                          {"id": "gas", "name": "Gas", "unit": "m³", "sensor": "", "supplier": ""}])
    for back, e, g in ((86, 17321.0, 3874.2), (56, 17602.4, 3911.9), (26, 17987.1, 3968.4)):
        day = (d - timedelta(days=back)).isoformat()
        meters.add_reading(svc, "electricity", e, day, "home assistant")
        meters.add_reading(svc, "gas", g, day)
    for kind, whose, where, exp in (("Passport", "Joe", "Grey folder, study desk drawer", (d + timedelta(days=1400)).isoformat()),
                                    ("Passport", "Sue", "Grey folder, study desk drawer", (d + timedelta(days=980)).isoformat()),
                                    ("Passport", "Ava", "Grey folder, study desk drawer", (d + timedelta(days=640)).isoformat()),
                                    ("Passport", "Leo", "Grey folder, study desk drawer", (d + timedelta(days=150)).isoformat()),
                                    ("Birth certificate", "Leo", "Fireproof box under the stairs", ""),
                                    ("Will", "", "With Hartley Solicitors (copy in the fireproof box)", ""),
                                    ("Car logbook (V5C)", "", "Blue wallet in the kitchen drawer", "")):
        documents.save(svc, kind, whose, where, exp)
    inventory.set_cover(svc, 35000)
    for name, room, value, back, serial in (("55-inch TV", "Living room", 899, 400, "LG55C3-77812"),
                                            ("Sofa", "Living room", 1250, 900, ""),
                                            ("MacBook Pro", "Study", 1899, 1500, "C02XK1ABCD"),
                                            ("Engagement ring", "Jewellery and watches", 2400, 4000, ""),
                                            ("Road bike", "Garage and shed", 950, 700, "WTU123456")):
        inventory.save_item(svc, name, room, value, (d - timedelta(days=back)).isoformat(), serial)

    # v0.20: the freezer, storage boxes, guests, something's broken and the kids' routines
    from lifeos import boxes, freezer, guests
    for name, portions, where, back in (("Spaghetti bolognese", 3, "Top drawer", 20), ("Chicken curry", 2, "Top drawer", 104),
                                        ("Frozen peas", 1, "Middle drawer", 40), ("Fish fingers", 1, "Middle drawer", 12),
                                        ("Vanilla ice cream", 1, "Bottom drawer", 30)):
        freezer.add(svc, name, portions, where, (d - timedelta(days=back)).isoformat())
    boxes.save(svc, "Christmas decorations", "Loft", ["Christmas lights", "Tree baubles", "Wreath", "Advent calendar",
                                                       "Tree skirt"])
    boxes.save(svc, "Baby clothes", "Loft", ["Ava 0-3 months", "Leo 3-6 months", "Baby sling"])
    boxes.save(svc, "Camping", "Garage", ["Tent", "Sleeping bags", "Camping stove", "Head torches", "Windbreak"])
    guests.save(svc, ssid="Apple-Home", password="sample-wifi-pass", security="WPA",
                notes="Heating: the thermostat is in the hall.\nHot water: the shower's pull cord is outside the door.\n"
                      "Bins: black bin and recycling go out on Wednesday night.\nParking: the drive is fine.")
    guests.add_visit(svc, "Grandma Pat", (d + timedelta(days=20)).isoformat(), (d + timedelta(days=23)).isoformat(),
                     "Spare room", "No onions, likes a cup of tea first thing")
    svc.store.add_renewal("Warranty: Bosch washing machine", (d + timedelta(days=500)).isoformat(), category="Warranty",
                          notes=f"Bought from AO.com on {(d - timedelta(days=230)).isoformat()}. Receipt: https://mail.google.com/",
                          remind_days=30, repeat_months=0)
    svc.store.add_new("purchase", "demo-kettle", {"item": "Russell Hobbs kettle", "category": "household", "amount": 39.99,
                                                  "shop": "Argos", "person": "me", "link": ""},
                      at=(d - timedelta(days=21)).isoformat())

    # Watch for the (simulated) electrician's reply, as the real scheduler does every 30 minutes.
    import threading
    import time

    from lifeos import trades

    def watch():
        from lifeos import bridge
        while True:
            time.sleep(float(os.getenv("LIFEOS_DEMO_WATCH_SECONDS", "5")))
            try:  # ticks on the Waiting on list (in the app's Lists) act, as they would from Gemini
                bridge.sync_ticks(svc)
            except Exception as e:
                print("demo tick check:", e)
            try:
                trades.check_requests(svc)
            except Exception as e:  # keep the demo running whatever happens
                print("[demo] reply check failed:", e)
    threading.Thread(target=watch, daemon=True).start()

    agent = Agent(svc, provider=DemoAI())
    agent.active_provider_name = lambda: "demo"
    agent.switch_provider = lambda name, provider=None: name  # the demo keeps its stand-in assistant
    return create_app(svc, agent, run_scheduler=False), svc


def main():
    import uvicorn

    app, _ = build()
    from lifeos import __version__
    print(f"\nLife OS {__version__} demo running: open http://localhost:{port()}/app/  (household key: {DEMO_KEY})")
    print(f"Kitchen display: http://localhost:{port()}/app/wall.html\nStop with Ctrl+C. Nothing is saved.\n")
    uvicorn.run(app, host="127.0.0.1", port=port(), log_level="warning")


if __name__ == "__main__":
    main()
