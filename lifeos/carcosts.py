"""Car running costs: fuel or charging, servicing by mileage, and MOT advisories.

For each car in To-do > Lists > Cars:
- Costs you add (fuel, charging, service, repairs, tyres, parking, insurance, tax) with the mileage when you know
  it. The last 12 months are added up by kind, per month, and per mile once there are two mileages.
- Servicing: every 10,000 miles or 12 months unless you change it. From the last service and the miles you do,
  Life OS works out roughly when the next one is due, and adds "Book a service" to the to-dos a month (or 500
  miles) before.
- MOT advisories: the things the tester noticed but didn't fail ("tyre worn close to the legal limit"). With the
  free DVSA MOT history API (apply at documentation.history.mot.api.gov.uk, then put the client ID, client secret,
  API key and token URL in Settings > Keys and addresses), they come in by themselves along with the mileage at
  every MOT. Without it, type them from the MOT certificate. Each one can go on the to-do list with one tap.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import time
from datetime import date, timedelta

log = logging.getLogger("lifeos.carcosts")
KINDS = ["fuel", "charging", "service", "repair", "tyres", "parking", "insurance", "tax", "breakdown cover", "other"]
MOT_API = "https://history.mot.api.gov.uk/v1/trade/vehicles/registration/"
MOT_SCOPE = "https://tapi.dvsa.gov.uk/.default"
_token: dict = {}


def _today(svc) -> date:
    from . import household
    return household.today_local(svc.settings)


def _car(svc, reg: str) -> dict:
    from . import cars
    reg = cars.clean_reg(reg)
    car = svc.store.get("car", reg)
    if not car:
        raise ValueError("Add the car first.")
    return car


def _save_car(svc, car: dict) -> None:
    rec = {k: v for k, v in car.items() if k not in ("id", "at", "updated_at")}
    svc.store.put("car", rec["reg"], rec)


# ---------- costs ----------
def add_cost(svc, reg: str, kind: str, amount: float, day: str = "", miles: int | None = None,
             litres: float | None = None, kwh: float | None = None, note: str = "") -> dict:
    car = _car(svc, reg)
    kind = (kind or "").lower()
    if kind not in KINDS:
        raise ValueError("Choose what the cost was for.")
    amount = round(float(amount or 0), 2)
    if not 0 < amount < 100000:
        raise ValueError("Give the amount in pounds, for example 62.40.")
    d = date.fromisoformat(day) if day else _today(svc)
    if d > _today(svc):
        raise ValueError("That date is in the future.")
    rec = {"car": car["reg"], "kind": kind, "amount": amount, "date": d.isoformat(),
           "miles": int(miles) if miles else None, "litres": float(litres) if litres else None,
           "kwh": float(kwh) if kwh else None, "note": " ".join((note or "").split())[:120]}
    if rec["miles"] is not None and not 0 <= rec["miles"] < 1_000_000:
        raise ValueError("The mileage looks wrong.")
    rid = secrets.token_hex(6)
    svc.store.put("car_cost", rid, rec, at=d.isoformat())
    if kind == "service":
        car["last_service"] = {"date": d.isoformat(), "miles": rec["miles"]}
        car.pop("service_nudged", None)
        _save_car(svc, car)
    return {**rec, "id": rid}


def remove_cost(svc, rid: str) -> bool:
    return svc.store.remove("car_cost", rid)


def costs(svc, reg: str, days: int = 365) -> list[dict]:
    since = (_today(svc) - timedelta(days=days)).isoformat()
    return [c for c in svc.store.records("car_cost", since=since) if c["car"] == reg]


def readings(svc, car: dict) -> list[tuple[date, int]]:
    """Every known mileage: from costs, the last service, and MOT tests."""
    pts = [(date.fromisoformat(c["date"]), int(c["miles"])) for c in costs(svc, car["reg"], 3 * 365) if c.get("miles")]
    for t in car.get("mot_tests") or []:
        if t.get("miles"):
            pts.append((date.fromisoformat(t["date"]), int(t["miles"])))
    return sorted(set(pts))


def miles_per_day(pts: list[tuple[date, int]]) -> float | None:
    if len(pts) < 2:
        return None
    (d0, m0), (d1, m1) = pts[0], pts[-1]
    if (d1 - d0).days < 20 or m1 <= m0:
        return None
    return (m1 - m0) / (d1 - d0).days


def service_due(svc, car: dict, today: date | None = None) -> dict | None:
    today = today or _today(svc)
    every_miles = int(car.get("service_every_miles") or 10000)
    every_months = int(car.get("service_every_months") or 12)
    last = car.get("last_service") or {}
    if not last.get("date"):
        return None
    from . import household
    by_date = household.add_months(date.fromisoformat(last["date"]), every_months)
    pts = readings(svc, car)
    rate = miles_per_day(pts)
    by_miles = None
    if last.get("miles") is not None and rate:
        target = int(last["miles"]) + every_miles
        latest_d, latest_m = pts[-1]
        left = target - (latest_m + rate * (today - latest_d).days)
        by_miles = today + timedelta(days=max(0, int(left / rate)))
    due = min(d for d in (by_date, by_miles) if d)
    return {"date": due.isoformat(), "why": "mileage" if by_miles and by_miles < by_date else "time",
            "every_miles": every_miles, "every_months": every_months}


def summary(svc, reg: str, today: date | None = None) -> dict:
    today = today or _today(svc)
    car = _car(svc, reg)
    rows = costs(svc, car["reg"])
    by_kind: dict[str, float] = {}
    for c in rows:
        by_kind[c["kind"]] = round(by_kind.get(c["kind"], 0) + c["amount"], 2)
    total = round(sum(by_kind.values()), 2)
    pts = readings(svc, car)
    rate = miles_per_day(pts)
    year_miles = int(rate * 365) if rate else None
    per_mile = None
    recent = [p for p in pts if (today - p[0]).days <= 365]
    if len(recent) >= 2 and recent[-1][1] > recent[0][1]:
        per_mile = round(total / (recent[-1][1] - recent[0][1]), 2)
    fuel = [c for c in rows if c["kind"] == "fuel" and c.get("litres")]
    return {"reg": car["reg"], "name": car.get("name", ""), "total_12m": total, "per_month": round(total / 12, 2),
            "by_kind": [{"kind": k, "amount": v} for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])],
            "per_mile": per_mile, "miles_a_year": year_miles, "latest_miles": pts[-1][1] if pts else None,
            "service": service_due(svc, car, today),
            "service_every_miles": int(car.get("service_every_miles") or 10000),
            "service_every_months": int(car.get("service_every_months") or 12),
            "pence_per_litre": round(100 * sum(c["amount"] for c in fuel) / sum(c["litres"] for c in fuel), 1) if fuel else None,
            "costs": sorted(rows, key=lambda c: c["date"], reverse=True)[:20],
            "advisories": advisories(car), "mot_api": bool(_mot_creds()), "mot_checked": car.get("mot_checked", "")}


def set_service(svc, reg: str, every_miles: int | None = None, every_months: int | None = None,
                last_date: str = "", last_miles: int | None = None) -> dict:
    car = _car(svc, reg)
    if every_miles:
        if not 1000 <= int(every_miles) <= 50000:
            raise ValueError("Service every 1,000 to 50,000 miles.")
        car["service_every_miles"] = int(every_miles)
    if every_months:
        if not 3 <= int(every_months) <= 36:
            raise ValueError("Service every 3 to 36 months.")
        car["service_every_months"] = int(every_months)
    if last_date:
        car["last_service"] = {"date": date.fromisoformat(last_date).isoformat(),
                               "miles": int(last_miles) if last_miles else None}
    _save_car(svc, car)
    return summary(svc, reg)


# ---------- MOT advisories ----------
def advisories(car: dict) -> list[dict]:
    """From the latest MOT test (and any typed in), with whether each is on the to-do list yet."""
    out = []
    tests = car.get("mot_tests") or []
    if tests:
        latest = max(tests, key=lambda t: t["date"])
        for i, a in enumerate(latest.get("advisories") or []):
            out.append({"id": f"mot-{latest['date']}-{i}", "text": a, "from": f"MOT {latest['date']}",
                        "added": f"mot-{latest['date']}-{i}" in (car.get("advisories_added") or [])})
    for a in car.get("typed_advisories") or []:
        out.append({**a, "added": a["id"] in (car.get("advisories_added") or [])})
    return out


def add_advisory(svc, reg: str, text: str) -> dict:
    car = _car(svc, reg)
    text = " ".join((text or "").split())[:200]
    if not text:
        raise ValueError("Type what the MOT tester advised.")
    car.setdefault("typed_advisories", []).append({"id": "typed-" + secrets.token_hex(4), "text": text,
                                                    "from": f"typed {_today(svc).isoformat()}"})
    _save_car(svc, car)
    return summary(svc, reg)


def advisory_todo(svc, reg: str, aid: str, by: str = "") -> dict:
    from . import todos
    car = _car(svc, reg)
    a = next((x for x in advisories(car) if x["id"] == aid), None)
    if not a:
        raise ValueError("That advisory isn't there any more.")
    t = todos.guess(svc, f"Get looked at on the {car.get('name') or car['reg']}: {a['text']}")
    t["title"] = f"{car.get('name') or car['reg']}: {a['text'][:1].lower() + a['text'][1:]}"[:120]
    filed = todos.file_todo(svc, t, by)
    car.setdefault("advisories_added", []).append(aid)
    _save_car(svc, car)
    return filed


def _mot_creds() -> dict:
    keys = {k: os.environ.get(f"MOT_{k.upper()}", "") for k in ("client_id", "client_secret", "api_key", "token_url")}
    return keys if all(keys.values()) else {}


def _mot_token(creds: dict, client=None) -> str:
    import httpx
    if _token.get("value") and _token.get("until", 0) > time.time() + 60:
        return _token["value"]
    http = client or httpx
    r = http.post(creds["token_url"], data={"grant_type": "client_credentials", "client_id": creds["client_id"],
                                            "client_secret": creds["client_secret"], "scope": MOT_SCOPE}, timeout=20)
    r.raise_for_status()
    data = r.json()
    _token.update(value=data["access_token"], until=time.time() + int(data.get("expires_in", 1800)))
    return _token["value"]


def parse_mot(data: dict) -> list[dict]:
    """DVSA's record -> [{date, result, miles, advisories: [...], failures: [...]}], newest first."""
    out = []
    for t in data.get("motTests") or []:
        d = (t.get("completedDate") or "")[:10]
        try:
            d = date.fromisoformat(d.replace(".", "-")).isoformat()
        except ValueError:
            continue
        miles = None
        try:
            if str(t.get("odometerResultType", "READ")).upper() == "READ":
                v = int(str(t.get("odometerValue") or "0").replace(",", ""))
                miles = v if (t.get("odometerUnit") or "MI").upper().startswith("MI") else int(v * 0.621371)
        except ValueError:
            pass
        items = t.get("defects") or t.get("rfrAndComments") or []
        adv = [i.get("text", "") for i in items if str(i.get("type", "")).upper() in ("ADVISORY", "MINOR", "USER ENTERED")]
        fail = [i.get("text", "") for i in items if str(i.get("type", "")).upper() in ("MAJOR", "DANGEROUS", "FAIL", "PRS")]
        out.append({"date": d, "result": str(t.get("testResult", "")).title(), "miles": miles,
                    "advisories": [re.sub(r"\s+", " ", a).strip() for a in adv if a][:12],
                    "failures": [re.sub(r"\s+", " ", f).strip() for f in fail if f][:12]})
    return sorted(out, key=lambda t: t["date"], reverse=True)


