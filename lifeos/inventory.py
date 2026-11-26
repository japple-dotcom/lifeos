"""Home contents list for insurance: valuables room by room, with photos and receipts, kept on this computer.

Home > Contents list. Each item: what it is, room, what it would cost to replace, when it was bought, a serial
number if it has one, and photos or receipts attached (stored in Life OS's own folder, never sent to Google or the
assistant). The totals per room and overall are compared with the contents cover you type in, with a warning when
the list adds up to more than the cover. "Copy the list" and the printable view are what an insurer asks for after
a break-in, fire or flood.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import date

ROOMS = ["Living room", "Kitchen", "Dining room", "Main bedroom", "Children's rooms", "Bathroom", "Hall", "Study",
         "Loft", "Garage and shed", "Garden", "Jewellery and watches", "Other"]
HIGH_VALUE = 1500  # many UK policies need single items over about £1,500 named on the policy


def _money(v) -> float:
    try:
        n = round(float(v or 0), 2)
    except (TypeError, ValueError) as e:
        raise ValueError("Give the value in pounds, for example 450.") from e
    if not 0 <= n < 10_000_000:
        raise ValueError("That value looks wrong.")
    return n


def save_item(svc, name: str, room: str, value, bought: str = "", serial: str = "", notes: str = "",
              iid: str = "") -> dict:
    name = " ".join((name or "").split())[:80]
    if not name:
        raise ValueError("What is it?")
    if room not in ROOMS:
        raise ValueError("Choose a room.")
    if bought:
        bought = date.fromisoformat(bought).isoformat()
    iid = iid or secrets.token_hex(6)
    rec = {"name": name, "room": room, "value": _money(value), "bought": bought,
           "serial": " ".join((serial or "").split())[:60], "notes": (notes or "").strip()[:300]}
    svc.store.put("item", iid, rec)
    return {**rec, "id": iid}


def remove_item(svc, iid: str) -> bool:
    from . import files
    for f in files.for_item(svc, "item", iid):
        files.delete(svc, f["id"])
    return svc.store.remove("item", iid)


def cover(svc) -> float:
    try:
        return float(json.loads(svc.store.get_state("contents_cover", "0") or "0"))
    except ValueError:
        return 0.0


def set_cover(svc, amount) -> dict:
    svc.store.set_state("contents_cover", json.dumps(_money(amount)))
    return overview(svc)


def overview(svc) -> dict:
    from . import files
    items = sorted(svc.store.records("item"), key=lambda i: (ROOMS.index(i["room"]) if i["room"] in ROOMS else 99, -i["value"]))
    rooms: dict[str, dict] = {}
    for i in items:
        i["files_list"] = files.for_item(svc, "item", i["id"])
        i["files"] = len(i["files_list"])
        r = rooms.setdefault(i["room"], {"room": i["room"], "total": 0.0, "items": []})
        r["total"] = round(r["total"] + i["value"], 2)
        r["items"].append({k: i[k] for k in ("id", "name", "value", "bought", "serial", "notes", "files", "files_list")})
    total = round(sum(i["value"] for i in items), 2)
    c = cover(svc)
    warn = []
    if c and total > c:
        warn.append(f"The list adds up to £{total:,.0f}, more than your contents cover (£{c:,.0f}). Check the cover is enough.")
    high = [i["name"] for i in items if i["value"] >= HIGH_VALUE]
    if high:
        warn.append(f"Items over £{HIGH_VALUE:,} often need naming on the policy: {', '.join(high[:6])}.")
    missing = sum(1 for i in items if not i["files"])
    return {"rooms": list(rooms.values()), "total": total, "cover": c, "count": len(items), "warnings": warn,
            "without_photos": missing, "room_names": ROOMS}


def _cell(v) -> str:
    """Typed text can't become a spreadsheet formula when the list is opened in Excel or Sheets."""
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def as_csv(svc) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Room", "Item", "Replacement value (GBP)", "Bought", "Serial number", "Notes", "Photos or receipts"])
    for r in overview(svc)["rooms"]:
        for i in r["items"]:
            w.writerow([r["room"], _cell(i["name"]), f"{i['value']:.2f}", i["bought"], _cell(i["serial"]),
                        _cell(i["notes"]), i["files"]])
    return buf.getvalue()
