"""A tiny stand-in for athenahealth's Preview API, for testing without keys.

    cd sms && uv run uvicorn tools.fake_athena:app --port 8796
    # then in .env: ATHENA_CLIENT_ID=x  ATHENA_CLIENT_SECRET=x  ATHENA_BASE_URL=http://127.0.0.1:8796

Department 1 has open slots on weekdays at 9:00, 11:00 and 15:00 for the next two weeks.
Test hooks: POST /_take/{id} (front desk books a slot), POST /_down and /_up, GET /_state.
"""
import datetime as dt

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()
S = {"booked": {}, "cancelled": [], "patients": {}, "down": False, "next_pid": 1000}


def _guard(auth: str | None):
    if S["down"]:
        raise HTTPException(503, "Service Unavailable")
    if auth != "Bearer fake-token":
        raise HTTPException(401, "invalid token")


def _slots():
    today = dt.date.today()
    for d in range(14):
        day = today + dt.timedelta(days=d)
        if day.weekday() < 5:
            for t in ("09:00", "11:00", "15:00"):
                yield {"appointmentid": f"{day:%m%d}{t[:2]}", "date": f"{day:%m/%d/%Y}", "starttime": t,
                       "departmentid": "1", "providerid": "71", "appointmenttypeid": "82",
                       "appointmenttype": "Office Visit", "duration": 30}


@app.post("/oauth2/v1/token")
async def token(request: Request, grant_type: str = Form(...), scope: str = Form("")):
    if S["down"]:
        raise HTTPException(503)
    if grant_type != "client_credentials" or not request.headers.get("authorization", "").startswith("Basic "):
        raise HTTPException(401, "bad client")
    return {"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer", "scope": scope}


@app.get("/v1/{practice}/appointments/open")
def open_slots(practice: str, departmentid: str, authorization: str | None = Header(None)):
    _guard(authorization)
    rows = [a for a in _slots() if a["departmentid"] == departmentid and a["appointmentid"] not in S["booked"]]
    return {"appointments": rows, "totalcount": len(rows)}


@app.put("/v1/{practice}/appointments/{appt}")
def book(practice: str, appt: str, patientid: str = Form(...), departmentid: str = Form(None),
         appointmenttypeid: str = Form(None), authorization: str | None = Header(None)):
    _guard(authorization)
    if appt not in {a["appointmentid"] for a in _slots()}:
        raise HTTPException(404, "Appointment not found")
    if appt in S["booked"]:
        return JSONResponse({"error": "The appointment is not available."}, status_code=400)
    S["booked"][appt] = patientid
    return [{"appointmentid": appt, "patientid": patientid, "appointmentstatus": "f"}]


@app.put("/v1/{practice}/appointments/{appt}/cancel")
def cancel(practice: str, appt: str, patientid: str = Form(...), cancellationreason: str = Form(""),
           authorization: str | None = Header(None)):
    _guard(authorization)
    if S["booked"].get(appt) != patientid:
        return JSONResponse({"error": "Appointment is not booked for this patient."}, status_code=400)
    del S["booked"][appt]
    S["cancelled"].append(appt)
    return {"status": "x"}


@app.get("/v1/{practice}/patients/enhancedbestmatch")
def bestmatch(practice: str, firstname: str, lastname: str, dob: str, authorization: str | None = Header(None)):
    _guard(authorization)
    pid = S["patients"].get((firstname.lower(), lastname.lower(), dob))
    return [{"patientid": pid, "score": 30}] if pid else []


@app.post("/v1/{practice}/patients")
def create_patient(practice: str, firstname: str = Form(...), lastname: str = Form(...), dob: str = Form(...),
                   departmentid: str = Form(...), mobilephone: str = Form(""), zip: str = Form(""),
                   authorization: str | None = Header(None)):
    _guard(authorization)
    S["next_pid"] += 1
    S["patients"][(firstname.lower(), lastname.lower(), dob)] = str(S["next_pid"])
    return [{"patientid": str(S["next_pid"])}]


@app.get("/v1/{practice}/departments")
def departments(practice: str, authorization: str | None = Header(None)):
    _guard(authorization)
    return {"departments": [{"departmentid": "1", "name": "Fake Family Medicine", "city": "ATHENS", "state": "OH", "zip": "45701"}]}


@app.get("/v1/{practice}/providers")
def providers(practice: str, authorization: str | None = Header(None)):
    _guard(authorization)
    return {"providers": [{"providerid": 71, "displayname": "Dr. Fake Provider"}]}


@app.get("/v1/{practice}/appointmenttypes")
def appointmenttypes(practice: str, authorization: str | None = Header(None)):
    _guard(authorization)
    return {"appointmenttypes": [{"appointmenttypeid": "82", "name": "Office Visit", "duration": "30"}]}


@app.post("/_take/{appt}")
def take(appt: str):
    S["booked"][appt] = "front-desk"
    return {"ok": True}


@app.post("/_down")
def down():
    S["down"] = True
    return {"ok": True}


@app.post("/_up")
def up():
    S["down"] = False
    return {"ok": True}


@app.get("/_state")
def state():
    return {"booked": S["booked"], "cancelled": S["cancelled"], "patients": len(S["patients"])}
