"""Check everything: tests every real connection and says in plain English what to fix.

Used by Settings > Check everything (/api/system/check) and by scripts/doctor.py.
"""
from __future__ import annotations

import json
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "  OK  ", WARN: " WARN ", FAIL: " FAIL "}


def configured_entities(settings) -> list[str]:
    """Every Home Assistant entity the settings refer to, so typos are caught before they bite."""
    ids = list(settings.home_status)
    for a in settings.quick_actions:
        if a.get("entity_id") and a["entity_id"] != "all":
            ids.append(a["entity_id"])
    for tag in settings.tags.values():
        for action in tag.get("actions", []):
            params = action.get("ha_service") or {}
            if params.get("entity_id") and params["entity_id"] != "all":
                ids.append(params["entity_id"])
    weather = (settings.behaviour.get("wall") or {}).get("weather_entity")
    if weather:
        ids.append(weather)
    return sorted(set(ids))


def check_config(svc) -> list[tuple[str, str]]:
    from .config import setup_problems
    probs = setup_problems(svc.settings)
    return [(FAIL, p) for p in probs] or [(OK, "Settings file (.env) looks complete")]


def check_home_assistant(svc) -> list[tuple[str, str]]:
    if not svc.ha:
        return [(WARN, "Home Assistant not configured (HA_URL / HA_TOKEN). Home controls are off until it is.")]
    try:
        states = svc.ha.states()
    except Exception as e:
        hint = " The token is wrong or expired: make a new long-lived token." if "401" in str(e) else \
            " Check HA_URL is reachable from this machine."
        return [(FAIL, f"Can't talk to Home Assistant: {e}.{hint}")]
    known = {s["entity_id"] for s in states}
    out = [(OK, f"Home Assistant connected ({len(known)} devices and sensors)")]
    missing = [e for e in configured_entities(svc.settings) if e not in known]
    if missing:
        out.append((WARN, "These names in config/settings.yaml don't exist in Home Assistant (rename them to "
                          "match Settings > Entities): " + ", ".join(missing)))
    return out


def check_google(svc) -> list[tuple[str, str]]:
    s = svc.settings
    out = []
    client = Path(s.google_client_secret_file)
    if not client.exists():
        out.append((FAIL, f"Google sign-in not set up: {client} is missing (README step 4)."))
    else:
        try:
            web = json.loads(client.read_text()).get("web")
            if not web:
                out.append((FAIL, "The Google OAuth client is not a 'Web application' type. Create one of that type."))
            elif f"{s.public_url}/auth/google/callback" not in web.get("redirect_uris", []):
                out.append((FAIL, f"Add this exact redirect URI to the Google OAuth client: "
                                  f"{s.public_url}/auth/google/callback"))
            else:
                out.append((OK, "Google sign-in client is set up correctly"))
        except Exception as e:
            out.append((FAIL, f"Can't read {client}: {e}"))
    accounts = svc.google
    for st in accounts.status() if accounts is not None else []:
        if not st["connected"]:
            out.append((WARN, f"{st['name']}'s Google account isn't connected yet (app: Settings > Google accounts)."))
            continue
        g = accounts.get(st["person"])
        try:
            lists = g.list_names()
            events = g.events(days=1)
            msg = f"{st['name']}: Google connected ({len(lists)} task lists, {len(events)} events today"
            if st["gmail"]:
                g.search_mail("newer_than:1d", limit=1)
                msg += ", Gmail allowed"
            out.append((OK, msg + ")"))
        except Exception as e:
            from .aierrors import explain_google
            hint = " Reconnect in the app (Settings > Google accounts)." if "401" in str(e) and "invalid_grant" not in str(e) \
                else ""
            out.append((FAIL, f"{st['name']}: {explain_google(e)}{hint}"))
    return out


def check_ai(agent, skip: bool) -> list[tuple[str, str]]:
    name = agent.active_provider_name()
    if skip:
        return [(WARN, f"AI check skipped ({name.title()})")]
    try:
        reply = agent.provider.chat("Reply with exactly: OK", [], "Say OK", lambda n, a: ("", True))
        return [(OK, f"{name.title()} answered ({reply.strip()[:20]!r})")]
    except Exception as e:
        from . import aierrors
        return [(FAIL, f"{name.title()} didn't answer: {aierrors.explain(e, name)}")]


def check_notifications(svc) -> list[tuple[str, str]]:
    out = []
    if svc.pusher:
        subs = svc.store.subscriptions()
        people = sorted({s["person"] for s in subs})
        if subs:
            out.append((OK, f"In-app notifications ready ({len(subs)} phone(s): {', '.join(people)})"))
        else:
            out.append((WARN, "No phone has turned on notifications yet (app: Settings > Notifications)."))
    else:
        why = getattr(svc, "push_problem", "") or "is pywebpush installed?"
        out.append((FAIL, f"In-app notifications couldn't start ({why}). Try Settings > Updates and this computer > Restart Life OS."))
    if svc.settings.ntfy_topic:
        out.append((OK, f"ntfy also configured (topic {svc.settings.ntfy_topic[:6]}...)"))
    return out


