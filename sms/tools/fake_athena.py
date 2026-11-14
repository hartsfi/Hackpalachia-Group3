"""A stand-in for athenahealth's API, for building and testing without keys.

    cd sms && uv run uvicorn tools.fake_athena:app --port 8796
    # then in .env: ATHENA_CLIENT_ID=x  ATHENA_CLIENT_SECRET=x  ATHENA_BASE_URL=http://127.0.0.1:8796
    #               ATHENA_PRACTICE_IDS=195900,195901

It answers in athenahealth's shapes: list calls page with totalcount / next, dates are
MM/DD/YYYY, times are HH:MM in the department's own timezone, errors are
{"error": ..., "detailedmessage": ...}, and ids only mean something inside their practice.

Two fictional clinic organizations in Southeast Ohio (every name, address and 555-01xx
phone is made up):
  195900  Hocking Valley Community Health   1 Athens, 2 Nelsonville, 3 Logan,
          4 Glouster (no latitude/longitude), 5 McArthur (inactive), 6 billing office (not a
          service department)
  195901  Ohio River Family Health          1 Pomeroy, 2 Gallipolis, 3 Marietta
Both practices have a department 1 and hand out the same appointment ids, as two real
practices can: a caller that forgets the practice books the wrong visit and fails a check.
Patient ids start at 1001 in the first practice and 5001 in the second, and booking with a
patient id from the other practice is refused.

One field is a guess: athenahealth's public material shows no "active" flag on a department
(inactive ones are left out unless showalldepartments is set). Department 5 is returned with
"active": false so the code that skips inactive departments is exercised.

Test controls (not part of athenahealth):
  POST /_take/{appointmentid}?practice=195900   the front desk books a slot
  POST /_down, POST /_up                        every call answers 503 / works again
  POST /_throttle?n=3&retry_after=1             the next n API calls answer 429 with Retry-After
  POST /_slow?departmentid=3&seconds=8&practice=195900   open-slot calls for one department wait
  POST /_fail?departmentid=3&practice=195900&on=1        open-slot calls for one department answer 500
  POST /_reset                                  forget bookings, patients and every control
  GET  /_state                                  bookings, cancellations, patients, call counts
"""
import datetime as dt
import time
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import JSONResponse

app = FastAPI()
PAGE = {"departments": 3, "providers": 3, "appointmenttypes": 3, "appointments": 25}  # largest page per list
WEEKDAYS, MWF, TTH = (0, 1, 2, 3, 4), (0, 2, 4), (1, 3)


def _dept(i, name, address, city, zip_code, phone, lat=None, lon=None, **extra):
    d = {"departmentid": str(i), "name": name, "patientdepartmentname": name, "address": address,
         "address2": "", "city": city.upper(), "state": "OH", "zip": zip_code, "phone": phone,
         "fax": "", "timezonename": "America/New_York", "timezoneoffset": -5, "timezone": -5,
         "doesnotobservedst": False, "servicedepartment": True, "ishospitaldepartment": False,
         "placeofservicetypeid": "11", "placeofservicetypename": "OFFICE", "placeofservicefacility": False,
         "clinicals": "ON", "medicationhistoryconsent": False, "providergroupid": "1",
         "chartsharinggroupid": "1", "communicatorbrandid": "1"}
    if lat is not None:
        d.update(latitude=str(lat), longitude=str(lon))
    d.update(extra)
    return d


TYPES = [  # the same appointment types in both practices
    {"appointmenttypeid": "82", "name": "Office Visit", "shortname": "OV", "duration": "30", "patient": "true", "generic": "false"},
    {"appointmenttypeid": "101", "name": "Behavioral Health Visit", "shortname": "BH", "duration": "60", "patient": "true", "generic": "false"},
    {"appointmenttypeid": "62", "name": "Dental Exam", "shortname": "DEN", "duration": "45", "patient": "true", "generic": "false"},
    {"appointmenttypeid": "71", "name": "Well Child Visit", "shortname": "WCV", "duration": "30", "patient": "true", "generic": "false"},
]
TYPE_NAME = {t["appointmenttypeid"]: t["name"] for t in TYPES}
REASONS = {"1082": "82", "1101": "101", "1062": "62", "1071": "71"}  # patient appointment reason -> type

