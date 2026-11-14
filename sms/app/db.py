import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_phone TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  category TEXT,
  urgency TEXT,
  summary TEXT,
  language TEXT DEFAULT 'en',
  assigned_chw TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER REFERENCES cases(id),
  sender TEXT NOT NULL,          -- patient | chw | dashboard | system
  from_phone TEXT,
  to_phone TEXT,
  body TEXT,
  channel TEXT,                  -- twilio | dev | inbound | failed
  sid TEXT,
  created_at REAL
);
-- Clinic calendar: one patient per slot. The partial unique index (created in init) is what
-- makes "slot taken in the meantime" safe even if two people pick it at once.
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER REFERENCES cases(id),
  patient_phone TEXT NOT NULL,
  service_id TEXT NOT NULL,
  start_ts REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'booked',   -- booked | cancelled
  reminded INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS optouts (phone TEXT PRIMARY KEY, created_at REAL);
-- One account per phone number: who they are and what they told us last time.
CREATE TABLE IF NOT EXISTS patients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT UNIQUE NOT NULL,
  first_name TEXT,
  last_name TEXT,
  zip TEXT,
  town TEXT,
  income TEXT,
  language TEXT,
  created_at REAL,
  last_seen REAL
);
-- athenahealth patient id per phone, so the name/date-of-birth question is asked only once.
CREATE TABLE IF NOT EXISTS athena_patients (phone TEXT PRIMARY KEY, patient_id TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_cases_phone ON cases(patient_phone, status);
CREATE INDEX IF NOT EXISTS idx_messages_case ON messages(case_id);
"""

# Columns added after the first version; added in place so old databases keep working.
CASE_EXTRAS = {
    "stage": "TEXT",        # where the patient is in the booking questions (see relay.py)
    "income": "TEXT",       # low | standard
    "zip": "TEXT",
    "town": "TEXT",
    "offers": "TEXT",       # JSON list of visit options last texted to the patient
    "resched_id": "INTEGER",  # booking being moved, kept until the new one is picked
    "pick": "TEXT",         # JSON of the visit picked while we ask name + date of birth (athenahealth)
}
BOOKING_EXTRAS = {"external_id": "TEXT"}  # athenahealth appointmentid


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        have = {r[1] for r in c.execute("PRAGMA table_info(cases)")}
        for col, typ in CASE_EXTRAS.items():
            if col not in have:
                c.execute(f"ALTER TABLE cases ADD COLUMN {col} {typ}")
        have = {r[1] for r in c.execute("PRAGMA table_info(bookings)")}
        for col, typ in BOOKING_EXTRAS.items():
            if col not in have:
                c.execute(f"ALTER TABLE bookings ADD COLUMN {col} {typ}")
        # One booking per slot: per start time for mock clinics, per athenahealth appointment id
        # for athena clinics (two providers can share a start time).
        c.execute("DROP INDEX IF EXISTS uq_slot")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_slot_ext ON bookings"
                  "(service_id, start_ts, IFNULL(external_id, '')) WHERE status='booked'")


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- cases
def open_case_for(phone: str):
    with connect() as c:
        return c.execute(
            "SELECT * FROM cases WHERE patient_phone=? AND status='open' ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()


def get_case(case_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()


def create_case(phone: str, triage: dict) -> int:
    t = now()
    with connect() as c:
        cur = c.execute(
            "INSERT INTO cases(patient_phone, category, urgency, summary, language, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (phone, triage["category"], triage["urgency"], triage["summary"], triage["language"], t, t),
        )
        return cur.lastrowid


def update_case(case_id: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE cases SET {cols} WHERE id=?", (*fields.values(), case_id))


def list_cases(status: str | None = None):
    q = """SELECT c.*,
             (SELECT body FROM messages m WHERE m.case_id=c.id AND m.sender='patient'
              ORDER BY m.id DESC LIMIT 1) AS last_patient_message,
             (SELECT COUNT(*) FROM bookings b WHERE b.case_id=c.id AND b.status='booked') AS booked,
             p.id AS patient_id, p.first_name, p.last_name
           FROM cases c LEFT JOIN patients p ON p.phone = c.patient_phone"""
    args: tuple = ()
    if status:
        q += " WHERE c.status=?"
        args = (status,)
    q += """ ORDER BY c.status='open' DESC,
             CASE c.urgency WHEN 'emergency' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
             c.updated_at DESC"""
    with connect() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ---------------------------------------------------------------- messages
def add_message(case_id, sender, from_phone, to_phone, body, channel, sid=None) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO messages(case_id, sender, from_phone, to_phone, body, channel, sid, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (case_id, sender, from_phone, to_phone, body, channel, sid, now()),
        )
        if case_id:
            c.execute("UPDATE cases SET updated_at=? WHERE id=?", (now(), case_id))


def case_messages(case_id: int):
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE case_id=? ORDER BY id", (case_id,)).fetchall()]


def is_first_contact(phone: str) -> bool:
    """True if we have never texted this phone before."""
    with connect() as c:
        return c.execute("SELECT 1 FROM messages WHERE to_phone=? LIMIT 1", (phone,)).fetchone() is None


def phone_messages(phone: str, since_id: int = 0):
    """Everything sent to or from one phone (used by the dev phone simulator)."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE (to_phone=? OR from_phone=?) AND id>? ORDER BY id",
            (phone, phone, since_id)).fetchall()]


