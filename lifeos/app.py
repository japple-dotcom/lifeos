"""HTTP entry points.

The phone app (an installable web app under /app) uses the /api routes with a signed session cookie.
Scripts, Home Assistant and NFC tags use /ask, /nfc and /loops with the X-LifeOS-Key header.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

import hmac
from html import escape

from fastapi import Body, Cookie, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import approvals, oauth, sessions
from .accounts import GoogleAccounts
from .agent import Agent
from . import (activity, adminhour, agenda, aierrors, away, bills, bins, bridge, cars, childcare, christmas, deliveries, doctor,
               energy, files, guests, haggle, household, kidsmode, kidstuff, leaving, manual, money, offsite, party, profiles,
               recipes, scheduling, schoolkit, schoolrun, seasonal, shop, solver, todos, trades, trips, voice)
from . import checklist as checklist_mod
from .config import Settings, load_settings, setup_problems
from .scheduler import run_loop, start_scheduler
from .store import Store
from .tags import handle_tag
from .tools import Services, run_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("lifeos.app")
logging.getLogger("apscheduler").setLevel(logging.WARNING)  # keep the log readable
WEB_DIR = Path(__file__).resolve().parent.parent / "web"  # = paths.web_dir()
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
DISPLAY = "display"  # the read-only kitchen wall display: can view /api/wall and nothing else
KIDS = "kids"  # the kitchen tablet in kids' mode: the wall display, plus ticking chores (never paying, never anything else)


def build_services(settings: Settings) -> Services:
    from .integrations.homeassistant import HomeAssistant
    from .integrations.notify import Notifier

    ha = HomeAssistant(settings.ha_url, settings.ha_token) if settings.ha_url and settings.ha_token else None
    from .integrations.webpush import FanOut, WebPusher

    store = Store(settings.db_path)
    ntfy = Notifier(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token) if settings.ntfy_topic else None
    pusher, push_problem = None, ""
    try:
        key_file = str(Path(settings.google_token_file).parent / "vapid_private.pem")
        pusher = WebPusher(store, key_file, settings.push_contact)
    except Exception as e:
        push_problem = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        log.warning("In-app notifications unavailable: %s", push_problem, exc_info=True)
    channels = FanOut(pusher, ntfy)
    svc = Services(settings=settings, store=store, ha=ha, google=GoogleAccounts(settings), pusher=pusher,
                   push_problem=push_problem)
    svc.notifier = household.QuietHours(channels, svc) if channels else None
    return svc


class AskBody(BaseModel):
    text: str
    deep: bool = False


class ProviderBody(BaseModel):
    provider: str


class LoginBody(BaseModel):
    key: str = ""
    person: str = ""
    join: str = ""   # a one-time code from the QR on this computer's household key page


class TaskBody(BaseModel):
    list_name: str
    title: str = ""
    due: str | None = None
    task_id: str = ""
    person: str | None = None


class RenewalBody(BaseModel):
    name: str
    due_date: str
    category: str = ""
    notes: str = ""
    repeat_months: int = 12


class ChoreBody(BaseModel):
    kid: str
    chore_id: str = ""


class SubscriptionBody(BaseModel):
    endpoint: str
    keys: dict


class TodoBody(BaseModel):
    text: str


class TodoDoneBody(BaseModel):
    list_name: str
    task_id: str


class SnoozeBody(TodoDoneBody):
    until: str  # tomorrow | weekend | next_week | YYYY-MM-DD


class EditBody(TodoDoneBody):
    title: str | None = None
    due: str | None = None        # YYYY-MM-DD, or "" to clear
    category: str | None = None
    who: str | None = None
    priority: int | None = None
    next_action: str | None = None


class PlanItem(BaseModel):
    list_name: str
    task_id: str
    start: str
    duration_min: int = 30


class PlanBody(BaseModel):
    items: list[PlanItem]


class PredictionAct(BaseModel):
    pid: str
    action: str                 # add | snooze | dismiss | never | last
    days: int = 7               # snooze length
    last: str = ""              # YYYY-MM-DD: when a check-up was last done


class OrderedBody(BaseModel):
    task_ids: list[str]
    where: str = "online"


class ProductBody(BaseModel):
    item: str
    link: str                   # Amazon product link or ASIN
    title: str = ""


class SolveBody(BaseModel):
    list_name: str
    task_id: str
    hint: str = ""
    answer: str = ""
    option: int = 0


class ContactBody(BaseModel):
    name: str
    trade: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    notes: str = ""
    favourite: bool = False


class ArrangeBody(BaseModel):
    list_name: str
    task_id: str
    contact: int = 0          # index into the suggestion's contacts
    contacts: list[int] = []  # several, to ask for quotes: each gets the same email, greeted by name
    subject: str
    body: str


class BookBody(BaseModel):
    start: str                # ISO date-time, local
    duration_min: int = 120
    confirm_body: str = ""    # shown to the person and ticked to send: confirms the time in the same thread


class ReminderBody(BaseModel):
    subject: str = ""
    body: str = ""


class ScheduleBody(BaseModel):
    list_name: str
    task_id: str
    start: str
    duration_min: int = 60


class HomeDetailBody(BaseModel):
    area: str
    item: str
    details: str


class ProjectBody(BaseModel):
    name: str
    details: str = ""


class MealBody(BaseModel):
    day: str
    dinner: str = ""


class MealPlanBody(BaseModel):
    meals: list[MealBody]


class LoginLimiter:
    """At most 5 wrong household keys per address per 15 minutes."""

    def __init__(self, max_failures: int = 5, window_s: int = 900):
        self.max, self.window = max_failures, window_s
        self.failures: dict[str, deque] = defaultdict(deque)

    def blocked(self, who: str) -> bool:
        q = self.failures[who]
        while q and time.time() - q[0] > self.window:
            q.popleft()
        return len(q) >= self.max

    def fail(self, who: str) -> None:
        self.failures[who].append(time.time())

    def reset(self, who: str) -> None:
        self.failures.pop(who, None)


def create_app(svc: Services | None = None, agent: Agent | None = None, run_scheduler: bool = True) -> FastAPI:
    svc = svc or build_services(load_settings())
    agent = agent or Agent(svc)
    svc.agent = agent
    s = svc.settings
    problems = setup_problems(s)
    for prob in problems:
        log.warning("SETUP: %s", prob)
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched = start_scheduler(agent) if run_scheduler and s.loops else None
        yield
        if sched:
            sched.shutdown(wait=False)

    from . import __version__
    app = FastAPI(title="Life OS", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def always_fresh_app(request: Request, call_next):
        """Phones and browsers must check for a newer app every time, never show an old saved copy."""
        response = await call_next(request)
        if request.url.path.startswith("/app"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        """The app's own pages are the only website allowed to make changes through /api."""
        if request.method != "GET" and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in {s.public_url, f"{request.url.scheme}://{request.url.netloc}"}:
                return JSONResponse({"detail": "Request from another website blocked."}, status_code=403)
        return await call_next(request)

    def people() -> list[str]:
        return list(s.household.get("adults") or ["me"])

    def name_of(person: str) -> str:
        return (s.household.get("names") or {}).get(person, person.title())

    # ---------- auth ----------
    def current_viewer(lifeos_session: str | None = Cookie(default=None),
                       x_lifeos_key: str | None = Header(default=None)) -> str:
        """Adults, or the read-only kitchen display."""
        who = sessions.read(s.signing_secret, lifeos_session)
        if who in (DISPLAY, KIDS) or (who and who in people()):
            return who
        if s.api_key and x_lifeos_key == s.api_key:
            return DISPLAY
        raise HTTPException(status_code=401, detail="Please sign in")

    def require_key(x_lifeos_key: str | None = Header(default=None), key: str | None = Query(default=None)):
        if not s.api_key or (x_lifeos_key or key) != s.api_key:
            raise HTTPException(status_code=401, detail="bad key")

    def current_kids(lifeos_session: str | None = Cookie(default=None)) -> str:
        """The kids' tablet (or an adult): may tick chores, nothing more."""
        who = sessions.read(s.signing_secret, lifeos_session)
        if who == KIDS or (who and who in people()):
            return who
        raise HTTPException(status_code=401, detail="Please sign in")

    def current_person(lifeos_session: str | None = Cookie(default=None),
                       x_lifeos_key: str | None = Header(default=None)) -> str:
        person = sessions.read(s.signing_secret, lifeos_session)
        if person and person in people():
            return person
        if s.api_key and x_lifeos_key == s.api_key:
            return people()[0]
        raise HTTPException(status_code=401, detail="Please sign in")

    # ---------- app pages ----------
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")

    if WEB_DIR.exists():
        app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="app")

    # ---------- sign in ----------
    @app.get("/api/people")
    def list_people():
        return [{"id": p, "name": name_of(p)} for p in people()]

    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        if not s.api_key:
            raise HTTPException(503, "Life OS isn't set up yet: the household key (LIFEOS_API_KEY) is missing.")
        who = request.client.host if request.client else "?"
        if limiter.blocked(who):
            raise HTTPException(429, "Too many wrong keys. Wait 15 minutes, then try again.")
        if body.join:
            from . import phones
            joined = phones.use_code(body.join.strip())
            if not joined:
                limiter.fail(who)
                raise HTTPException(401, "That sign-in code has been used or is more than 10 minutes old. Show a new "
                                         "one from the Life OS icon on the computer.")
            body.person = joined
        elif not hmac.compare_digest(body.key.strip().encode(), s.api_key.encode()):
            limiter.fail(who)
            raise HTTPException(status_code=401, detail="That household key is not right.")
        limiter.reset(who)
        if body.person not in people() and body.person not in (DISPLAY, KIDS):
            raise HTTPException(status_code=400, detail="Unknown person.")
        resp = JSONResponse({"person": body.person, "name": name_of(body.person)})
        resp.set_cookie(sessions.COOKIE, sessions.make(s.signing_secret, body.person), max_age=sessions.MAX_AGE,
                        httponly=True, samesite="lax",
                        # https pages get a secure cookie; this computer's own http://localhost page must not (Safari
                        # drops secure cookies there, so signing in on the Mac would never stick)
                        secure=request.url.scheme == "https" or (s.public_url.startswith("https://") and
                                                                 (request.url.hostname or "") not in LOCAL_HOSTS))
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(sessions.COOKIE)
        return resp

    @app.get("/api/me")
    def me(person: str = Depends(current_person)):
        return {"person": person, "name": name_of(person), "ai": agent.active_provider_name(),
                "people": [{"id": p, "name": name_of(p)} for p in people()], "problems": problems,
                "demo": s.demo, "checklist": checklist(person), "version": __version__}

    def checklist(person: str) -> dict:
        """Server-side half of the app's getting-started card (the phone works out notifications and install)."""
        connected = bool(svc.google) and person in getattr(svc.google, "people", {})
        week = household.week_meals(svc)
        return {"google": connected, "renewals": bool(svc.store.renewals()),
                "dinners": any(d["dinner"] for d in week)}

    # ---------- today ----------
    def waiting_on() -> list[dict]:
        """Tradespeople and appointments in motion, plus anything on the 'Waiting on' list in Google Tasks."""
        items = trades.open_loops(svc)
        if svc.google and "Waiting on" in (svc.settings.task_lists or []):
            ours = bridge.mirror_ids(svc)
            try:
                for t in [t for t in svc.google.get().list_tasks("Waiting on") if t["id"] not in ours][:6]:
                    items.append({"id": f"list-{t['id']}", "status": "list", "who": "", "title": t["title"],
                                  "line": "On your Waiting on list", "link": "#tasks"})
            except Exception as e:  # a missing list shouldn't break Today
                log.debug("waiting on list: %s", e)
        return items

    @app.get("/api/bridge")
    def api_bridge(person: str = Depends(current_person)):
        """Is the Gemini / Google apps link working? When did it last check, and what did it do?"""
        return bridge.status(svc)

    @app.post("/api/bridge/run")
    def api_bridge_run(person: str = Depends(current_person)):
        """Check now: e.g. just after telling Gemini about a job."""
        if not svc.google:
            raise HTTPException(400, "Connect a Google account first (Settings, Google accounts).")
        result = bridge.run(svc, svc.agent)
        return {**bridge.status(svc), "result": {k: len(v) for k, v in result.items()}}

    @app.get("/api/today")
    def today(fresh: bool = False, person: str = Depends(current_person)):
        warnings, events, tasks, next_up = [], [], [], []
        if fresh and svc.google and hasattr(svc.google, "forget_cached"):
            svc.google.forget_cached()
        if svc.google:
            try:
                events = svc.google.events_all(days=1)
            except Exception as e:
                warnings.append(f"Calendar: {aierrors.explain_google(e)}")
            try:
                # The few jobs worth doing next (with their main action), then anything else due or overdue.
                next_up = todos.next_up(svc)
                shown = {n["id"] for n in next_up}
                for t in todos.list_todos(svc):
                    if t["bucket"] == "now" and t["id"] not in shown:
                        tasks.append({**t, "overdue": t["days"] is not None and t["days"] < 0})
                tasks = tasks[:8]
            except Exception as e:
                warnings.append(f"To-dos: {aierrors.explain_google(e)}")
        else:
            warnings.append("No Google account connected yet. Go to Settings to connect one.")
        return {"name": name_of(person), "events": events, "tasks": tasks,
                "approvals": svc.store.list_pending(), "chores": svc.store.recent_chores(5), "warnings": warnings,
                "brief": svc.store.latest_brief(), "renewals": household.upcoming_renewals(svc, 60),
                "dinner": household.dinner_on(svc), "waiting": waiting_on(), "next_up": next_up,
                "rate_dinner": household.dinner_to_rate(svc), "childcare": soon_childcare(),
                "parcels": _safe_call(lambda: deliveries.current(svc), {}),
                "bins_tomorrow": _safe_call(lambda: bins.tomorrow(svc), []),
                "school_tomorrow": _safe_call(lambda: schoolkit.tomorrow(svc), None),
                "christmas": _safe_call(lambda: christmas.wall(svc), None)}

    def _safe_call(fn, default):
        try:
            return fn()
        except Exception as e:  # one feature failing never stops Today or the display loading
            log.warning("today/display: %s", e)
            return default

    # ---------- smart to-dos and projects ----------
    def need_google():
        if not svc.google:
            raise HTTPException(503, "Connect a Google account first (Settings > Google accounts).")

    @app.get("/api/todos")
    def api_todos(person: str = Depends(current_person)):
        need_google()
        try:
            items = todos.list_todos(svc)
        except Exception as e:
            raise HTTPException(503, str(e)) from e
        return {"todos": items, "buckets": todos.BUCKET_LABELS, "categories": todos.category_names(s),
                "priorities": todos.PRIORITY_WORDS}

    @app.post("/api/todos")
    def api_add_todo(body: TodoBody, person: str = Depends(current_person)):
        need_google()
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Say what needs doing first.")
        try:
            return todos.add_todo(svc, agent, text, added_by=person)
        except Exception as e:
            log.exception("add todo failed")
            raise HTTPException(503, f"Couldn't add that just now: {aierrors.explain_google(e)}") from e

    def _todo_change(fn):
        need_google()
        try:
            return fn()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/todos/snooze")
    def api_snooze(body: SnoozeBody, person: str = Depends(current_person)):
        return _todo_change(lambda: todos.snooze_todo(svc, body.list_name, body.task_id, body.until, by=person))

    @app.post("/api/todos/edit")
    def api_edit(body: EditBody, person: str = Depends(current_person)):
        return _todo_change(lambda: todos.edit_todo(
            svc, body.list_name, body.task_id, by=person, title=body.title, due=body.due, category=body.category,
            who=body.who, priority=body.priority, next_action=body.next_action))

    @app.post("/api/todos/delete")
    def api_delete(body: TodoDoneBody, person: str = Depends(current_person)):
        return _todo_change(lambda: todos.delete_todo(svc, body.list_name, body.task_id, by=person))

    # ---------- predictions: what the household will need, learned from what it does ----------
    @app.get("/api/predictions")
    def api_predictions(person: str = Depends(current_person)):
        on_lists, open_jobs = activity.current_context(svc)
        cfg = svc.settings.behaviour.get("predictions") or {}
        history = svc.store.activity(since=(date.today() - timedelta(days=800)).isoformat())
        return {"predictions": activity.predict(svc, on_lists=on_lists, open_jobs=open_jobs),
                "auto_add": cfg.get("auto_add", "high"), "learned_from": len(history)}

    @app.post("/api/predictions/act")
    def api_prediction_act(body: PredictionAct, person: str = Depends(current_person)):
        p = activity.find(svc, body.pid)
        if not p:
            raise HTTPException(404, "That prediction has changed. Pull down to refresh.")
        if body.action == "add":
            need_google()
            return {"ok": True, **activity.add_prediction(svc, p, by=person)}
        if body.action == "snooze":
            activity.snooze(svc, p, max(1, min(90, body.days)))
        elif body.action == "dismiss":
            activity.dismiss(svc, p)
        elif body.action == "never":
            activity.never(svc, p)
        elif body.action == "last" and p["kind"] == "checkup":
            try:
                when = date.fromisoformat(body.last)
            except ValueError as e:
                raise HTTPException(400, "Pick the date of the last one.") from e
            activity.record_done(svc, p["title"], "Health and appointments", person, "told", at=when.isoformat())
        else:
            raise HTTPException(400, "Unknown action.")
        return {"ok": True}

    # ---------- ordering the Shopping list online ----------
    @app.get("/api/shop")
    def api_shop(supermarket: str = "", person: str = Depends(current_person)):
        need_google()
        items = [t for t in svc.google.get().list_tasks("Shopping") if t.get("status") != "completed"]
        return shop.plan(svc, items, supermarket)

    @app.post("/api/shop/ordered")
    def api_shop_ordered(body: OrderedBody, person: str = Depends(current_person)):
        """Ordered (or bought): ticked off the Shopping list and remembered as bought."""
        need_google()
        g = svc.google.get()
        open_items = {t["id"]: t for t in g.list_tasks("Shopping")}
        done = []
        for tid in body.task_ids[:100]:
            t = open_items.get(tid)
            if not t:
                continue
            g.complete_task("Shopping", tid)
            activity.record_bought(svc, t["title"], person, body.where[:20], ref=f"task:{tid}")
            done.append(t["title"])
        svc.store.log_event("shopping_ordered", f"{len(done)} items ({body.where}) by {person}")
        return {"ok": True, "done": done}

    @app.get("/api/products")
    def api_products(person: str = Depends(current_person)):
        return {"products": list(svc.store.products().values())}

    @app.post("/api/products")
    def api_save_product(body: ProductBody, person: str = Depends(current_person)):
        asin = shop.asin_of(body.link)
        key = activity.normalise(body.item)
        if not asin:
            raise HTTPException(400, "That doesn't look like an Amazon product link. Open the product on Amazon, "
                                     "tap Share, Copy link, and paste it here.")
        if not key:
            raise HTTPException(400, "Say which item this is for.")
        svc.store.save_product(key, body.title.strip() or body.item.strip(), asin=asin,
                               url=body.link if body.link.startswith("http") else "")
        return {"ok": True, "key": key, "asin": asin}

    @app.post("/api/products/delete")
    def api_delete_product(body: ProductBody, person: str = Depends(current_person)):
        svc.store.delete_product(activity.normalise(body.item))
        return {"ok": True}

    @app.get("/api/week")
    def api_week(person: str = Depends(current_person)):
        """The week in one place: what got done, who you're waiting on, the next 7 days, needs coming up."""
        done = activity.summary_week(svc)
        events, predicted = [], []
        if svc.google:
            try:
                events = svc.google.events_all(days=8)
            except Exception as e:
                log.warning("week calendar: %s", e)
            on_lists, open_jobs = activity.current_context(svc)
            predicted = [p for p in activity.predict(svc, on_lists=on_lists, open_jobs=open_jobs)
                         if p["days_until"] <= 7 and p["status"] != "added" and not p.get("needs_last")]
        return {**done, "waiting": waiting_on(), "events": events, "predicted": predicted,
                "childcare": childcare.grouped(childcare.gaps(svc)) if svc.google else [],
                "closed": [c for c in childcare.closed_periods(svc) if c["end"] >= household.today_local(s).isoformat()][:8]}

    @app.get("/api/plan-week")
    def api_plan_week(person: str = Depends(current_person)):
        need_google()
        return todos.plan_week(svc)

    @app.post("/api/plan-week")
    def api_plan_week_confirm(body: PlanBody, person: str = Depends(current_person)):
        """The person ticked which suggested times to keep: put them in the calendar."""
        need_google()
        open_items = {(t["list"], t["id"]): t for t in todos.list_todos(svc)}
        done = []
        for i in body.items[:10]:
            t = open_items.get((i.list_name, i.task_id))
            if t:
                done.append({"title": t["title"], **trades.block_time(svc, t, i.start, i.duration_min, person)})
        return {"planned": done}

    @app.post("/api/todos/complete")
    def api_complete_todo(body: TodoDoneBody, person: str = Depends(current_person)):
        need_google()
        try:
            return todos.complete_todo(svc, body.list_name, body.task_id, by=person)
        except Exception as e:
            raise HTTPException(503, str(e)) from e

    def find_todo(list_name: str, task_id: str) -> dict:
        todo = next((t for t in todos.list_todos(svc) if t["id"] == task_id and t["list"] == list_name), None)
        if not todo:
            raise HTTPException(404, "That to-do isn't open any more.")
        return todo

    @app.post("/api/todos/solve")
    def api_solve(body: SolveBody, person: str = Depends(current_person)):
        need_google()
        return solver.solve_and_store(svc, agent, find_todo(body.list_name, body.task_id), body.hint.strip()[:300])

    @app.post("/api/todos/answer")
    def api_answer(body: SolveBody, person: str = Depends(current_person)):
        """Answer the suggestion's question: saved to home details, then solved again with it."""
        need_google()
        todo = find_todo(body.list_name, body.task_id)
        sol = todo.get("solution") or {}
        if not body.answer.strip():
            raise HTTPException(400, "Pick or type an answer.")
        solver.answer_question(svc, sol, body.answer[:200])
        return solver.solve_and_store(svc, agent, todo)

    @app.post("/api/todos/ordered")
    def api_ordered(body: SolveBody, person: str = Depends(current_person)):
        """The person bought one of the suggestions: remember it and tick the to-do off."""
        need_google()
        todo = find_todo(body.list_name, body.task_id)
        sol = todo.get("solution") or {}
        options = sol.get("options") or []
        if options and 0 <= body.option < len(options):
            opt = options[body.option]
            solver.record_purchase(svc, sol, opt)
            profiles.record_given(svc, todo["title"], opt["title"])  # a present: not suggested again
            svc.store.log_event("ordered", f"{opt['title']} for '{todo['title']}' by {person}")
            key = activity.normalise(todo["title"])
            activity.record_bought(svc, todo["title"], person, "amazon", ref=f"order:{body.task_id}",
                                   data={"product": opt["title"]})
            asin = shop.asin_of(opt.get("basket_url") or opt.get("url") or "")
            if asin and key:  # next time this is needed, it's one tap to the same product
                svc.store.save_product(key, opt["title"], asin=asin, url=opt.get("url") or "")
        return todos.complete_todo(svc, body.list_name, body.task_id, by=person)

    # ---------- tradespeople: contacts, emailing, replies, booking ----------
    @app.get("/api/contacts")
    def api_contacts(trade: str = "", person: str = Depends(current_person)):
        return {"contacts": svc.store.contacts(trade or None)}

    @app.post("/api/contacts")
    def api_save_contact(body: ContactBody, cid: int = 0, person: str = Depends(current_person)):
        if not body.name.strip():
            raise HTTPException(400, "Give them a name.")
        if body.email and "@" not in body.email:
            raise HTTPException(400, "That email address doesn't look right.")
        return {"id": svc.store.save_contact(cid or None, **{**body.model_dump(), "favourite": int(body.favourite)})}

    @app.post("/api/contacts/{cid}/favourite")
    def api_favourite_contact(cid: int, person: str = Depends(current_person)):
        c = svc.store.get_contact(cid)
        if not c:
            raise HTTPException(404, "Not found.")
        svc.store.save_contact(cid, **{k: c[k] for k in ("name", "trade", "company", "email", "phone", "website", "notes")
                                       if c.get(k) is not None}, favourite=1)
        return {"ok": True}

    @app.post("/api/contacts/{cid}/delete")
    def api_delete_contact(cid: int, person: str = Depends(current_person)):
        if not svc.store.delete_contact(cid):
            raise HTTPException(404, "Not found.")
        return {"ok": True}

    @app.post("/api/todos/arrange")
    def api_arrange(body: ArrangeBody, person: str = Depends(current_person)):
        """The person tapped Send on this exact email: send it from their Gmail and watch for a reply."""
        need_google()
        todo = find_todo(body.list_name, body.task_id)
        sol = todo.get("solution") or {}
        contacts = sol.get("contacts") or []
        chosen = body.contacts or [body.contact]
        if sol.get("kind") != "arrange" or not chosen or not all(0 <= i < len(contacts) for i in chosen):
            raise HTTPException(400, "Choose who to send it to.")
        if not body.subject.strip() or not body.body.strip():
            raise HTTPException(400, "The email needs a subject and a message.")
        sender = person if person in svc.google.people and getattr(svc.google.people[person], "has_gmail", True) \
            else sol.get("sender") or svc.google.owner
        try:
            if len(chosen) == 1:
                contact = {**contacts[chosen[0]], "trade": sol.get("trade", "")}
                return trades.send_request(svc, todo, contact, body.subject, body.body, sol.get("slots") or [], sender)
            return {"sent": trades.ask_for_quotes(svc, todo, [{**contacts[i], "trade": sol.get("trade", "")} for i in chosen],
                                                  body.subject, body.body, sol.get("slots") or [], sender)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            log.exception("sending to tradesperson failed")
            raise HTTPException(503, f"Couldn't send the email: {e}") from e

    @app.get("/api/requests/{rid}/reminder")
    def api_reminder_draft(rid: int, person: str = Depends(current_person)):
        """The reminder text, so the person reads it before anything is sent."""
        r = svc.store.get_request(rid)
        if not r:
            raise HTTPException(404, "Not found.")
        m = trades.reminder_email(svc, r)
        return {"subject": m["subject"], "body": m["body"], "to": r["contact_email"]}

    @app.post("/api/requests/{rid}/remind")
    def api_remind(rid: int, body: ReminderBody | None = None, person: str = Depends(current_person)):
        """The person tapped Send on this exact reminder text."""
        if not body or not body.body.strip():
            raise HTTPException(400, "Nothing to send: open the reminder first.")
        try:
            return trades.send_reminder(svc, rid, body.subject, body.body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/requests/{rid}/book")
    def api_book(rid: int, body: BookBody, person: str = Depends(current_person)):
        try:
            return trades.book(svc, rid, body.start, body.duration_min, person, body.confirm_body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/requests/{rid}/cancel")
    def api_cancel_request(rid: int, person: str = Depends(current_person)):
        r = svc.store.get_request(rid)
        if not r:
            raise HTTPException(404, "Not found.")
        trades.cancel_request(svc, rid, f"asked someone else ({person})")
        return {"ok": True}

    @app.get("/api/slots")
    def api_slots(duration_min: int = 60, diy: bool = True, person: str = Depends(current_person)):
        return {"slots": scheduling.free_slots(svc, duration_min, 3, 0 if diy else 2,
                                               hours=scheduling.DIY_HOURS if diy else None,
                                               people=[name_of(person)] if diy else None)}

    @app.post("/api/todos/schedule")
    def api_schedule(body: ScheduleBody, person: str = Depends(current_person)):
        need_google()
        return trades.block_time(svc, find_todo(body.list_name, body.task_id), body.start, body.duration_min, person)

    @app.get("/api/home-details")
    def api_home_details(person: str = Depends(current_person)):
        return {"details": svc.store.home_details()}

    @app.post("/api/home-details")
    def api_save_home_detail(body: HomeDetailBody, person: str = Depends(current_person)):
        if not (body.area.strip() and body.item.strip() and body.details.strip()):
            raise HTTPException(400, "Fill in where, what and the details.")
        return {"id": svc.store.set_home_detail(body.area, body.item, body.details)}

    @app.post("/api/home-details/{did}/delete")
    def api_delete_home_detail(did: int, person: str = Depends(current_person)):
        if not svc.store.delete_home_detail(did):
            raise HTTPException(404, "Not found.")
        return {"ok": True}

    @app.get("/api/projects")
    def api_projects(person: str = Depends(current_person)):
        need_google()
        return {"projects": todos.all_projects(svc, include_done=True)}

    @app.post("/api/projects")
    def api_start_project(body: ProjectBody, person: str = Depends(current_person)):
        need_google()
        if not body.name.strip():
            raise HTTPException(400, "Give the project a name, for example Redecorate the hallway.")
        try:
            return todos.start_project(svc, agent, body.name, body.details, by=person)
        except Exception as e:
            log.exception("start project failed")
            raise HTTPException(503, f"Couldn't start that just now: {e}") from e

    @app.post("/api/projects/{pid}/steps")
    def api_add_step(pid: int, body: TodoBody, person: str = Depends(current_person)):
        need_google()
        project = svc.store.get_project(pid)
        if not project:
            raise HTTPException(404, "Not found.")
        t = todos.normalise(svc, {"title": body.text, "category": project["category"], "project": project["name"],
                                  "timescale": "later"}, body.text)
        todos.file_todo(svc, t, person, original=body.text)
        if project["status"] == "done":
            svc.store.set_project_status(pid, "active")
        return todos.project_summary(svc, svc.store.get_project(pid))

    # ---------- meal plan ----------
    @app.post("/api/meals/shopping")
    def api_meals_shopping(person: str = Depends(current_person)):
        """Missing ingredients for this week's dinners onto the Shopping list."""
        need_google()
        return household.meals_to_shopping(svc, svc.agent)

    @app.get("/api/meals")
    def api_meals(person: str = Depends(current_person)):
        return {"days": household.week_meals(svc), "favourites": s.behaviour.get("favourite_meals") or []}

    @app.post("/api/meals")
    def api_save_meals(body: MealPlanBody, person: str = Depends(current_person)):
        """Saves names only from the app; the AI adds ingredients when it plans."""
        current = {m["day"]: m for m in household.week_meals(svc, days=14)}
        changes = []
        for m in body.meals:
            try:
                date.fromisoformat(m.day)
            except ValueError:
                raise HTTPException(400, "Bad date.") from None
            before = current.get(m.day, {})
            if m.dinner.strip() != before.get("dinner", ""):
                same = m.dinner.strip().lower() == before.get("dinner", "").lower()
                changes.append({"day": m.day, "dinner": m.dinner.strip(),
                                "ingredients": before.get("ingredients", []) if same else [], "notes": ""})
        result = household.save_meal_plan(svc, changes, add_to_shopping=False)
        return {"ok": True, "changed": len(changes), **result}

    # ---------- kitchen wall display (read-only) ----------
    @app.get("/api/wall")
    def api_wall(viewer: str = Depends(current_viewer)):
        events, weather = [], None
        if svc.google:
            try:
                events = svc.google.events_all(days=2)
            except Exception as e:
                log.warning("wall calendar: %s", e)
        wcfg = s.behaviour.get("wall") or {}
        if svc.ha and wcfg.get("weather_entity"):
            try:
                st = svc.ha.state(wcfg["weather_entity"])
                attrs = st.get("attributes", {})
                weather = {"condition": st.get("state"), "temperature": attrs.get("temperature"),
                           "unit": attrs.get("temperature_unit", "°C")}
            except Exception as e:
                log.warning("wall weather: %s", e)
        board = household.chores_board(svc)
        tomorrow = household.today_local(s) + __import__("datetime").timedelta(days=1)
        brief = svc.store.latest_brief()
        return {"events": events, "weather": weather, "dinner": household.dinner_on(svc),
                "dinner_tomorrow": household.dinner_on(svc, tomorrow),
                "kids": [{"kid": k["kid"], "owed": k["owed"], "done": len(k["done_this_week"]),
                          "goals": [{"name": g["name"], "pct": g["pct"]} for g in household.goals(svc, k["kid"]) if not g["done"]]}
                         for k in board["kids"]],
                "renewals": household.upcoming_renewals(svc, 30),
                "manual": manual.for_display(svc),
                "brief": brief["text"] if brief and brief["name"] == "morning_brief" else "",
                "pending": len(svc.store.list_pending()),
                "parcels": _safe_call(lambda: deliveries.wall(svc), []),
                "bins": _safe_call(lambda: bins.overview(svc)["next"], None),
                "stars": _safe_call(lambda: [{"kid": k["kid"], "total": k["total"], "target": k["target"]}
                                             for k in kidstuff.stars(svc)["kids"]], []),
                "school_tomorrow": _safe_call(lambda: schoolkit.tomorrow(svc), None),
                "christmas": _safe_call(lambda: christmas.wall(svc), None),
                "guests": _safe_call(lambda: guests.wall(svc), None)}

    # ---------- renewals ----------
    @app.get("/api/renewals")
    def api_renewals(person: str = Depends(current_person)):
        return {"renewals": [{**r, "files": files.for_item(svc, "renewal", r["id"])} for r in svc.store.renewals()]}

    @app.post("/api/renewals")
    def api_add_renewal(body: RenewalBody, person: str = Depends(current_person)):
        if not body.name.strip():
            raise HTTPException(400, "Give it a name, for example Car insurance.")
        try:
            date.fromisoformat(body.due_date)
        except ValueError:
            raise HTTPException(400, "Choose the date it renews or expires.") from None
        result = run_tool("add_renewal", {"name": body.name.strip()[:120], "due_date": body.due_date,
                                          "category": body.category, "notes": body.notes[:500],
                                          "repeat_months": max(0, min(120, body.repeat_months))}, svc)
        household.check_renewals(svc)  # remind straight away if it is already close
        return result

    @app.post("/api/renewals/{rid}/delete")
    def api_delete_renewal(rid: int, person: str = Depends(current_person)):
        if not svc.store.delete_renewal(rid):
            raise HTTPException(404, "Not found.")
        return {"ok": True}

    # ---------- chores and pocket money ----------
    @app.get("/api/chores")
    def api_chores(person: str = Depends(current_person)):
        return household.chores_board(svc)

    @app.post("/api/chores/done")
    def api_chore_done(body: ChoreBody, person: str = Depends(current_person)):
        chore = next((c for c in household.chore_catalogue(s) if c["id"] == body.chore_id), None)
        if not chore or body.kid not in chore["who"]:
            raise HTTPException(400, "Unknown chore for that child.")
        cid = svc.store.log_chore(chore["name"], body.kid, chore["value"])
        svc.store.log_event("chore", f"{chore['name']} by {body.kid} (ticked by {person})")
        return {"ok": True, "id": cid, "value": chore["value"]}

    @app.post("/api/chores/{cid}/undo")
    def api_chore_undo(cid: int, person: str = Depends(current_person)):
        if not svc.store.undo_chore(cid):
            raise HTTPException(400, "That one can't be undone (already paid, or not found).")
        return {"ok": True}

    @app.post("/api/chores/paid")
    def api_chore_paid(body: ChoreBody, person: str = Depends(current_person)):
        amount = svc.store.mark_paid(body.kid)
        svc.store.log_event("pocket_money_paid", f"{body.kid} £{amount:.2f} by {person}")
        return {"ok": True, "paid": amount}

    # ---------- in-app notifications ----------
    def push_unavailable() -> str:
        why = f" ({svc.push_problem})" if svc.push_problem else ""
        return (f"Notifications couldn't start on the Life OS computer{why}. "
                "Restart Life OS; if this stays, Settings > Check everything shows what to do.")

    @app.get("/api/push/key")
    def api_push_key(person: str = Depends(current_person)):
        if not svc.pusher:
            raise HTTPException(503, push_unavailable())
        return {"key": svc.pusher.public_key}

    @app.post("/api/push/subscribe")
    def api_push_subscribe(body: SubscriptionBody, person: str = Depends(current_person)):
        if not body.endpoint.startswith("https://"):
            raise HTTPException(400, "Invalid subscription.")
        svc.store.save_subscription(person, {"endpoint": body.endpoint, "keys": body.keys})
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    def api_push_unsubscribe(body: SubscriptionBody, person: str = Depends(current_person)):
        svc.store.delete_subscription(body.endpoint)
        return {"ok": True}

    @app.post("/api/push/test")
    def api_push_test(person: str = Depends(current_person)):
        if not svc.pusher:
            raise HTTPException(503, push_unavailable())
        n = svc.pusher.push(f"Notifications are working for {name_of(person)}.", title="Life OS", person=person)
        if not n:
            raise HTTPException(400, "This phone isn't signed up for notifications yet.")
        return {"ok": True, "sent": n}

    # ---------- ask ----------
    @app.post("/api/ask")
    def api_ask(body: AskBody, person: str = Depends(current_person)):
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Type or say something first.")
        if len(text) > 4000:
            raise HTTPException(400, "That's too long; keep it under 4,000 characters.")
        history = svc.store.recent_turns(person)
        try:
            reply = agent.run(f"[Asked by {name_of(person)} (person id: {person})] {text}", deep=body.deep,
                              source=f"app:{person}", history=history) or "Done."
        except Exception as e:
            log.exception("ask failed")
            from . import aierrors
            raise HTTPException(502, aierrors.explain(e, agent.active_provider_name())) from e
        svc.store.add_turn(person, "user", f"[Asked by {name_of(person)} (person id: {person})] {text}")
        svc.store.add_turn(person, "assistant", reply)
        return {"reply": reply, "ai": agent.active_provider_name()}

    @app.get("/api/conversation")
    def api_conversation(person: str = Depends(current_person)):
        turns = svc.store.recent_turns(person)
        for t in turns:
            if t["role"] == "user" and t["text"].startswith("[Asked by"):
                t["text"] = t["text"].split("] ", 1)[-1]
        return {"turns": turns}

    @app.post("/api/conversation/reset")
    def api_conversation_reset(person: str = Depends(current_person)):
        svc.store.clear_turns(person)
        return {"ok": True}

    # ---------- tasks ----------
    def owner_for(list_owner: str | None, person: str) -> str | None:
        """Lists are either shared (held by the household account) or the signed-in person's own."""
        return person if list_owner == "mine" and person != svc.google.owner else None

    @app.get("/api/tasks")
    def api_tasks(list_name: str = "Shopping", owner: str = "shared", person: str = Depends(current_person)):
        if not svc.google:
            raise HTTPException(503, "No Google account connected yet. Go to Settings to connect one.")
        groups = [{"owner": "shared", "label": "Shared lists", "lists": s.task_lists}]
        if person != svc.google.owner and person in svc.google.people:
            try:
                mine = [n for n in svc.google.get(person).list_names() if n not in s.task_lists]
                if mine:
                    groups.append({"owner": "mine", "label": "My own lists", "lists": mine})
            except Exception as e:
                log.warning("own lists for %s failed: %s", person, e)
        try:
            tasks = svc.google.get(owner_for(owner, person)).list_tasks(list_name)
        except Exception as e:
            raise HTTPException(503, str(e)) from e
        return {"groups": groups, "lists": s.task_lists, "list": list_name, "owner": owner, "tasks": tasks}

    @app.post("/api/tasks")
    def api_add_task(body: TaskBody, owner: str = "shared", person: str = Depends(current_person)):
        if not body.title.strip():
            raise HTTPException(400, "The task needs a name.")
        args = {"list_name": body.list_name, "title": body.title.strip()[:500], "due": body.due or None}
        if owner_for(owner, person):
            args["person"] = person
        try:
            return run_tool("add_task", args, svc)
        except Exception as e:
            raise HTTPException(503, str(e)) from e

    @app.post("/api/tasks/complete")
    def api_complete_task(body: TaskBody, owner: str = "shared", person: str = Depends(current_person)):
        args = {"list_name": body.list_name, "task_id": body.task_id}
        if owner_for(owner, person):
            args["person"] = person
        title = ""
        if body.list_name.lower() == "shopping" and svc.google:
            try:
                acct = svc.google.get(person) if "person" in args else svc.google.get()
                title = next((t["title"] for t in acct.list_tasks(body.list_name) if t["id"] == body.task_id), "")
            except Exception as e:
                log.debug("could not read shopping item: %s", e)
        try:
            result = run_tool("complete_task", args, svc)
            svc.store.log_event("task_done", f"{body.list_name}/{body.task_id} by {person}")
            if title:  # bought: remembered, so Life OS can tell when it'll be needed again
                activity.record_bought(svc, title, person, "app", ref=f"task:{body.task_id}")
            return result
        except Exception as e:
            raise HTTPException(503, str(e)) from e

    # ---------- leaving home check ----------
    @app.get("/api/home/leaving")
    def api_leaving(person: str = Depends(current_person)):
        return leaving.check(svc)

    @app.post("/api/home/leaving/fix")
    def api_leaving_fix(entity_id: str = Body(..., embed=True), person: str = Depends(current_person)):
        try:
            return leaving.fix(svc, entity_id, person)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(503, f"Home Assistant didn't respond: {e}") from e

    # ---------- home ----------
    @app.get("/api/home")
    def api_home(person: str = Depends(current_person)):
        states, warning = [], ""
        if svc.ha:
            for entity in s.home_status:
                try:
                    st = svc.ha.state(entity)
                    states.append({"entity_id": entity, "name": st.get("attributes", {}).get("friendly_name", entity),
                                   "state": st.get("state")})
                except Exception:
                    states.append({"entity_id": entity, "name": entity, "state": "unavailable"})
        else:
            warning = "Home Assistant is not connected yet."
        actions = [{"id": a["id"], "label": a["label"]} for a in s.quick_actions]
        return {"actions": actions, "states": states, "warning": warning}

    @app.post("/api/home/action/{action_id}")
    def api_home_action(action_id: str, person: str = Depends(current_person)):
        action = next((a for a in s.quick_actions if a["id"] == action_id), None)
        if not action:
            raise HTTPException(404, "Unknown button.")
        if not svc.ha:
            raise HTTPException(503, "Home Assistant is not connected yet.")
        data = {k: v for k, v in action.items() if k not in ("id", "label", "service")}
        try:
            svc.ha.call_service(action["service"], data)
        except Exception as e:
            raise HTTPException(503, f"Home Assistant said: {e}") from e
        svc.store.log_event("quick_action", f"{action_id} by {person}")
        return {"ok": True, "label": action["label"]}

    # ---------- approvals ----------
    @app.get("/api/approvals")
    def api_approvals(person: str = Depends(current_person)):
        return svc.store.list_pending()

    @app.post("/api/approvals/{pid}/{decision}")
    def api_decide(pid: str, decision: str, person: str = Depends(current_person)):
        if decision not in ("approve", "reject"):
            raise HTTPException(404)
        result = approvals.decide(svc, pid, decision)
        svc.store.log_event("approval_decided", f"{pid} {decision} by {person}")
        return result

    # ---------- AI switch ----------
    @app.get("/api/provider")
    def api_get_provider(person: str = Depends(current_person)):
        return {"provider": agent.active_provider_name()}

    @app.post("/api/provider")
    def api_set_provider(body: ProviderBody, person: str = Depends(current_person)):
        return _switch(body.provider)

    def _switch(raw: str):
        name = raw.strip().lower()
        if name not in ("claude", "gemini"):
            raise HTTPException(400, "Choose Claude or Gemini.")
        try:
            agent.switch_provider(name)
        except Exception as e:
            raise HTTPException(400, f"Could not start {name.title()}: {e}") from e
        return {"provider": name}

    # ---------- Google accounts ----------
    @app.get("/api/accounts")
    def api_accounts(person: str = Depends(current_person)):
        status = svc.google.status() if svc.google is not None and hasattr(svc.google, "status") else []
        return {"accounts": status, "redirect_uri": oauth.redirect_uri(s),
                "client_ready": Path(s.google_client_secret_file).exists()}

    @app.get("/auth/google/start")
    def google_start(for_person: str = Query(alias="person"), gmail: bool = False,
                     person: str = Depends(current_person)):
        if for_person not in people():
            raise HTTPException(400, "Unknown person.")
        if not Path(s.google_client_secret_file).exists():
            raise HTTPException(503, "Google sign-in is not set up on the server yet (see README, step 4).")
        return RedirectResponse(oauth.start(s, for_person, gmail))

    @app.get("/auth/google/callback")
    def google_callback(state: str = "", code: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/app/#settings?google=cancelled")
        try:
            who = oauth.finish(s, svc.google, state, code)
        except Exception as e:
            log.warning("Google sign-in failed: %s", e)
            return RedirectResponse("/app/#settings?google=failed")
        svc.store.log_event("google_connected", who)
        return RedirectResponse(f"/app/#settings?google=connected&person={who}")

    @app.post("/api/accounts/{who}/disconnect")
    def api_disconnect(who: str, person: str = Depends(current_person)):
        if who not in people():
            raise HTTPException(400, "Unknown person.")
        svc.google.disconnect(who)
        svc.store.log_event("google_disconnected", f"{who} by {person}")
        return {"ok": True}

    # ---------- v0.15: cars, money, childcare, energy, dinners, gift memory, home manual ----------
    BODY = Body(...)  # a plain JSON object

    def soon_childcare() -> list[dict]:
        if not svc.google:
            return []
        try:
            return [g for g in childcare.grouped(childcare.gaps(svc, days=21))][:2]
        except Exception as e:
            log.warning("childcare check: %s", e)
            return []

    def _bad(e: Exception):
        raise HTTPException(400, str(e)) from e

    @app.get("/api/cars")
    def api_cars(person: str = Depends(current_person)):
        return {"cars": cars.cars(svc), "dvla": bool(cars._key(svc))}

    @app.post("/api/cars")
    def api_add_car(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return cars.add_car(svc, str(body.get("reg", "")), str(body.get("name", "")), str(body.get("mot_due", "")),
                                str(body.get("tax_due", "")))
        except ValueError as e:
            _bad(e)
        except Exception as e:
            raise HTTPException(503, f"The DVLA didn't answer: {e}") from e

    @app.post("/api/cars/delete")
    def api_delete_car(body: dict = BODY, person: str = Depends(current_person)):
        try:
            cars.remove_car(svc, str(body.get("reg", "")))
        except ValueError as e:
            _bad(e)
        return {"ok": True}

    @app.get("/api/money")
    def api_money(month: str = "", person: str = Depends(current_person)):
        return {"subscriptions": money.subscriptions(svc), "spending": money.spending(svc, month)}

    @app.post("/api/money/subscription")
    def api_sub_decide(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return money.decide(svc, str(body.get("id", "")), str(body.get("choice", "")), person)
        except ValueError as e:
            _bad(e)

    @app.post("/api/money/subscription/add")
    def api_sub_add(body: dict = BODY, person: str = Depends(current_person)):
        try:
            amount = float(body.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if not str(body.get("service", "")).strip() or amount <= 0:
            raise HTTPException(400, "Give the name and what it costs.")
        return money.record_subscription(svc, str(body["service"]), round(amount, 2), str(body.get("period", "monthly")),
                                         str(body.get("next_date", "")), seen=household.today_local(s).isoformat())

    @app.get("/api/childcare")
    def api_childcare(person: str = Depends(current_person)):
        return {"gaps": childcare.grouped(childcare.gaps(svc)) if svc.google else [],
                "closed": childcare.closed_periods(svc), "kids": s.household.get("kids") or [],
                # Only people saved as family or childcare (not the electrician)
                "contacts": [c for c in svc.store.contacts() if (c.get("phone") or c.get("email")) and
                             any(w in (c.get("trade") or "").lower() for w in childcare.HELPER_WORDS)][:6]}

    @app.post("/api/childcare/closed")
    def api_childcare_closed(body: dict = BODY, person: str = Depends(current_person)):
        """Paste the school's term dates, or add one closure."""
        text = str(body.get("text", ""))
        added = []
        if text.strip():
            for c in childcare.parse_term_dates(text):
                if childcare.add_closed(svc, c["name"], c["start"], c["end"], source="pasted"):
                    added.append(c)
        elif body.get("start") and childcare.add_closed(svc, str(body.get("name") or "School closed"), str(body["start"]),
                                                        str(body.get("end") or body["start"]), source="typed"):
            added.append({"name": body.get("name"), "start": body["start"], "end": body.get("end")})
        if not added:
            raise HTTPException(400, "No school closures found there. Try lines like: Christmas holidays 22 December "
                                     "2026 - 2 January 2027")
        return {"added": added}

    @app.post("/api/childcare/closed/delete")
    def api_childcare_closed_delete(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": svc.store.remove("school_day", str(body.get("id", "")))}

    @app.post("/api/childcare/covered")
    def api_childcare_covered(body: dict = BODY, person: str = Depends(current_person)):
        dates = [str(d) for d in body.get("dates") or []][:40]
        if not dates:
            raise HTTPException(400, "Which days?")
        return {"covered": childcare.mark_covered(svc, dates, str(body.get("who", "")), bool(body.get("calendar", True)))}

    @app.post("/api/childcare/leave")
    def api_childcare_leave(body: dict = BODY, person: str = Depends(current_person)):
        label = str(body.get("label", "")).strip()[:60]
        title = f"Ask for time off: {label}" if label else "Ask for time off for the school holidays"
        return todos.add_todo(svc, None, title, person, judged=todos.normalise(svc, {
            "title": title, "category": "Admin and renewals", "priority": 2, "effort_min": 10,
            "due": household.today_local(s).isoformat()}, title))

    @app.get("/api/energy")
    def api_energy(person: str = Depends(current_person)):
        return energy.plan(svc)

    @app.post("/api/energy/run")
    def api_energy_run(body: dict = BODY, person: str = Depends(current_person)):
        try:
            r = energy.schedule(svc, str(body.get("entity", "")), str(body.get("start", "")), person)
        except ValueError as e:
            _bad(e)
        svc.store.log_event("energy_run", f"{r['name']} at {r['start']} by {person}")
        return r

    @app.post("/api/energy/cancel")
    def api_energy_cancel(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": energy.cancel(svc, str(body.get("id", "")))}

    @app.get("/api/meals/suggest")
    def api_meals_suggest(person: str = Depends(current_person)):
        return {"suggestions": household.suggest_dinners(svc)}

    @app.post("/api/meals/rate")
    def api_meals_rate(body: dict = BODY, person: str = Depends(current_person)):
        day, rating = str(body.get("day", "")), int(body.get("rating") or 0)
        if rating == 0:
            svc.store.set_state(f"meal_rate_skip:{day}", "1")
            return {"ok": True}
        return {"ok": household.rate_dinner(svc, day, rating)}

    @app.get("/api/people-gifts")
    def api_profiles(person: str = Depends(current_person)):
        return {"people": profiles.profiles(svc)}

    @app.post("/api/people-gifts")
    def api_profile_save(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return profiles.save(svc, str(body.get("name", "")), str(body.get("relation", "")), str(body.get("birthday", "")),
                                 str(body.get("likes", "")), str(body.get("sizes", "")), str(body.get("notes", "")),
                                 str(body.get("wishlist", "")))
        except ValueError as e:
            _bad(e)

    @app.post("/api/people-gifts/delete")
    def api_profile_delete(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return {"ok": profiles.delete(svc, str(body.get("name", "")))}
        except ValueError as e:
            _bad(e)

    @app.get("/api/manual")
    def api_manual(person: str = Depends(current_person)):
        return {"entries": [{**e, "files": files.for_item(svc, "manual", e["id"])} for e in manual.entries(svc)],
                "sections": manual.SECTIONS, "trades": manual.trades(svc),
                "starters": [{"section": a, "title": b} for a, b in manual.STARTERS
                             if not any(e["title"].lower() == b.lower() for e in manual.entries(svc))]}

    @app.post("/api/manual")
    def api_manual_save(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return manual.save(svc, str(body.get("title", "")), str(body.get("details", "")), str(body.get("section", "Other")),
                               body.get("private") if isinstance(body.get("private"), bool) else None, str(body.get("id", "")))
        except ValueError as e:
            _bad(e)

    @app.post("/api/manual/delete")
    def api_manual_delete(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": manual.delete(svc, str(body.get("id", "")))}

    # ---------- v0.16: checklist, bills, trips, attachments, school run, pocket money, energy, admin hour ----------
    @app.get("/api/checklist")
    def api_checklist(person: str = Depends(current_person)):
        return checklist_mod.summary(svc, agent)

    @app.get("/api/bills")
    def api_bills(person: str = Depends(current_person)):
        return bills.bills(svc)

    @app.post("/api/bills")
    def api_bill_add(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return bills.record(svc, str(body.get("provider", "")), str(body.get("kind", "other")), float(body.get("amount") or 0),
                                str(body.get("period", "monthly")), bool(body.get("direct_debit", True)),
                                str(body.get("next_date", "")))
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/bills/delete")
    def api_bill_delete(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": bills.delete(svc, str(body.get("id", "")))}

    @app.get("/api/trips")
    def api_trips(person: str = Depends(current_person)):
        return {"trips": trips.trips(svc)}

    @app.post("/api/trips")
    def api_trip_add(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            return trips.create(svc, str(body.get("name", "")), str(body.get("destination", "")), str(body.get("start", "")),
                                str(body.get("end", "")), bool(body.get("abroad")), str(body.get("travel", "car")),
                                bool(body.get("kids", True)), bool(body.get("pets")), person)
        except ValueError as e:
            _bad(e)

    @app.post("/api/trips/delete")
    def api_trip_delete(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": trips.delete(svc, str(body.get("id", "")))}

    @app.post("/api/files")
    async def api_file_upload(request: Request, for_kind: str, for_id: str, name: str = "",
                              person: str = Depends(current_person)):
        data = await request.body()
        try:
            return files.save(svc, data, request.headers.get("content-type", ""), for_kind, for_id, name)
        except ValueError as e:
            _bad(e)

    @app.get("/api/files/{fid}")
    def api_file(fid: str, person: str = Depends(current_person)):
        found = files.path_of(svc, fid)
        if not found:
            raise HTTPException(404, "Not found.")
        path, rec = found
        return FileResponse(path, media_type=rec["type"], filename=rec["name"], content_disposition_type="inline",
                            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                                     "Cache-Control": "private, max-age=3600"})

    @app.post("/api/files/{fid}/delete")
    def api_file_delete(fid: str, person: str = Depends(current_person)):
        return {"ok": files.delete(svc, fid)}

    @app.get("/api/school-run")
    def api_school_run(person: str = Depends(current_person)):
        return {"days": schoolrun.week(svc) if svc.google else []}

    @app.post("/api/school-run/claim")
    def api_school_run_claim(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            return schoolrun.claim(svc, str(body.get("date", "")), str(body.get("kind", "")), str(body.get("at", "")),
                                   str(body.get("who", "")) or name_of(person))
        except ValueError as e:
            _bad(e)

    @app.get("/api/pocket-money")
    def api_pocket(person: str = Depends(current_person)):
        kids = s.household.get("kids") or []
        return {"kids": [{"kid": k, "allowance": household.allowance(svc, k), "owed": svc.store.unpaid_totals().get(k, 0.0),
                          "goals": household.goals(svc, k)} for k in kids]}

    @app.post("/api/pocket-money/allowance")
    def api_allowance(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return {"allowance": household.set_allowance(svc, str(body.get("kid", "")), float(body.get("amount") or 0))}
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/pocket-money/goal")
    def api_goal(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return household.add_goal(svc, str(body.get("kid", "")), str(body.get("name", "")), float(body.get("target") or 0))
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/pocket-money/save")
    def api_goal_save(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return household.save_towards(svc, str(body.get("id", "")), float(body.get("amount") or 0))
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/pocket-money/goal/delete")
    def api_goal_delete(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": household.delete_goal(svc, str(body.get("id", "")))}

    @app.get("/api/energy/report")
    def api_energy_report(person: str = Depends(current_person)):
        return energy.report(svc)

    @app.get("/api/admin-hour")
    def api_admin_hour(skip: int = 0, person: str = Depends(current_person)):
        return adminhour.plan(svc, skip=max(0, min(skip, 5)))

    @app.post("/api/admin-hour/book")
    def api_admin_hour_book(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            return adminhour.book(svc, str(body.get("start", "")), [str(j) for j in body.get("jobs") or []],
                                  str(body.get("who", "")))
        except ValueError as e:
            _bad(e)

    # ---------- v0.17: check everything, backups, kids' tablet, haggling, away mode, recipes, seasonal jobs,
    # the Sunday agenda and parties ----------
    @app.post("/api/system/check")
    def api_check(body: dict = BODY, person: str = Depends(current_person)):
        return doctor.report(svc, agent, skip_ai=not body.get("ai"))

    @app.get("/api/system/backup")
    def api_backup(person: str = Depends(current_person)):
        return offsite.status(svc)

    @app.post("/api/system/backup")
    def api_backup_set(body: dict = BODY, person: str = Depends(current_person)):
        if s.demo:
            raise HTTPException(400, "The sample household isn't backed up.")
        try:
            return offsite.configure(svc, str(body.get("folder", "")), str(body.get("passphrase", "")))
        except offsite.BackupError as e:
            _bad(e)

    @app.post("/api/system/backup/run")
    def api_backup_run(person: str = Depends(current_person)):
        if s.demo:
            raise HTTPException(400, "The sample household isn't backed up.")
        return offsite.run(svc, notify_on_problem=False)

    @app.post("/api/system/backup/restore")
    async def api_backup_restore(request: Request, person: str = Depends(current_person),
                                 x_backup_passphrase: str | None = Header(default=None)):
        if s.demo:
            raise HTTPException(400, "The sample household can't restore a backup.")
        data = await request.body()
        try:
            r = offsite.stage_restore(data, x_backup_passphrase or "")
        except (offsite.BackupError, ValueError) as e:
            _bad(e)
        svc.store.log_event("restore", f"backup staged for restore by {person}")
        return r

    @app.get("/api/kids/board")
    def api_kids_board(who: str = Depends(current_kids)):
        return kidsmode.board(svc)

    @app.post("/api/kids/done")
    def api_kids_done(body: dict = BODY, who: str = Depends(current_kids)):
        try:
            return kidsmode.tick(svc, str(body.get("kid", "")), str(body.get("chore_id", "")))
        except ValueError as e:
            _bad(e)

    @app.post("/api/kids/undo")
    def api_kids_undo(body: dict = BODY, who: str = Depends(current_kids)):
        try:
            return {"ok": kidsmode.undo(svc, int(body.get("id") or 0))}
        except ValueError as e:
            _bad(e)

    @app.get("/api/haggle/bill/{bid}")
    def api_haggle_bill(bid: str, person: str = Depends(current_person)):
        try:
            return haggle.for_bill(svc, bid, name_of(person))
        except ValueError as e:
            _bad(e)

    def _haggle_from(body: dict, person: str) -> dict:
        if body.get("for") == "renewal":
            return haggle.for_renewal(svc, int(body.get("id") or 0), float(body.get("quoted") or 0),
                                      float(body.get("last_year") or 0), name_of(person))
        return haggle.for_bill(svc, str(body.get("id", "")), name_of(person))

    @app.post("/api/haggle")
    def api_haggle(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return _haggle_from(body, person)
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/haggle/draft")
    def api_haggle_draft(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            return haggle.make_draft(svc, person, _haggle_from(body, person))
        except (ValueError, TypeError, RuntimeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/haggle/todo")
    def api_haggle_todo(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            return haggle.make_todo(svc, person, _haggle_from(body, person))
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    @app.get("/api/away")
    def api_away(person: str = Depends(current_person)):
        return away.options(svc)

    @app.post("/api/trips/away")
    def api_trip_away(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return away.set_mode(svc, str(body.get("id", "")), bool(body.get("on")))
        except ValueError as e:
            _bad(e)

    @app.post("/api/meals/recipe")
    def api_recipe(body: dict = BODY, person: str = Depends(current_person)):
        try:
            return recipes.add(svc, str(body.get("url", "")), str(body.get("day", "")))
        except (ValueError, OSError) as e:
            _bad(ValueError(str(e) if isinstance(e, ValueError) else "Couldn't open that page. Check the link."))

    @app.get("/api/seasonal")
    def api_seasonal(person: str = Depends(current_person)):
        return seasonal.overview(svc)

    @app.post("/api/seasonal/add")
    def api_seasonal_add(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            return seasonal.add(svc, str(body.get("id", "")), person)
        except ValueError as e:
            _bad(e)

    @app.post("/api/seasonal/off")
    def api_seasonal_off(body: dict = BODY, person: str = Depends(current_person)):
        seasonal.set_off(svc, str(body.get("id", "")), bool(body.get("off")))
        return seasonal.overview(svc)

    @app.post("/api/seasonal/have")
    def api_seasonal_have(body: dict = BODY, person: str = Depends(current_person)):
        try:
            seasonal.set_have(svc, str(body.get("thing", "")), bool(body.get("have")))
        except ValueError as e:
            _bad(e)
        return seasonal.overview(svc)

    @app.get("/api/agenda")
    def api_agenda(person: str = Depends(current_person)):
        a = agenda.build(svc)
        return {**a, "text": agenda.text(a)}

    @app.get("/api/parties")
    def api_parties(person: str = Depends(current_person)):
        return {"parties": party.parties(svc), "kids": s.household.get("kids") or []}

    @app.post("/api/parties")
    def api_party_add(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        try:
            age = int(body["age"]) if str(body.get("age") or "").strip() else None
            return party.create(svc, str(body.get("child", "")), str(body.get("date", "")), str(body.get("start", "")),
                                str(body.get("end", "")), age, str(body.get("venue", "")), str(body.get("theme", "")), person)
        except (ValueError, TypeError) as e:
            _bad(ValueError(str(e)))

    def _party_call(fn):
        try:
            return fn()
        except (ValueError, RuntimeError) as e:
            _bad(ValueError(str(e)))

    @app.post("/api/parties/guest")
    def api_party_guest(body: dict = BODY, person: str = Depends(current_person)):
        return _party_call(lambda: party.add_guest(svc, str(body.get("id", "")), str(body.get("name", "")),
                                                   str(body.get("email", "")), str(body.get("phone", ""))))

    @app.post("/api/parties/guest/remove")
    def api_party_guest_remove(body: dict = BODY, person: str = Depends(current_person)):
        return _party_call(lambda: party.remove_guest(svc, str(body.get("id", "")), str(body.get("name", ""))))

    @app.post("/api/parties/rsvp")
    def api_party_rsvp(body: dict = BODY, person: str = Depends(current_person)):
        return _party_call(lambda: party.rsvp(svc, str(body.get("id", "")), str(body.get("name", "")),
                                              str(body.get("answer", "")), body.get("gift")))

    @app.post("/api/parties/invites")
    def api_party_invites(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        return _party_call(lambda: party.make_invites(svc, str(body.get("id", "")), person))

    @app.post("/api/parties/food")
    def api_party_food(body: dict = BODY, person: str = Depends(current_person)):
        need_google()
        return _party_call(lambda: party.food_to_shopping(svc, str(body.get("id", ""))))

    @app.post("/api/parties/delete")
    def api_party_delete(body: dict = BODY, person: str = Depends(current_person)):
        return {"ok": party.delete(svc, str(body.get("id", "")))}

    @app.get("/api/who-home")
    def api_who_home(start: str, duration_min: int = 120, person: str = Depends(current_person)):
        from datetime import datetime as _dt
        try:
            a = _dt.fromisoformat(start)
        except ValueError as e:
            _bad(e)
        return scheduling.who_free(svc, a, a + timedelta(minutes=max(15, duration_min)), prefer=name_of(person))

    # ---------- this computer: updates, start with the computer, keys ----------
    from . import system
    system.add_routes(app, svc, current_person)

    # ---------- key-based routes for scripts, Home Assistant and NFC ----------
    @app.get("/health")
    def health():
        return {"ok": not problems, "version": __version__, "problems": problems, "ai": agent.active_provider_name(),
                "home_assistant": svc.ha is not None,
                "google": [a["person"] for a in svc.google.status() if a["connected"]]
                if svc.google is not None and hasattr(svc.google, "status") else bool(svc.google),
                "notifications": svc.notifier is not None, "pending": len(svc.store.list_pending())}

    @app.get("/provider", dependencies=[Depends(require_key)])
    def get_provider():
        return {"provider": agent.active_provider_name()}

    @app.post("/provider", dependencies=[Depends(require_key)])
    def set_provider(body: ProviderBody):
        return _switch(body.provider)

    @app.post("/ask", dependencies=[Depends(require_key)])
    def ask(body: AskBody):
        return {"reply": agent.run(body.text, deep=body.deep, source="ask")}

    # ---------- voice: Google speakers (via Home Assistant) and Home Assistant Assist ----------
    @app.post("/api/voice/command", dependencies=[Depends(require_key)])
    def voice_command(body: dict = BODY):
        try:
            return voice.run_command(svc, str(body.get("command", "")), str(body.get("speaker", "")))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/voice/ask", dependencies=[Depends(require_key)])
    def voice_ask(body: dict = BODY):
        return voice.ask(svc, agent, str(body.get("text", "")), str(body.get("person", "")))

    @app.get("/api/voice")
    def voice_settings(request: Request, person: str = Depends(current_person)):
        speakers = []
        if svc.ha:
            try:
                speakers = [{"id": x["entity_id"], "name": x.get("attributes", {}).get("friendly_name") or x["entity_id"]}
                            for x in svc.ha.states("media_player")]
            except Exception as e:
                log.warning("speakers unreadable: %s", e)
        c = voice.cfg(svc)
        base = s.behaviour.get("voice", {}).get("lifeos_url_for_ha") or f"http://{request.url.hostname}:{request.url.port or 8800}"
        return {"ha": bool(svc.ha), "speakers": speakers, "speaker": c.get("speaker", ""), "tts_entity": c.get("tts_entity", ""),
                "routines": voice.routine_steps(), "package": voice.ha_package(svc, base), "sentences": voice.ha_sentences(), "sentences_all": voice.ha_sentences(catch_all=True),
                "announce": voice.announcements(svc)}

    @app.post("/api/voice/settings")
    def voice_save(body: dict = BODY, person: str = Depends(current_person)):
        for k in ("speaker", "tts_entity"):
            if k in body:
                v = str(body[k] or "").strip()
                if v and not re.fullmatch(r"(media_player|tts)\.[a-z0-9_]+", v):
                    raise HTTPException(400, "That isn't a Home Assistant entity.")
                svc.store.set_state(f"voice_{k}", v)
        return {"speaker": voice.cfg(svc).get("speaker", ""), "tts_entity": voice.cfg(svc).get("tts_entity", "")}

    @app.post("/api/voice/test")
    def voice_test(person: str = Depends(current_person)):
        ok = voice.speak(svc, f"Hello from Life OS. {voice.answer(svc, 'dinner')}", force=True)
        if not ok:
            raise HTTPException(400, "Couldn't speak: check Home Assistant is connected and a speaker is chosen.")
        return {"ok": True}

    @app.api_route("/nfc/{tag_id}", methods=["GET", "POST"], dependencies=[Depends(require_key)])
    def nfc(tag_id: str, who: str | None = None):
        return handle_tag(svc, tag_id, who=who, agent=agent)

    @app.get("/approvals", dependencies=[Depends(require_key)])
    def pending():
        return svc.store.list_pending()

    def page(title: str, body_html: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='en-GB'><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width, initial-scale=1'><title>Life OS</title><body style='font:1.25rem/1.5 "
            "system-ui;max-width:32rem;margin:auto;padding:1.5rem;color-scheme:light dark'>"
            f"<h1>{escape(title)}</h1>{body_html}<p><a href='/app/#today'>Open Life OS</a></p></body></html>",
            status_code=status)

    @app.get("/approvals/{pid}/{decision}")
    def confirm_page(pid: str, decision: str, token: str = ""):
        """Opening a link never acts by itself (link previews would trigger it); it asks for a button press."""
        if decision not in ("approve", "reject") or not approvals.verify(s.signing_secret, pid, decision, token):
            return page("Link not valid", "<p>This link is not valid.</p>", 403)
        item = svc.store.get_pending(pid)
        if not item or item["status"] != "pending":
            state = item["status"] if item else "gone"
            return page("Nothing to do", f"<p>This request is {escape(state)}.</p>")
        verb = "Approve" if decision == "approve" else "Reject"
        return page(f"{verb}?", f"<p style='white-space:pre-wrap'>{escape(item['reason'] or item['tool'])}</p>"
                    f"<form method='post'><button style='font:inherit;font-weight:700;width:100%;min-height:3.5rem;"
                    f"border-radius:14px;border:0;background:#0a5c53;color:#fff'>{verb}</button></form>")

    @app.post("/approvals/{pid}/{decision}")
    def decide(pid: str, decision: str, token: str = ""):
        if decision not in ("approve", "reject"):
            raise HTTPException(404)
        if not approvals.verify(s.signing_secret, pid, decision, token):
            raise HTTPException(status_code=403, detail="bad token")
        result = approvals.decide(svc, pid, decision)
        if svc.notifier and result.get("status") == "failed":
            svc.notifier.push(f"Approved action failed: {result.get('error')}", title="Life OS error")
        word = {"approved": "Done", "rejected": "Cancelled"}.get(result.get("status"))
        return page(word or "Not done", "" if word else f"<p>{escape(result.get('error', 'Error'))}</p>")

    @app.post("/loops/{name}/run", dependencies=[Depends(require_key)])
    def run_now(name: str):
        loop = s.loops.get(name)
        if not loop:
            raise HTTPException(404, f"no loop {name}")
        return {"reply": run_loop(agent, name, loop)}

    @app.get("/events", dependencies=[Depends(require_key)])
    def events(limit: int = 50):
        return svc.store.recent_events(limit)

    # ---------- v0.18: deliveries, bins, babysitter, date night, kids' extras, cleaning, car costs, live test ----------
    from types import SimpleNamespace

    from . import routes18
    routes18.add_routes(app, svc, agent, SimpleNamespace(
        current_person=current_person, current_kids=current_kids, current_viewer=current_viewer,
        require_key=require_key, name_of=name_of, need_google=need_google))

    # ---------- v0.19: Christmas, phone access, meters, school kit, garden, fridge, week sheet, contents, documents ----------
    from . import routes19
    routes19.add_routes(app, svc, agent, SimpleNamespace(
        current_person=current_person, name_of=name_of, need_google=need_google))

    # ---------- v0.20: set-up check, brain dump, letters, email to-dos, tomorrow, freezer, boxes, guests, broken, routines ----------
    from . import routes20
    routes20.add_routes(app, svc, agent, SimpleNamespace(
        current_person=current_person, current_kids=current_kids, need_google=need_google))
    return app


def main():
    import uvicorn

    uvicorn.run("lifeos.app:create_app", factory=True, host="0.0.0.0", port=8800, proxy_headers=True,
                forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
