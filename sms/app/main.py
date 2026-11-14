import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import config, db, relay, services

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


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(title="TextBridge", lifespan=lifespan)

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


@app.get("/health")
def health():
    return {"ok": True, "twilio": config.TWILIO_ENABLED, "claude": bool(config.ANTHROPIC_API_KEY),
            "simulator": config.DEV_SIMULATOR}


# ---------------------------------------------------------------- dashboard API
class Reply(BaseModel):
    body: str


@app.get("/api/cases", dependencies=[Depends(staff)])
def api_cases(status: str | None = None):
    return db.list_cases(status)


@app.get("/api/cases/{case_id}", dependencies=[Depends(staff)])
def api_case(case_id: int):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(404)
    bookings = []
    for b in db.case_bookings(case_id):
        svc = services.clinics().get(b["service_id"], {})
        bookings.append({**b, "clinic": svc.get("short", b["service_id"]), "when": services.when(b["start_ts"])})
    return {"case": dict(case), "messages": db.case_messages(case_id), "bookings": bookings}


@app.post("/api/cases/{case_id}/reply", dependencies=[Depends(staff)])
def api_reply(case_id: int, reply: Reply):
    if not relay.reply_from_dashboard(case_id, reply.body):
        raise HTTPException(400, "case not open or empty message")
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


@app.get("/dev/phone/{phone}", dependencies=[Depends(dev_only)])
def dev_phone(phone: str, since: int = 0):
    return db.phone_messages(phone, since)


@app.post("/dev/reminders", dependencies=[Depends(dev_only)])
def dev_reminders(hours_ahead: float = 0):
    """Run the reminder check as if the clock were `hours_ahead` hours later."""
    return {"sent": relay.send_due_reminders(db.now() + hours_ahead * 3600)}