# ---------------------------------------------------------------- bookings
def taken_external(service_id: str) -> set[str]:
    with connect() as c:
        return {r[0] for r in c.execute(
            "SELECT external_id FROM bookings WHERE service_id=? AND status='booked' AND external_id IS NOT NULL",
            (service_id,))}


def taken_slots(service_id: str) -> set[float]:
    with connect() as c:
        return {r[0] for r in c.execute(
            "SELECT start_ts FROM bookings WHERE service_id=? AND status='booked'", (service_id,))}


def book(case_id: int, phone: str, service_id: str, start_ts: float, external_id: str | None = None) -> int | None:
    """Returns the booking id, or None when someone else holds the slot."""
    try:
        with connect() as c:
            # Booked less than a day ahead: no separate 24-hour reminder is needed.
            reminded = 1 if start_ts - now() <= 24 * 3600 else 0
            cur = c.execute(
                "INSERT INTO bookings(case_id, patient_phone, service_id, start_ts, reminded, created_at, external_id)"
                " VALUES (?,?,?,?,?,?,?)", (case_id, phone, service_id, start_ts, reminded, now(), external_id))
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_booking(booking_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()


def active_booking(phone: str):
    with connect() as c:
        return c.execute(
            "SELECT * FROM bookings WHERE patient_phone=? AND status='booked' AND start_ts>?"
            " ORDER BY start_ts LIMIT 1", (phone, now())).fetchone()


def cancel_booking(booking_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))


def case_bookings(case_id: int):
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM bookings WHERE case_id=? ORDER BY id", (case_id,)).fetchall()]


def due_reminders(at: float):
    with connect() as c:
        return c.execute(
            "SELECT * FROM bookings WHERE status='booked' AND reminded=0 AND start_ts>? AND start_ts<=?",
            (at, at + 24 * 3600)).fetchall()


def mark_reminded(booking_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE bookings SET reminded=1 WHERE id=?", (booking_id,))


# ---------------------------------------------------------------- opt-out
def opt_out(phone: str) -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO optouts(phone, created_at) VALUES (?,?)", (phone, now()))


def opt_in(phone: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM optouts WHERE phone=?", (phone,))


def is_opted_out(phone: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM optouts WHERE phone=?", (phone,)).fetchone() is not None


# ---------------------------------------------------------------- athenahealth patients
def athena_patient(phone: str) -> str | None:
    with connect() as c:
        r = c.execute("SELECT patient_id FROM athena_patients WHERE phone=?", (phone,)).fetchone()
        return r[0] if r else None


def save_athena_patient(phone: str, patient_id: str) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO athena_patients(phone, patient_id, created_at) VALUES (?,?,?)",
                  (phone, patient_id, now()))


# ---------------------------------------------------------------- patient accounts
def patient_code(patient_id: int) -> str:
    """Short ID a person can read out on the phone, e.g. TB-1001."""
    return f"TB-{1000 + patient_id}"


def get_patient(phone: str):
    with connect() as c:
        return c.execute("SELECT * FROM patients WHERE phone=?", (phone,)).fetchone()


def create_patient(phone: str, language: str = "en"):
    t = now()
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO patients(phone, language, created_at, last_seen) VALUES (?,?,?,?)",
                  (phone, language, t, t))
    return get_patient(phone)


def update_patient(phone: str, **fields) -> None:
    fields["last_seen"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE patients SET {cols} WHERE phone=?", (*fields.values(), phone))
