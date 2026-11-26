"""Runs the scheduled loops from config/settings.yaml (morning brief, inbox triage, weekly review...)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("lifeos.scheduler")


QUIET = ("no action needed", "nothing to report", "")


def run_loop(agent, name: str, loop: dict) -> str:
    try:
        text = agent.run(loop["prompt"], deep=loop.get("model") == "deep", source=f"loop:{name}")
        if text.strip().lower().rstrip(".") not in QUIET:
            agent.svc.store.add_brief(name, text.strip())  # shown on the app's Today screen
        return text
    except Exception as e:
        log.exception("loop %s failed", name)
        agent.svc.store.log_event(f"loop_failed:{name}", str(e))
        if agent.svc.notifier:
            agent.svc.notifier.push(f"Loop '{name}' failed: {e}", title="Life OS error", priority=4)
        return ""


def start_scheduler(agent) -> BackgroundScheduler:
    s = agent.svc.settings
    sched = BackgroundScheduler(timezone=s.tz)
    for name, loop in s.loops.items():
        sched.add_job(run_loop, CronTrigger.from_crontab(loop["cron"], timezone=s.tz),
                      args=[agent, name, loop], id=name, max_instances=1, coalesce=True,
                      misfire_grace_time=3600)
        log.info("scheduled %s at '%s'", name, loop["cron"])
    # Renewal reminders every morning at 07:05 (rules, no AI cost).
    sched.add_job(renewals_job, CronTrigger(hour=7, minute=5, timezone=s.tz), args=[agent.svc],
                  id="renewals", max_instances=1, coalesce=True)
    # Send notifications held during quiet hours, as soon as they end.
    if hasattr(agent.svc.notifier, "flush"):
        sched.add_job(agent.svc.notifier.flush, CronTrigger(minute="*/10", timezone=s.tz), id="quiet_flush",
                      max_instances=1, coalesce=True)
    # Sort anything typed straight into a Google Tasks "Inbox" list.
    sched.add_job(inbox_job, CronTrigger(minute="*/15", timezone=s.tz), args=[agent], id="inbox_sweep",
                  max_instances=1, coalesce=True)
    # Receipts and school emails: read every two hours in the day (only matching emails, each once).
    sched.add_job(mail_job, CronTrigger(minute=40, hour="7-21/2", timezone=s.tz), args=[agent],
                  id="mail_reader", max_instances=1, coalesce=True)
    # Every morning: needs Life OS sees coming move onto the lists (very likely ones) or get a reminder (likely ones).
    sched.add_job(predictions_job, CronTrigger(hour=7, minute=20, timezone=s.tz), args=[agent.svc],
                  id="predictions", max_instances=1, coalesce=True)
    # Sunday evening: nudge about jobs that have sat for a month with nothing under way.
    sched.add_job(stalled_job, CronTrigger(day_of_week="sun", hour=18, minute=10, timezone=s.tz), args=[agent.svc],
                  id="stalled_jobs", max_instances=1, coalesce=True)
    # Gemini and the Google apps: pick up to-dos Gemini added, notice emails sent from Gmail drafts, act on ticks.
    sched.add_job(bridge_job, CronTrigger(minute="*/5", timezone=s.tz), args=[agent], id="gemini_bridge",
                  max_instances=1, coalesce=True)
    # Watch for replies from tradespeople, and chase silence (every 30 minutes, 7am to 10pm).
    sched.add_job(requests_job, CronTrigger(minute="5,35", hour="7-22", timezone=s.tz), args=[agent.svc],
                  id="trade_requests", max_instances=1, coalesce=True)
    # Housekeeping at 03:17: expire old approvals, keep 90 days of logs and conversations.
    sched.add_job(housekeeping, CronTrigger(hour=3, minute=17, timezone=s.tz), args=[agent.svc],
                  id="housekeeping", max_instances=1, coalesce=True)
    # v0.15: car dates weekly, subscription nudges daily, childcare gaps on Sunday evening, appliance cycles every
    # 10 minutes, cheap-time plugs every 5 minutes, and a morning check that whoever's in for a visit still is.
    from . import (adminhour, agenda, away, bins, carcosts, care, cars, childcare, cleaning, deliveries, energy, garden,
                   household, meters, money, offsite, party, schoolkit, schoolrun, seasonal, trades, trips, weeksheet)
    v = agent.svc
    for job_id, fn, trig in (
            ("cars", _safe("cars", lambda: cars.weekly(v)), CronTrigger(day_of_week="mon", hour=6, minute=40, timezone=s.tz)),
            ("subscriptions", _safe("subscriptions", lambda: money.nudges(v)), CronTrigger(hour=8, minute=20, timezone=s.tz)),
            ("childcare", _safe("childcare", lambda: childcare.remind(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=20, timezone=s.tz)),
            ("care", _safe("care", lambda: care.poll(v)), IntervalTrigger(minutes=10)),
            ("energy", _safe("energy", lambda: energy.run_due(v)), IntervalTrigger(minutes=5)),
            ("visits", _safe("visits", lambda: trades.visit_clashes(v)), CronTrigger(hour=7, minute=35, timezone=s.tz)),
            # v0.16
            ("trip_morning", _safe("trips", lambda: trips.morning(v)), CronTrigger(hour=7, minute=5, timezone=s.tz)),
            ("allowance", _safe("allowance", lambda: household.pay_allowances(v)),
             CronTrigger(day_of_week="sat", hour=8, minute=0, timezone=s.tz)),
            ("school_run", _safe("school run", lambda: schoolrun.sunday_note(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=25, timezone=s.tz)),
            ("energy_report", _safe("energy report", lambda: energy.weekly_note(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=30, timezone=s.tz)),
            ("admin_hour", _safe("admin hour", lambda: adminhour.sunday(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=40, timezone=s.tz)),
            # v0.17
            ("away", _safe("away mode", lambda: away.tick(v)), IntervalTrigger(minutes=10)),
            ("offsite_backup", _safe("backup", lambda: offsite.run(v)), CronTrigger(hour=2, minute=40, timezone=s.tz)),
            ("seasonal", _safe("seasonal jobs", lambda: seasonal.monthly(v)), CronTrigger(day=1, hour=8, minute=15, timezone=s.tz)),
            ("agenda", _safe("Sunday agenda", lambda: agenda.sunday(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=50, timezone=s.tz)),
            ("party_replies", _safe("party replies", lambda: party.check_replies(v)),
             CronTrigger(minute="10,40", hour="7-22", timezone=s.tz)),
            # v0.18
            ("deliveries_read", _safe("deliveries", lambda: deliveries.read_emails(v, agent)),
             CronTrigger(minute=25, hour="8,11", timezone=s.tz)),
            ("deliveries_nudge", _safe("parcel nudge", lambda: deliveries.nudge(v)),
             CronTrigger(minute=45, hour="8,12", timezone=s.tz)),
            ("bins", _safe("bins", lambda: bins.evening(v)), CronTrigger(minute="*/10", hour="16-21", timezone=s.tz)),
            ("vacuum", _safe("vacuum", lambda: cleaning.vacuum_tick(v)), CronTrigger(minute="*/15", hour="8-18", timezone=s.tz)),
            ("cleaning", _safe("cleaning rota", lambda: cleaning.sunday(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=55, timezone=s.tz)),
            ("car_costs", _safe("car costs", lambda: carcosts.weekly(v)),
             CronTrigger(day_of_week="mon", hour=6, minute=45, timezone=s.tz)),
            # v0.19
            ("school_kit", _safe("school kit", lambda: schoolkit.evening(v)),
             CronTrigger(day_of_week="sun-thu", hour=18, minute=30, timezone=s.tz)),
            ("garden", _safe("garden", lambda: garden.morning(v)), CronTrigger(hour=7, minute=30, timezone=s.tz)),
            ("meters", _safe("meter readings", lambda: meters.monthly(v)), CronTrigger(hour=9, minute=0, timezone=s.tz)),
            ("week_sheet", _safe("week sheet", lambda: weeksheet.sunday(v)),
             CronTrigger(day_of_week="sun", hour=18, minute=45, timezone=s.tz))):
        sched.add_job(fn, trig, id=job_id, max_instances=1, coalesce=True)
    # Spoken announcements on a Google speaker (voice.announce in settings.yaml), e.g. the brief at 07:15 on weekdays.
    from . import voice
    for i, item in enumerate(voice.announcements(v)):
        try:
            hh, mm = (int(x) for x in str(item["at"]).split(":", 1))
            days = str(item.get("days", "*")).replace("weekdays", "mon-fri").replace("daily", "*")
            sched.add_job(_safe(f"announce {item['say']}", lambda it=item: voice.announce(v, it)),
                          CronTrigger(day_of_week=days, hour=hh, minute=mm, timezone=s.tz), id=f"announce_{i}",
                          max_instances=1, coalesce=True)
        except (ValueError, KeyError) as e:
            log.warning("announcement %s skipped: %s", item, e)
    # Updates: a notification when a new version is ready (07:50), and installed overnight if allowed (03:40).
    sched.add_job(updates_job, CronTrigger(hour=7, minute=50, timezone=s.tz), args=[agent.svc],
                  id="updates", max_instances=1, coalesce=True)
    sched.add_job(overnight_update_job, CronTrigger(hour=3, minute=40, timezone=s.tz), args=[agent.svc],
                  id="overnight_update", max_instances=1, coalesce=True)
    sched.start()
    return sched


def _safe(name: str, fn):
    def run():
        try:
            fn()
        except Exception:
            log.exception("%s job failed", name)
    return run


def updates_job(svc) -> None:
    from . import updater
    updater.daily(svc)


def overnight_update_job(svc) -> None:
    """Only when Settings > Updates > 'Install updates overnight' is on, and only from the update feed."""
    from . import desktop, system, updater
    if not system.auto_updates(svc) or not desktop.controller:
        return
    st = updater.check(svc, force=True)
    if st["available"]:
        updater.download_and_install(svc)
        desktop.controller.request_restart()


def inbox_job(agent) -> None:
    from .todos import sweep_inbox
    try:
        sweep_inbox(agent.svc, agent)
    except Exception:
        log.exception("inbox sweep failed")


def mail_job(agent) -> None:
    from . import mailreader
    if agent.svc.google:
        mailreader.run(agent.svc, agent)


def predictions_job(svc) -> None:
    from . import activity
    if svc.google:
        activity.daily(svc)


def stalled_job(svc) -> None:
    from . import todos
    if svc.google:
        todos.stalled_nudge(svc)


def bridge_job(agent) -> None:
    from . import bridge
    if agent.svc.google:
        bridge.run(agent.svc, agent)


def requests_job(svc) -> None:
    from .trades import check_requests
    try:
        if svc.google:
            check_requests(svc)
    except Exception:
        log.exception("checking tradespeople replies failed")


def renewals_job(svc) -> None:
    from .household import check_renewals
    try:
        check_renewals(svc)
    except Exception:
        log.exception("renewal check failed")


def housekeeping(svc, keep_backups: int = 14) -> None:
    from datetime import date
    from pathlib import Path
    try:
        svc.store.expire_pending()
        svc.store.prune(days=90)
        db = svc.settings.db_path
        if db and db != ":memory:":
            folder = Path(db).parent / "backups"
            svc.store.backup_to(str(folder / f"lifeos-{date.today().isoformat()}.db"))
            for old in sorted(folder.glob("lifeos-*.db"))[:-keep_backups]:
                old.unlink(missing_ok=True)
    except Exception:
        log.exception("housekeeping failed")
