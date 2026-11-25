"""App sign-in: the household key once per phone, then a signed cookie that says who is using it."""
from __future__ import annotations

import hashlib
import hmac
import time

COOKIE = "lifeos_session"
MAX_AGE = 60 * 60 * 24 * 180  # stay signed in on the phone for 6 months


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:40]


def make(secret: str, person: str) -> str:
    payload = f"{person}|{int(time.time())}"
    return f"{payload}|{_sig(secret, payload)}"


def read(secret: str, value: str | None) -> str | None:
    """Returns the person, or None if the cookie is missing, forged or too old."""
    if not value or not secret or value.count("|") != 2:
        return None
    person, issued, sig = value.split("|")
    if not hmac.compare_digest(sig, _sig(secret, f"{person}|{issued}")):
        return None
    if not issued.isdigit() or time.time() - int(issued) > MAX_AGE:
        return None
    return person