PRACTICES = {
    "195900": {
        "name": "Hocking Valley Community Health",
        "departments": [
            _dept(1, "Athens Health Center", "40 Elm St", "Athens", "45701", "(740) 555-0112", 39.331, -82.098,
                  providergroupname="Hocking Valley Community Health"),
            _dept(2, "Nelsonville Family Care", "15 Canal St", "Nelsonville", "45764", "(740) 555-0121", 39.4585, -82.2318),
            _dept(3, "Logan Community Clinic", "21 Market St", "Logan", "43138", "(740) 555-0122", 39.5401, -82.4071),
            _dept(4, "Glouster Health Center", "9 High St", "Glouster", "45732", "(740) 555-0123"),  # no coordinates
            _dept(5, "McArthur Clinic (closed)", "8 Main St", "McArthur", "45651", "(740) 555-0124", 39.2465, -82.4785,
                  active=False),
            _dept(6, "Central Billing Office", "40 Elm St Suite B", "Athens", "45701", "(740) 555-0125", 39.331, -82.098,
                  servicedepartment=False, patientdepartmentname="", placeofservicetypename="OTHER"),
        ],
        "providers": [
            {"providerid": 71, "firstname": "Ada", "lastname": "Fake", "displayname": "Dr. Ada Fake", "specialty": "Family Medicine"},
            {"providerid": 72, "firstname": "Ben", "lastname": "Sample", "displayname": "Ben Sample, LISW", "specialty": "Behavioral Health"},
            {"providerid": 73, "firstname": "Cy", "lastname": "Placeholder", "displayname": "Dr. Cy Placeholder", "specialty": "Family Medicine"},
            {"providerid": 74, "firstname": "Di", "lastname": "Example", "displayname": "Dr. Di Example", "specialty": "Dentistry"},
            {"providerid": 75, "firstname": "Eli", "lastname": "Mock", "displayname": "Eli Mock, CNP", "specialty": "Family Medicine"},
        ],
        # departmentid -> (providerid, appointmenttypeid, weekdays, start times)
        "schedule": {
            "1": [(71, "82", WEEKDAYS, ("09:00", "11:00", "15:00")), (72, "101", TTH, ("13:00",))],
            "2": [(73, "82", WEEKDAYS, ("08:40", "13:20"))],
            "3": [(75, "82", MWF, ("10:00", "14:30")), (74, "62", TTH, ("09:30",))],
            "4": [(73, "82", TTH, ("09:40", "14:00")), (72, "101", (2,), ("10:30",))],
            "5": [(71, "82", WEEKDAYS, ("09:00",))],
            "6": [],
        },
        "first_patient": 1000,
    },
    "195901": {
        "name": "Ohio River Family Health",
        "departments": [
            _dept(1, "Pomeroy Family Practice", "110 Court St", "Pomeroy", "45769", "(740) 555-0131", 39.0276, -82.0338),
            _dept(2, "Gallipolis Clinic", "52 State St", "Gallipolis", "45631", "(740) 555-0132", 38.8098, -82.2024),
            _dept(3, "Marietta Health Center", "300 Front St", "Marietta", "45750", "(740) 555-0133", 39.4154, -81.4548),
        ],
        "providers": [
            {"providerid": 71, "firstname": "Flo", "lastname": "Standin", "displayname": "Dr. Flo Standin", "specialty": "Family Medicine"},
            {"providerid": 72, "firstname": "Gus", "lastname": "Dummy", "displayname": "Dr. Gus Dummy", "specialty": "Pediatrics"},
            {"providerid": 73, "firstname": "Hal", "lastname": "Testcase", "displayname": "Hal Testcase, LPCC", "specialty": "Behavioral Health"},
            {"providerid": 74, "firstname": "Ivy", "lastname": "Fixture", "displayname": "Dr. Ivy Fixture", "specialty": "Family Medicine"},
        ],
        "schedule": {
            "1": [(71, "82", WEEKDAYS, ("09:00", "11:00", "15:00")), (72, "71", MWF, ("10:20",))],
            "2": [(74, "82", TTH, ("08:30", "13:00")), (73, "101", (0, 3), ("15:30",))],
            "3": [(74, "82", MWF, ("09:15", "14:15"))],
        },
        "first_patient": 5000,
    },
}


