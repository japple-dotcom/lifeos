"""Recipe links: paste (or share) a recipe page and its ingredients go on the Shopping list.

Most recipe sites (BBC Good Food, Tesco Real Food, Jamie Oliver, Delicious, AllRecipes...) publish the recipe in a
standard machine-readable form (schema.org Recipe). Life OS reads that, saves the dinner for the day you pick with
its link, and puts the ingredients that aren't cupboard staples on Shopping. The page is treated as data only.

Fetching is careful: only http(s), only public internet addresses (never the home network), at most 3 MB.
"""
from __future__ import annotations

import html as htmllib
import ipaddress
import json
import re
import socket
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse

from . import household

MAX_BYTES = 3 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; LifeOS household assistant; recipe reader)"


class RecipeError(ValueError):
    pass


# ---------- fetching ----------
def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {i[4][0] for i in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def fetch(url: str) -> str:
    import httpx
    for _ in range(5):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise RecipeError("That doesn't look like a web link.")
        if not _public(u.hostname):
            raise RecipeError("Life OS only reads recipe pages on the public internet.")
        with httpx.stream("GET", url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=12,
                          follow_redirects=False) as r:
            if r.is_redirect and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code >= 400:
                raise RecipeError(f"The page answered {r.status_code}.")
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    break
            return data.decode(r.encoding or "utf-8", errors="replace")
    raise RecipeError("That link redirects too many times.")


# ---------- reading the page ----------
def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _is_recipe(d: dict) -> bool:
    t = d.get("@type")
    return t == "Recipe" or (isinstance(t, list) and "Recipe" in t)


def _text(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("text") or v.get("name") or ""
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", str(v))).strip()


def parse(page: str) -> dict:
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        block = block.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for d in _walk(data):
            if _is_recipe(d) and d.get("recipeIngredient"):
                ings = d["recipeIngredient"]
                ings = ings if isinstance(ings, list) else [ings]
                return {"name": _text(d.get("name")) or "Recipe", "ingredients": [_text(i) for i in ings if _text(i)][:40],
                        "serves": _text(d.get("recipeYield")), "time": _text(d.get("totalTime"))}
    # older pages: microdata
    ings = re.findall(r'itemprop=["\'](?:recipeIngredient|ingredients)["\'][^>]*?(?:content=["\']([^"\']+)["\'][^>]*>|>(.*?)<)',
                      page, re.S | re.I)
    items = [htmllib.unescape(re.sub(r"<[^>]+>", " ", a or b)).strip() for a, b in ings]
    items = [i for i in items if i]
    if items:
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I) or \
            re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        return {"name": htmllib.unescape(m.group(1)).strip()[:120] if m else "Recipe", "ingredients": items[:40],
                "serves": "", "time": ""}
    raise RecipeError("Couldn't find a recipe on that page. Type the dinner in yourself instead.")


UNITS = (r"(?:kg|g|grams?|ml|millilitres?|l|litres?|tbsp|tablespoons?|tsp|teaspoons?|dsp|cups?|oz|lb|lbs|pints?|"
         r"pinch(?:es)?|handfuls?|cloves?|tins?|cans?|packs?|packets?|pots?|jars?|bunch(?:es)?|sprigs?|slices?|"
         r"sticks?|knobs?|sheets?|bags?|heaped|level|rounded|large|medium|small|x)")
FRACTIONS = "½¼¾⅓⅔⅛"


def clean(item: str) -> str:
    """'2 x 400g tins chopped tomatoes' -> 'chopped tomatoes'; '1 tbsp olive oil, plus extra' -> 'olive oil'."""
    s = item.lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.split(r",| - | – |;", s)[0]
    s = re.sub(r"\b(?:to serve|to taste|optional|for frying|for greasing|plus extra|about|approx\.?)\b", " ", s)
    for _ in range(4):  # quantities and units can repeat: "2 x 400g tins"
        s = re.sub(rf"^\s*(?:[\d{FRACTIONS}][\d{FRACTIONS}/.\-–]*\s*{UNITS}?\b\.?|{UNITS}\b\.?|of\b|a\b|an\b)\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s[:60]


DEMO_PAGE = """<script type="application/ld+json">{"@type": "Recipe", "name": "One-pot chicken and chorizo rice",
"recipeYield": "4", "recipeIngredient": ["1 tbsp olive oil", "6 chicken thighs", "100g chorizo, sliced", "1 onion, chopped",
"2 garlic cloves", "1 red pepper", "300g paella rice", "1 x 400g tin chopped tomatoes", "700ml chicken stock",
"100g frozen peas"]}</script>"""


def _next_free_day(svc) -> str:
    planned = {m["day"] for m in household.week_meals(svc, days=14) if m["dinner"]}
    d = household.today_local(svc.settings)
    for i in range(14):
        if (d + timedelta(days=i)).isoformat() not in planned:
            return (d + timedelta(days=i)).isoformat()
    return (d + timedelta(days=1)).isoformat()


def add(svc, url: str, day: str = "", page: str | None = None) -> dict:
    url = (url or "").strip()
    if not page and svc.settings.demo:
        page = DEMO_PAGE  # the sample household doesn't go on the internet
    if not page:
        page = fetch(url)
    r = parse(page)
    names, seen = [], set()
    for i in r["ingredients"]:
        c = clean(i)
        if c and c not in seen:
            seen.add(c)
            names.append(c)
    day = day or _next_free_day(svc)
    date.fromisoformat(day)
    household.save_meal_plan(svc, [{"day": day, "dinner": r["name"][:120], "ingredients": names, "notes": url[:300]}],
                             add_to_shopping=False)
    staples = household.pantry(svc.settings)
    cupboard = [n for n in names if is_staple(n, staples)]
    added, already = [], []
    if svc.google:
        from . import activity
        g = svc.google.get()
        have = {activity.normalise(t["title"]) for t in g.list_tasks("Shopping")}
        for n in names:
            if n in cupboard:
                continue
            if activity.normalise(n) in have:
                already.append(n)
            elif not g.add_task("Shopping", n).get("duplicate"):
                added.append(n)
    return {"dinner": r["name"], "day": day, "ingredients": names, "added": added, "already_on_list": already,
            "in_cupboard": cupboard, "serves": r["serves"]}


SOFT = {"fresh", "ground", "dried", "sea", "black", "white", "extra", "virgin", "cloves", "clove", "flaked", "fine",
        "freshly", "cracked", "rock", "table", "cold", "hot", "boiling", "of", "a", "some"}


def is_staple(name: str, staples: set[str]) -> bool:
    """'garlic cloves', 'salt and pepper', 'extra virgin olive oil' count as cupboard staples; 'peppers' doesn't."""
    if name in staples:
        return True
    parts = [p.strip() for p in re.split(r"\band\b|&|/", name) if p.strip()]
    def core(p: str) -> str:
        return " ".join(w for w in p.split() if w not in SOFT)
    return bool(parts) and all(core(p) in staples or core(p).rstrip("s") in staples and core(p) != "peppers"
                               for p in parts)
