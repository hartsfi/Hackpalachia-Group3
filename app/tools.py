"""The tools the agent can call. Every fact in a reply must come from one of these.

The caller's phone number is supplied by the server, never by the model.
"""
import json
import math
import re
from datetime import datetime
from pathlib import Path

from . import db

PLACES = json.loads((Path(__file__).parent / "data" / "places.json").read_text())

TOOL_DEFS = [
    {
        "name": "find_clinics",
        "description": (
            "Find the nearest clinics to a place in Southeast Ohio and their next open "
            "appointment times. Call this as soon as you know where the person is. "
            "Returns up to 3 clinics, nearest first, each with distance in miles and "
            "up to 2 open slots (slot_id and time)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "A 5-digit ZIP code or a town name, e.g. '45769' or 'Pomeroy'."}
            },
            "required": ["location"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_open_slots",
        "description": "List more open appointment times at one clinic, soonest first. Use when the person wants a different day or time than the ones already offered.",
        "input_schema": {
            "type": "object",
            "properties": {"clinic_id": {"type": "integer", "description": "The clinic's id from find_clinics."}},
            "required": ["clinic_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "book_slot",
        "description": (
            "Book one appointment slot for the person texting. Only call this after they "
            "have clearly chosen a specific slot. If the slot was taken in the meantime the "
            "result says so and lists alternatives: tell the person and offer those."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"slot_id": {"type": "integer", "description": "The slot_id the person chose."}},
            "required": ["slot_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_my_booking",
        "description": "Look up the upcoming appointment, if any, booked from this phone number.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "cancel_booking",
        "description": "Cancel the upcoming appointment booked from this phone number. Use when the person asks to cancel, or before rebooking when they ask to reschedule.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "handoff_to_staff",
        "description": (
            "Flag this conversation for a clinic staff member to call the person back. Use "
            "when the person asks for a human, when you have failed to understand them twice "
            "in a row, or when the request is outside finding and booking appointments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "One short sentence for staff on why a call back is needed."}},
            "required": ["reason"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


# ---------- helpers ----------

def _miles(lat1, lon1, lat2, lon2) -> float:
    """Straight-line distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def _when(start_at: str) -> str:
    dt = datetime.fromisoformat(start_at)
    hour = dt.hour % 12 or 12
    return f"{dt.strftime('%a %b')} {dt.day}, {hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def resolve_place(location: str) -> dict | None:
    """Match a ZIP code or town name against the local places table."""
    text = location.strip().lower()
    zip_match = re.search(r"\b(\d{5})\b", text)
    for place in PLACES:
        if zip_match and place["zip"] == zip_match.group(1):
            return place
    for place in PLACES:
        if place["town"].lower() in text:
            return place
    return None


def _open_slots(clinic_id: int, limit: int) -> list[dict]:
    rows = db.query(
        "SELECT id, start_at FROM slots WHERE clinic_id = ? AND status = 'open' AND start_at > ? "
        "ORDER BY start_at LIMIT ?",
        (clinic_id, db.now_iso(), limit),
    )
    return [{"slot_id": r["id"], "time": _when(r["start_at"])} for r in rows]


def _active_booking(phone: str):
    rows = db.query(
        "SELECT b.id, b.slot_id, s.start_at, c.name, c.address, c.phone AS clinic_phone "
        "FROM bookings b JOIN slots s ON s.id = b.slot_id JOIN clinics c ON c.id = b.clinic_id "
        "WHERE b.phone = ? AND b.status = 'booked' AND s.start_at > ? ORDER BY s.start_at LIMIT 1",
        (phone, db.now_iso()),
    )
    return rows[0] if rows else None


# ---------- tools ----------

def find_clinics(phone: str, location: str) -> dict:
    place = resolve_place(location)
    if not place:
        return {"error": "unknown_location", "message": "That place was not recognised. Ask for a 5-digit ZIP code or a town name in Southeast Ohio."}
    clinics = []
    for c in db.query("SELECT * FROM clinics"):
        slots = _open_slots(c["id"], 2)
        if slots:
            clinics.append({
                "clinic_id": c["id"],
                "name": c["name"],
                "town": c["town"],
                "distance_mi": round(_miles(place["lat"], place["lon"], c["lat"], c["lon"])),
                "open_slots": slots,
            })
    clinics.sort(key=lambda c: c["distance_mi"])
    clinics = clinics[:3]
    db.set_last_offered(phone, [s["slot_id"] for c in clinics for s in c["open_slots"]])
    return {"place": f"{place['town']}, OH {place['zip']}", "clinics": clinics}


def get_open_slots(phone: str, clinic_id: int) -> dict:
    rows = db.query("SELECT name FROM clinics WHERE id = ?", (clinic_id,))
    if not rows:
        return {"error": "unknown_clinic"}
    slots = _open_slots(clinic_id, 6)
    db.set_last_offered(phone, [s["slot_id"] for s in slots])
    return {"clinic": rows[0]["name"], "open_slots": slots}


def book_slot(phone: str, slot_id: int) -> dict:
    slot = db.query("SELECT * FROM slots WHERE id = ?", (slot_id,))
    if not slot:
        return {"error": "unknown_slot", "message": "No such slot. Offer times from find_clinics or get_open_slots."}
    slot = slot[0]
    # Atomic claim: only succeeds if the slot is still open.
    claimed = db.execute("UPDATE slots SET status = 'booked' WHERE id = ? AND status = 'open'", (slot_id,))
    if claimed.rowcount == 0:
        return {
            "error": "slot_taken",
            "message": "That time was just taken by someone else. Apologise briefly and offer these alternatives.",
            "alternatives": _open_slots(slot["clinic_id"], 3),
        }
    cur = db.execute(
        "INSERT INTO bookings (phone, slot_id, clinic_id, created_at) VALUES (?, ?, ?, ?)",
        (phone, slot_id, slot["clinic_id"], db.now_iso()),
    )
    clinic = db.query("SELECT * FROM clinics WHERE id = ?", (slot["clinic_id"],))[0]
    return {
        "booked": True,
        "booking_id": cur.lastrowid,
        "clinic": clinic["name"],
        "address": clinic["address"],
        "clinic_phone": clinic["phone"],
        "time": _when(slot["start_at"]),
        "note": "A reminder text will be sent the day before.",
    }


def get_my_booking(phone: str) -> dict:
    b = _active_booking(phone)
    if not b:
        return {"booking": None}
    return {"booking": {"clinic": b["name"], "address": b["address"], "clinic_phone": b["clinic_phone"], "time": _when(b["start_at"])}}


def cancel_booking(phone: str) -> dict:
    b = _active_booking(phone)
    if not b:
        return {"error": "no_booking", "message": "There is no upcoming appointment for this number."}
    db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (b["id"],))
    db.execute("UPDATE slots SET status = 'open' WHERE id = ?", (b["slot_id"],))
    return {"cancelled": True, "clinic": b["name"], "time": _when(b["start_at"])}


def handoff_to_staff(phone: str, reason: str) -> dict:
    db.execute("INSERT INTO handoffs (phone, reason, created_at) VALUES (?, ?, ?)", (phone, reason, db.now_iso()))
    return {"flagged": True, "message": "Staff have been asked to call this number back during clinic hours. Tell the person that."}


HANDLERS = {
    "find_clinics": find_clinics,
    "get_open_slots": get_open_slots,
    "book_slot": book_slot,
    "get_my_booking": get_my_booking,
    "cancel_booking": cancel_booking,
    "handoff_to_staff": handoff_to_staff,
}


def run_tool(phone: str, name: str, tool_input: dict) -> dict:
    handler = HANDLERS.get(name)
    if not handler:
        return {"error": "unknown_tool"}
    try:
        return handler(phone, **tool_input)
    except TypeError as exc:  # wrong or missing arguments from the model
        return {"error": "bad_arguments", "message": str(exc)}


def reminder_text(phone: str) -> str | None:
    """The reminder SMS for this number's upcoming appointment, or None."""
    b = _active_booking(phone)
    if not b:
        return None
    return f"Reminder: your visit at {b['name']} is {_when(b['start_at'])}, {b['address']}. Reply CANCEL to cancel."
