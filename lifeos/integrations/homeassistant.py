"""Home Assistant REST API client (https://developers.home-assistant.io/docs/api/rest/)."""
from __future__ import annotations

import httpx


class HomeAssistant:
    def __init__(self, url: str, token: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self._client = client or httpx.Client(timeout=15)
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def states(self, domain: str | None = None) -> list[dict]:
        r = self._client.get(f"{self.url}/api/states", headers=self._headers)
        r.raise_for_status()
        items = r.json()
        if domain:
            items = [s for s in items if s["entity_id"].startswith(domain + ".")]
        return items

    def state(self, entity_id: str) -> dict:
        r = self._client.get(f"{self.url}/api/states/{entity_id}", headers=self._headers)
        r.raise_for_status()
        return r.json()

    def summary(self, domains: tuple[str, ...] = ("light", "switch", "lock", "binary_sensor", "sensor",
                                                   "climate", "vacuum", "person", "calendar", "todo")) -> list[dict]:
        """Compact view of the house for the agent: id, friendly name, state."""
        out = []
        for s in self.states():
            if s["entity_id"].split(".")[0] in domains:
                out.append({
                    "entity_id": s["entity_id"],
                    "name": s.get("attributes", {}).get("friendly_name", ""),
                    "state": s["state"],
                })
        return out

    def call_service(self, service: str, data: dict | None = None) -> list[dict]:
        """service is 'domain.service', for example 'light.turn_on'."""
        domain, svc = service.split(".", 1)
        r = self._client.post(f"{self.url}/api/services/{domain}/{svc}", headers=self._headers, json=data or {})
        r.raise_for_status()
        return r.json()

    def history(self, entity_id: str, start: str, end: str, attributes: bool = False) -> list[dict]:
        """State changes of one entity between two ISO times: [{state, attributes, last_changed}, ...]."""
        params = {"filter_entity_id": entity_id, "end_time": end}
        if not attributes:
            params["no_attributes"] = ""
        r = self._client.get(f"{self.url}/api/history/period/{start}", headers=self._headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data[0] if data else []