def check_public_url(svc) -> list[tuple[str, str]]:
    import httpx
    url = svc.settings.public_url
    if not url.startswith("https://"):
        return [(FAIL, "LIFEOS_PUBLIC_URL must be https (Tailscale: tailscale serve --bg 8800).")]
    try:
        r = httpx.get(f"{url}/health", timeout=8)
        return [(OK, f"Phones can reach Life OS at {url}")] if r.status_code == 200 else \
            [(WARN, f"{url} answered {r.status_code}; is Life OS running behind it?")]
    except Exception as e:
        return [(WARN, f"Couldn't reach {url} from this machine ({type(e).__name__}). If Life OS isn't running yet, "
                       "start it and run the doctor again.")]


def check_voice(svc) -> list[tuple[str, str]]:
    from . import voice
    speaker = voice.cfg(svc).get("speaker")
    if not svc.ha:
        return []
    if not speaker:
        return [(WARN, "No speaker chosen for Life OS to talk on (Settings > Voice and speakers).")]
    try:
        st = svc.ha.state(speaker)
    except Exception as e:
        return [(WARN, f"Couldn't check the speaker {speaker}: {e}")]
    if (st or {}).get("state") in (None, "unavailable", "unknown"):
        return [(WARN, f"The speaker {speaker} isn't answering in Home Assistant (is it plugged in?).")]
    return [(OK, f"Speaker ready: {(st.get('attributes') or {}).get('friendly_name', speaker)}")]


def check_bridge(svc) -> list[tuple[str, str]]:
    if not svc.google:
        return []
    from . import bridge
    last = bridge.status(svc).get("last") or {}
    at = last.get("at", "")
    if not at:
        return [(WARN, "The Gemini and Google Tasks check hasn't run yet (it runs every 5 minutes while Life OS is on).")]
    errors = last.get("errors") or []
    if errors:
        return [(WARN, f"The last Gemini and Google Tasks check had a problem: {errors[0]}")]
    return [(OK, "Gemini and Google Tasks are being checked every 5 minutes")]


def check_backups(svc) -> list[tuple[str, str]]:
    import time as _t

    from . import offsite
    st = offsite.status(svc)
    if not st["folder"]:
        return [(WARN, "No copy of Life OS is kept off this computer yet (Settings > Backups).")]
    last = st.get("last") or {}
    if not last:
        return [(WARN, "The off-computer backup is set up but hasn't run yet. Tap Back up now.")]
    if not last.get("ok"):
        return [(FAIL, f"The last backup didn't work: {last.get('detail', 'unknown problem')}")]
    if _t.time() - float(last.get("at", 0)) > 3 * 86400:
        return [(WARN, "The last good backup is more than 3 days old (is the backup folder or USB stick there?).")]
    return [(OK, f"Backed up and checked: {last.get('when', '')}")]


def check_disk(svc) -> list[tuple[str, str]]:
    import shutil

    from . import paths
    free = shutil.disk_usage(paths.home()).free
    if free < 1_000_000_000:
        return [(WARN, f"Only {free // 1_000_000} MB free on this computer's disk.")]
    return [(OK, f"{free // 1_000_000_000} GB free on this computer's disk")]


# Where in the app each section is fixed
LINKS = {"Settings": "#settings?sec=computer", "Home Assistant": "#settings?sec=computer", "Google": "#settings?sec=google",
         "Assistant": "#settings?sec=computer", "Notifications": "#settings?sec=notify", "Phone access": "#settings?sec=computer",
         "Voice": "#settings?sec=voice", "Gemini": "#settings?sec=bridge", "Backups": "#settings?sec=backups",
         "This computer": "#settings?sec=computer"}


def run(svc, agent, skip_ai: bool = False, check_url: bool = True) -> list[tuple[str, str, str]]:
    sections = [("Settings", lambda: check_config(svc)), ("Home Assistant", lambda: check_home_assistant(svc)),
                ("Google", lambda: check_google(svc)), ("Assistant", lambda: check_ai(agent, skip_ai)),
                ("Notifications", lambda: check_notifications(svc)), ("Voice", lambda: check_voice(svc)),
                ("Gemini", lambda: check_bridge(svc)), ("Backups", lambda: check_backups(svc)),
                ("This computer", lambda: check_disk(svc))]
    if check_url:
        sections.append(("Phone access", lambda: check_public_url(svc)))
    results = []
    for name, fn in sections:
        try:
            for status, msg in fn():
                results.append((name, status, msg))
        except Exception as e:
            results.append((name, FAIL, f"check crashed: {e}"))
    return results




def report(svc, agent, skip_ai: bool = False) -> dict:
    """For the app: each result with where to fix it, and a one-line summary."""
    if svc.settings.demo:
        return {"results": [{"section": "Sample household", "status": OK, "link": "#settings?sec=computer",
                             "text": "This is the sample household, so its connections are pretend. Once Life OS is set "
                                     "up for your household this tests the real ones."}],
                "fails": 0, "warns": 0, "summary": "Nothing to check in the sample household."}
    rows = [{"section": sec, "status": st, "text": msg, "link": LINKS.get(sec, "#settings")}
            for sec, st, msg in run(svc, agent, skip_ai=skip_ai)]
    fails = sum(r["status"] == FAIL for r in rows)
    warns = sum(r["status"] == WARN for r in rows)
    summary = "All good." if not fails and not warns else \
        f"{fails} problem{'s' if fails != 1 else ''} to fix, {warns} thing{'s' if warns != 1 else ''} worth a look." if fails \
        else f"Working. {warns} thing{'s' if warns != 1 else ''} worth a look."
    return {"results": rows, "fails": fails, "warns": warns, "summary": summary}
