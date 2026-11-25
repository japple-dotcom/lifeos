"""End-to-end: drives the real app in Chrome against demo mode, like a person tapping through it.

Skipped automatically when Playwright or Chrome isn't installed. To run:
    pip install playwright && python -m playwright install chromium
    python -m pytest -q tests/test_e2e_browser.py
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
playwright = pytest.importorskip("playwright.sync_api")
CHROME = os.getenv("CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    os.environ["LIFEOS_DEMO_PORT"] = str(port)
    os.environ["LIFEOS_DEMO_REPLY_SECONDS"] = "2"  # the pretend electrician answers quickly
    os.environ["LIFEOS_DEMO_WATCH_SECONDS"] = "1"
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib

    import demo
    importlib.reload(demo)
    import uvicorn

    app, svc = demo.build()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", svc
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"]) if CHROME else p.chromium.launch()
        except Exception as e:  # no browser available on this machine
            pytest.skip(f"Chrome not available: {e}")
        yield b
        b.close()


def _phone(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="en-GB")
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    return ctx, page


def _sign_in(page, url, name="Joe"):
    page.goto(f"{url}/app/")
    page.get_by_role("button", name=name).click()
    page.get_by_label("Household key").fill("demo")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("heading", level=1).filter(has_text=name).wait_for()


def test_a_day_in_the_app(base_url, browser):
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # Sign in: wrong key first
    page.goto(f"{url}/app/")
    page.get_by_role("button", name="Joe").click()
    page.get_by_label("Household key").fill("wrong")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("alert").filter(has_text="not right").wait_for()
    page.get_by_label("Household key").fill("demo")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("heading", name="Joe").wait_for()

    # Today: brief, dinner, approval with the email shown, merged calendar
    assert "Chicken fajitas" in page.locator("#dinner-line").inner_text()
    assert "office@school.example" in page.locator(".approval-details").inner_text()
    assert page.locator("#events-list li").count() == 4
    page.get_by_role("button", name="Approve: Reply to school about Friday's trip").click()
    page.locator("#toast").filter(has_text="Approved").wait_for()
    page.locator("#approvals-block").wait_for(state="hidden")  # Today reloads after the toast

    # The tab bar must be tappable once messages have faded (a hidden message once covered it)
    page.wait_for_timeout(4500)
    blocked = page.evaluate("""() => [...document.querySelectorAll('#tabbar a')].filter(a => {
        const b = a.getBoundingClientRect();
        return !a.contains(document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)); }).length""")
    assert blocked == 0

    # Ask: the assistant uses the same tools
    page.get_by_role("link", name="Ask").click()
    page.get_by_label("Your message").fill("add oat milk to shopping")
    page.get_by_role("button", name="Send").click()
    page.locator(".msg.ai").filter(has_text="Added Oat milk to Shopping").wait_for()

    # To-do: say it naturally, it gets sorted
    page.get_by_role("link", name="To-do").click()
    page.get_by_role("button", name="To-do", exact=True).click()
    page.locator("#todo-text").fill("the bath tap in the en suite is leaking, fix asap")
    page.get_by_role("button", name="Add", exact=True).click()
    page.locator("#todo-result").filter(has_text="Filed under Home maintenance").wait_for()
    assert "Urgent" in page.locator("#todo-result").inner_text()
    page.get_by_role("heading", name="Now (").wait_for()
    page.get_by_role("button", name="Mark done: Fix dripping bath tap").click()
    page.locator("#toast").filter(has_text="Done: Fix dripping bath tap").wait_for()
    page.get_by_label("Show").select_option(label="Quick jobs (15 min or less)")
    assert page.locator("#todo-groups .todo").count() >= 1
    page.get_by_label("Show").select_option(label="Everything")

    # Self-solving: the landing bulb is matched from home details; Mum's present asks one question first
    bulb = page.locator(".todo").filter(has_text="landing lightbulb")
    amazon = bulb.get_by_role("link", name="Find Philips LED bulb B22 bayonet, 8.5W LED, warm white (2700K) on Amazon (opens Amazon)")
    assert amazon.get_attribute("href").startswith("https://www.amazon.co.uk/s?k=Philips+LED+bulb+B22")
    mum = page.locator(".todo").filter(has_text="Mum's birthday present")
    mum.get_by_role("button", name="Gardening").click()
    # Gift memory: her wish list comes first, and last year's present isn't suggested again
    page.locator(".todo").filter(has_text="Mum's birthday present").filter(has_text="Hori hori garden knife").wait_for()
    assert "Burgon & Ball" not in page.locator(".todo").filter(has_text="Mum's birthday present").inner_text()
    assert any(d["item"] == "Mum: likes" for d in svc.store.home_details())  # remembered for next time
    page.locator(".todo").filter(has_text="Mum's birthday present").get_by_role("link", name=re.compile("Find Hori hori")).click()
    page.wait_for_timeout(300)
    for p in ctx.pages[1:]:
        p.close()  # the Amazon tab (it won't load in the test, which is fine)
    page.get_by_role("button", name="I've ordered Hori hori garden knife").click()
    page.locator("#toast").filter(has_text="Ordered").wait_for()
    assert page.locator(".todo").filter(has_text="Mum's birthday present").count() == 0
    from lifeos import profiles
    assert profiles.get(svc, "Mum")["given"][-1]["what"] == "Hori hori garden knife"

    # Projects: plan one and tick the next step
    page.get_by_role("button", name="Projects").click()
    page.get_by_label("Start a project").fill("Sort out the garage")
    page.get_by_role("button", name="Plan the steps").click()
    page.get_by_role("article", name="Sort out the garage").wait_for()
    card = page.get_by_role("article", name="Redecorate the hallway")
    assert "1 of 6 done" in card.inner_text()
    card.get_by_role("button", name="Mark done: Measure up and list what's needed").click()
    page.get_by_role("article", name="Redecorate the hallway").filter(has_text="2 of 6 done").wait_for()

    # Lists: the item from Ask is on Shopping; tick one off
    page.get_by_role("button", name="Lists", exact=True).click()
    page.get_by_role("button", name="Mark done: Oat milk").wait_for()
    page.get_by_role("button", name="Mark done: Milk", exact=True).click()
    page.locator("#toast").filter(has_text="Done: Milk").wait_for()
    assert page.get_by_role("button", name="Mark done: Milk", exact=True).count() == 0

    # Dinners: change Wednesday and save
    page.get_by_role("button", name="Dinners").click()
    page.locator("#meal-1").fill("Shepherd's pie")
    page.get_by_role("button", name="Save dinners").click()
    page.locator("#toast").filter(has_text="Dinners saved").wait_for()

    # Renewals: add one
    page.get_by_role("button", name="Renewals").click()
    page.get_by_label("What renews?").fill("TV licence")
    page.get_by_label("Renewal or expiry date").fill("2027-06-30")
    page.get_by_role("button", name="Add renewal").click()
    page.get_by_role("button", name="Remove TV licence").wait_for()

    # Home: a quick action and a chore
    page.get_by_role("link", name="Home").click()
    page.get_by_role("button", name="Vacuum now").click()
    page.locator("#toast").filter(has_text="Done: Vacuum now").wait_for()
    page.get_by_role("button", name="Leo: Bins out done, 50p").click()
    page.locator(".kid").filter(has_text="Leo").filter(has_text="80p owed").wait_for()

    # Settings: bigger text sticks
    page.get_by_role("link", name="Settings").click()
    page.get_by_label("Extra large").check()
    assert "text-xl" in page.evaluate("document.documentElement.className")

    assert errors == []
    assert svc.ha.devices["vacuum.robot"][1] == "cleaning"
    assert any(r["name"] == "TV licence" for r in svc.store.renewals())
    ctx.close()


def test_get_an_electrician_end_to_end(base_url, browser):
    """Broken socket -> go-to electrician offered -> email with free times -> reply spotted -> booked."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, url)
    page.get_by_role("link", name="To-do").click()
    card = page.locator(".todo").filter(has_text="Kitchen socket")
    card.get_by_role("radio", name=re.compile("Spark Electrical")).wait_for()
    assert card.get_by_role("radio", name=re.compile("Spark Electrical")).is_checked()  # the go-to comes first
    assert card.get_by_text("Ready in Gmail:").is_visible()  # the same email already waits in Gmail drafts
    body = card.get_by_label("Message (edit anything)")
    assert body.input_value().startswith("Hello Dave,")
    first_time = re.search(r"^- (\w+day) [^,]+, (\S+?) to ", body.input_value(), re.M)  # free times are offered
    assert first_time
    body.fill(body.input_value() + "\n\nP.S. It's the double socket left of the kettle.")  # edits are sent as written
    card.get_by_role("button", name=re.compile("Send email to Spark Electrical")).click()  # confirm() is accepted
    page.locator(".todo").filter(has_text="Kitchen socket").get_by_text("Waiting for a reply", exact=True).wait_for()
    assert svc.google.people["me"].drafts == {}  # sent from the app, so the Gmail draft was tidied away
    sent = next(iter(svc.google.people["me"].sent_threads.values()))
    assert sent["to"] == "jobs@spark-electrical.example" and "P.S. It's the double socket" in sent["body"]
    # A reminder is shown in full before anything is sent
    card = page.locator(".todo").filter(has_text="Kitchen socket")
    card.get_by_role("button", name="Send a reminder").click()
    assert "Just following up" in card.get_by_label("Reminder to Dave (edit anything)").input_value()
    card.get_by_role("button", name="Not now").click()
    assert sent["count"] == 1
    # The reply arrives; the app shows it with a booking form pre-filled with the time they agreed to
    for _ in range(30):
        page.wait_for_timeout(500)
        if svc.store.requests(["replied"]):
            break
    page.reload()
    card = page.locator(".todo").filter(has_text="Kitchen socket")
    card.get_by_text("They replied", exact=True).wait_for()
    hour = int(re.match(r"\d+", first_time.group(2)).group()) % 12 + (12 if "pm" in first_time.group(2) else 0)
    assert card.get_by_label("Time").input_value().startswith(f"{hour:02d}:")  # pre-filled with the time they agreed
    assert card.get_by_label(re.compile("^Also email Dave to confirm")).is_checked()
    assert "is great, thank you" in card.get_by_label("Confirmation email (edit anything)").input_value()
    card.get_by_role("button", name="Add to calendar").click()
    page.locator(".todo").filter(has_text="Kitchen socket").get_by_text("Appointment:").wait_for()
    assert any(e["summary"].startswith("Electrician: Spark Electrical") for e in svc.google.get().events(days=60))
    assert sent["count"] == 2  # the confirmation went in the same thread
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0  # nothing spills sideways

    # Find time for a do-it-yourself job puts it in the calendar
    page.get_by_role("button", name=re.compile("^Find time to do:")).first.click()
    page.locator(".find-time .chip").first.click()
    page.locator("#toast").filter(has_text="In your calendar").wait_for()

    # Home: save a new tradesperson and make them the go-to
    page.get_by_role("link", name="Home").click()
    page.get_by_text("Add a tradesperson").click()
    page.locator("#ct-name").fill("Lee")
    page.locator("#ct-company").fill("Bright Sparks")
    page.locator("#ct-trade").fill("electrician")
    page.locator("#ct-email").fill("lee@brightsparks.example")
    page.get_by_role("button", name="Save tradesperson").click()
    page.get_by_role("button", name="Make Bright Sparks (Lee) your go-to electrician").click()
    page.locator("#toast").filter(has_text="now your go-to electrician").wait_for()
    assert [c["company"] for c in svc.store.contacts("electrician") if c["favourite"]] == ["Bright Sparks"]
    assert errors == []
    ctx.close()


