"""Garden: watering and frost nudges from the forecast, and the month's garden jobs.

The forecast comes from Home Assistant's weather (weather.get_forecasts) or, without Home Assistant, from
Open-Meteo (free, no key) for your postcode (looked up once at postcodes.io). Each morning at 7:30:
- Frost: tonight's low at or below 1°C, September to May: "Frost tonight: bring in tender pots, cover the
  ... , check the outside tap's insulated."
- Watering: April to September, three dry days ahead (under 1mm a day) with a top of 22°C or more: "Water the
  pots and anything newly planted this evening." Not again for 3 days.
Home > Garden lists the month's jobs for what you have (lawn, hedge, pots, veg, greenhouse) with Add to to-dos.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

log = logging.getLogger("lifeos.garden")
HAVE = ["lawn", "hedge", "pots", "veg", "greenhouse", "trees"]
JOBS = {  # month: [(needs, job)]
    1: [("trees", "Prune apple and pear trees while they're dormant"), ("pots", "Lift pots onto feet so they drain")],
    2: [("veg", "Order seeds and seed potatoes"), ("hedge", "Last chance to cut hedges before birds nest (March)")],
    3: [("lawn", "First lawn cut on a high setting once it's growing"), ("veg", "Sow early veg under cover"),
        ("hedge", "Check hedges for nests before trimming; leave cutting until September if you can")],
    4: [("lawn", "Feed the lawn and patch bare bits with seed"), ("pots", "Refresh pots with new compost")],
    5: [("veg", "Plant out tender veg after the last frost (late May)"), ("lawn", "Mow weekly; lower the blades a notch")],
    6: [("pots", "Water pots every evening in dry spells"), ("veg", "Pick and keep picking")],
    7: [("lawn", "Raise the mower blades in hot dry spells; don't feed in a drought"), ("greenhouse", "Shade and ventilate the greenhouse")],
    8: [("hedge", "Trim hedges from late August once nesting has finished"), ("veg", "Sow winter salads")],
    9: [("lawn", "Scarify, spike and feed the lawn (autumn feed)"), ("trees", "Pick apples and pears"),
        ("hedge", "Give hedges their main trim")],
    10: [("lawn", "Rake up leaves (they kill grass); last mow on a high setting"), ("pots", "Plant spring bulbs in pots"),
         ("greenhouse", "Clean the greenhouse glass for winter light")],
    11: [("pots", "Move tender pots against the house or into the greenhouse"), ("trees", "Plant bare-root trees and hedging")],
    12: [("pots", "Wrap pots that might crack in a hard frost"), ("greenhouse", "Insulate the greenhouse with bubble wrap")],
}
ALWAYS = {10: ["Clear the gutters once the leaves are down"], 11: ["Drain and cover the outside tap"],
          3: ["Turn the outside tap back on and check for leaks"]}


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def cfg(svc) -> dict:
    try:
        c = json.loads(svc.store.get_state("garden_cfg", "") or "{}")
    except ValueError:
        c = {}
    c.setdefault("have", ["lawn", "pots"])
    c.setdefault("weather", "")
    c.setdefault("postcode", "")
    c.setdefault("lat", None)
    c.setdefault("lon", None)
    c.setdefault("nudges", True)
    return c


def save(svc, have: list[str] | None = None, weather: str | None = None, postcode: str | None = None,
         nudges: bool | None = None, http=None) -> dict:
    c = cfg(svc)
    if have is not None:
        c["have"] = [h for h in have if h in HAVE]
    if weather is not None:
        if weather and not re.fullmatch(r"weather\.[a-z0-9_]+", weather):
            raise ValueError("Choose one of the weather entities listed.")
        c["weather"] = weather
    if nudges is not None:
        c["nudges"] = bool(nudges)
    if postcode is not None:
        pc = re.sub(r"\s+", "", postcode.upper())
        if pc and not re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}", pc):
            raise ValueError("That doesn't look like a UK postcode.")
        c["postcode"], c["lat"], c["lon"] = pc, None, None
        if pc:
            import httpx
            try:
                r = (http or httpx).get(f"https://api.postcodes.io/postcodes/{pc}", timeout=10)
            except Exception as e:
                raise ValueError("Couldn't look up that postcode just now. Check the internet and try again.") from e
            if r.status_code != 200:
                raise ValueError("Couldn't find that postcode.")
            res = r.json().get("result") or {}
            c["lat"], c["lon"] = round(float(res["latitude"]), 3), round(float(res["longitude"]), 3)
    svc.store.set_state("garden_cfg", json.dumps(c))
    return c


def weathers(svc) -> list[dict]:
    if not svc.ha:
        return []
    try:
        return [{"id": w["entity_id"], "name": (w.get("attributes") or {}).get("friendly_name") or w["entity_id"]}
                for w in svc.ha.states("weather")]
    except Exception:
        return []


def forecast(svc, http=None) -> list[dict]:
    """[{date, high, low, rain}] for today and the next few days, or [] if there's no way to know."""
    c = cfg(svc)
    ent = c["weather"] or (weathers(svc)[0]["id"] if weathers(svc) else "")
    if svc.ha and ent:
        try:
            resp = svc.ha.service_response("weather.get_forecasts", {"entity_id": ent, "type": "daily"})
            days = (resp.get(ent) or {}).get("forecast") or []
            return [{"date": str(d.get("datetime", ""))[:10], "high": d.get("temperature"),
                     "low": d.get("templow"), "rain": d.get("precipitation") or 0} for d in days[:5]]
        except Exception as e:
            log.warning("HA forecast: %s", e)
    if c["lat"] is not None:
        try:
            import httpx
            r = (http or httpx).get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
                "latitude": c["lat"], "longitude": c["lon"], "timezone": "Europe/London", "forecast_days": 4,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum"})
            d = r.json().get("daily") or {}
            return [{"date": t, "high": hi, "low": lo, "rain": rn or 0} for t, hi, lo, rn in
                    zip(d.get("time", []), d.get("temperature_2m_max", []), d.get("temperature_2m_min", []),
                        d.get("precipitation_sum", []), strict=False)]
        except Exception as e:
            log.warning("Open-Meteo: %s", e)
    return []


