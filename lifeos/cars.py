"""Cars: MOT and tax dates from the registration, kept in Renewals by themselves.

With a DVLA Vehicle Enquiry API key (free, register at register-for-ves.driver-vehicle-licensing.api.gov.uk; set
DVLA_API_KEY in Settings > Updates and this computer > Keys), the dates come from the DVLA and are checked every
week. Without one, type the dates once and Life OS keeps them rolling forward.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date

log = logging.getLogger("lifeos.cars")
DVLA_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles"


def clean_reg(reg: str) -> str:
    r = re.sub(r"[^A-Z0-9]", "", (reg or "").upper())
    if not 2 <= len(r) <= 8:
        raise ValueError("That doesn't look like a registration number.")
    return r


def pretty_reg(reg: str) -> str:
    """'AB12CDE' -> 'AB12 CDE' (current-style plates); others as they are."""
    return f"{reg[:4]} {reg[4:]}" if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z]{3}", reg) else reg


def _key(svc) -> str:
    return os.environ.get("DVLA_API_KEY", "") or (svc.settings.behaviour.get("cars_api") or {}).get("dvla_key", "")


def lookup(svc, reg: str) -> dict | None:
    """The DVLA's record for this registration: make, colour, MOT expiry, tax due. None without a key."""
    key = _key(svc)
    if not key:
        return None
    import httpx
    r = httpx.post(DVLA_URL, json={"registrationNumber": reg}, headers={"x-api-key": key, "Accept": "application/json"},
                   timeout=15)
    if r.status_code == 404:
        raise ValueError("The DVLA doesn't know that registration.")
    r.raise_for_status()
    d = r.json()
    return {"make": (d.get("make") or "").title(), "colour": (d.get("colour") or "").title(),
            "year": d.get("yearOfManufacture"), "mot_due": d.get("motExpiryDate") or "",
            "mot_status": d.get("motStatus") or "", "tax_due": d.get("taxDueDate") or "",
            "tax_status": d.get("taxStatus") or ""}


def cars(svc) -> list[dict]:
    return svc.store.records("car")


def add_car(svc, reg: str, name: str = "", mot_due: str = "", tax_due: str = "") -> dict:
    reg = clean_reg(reg)
    found = lookup(svc, reg)
    car = {"reg": reg, "name": (name or "").strip()[:40], **(found or {})}
    if mot_due:
        car["mot_due"] = _valid(mot_due)
    if tax_due:
        car["tax_due"] = _valid(tax_due)
    if not car["name"]:
        car["name"] = " ".join(x for x in (car.get("colour"), car.get("make")) if x) or pretty_reg(reg)
    car["checked"] = date.today().isoformat() if found else ""
    svc.store.put("car", reg, car)
    sync_renewals(svc, car)
    return car


def remove_car(svc, reg: str) -> None:
    reg = clean_reg(reg)
    for r in svc.store.renewals():
        if f"({pretty_reg(reg)})" in r["name"] and r["category"] == "Car":
            svc.store.delete_renewal(r["id"])
    svc.store.remove("car", reg)


def _valid(d: str) -> str:
    try:
        return date.fromisoformat(d[:10]).isoformat()
    except ValueError as e:
        raise ValueError("Use a date like 2027-03-14.") from e


def sync_renewals(svc, car: dict) -> list[str]:
    """One renewal each for MOT and tax, named so they can be found again: 'MOT: Golf (AB12 CDE)'."""
    did = []
    plate = pretty_reg(car["reg"])
    existing = {r["name"]: r for r in svc.store.renewals()}
    for label, field, months in (("MOT", "mot_due", 12), ("Car tax", "tax_due", 12)):
        due = car.get(field)
        if not due:
            continue
        name = f"{label}: {car['name']} ({plate})"
        notes = "Dates from the DVLA, checked weekly." if car.get("checked") else "Date typed in; it moves on a year once passed."
        if name in existing:
            if existing[name]["due_date"] != due:
                svc.store.update_renewal(existing[name]["id"], due_date=due, notes=notes, reminded_for="")
                did.append(f"moved:{name}")
        else:
            svc.store.add_renewal(name, due, category="Car", notes=notes, remind_days=42 if label == "MOT" else 21,
                                  repeat_months=months)
            did.append(f"added:{name}")
    return did


def weekly(svc) -> list[str]:
    """Checks each car with the DVLA: dates kept up to date, and a warning if it shows as untaxed or out of MOT."""
    if not _key(svc):
        return []
    did = []
    for car in cars(svc):
        try:
            found = lookup(svc, car["reg"])
        except Exception as e:
            log.warning("DVLA check for %s failed: %s", car["reg"], e)
            continue
        if not found:
            continue
        car = {**car, **found, "checked": date.today().isoformat()}
        svc.store.put("car", car["reg"], car)
        did += sync_renewals(svc, car)
        warn = []
        if found["tax_status"] and found["tax_status"].lower() not in ("taxed", "sorn"):
            warn.append(f"shows as {found['tax_status'].lower()}")
        if found["mot_status"].lower() == "not valid":
            warn.append("has no valid MOT")
        if warn and svc.notifier and svc.store.get_state(f"car_warned:{car['reg']}") != date.today().isoformat()[:7]:
            svc.notifier.push(f"The DVLA says {car['name']} ({pretty_reg(car['reg'])}) {' and '.join(warn)}.", title="Car check",
                              priority=4)
            svc.store.set_state(f"car_warned:{car['reg']}", date.today().isoformat()[:7])
    return did
