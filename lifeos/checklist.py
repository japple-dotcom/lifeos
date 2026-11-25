"""The first real day: everything that makes Life OS useful, ticked off as it's done (worked out live, not stored).

Settings > Getting set up shows the whole list; Today shows the next few until the essentials are done.
"""
from __future__ import annotations


def items(svc, agent=None, person: str = "") -> list[dict]:
    s = svc.settings
    hh = s.household
    names = hh.get("names") or {}
    adults = hh.get("adults") or []
    kids = hh.get("kids") or []
    google_people = list(getattr(svc.google, "people", {}) or {}) if svc.google else []
    gmail = any(getattr(g, "has_gmail", False) for g in (svc.google.people.values() if svc.google else []))
    provider = getattr(agent, "provider", None) is not None if agent is not None else True
    school = (s.behaviour.get("school") or {}).get("senders") or []
    contacts = svc.store.contacts()
    manual_n = len(svc.store.records("manual"))
    from . import autostart, voice
    try:
        from . import bridge
        picked = any("Gemini" in str(x) or "->" in str(x) for x in bridge.status(svc).get("recent", []))
    except Exception:
        picked = False
    out = [
        # Essentials
        ("essentials", "names", "Your real first names", all(names.get(a, "").lower() not in ("", "me", "my wife") for a in adults),
         "Settings in the setup page, or config/settings.yaml household.names", "#settings"),
        ("essentials", "google", f"Connect Google for each adult ({len(google_people)} of {len(adults)})",
         bool(adults) and all(a in google_people for a in adults), "Settings > Google accounts > Connect", "#settings?sec=google"),
        ("essentials", "ai", "Add the AI key (Claude or Gemini)", provider,
         "Settings > Updates and this computer > Keys and addresses", "#settings?sec=computer"),
        ("essentials", "phones", "A private https address for the phones (Tailscale)", s.public_url.startswith("https://"),
         "Install Tailscale, run 'tailscale serve --bg 8800', paste the address in Keys and addresses",
         "#settings?sec=computer"),
        ("essentials", "notify", "Notifications on (at least one phone)", bool(svc.store.subscriptions()),
         "On each phone: Settings > Notifications > Turn on", "#settings?sec=notify"),
        # The household
        ("household", "area", "Your town and postcode area (for local tradespeople)", bool(hh.get("area")),
         "config/settings.yaml household.area", "#settings"),
        ("household", "gmail", "Let Life OS read one Gmail (receipts, school, bills)", gmail,
         "Settings > Google accounts > Connect, with email ticked", "#settings?sec=google"),
        ("household", "school", "The school's email addresses", bool(school) or not kids,
         "config/settings.yaml school.senders", "#settings"),
        ("household", "trades", "Save your go-to tradespeople", any(c.get("favourite") for c in contacts),
         "Home > Trades and contacts", "#home"),
        ("household", "manual", f"Start the home manual ({manual_n} so far)", manual_n >= 3,
         "Home > Home manual: stopcock, fuse box, boiler", "#home"),
        ("household", "renewals", "Add renewals and the car", bool(svc.store.renewals()),
         "To-do > Renewals", "#tasks?sec=renewals"),
        ("household", "gemini", "Try Gemini: \"Hey Google, add a task: fix the gate\"", picked,
         "Life OS picks it up within a few minutes", "#settings?sec=bridge"),
        # Extras
        ("extras", "ha", "Connect Home Assistant (lights, plugs, heating, speakers)", bool(svc.ha),
         "Keys and addresses > Home Assistant", "#settings?sec=computer"),
        ("extras", "voice", "Choose a speaker for Life OS to talk on", bool(voice.cfg(svc).get("speaker")),
         "Settings > Voice and speakers", "#settings?sec=voice"),
        ("extras", "start", "Start Life OS when the computer starts", autostart.enabled(),
         "Settings > Updates and this computer", "#settings?sec=computer"),
    ]
    return [{"group": g, "id": i, "text": t, "done": bool(d), "how": how, "link": link} for g, i, t, d, how, link in out]


def summary(svc, agent=None) -> dict:
    its = items(svc, agent)
    ess = [i for i in its if i["group"] == "essentials"]
    return {"items": its, "done": sum(i["done"] for i in its), "total": len(its),
            "essentials_done": all(i["done"] for i in ess),
            "next": [i for i in its if not i["done"]][:3]}
