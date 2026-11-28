"""Google Tasks, Calendar and Gmail through the official Google API client.

Run scripts/google_auth.py once to create the token file.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
HANDLED_LABEL = "Life OS/Handled"
DEFAULT_LIST = "@default"  # each person's main Google Tasks list ("My Tasks"); Gemini adds to-dos here

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"  # read, label, draft; sending still needs approval
BASE_SCOPES = [TASKS_SCOPE, CALENDAR_SCOPE]
SCOPES = BASE_SCOPES + [GMAIL_SCOPE]  # everything; each person chooses whether to include Gmail


def load_credentials(token_file: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(token_file)  # keeps the scopes that person granted
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return creds


class Google:
    def __init__(self, token_file: str, calendar_id: str = "primary", services: dict | None = None):
        """services lets tests inject fakes: {'tasks': ..., 'calendar': ..., 'gmail': ...}."""
        self.calendar_id = calendar_id
        if services is None:
            from googleapiclient.discovery import build

            creds = load_credentials(token_file)
            granted = set(creds.scopes or [])
            services = {
                "tasks": build("tasks", "v1", credentials=creds, cache_discovery=False),
                "calendar": build("calendar", "v3", credentials=creds, cache_discovery=False),
                "gmail": build("gmail", "v1", credentials=creds, cache_discovery=False)
                if GMAIL_SCOPE in granted else None,
            }
        self.tasks_api = services["tasks"]
        self.cal_api = services["calendar"]
        self.gmail_api = services.get("gmail")
        self._list_ids: dict[str, str] = {}

    # ---------- Tasks ----------
    def _list_id(self, name: str) -> str:
        if name == DEFAULT_LIST:  # the person's main list, whatever it's called (where Gemini adds to-dos)
            return DEFAULT_LIST
        if not self._list_ids:
            for tl in self.tasks_api.tasklists().list(maxResults=100).execute().get("items", []):
                self._list_ids[tl["title"].lower()] = tl["id"]
        key = name.lower()
        if key not in self._list_ids:
            created = self.tasks_api.tasklists().insert(body={"title": name}).execute()
            self._list_ids[key] = created["id"]
        return self._list_ids[key]

    def list_names(self) -> list[str]:
        res = self.tasks_api.tasklists().list(maxResults=100).execute()
        return [tl["title"] for tl in res.get("items", [])]

    def add_task(self, list_name: str, title: str, notes: str = "", due: str | None = None) -> dict:
        """Adds a task, unless an open task with the same title is already on the list."""
        wanted = " ".join(title.lower().split())
        for t in self.list_tasks(list_name):
            if " ".join(t["title"].lower().split()) == wanted:
                return {**t, "duplicate": True}
        body = {"title": title}
        if notes:
            body["notes"] = notes
        if due:  # YYYY-MM-DD; Google Tasks stores the date part only
            body["due"] = f"{due}T00:00:00.000Z"
        return self.tasks_api.tasks().insert(tasklist=self._list_id(list_name), body=body).execute()

    def list_tasks(self, list_name: str, include_completed: bool = False) -> list[dict]:
        res = self.tasks_api.tasks().list(
            tasklist=self._list_id(list_name), showCompleted=include_completed, maxResults=100
        ).execute()
        return [
            {"id": t["id"], "title": t.get("title", ""), "notes": t.get("notes", ""),
             "due": (t.get("due") or "")[:10], "status": t.get("status")}
            for t in res.get("items", [])
        ]

    def list_changed(self, list_name: str, since_iso: str) -> list[dict]:
        """Tasks changed since a moment (RFC 3339), including ticked-off and deleted ones."""
        out, token = [], None
        while True:
            res = self.tasks_api.tasks().list(
                tasklist=self._list_id(list_name), showCompleted=True, showHidden=True, showDeleted=True,
                updatedMin=since_iso, maxResults=100, pageToken=token).execute()
            out += [{"id": t["id"], "title": t.get("title", ""), "notes": t.get("notes", ""),
                     "due": (t.get("due") or "")[:10], "status": t.get("status"), "deleted": bool(t.get("deleted"))}
                    for t in res.get("items", [])]
            token = res.get("nextPageToken")
            if not token or len(out) >= 500:
                return out

    def update_task(self, list_name: str, task_id: str, title: str | None = None, notes: str | None = None,
                    due: str | None = None) -> dict:
        body = {k: v for k, v in (("title", title), ("notes", notes)) if v is not None}
        if due is not None:
            body["due"] = f"{due}T00:00:00.000Z" if due else None
        return self.tasks_api.tasks().patch(tasklist=self._list_id(list_name), task=task_id, body=body).execute()

    def complete_task(self, list_name: str, task_id: str) -> dict:
        return self.tasks_api.tasks().patch(
            tasklist=self._list_id(list_name), task=task_id, body={"status": "completed"}
        ).execute()

    def delete_task(self, list_name: str, task_id: str) -> None:
        self.tasks_api.tasks().delete(tasklist=self._list_id(list_name), task=task_id).execute()

    def delete_list(self, list_name: str) -> None:
        """Only for a list Life OS made for itself (the live test's own list); never the person's lists."""
        if list_name == DEFAULT_LIST:
            raise ValueError("Life OS never deletes the main list.")
        list_id = self._list_id(list_name)
        self.tasks_api.tasklists().delete(tasklist=list_id).execute()
        self._list_ids.pop(list_name.lower(), None)

    def move_task_due(self, list_name: str, task_id: str, due: str) -> dict:
        return self.tasks_api.tasks().patch(
            tasklist=self._list_id(list_name), task=task_id, body={"due": f"{due}T00:00:00.000Z"}
        ).execute()

    # ---------- Calendar ----------
    @property
    def has_gmail(self) -> bool:
        return self.gmail_api is not None

    def _gmail(self):
        if self.gmail_api is None:
            raise RuntimeError("This person has not given Life OS access to their Gmail.")
        return self.gmail_api

    def events(self, days: int = 1, start: datetime | None = None, calendar_id: str | None = None) -> list[dict]:
        # Days run midnight to midnight UK time (correct in both GMT and BST).
        start = start or datetime.now(UK).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)
        res = self.cal_api.events().list(
            calendarId=calendar_id or self.calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100,
        ).execute()
        return [
            {"id": e["id"], "uid": e.get("iCalUID", e["id"]),
             "summary": e.get("summary", ""), "location": e.get("location", ""),
             "start": e["start"].get("dateTime", e["start"].get("date")),
             "end": e["end"].get("dateTime", e["end"].get("date")),
             "description": (e.get("description") or "")[:500]}
            for e in res.get("items", [])
        ]

    def add_event(self, summary: str, start: str, end: str, description: str = "", location: str = "",
                  calendar_id: str | None = None) -> dict:
        """start/end: ISO datetime (2026-12-01T09:00:00) or date (2026-12-01) for all-day."""
        def when(v: str) -> dict:
            return {"date": v} if len(v) == 10 else {"dateTime": v, "timeZone": "Europe/London"}

        body = {"summary": summary, "start": when(start), "end": when(end)}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return self.cal_api.events().insert(calendarId=calendar_id or self.calendar_id, body=body).execute()

    def delete_event(self, event_id: str, calendar_id: str | None = None) -> None:
        """Only for entries Life OS made itself (a trip's "Away" entry, a booked admin hour)."""
        self.cal_api.events().delete(calendarId=calendar_id or self.calendar_id, eventId=event_id).execute()

    # ---------- Gmail ----------
    def search_mail(self, query: str, limit: int = 15) -> list[dict]:
        res = self._gmail().users().messages().list(userId="me", q=query, maxResults=limit).execute()
        out = []
        for m in res.get("messages", []):
            full = self._gmail().users().messages().get(
                userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            out.append({
                "id": m["id"], "thread_id": full.get("threadId"),
                "from": headers.get("From", ""), "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""), "snippet": full.get("snippet", ""),
                "link": f"https://mail.google.com/mail/u/0/#all/{m['id']}",
            })
        return out

    def _label_id(self, name: str) -> str:
        if not hasattr(self, "_labels"):
            self._labels = {}
        if name not in self._labels:
            labels = self._gmail().users().labels().list(userId="me").execute().get("labels", [])
            found = next((lb["id"] for lb in labels if lb["name"] == name), None)
            if not found:
                found = self._gmail().users().labels().create(
                    userId="me", body={"name": name, "labelListVisibility": "labelShow",
                                       "messageListVisibility": "show"}).execute()["id"]
            self._labels[name] = found
        return self._labels[name]

    def mark_handled(self, message_ids: list[str]) -> int:
        """Labels emails 'Life OS/Handled' so later inbox checks skip them. Leaves them unread."""
        if not message_ids:
            return 0
        self._gmail().users().messages().batchModify(
            userId="me", body={"ids": list(message_ids)[:1000], "addLabelIds": [self._label_id(HANDLED_LABEL)]}
        ).execute()
        return len(message_ids)

    def relabel(self, message_ids: list[str], add: tuple = (), remove: tuple = ()) -> int:
        """Adds and removes labels by name (e.g. the person's "Life OS" label becomes "Life OS/Added")."""
        if not message_ids:
            return 0
        body: dict = {"ids": list(message_ids)[:1000]}
        if add:
            body["addLabelIds"] = [self._label_id(n) for n in add]
        if remove:
            body["removeLabelIds"] = [self._label_id(n) for n in remove]
        self._gmail().users().messages().batchModify(userId="me", body=body).execute()
        return len(message_ids)

    def message_text(self, message_id: str, limit: int = 12000) -> str:
        """The plain text of an email (HTML-only emails are reduced to text)."""
        import re as _re
        full = self._gmail().users().messages().get(userId="me", id=message_id, format="full").execute()

        def walk(part, want):
            if part.get("mimeType") == want and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
            for sub in part.get("parts", []) or []:
                found = walk(sub, want)
                if found:
                    return found
            return ""
        payload = full.get("payload", {})
        text = walk(payload, "text/plain")
        if not text:
            html = walk(payload, "text/html")
            text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=_re.S | _re.I)
            text = _re.sub(r"<br\s*/?>|</p>|</tr>|</li>", "\n", text, flags=_re.I)
            text = _re.sub(r"<[^>]+>", " ", text)
            text = _re.sub(r"&nbsp;", " ", text)
            text = _re.sub(r"[ \t]+", " ", text)
        return text.strip()[:limit]

    def get_draft(self, draft_id: str) -> dict:
        d = self._gmail().users().drafts().get(userId="me", id=draft_id, format="full").execute()
        payload = d.get("message", {}).get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

        def text_of(part) -> str:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
            for sub in part.get("parts", []) or []:
                found = text_of(sub)
                if found:
                    return found
            return ""

        return {"to": headers.get("to", ""), "subject": headers.get("subject", ""), "body": text_of(payload)}

    def create_draft(self, to: str, subject: str, body: str, thread_id: str | None = None) -> dict:
        msg = EmailMessage()
        if to:  # a draft can be left without a recipient (the person adds it in Gmail)
            msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        return self._gmail().users().drafts().create(userId="me", body={"message": message}).execute()

    def send_email(self, to: str, subject: str, body: str, thread_id: str | None = None) -> dict:
        """Sends straight away (only ever called after the person tapped Send on the exact text).
        Returns the sent message, including its threadId so replies can be spotted."""
        d = self.create_draft(to, subject, body, thread_id)
        return self.send_draft(d["id"])

    def thread_messages(self, thread_id: str) -> list[dict]:
        t = self._gmail().users().threads().get(userId="me", id=thread_id, format="metadata",
                                                metadataHeaders=["From", "Date", "Subject"]).execute()
        out = []
        for m in t.get("messages", []):
            headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "from": headers.get("From", ""), "date": headers.get("Date", ""),
                        "subject": headers.get("Subject", ""), "snippet": m.get("snippet", ""),
                        "labels": m.get("labelIds", [])})
        return out

    def draft_exists(self, draft_id: str) -> bool:
        """False once the draft has been sent or deleted in Gmail."""
        from googleapiclient.errors import HttpError
        try:
            self._gmail().users().drafts().get(userId="me", id=draft_id, format="minimal").execute()
            return True
        except HttpError as e:
            if getattr(getattr(e, "resp", None), "status", None) == 404:
                return False
            raise

    def delete_draft(self, draft_id: str) -> None:
        from googleapiclient.errors import HttpError
        try:
            self._gmail().users().drafts().delete(userId="me", id=draft_id).execute()
        except HttpError as e:
            if getattr(getattr(e, "resp", None), "status", None) != 404:
                raise

    def send_draft(self, draft_id: str) -> dict:
        return self._gmail().users().drafts().send(userId="me", body={"id": draft_id}).execute()
