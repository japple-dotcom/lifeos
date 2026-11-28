"""Settings > Google accounts > Check the Google set-up: each step Google needs, checked live where it can be, with a
direct link (built from the project number in the client ID) to fix what's missing.

Google Cloud can't be asked whether the redirect address or the test users are right, so those steps say exactly what
to put where; whether each service (Tasks, Calendar, Gmail) is switched on is found by trying it once per account.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("lifeos.googlesetup")
OK, TODO, PROBLEM, UNKNOWN = "ok", "todo", "problem", "unknown"
SERVICES = (("tasks", "Google Tasks", "tasks.googleapis.com"), ("calendar", "Google Calendar", "calendar-json.googleapis.com"),
            ("gmail", "Gmail", "gmail.googleapis.com"))


def _client(settings) -> dict:
    """What's in the sign-in file: its type and client ID (never the secret)."""
    p = Path(settings.google_client_secret_file)
    if not p.is_file():
        return {"present": False}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"present": True, "readable": False}
    kind = "web" if "web" in data else "installed" if "installed" in data else ""
    cid = str((data.get(kind) or {}).get("client_id", "")) if kind else ""
    m = re.match(r"(\d{6,20})-", cid)
    return {"present": True, "readable": True, "kind": kind, "client_id": cid, "project": m.group(1) if m else "",
            "has_secret": bool(kind and (data.get(kind) or {}).get("client_secret"))}


def links(project: str) -> dict:
    q = f"?project={project}" if project else ""
    return {"clients": f"https://console.cloud.google.com/auth/clients{q}",
            "audience": f"https://console.cloud.google.com/auth/audience{q}",
            **{key: f"https://console.cloud.google.com/apis/library/{api}{q}" for key, _, api in SERVICES}}


def _classify(e: Exception) -> str:
    low = str(e).lower()
    if "accessnotconfigured" in low or "has not been used in project" in low or "it is disabled" in low:
        return "off"
    if "invalid_grant" in low or "expired or revoked" in low or "401" in low:
        return "signin"
    if "insufficient" in low and "scope" in low:
        return "scope"
    return "error"


def _probe(g, key: str):
    if key == "tasks":
        return g.list_names()
    if key == "calendar":
        return g.events(days=1)
    return g.search_mail("newer_than:1d", limit=1)


def check(svc) -> dict:
    s = svc.settings
    c = _client(s)
    project = c.get("project", "")
    ln = links(project)
    redirect = f"{s.public_url}/auth/google/callback"
    steps: list[dict] = []

    def step(key, title, status, detail, link="", link_text="", copy=""):
        steps.append({"key": key, "title": title, "status": status, "detail": detail, "link": link,
                      "link_text": link_text, "copy": copy})

    https = s.public_url.startswith("https://")
    local = s.public_url.startswith(("http://localhost", "http://127.0.0.1"))
    step("address", "An address Google can send people back to",
         OK if https or local else PROBLEM,
         f"Life OS is at {s.public_url}." + ("" if https else " That works for connecting on this computer only; set up "
                                             "phone access (Updates and this computer) for phones."))
    if not c["present"]:
        step("client", "The Google sign-in file", TODO,
             "In Google Cloud, create a client of type Web application, then add its file below (Add the Google sign-in "
             "file).", ln["clients"], "Open Google Cloud clients")
    elif not c.get("readable") or not c.get("kind"):
        step("client", "The Google sign-in file", PROBLEM, "The saved file can't be read. Add it again below.",
             ln["clients"], "Open Google Cloud clients")
    elif c["kind"] != "web":
        step("client", "The Google sign-in file", PROBLEM,
             "It's for a Desktop app client, which can't send phones back to Life OS. Create a client of type Web "
             "application and add its file below.", ln["clients"], "Open Google Cloud clients")
    elif not c["has_secret"]:
        step("client", "The Google sign-in file", PROBLEM, "The file has no client secret in it. In Google Cloud, open "
             "the client, add a secret and download the file again.", ln["clients"], "Open Google Cloud clients")
    else:
        step("client", "The Google sign-in file", OK, f"Web application client {c['client_id'].split('-')[0]}... saved.")
    step("redirect", "The redirect address in Google Cloud", UNKNOWN,
         "Open your client in Google Cloud and check this is under Authorised redirect URIs (not JavaScript origins). "
         "Google won't tell Life OS, so it's worth a look if connecting says redirect_uri_mismatch.",
         ln["clients"], "Open the client", copy=redirect)
    step("testers", "Who's allowed to connect", UNKNOWN,
         "While the app is in Testing, add each person's Gmail address under Audience, Test users. Once everyone's "
         "connected, press Publish app there, or Google signs you out every 7 days.", ln["audience"], "Open Audience")

    accounts = svc.google
    statuses = accounts.status() if accounts is not None else []
    connected = [st for st in statuses if st["connected"]]
    for st in statuses:
        if not st["connected"]:
            step(f"account:{st['person']}", f"{st['name']}'s Google account", TODO,
                 "Not connected yet: press Connect for them below.")
    results: dict[str, dict[str, str]] = {key: {} for key, _, _ in SERVICES}
    for st in connected:
        g = accounts.get(st["person"])
        for key, _, _ in SERVICES:
            if key == "gmail" and not st["gmail"]:
                results[key][st["name"]] = "not_allowed"
                continue
            try:
                _probe(g, key)
                results[key][st["name"]] = "on"
            except Exception as e:  # noqa: BLE001 - every Google error is reported, none stops the check
                results[key][st["name"]] = _classify(e)
                log.info("google set-up check %s for %s: %s", key, st["person"], e)
    for key, name, _ in SERVICES:
        r = results[key]
        if not connected:
            step(key, f"{name} switched on", UNKNOWN, "Switch it on in Google Cloud (Enable). Life OS can check it once "
                 "someone's connected.", ln[key], f"Open {name}")
        elif "off" in r.values():
            step(key, f"{name} switched on", PROBLEM, "It's switched off in your Google Cloud project. Click Enable, "
                 "wait two minutes and check again.", ln[key], f"Switch on {name}")
        elif "signin" in r.values():
            who = ", ".join(n for n, v in r.items() if v == "signin")
            step(key, f"{name} switched on", PROBLEM, f"Google says {who}'s sign-in has run out. Press Reconnect below.")
        elif "error" in r.values() or "scope" in r.values():
            who = ", ".join(n for n, v in r.items() if v in ("error", "scope"))
            step(key, f"{name} switched on", PROBLEM, f"{name} didn't answer for {who}. Try Reconnect, ticking every "
                 "box on Google's screen.", ln[key], f"Open {name}")
        elif r and all(v == "not_allowed" for v in r.values()):
            step(key, f"{name} switched on", TODO, "Nobody has allowed Gmail yet. It's optional: it lets Life OS read "
                 "receipts and school emails and write drafts. Reconnect and tick Gmail to add it.")
        else:
            step(key, f"{name} switched on", OK, "Working for " + ", ".join(n for n, v in r.items() if v == "on") + ".")
    bad = [x for x in steps if x["status"] == PROBLEM]
    todo = [x for x in steps if x["status"] == TODO]
    summary = (f"{len(bad)} thing{'s' if len(bad) != 1 else ''} to fix" if bad else
               f"{len(todo)} thing{'s' if len(todo) != 1 else ''} still to do" if todo else
               "Everything Life OS can check is working")
    return {"steps": steps, "project": project, "redirect": redirect, "summary": summary, "ok": not bad and not todo}
