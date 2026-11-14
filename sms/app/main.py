import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import config, db, relay, services
from . import sms as sms_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

log = logging.getLogger("main")
db.init()


async def _reminder_loop():
    while True:
        try:
            sent = await run_in_threadpool(relay.send_due_reminders)
            if sent:
                log.info("sent %d visit reminders", sent)
        except Exception:
            log.exception("reminder check failed")
        await asyncio.sleep(60)


async def _mac_inbox_loop():
    """SMS_PROVIDER=mac: pick up new texts from the Messages database every 2 seconds.
    Starts at the newest message, so old conversations are never replayed."""
    from . import mac_messages

    last, warned = None, False
    while True:
        try:
            if last is None:
                last = await run_in_threadpool(mac_messages.latest_rowid)
                log.info("reading incoming texts from Messages (after message %d)", last)
            for rowid, sender, text in await run_in_threadpool(mac_messages.new_messages, last):
                last = rowid
                phone = sms_mod.normalize(sender)
                if text and mac_messages.allowed(phone):
                    await run_in_threadpool(relay.handle_incoming, phone, text)
        except sqlite3.OperationalError as e:
            if not warned:
                log.error("cannot read %s (%s): give the app running this server Full Disk Access "
                          "(System Settings > Privacy & Security > Full Disk Access), then restart it",
                          config.MAC_MESSAGES_DB, e)
                warned = True
        except Exception:
            log.exception("Messages inbox check failed")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(_reminder_loop())]
    if config.SMS_ENABLED and config.SMS_PROVIDER == "mac":
        tasks.append(asyncio.create_task(_mac_inbox_loop()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="CareLine", lifespan=lifespan)

_basic = HTTPBasic(auto_error=False)


def staff(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """Dashboard login. Off when DASHBOARD_PASSWORD is blank (local dev)."""
    if not config.DASHBOARD_PASSWORD:
        return
    if creds is None or not secrets.compare_digest(creds.password, config.DASHBOARD_PASSWORD):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


def dev_only() -> None:
    if not config.DEV_SIMULATOR:
        raise HTTPException(404)


# ---------------------------------------------------------------- Twilio webhook
@app.post("/sms/inbound")
async def sms_inbound(request: Request):
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if config.TWILIO_ENABLED:
        from twilio.request_validator import RequestValidator

        url = (config.PUBLIC_BASE_URL + "/sms/inbound") if config.PUBLIC_BASE_URL else str(request.url)
        sig = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(config.TWILIO_AUTH_TOKEN).validate(url, params, sig):
            raise HTTPException(403, "bad Twilio signature")
    await run_in_threadpool(relay.handle_incoming, params.get("From", ""), params.get("Body", ""))
    # Replies go out through the REST API (so every text is logged), so the TwiML stays empty.
    return Response(EMPTY_TWIML, media_type="application/xml")


# ---------------------------------------------------------------- Android gateway webhook
@app.post("/sms/android")
async def sms_android(request: Request):
    """Incoming text from SMS Gateway for Android (event sms:received)."""
    raw = await request.body()
    if config.ANDROID_SIGNING_KEY:
        ts = request.headers.get("X-Timestamp", "")
        sig = request.headers.get("X-Signature", "")
        want = hmac.new(config.ANDROID_SIGNING_KEY.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
        fresh = ts.isdigit() and abs(time.time() - int(ts)) <= 300
        if not (fresh and hmac.compare_digest(want.lower(), sig.lower())):
            raise HTTPException(403, "bad gateway signature")
    data = json.loads(raw or b"{}")
    if data.get("event") == "sms:received":
        p = data.get("payload") or {}
        sender = sms_mod.normalize(p.get("sender") or p.get("phoneNumber") or "")
        if sender:
            await run_in_threadpool(relay.handle_incoming, sender, p.get("message", ""))
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "sms": config.SMS_PROVIDER if config.SMS_ENABLED else "dev",
            "twilio": config.TWILIO_ENABLED, "android": config.ANDROID_ENABLED,
            "llm": config.LLM_PROVIDER, "simulator": config.DEV_SIMULATOR}


# ---------------------------------------------------------------- dashboard API
class Reply(BaseModel):
    body: str


@app.get("/api/cases", dependencies=[Depends(staff)])
def api_cases(status: str | None = None):
    rows = db.list_cases(status)
    for r in rows:
        r["patient_code"] = db.patient_code(r["patient_id"]) if r.get("patient_id") else None
    return rows


@app.get("/api/cases/{case_id}", dependencies=[Depends(staff)])
def api_case(case_id: int):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(404)
    bookings = []
    for b in db.case_bookings(case_id):
        svc = services.clinics().get(b["service_id"], {})
        bookings.append({**b, "clinic": svc.get("short", b["service_id"]), "when": services.when(b["start_ts"])})
    pt = db.get_patient(case["patient_phone"])
    patient = {"code": db.patient_code(pt["id"]), "first_name": pt["first_name"], "last_name": pt["last_name"],
               "since": pt["created_at"]} if pt else None
    rides = [{**r, "when": services.when(r["visit_ts"]) if r["visit_ts"] else ""} for r in db.case_rides(case_id)]
    return {"case": dict(case), "messages": db.case_messages(case_id), "bookings": bookings, "patient": patient,
            "rides": rides}


@app.post("/api/cases/{case_id}/reply", dependencies=[Depends(staff)])
def api_reply(case_id: int, reply: Reply):
    if not relay.reply_from_dashboard(case_id, reply.body):
        raise HTTPException(400, "case not open or empty message")
    return {"ok": True}


@app.post("/api/rides/{ride_id}/booked", dependencies=[Depends(staff)])
def api_ride_booked(ride_id: int):
    """The health worker booked this ride (e.g. in Uber Health's dashboard)."""
    db.set_ride_status(ride_id, "booked")
    return {"ok": True}


@app.post("/api/cases/{case_id}/close", dependencies=[Depends(staff)])
def api_close(case_id: int):
    if db.get_case(case_id) is None:
        raise HTTPException(404)
    db.update_case(case_id, status="closed")
    return {"ok": True}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


@app.get("/dashboard", include_in_schema=False, dependencies=[Depends(staff)])
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


# ---------------------------------------------------------------- dev phone simulator
class DevText(BaseModel):
    from_phone: str
    body: str


@app.get("/simulator", include_in_schema=False, dependencies=[Depends(dev_only)])
def simulator():
    return FileResponse(STATIC / "simulator.html")


@app.get("/dev/config", dependencies=[Depends(dev_only)])
def dev_config():
    return {"clinic": config.CLINIC_NUMBER, "chws": config.CHW_NUMBERS}


@app.post("/dev/text", dependencies=[Depends(dev_only)])
def dev_text(t: DevText):
    """Pretend a phone texted the clinic number (what Twilio's webhook would do)."""
    relay.handle_incoming(t.from_phone.strip(), t.body)
    return {"ok": True}


# ---------------------------------------------------------------- pure agent experiment
@app.get("/simulator-agent", include_in_schema=False, dependencies=[Depends(dev_only)])
def simulator_agent():
    """Same phone, but every text is answered by the pure agent (app/pure_agent.py)."""
    return FileResponse(STATIC / "simulator-agent.html")


@app.get("/dev/recent-chats", dependencies=[Depends(dev_only)])
def dev_recent_chats(limit: int = 25):
    """Patient conversations, newest first, so the simulator can reopen one."""
    import sqlite3
    with db.connect() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT p.phone, MAX(m.id) AS last_id, MAX(m.created_at) AS ts FROM messages m "
            "JOIN (SELECT from_phone AS phone FROM messages WHERE sender='patient' "
            "      UNION SELECT to_phone FROM messages WHERE sender!='patient') p "
            "  ON m.from_phone = p.phone OR m.to_phone = p.phone "
            "WHERE m.channel != 'ai' GROUP BY p.phone ORDER BY ts DESC LIMIT ?", (limit * 2,)).fetchall()
        out = []
        for r in rows:
            if not r["phone"] or r["phone"] in config.CHW_NUMBERS or r["phone"] == config.CLINIC_NUMBER:
                continue
            last = c.execute("SELECT body, sender FROM messages WHERE id=?", (r["last_id"],)).fetchone()
            pat = db.get_patient(r["phone"])
            name = " ".join(x for x in ((pat["first_name"], pat["last_name"]) if pat else ())
                            if x and "unknown" not in x.lower() and "<" not in x)
            out.append({"phone": r["phone"], "name": name, "ts": r["ts"],
                        "last": ("You: " if last["sender"] == "patient" else "") + (last["body"] or "")[:80]})
            if len(out) >= limit:
                break
    return out


@app.post("/dev/agent-text", dependencies=[Depends(dev_only)])
def dev_agent_text(t: DevText):
    from . import pure_agent
    pure_agent.handle(t.from_phone.strip(), t.body)
    return {"ok": True}


# Fictional 555-01xx numbers (never real phones) across Ohio area codes, for simulator users.
SIM_AREA_CODES = ["740", "614", "330", "216", "513", "937", "419", "220", "380", "567", "234", "440"]


@app.get("/dev/new-phone", dependencies=[Depends(dev_only)])
def dev_new_phone():
    """A test number nobody has used yet, so the simulator starts as a brand-new user."""
    import random

    taken = {config.CLINIC_NUMBER, *config.CHW_NUMBERS}
    for _ in range(500):
        phone = f"+1{random.choice(SIM_AREA_CODES)}5550{random.randint(101, 198)}"
        if phone not in taken and not db.get_patient(phone) and not db.phone_messages(phone):
            return {"phone": phone}
    raise HTTPException(503, "no unused test numbers left; delete textbridge.db to start over")


@app.post("/dev/demo-patient", dependencies=[Depends(dev_only)])
def dev_demo_patient():
    """An existing patient to test returning users: account, saved town, one booked visit."""
    phone = dev_new_phone()["phone"]
    db.create_patient(phone, "en")
    db.update_patient(phone, first_name="Sam", last_name="Miller", zip="45764", town="Nelsonville", income="low")
    case_id = db.create_case(phone, {"category": "checkup", "urgency": "low", "summary": "Checkup (demo patient)",
                                     "language": "en"})
    db.update_case(case_id, zip="45764", town="Nelsonville", income="low", stage="booked", status="closed")
    # The first clinic that still has an open time (a busy demo can fill one clinic's calendar).
    svc, slot = next((s, f[0]) for s in services.clinics().values()
                     if not s.get("kind") and not s.get("athena") and (f := services.free_slots(s, 1)))
    db.book(case_id, phone, svc["id"], slot["ts"])
    return {"phone": phone, "name": "Sam Miller", "visit": f"{services.when(slot['ts'])} {svc['short']}"}


@app.get("/dev/phone/{phone}", dependencies=[Depends(dev_only)])
def dev_phone(phone: str, since: int = 0):
    return db.phone_messages(sms_mod.normalize(phone), since)


@app.post("/dev/reminders", dependencies=[Depends(dev_only)])
def dev_reminders(hours_ahead: float = 0):
    """Run the reminder check as if the clock were `hours_ahead` hours later."""
    return {"sent": relay.send_due_reminders(db.now() + hours_ahead * 3600)}