def _fresh() -> dict:
    return {"down": False, "throttle": 0, "retry_after": 1, "slow": {}, "fail": set(), "open_calls": {},
            "p": {pid: {"booked": {}, "cancelled": [], "patients": {}, "next_pid": p["first_patient"]}
                  for pid, p in PRACTICES.items()}}


S = _fresh()


class Fail(Exception):
    def __init__(self, status: int, error: str, detail: str = "", headers: dict | None = None):
        self.status, self.error, self.detail, self.headers = status, error, detail, headers


@app.exception_handler(Fail)
async def _fail(_request: Request, e: Fail):
    body = {"error": e.error}
    if e.detail:
        body["detailedmessage"] = e.detail
    return JSONResponse(body, status_code=e.status, headers=e.headers)


def _guard(practice: str, auth: str | None) -> dict:
    """Checks every API call goes through; returns the practice's state."""
    if S["down"]:
        raise Fail(503, "Service Unavailable")
    if S["throttle"] > 0:
        S["throttle"] -= 1
        raise Fail(429, "Too Many Requests", "Rate limit exceeded for this application.",
                   {"Retry-After": str(S["retry_after"])})
    if auth != "Bearer fake-token":
        raise Fail(401, "Not Authorized", "The access token is missing, invalid or expired.")
    if practice not in PRACTICES:
        raise Fail(404, "Invalid practice.", f"Practice {practice} is not available to this application.")
    return S["p"][practice]


def _page(practice: str, path: str, key: str, rows: list, limit: int | None, offset: int, query: str = "") -> dict:
    size = max(1, min(limit or PAGE[key], PAGE[key]))
    out = {"totalcount": len(rows), key: rows[offset:offset + size]}
    more = f"&{query}" if query else ""
    if offset + size < len(rows):
        out["next"] = f"/v1/{practice}{path}?offset={offset + size}&limit={size}{more}"
    if offset > 0:
        out["previous"] = f"/v1/{practice}{path}?offset={max(0, offset - size)}&limit={size}{more}"
    return out


def _department(practice: str, departmentid: str) -> dict | None:
    return next((d for d in PRACTICES[practice]["departments"] if d["departmentid"] == departmentid), None)


def _slots(practice: str, departmentid: str):
    """Every slot in the next two weeks for one department, booked or not, in time order."""
    dep = _department(practice, departmentid)
    if dep is None:
        return
    today = dt.datetime.now(ZoneInfo(dep["timezonename"])).date()
    for d in range(15):
        day = today + dt.timedelta(days=d)
        rows = []
        for n, (provider, atype, weekdays, times) in enumerate(PRACTICES[practice]["schedule"][departmentid]):
            if day.weekday() not in weekdays:
                continue
            for k, t in enumerate(times):
                rows.append({"appointmentid": f"{departmentid}{day.toordinal() % 10000:04d}{n}{k}",
                             "date": f"{day:%m/%d/%Y}", "starttime": t, "departmentid": departmentid,
                             "providerid": provider, "localproviderid": provider, "appointmenttypeid": atype,
                             "appointmenttype": TYPE_NAME[atype], "patientappointmenttypename": TYPE_NAME[atype],
                             "duration": int(next(x["duration"] for x in TYPES if x["appointmenttypeid"] == atype)),
                             "frozen": "false"})
        yield from sorted(rows, key=lambda a: a["starttime"])


def _find_slot(practice: str, appt: str) -> dict | None:
    for dep in PRACTICES[practice]["departments"]:
        for a in _slots(practice, dep["departmentid"]):
            if a["appointmentid"] == appt:
                return a
    return None


def _date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%m/%d/%Y").date()
    except ValueError:
        raise Fail(400, "Invalid date.", "Dates must be MM/DD/YYYY.")


# ---------------------------------------------------------------- athenahealth API
@app.post("/oauth2/v1/token")
async def token(request: Request, grant_type: str = Form(...), scope: str = Form("")):
    if S["down"]:
        raise Fail(503, "Service Unavailable")
    if grant_type != "client_credentials" or not request.headers.get("authorization", "").startswith("Basic "):
        raise Fail(401, "invalid_client", "Client authentication failed.")
    return {"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer", "scope": scope}


