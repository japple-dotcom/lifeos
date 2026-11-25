"""Ordering the Shopping list online.

No UK supermarket offers a way for other apps to fill your basket, so Life OS does the most it honestly can:

- Amazon: items with a usual product (learned when you order through Life OS, or saved by pasting an Amazon link)
  go into ONE "add to basket" link. One tap puts them all in your Amazon basket; you press Buy there.
- Your supermarket: a search link per item (opens straight on that item), and the whole list ready to paste into
  the supermarket's own list tools (Sainsbury's SmartLists, Waitrose multi-search, Ocado shopping lists).
- "I've ordered these" ticks them off and remembers them as bought, which is what predictions learn from.

Search links can be changed in settings.yaml (shopping.search_url, with {q} where the item goes).
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from .activity import normalise

AMAZON_BASKET = "https://www.amazon.co.uk/gp/aws/cart/add.html"
RETAILERS = {
    "tesco": ("Tesco", "https://www.tesco.com/groceries/en-GB/search?query={q}"),
    "sainsburys": ("Sainsbury's", "https://www.sainsburys.co.uk/gol-ui/SearchResults/{q}"),
    "asda": ("Asda", "https://www.asda.com/groceries/search/{q}"),
    "ocado": ("Ocado", "https://www.ocado.com/search?entry={q}"),
    "morrisons": ("Morrisons", "https://groceries.morrisons.com/search?entry={q}"),
    "waitrose": ("Waitrose", "https://www.waitrose.com/ecom/shop/search?searchTerm={q}"),
    "amazon": ("Amazon", "https://www.amazon.co.uk/s?k={q}"),
}


def asin_of(url: str) -> str:
    """The 10-character Amazon product code from a product or basket link, or from the code itself."""
    url = (url or "").strip()
    if re.fullmatch(r"[A-Z0-9]{10}", url):
        return url
    m = re.search(r"(?:/dp/|/gp/product/|ASIN\.\d+=|/product/)([A-Z0-9]{10})", url)
    return m.group(1) if m else ""


def basket_link(asins: list[tuple[str, int]]) -> str:
    """One link that puts several products in the Amazon basket (Amazon's own add-to-basket form)."""
    parts = [f"ASIN.{i}={a}&Quantity.{i}={max(1, min(20, q))}" for i, (a, q) in enumerate(asins, 1)]
    return f"{AMAZON_BASKET}?{'&'.join(parts)}" if parts else ""


def retailer(svc, choice: str = "") -> tuple[str, str, str]:
    cfg = svc.settings.behaviour.get("shopping") or {}
    rid = (choice or cfg.get("supermarket") or "tesco").lower()
    name, template = RETAILERS.get(rid, RETAILERS["tesco"])
    return rid if rid in RETAILERS else "tesco", name, cfg.get("search_url") or template


def plan(svc, items: list[dict], choice: str = "") -> dict:
    """items: Shopping list tasks. Splits them into one Amazon basket link and supermarket links."""
    products = svc.store.products()
    rid, name, template = retailer(svc, choice)
    amazon, other = [], []
    for t in items:
        key = normalise(t["title"])
        p = products.get(key)
        search = template.format(q=quote_plus(t["title"]))
        entry = {"id": t["id"], "title": t["title"], "key": key, "search_url": search}
        if p and p.get("asin"):
            amazon.append({**entry, "product": p["title"], "asin": p["asin"]})
        else:
            other.append(entry)
    return {"supermarket": {"id": rid, "name": name}, "retailers": [{"id": k, "name": v[0]} for k, v in RETAILERS.items()],
            "amazon": amazon, "amazon_basket": basket_link([(a["asin"], 1) for a in amazon]),
            "other": other, "copy_text": "\n".join(t["title"] for t in items)}