def test_everyday_shortcuts(base_url, browser):
    """Quick add from anywhere, open it, search, snooze, change, undo a tick, plan the week, shopping mode, checklist."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, url)
    # Today: Next up with its action buttons
    page.locator("#nextup-list li").first.wait_for()
    assert page.locator("#nextup-list .nextup-go").count() >= 1
    # Quick add from the Today screen
    page.get_by_role("button", name="Add a job").click()
    dialog = page.locator("#add-dialog")
    dialog.get_by_label("What needs doing?").fill("Clear the gutters before winter")
    dialog.get_by_role("button", name="Add", exact=True).click()
    dialog.get_by_role("link", name="Open it").click()
    job = page.locator("#todo-groups li.highlight")
    job.wait_for()
    assert "Clear the gutters" in job.inner_text()
    assert page.evaluate("document.activeElement.classList.contains('highlight')")  # focus moved to it
    # Search narrows the list
    page.get_by_label("Search").fill("gutters winter")  # every word must match
    page.locator("#todo-count").filter(has_text="Showing 1 of").wait_for()
    item = page.locator("#todo-groups li.todo").filter(has_text="Clear the gutters")
    # Snooze to tomorrow
    item.get_by_label("More options: Clear the gutters before winter").click()
    item.get_by_role("button", name="Snooze until tomorrow: Clear the gutters before winter").click()
    page.locator("#toast").filter(has_text="Moved to").wait_for()
    # Change its name and importance
    item = page.locator("#todo-groups li.todo").filter(has_text="Clear the gutters")
    item.get_by_label("More options: Clear the gutters before winter").click()
    item.get_by_role("button", name="Change: Clear the gutters before winter").click()
    edit = page.locator("#edit-dialog")
    edit.get_by_label("Name").fill("Clear the front gutters")
    edit.get_by_label("Importance").select_option("2")
    edit.get_by_role("button", name="Save").click()
    page.locator("#toast").filter(has_text="Saved: Clear the front gutters").wait_for()
    page.get_by_label("Search").fill("front gutters")
    # Tick, then Undo: nothing is completed in Google
    item = page.locator("#todo-groups li.todo").filter(has_text="Clear the front gutters")
    item.get_by_role("button", name="Mark done: Clear the front gutters").click()
    page.locator("#toast").get_by_role("button", name="Undo").click()
    page.locator("#toast").filter(has_text="Kept: Clear the front gutters").wait_for()
    assert item.is_visible()
    page.wait_for_timeout(500)
    assert any(t["title"] == "Clear the front gutters" for t in svc.google.get().list_tasks("Home maintenance"))
    # Plan my week
    page.get_by_label("Search").fill("")
    page.get_by_role("button", name="Plan my week").click()
    plan = page.locator("#plan-dialog")
    plan.locator(".plan-item").first.wait_for()
    n = plan.locator(".plan-item input:checked").count()
    plan.get_by_role("button", name="Add to calendar").click()
    page.locator("#toast").filter(has_text=f"{n} job").wait_for()
    # Shopping mode
    page.get_by_role("button", name="Lists").click()
    page.get_by_role("button", name="Shopping mode: big ticks, screen stays on").click()
    assert page.evaluate("document.body.classList.contains('shop-mode')")
    page.get_by_role("button", name="Finished shopping").click()
    assert not page.evaluate("document.body.classList.contains('shop-mode')")
    # Settings: the Google checklist with the address to copy
    page.get_by_role("link", name="Settings").click()
    page.locator("#google-checklist li").first.wait_for()
    assert "/auth/google/callback" in page.locator("#google-checklist code").inner_text()
    assert errors == []
    ctx.close()


def test_predictions_ordering_sharing_offline_and_leaving(base_url, browser):
    """v0.14: the Predicted tab, ordering online, sharing into Life OS, adding while offline, the leaving check."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, url)
    # Predicted: seen coming from what was bought before; add one to Shopping
    page.goto(f"{url}/app/#tasks?sec=predicted")
    first = page.locator("#pred-groups .prediction").filter(has=page.get_by_role("button", name=re.compile("^Add to Shopping: "))).first
    first.wait_for()
    title = first.locator("strong").first.inner_text()
    first.get_by_role("button", name=f"Add to Shopping: {title}").click()
    page.locator("#toast").filter(has_text=f"Added: {title}").wait_for()
    assert any(t["title"].lower() == title.lower() for t in svc.google.get().list_tasks("Shopping"))
    # Lists: order online. Items with a usual Amazon product go in one basket link; the rest get search links
    page.get_by_role("button", name="Lists", exact=True).click()
    page.locator("#shop-order").wait_for()
    page.locator("#shop-other a").first.wait_for()
    assert "tesco.com" in page.locator("#shop-other a").first.get_attribute("href")
    page.get_by_label("Your supermarket").select_option("sainsburys")
    page.locator("#shop-other a[href*='sainsburys.co.uk']").first.wait_for()
    # Shared from another app (Android share sheet): Add opens with it filled in, link only once
    page.goto(f"{url}/app/?share_title=Noisy%20fan&share_text=Bathroom%20extractor%20fan%20is%20noisy%20"
              "https%3A%2F%2Fexample.com%2Ffan&share_url=https%3A%2F%2Fexample.com%2Ffan")
    add = page.locator("#add-dialog")
    add.wait_for()
    text = add.get_by_label("What needs doing?").input_value()
    assert "Bathroom extractor fan is noisy" in text and text.count("https://example.com/fan") == 1
    assert "share_text" not in page.url
    add.get_by_role("button", name="Close").click()
    # Offline: a job added on the train waits on the phone, then goes through
    ctx.set_offline(True)
    page.get_by_role("button", name="Add a job").click()
    add.get_by_label("What needs doing?").fill("Book the chimney sweep")
    add.get_by_role("button", name="Add", exact=True).click()
    add.get_by_text("Saved offline").wait_for()
    add.get_by_role("button", name="Close").click()
    page.locator("#outbox-banner").filter(has_text="1 change").wait_for()
    ctx.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.locator("#outbox-banner").wait_for(state="hidden")
    assert any("chimney" in t["title"].lower() for g in svc.google.people.values() for t in g.tasks)
    # Leaving home check: lights off and heating down in a tap; nothing ever unlocked
    page.get_by_role("link", name="Home").click()
    page.get_by_role("button", name="Check before I go").click()
    page.get_by_role("button", name=re.compile("^Turn off: Kitchen")).click()
    page.locator("#toast").filter(has_text="Kitchen lights: turn off").wait_for()
    assert not any(c[0].startswith(("lock.unlock", "cover.open")) for c in getattr(svc.ha, "calls", []))
    assert errors == []
    ctx.close()


