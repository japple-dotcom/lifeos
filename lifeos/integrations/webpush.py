"""Notifications straight to the Life OS app on each phone (Web Push), so no extra notification app is needed.

Keys are created automatically on first start (secrets/vapid_private.pem). Each phone opts in from
Settings > Notifications. Approve / Reject buttons work right on the notification.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

log = logging.getLogger("lifeos.webpush")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_or_create_keys(path: str):
    from py_vapid import Vapid

    p = Path(path)
    if p.exists():
        return Vapid.from_file(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    v = Vapid()
    v.generate_keys()
    v.save_key(str(p))
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return v


def public_key_b64(vapid) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64url(raw)


class WebPusher:
    def __init__(self, store, key_file: str, contact: str = "mailto:lifeos@example.com", sender=None):
        self.store = store
        self.vapid = load_or_create_keys(key_file)
        self.public_key = public_key_b64(self.vapid)
        self.contact = contact
        self._send = sender  # tests inject a fake

    def _deliver(self, sub: dict, payload: str) -> int:
        if self._send:
            return self._send(sub, payload)
        from pywebpush import WebPushException, webpush

        try:
            webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]}, data=payload,
                    vapid_private_key=self.vapid, vapid_claims={"sub": self.contact}, ttl=12 * 3600, timeout=10)
            return 201
        except WebPushException as e:
            return getattr(e.response, "status_code", 500) if e.response is not None else 500

    def push(self, message: str, title: str = "Life OS", priority: int = 3,
             actions: list[dict] | None = None, tags=None, person: str | None = None) -> int:
        """Same signature as the ntfy notifier. Returns how many phones it reached."""
        data = {"title": title, "body": message[:1000], "url": "/app/#today", "urgent": priority >= 4}
        buttons = []
        for a in actions or []:
            if a.get("action", "http") == "http" and a["label"] in ("Approve", "Reject"):
                buttons.append({"action": a["label"].lower(), "title": a["label"], "url": a["url"]})
            elif a.get("action") == "view":
                data["url"] = a["url"]
        data["actions"] = buttons[:2]
        payload = json.dumps(data)
        sent = 0
        for sub in self.store.subscriptions(person):
            status = self._deliver(sub, payload)
            if status in (404, 410):  # the phone unsubscribed or the app was removed
                self.store.delete_subscription(sub["endpoint"])
            elif status < 300:
                sent += 1
            else:
                log.warning("web push to %s failed with %s", sub.get("person"), status)
        return sent


class FanOut:
    """Sends each notification through every configured channel (app push and/or ntfy)."""

    def __init__(self, *channels):
        self.channels = [c for c in channels if c is not None]

    def push(self, message: str, title: str = "Life OS", priority: int = 3, actions=None, tags=None) -> None:
        errors = []
        for c in self.channels:
            try:
                c.push(message, title=title, priority=priority, actions=actions, tags=tags)
            except Exception as e:  # one channel failing must not stop the others
                errors.append(e)
                log.warning("notification channel %s failed: %s", type(c).__name__, e)
        if errors and len(errors) == len(self.channels):
            raise errors[0]

    def __bool__(self) -> bool:
        return bool(self.channels)
