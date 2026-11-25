"""Phone notifications through ntfy (https://docs.ntfy.sh), with Approve / Reject buttons.

Published as JSON, so titles and messages can contain any characters (emoji, accents, curly quotes).
For privacy, self-host ntfy or protect the topic with an access token: notification text can include
calendar and email details.
"""
from __future__ import annotations

import httpx


class Notifier:
    def __init__(self, base_url: str, topic: str, token: str = "", client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.topic = topic
        self.token = token
        self._client = client or httpx.Client(timeout=10)

    def push(self, message: str, title: str = "Life OS", priority: int = 3,
             actions: list[dict] | None = None, tags: list[str] | str | None = None) -> None:
        """actions: up to 3 of {'action': 'http'|'view', 'label': ..., 'url': ...}."""
        if not self.topic:
            return
        payload: dict = {"topic": self.topic, "message": message[:4000], "title": title[:250],
                         "priority": max(1, min(5, int(priority)))}
        if tags:
            payload["tags"] = [tags] if isinstance(tags, str) else list(tags)
        if actions:
            payload["actions"] = []
            for a in actions[:3]:
                kind = a.get("action", "http")
                act = {"action": kind, "label": a["label"], "url": a["url"], "clear": True}
                if kind == "http":
                    act["method"] = a.get("method", "POST")
                payload["actions"].append(act)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self._client.post(self.base_url, json=payload, headers=headers).raise_for_status()