def fetch_mot(svc, reg: str, client=None) -> dict:
    """Pull the MOT history from DVSA (needs the MOT_* keys)."""
    creds = _mot_creds()
    if not creds:
        raise ValueError("Add the DVSA MOT history keys in Settings > Keys and addresses first.")
    import httpx
    car = _car(svc, reg)
    http = client or httpx
    r = http.get(MOT_API + car["reg"], headers={"Authorization": f"Bearer {_mot_token(creds, client)}",
                                               "X-API-Key": creds["api_key"], "Accept": "application/json"}, timeout=20)
    if r.status_code == 404:
        raise ValueError("DVSA has no MOT history for that registration (new cars don't need one for 3 years).")
    r.raise_for_status()
    car["mot_tests"] = parse_mot(r.json())[:10]
    car["mot_checked"] = _today(svc).isoformat()
    _save_car(svc, car)
    return summary(svc, reg)


def weekly(svc, today: date | None = None) -> list[str]:
    """Monday: refresh MOT history monthly (with keys) and add 'Book a service' when one's close."""
    from . import cars, todos
    today = today or _today(svc)
    did = []
    for car in cars.cars(svc):
        if _mot_creds() and (not car.get("mot_checked") or (today - date.fromisoformat(car["mot_checked"])).days >= 28):
            try:
                fetch_mot(svc, car["reg"])
                did.append(f"mot:{car['reg']}")
                car = svc.store.get("car", car["reg"]) or car
            except Exception as e:
                log.warning("MOT history for %s: %s", car["reg"], e)
        due = service_due(svc, car, today)
        if due and date.fromisoformat(due["date"]) - today <= timedelta(days=30) and car.get("service_nudged") != due["date"]:
            if svc.google:
                t = todos.guess(svc, f"Book a service for the {car.get('name') or car['reg']}")
                t.update(due=max(today, date.fromisoformat(due["date"]) - timedelta(days=7)).isoformat(), timescale="this_month")
                try:
                    todos.file_todo(svc, t, "cars")
                    did.append(f"service:{car['reg']}")
                except Exception as e:
                    log.warning("service to-do: %s", e)
            car["service_nudged"] = due["date"]
            _save_car(svc, car)
    return did