def advice(svc, days: list[dict], today: date | None = None) -> list[dict]:
    today = today or _today(svc)
    m = today.month
    have = set(cfg(svc)["have"])
    out = []
    if days and m in (9, 10, 11, 12, 1, 2, 3, 4, 5):
        low = days[0].get("low")
        if low is not None and low <= 1:
            what = ["bring tender pots in or against the house"] if "pots" in have else []
            what += ["close the greenhouse"] if "greenhouse" in have else []
            what += ["check the outside tap is drained or covered"]
            out.append({"kind": "frost", "text": f"Frost tonight (down to {low:g}°C): " + ", ".join(what) + "."})
    if len(days) >= 3 and m in (4, 5, 6, 7, 8, 9):
        dry = all((d.get("rain") or 0) < 1 for d in days[:3])
        hot = max((d.get("high") or 0) for d in days[:3]) >= 22
        if dry and hot:
            what = "the pots and anything newly planted" if "pots" in have or "veg" in have else "anything newly planted"
            out.append({"kind": "water", "text": f"Dry and warm for the next few days: water {what} this evening."})
    return out


def jobs(svc, today: date | None = None) -> list[str]:
    today = today or _today(svc)
    have = set(cfg(svc)["have"])
    return [j for need, j in JOBS.get(today.month, []) if need in have] + ALWAYS.get(today.month, [])


def overview(svc, http=None) -> dict:
    c = cfg(svc)
    days = forecast(svc, http)
    return {"have": c["have"], "all": HAVE, "weather": c["weather"], "weathers": weathers(svc), "postcode": c["postcode"],
            "nudges": c["nudges"], "forecast": days, "advice": advice(svc, days), "jobs": jobs(svc),
            "source": "home assistant" if svc.ha and (c["weather"] or weathers(svc)) else "open-meteo" if c["lat"] is not None else "none"}


def morning(svc, today: date | None = None, http=None) -> list[str]:
    """7:30: frost and watering nudges, each kind at most every 3 days."""
    today = today or _today(svc)
    c = cfg(svc)
    if not c["nudges"]:
        return []
    sent = []
    for a in advice(svc, forecast(svc, http), today):
        key = f"garden_nudge:{a['kind']}"
        last = svc.store.get_state(key, "")
        if last and (today - date.fromisoformat(last)).days < 3:
            continue
        svc.store.set_state(key, today.isoformat())
        if svc.notifier:
            svc.notifier.push(a["text"], title="Garden", priority=2)
        sent.append(a["text"])
    return sent


def job_todo(svc, text: str, by: str = "") -> dict:
    from . import todos
    if text not in jobs(svc) and text not in [j for js in JOBS.values() for _, j in js]:
        raise ValueError("That job isn't on the list.")
    t = todos.guess(svc, text)
    t.update(title=text, timescale="this_month", due=(_today(svc) + timedelta(days=14)).isoformat())
    return todos.file_todo(svc, t, by or "garden")