@app.get("/v1/{practice}/departments")
def departments(practice: str, limit: int | None = None, offset: int = 0, authorization: str | None = Header(None)):
    _guard(practice, authorization)
    return _page(practice, "/departments", "departments", PRACTICES[practice]["departments"], limit, offset)


@app.get("/v1/{practice}/providers")
def providers(practice: str, limit: int | None = None, offset: int = 0, authorization: str | None = Header(None)):
    _guard(practice, authorization)
    return _page(practice, "/providers", "providers", PRACTICES[practice]["providers"], limit, offset)


@app.get("/v1/{practice}/appointmenttypes")
def appointmenttypes(practice: str, limit: int | None = None, offset: int = 0,
                     authorization: str | None = Header(None)):
    _guard(practice, authorization)
    return _page(practice, "/appointmenttypes", "appointmenttypes", TYPES, limit, offset)


@app.get("/v1/{practice}/patientappointmentreasons")
def reasons(practice: str, departmentid: str, providerid: str | None = None,
            authorization: str | None = Header(None)):
    _guard(practice, authorization)
    have = {atype for prov, atype, _, _ in PRACTICES[practice]["schedule"].get(departmentid, [])
            if providerid in (None, str(prov))}
    rows = [{"reasonid": int(r), "reason": TYPE_NAME[t], "reasontype": "all", "description": TYPE_NAME[t]}
            for r, t in REASONS.items() if t in have]
    return {"totalcount": len(rows), "patientappointmentreasons": rows}


@app.get("/v1/{practice}/appointments/open")
def open_slots(request: Request, practice: str, departmentid: str, providerid: str | None = None,
               appointmenttypeid: str | None = None, reasonid: str | None = None, startdate: str | None = None,
               enddate: str | None = None, limit: int | None = None, offset: int = 0,
               authorization: str | None = Header(None)):
    state = _guard(practice, authorization)
    key = f"{practice}:{departmentid}"
    S["open_calls"][key] = S["open_calls"].get(key, 0) + 1
    if S["slow"].get(key):
        time.sleep(S["slow"][key])
    if key in S["fail"]:
        raise Fail(500, "Internal Server Error", "The scheduling service could not answer.")
    if _department(practice, departmentid) is None:
        raise Fail(404, "Invalid departmentid.", f"Department {departmentid} does not exist in practice {practice}.")
    if not appointmenttypeid and not reasonid:
        raise Fail(400, "Additional fields are required.", "Either appointmenttypeid or reasonid is required.")
    if appointmenttypeid:
        want = {appointmenttypeid}
    else:
        want = None if reasonid == "-1" else {REASONS.get(r) for r in reasonid.split(",")}
    start, end = _date(startdate), _date(enddate)
    rows = []
    for a in _slots(practice, departmentid):
        day = dt.datetime.strptime(a["date"], "%m/%d/%Y").date()
        if (a["appointmentid"] in state["booked"] or (want is not None and a["appointmenttypeid"] not in want)
                or (providerid and str(a["providerid"]) != providerid)
                or (start and day < start) or (end and day > end)):
            continue
        rows.append(a)
    keep = "&".join(f"{k}={v}" for k, v in request.query_params.items() if k not in ("limit", "offset"))
    return _page(practice, "/appointments/open", "appointments", rows, limit, offset, keep)


@app.put("/v1/{practice}/appointments/{appt}")
def book(practice: str, appt: str, patientid: str = Form(...), departmentid: str = Form(None),
         appointmenttypeid: str = Form(None), reasonid: str = Form(None), authorization: str | None = Header(None)):
    state = _guard(practice, authorization)
    slot = _find_slot(practice, appt)
    if slot is None:
        raise Fail(404, "The appointment ID is invalid.", "No such appointment in this practice.")
    if patientid not in state["patients"].values():
        raise Fail(400, "Invalid patientid.", "That patient does not exist in this practice.")
    if appt in state["booked"]:
        raise Fail(400, "The appointment is not available.", "That appointment time was already booked.")
    state["booked"][appt] = patientid
    return [{**slot, "patientid": patientid, "appointmentstatus": "f"}]


