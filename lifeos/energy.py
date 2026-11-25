"""The cheapest time to run the dishwasher or washing machine, for households on Octopus Agile or Go.

Octopus publishes each tariff's half-hourly prices openly (no account login needed). Set the tariff code in
Settings > Updates and this computer > Keys (OCTOPUS_TARIFF), e.g. E-1R-AGILE-24-10-01-C: it's on your bill and in
the Octopus app. List appliances on smart plugs in settings.yaml:

  energy:
    appliances:
      - {name: Dishwasher, entity: switch.dishwasher_plug, hours: 2}
      - {name: Washing machine, entity: switch.washing_machine_plug, hours: 2}

Set the appliance's programme and press start (or delay start) as usual, with the plug off. "Run it then" switches
the plug on at the cheapest time. Life OS only ever turns those listed plugs on; it never turns anything off.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time as _time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("lifeos.energy")
API = "https://api.octopus.energy/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/"


def tariff(svc) -> str:
    return (os.environ.get("OCTOPUS_TARIFF") or (svc.settings.behaviour.get("energy") or {}).get("octopus_tariff") or "").strip()


def product_of(code: str) -> str:
    """'E-1R-AGILE-24-10-01-C' -> 'AGILE-24-10-01'"""
    m = re.fullmatch(r"E-\dR-(.+)-[A-P]", code.strip().upper())
    if not m:
        raise ValueError("That doesn't look like an Octopus tariff code (like E-1R-AGILE-24-10-01-C).")
    return m.group(1)


def appliances(svc) -> list[dict]:
    return [a for a in (svc.settings.behaviour.get("energy") or {}).get("appliances") or []
            if a.get("name") and str(a.get("entity", "")).startswith("switch.")]


def rates(svc, now: datetime | None = None) -> list[dict]:
    """Half-hourly prices (pence per kWh including VAT) from now for the next day or so, cached for half an hour."""
    code = tariff(svc)
    if not code:
        return []
    now = now or datetime.now(timezone.utc)
    if getattr(svc.settings, "demo", False):
        return _sample_rates(now)
    cached = svc.store.get_state("energy_rates", "")
    if cached:
        try:
            c = json.loads(cached)
            if c.get("tariff") == code and _time.time() - c.get("at", 0) < 1800:
                return [r for r in c["rates"] if r["to"] > now.isoformat()]
        except ValueError:
            pass
    import httpx
    url = API.format(product=product_of(code), tariff=code.upper())
    r = httpx.get(url, params={"period_from": now.strftime("%Y-%m-%dT%H:%M:00Z"),
                               "period_to": (now + timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:00Z"),
                               "page_size": 200}, timeout=15)
    r.raise_for_status()
    out = sorted(({"from": x["valid_from"].replace("Z", "+00:00"), "to": (x.get("valid_to") or "").replace("Z", "+00:00")
                   or (now + timedelta(hours=36)).isoformat(), "p": round(float(x["value_inc_vat"]), 2)}
                  for x in r.json().get("results", [])), key=lambda x: x["from"])
    svc.store.set_state("energy_rates", json.dumps({"tariff": code, "at": _time.time(), "rates": out}))
    return out


def _slots(rs: list[dict], start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Half-hour slots with their price, between start and end."""
    out = []
    for r in rs:
        a, b = datetime.fromisoformat(r["from"]), datetime.fromisoformat(r["to"])
        t = max(a, start)
        t = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
        while t < b and t < end:
            if t >= start - timedelta(minutes=29):
                out.append((t, r["p"]))
            t += timedelta(minutes=30)
    return out


def cheapest(rs: list[dict], hours: float, now: datetime, within_hours: int = 24) -> dict | None:
    slots = _slots(rs, now, now + timedelta(hours=within_hours))
    n = max(1, round(hours * 2))
    if len(slots) < n:
        return None
    best = None
    for i in range(len(slots) - n + 1):
        window = slots[i:i + n]
        if (window[-1][0] - window[0][0]) != timedelta(minutes=30 * (n - 1)):
            continue  # a gap in the prices
        avg = sum(p for _, p in window) / n
        if best is None or avg < best["avg"] - 0.001:
            best = {"start": window[0][0], "end": window[0][0] + timedelta(minutes=30 * n), "avg": round(avg, 2)}
    if not best:
        return None
    now_window = slots[:n]
    now_avg = round(sum(p for _, p in now_window) / len(now_window), 2)
    flat = max(p for _, p in slots) - min(p for _, p in slots) < 0.5
    return {**best, "start": best["start"].isoformat(), "end": best["end"].isoformat(), "now_avg": now_avg, "flat": flat}


