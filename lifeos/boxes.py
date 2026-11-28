"""Loft, garage and shed boxes: what's in each one, printed QR labels that open the box's list on a phone, and
"Where are the Christmas lights?" in the search box and on the speakers. Kept on the Life OS computer only.
"""
from __future__ import annotations

import re
import secrets

KIND = "box"
PLACES = ["Loft", "Garage", "Shed", "Under the stairs", "Spare room", "Cellar", "Wardrobe", "Other"]
_STOP = {"the", "a", "an", "our", "my", "is", "are", "where", "wheres", "where's", "s", "of", "box", "boxes", "some",
         "any", "in", "which", "what", "whats", "what's", "kept", "put", "did", "we", "i"}


def _clean_items(items) -> list[str]:
    if isinstance(items, str):
        items = re.split(r"[\n,;]", items)
    out, seen = [], set()
    for i in items or []:
        i = " ".join(str(i).split())[:60]
        if i and i.lower() not in seen:
            seen.add(i.lower())
            out.append(i)
    return out[:200]


def boxes(svc) -> list[dict]:
    out = sorted(svc.store.records(KIND), key=lambda b: (PLACES.index(b["where"]) if b.get("where") in PLACES else 99,
                                                          int(b.get("number") or 0)))
    from . import files
    for b in out:
        b["files_list"] = files.for_item(svc, "box", b["id"])
    return out


def get(svc, bid: str) -> dict | None:
    return svc.store.get(KIND, bid)


def _next_number(svc) -> int:
    return max([int(b.get("number") or 0) for b in svc.store.records(KIND)] or [0]) + 1


def save(svc, name: str = "", where: str = "", items=None, notes: str = "", bid: str = "") -> dict:
    old = get(svc, bid) if bid else None
    if bid and not old:
        raise ValueError("That box isn't on the list any more.")
    where = where if where in PLACES else (old or {}).get("where") or ""
    if not where:
        raise ValueError("Say where the box lives.")
    number = int((old or {}).get("number") or _next_number(svc))
    name = " ".join((name or "").split())[:50] or (old or {}).get("name") or f"Box {number}"
    rec = {"id": bid or secrets.token_hex(4), "number": number, "name": name, "where": where,
           "items": _clean_items(items) if items is not None else (old or {}).get("items", []),
           "notes": " ".join((notes if notes is not None else (old or {}).get("notes", "")).split())[:200]}
    svc.store.put(KIND, rec["id"], rec)
    return rec


def add_items(svc, bid: str, items) -> dict:
    b = get(svc, bid)
    if not b:
        raise ValueError("That box isn't on the list any more.")
    b["items"] = _clean_items(list(b.get("items", [])) + _clean_items(items))
    svc.store.put(KIND, bid, b)
    return b


def take_out(svc, bid: str, item: str) -> dict:
    b = get(svc, bid)
    if not b:
        raise ValueError("That box isn't on the list any more.")
    b["items"] = [i for i in b.get("items", []) if i.lower() != (item or "").strip().lower()]
    svc.store.put(KIND, bid, b)
    return b


def remove(svc, bid: str) -> None:
    from . import files
    for f in files.for_item(svc, "box", bid):
        files.delete(svc, f["id"])
    svc.store.remove(KIND, bid)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 1]


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def find(svc, question: str) -> list[dict]:
    """Boxes holding what was asked for, best match first: [{box, matches}]."""
    want = {_stem(w) for w in _words(question)}
    if not want:
        return []
    out = []
    for b in boxes(svc):
        hits = []
        for i in b.get("items", []) + ([b["name"]] if b.get("name") else []):
            have = {_stem(w) for w in _words(i)}
            score = len(want & have)
            if score:
                hits.append((score, i))
        if hits:
            hits.sort(key=lambda h: -h[0])
            out.append({"box": b, "matches": [h[1] for h in hits[:3]], "score": hits[0][0]})
    out.sort(key=lambda r: -r["score"])
    return out


def where_is(svc, question: str) -> str:
    found = find(svc, question)
    if not found:
        return ""
    best = found[0]
    b = best["box"]
    first = best["matches"][0]
    label = b["name"] if b["name"].lower().startswith("box") else f"box {b['number']} ({b['name']})"
    more = f" Also in box {found[1]['box']['number']}." if len(found) > 1 and found[1]["score"] == best["score"] else ""
    return f"{first}: {label}, {b['where'].lower() if b['where'] != 'Other' else 'elsewhere'}.{more}"


def label_url(svc, bid: str) -> str:
    return f"{svc.settings.public_url}/app/box.html?id={bid}"


def labels(svc, ids: list[str] | None = None) -> list[dict]:
    """QR labels (as SVG) for printing: each opens that box's list when scanned by a signed-in phone."""
    from . import qr
    out = []
    for b in boxes(svc):
        if ids and b["id"] not in ids:
            continue
        url = label_url(svc, b["id"])
        # the code as rows of 0/1, drawn by the page itself (no markup from here)
        rows = ["".join("1" if dark else "0" for dark in row) for row in qr.matrix(url)]
        out.append({"id": b["id"], "number": b["number"], "name": b["name"], "where": b["where"], "qr": rows,
                    "count": len(b.get("items", [])), "top": b.get("items", [])[:4]})
    return out
