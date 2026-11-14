"""SQLite storage: conversations, audit events, clinics, slots, bookings, handoffs.

Everything here is demo data. Clinics are fictional and slots are generated.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "demo.db"))

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    phone TEXT PRIMARY KEY,
    messages TEXT NOT NULL DEFAULT '[]',
    opted_out INTEGER NOT NULL DEFAULT 0,
    last_offered TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clinics (
    id INTEGER PRIMARY KEY,
    name TEXT, town TEXT, address TEXT, lat REAL, lon REAL, phone TEXT
);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    slot_id INTEGER NOT NULL,
    clinic_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Two daily patterns so neighbouring clinics do not offer identical times.
SLOT_TIMES = [[(9, 40), (13, 20), (17, 15)], [(8, 30), (11, 0), (16, 45)]]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def query(sql: str, params=()) -> list[sqlite3.Row]:
    with _lock:
        return _conn.execute(sql, params).fetchall()


def execute(sql: str, params=()) -> sqlite3.Cursor:
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


def _seed_slots() -> None:
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    added = 0
    while added < 6:
        day += timedelta(days=1)
        if day.weekday() >= 5:  # skip weekends
            continue
        added += 1
        for clinic in query("SELECT id FROM clinics"):
            for hour, minute in SLOT_TIMES[clinic["id"] % 2]:
                start = day.replace(hour=hour, minute=minute)
                execute(
                    "INSERT INTO slots (clinic_id, start_at) VALUES (?, ?)",
                    (clinic["id"], start.isoformat(timespec="minutes")),
                )


def init(reset: bool = False) -> None:
    """Create tables and seed demo data. With reset=True, wipe everything first."""
    with _lock:
        if reset:
            for table in ("conversations", "events", "clinics", "slots", "bookings", "handoffs"):
                _conn.execute(f"DROP TABLE IF EXISTS {table}")
        _conn.executescript(SCHEMA)
        _conn.commit()
        if not query("SELECT 1 FROM clinics LIMIT 1"):
            for c in json.loads((DATA_DIR / "clinics.json").read_text()):
                execute(
                    "INSERT INTO clinics (id, name, town, address, lat, lon, phone) VALUES (?,?,?,?,?,?,?)",
                    (c["id"], c["name"], c["town"], c["address"], c["lat"], c["lon"], c["phone"]),
                )
            _seed_slots()


# ---------- conversations ----------

def get_conversation(phone: str) -> dict:
    rows = query("SELECT * FROM conversations WHERE phone = ?", (phone,))
    if not rows:
        execute("INSERT INTO conversations (phone, updated_at) VALUES (?, ?)", (phone, now_iso()))
        return {"phone": phone, "messages": [], "opted_out": False, "last_offered": []}
    row = rows[0]
    return {
        "phone": phone,
        "messages": json.loads(row["messages"]),
        "opted_out": bool(row["opted_out"]),
        "last_offered": json.loads(row["last_offered"]),
    }


def save_messages(phone: str, messages: list) -> None:
    execute(
        "UPDATE conversations SET messages = ?, updated_at = ? WHERE phone = ?",
        (json.dumps(messages), now_iso(), phone),
    )


def set_opted_out(phone: str, opted_out: bool) -> None:
    get_conversation(phone)
    execute("UPDATE conversations SET opted_out = ? WHERE phone = ?", (int(opted_out), phone))


def set_last_offered(phone: str, slot_ids: list[int]) -> None:
    get_conversation(phone)
    execute("UPDATE conversations SET last_offered = ? WHERE phone = ?", (json.dumps(slot_ids), phone))


# ---------- audit trail ----------

def log_event(phone: str, kind: str, detail) -> None:
    execute(
        "INSERT INTO events (phone, ts, kind, detail) VALUES (?, ?, ?, ?)",
        (phone, now_iso(), kind, json.dumps(detail)),
    )


def get_events(phone: str, limit: int = 100) -> list[dict]:
    rows = query("SELECT * FROM events WHERE phone = ? ORDER BY id DESC LIMIT ?", (phone, limit))
    return [
        {"id": r["id"], "ts": r["ts"], "kind": r["kind"], "detail": json.loads(r["detail"])}
        for r in reversed(rows)
    ]
