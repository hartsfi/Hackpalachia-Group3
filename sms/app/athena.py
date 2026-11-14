"""Minimal athenahealth (athenaOne) client for scheduling.

Endpoints and parameters follow athenahealth's own sample code and the
eleanorhealth/go-athenahealth client:
  POST /oauth2/v1/token                      client_credentials, scope athena/service/Athenanet.MDP.*
  GET  /v1/{practice}/appointments/open      departmentid, providerid, appointmenttypeid|reasonid, startdate, enddate
  PUT  /v1/{practice}/appointments/{id}      patientid, appointmenttypeid, departmentid  (books an open slot)
  PUT  /v1/{practice}/appointments/{id}/cancel      patientid, cancellationreason
  GET  /v1/{practice}/patients/enhancedbestmatch    firstname, lastname, dob (+ mobilephone, zip)
  POST /v1/{practice}/patients               firstname, lastname, dob, mobilephone, zip, departmentid
  GET  /v1/{practice}/departments | /providers | /appointmenttypes   (used by tools/athena_setup.py)
Dates are MM/DD/YYYY, open-slot times HH:MM in the practice's local time.
"""
import datetime as dt
import logging
import threading
import time

import requests

from . import config

log = logging.getLogger("athena")
HOSTS = {"preview": "https://api.preview.platform.athenahealth.com",
         "production": "https://api.platform.athenahealth.com"}
SCOPE = "athena/service/Athenanet.MDP.*"


class AthenaError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"athenahealth {status}: {text[:300]}")
        self.status, self.text = status, text


class Athena:
    def __init__(self):
        self._token, self._expires, self._lock = None, 0.0, threading.Lock()

    @property
    def host(self) -> str:
        return config.ATHENA_BASE_URL or HOSTS.get(config.ATHENA_ENV, HOSTS["preview"])

    def _get_token(self, force: bool = False) -> str:
        with self._lock:
            if self._token and not force and time.time() < self._expires:
                return self._token
            r = requests.post(f"{self.host}/oauth2/v1/token",
                              data={"grant_type": "client_credentials", "scope": SCOPE},
                              auth=(config.ATHENA_CLIENT_ID, config.ATHENA_CLIENT_SECRET), timeout=15)
            if r.status_code >= 400:
                raise AthenaError(r.status_code, r.text)
            j = r.json()
            self._token = j["access_token"]
            self._expires = time.time() + int(j.get("expires_in", 3600)) - 60
            return self._token

    def call(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        url = f"{self.host}/v1/{config.ATHENA_PRACTICE_ID}{path}"
        for attempt in (1, 2):
            r = requests.request(method, url, params=params, data=data, timeout=20,
                                 headers={"Authorization": f"Bearer {self._get_token(force=attempt == 2)}"})
            if r.status_code == 401 and attempt == 1:
                continue  # token expired early: get a fresh one and retry once
            if r.status_code >= 400:
                raise AthenaError(r.status_code, r.text)
            return r.json() if r.content else {}

    # ------------------------------------------------------------ scheduling
    def open_slots(self, a: dict, start: dt.date, end: dt.date, limit: int = 50) -> list[dict]:
        params = {"departmentid": a["departmentid"], "startdate": start.strftime("%m/%d/%Y"),
                  "enddate": end.strftime("%m/%d/%Y"), "ignoreschedulablepermission": "true",
                  "limit": limit}
        if a.get("providerid"):
            params["providerid"] = a["providerid"]
        if a.get("appointmenttypeid"):
            params["appointmenttypeid"] = a["appointmenttypeid"]
        else:
            params["reasonid"] = a.get("reasonid", "-1")
        return self.call("GET", "/appointments/open", params=params).get("appointments", [])

    def book(self, appointment_id: str, patient_id: str, a: dict, appointmenttypeid: str | None) -> bool:
        """True when booked, False when the slot is no longer open."""
        data = {"patientid": patient_id, "departmentid": a["departmentid"],
                "ignoreschedulablepermission": "true"}
        if appointmenttypeid or a.get("appointmenttypeid"):
            data["appointmenttypeid"] = appointmenttypeid or a["appointmenttypeid"]
        try:
            self.call("PUT", f"/appointments/{appointment_id}", data=data)
            return True
        except AthenaError as e:
            if e.status in (400, 404, 409):  # athena answers a taken/closed slot with a 4xx
                log.info("slot %s not bookable: %s", appointment_id, e)
                return False
            raise

    def cancel(self, appointment_id: str, patient_id: str) -> None:
        self.call("PUT", f"/appointments/{appointment_id}/cancel",
                  data={"patientid": patient_id, "cancellationreason": "Cancelled by patient by text"})

    # ------------------------------------------------------------ patients
    def find_or_create_patient(self, first: str, last: str, dob: str, phone: str, zip_code: str,
                               departmentid: str) -> str:
        mobile = phone[-10:]
        try:
            found = self.call("GET", "/patients/enhancedbestmatch", params={
                "firstname": first, "lastname": last, "dob": dob, "mobilephone": mobile, "zip": zip_code})
            if isinstance(found, list) and found and found[0].get("patientid"):
                return str(found[0]["patientid"])
        except AthenaError as e:
            log.info("no patient match (%s), creating one", e.status)
        made = self.call("POST", "/patients", data={
            "firstname": first, "lastname": last, "dob": dob, "mobilephone": mobile,
            "zip": zip_code, "departmentid": departmentid})
        row = made[0] if isinstance(made, list) else made
        return str(row["patientid"])


client = Athena()