def test_installable_and_opens_offline(base_url, browser):
    url, _ = base_url
    ctx, page = _phone(browser)
    _sign_in(page, url, "Sue")
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller !== null || "
                           "navigator.serviceWorker.ready.then(() => true)")
    page.reload()
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10000)
    cdp = ctx.new_cdp_session(page)
    errors = cdp.send("Page.getInstallabilityErrors").get("installabilityErrors", [])
    # Test browsers run incognito, which can never install apps; anything else would be a real problem.
    errors = [e for e in errors if e["errorId"] != "in-incognito"]
    assert errors == [], errors  # Chrome considers it an installable app
    manifest = cdp.send("Page.getAppManifest")
    assert not manifest.get("errors")
    # Offline: the app still opens and explains, rather than showing Chrome's dinosaur
    ctx.set_offline(True)
    page.reload()
    page.get_by_role("heading", level=1).wait_for()
    assert "Life OS" in page.title()
    ctx.set_offline(False)
    ctx.close()


def test_kitchen_display(base_url, browser):
    url, _ = base_url
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-GB")
    page = ctx.new_page()
    page.goto(f"{url}/app/wall.html")
    page.get_by_label("Household key").fill("demo")
    page.get_by_role("button", name="Start display").click()
    page.locator("#w-dinner").filter(has_text="Chicken fajitas").wait_for()
    assert page.locator("#w-tomorrow li").count() >= 1  # bin collection + dentist
    assert "9°C" in page.locator("#weather").inner_text()
    # the display's session can't use anything else
    status = page.evaluate("fetch('/api/today').then(r => r.status)")
    assert status == 401
    ctx.close()