@app.put("/v1/{practice}/appointments/{appt}/reschedule")
def reschedule(practice: str, appt: str, newappointmentid: str = Form(...), patientid: str = Form(...),
               reschedulereason: str = Form(""), reasonid: str = Form(None),
               authorization: str | None = Header(None)):
    state = _guard(practice, authorization)
    if state["booked"].get(appt) != patientid:
        raise Fail(400, "The appointment is not booked for this patient.",
                   "Only a booked appointment can be rescheduled.")
    slot = _find_slot(practice, newappointmentid)
    if slot is None:
        raise Fail(404, "The appointment ID is invalid.", "No such new appointment in this practice.")
    if newappointmentid in state["booked"]:
        raise Fail(400, "The appointment is not available.", "That appointment time was already booked.")
    del state["booked"][appt]
    state["cancelled"].append(appt)
    state["booked"][newappointmentid] = patientid
    return [{**slot, "patientid": patientid, "appointmentstatus": "f"}]


@app.put("/v1/{practice}/appointments/{appt}/cancel")
def cancel(practice: str, appt: str, patientid: str = Form(...), cancellationreason: str = Form(""),
           authorization: str | None = Header(None)):
    state = _guard(practice, authorization)
    if state["booked"].get(appt) != patientid:
        raise Fail(400, "The appointment is not booked for this patient.")
    del state["booked"][appt]
    state["cancelled"].append(appt)
    return {"status": "x"}


@app.get("/v1/{practice}/patients/enhancedbestmatch")
def bestmatch(practice: str, firstname: str, lastname: str, dob: str, authorization: str | None = Header(None)):
    state = _guard(practice, authorization)
    pid = state["patients"].get((firstname.lower(), lastname.lower(), dob))
    return [{"patientid": pid, "score": 30}] if pid else []


@app.post("/v1/{practice}/patients")
def create_patient(practice: str, firstname: str = Form(...), lastname: str = Form(...), dob: str = Form(...),
                   departmentid: str = Form(...), mobilephone: str = Form(""), zip: str = Form(""),
                   authorization: str | None = Header(None)):
    state = _guard(practice, authorization)
    if _department(practice, departmentid) is None:
        raise Fail(400, "Invalid departmentid.")
    state["next_pid"] += 1
    state["patients"][(firstname.lower(), lastname.lower(), dob)] = str(state["next_pid"])
    return [{"patientid": str(state["next_pid"])}]


# ---------------------------------------------------------------- test controls
def _practice(practice: str | None) -> str:
    return practice if practice in PRACTICES else next(iter(PRACTICES))


@app.post("/_take/{appt}")
def take(appt: str, practice: str | None = None):
    S["p"][_practice(practice)]["booked"][appt] = "front-desk"
    return {"ok": True}


@app.post("/_down")
def down():
    S["down"] = True
    return {"ok": True}


@app.post("/_up")
def up():
    S["down"] = False
    return {"ok": True}


@app.post("/_throttle")
def throttle(n: int = 1, retry_after: float = 1):
    S["throttle"], S["retry_after"] = n, retry_after
    return {"ok": True}


@app.post("/_slow")
def slow(departmentid: str, seconds: float = 0, practice: str | None = None):
    S["slow"][f"{_practice(practice)}:{departmentid}"] = seconds
    return {"ok": True}


@app.post("/_fail")
def fail(departmentid: str, practice: str | None = None, on: int = 1):
    key = f"{_practice(practice)}:{departmentid}"
    S["fail"].add(key) if on else S["fail"].discard(key)
    return {"ok": True}


@app.post("/_reset")
def reset():
    S.clear()
    S.update(_fresh())
    return {"ok": True}


@app.get("/_state")
def state():
    """Top level = every practice together (what the older checks read); "practices" = each one."""
    each = {pid: {"booked": s["booked"], "cancelled": s["cancelled"], "patients": len(s["patients"])}
            for pid, s in S["p"].items()}
    return {"booked": {k: v for s in each.values() for k, v in s["booked"].items()},
            "cancelled": [a for s in each.values() for a in s["cancelled"]],
            "patients": sum(s["patients"] for s in each.values()), "practices": each,
            "open_calls": S["open_calls"], "throttle": S["throttle"], "down": S["down"]}