def plan(svc, now: datetime | None = None) -> dict:
    """For the Home screen: the cheapest time for each appliance, and the price now."""
    if not tariff(svc):
        return {"connected": False, "appliances": []}
    now = now or datetime.now(timezone.utc)
    try:
        rs = rates(svc, now)
    except Exception as e:
        log.warning("Octopus prices unavailable: %s", e)
        return {"connected": True, "error": "Couldn't get today's prices from Octopus just now.", "appliances": []}
    if not rs:
        return {"connected": True, "error": "Octopus hasn't published prices for the next few hours yet.", "appliances": []}
    now_p = next((r["p"] for r in rs if r["from"] <= now.isoformat() < r["to"]), rs[0]["p"])
    pending = {r["entity"]: r for r in svc.store.records("energy_run") if r.get("status") == "pending"}
    items = []
    for a in appliances(svc) or [{"name": "Something that uses a lot", "entity": "", "hours": 2}]:
        best = cheapest(rs, float(a.get("hours", 2)), now)
        if not best:
            continue
        items.append({"name": a["name"], "entity": a.get("entity", ""), "hours": a.get("hours", 2), **best,
                      "label": _label(best["start"], best["end"], svc.settings.tz),
                      "scheduled": pending.get(a.get("entity", ""))})
    return {"connected": True, "now_p": now_p, "appliances": items,
            "flat": bool(items) and all(i["flat"] for i in items)}


def _label(a: str, b: str, tz: str) -> str:
    from zoneinfo import ZoneInfo
    z = ZoneInfo(tz)
    s, e = datetime.fromisoformat(a).astimezone(z), datetime.fromisoformat(b).astimezone(z)
    day = "" if s.date() == datetime.now(z).date() else ("tomorrow " if s.date() == (datetime.now(z) + timedelta(days=1)).date()
                                                        else s.strftime("%A "))
    return f"{day}{s.strftime('%H:%M')} to {e.strftime('%H:%M')}"


def schedule(svc, entity: str, start: str, person: str = "") -> dict:
    allowed = {a["entity"]: a for a in appliances(svc)}
    if entity not in allowed:
        raise ValueError("Only plugs listed under energy.appliances can be switched on by Life OS.")
    when = datetime.fromisoformat(start)
    for r in svc.store.records("energy_run"):
        if r.get("entity") == entity and r.get("status") == "pending":
            svc.store.remove("energy_run", r["id"])
    rid = secrets.token_hex(4)
    return svc.store.put("energy_run", rid, {"entity": entity, "name": allowed[entity]["name"], "start": when.isoformat(),
                                             "status": "pending", "by": person}, at=when.isoformat())


def cancel(svc, rid: str) -> bool:
    return svc.store.remove("energy_run", rid)


def run_due(svc, now: datetime | None = None) -> list[str]:
    """Every few minutes: switches on any plug whose cheap time has come."""
    now = now or datetime.now(timezone.utc)
    did = []
    for r in svc.store.records("energy_run"):
        if r.get("status") != "pending" or datetime.fromisoformat(r["start"]) > now:
            continue
        rec = {k: v for k, v in r.items() if k not in ("id", "at", "updated_at")}
        late = now - datetime.fromisoformat(r["start"]) > timedelta(hours=1)
        if late or not svc.ha:
            rec["status"] = "missed"
        else:
            try:
                svc.ha.call_service("switch.turn_on", {"entity_id": r["entity"]})
                rec["status"] = "done"
                did.append(r["entity"])
                if svc.notifier:
                    svc.notifier.push(f"{r['name']} switched on: it's the cheapest time now.", title="Energy")
            except Exception as e:
                log.warning("couldn't switch on %s: %s", r["entity"], e)
                rec["status"] = "failed"
        svc.store.put("energy_run", r["id"], rec, at=r["start"])
    return did


def _sample_rates(now: datetime) -> list[dict]:
    """The sample household: a typical winter Agile day (cheap overnight, dear 4-7pm)."""
    start = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    out = []
    for i in range(72):
        a = start + timedelta(minutes=30 * i)
        h = a.hour + a.minute / 60
        p = 9.5 + (h - 1) * 0.4 if 0 <= h < 5 else 36.0 if 16 <= h < 19 else 17.5 if 11 <= h < 15 else 24.0
        out.append({"from": a.isoformat(), "to": (a + timedelta(minutes=30)).isoformat(), "p": round(p, 2)})
    return out


# ---------- weekly energy and heating report (v0.16) ----------
def _kwh(points: list[dict]) -> float:
    """A total-increasing kWh sensor: add up the rises (and survive a meter reset back to zero)."""
    total, prev = 0.0, None
    for p in points:
        try:
            v = float(p["state"])
        except (TypeError, ValueError, KeyError):
            continue
        if prev is not None and v >= prev:
            total += v - prev
        prev = v
    return round(total, 1)


