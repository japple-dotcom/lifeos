"""Google accounts for each adult in the household (for example you and your wife).

Each person connects their own account from the app and chooses whether Life OS may use their Gmail.
Tokens live in secrets/google_token_<person>.json. Calendars are merged; tasks and email are per person.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections.abc import Callable

log = logging.getLogger("lifeos.accounts")


class _Serialised:
    """Google's API client is not thread-safe. The app and scheduled routines run in different threads,
    so every call on one person's account goes through that account's lock.

    Reads are also remembered for a short time (CACHE_SECONDS), so opening the app doesn't wait on the same
    Google requests again and again; any change through Life OS clears that person's remembered reads."""

    CACHE_SECONDS = 60
    READS = {"list_tasks", "list_names", "events"}
    WRITES = {"add_task", "complete_task", "move_task_due", "add_event", "delete_event", "delete_task", "send_email", "update_task",
              "create_draft", "delete_draft", "send_draft"}

    def __init__(self, inner):
        self._inner = inner
        self._lock = threading.RLock()
        self._cache: dict = {}

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                if name in self.READS:
                    key = (name, args, tuple(sorted(kwargs.items())))
                    hit = self._cache.get(key)
                    if hit and time.monotonic() - hit[0] < self.CACHE_SECONDS:
                        return copy.deepcopy(hit[1])
                    result = attr(*args, **kwargs)
                    self._cache[key] = (time.monotonic(), copy.deepcopy(result))
                    return result
                if name in self.WRITES:
                    self._cache.clear()
                return attr(*args, **kwargs)
        return call

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()


class GoogleAccounts:
    def __init__(self, settings, factory: Callable | None = None, people: dict | None = None):
        """people lets tests inject ready-made clients: {'me': FakeGoogle(), 'wife': FakeGoogle()}."""
        self.settings = settings
        self._factory = factory
        self.people: dict = dict(people) if people is not None else {}
        if people is None:
            self.reload()

    # ---------- people ----------
    @property
    def person_ids(self) -> list[str]:
        return list(self.settings.household.get("adults") or ["me"])

    def display_name(self, person: str) -> str:
        return (self.settings.household.get("names") or {}).get(person, person.title())

    @property
    def owner(self) -> str:
        """Whose Google Tasks hold the shared household lists."""
        return self.settings.household.get("tasks_owner") or self.person_ids[0]

    def token_path(self, person: str) -> Path:
        return Path(self.settings.google_token_file).parent / f"google_token_{person}.json"

    def _make(self, token_file: str):
        if self._factory:
            return _Serialised(self._factory(token_file))
        from .integrations.google import Google

        return _Serialised(Google(token_file, "primary"))

    def reload(self) -> None:
        self.people = {}
        legacy = Path(self.settings.google_token_file)
        for person in self.person_ids:
            path = self.token_path(person)
            if not path.exists() and person == self.person_ids[0] and legacy.exists():
                path = legacy  # token made by scripts/google_auth.py before multi-account support
            if path.exists():
                try:
                    self.people[person] = self._make(str(path))
                except Exception as e:  # a revoked or broken token should not stop the service
                    log.warning("Google account for %s could not load: %s", person, e)

    def forget_cached(self) -> None:
        """Drop remembered Google reads (the app's Refresh button)."""
        for g in self.people.values():
            if hasattr(g, "forget"):
                g.forget()

    def disconnect(self, person: str) -> None:
        self.token_path(person).unlink(missing_ok=True)
        self.people.pop(person, None)

    # ---------- access ----------
    def get(self, person: str | None = None):
        person = person or self.owner
        if person not in self.people:
            raise RuntimeError(f"{self.display_name(person)}'s Google account is not connected yet. "
                               "Connect it in the app under Settings.")
        return self.people[person]

    def status(self) -> list[dict]:
        return [{"person": p, "name": self.display_name(p), "connected": p in self.people,
                 "gmail": bool(p in self.people and getattr(self.people[p], "has_gmail", True))}
                for p in self.person_ids]

    def __bool__(self) -> bool:
        return bool(self.people)

    # ---------- merged calendar ----------
    def events_all(self, days: int = 1) -> list[dict]:
        merged: dict[str, dict] = {}

        def add(events, who):
            for e in events:
                key = e.get("uid") or e.get("id")
                if key in merged:
                    if who not in merged[key]["who"]:
                        merged[key]["who"].append(who)
                else:
                    merged[key] = {**e, "who": [who]}

        # Each person's calendar is fetched at the same time (they have separate locks).
        jobs = [(self.display_name(p), g, None) for p, g in self.people.items()]
        family = self.settings.family_calendar_id
        if family and family != "primary" and self.owner in self.people:
            jobs.append(("Family", self.people[self.owner], family))

        def fetch(job):
            who, g, cal = job
            try:
                return who, (g.events(days=days, calendar_id=cal) if cal else g.events(days=days))
            except Exception as e:
                log.warning("calendar for %s failed: %s", who, e)
                return who, []

        with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
            for who, events in pool.map(fetch, jobs):
                add(events, who)
        return sorted(merged.values(), key=lambda e: e.get("start") or "")
