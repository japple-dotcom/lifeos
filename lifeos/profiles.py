"""Gift memory: what each person likes, their sizes, wish-list links, and what they've been given before.

Used when a present needs sorting: birthday predictions come from these birthdays as well as the calendar, the
ideas Life OS suggests start from their wish list and likes, and nothing given in the last three years is
suggested again. Answering "What's Mum into?" fills in their likes here.
"""
from __future__ import annotations

import re
from datetime import date

from .activity import normalise
from .dates import fmt as _dfmt


def _id(name: str) -> str:
    key = normalise(name)
    if not key:
        raise ValueError("Type their name.")
    return key


def profiles(svc) -> list[dict]:
    out = []
    for p in svc.store.records("profile"):
        out.append({**p, "next_birthday": next_birthday(p.get("birthday", ""))})
    return sorted(out, key=lambda p: (p["next_birthday"] or "9999", p["name"].lower()))


def get(svc, name: str) -> dict | None:
    return svc.store.get("profile", _id(name))


def _clean_birthday(b: str) -> str:
    b = (b or "").strip()
    if not b:
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", b) or re.fullmatch(r"--?(\d{2})-(\d{2})", b)
    try:
        if m and len(m.groups()) == 3:
            date(int(m[1]), int(m[2]), int(m[3]))
            return b
        if m:
            date(2000, int(m[1]), int(m[2]))
            return f"--{m[1]}-{m[2]}"
    except ValueError:
        pass
    raise ValueError("Use a date like 1958-03-14 (or --03-14 without the year).")


def next_birthday(b: str, today: date | None = None) -> str:
    if not b:
        return ""
    today = today or date.today()
    mm, dd = int(b[-5:-3]), int(b[-2:])
    for year in (today.year, today.year + 1):
        try:
            d = date(year, mm, dd)
        except ValueError:  # 29 February
            d = date(year, mm, 28)
        if d >= today:
            return d.isoformat()
    return ""


def save(svc, name: str, relation: str = "", birthday: str = "", likes: str = "", sizes: str = "", notes: str = "",
         wishlist: list | str = "", given: list | None = None) -> dict:
    pid = _id(name)
    old = svc.store.get("profile", pid) or {}
    if isinstance(wishlist, str):
        items = []
        for line in wishlist.splitlines():
            line = line.strip()
            if not line:
                continue
            url = next((w for w in line.split() if w.startswith("https://")), "")
            title = line.replace(url, "").strip(" -:") or url
            items.append({"title": title[:120], "url": url})
        wishlist = items
    rec = {"name": name.strip()[:40], "relation": relation.strip()[:40], "birthday": _clean_birthday(birthday),
           "likes": likes.strip()[:400], "sizes": sizes.strip()[:200], "notes": notes.strip()[:600],
           "wishlist": [w for w in (wishlist or []) if isinstance(w, dict) and w.get("title")][:20],
           "given": given if given is not None else old.get("given", [])}
    return svc.store.put("profile", pid, rec)


def delete(svc, name: str) -> bool:
    return svc.store.remove("profile", _id(name))


def add_like(svc, name: str, likes: str) -> dict:
    """From the present question ("What's Mum into?"): adds to their likes, creating them if new."""
    p = get(svc, name) or {"name": name.strip().title()}
    merged = ", ".join(x for x in [p.get("likes", ""), likes.strip()] if x)
    return save(svc, p["name"], p.get("relation", ""), p.get("birthday", ""), merged, p.get("sizes", ""),
                p.get("notes", ""), p.get("wishlist", []), p.get("given"))


def record_given(svc, todo_title: str, what: str, today: date | None = None) -> str | None:
    """After 'I've ordered it' on a present: remembered so it isn't suggested again."""
    today = today or date.today()
    who = who_in(svc, todo_title)
    if not who or not re.search(r"present|gift|birthday|christmas", todo_title, re.I):
        return None
    p = get(svc, who)
    given = [g for g in p.get("given", []) if g.get("what") != what] + [{"year": today.year, "what": what[:120]}]
    save(svc, p["name"], p.get("relation", ""), p.get("birthday", ""), p.get("likes", ""), p.get("sizes", ""),
         p.get("notes", ""), p.get("wishlist", []), given[-12:])
    return p["name"]


def who_in(svc, text: str) -> str | None:
    low = f" {text.lower()} "
    for p in svc.store.records("profile"):
        if re.search(rf"\b{re.escape(p['name'].lower())}\b", low) or \
                (p.get("relation") and re.search(rf"\b{re.escape(p['relation'].lower())}\b", low)):
            return p["name"]
    return None


def prompt_text(svc, today: date | None = None) -> str:
    """For the assistant when it suggests presents."""
    today = today or date.today()
    rows = []
    for p in svc.store.records("profile")[:40]:
        recent = [g["what"] for g in p.get("given", []) if g.get("year", 0) >= today.year - 3]
        bits = [f"{p['name']}" + (f" ({p['relation']})" if p.get("relation") else "")]
        if p.get("likes"):
            bits.append(f"likes {p['likes']}")
        if p.get("sizes"):
            bits.append(f"sizes {p['sizes']}")
        if p.get("wishlist"):
            bits.append("wish list: " + "; ".join(f"{w['title']} {w.get('url', '')}".strip() for w in p["wishlist"][:6]))
        if recent:
            bits.append("already given (don't repeat): " + "; ".join(recent))
        if p.get("notes"):
            bits.append(p["notes"])
        rows.append("- " + ". ".join(bits))
    return "PEOPLE (for presents):\n" + "\n".join(rows) if rows else ""


def birthday_predictions(svc, today: date, days: int = 42, skip: set[str] | None = None) -> list[dict]:
    from datetime import timedelta
    out = []
    for p in svc.store.records("profile"):
        nb = next_birthday(p.get("birthday", ""), today)
        if not nb or (date.fromisoformat(nb) - today).days > days:
            continue
        key = normalise(p["name"])
        if skip and key in skip:
            continue
        day = date.fromisoformat(nb)
        likes = f" Likes {p['likes']}." if p.get("likes") else ""
        wl = f" {len(p['wishlist'])} things on their wish list." if p.get("wishlist") else ""
        out.append({"pid": f"gift:{key}:{day.year}", "kind": "gift", "key": key, "title": f"Present for {p['name']}",
                    "due": (day - timedelta(days=7)).isoformat(), "confidence": "high", "interval_days": 365, "last": "",
                    "why": f"{p['name']}'s birthday is {day.strftime(_dfmt('%A %-d %B'))}.{likes}{wl}"})
    return out
