"""athenahealth (athenaOne) client for the clinic directory and scheduling.

Endpoints and parameters follow athenahealth's own sample code and the
eleanorhealth/go-athenahealth client:
  POST /oauth2/v1/token                      client_credentials, scope athena/service/Athenanet.MDP.*
  GET  /v1/{practice}/departments            (clinic directory, app/clinics.py)
  GET  /v1/{practice}/appointments/open      departmentid, providerid, appointmenttypeid|reasonid, startdate, enddate
  PUT  /v1/{practice}/appointments/{id}      patientid, appointmenttypeid|reasonid, departmentid  (books an open slot)
  PUT  /v1/{practice}/appointments/{id}/reschedule  newappointmentid, patientid, reschedulereason
  PUT  /v1/{practice}/appointments/{id}/cancel      patientid, cancellationreason
  GET  /v1/{practice}/patients/enhancedbestmatch    firstname, lastname, dob (+ mobilephone, zip)
  POST /v1/{practice}/patients               firstname, lastname, dob, mobilephone, zip, departmentid
  GET  /v1/{practice}/providers | /appointmenttypes   (used by tools/athena_setup.py)
Dates are MM/DD/YYYY, open-slot times HH:MM in the department's local time.

Every clinic organization is its own practice, so each call names one (default: the first of
ATHENA_PRACTICE_IDS). Appointment and patient ids only mean something inside their practice.

Retries: a 429 is retried for every call (athenahealth did not process it), waiting for
Retry-After when given, otherwise ATHENA_BACKOFF doubled each try. 5xx answers and network
errors are retried for GET only; a write (book, cancel, reschedule, create patient) is retried
only on 503, because repeating a write that may have gone through could book a visit twice.

Logging: ids, paths and athenahealth's own error text only. Request parameters are never
logged, and for /patients calls not even the detailed error text, so no names, dates of birth
or phone numbers reach the logs.
"""
import datetime as dt
import logging
import threading
import time
from urllib.parse import parse_qsl, urlsplit

import requests

from . import config

log = logging.getLogger("athena")
HOSTS = {"preview": "https://api.preview.platform.athenahealth.com",
         "production": "https://api.platform.athenahealth.com"}
SCOPE = "athena/service/Athenanet.MDP.*"
MAX_RETRY_AFTER = 10.0  # never sleep longer than this on one try, whatever Retry-After says
NOT_BOOKABLE = (400, 404, 409)  # athena answers a taken or closed slot with one of these


class AthenaError(Exception):
    """A failed call. `status` is the HTTP status (0 = no answer); `text` is athenahealth's own
    error message (its `error` / `detailedmessage` fields), never the raw response body."""

    def __init__(self, status: int, text: str, where: str = ""):
        super().__init__(f"athenahealth {status or 'no answer'}{' on ' + where if where else ''}: {text[:300]}")
        self.status, self.text, self.where = status, text, where


def _reason(r: requests.Response, patient_call: bool) -> str:
    """athenahealth's error body ({"error": ..., "detailedmessage": ...}) as one short line."""
    try:
        j = r.json()
    except ValueError:
        return r.reason or "no error body"
    if not isinstance(j, dict):
        return r.reason or "no error body"
    keys = ("error",) if patient_call else ("error", "detailedmessage")  # see "Logging" above
    parts = [str(j[k]) for k in keys if j.get(k)]
    if j.get("missingfields"):
        parts.append(f"missing fields: {j['missingfields']}")
    return ("; ".join(parts) or r.reason or "no error body")[:300]


def _retry_after(r: requests.Response) -> float | None:
    try:
        return max(0.0, float(r.headers.get("Retry-After", "")))
    except ValueError:
        return None