def test_v015_money_childcare_manual_energy_and_dinners(base_url, browser):
    """v0.15 screens: subscriptions and spending, childcare gaps, the home manual, cheapest time, dinner ideas."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, url)
    # Money: subscriptions with keep/cancel, spending by category
    page.goto(f"{url}/app/#tasks?sec=money")
    page.locator("#subs-list li").first.wait_for()
    assert "a month" in page.locator("#subs-total").inner_text()
    netflix = page.locator("#subs-list li").filter(has_text="Netflix")
    assert "up from £10.99" in netflix.inner_text().lower()
    netflix.get_by_role("button", name="Cancel it: Netflix").click()
    page.locator("#toast").filter(has_text='Added "Cancel Netflix" to your to-dos').wait_for()
    assert page.locator("#spend-cats li").count() >= 2
    # Week: childcare gap sorted in one tap, then gone
    page.goto(f"{url}/app/#tasks?sec=week")
    gap = page.locator("#cc-gaps li.cc-gap").first
    gap.wait_for()
    label = gap.locator("strong").inner_text()
    gap.get_by_label(re.compile("Who's covering")).select_option("Grandparents")
    gap.get_by_role("button", name="It's sorted").click()
    page.locator("#toast").filter(has_text=f"Sorted: {label}").wait_for()
    page.wait_for_function(f"![...document.querySelectorAll('#cc-gaps strong')].some(s => s.textContent === {label!r})")
    # Dinners: suggestions fill the gaps with a reason
    page.goto(f"{url}/app/#tasks?sec=meals")
    page.get_by_role("button", name="Suggest dinners for the gaps").click()
    page.locator("#suggest-result").filter(has_text="Suggested").wait_for()
    assert page.locator("#meal-fields .why").count() >= 1
    # Home: cheapest time, the manual (a private Wi-Fi entry stays off the kitchen display)
    page.get_by_role("link", name="Home").click()
    page.locator("#energy-list li").first.wait_for()
    page.get_by_role("button", name=re.compile("^Switch Dishwasher on at")).click()
    page.locator("#toast").filter(has_text="Dishwasher switches on at").wait_for()
    assert any(r.get("status") == "pending" for r in svc.store.records("energy_run"))
    page.locator("#manual-list li").filter(has_text="Stopcock").wait_for()
    assert "private" in page.locator("#manual-list li").filter(has_text="Wi-Fi").inner_text()
    page.locator("#manual-add summary").click()
    page.get_by_label("What it is", exact=True).fill("Gas meter")
    page.get_by_label("Where it is, or what to do").fill("Outside by the back door, key on the hook")
    page.locator("#manual-form").get_by_role("button", name="Save").click()
    page.locator("#manual-list li").filter(has_text="Gas meter").wait_for()
    assert any(t["title"] == "Gas meter" for t in svc.google.get().list_tasks("Home manual"))
    # Gift memory
    page.locator("#gifts-list li").filter(has_text="Mum").wait_for()
    assert "Hori hori garden knife" in page.locator("#gifts-list li").filter(has_text="Mum").inner_text()
    # Settings: version, and an update file can't be installed into the sample household
    page.get_by_role("link", name="Settings").click()
    page.locator("#sys-version").filter(has_text="Life OS").wait_for()
    assert "sample household" in page.locator("#sys-version").inner_text()
    assert errors == []
    ctx.close()


def test_v016_bills_trips_school_run_admin_hour_goals_and_setup(base_url, browser):
    """v0.16 screens: bills with a price rise, an attachment, a trip, the school run, the admin hour, saving goals,
    the weekly energy report and the full setup list."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, url)
    # Money: bills, with the rise flagged, then add one
    page.goto(f"{url}/app/#tasks?sec=money")
    octopus = page.locator("#bills-list li").filter(has_text="Octopus Energy")
    octopus.wait_for()
    assert "up 12% from £112.00" in octopus.inner_text().lower()
    page.locator("#bills-title + p + ul + details summary").click()
    page.get_by_label("Who to").fill("Aviva Home")
    page.get_by_label("What for").select_option("insurance")
    page.get_by_label("How much (£)").last.fill("24.50")
    page.locator("#bill-form").get_by_role("button", name="Add bill").click()
    page.locator("#bills-list li").filter(has_text="Aviva Home").wait_for()
    # a photo of the bill, kept on the home computer
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    page.locator("#bills-list li").filter(has_text="Octopus Energy").locator("input[type=file]").set_input_files(
        {"name": "octopus-bill.png", "mimeType": "image/png", "buffer": png})
    page.get_by_role("link", name="Open octopus-bill.png").wait_for()
    # Week: life admin hour booked, school run claimed
    page.goto(f"{url}/app/#tasks?sec=week")
    page.locator("#admin-jobs li").first.wait_for()
    page.get_by_role("button", name="Book it").click()
    page.locator("#toast").filter(has_text="is in the calendar").wait_for()
    assert any(e["summary"].startswith("Life admin hour") for e in svc.google.get()._events)
    claim = page.locator("#run-days button", has_text="Claim it").first
    claim.wait_for()
    label = claim.get_attribute("aria-label")
    claim.click()
    page.locator("#toast").filter(has_text=label.split(" does ")[0]).wait_for()
    assert any(e["summary"].startswith("School run:") for e in svc.google.get()._events)
    # Projects: the trip with its packing list, then plan another
    page.goto(f"{url}/app/#tasks?sec=projects")
    page.locator("#trips-list li").filter(has_text="Cornwall").wait_for()
    assert "Packed 0 of" in page.locator("#trips-list li").first.inner_text()
    page.locator("#trip-add summary").click()
    page.get_by_label("Where to?").fill("Malaga")
    start = (date.today() + timedelta(days=60))
    page.get_by_label("Going", exact=True).fill(start.isoformat())
    page.get_by_label("Coming home").fill((start + timedelta(days=7)).isoformat())
    page.get_by_label("Getting there").select_option("plane")
    page.get_by_label("Abroad").check()
    page.locator("#trip-form").get_by_role("button", name="Make the plan").click()
    page.locator("#trips-list li").filter(has_text="Malaga").wait_for()
    assert "Passports" in [t["title"] for t in svc.google.get().list_tasks("Packing: Malaga")]
    # Home: energy report and saving goals
    page.get_by_role("link", name="Home").click()
    page.locator("#report-list li").filter(has_text="Electricity").wait_for()
    assert page.locator("#report-tip").inner_text()
    ava = page.locator(".kid").filter(has_text="Ava")
    ava.locator(".goal").filter(has_text="Lego treehouse").wait_for()
    ava.get_by_role("button", name=re.compile("^Put .* towards it$")).click()
    page.locator("#toast").filter(has_text="saved towards Lego treehouse").wait_for()
    leo = page.locator(".kid").filter(has_text="Leo")
    leo.locator("summary").filter(has_text="Pocket money and saving for Leo").click()
    leo.get_by_label("Saving up for").fill("Scooter")
    leo.get_by_label("It costs (£)").fill("40")
    leo.get_by_role("button", name="Add saving goal").click()
    leo.locator(".goal").filter(has_text="Scooter").wait_for()
    # Settings: the full setup list
    page.goto(f"{url}/app/#settings?sec=setup-all")
    page.locator("#setup-all-groups li").first.wait_for()
    assert "of 16 done" in page.locator("#setup-all-count").inner_text()
    assert page.locator("#setup-all-groups h3").count() == 3
    assert errors == []
    ctx.close()

