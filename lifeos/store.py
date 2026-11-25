"""SQLite store: pending approvals, chore log, event log and a small notes table."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | failed
    result TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS chore_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chore TEXT NOT NULL,
    who TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    role TEXT NOT NULL,          -- user | assistant
    text TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    due_date TEXT NOT NULL,            -- YYYY-MM-DD
    category TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    remind_days INTEGER DEFAULT 42,
    repeat_months INTEGER DEFAULT 12,  -- 0 = one-off
    reminded_for TEXT,                 -- the due_date a reminder was already created for
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY,
    person TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meals (
    day TEXT PRIMARY KEY,              -- YYYY-MM-DD
    dinner TEXT NOT NULL,
    ingredients TEXT DEFAULT '[]',     -- JSON list
    notes TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS held_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_meta (
    person TEXT NOT NULL,
    task_id TEXT NOT NULL,
    list_name TEXT DEFAULT '',
    category TEXT DEFAULT '',
    project_id INTEGER,
    step INTEGER,                      -- order within a project
    priority INTEGER DEFAULT 3,        -- 1 urgent, 2 high, 3 normal, 4 low
    timescale TEXT DEFAULT 'this_month',
    effort_min INTEGER DEFAULT 30,
    next_action TEXT DEFAULT '',
    recurrence TEXT DEFAULT '',        -- '', weekly, monthly, quarterly, yearly, every_N_months, every_N_weeks
    who TEXT DEFAULT '',
    original TEXT DEFAULT '',
    done_at TEXT,                      -- ticked off (in Life OS, Gemini or the Google Tasks app)
    created_at TEXT NOT NULL,
    PRIMARY KEY (person, task_id)
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    goal TEXT DEFAULT '',
    category TEXT DEFAULT '',
    list_name TEXT NOT NULL,
    person TEXT NOT NULL,
    status TEXT DEFAULT 'active',      -- active | done
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS home_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area TEXT NOT NULL,                -- room or place: Landing, Kitchen, Car, Boiler, People
    item TEXT NOT NULL,                -- what it is: Ceiling light, Spotlights, Tyres, Mum
    details TEXT NOT NULL,             -- the useful facts: "B22 bayonet, 8.5W LED, warm white 2700K"
    updated_at TEXT NOT NULL,
    UNIQUE(area, item)
);
CREATE TABLE IF NOT EXISTS solutions (
    person TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,              -- working | ready | error
    data TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (person, task_id)
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trade TEXT DEFAULT '',             -- electrician, plumber, gas engineer, dentist, cleaner...
    company TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    website TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    favourite INTEGER DEFAULT 0,       -- the household's go-to for this trade
    last_used TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,              -- whose Gmail sent it
    task_id TEXT NOT NULL,
    list_name TEXT NOT NULL,
    contact_id INTEGER,
    contact_name TEXT NOT NULL,
    contact_email TEXT DEFAULT '',
    trade TEXT DEFAULT '',
    task_title TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    thread_id TEXT DEFAULT '',
    slots TEXT DEFAULT '[]',           -- JSON list of offered slots
    status TEXT NOT NULL,              -- drafted | sent | chased | replied | booked | visited | done | cancelled
    sent_at TEXT NOT NULL,
    replied_at TEXT,
    reply_snippet TEXT DEFAULT '',
    chased_at TEXT,
    appointment TEXT DEFAULT '',       -- JSON {start, end, event_id}
    draft_id TEXT DEFAULT '',          -- Gmail draft waiting to be sent from Gmail
    reminder_draft_id TEXT DEFAULT '', -- chase reminder waiting in Gmail drafts
    sent_count INTEGER DEFAULT 0,      -- our messages in the thread when the reminder was drafted
    gtask_id TEXT DEFAULT '',          -- the matching task in the Google Tasks "Waiting on" list
    suggested TEXT DEFAULT ''          -- JSON {start, label, duration_min}: the time their reply agreed to
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- bought | done | added | ordered | booked
    key TEXT NOT NULL,                 -- normalised item or job, e.g. "toilet roll"
    title TEXT NOT NULL,
    person TEXT DEFAULT '',
    source TEXT DEFAULT '',            -- app | google_tasks | gemini | receipt | amazon | order
    list_name TEXT DEFAULT '',
    qty REAL DEFAULT 1,
    ref TEXT,                          -- de-duplicates the same event seen twice (task id, email id)
    data TEXT DEFAULT '',              -- JSON
    at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS activity_ref ON activity(kind, ref);
CREATE INDEX IF NOT EXISTS activity_key ON activity(kind, key, at);
CREATE TABLE IF NOT EXISTS prediction_state (
    pid TEXT PRIMARY KEY,              -- e.g. shopping:toilet roll
    status TEXT NOT NULL,              -- added | snoozed | dismissed | never
    until TEXT DEFAULT '',             -- YYYY-MM-DD: not shown again before this day
    ref TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    key TEXT PRIMARY KEY,              -- normalised item, e.g. "toilet roll"
    title TEXT NOT NULL,               -- the usual product, e.g. "Andrex Classic Clean 16 rolls"
    asin TEXT DEFAULT '',
    url TEXT DEFAULT '',
    retailer TEXT DEFAULT 'Amazon',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_tasks (
    person TEXT NOT NULL,
    task_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (person, task_id)
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Small records for the v0.15 features, one kind each: car, subscription, purchase, profile (gift memory),
-- manual (home manual), school_day, covered (childcare sorted), energy_run, care (appliance cycles).
CREATE TABLE IF NOT EXISTS records (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    data TEXT NOT NULL,                -- JSON
    at TEXT DEFAULT '',                -- a date the record is about (purchase day, school day...), for ranges
    updated_at TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS records_at ON records(kind, at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Result:
    """A finished query: the rows (already read), how many rows changed, and the new row's id."""
    __slots__ = ("_rows", "rowcount", "lastrowid")

    def __init__(self, rows: list, rowcount: int, lastrowid):
        self._rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchall(self) -> list:
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(pending_actions)")}
            if "expires_at" not in cols:  # databases created by v0.1-0.3
                self._db.execute("ALTER TABLE pending_actions ADD COLUMN expires_at TEXT")
            chore_cols = {r[1] for r in self._db.execute("PRAGMA table_info(chore_log)")}
            if "value" not in chore_cols:  # databases created by v0.1-0.4
                self._db.execute("ALTER TABLE chore_log ADD COLUMN value REAL DEFAULT 0")
                self._db.execute("ALTER TABLE chore_log ADD COLUMN paid_at TEXT")
            req_cols = {r[1] for r in self._db.execute("PRAGMA table_info(trade_requests)")}
            for col, kind in (("task_title", "TEXT DEFAULT ''"), ("draft_id", "TEXT DEFAULT ''"),  # v0.10 -> v0.11
                              ("reminder_draft_id", "TEXT DEFAULT ''"), ("sent_count", "INTEGER DEFAULT 0"),
                              ("gtask_id", "TEXT DEFAULT ''"), ("suggested", "TEXT DEFAULT ''")):
                if col not in req_cols:
                    self._db.execute(f"ALTER TABLE trade_requests ADD COLUMN {col} {kind}")
            meal_cols = {r[1] for r in self._db.execute("PRAGMA table_info(meals)")}
            if "rating" not in meal_cols:  # v0.15: liked (1) or not again (-1)
                self._db.execute("ALTER TABLE meals ADD COLUMN rating INTEGER DEFAULT 0")
            meta_cols = {r[1] for r in self._db.execute("PRAGMA table_info(task_meta)")}
            if "done_at" not in meta_cols:  # databases created by v0.8-0.10
                self._db.execute("ALTER TABLE task_meta ADD COLUMN done_at TEXT")
            self._db.commit()

    # ---------- records (v0.15 features) ----------
    def put(self, kind: str, rid: str, data: dict, at: str = "") -> dict:
        self._exec("INSERT INTO records (kind, id, data, at, updated_at) VALUES (?,?,?,?,?) ON CONFLICT(kind, id) DO "
                   "UPDATE SET data=excluded.data, at=excluded.at, updated_at=excluded.updated_at",
                   (kind, str(rid), json.dumps(data), at, _now()))
        return {**data, "id": str(rid)}

    def add_new(self, kind: str, rid: str, data: dict, at: str = "") -> bool:
        """Only if not there already (e.g. the same receipt line read twice)."""
        try:
            self._exec("INSERT INTO records (kind, id, data, at, updated_at) VALUES (?,?,?,?,?)",
                       (kind, str(rid), json.dumps(data), at, _now()))
            return True
        except sqlite3.IntegrityError:
            return False

    def get(self, kind: str, rid: str) -> dict | None:
        r = self._exec("SELECT id, data, at, updated_at FROM records WHERE kind=? AND id=?", (kind, str(rid))).fetchone()
        return {**json.loads(r["data"]), "id": r["id"], "at": r["at"], "updated_at": r["updated_at"]} if r else None

    def records(self, kind: str, since: str = "", until: str = "") -> list[dict]:
        sql, args = "SELECT id, data, at, updated_at FROM records WHERE kind=?", [kind]
        if since:
            sql, args = sql + " AND at >= ?", [*args, since]
        if until:
            sql, args = sql + " AND at <= ?", [*args, until]
        return [{**json.loads(r["data"]), "id": r["id"], "at": r["at"], "updated_at": r["updated_at"]}
                for r in self._exec(sql + " ORDER BY at, id", tuple(args)).fetchall()]

    def remove(self, kind: str, rid: str) -> bool:
        return self._exec("DELETE FROM records WHERE kind=? AND id=?", (kind, str(rid))).rowcount == 1

    def rate_meal(self, day: str, rating: int) -> bool:
        return self._exec("UPDATE meals SET rating=? WHERE day=?", (max(-1, min(1, rating)), day)).rowcount == 1

    def meal_history(self, since: str) -> list[dict]:
        rows = self._exec("SELECT day, dinner, ingredients, rating FROM meals WHERE day >= ? AND dinner != '' ORDER BY day",
                          (since,)).fetchall()
        return [dict(r) | {"ingredients": json.loads(r["ingredients"] or "[]")} for r in rows]

    def _exec(self, sql: str, params: tuple = ()) -> _Result:
        # Rows are read while the lock is held: several requests share this connection, and reading a cursor after
        # another thread has run a statement can hand back the wrong rows (seen on the busy Home screen).
        with self._lock:
            cur = self._db.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            self._db.commit()
            return _Result(rows, cur.rowcount, cur.lastrowid)

    # pending approvals
    def add_pending(self, tool: str, args: dict, reason: str = "", ttl_minutes: int = 24 * 60) -> str:
        """Queue an action. The same tool + arguments already waiting returns the existing id (no duplicates)."""
        args_json = json.dumps(args, sort_keys=True)
        self.expire_pending()
        row = self._exec("SELECT id FROM pending_actions WHERE status='pending' AND tool=? AND args=?",
                         (tool, args_json)).fetchone()
        if row:
            return row["id"]
        pid = uuid.uuid4().hex[:12]
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")
        self._exec(
            "INSERT INTO pending_actions (id, tool, args, reason, created_at, expires_at) VALUES (?,?,?,?,?,?)",
            (pid, tool, args_json, reason, _now(), expires),
        )
        return pid

    def expire_pending(self) -> int:
        cur = self._exec("UPDATE pending_actions SET status='expired', decided_at=? "
                         "WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < ?", (_now(), _now()))
        return cur.rowcount

    def get_pending(self, pid: str) -> dict | None:
        self.expire_pending()
        row = self._exec("SELECT * FROM pending_actions WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["args"] = json.loads(d["args"])
        return d

    def list_pending(self) -> list[dict]:
        self.expire_pending()
        rows = self._exec("SELECT * FROM pending_actions WHERE status='pending' ORDER BY created_at").fetchall()
        return [dict(r) | {"args": json.loads(r["args"])} for r in rows]

    def decide(self, pid: str, status: str, result: str = "", only_if_pending: bool = False) -> bool:
        """Returns True if the row changed. only_if_pending makes the claim atomic (no double execution)."""
        sql = "UPDATE pending_actions SET status=?, result=?, decided_at=? WHERE id=?"
        if only_if_pending:
            sql += " AND status='pending'"
        return self._exec(sql, (status, result, _now(), pid)).rowcount == 1

    # conversation memory for the Ask screen (short-term, per person)
    def add_turn(self, person: str, role: str, text: str) -> None:
        self._exec("INSERT INTO conversation (person, role, text, at) VALUES (?,?,?,?)",
                   (person, role, text[:4000], _now()))

    def recent_turns(self, person: str, max_turns: int = 12, idle_minutes: int = 45) -> list[dict]:
        """The current conversation: recent turns, reset after a quiet spell."""
        rows = self._exec("SELECT role, text, at FROM conversation WHERE person=? ORDER BY id DESC LIMIT ?",
                          (person, max_turns)).fetchall()
        turns, last = [], datetime.now(timezone.utc)
        for r in rows:  # newest first; stop at the first long gap
            at = datetime.fromisoformat(r["at"])
            if (last - at) > timedelta(minutes=idle_minutes):
                break
            turns.append({"role": r["role"], "text": r["text"]})
            last = at
        turns.reverse()
        while turns and turns[0]["role"] != "user":  # a conversation must start with the person speaking
            turns.pop(0)
        return turns

    def clear_turns(self, person: str) -> None:
        self._exec("DELETE FROM conversation WHERE person=?", (person,))

    # housekeeping
    def count_events_since(self, kind_prefix: str, since_iso: str) -> int:
        return self._exec("SELECT COUNT(*) FROM event_log WHERE kind LIKE ? AND at >= ?",
                          (kind_prefix + "%", since_iso)).fetchone()[0]

    def prune(self, days: int = 90) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        self._exec("DELETE FROM event_log WHERE at < ?", (cutoff,))
        self._exec("DELETE FROM conversation WHERE at < ?", (cutoff,))
        self._exec("DELETE FROM briefs WHERE at < ?", (cutoff,))
        self._exec("DELETE FROM meals WHERE day < ?", (cutoff[:10],))
        self._exec("DELETE FROM pending_actions WHERE status != 'pending' AND created_at < ?", (cutoff,))

    # chores and pocket money
    def log_chore(self, chore: str, who: str | None = None, value: float = 0) -> int:
        return self._exec("INSERT INTO chore_log (chore, who, at, value) VALUES (?,?,?,?)",
                          (chore, who, _now(), value)).lastrowid

    def recent_chores(self, limit: int = 50) -> list[dict]:
        rows = self._exec("SELECT id, chore, who, at, value, paid_at FROM chore_log ORDER BY id DESC LIMIT ?",
                          (limit,)).fetchall()
        return [dict(r) for r in rows]

    def chores_since(self, since_iso: str, who: str | None = None) -> list[dict]:
        sql, args = "SELECT id, chore, who, at, value, paid_at FROM chore_log WHERE at >= ?", [since_iso]
        if who:
            sql += " AND who = ?"
            args.append(who)
        return [dict(r) for r in self._exec(sql + " ORDER BY id", tuple(args)).fetchall()]

    def unpaid_totals(self) -> dict[str, float]:
        # Savings put towards a goal are negative entries (v0.16), so they come off what's owed
        rows = self._exec("SELECT who, SUM(value) AS owed FROM chore_log WHERE paid_at IS NULL AND value != 0 "
                          "GROUP BY who").fetchall()
        return {r["who"]: round(r["owed"] or 0, 2) for r in rows if r["who"]}

    def mark_paid(self, who: str) -> float:
        owed = self.unpaid_totals().get(who, 0)
        self._exec("UPDATE chore_log SET paid_at=? WHERE who=? AND paid_at IS NULL AND value != 0", (_now(), who))
        return owed

    def undo_chore(self, chore_id: int) -> bool:
        return self._exec("DELETE FROM chore_log WHERE id=? AND paid_at IS NULL", (chore_id,)).rowcount == 1

    # routine outputs (morning brief etc.), shown on the Today screen
    def add_brief(self, name: str, text: str) -> None:
        self._exec("INSERT INTO briefs (name, text, at) VALUES (?,?,?)", (name, text[:6000], _now()))

    def latest_brief(self, names: tuple[str, ...] = ("morning_brief", "evening_reset", "weekly_review")) -> dict | None:
        marks = ",".join("?" * len(names))
        # Only '?' placeholders are interpolated; values are bound parameters.
        row = self._exec(f"SELECT name, text, at FROM briefs WHERE name IN ({marks}) ORDER BY id DESC LIMIT 1",  # nosec B608
                         names).fetchone()
        return dict(row) if row else None

    # renewals
    def add_renewal(self, name: str, due_date: str, category: str = "", notes: str = "",
                    remind_days: int = 42, repeat_months: int = 12) -> int:
        return self._exec(
            "INSERT INTO renewals (name, due_date, category, notes, remind_days, repeat_months, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (name, due_date, category, notes, remind_days, repeat_months, _now())).lastrowid

    def update_renewal(self, rid: int, **fields) -> bool:
        allowed = {k: v for k, v in fields.items()
                   if k in {"name", "due_date", "category", "notes", "remind_days", "repeat_months", "reminded_for"}}
        if not allowed:
            return False
        sets = ", ".join(f"{k}=?" for k in allowed)
        # Column names come from the fixed allow-list above; values are bound parameters.
        return self._exec(f"UPDATE renewals SET {sets} WHERE id=?",  # nosec B608
                          (*allowed.values(), rid)).rowcount == 1

    def delete_renewal(self, rid: int) -> bool:
        return self._exec("DELETE FROM renewals WHERE id=?", (rid,)).rowcount == 1

    def renewals(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM renewals ORDER BY due_date").fetchall()]

    def get_renewal(self, rid: int) -> dict | None:
        row = self._exec("SELECT * FROM renewals WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    # web push subscriptions
    def save_subscription(self, person: str, sub: dict) -> None:
        self._exec("INSERT INTO push_subscriptions (endpoint, person, data, created_at) VALUES (?,?,?,?) "
                   "ON CONFLICT(endpoint) DO UPDATE SET person=excluded.person, data=excluded.data",
                   (sub["endpoint"], person, json.dumps(sub), _now()))

    def subscriptions(self, person: str | None = None) -> list[dict]:
        sql, args = "SELECT person, data FROM push_subscriptions", ()
        if person:
            sql, args = sql + " WHERE person=?", (person,)
        return [{"person": r["person"], **json.loads(r["data"])} for r in self._exec(sql, args).fetchall()]

    def delete_subscription(self, endpoint: str) -> None:
        self._exec("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))

    # meal plan
    def set_meal(self, day: str, dinner: str, ingredients: list[str] | None = None, notes: str = "") -> None:
        self._exec("INSERT INTO meals (day, dinner, ingredients, notes, updated_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(day) DO UPDATE SET dinner=excluded.dinner, ingredients=excluded.ingredients, "
                   "notes=excluded.notes, updated_at=excluded.updated_at",
                   (day, dinner, json.dumps(ingredients or []), notes, _now()))

    def delete_meal(self, day: str) -> None:
        self._exec("DELETE FROM meals WHERE day=?", (day,))

    def known_ingredients(self, dinner: str) -> list[str]:
        """What went into this dinner last time it was planned (so it isn't worked out again)."""
        row = self._exec("SELECT ingredients FROM meals WHERE lower(dinner)=lower(?) AND ingredients != '[]' "
                         "ORDER BY day DESC LIMIT 1", (dinner.strip(),)).fetchone()
        return json.loads(row["ingredients"]) if row else []

    def meals_between(self, start: str, end: str) -> list[dict]:
        rows = self._exec("SELECT day, dinner, ingredients, notes FROM meals WHERE day >= ? AND day <= ? ORDER BY day",
                          (start, end)).fetchall()
        return [dict(r) | {"ingredients": json.loads(r["ingredients"] or "[]")} for r in rows]

    # notifications held during quiet hours
    def hold_notification(self, title: str, message: str) -> None:
        self._exec("INSERT INTO held_notifications (title, message, at) VALUES (?,?,?)", (title, message[:2000], _now()))

    def take_held_notifications(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT title, message, at FROM held_notifications ORDER BY id").fetchall()
            self._db.execute("DELETE FROM held_notifications")
            self._db.commit()
        return [dict(r) for r in rows]

    # to-do details (the extra fields Google Tasks has no room for)
    META_FIELDS = ("list_name", "category", "project_id", "step", "priority", "timescale", "effort_min",
                   "next_action", "recurrence", "who", "original")

    def move_task_refs(self, person: str, old_id: str, new_id: str, list_name: str) -> None:
        """A to-do moved to another list gets a new Google id: carry its details, plan and requests across."""
        self._exec("UPDATE task_meta SET task_id=?, list_name=? WHERE person=? AND task_id=?", (new_id, list_name, person, old_id))
        self._exec("UPDATE solutions SET task_id=? WHERE person=? AND task_id=?", (new_id, person, old_id))
        self._exec("UPDATE trade_requests SET task_id=?, list_name=? WHERE task_id=?", (new_id, list_name, old_id))

    # ---------- activity: everything done through Life OS, used to predict what's needed next ----------
    def record_activity(self, kind: str, key: str, title: str, person: str = "", source: str = "",
                        list_name: str = "", qty: float = 1, ref: str | None = None, data: dict | None = None,
                        at: str | None = None) -> int | None:
        try:
            return self._exec(
                "INSERT INTO activity (kind, key, title, person, source, list_name, qty, ref, data, at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kind, key, title[:200], person or "", source, list_name, qty, ref, json.dumps(data or {}),
                 at or _now())).lastrowid
        except sqlite3.IntegrityError:
            return None  # already recorded (same ref)

    def activity(self, kind: str | None = None, since: str = "", key: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM activity WHERE at >= ?", [since]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if key:
            sql += " AND key=?"
            args.append(key)
        rows = self._exec(sql + " ORDER BY at", tuple(args)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]

    def delete_activity(self, aid: int) -> None:
        self._exec("DELETE FROM activity WHERE id=?", (aid,))

    def prediction_states(self) -> dict[str, dict]:
        return {r["pid"]: dict(r) for r in self._exec("SELECT * FROM prediction_state").fetchall()}

    def set_prediction_state(self, pid: str, status: str, until: str = "", ref: str = "") -> None:
        self._exec("INSERT INTO prediction_state (pid, status, until, ref, updated_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(pid) DO UPDATE SET status=excluded.status, until=excluded.until, ref=excluded.ref, "
                   "updated_at=excluded.updated_at", (pid, status, until, ref, _now()))

    def clear_prediction_state(self, pid: str) -> None:
        self._exec("DELETE FROM prediction_state WHERE pid=?", (pid,))

    # usual products (what "toilet roll" means for this household, and where to buy it in one tap)
    def save_product(self, key: str, title: str, asin: str = "", url: str = "", retailer: str = "Amazon") -> None:
        self._exec("INSERT INTO products (key, title, asin, url, retailer, updated_at) VALUES (?,?,?,?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET title=excluded.title, asin=excluded.asin, url=excluded.url, "
                   "retailer=excluded.retailer, updated_at=excluded.updated_at",
                   (key, title[:200], asin, url, retailer, _now()))

    def products(self) -> dict[str, dict]:
        return {r["key"]: dict(r) for r in self._exec("SELECT * FROM products").fetchall()}

    def delete_product(self, key: str) -> None:
        self._exec("DELETE FROM products WHERE key=?", (key,))

    def mark_task_done(self, person: str, task_id: str) -> None:
        self._exec("UPDATE task_meta SET done_at=? WHERE person=? AND task_id=?", (_now(), person, task_id))

    def open_task_meta(self, person: str) -> list[dict]:
        """To-dos Life OS knows about that haven't been ticked off yet."""
        return [dict(r) for r in self._exec("SELECT * FROM task_meta WHERE person=? AND done_at IS NULL "
                                            "AND list_name != ''", (person,)).fetchall()]

    # Google Tasks items already looked at (so a Gemini to-do is only picked up once)
    def is_seen(self, person: str, task_id: str) -> bool:
        return self._exec("SELECT 1 FROM seen_tasks WHERE person=? AND task_id=?", (person, task_id)).fetchone() is not None

    def mark_seen(self, person: str, task_ids) -> None:
        for tid in task_ids:
            self._exec("INSERT OR IGNORE INTO seen_tasks (person, task_id, seen_at) VALUES (?,?,?)", (person, tid, _now()))

    # small bookkeeping values for background syncs (kept apart from the assistant's notes)
    def get_state(self, key: str, default: str = "") -> str:
        row = self._exec("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self._exec("INSERT INTO sync_state (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                   "value=excluded.value, updated_at=excluded.updated_at", (key, value, _now()))

    def set_task_meta(self, person: str, task_id: str, **fields) -> None:
        f = {k: v for k, v in fields.items() if k in self.META_FIELDS}
        cols = ", ".join(["person", "task_id", "created_at", *f])
        marks = ", ".join("?" * (3 + len(f)))
        updates = ", ".join(f"{k}=excluded.{k}" for k in f) or "person=excluded.person"
        # Column names come from META_FIELDS above; values are bound parameters.
        self._exec(f"INSERT INTO task_meta ({cols}) VALUES ({marks}) "  # nosec B608
                   f"ON CONFLICT(person, task_id) DO UPDATE SET {updates}", (person, task_id, _now(), *f.values()))

    def task_meta(self, person: str | None = None) -> dict[tuple[str, str], dict]:
        sql, args = "SELECT * FROM task_meta", ()
        if person:
            sql, args = sql + " WHERE person=?", (person,)
        return {(r["person"], r["task_id"]): dict(r) for r in self._exec(sql, args).fetchall()}

    def delete_task_meta(self, person: str, task_id: str) -> None:
        self._exec("DELETE FROM task_meta WHERE person=? AND task_id=?", (person, task_id))

    # projects
    def add_project(self, name: str, list_name: str, person: str, goal: str = "", category: str = "") -> int:
        return self._exec("INSERT INTO projects (name, goal, category, list_name, person, created_at) "
                          "VALUES (?,?,?,?,?,?)", (name, goal, category, list_name, person, _now())).lastrowid

    def projects(self, status: str | None = "active") -> list[dict]:
        sql, args = "SELECT * FROM projects", ()
        if status:
            sql, args = sql + " WHERE status=?", (status,)
        return [dict(r) for r in self._exec(sql + " ORDER BY id", args).fetchall()]

    def get_project(self, pid: int) -> dict | None:
        row = self._exec("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def set_project_status(self, pid: int, status: str) -> None:
        self._exec("UPDATE projects SET status=? WHERE id=?", (status, pid))

    # home details: what's in the house, so suggestions fit (bulb types, appliance models, sizes...)
    def set_home_detail(self, area: str, item: str, details: str) -> int:
        area, item = area.strip()[:60], item.strip()[:80]
        self._exec("INSERT INTO home_details (area, item, details, updated_at) VALUES (?,?,?,?) "
                   "ON CONFLICT(area, item) DO UPDATE SET details=excluded.details, updated_at=excluded.updated_at",
                   (area, item, details.strip()[:500], _now()))
        return self._exec("SELECT id FROM home_details WHERE area=? AND item=?", (area, item)).fetchone()["id"]

    def home_details(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM home_details ORDER BY area, item").fetchall()]

    def delete_home_detail(self, did: int) -> bool:
        return self._exec("DELETE FROM home_details WHERE id=?", (did,)).rowcount == 1

    # suggested solutions for to-dos
    def set_solution(self, person: str, task_id: str, status: str, data: dict | None = None) -> None:
        self._exec("INSERT INTO solutions (person, task_id, status, data, updated_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(person, task_id) DO UPDATE SET status=excluded.status, data=excluded.data, "
                   "updated_at=excluded.updated_at", (person, task_id, status, json.dumps(data or {}), _now()))

    def solutions(self, person: str) -> dict[str, dict]:
        rows = self._exec("SELECT task_id, status, data, updated_at FROM solutions WHERE person=?", (person,)).fetchall()
        return {r["task_id"]: {"status": r["status"], **json.loads(r["data"] or "{}"), "updated_at": r["updated_at"]}
                for r in rows}

    # contacts: tradespeople and services the household uses
    CONTACT_FIELDS = ("name", "trade", "company", "email", "phone", "website", "notes", "favourite")

    def save_contact(self, cid: int | None = None, **f) -> int:
        f = {k: (v.strip()[:300] if isinstance(v, str) else v) for k, v in f.items() if k in self.CONTACT_FIELDS}
        if f.get("trade"):
            f["trade"] = f["trade"].lower()
        if f.get("favourite"):  # one favourite per trade
            self._exec("UPDATE contacts SET favourite=0 WHERE trade=?", (f.get("trade", ""),))
        if cid:
            if f:
                sets = ", ".join(f"{k}=?" for k in f)
                # Column names come from CONTACT_FIELDS; values are bound parameters.
                self._exec(f"UPDATE contacts SET {sets} WHERE id=?", (*f.values(), cid))  # nosec B608
            return cid
        if f.get("email"):  # same email = same contact
            row = self._exec("SELECT id FROM contacts WHERE lower(email)=lower(?)", (f["email"],)).fetchone()
            if row:
                return self.save_contact(row["id"], **f)
        cols = ", ".join([*f, "created_at"])
        marks = ", ".join("?" * (len(f) + 1))
        return self._exec(f"INSERT INTO contacts ({cols}) VALUES ({marks})", (*f.values(), _now())).lastrowid  # nosec B608

    def contacts(self, trade: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM contacts", ()
        if trade:
            sql, args = sql + " WHERE trade=?", (trade.lower(),)
        return [dict(r) for r in self._exec(sql + " ORDER BY favourite DESC, last_used DESC, name", args).fetchall()]

    def get_contact(self, cid: int) -> dict | None:
        row = self._exec("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def delete_contact(self, cid: int) -> bool:
        return self._exec("DELETE FROM contacts WHERE id=?", (cid,)).rowcount == 1

    def touch_contact(self, cid: int) -> None:
        self._exec("UPDATE contacts SET last_used=? WHERE id=?", (_now(), cid))

    # requests sent to tradespeople, so replies can be spotted and chased
    def add_request(self, **f) -> int:
        f["slots"] = json.dumps(f.get("slots") or [])
        f.setdefault("status", "sent")
        f.setdefault("sent_at", _now())
        cols, marks = ", ".join(f), ", ".join("?" * len(f))
        # Keys are fixed by the callers in trades.py; values are bound parameters.
        return self._exec(f"INSERT INTO trade_requests ({cols}) VALUES ({marks})", tuple(f.values())).lastrowid  # nosec B608

    def update_request(self, rid: int, **f) -> None:
        for k in ("appointment", "suggested"):
            if k in f and f[k] is not None and not isinstance(f[k], str):
                f[k] = json.dumps(f[k])
        sets = ", ".join(f"{k}=?" for k in f)
        self._exec(f"UPDATE trade_requests SET {sets} WHERE id=?", (*f.values(), rid))  # nosec B608

    def _req(self, row) -> dict:
        d = dict(row)
        d["slots"] = json.loads(d.get("slots") or "[]")
        d["appointment"] = json.loads(d["appointment"]) if d.get("appointment") else None
        d["suggested"] = json.loads(d["suggested"]) if d.get("suggested") else None
        return d

    def requests(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        rows = self._exec("SELECT * FROM trade_requests ORDER BY id DESC").fetchall()
        out = [self._req(r) for r in rows]
        return [r for r in out if not statuses or r["status"] in statuses]

    def get_request(self, rid: int) -> dict | None:
        row = self._exec("SELECT * FROM trade_requests WHERE id=?", (rid,)).fetchone()
        return self._req(row) if row else None

    def request_for_task(self, task_id: str) -> dict | None:
        row = self._exec("SELECT * FROM trade_requests WHERE task_id=? AND status NOT IN ('cancelled', 'done') "
                         "ORDER BY id DESC LIMIT 1",
                         (task_id,)).fetchone()
        return self._req(row) if row else None

    # backups
    def backup_to(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(path)
            try:
                self._db.backup(dest)
            finally:
                dest.close()

    # events
    def log_event(self, kind: str, detail: str = "") -> None:
        self._exec("INSERT INTO event_log (kind, detail, at) VALUES (?,?,?)", (kind, detail[:4000], _now()))

    def recent_events(self, limit: int = 50) -> list[dict]:
        rows = self._exec("SELECT kind, detail, at FROM event_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # notes: the agent's small persistent memory (meal favourites, renewal dates, etc.)
    def set_note(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO notes (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, _now()),
        )

    def get_notes(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._exec("SELECT key, value FROM notes").fetchall()}