class Athena:
    def __init__(self):
        self._token, self._expires, self._lock = None, 0.0, threading.Lock()
        self.read_only = False  # tools/check_athena_live.py sets this: anything but a GET is refused

    @property
    def host(self) -> str:
        return config.ATHENA_BASE_URL or HOSTS.get(config.ATHENA_ENV, HOSTS["preview"])

    # ------------------------------------------------------------ transport
    def _send(self, method: str, url: str, where: str, *, headers=None, auth=None, params=None, data=None,
              timeout: float | None = None, deadline: float | None = None, safe: bool = False):
        """One request with retries (see the module docstring). `deadline` is a time.monotonic()
        value after which no further try or wait is started. `safe` = may be repeated freely."""
        retries, wait = max(0, config.ATHENA_RETRIES), config.ATHENA_BACKOFF
        patient_call = "/patients" in where
        for attempt in range(retries + 1):
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise AthenaError(0, "gave up: out of time", where)
            limit = min(timeout or config.ATHENA_TIMEOUT, left) if left is not None else (timeout or config.ATHENA_TIMEOUT)
            hdrs = headers() if callable(headers) else headers
            try:
                r = requests.request(method, url, params=params, data=data, headers=hdrs, auth=auth, timeout=limit)
            except requests.RequestException as e:
                # Only the kind of failure: the exception text holds the URL with its parameters.
                err, retry, pause = AthenaError(0, type(e).__name__, where), safe, None
            else:
                if r.status_code < 400 or r.status_code == 401:
                    return r  # on a 401 the caller gets a fresh token
                err = AthenaError(r.status_code, _reason(r, patient_call), where)
                retry = r.status_code in (429, 503) or (safe and r.status_code >= 500)
                pause = _retry_after(r)
            if not retry or attempt == retries:
                raise err
            pause = min(wait if pause is None else pause, MAX_RETRY_AFTER)
            if deadline is not None and time.monotonic() + pause >= deadline:
                raise err
            log.warning("%s; trying again in %.1fs (%d of %d)", err, pause, attempt + 1, retries)
            time.sleep(pause)
            wait *= 2

    def _get_token(self, force: bool = False, deadline: float | None = None) -> str:
        with self._lock:
            if self._token and not force and time.time() < self._expires:
                return self._token
            r = self._send("POST", f"{self.host}/oauth2/v1/token", "POST /oauth2/v1/token",
                           data={"grant_type": "client_credentials", "scope": SCOPE},
                           auth=(config.ATHENA_CLIENT_ID, config.ATHENA_CLIENT_SECRET),
                           timeout=15, deadline=deadline, safe=True)
            if r.status_code >= 400:
                raise AthenaError(r.status_code, "client id or secret refused", "POST /oauth2/v1/token")
            j = r.json()
            self._token = j["access_token"]
            self._expires = time.time() + int(j.get("expires_in", 3600)) - 60
            return self._token

    def call(self, method: str, path: str, params: dict | None = None, data: dict | None = None, *,
             practice: str | None = None, timeout: float | None = None, deadline: float | None = None):
        practice = str(practice or config.ATHENA_PRACTICE_ID)
        where = f"{method} /v1/{practice}{path}"
        if self.read_only and method != "GET":
            raise AthenaError(0, "refused: this client is read-only", where)
        url = f"{self.host}/v1/{practice}{path}"
        for attempt in (1, 2):
            token = self._get_token(force=attempt == 2, deadline=deadline)
            r = self._send(method, url, where, headers={"Authorization": f"Bearer {token}"}, params=params,
                           data=data, timeout=timeout, deadline=deadline, safe=method == "GET")
            if r.status_code == 401 and attempt == 1:
                continue  # token expired early: get a fresh one and retry once
            if r.status_code >= 400:
                raise AthenaError(r.status_code, _reason(r, "/patients" in path), where)
            return r.json() if r.content else {}

    def list(self, path: str, key: str, params: dict | None = None, *, practice: str | None = None,
             max_rows: int = 5000, timeout: float | None = None, deadline: float | None = None) -> list[dict]:
        """Every row of a list call, following athenahealth's paging: each page is
        {"totalcount": n, key: [...], "next": "/v1/{practice}{path}?offset=..&limit=.."}."""
        params, rows = dict(params or {}), []
        while True:
            page = self.call("GET", path, params=params, practice=practice, timeout=timeout, deadline=deadline)
            got = page.get(key) or [] if isinstance(page, dict) else []
            rows.extend(got)
            nxt = page.get("next") if isinstance(page, dict) else None
            if not nxt or not got or len(rows) >= max_rows:
                return rows[:max_rows]
            step = dict(parse_qsl(urlsplit(nxt).query))
            if str(step.get("offset", "")) == str(params.get("offset", "")):
                return rows  # no progress: never loop on a broken "next"
            params.update(step)

    # ------------------------------------------------------------ scheduling
    def open_slots(self, a: dict, start: dt.date, end: dt.date, limit: int = 300, *,
                   timeout: float | None = None, deadline: float | None = None) -> list[dict]:
        """Open slots for one clinic's "athena" block: {"practiceid", "departmentid"} plus optional
        "providerid" and "appointmenttypeid" or "reasonid" (neither = every reason)."""
        params = {"departmentid": a["departmentid"], "startdate": start.strftime("%m/%d/%Y"),
                  "enddate": end.strftime("%m/%d/%Y"), "ignoreschedulablepermission": "true"}
        if a.get("providerid"):
            params["providerid"] = a["providerid"]
        if a.get("appointmenttypeid"):
            params["appointmenttypeid"] = a["appointmenttypeid"]
        else:
            params["reasonid"] = a.get("reasonid", "-1")
        return self.list("/appointments/open", "appointments", params, practice=a.get("practiceid"),
                         max_rows=limit, timeout=timeout, deadline=deadline)

    def _visit_type(self, a: dict, appointmenttypeid: str | None) -> dict:
        if appointmenttypeid or a.get("appointmenttypeid"):
            return {"appointmenttypeid": appointmenttypeid or a["appointmenttypeid"]}
        return {"reasonid": a["reasonid"]} if a.get("reasonid") else {}

    def book(self, appointment_id: str, patient_id: str, a: dict, appointmenttypeid: str | None) -> bool:
        """True when booked, False when the slot is no longer open."""
        data = {"patientid": patient_id, "departmentid": a["departmentid"],
                "ignoreschedulablepermission": "true", **self._visit_type(a, appointmenttypeid)}
        try:
            self.call("PUT", f"/appointments/{appointment_id}", data=data, practice=a.get("practiceid"))
            return True
        except AthenaError as e:
            if e.status in NOT_BOOKABLE:
                log.info("slot %s not bookable: %s", appointment_id, e)
                return False
            raise

    def reschedule(self, appointment_id: str, new_appointment_id: str, patient_id: str, a: dict) -> str | None:
        """Move a booked visit to another open slot in the same practice, in one call.

        Returns the appointment id the patient now holds, or None when the new slot is no longer
        open (the old visit is then untouched)."""
        data = {"newappointmentid": new_appointment_id, "patientid": patient_id,
                "reschedulereason": "Rescheduled by patient by text", "ignoreschedulablepermission": "true"}
        if a.get("reasonid") and not a.get("appointmenttypeid"):
            data["reasonid"] = a["reasonid"]
        try:
            made = self.call("PUT", f"/appointments/{appointment_id}/reschedule", data=data,
                             practice=a.get("practiceid"))
        except AthenaError as e:
            if e.status in NOT_BOOKABLE:
                log.info("visit %s not moved to slot %s: %s", appointment_id, new_appointment_id, e)
                return None
            raise
        row = (made[0] if isinstance(made, list) and made else made) or {}
        # The answer names the replacement visit as "appointmentid" or "rescheduledappointmentid".
        for k in ("rescheduledappointmentid", "appointmentid"):
            if row.get(k) and str(row[k]) != str(appointment_id):
                return str(row[k])
        return str(new_appointment_id)

    def cancel(self, appointment_id: str, patient_id: str, practice: str | None = None) -> None:
        self.call("PUT", f"/appointments/{appointment_id}/cancel", practice=practice,
                  data={"patientid": patient_id, "cancellationreason": "Cancelled by patient by text"})

    # ------------------------------------------------------------ patients
    def find_or_create_patient(self, first: str, last: str, dob: str, phone: str, zip_code: str,
                               departmentid: str, practice: str | None = None) -> str:
        """The patient id in one practice. Ids are per practice: never reuse one elsewhere."""
        mobile = phone[-10:]
        try:
            found = self.call("GET", "/patients/enhancedbestmatch", practice=practice, params={
                "firstname": first, "lastname": last, "dob": dob, "mobilephone": mobile, "zip": zip_code})
            if isinstance(found, list) and found and found[0].get("patientid"):
                return str(found[0]["patientid"])
        except AthenaError as e:
            log.info("no patient match (%s), creating one", e.status)
        made = self.call("POST", "/patients", practice=practice, data={
            "firstname": first, "lastname": last, "dob": dob, "mobilephone": mobile,
            "zip": zip_code, "departmentid": departmentid})
        row = made[0] if isinstance(made, list) else made
        return str(row["patientid"])


client = Athena()