def test_v017_check_haggle_recipe_party_away_agenda_seasonal(base_url, browser):
    """v0.17 screens: check everything, haggling, a recipe link, a party, away mode, the Sunday agenda, seasonal jobs."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, url)
    # Settings: check everything
    page.goto(f"{url}/app/#settings?sec=check")
    page.get_by_role("button", name="Check everything").click()
    page.locator("#check-results li").first.wait_for()
    assert page.locator("#check-summary").inner_text()
    assert "sample household" in page.locator("#check-results").inner_text()
    # Money: haggle the energy bill that went up
    page.goto(f"{url}/app/#tasks?sec=money")
    page.get_by_role("button", name="Ask Octopus Energy for a better price").click()
    page.locator("#haggle-body").filter(has_text="gone up from £112.00 to £126.00").wait_for()
    assert "uswitch" in page.locator("#haggle-body a").first.get_attribute("href")
    page.get_by_role("button", name="Add a to-do to ring them").click()
    page.locator("#toast").filter(has_text="Ring Octopus Energy for a better price").wait_for()
    page.locator("#haggle-dialog").get_by_role("button", name="Close").click()
    # Renewals: car insurance quote
    page.goto(f"{url}/app/#tasks?sec=renewals")
    page.get_by_role("button", name="Ask for a better price on Car insurance").click()
    page.get_by_label("Their price for next year (£)").fill("640")
    page.get_by_label("What you paid last year (£, if you know)").fill("512")
    page.get_by_role("button", name="Show me what to say").click()
    page.locator("#haggle-body").filter(has_text="25% more").wait_for()
    page.locator("#haggle-dialog").get_by_role("button", name="Close").click()
    # Dinners: a recipe link
    page.goto(f"{url}/app/#tasks?sec=meals")
    page.get_by_label("Recipe link").fill("https://www.example.com/chicken-chorizo-rice")
    page.locator("#recipe-form").get_by_role("button", name="Add it").click()
    page.locator("#recipe-result").filter(has_text="Added to Shopping").wait_for()
    assert "chorizo" in page.locator("#recipe-result").inner_text()
    # Projects: the party, a reply, the invites; away mode on the trip
    page.goto(f"{url}/app/#tasks?sec=projects")
    party = page.locator("#parties-list li.party").first
    party.wait_for()
    assert "2 coming" in party.inner_text()
    party.get_by_role("button", name="Isla: Coming").click()
    page.locator("#parties-list li.party").first.filter(has_text="3 coming").wait_for()
    page.get_by_role("button", name="Make the invites for Ava's 8th").click()
    page.locator("#toast").filter(has_text="in your Gmail drafts").wait_for()
    page.locator("#trips-list").get_by_label("Away mode while we're gone").first.check()
    page.locator("#toast").filter(has_text="Away mode is on").wait_for()
    assert any(t.get("away") for t in svc.store.records("trip"))
    # Week: the Sunday agenda
    page.goto(f"{url}/app/#tasks?sec=week")
    page.get_by_role("button", name="Sunday agenda").click()
    page.locator("#agenda-body h3").first.wait_for()
    assert "Decisions to make" in page.locator("#agenda-body").inner_text()
    page.locator("#agenda-dialog").get_by_role("button", name="Close").click()
    # Home: seasonal jobs
    page.get_by_role("link", name="Home").click()
    page.locator("#seasonal-now li").first.wait_for()
    assert errors == []
    ctx.close()


def test_v017_kids_tablet(base_url, browser):
    url, svc = base_url
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-GB")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{url}/app/kids.html")
    page.get_by_label("Household key (a grown-up types this once)").fill("demo")
    page.get_by_role("button", name="Start").click()
    page.get_by_role("button", name="Leo").click()
    page.get_by_role("button", name=re.compile("^I did Feed the pet")).click()
    page.locator("#kids-cheer").filter(has_text="Feed the pet").wait_for()
    page.get_by_role("button", name="Undo Feed the pet").wait_for()
    # still only a tablet: no adult screens
    assert page.evaluate("fetch('/api/today').then(r => r.status)") == 401
    assert page.evaluate("fetch('/api/chores/paid', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                         " body: JSON.stringify({kid: 'Leo'})}).then(r => r.status)") == 401
    page.get_by_role("link", name="Back to the display").click()
    page.locator("#w-dinner").wait_for()
    assert errors == []
    ctx.close()

def test_first_run_setup_page(tmp_path, browser, monkeypatch):
    """The setup page a new download opens: names, key shown once, bringing an old install across offered."""
    import uvicorn

    from lifeos.setup_web import create_setup_app
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path / "home"))
    for k in ("LIFEOS_API_KEY", "LIFEOS_SIGNING_SECRET"):
        monkeypatch.delenv(k, raising=False)
    port = _free_port()
    starts = []

    class Ctl:
        def request_restart(self, extra=None, drop=()):
            starts.append(extra)
    server = uvicorn.Server(uvicorn.Config(create_setup_app(Ctl()), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    ctx = browser.new_context(viewport={"width": 1100, "height": 900}, locale="en-GB")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/")
    page.get_by_role("heading", name="Welcome to Life OS").wait_for()
    page.get_by_role("button", name="Finish setting up").click()
    page.get_by_role("alert").filter(has_text="Type your first name").wait_for()
    page.get_by_label("Your first name").fill("Joe")
    page.get_by_label("The other adult's first name").fill("Sue")
    page.get_by_label("Children's first names").fill("Ava, Leo")
    page.get_by_role("button", name="Finish setting up").click()
    page.get_by_role("heading", name="You're set up").wait_for()
    key = page.locator("#key-show").inner_text()
    from lifeos import setup_core
    assert setup_core.read_env()["LIFEOS_API_KEY"] == key and setup_core.household()["kids"] == ["Ava", "Leo"]
    page.get_by_role("button", name="Open Life OS").click()
    for _ in range(50):
        if starts:
            break
        time.sleep(0.1)
    assert starts == [[]]
    assert errors == []
    ctx.close()
    server.should_exit = True


def test_v018_parcels_bins_rota_study_date_sitter_cars_livetest(base_url, browser):
    """v0.18 screens on a phone: parcels and bins on Today, the Home blocks, car running costs, the live test,
    and the printable babysitter sheet."""
    url, svc = base_url
    ctx, page = _phone(browser)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    _sign_in(page, url)
    page.locator("#parcels-list li").first.wait_for()
    assert "Lego treehouse" in page.locator("#parcels-list").inner_text()
    page.get_by_role("button", name="Got it: Lego treehouse").click()
    page.locator("#parcels-list").filter(has_not_text="Lego treehouse").wait_for()
    # Home: bins, cleaning rota (tick one), homework, date night, sitter
    page.goto(f"{url}/app/#home")
    page.locator("#clean-list li").first.wait_for()
    assert page.locator("#bins-next").inner_text()
    first = page.locator("#clean-list li").first.get_by_role("button", name=re.compile("^Done: "))
    job = first.get_attribute("aria-label").removeprefix("Done: ")
    first.click()
    page.get_by_role("button", name=f"Undo: {job}").wait_for()
    page.get_by_text("Add homework").click()
    page.get_by_label("What's the homework?", exact=True).fill("Read chapter 3")
    page.get_by_role("button", name="Save homework").click()
    page.locator("#study-homework").filter(has_text="Read chapter 3").wait_for()
    page.wait_for_function("document.querySelector('#date-message').value.includes('are you free to babysit')")
    page.get_by_role("button", name="Put it in the calendar").click()
    page.locator("#toast").filter(has_text="Date night is in the calendar").wait_for()
    # Cars: running costs
    page.goto(f"{url}/app/#tasks?sec=renewals")
    page.get_by_role("button", name="Running costs: The Golf").click()
    page.locator("#carcost-body .lead").filter(has_text="Last 12 months").wait_for()
    page.get_by_label("Kind of cost").select_option("parking")
    page.get_by_label("Cost (£)").fill("3.20")
    page.get_by_role("button", name="Add cost").click()
    page.locator("#toast").filter(has_text="Cost added").wait_for()
    page.get_by_role("button", name="Close").last.click()
    # Settings: the live test sends nothing
    page.goto(f"{url}/app/#settings?sec=check")
    page.locator("#livetest summary").click()
    page.get_by_role("button", name="Run the live test").click()
    page.locator("#live-summary").filter(has_text="Nothing was sent").wait_for()
    # the babysitter sheet
    page.goto(f"{url}/app/sitter.html")
    page.get_by_role("heading", name="While we're out").wait_for()
    assert "999" in page.locator("#sheet-emergency").inner_text()
    assert not errors, errors
    ctx.close()


def test_v018_kids_tablet_reading_homework_stars(base_url, browser):
    url, svc = base_url
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-GB")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{url}/app/kids.html")
    page.get_by_label("Household key (a grown-up types this once)").fill("demo")
    page.get_by_role("button", name="Start").click()
    page.get_by_role("button", name="Reading", exact=True).click()
    page.get_by_role("button", name="Ava").click()
    page.get_by_role("button", name="I read for 20 minutes").click()
    page.locator("#kids-cheer").filter(has_text="20 minutes of reading").wait_for()
    page.get_by_role("button", name="Undo 20 minutes reading").wait_for()
    page.get_by_role("button", name="Homework", exact=True).click()
    page.get_by_role("button", name=re.compile("^I've done: Times tables")).click()
    page.get_by_role("button", name=re.compile("^Undo: Times tables")).wait_for()
    page.get_by_role("button", name="Stars", exact=True).click()
    page.get_by_role("heading", name=re.compile("^Ava: \\d+ of 15 stars")).wait_for()
    assert "null" not in page.locator("#kid-panel").inner_text()
    # still only a tablet: it can't add homework or reach adult screens
    assert page.evaluate("fetch('/api/kids/homework', {method:'POST', headers:{'Content-Type':'application/json'}, "
                         "body: JSON.stringify({kid:'Ava', title:'x'})}).then(r => r.status)") == 401
    assert not errors, errors
    ctx.close()
