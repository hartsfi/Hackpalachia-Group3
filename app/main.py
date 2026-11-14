"""HTTP surface for the agent and data layer.

The SMS transport lives elsewhere. It calls POST /api/message with the sender's
number and text, and sends whatever comes back in "reply" (null means send nothing).
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent, db, tools

app = FastAPI(title="Rural care text line: agent and data layer")
db.init()

STATIC = Path(__file__).parent / "static"


class Inbound(BaseModel):
    phone: str
    text: str


class Phone(BaseModel):
    phone: str


@app.post("/api/message")
def message(body: Inbound):
    """One inbound text in, one reply out. This is the contract for the SMS layer."""
    return {"reply": agent.handle_message(body.phone, body.text)}


@app.get("/api/state")
def state(phone: str):
    """Audit trail and current records for one phone number."""
    bookings = db.query(
        "SELECT b.id, b.status, s.start_at, c.name FROM bookings b "
        "JOIN slots s ON s.id = b.slot_id JOIN clinics c ON c.id = b.clinic_id "
        "WHERE b.phone = ? ORDER BY b.id DESC",
        (phone,),
    )
    handoffs = db.query("SELECT reason, created_at FROM handoffs WHERE phone = ? ORDER BY id DESC", (phone,))
    return {
        "events": db.get_events(phone),
        "bookings": [dict(b) for b in bookings],
        "handoffs": [dict(h) for h in handoffs],
    }


@app.post("/api/reminder")
def reminder(body: Phone):
    """The reminder text for this number's upcoming visit. A scheduler would call this the day before."""
    text = tools.reminder_text(body.phone)
    if text and not db.get_conversation(body.phone)["opted_out"]:
        db.log_event(body.phone, "outbound", {"text": text, "via": "reminder", "chars": len(text)})
        return {"reply": text}
    return {"reply": None}


# ---------- demo helpers (test console only) ----------

@app.post("/api/demo/take-slot")
def take_slot(body: Phone):
    """Simulate another patient booking the first time that was just offered."""
    for slot_id in db.get_conversation(body.phone)["last_offered"]:
        if db.execute("UPDATE slots SET status = 'booked' WHERE id = ? AND status = 'open'", (slot_id,)).rowcount:
            db.log_event(body.phone, "demo", {"note": f"Another patient took slot {slot_id}"})
            return {"taken": slot_id}
    return {"taken": None}


@app.post("/api/demo/reset")
def reset():
    """Wipe all demo data and reseed clinics and slots."""
    db.init(reset=True)
    return {"ok": True}


@app.get("/")
def console():
    return FileResponse(STATIC / "index.html")