def _peak_kwh(points: list[dict], svc=None) -> float:
    """kWh used in hours starting 4pm to 6pm (local time): each rise counts for the hour it started in."""
    from . import scheduling
    tz = scheduling._tz(svc.settings) if svc is not None else timezone.utc
    total, prev = 0.0, None
    for p in points:
        try:
            v = float(p["state"])
            when = datetime.fromisoformat(p["last_changed"].replace("Z", "+00:00")).astimezone(tz)
        except (TypeError, ValueError, KeyError):
            continue
        if prev is not None and v >= prev[0] and 16 <= prev[1].hour <= 18:
            total += v - prev[0]
        prev = (v, when)
    return round(total, 1)


def _heating_hours(points: list[dict], end: datetime) -> float:
    secs, on_since = 0.0, None
    for p in points:
        when = datetime.fromisoformat(p["last_changed"].replace("Z", "+00:00"))
        attrs = p.get("attributes") or {}
        heating = attrs.get("hvac_action") == "heating" if "hvac_action" in attrs else p.get("state") in ("heating", "on")
        if heating and on_since is None:
            on_since = when
        elif not heating and on_since is not None:
            secs += (when - on_since).total_seconds()
            on_since = None
    if on_since is not None:
        secs += (end - on_since).total_seconds()
    return round(secs / 3600, 1)


def report(svc, now: datetime | None = None) -> dict:
    """This week against last week: electricity used and roughly what it cost, hours the heating ran, and one
    suggestion. Needs Home Assistant with `energy.usage_entity` (a kWh meter sensor) and/or `energy.heating_entity`."""
    c = svc.settings.behaviour.get("energy") or {}
    usage, heating = c.get("usage_entity", ""), c.get("heating_entity", "")
    if not svc.ha or not (usage or heating) or not hasattr(svc.ha, "history"):
        return {"available": False}
    now = now or datetime.now(timezone.utc)
    week_ago, fortnight = now - timedelta(days=7), now - timedelta(days=14)
    out: dict = {"available": True}
    try:
        if usage:
            this = svc.ha.history(usage, week_ago.isoformat(), now.isoformat())
            last = svc.ha.history(usage, fortnight.isoformat(), week_ago.isoformat())
            out["kwh"], out["kwh_last"] = _kwh(this), _kwh(last)
            rate = float(c.get("unit_rate", 0) or 0)
            if not rate and tariff(svc):
                try:
                    rs = rates(svc, now)
                    rate = sum(r["p"] for r in rs) / len(rs) if rs else 0
                except Exception:
                    rate = 0
            rate = rate or 24.5  # a typical UK price per kWh if nothing better is known
            out["cost"], out["cost_last"] = round(out["kwh"] * rate / 100, 2), round(out["kwh_last"] * rate / 100, 2)
            out["peak_share"] = round(_peak_kwh(this, svc) / out["kwh"], 2) if out["kwh"] else 0
        if heating:
            this = svc.ha.history(heating, week_ago.isoformat(), now.isoformat(), attributes=True)
            last = svc.ha.history(heating, fortnight.isoformat(), week_ago.isoformat(), attributes=True)
            out["heating_hours"], out["heating_hours_last"] = _heating_hours(this, now), _heating_hours(last, week_ago)
    except Exception as e:
        log.warning("energy report: %s", e)
        return {"available": False, "error": "Couldn't read the history from Home Assistant."}
    out["suggestion"] = suggest(out, bool(tariff(svc)))
    return out


def suggest(r: dict, on_octopus: bool) -> str:
    hh, hl = r.get("heating_hours", 0), r.get("heating_hours_last", 0)
    if hl and hh > hl * 1.15 and hh - hl >= 3:
        return (f"The heating ran {hh - hl:g} hours more than last week. Turning the thermostat down by one degree "
                "typically saves about a tenth of heating costs.")
    if on_octopus and r.get("peak_share", 0) > 0.25:
        return (f"{round(100 * r['peak_share'])}% of your electricity was used between 4 and 7pm, the dearest time on "
                "your tariff. Running the dishwasher and washing machine at the cheapest time would help.")
    k, kl = r.get("kwh", 0), r.get("kwh_last", 0)
    if kl and k > kl * 1.2:
        return (f"Electricity use was up {round(100 * (k - kl) / kl)}% on last week. Worth checking for something "
                "left on: Home > Check before I go lists lights and plugs that are on.")
    if kl and k < kl * 0.9:
        return f"Electricity use was down {round(100 * (kl - k) / kl)}% on last week. Nice."
    return "Much the same as last week. Nothing to change."


def weekly_note(svc) -> dict:
    r = report(svc)
    if r.get("available") and svc.notifier and "kwh" in r:
        diff = r["cost"] - r["cost_last"]
        svc.notifier.push(f"Electricity this week: {r['kwh']:g} kWh, about £{r['cost']:.2f} "
                          f"({'up' if diff >= 0 else 'down'} £{abs(diff):.2f}). {r['suggestion']}", title="Energy this week")
    return r
