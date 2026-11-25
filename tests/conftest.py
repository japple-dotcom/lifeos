import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lifeos.accounts import GoogleAccounts  # noqa: E402
from lifeos.config import load_settings  # noqa: E402
from lifeos.store import Store  # noqa: E402
from lifeos.tools import Services  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_environment():
    """Each test starts with the environment the last one found: the demo sets LIFEOS_* keys for its own process, and
    left behind they made later setup tests think Life OS was already set up (seen only in CI's test order)."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


class FakeHA:
    def __init__(self):
        self.calls = []

    def summary(self):
        return [{"entity_id": "light.kitchen", "name": "Kitchen", "state": "on"}]

    def state(self, entity_id):
        return {"entity_id": entity_id, "state": "locked", "attributes": {"friendly_name": "Front door"}}

    def states(self, domain=None):
        return [{"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}}]

    def call_service(self, service, data=None):
        self.calls.append((service, data))
        return [{"entity_id": (data or {}).get("entity_id")}]


class FakeGoogle:
    def __init__(self, events=None, gmail=True):
        self.tasks, self.drafts_sent, self.completed = [], [], []
        self._events = events if events is not None else [
            {"id": "e1", "uid": "u1", "summary": "Swimming", "start": "2026-11-25T17:00:00Z"}]
        self.has_gmail = gmail

    def complete_task(self, list_name, task_id):
        self.completed.append((list_name, task_id))
        return {}

    def add_task(self, list_name, title, notes="", due=None):
        for i, t in enumerate(self.tasks):  # same duplicate rule as the real Google wrapper
            if t[0] == list_name and " ".join(t[1].lower().split()) == " ".join(title.lower().split()):
                return {"id": f"t{i + 1}", "duplicate": True}
        self.tasks.append((list_name, title, notes, due))
        return {"id": f"t{len(self.tasks)}"}

    def list_tasks(self, list_name, include_completed=False):
        return [{"id": f"t{i + 1}", "title": t[1], "notes": t[2], "due": t[3] or ""}
                for i, t in enumerate(self.tasks) if t[0] == list_name]

    def events(self, days=1, calendar_id=None):
        return list(self._events)

    def list_names(self):
        return sorted({t[0] for t in self.tasks} | {"Gym"})

    def mark_handled(self, ids):
        self.handled = list(ids)
        return len(ids)

    def get_draft(self, draft_id):
        return {"to": "office@school.example", "subject": "Friday trip", "body": "Yes, Ava can go on the trip."}

    def add_event(self, summary, start, end, description="", location="", calendar_id=None):
        self.added_event = (summary, calendar_id)
        return {"id": "ev1"}

    def search_mail(self, query, limit=15):
        if not self.has_gmail:
            raise RuntimeError("This person has not given Life OS access to their Gmail.")
        return [{"id": "m1", "subject": "Hello"}]

    def send_draft(self, draft_id):
        self.drafts_sent.append(draft_id)
        return {"id": draft_id}


class FakeNotifier:
    def __init__(self):
        self.pushes = []

    def push(self, message, title="Life OS", priority=3, actions=None, tags=""):
        self.pushes.append({"message": message, "title": title, "actions": actions or []})


class ScriptedClaude:
    """Mimics anthropic.Anthropic().messages.create with a fixed list of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        blocks, stop = self.responses.pop(0)
        content = [SimpleNamespace(**b) for b in blocks]
        return SimpleNamespace(content=content, stop_reason=stop)


def tool_use(id_, name, input_):
    return {"type": "tool_use", "id": id_, "name": name, "input": input_}


def text(t):
    return {"type": "text", "text": t}


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("LIFEOS_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("LIFEOS_API_KEY", "test-key")
    monkeypatch.setenv("LIFEOS_PUBLIC_URL", "http://lifeos.test")
    settings = load_settings(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")
    me = FakeGoogle()
    wife = FakeGoogle(events=[
        {"id": "e1", "uid": "u1", "summary": "Swimming", "start": "2026-11-25T17:00:00Z"},  # shared invite
        {"id": "e9", "uid": "u9", "summary": "Dentist", "start": "2026-11-25T09:00:00Z"}], gmail=False)
    accounts = GoogleAccounts(settings, people={"me": me, "wife": wife})
    accounts.me, accounts.wife = me, wife
    return Services(settings=settings, store=Store(":memory:"), ha=FakeHA(), google=accounts,
                    notifier=FakeNotifier())
