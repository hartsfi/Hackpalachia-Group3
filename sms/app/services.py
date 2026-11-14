"""Places, clinics and their open times (app/data/*.json, athenahealth, the bookings table).

Which clinics exist comes from app/clinics.py; which ones to offer a patient is decided by
app/finder.py. This module turns places and times into text and reads each clinic's calendar:
athenahealth for clinics with an "athena" block, the mock calendar for the rest.
"""
import datetime as dt
import json
import logging
import math
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import clinics as directory
from . import config, db

log = logging.getLogger("services")

DATA = Path(__file__).parent / "data"
TZ = ZoneInfo("America/New_York")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LOOKAHEAD_DAYS = 14


def _load(name: str, key: str) -> list[dict]:
    return json.loads((DATA / name).read_text())[key]


def places() -> list[dict]:
    return _load("places.json", "places")


def clinics() -> dict[str, dict]:
    """Every clinic we know, by id (see app/clinics.py for where they come from)."""
    return directory.every()


def clinic_tz(service_id: str) -> ZoneInfo:
    """The clinic's own timezone: visit times are shown the way its front desk says them."""
    return _zone(clinics().get(service_id))


def _zone(svc: dict | None) -> ZoneInfo:
    try:
        return ZoneInfo((svc or {}).get("tz") or config.CLINIC_TIMEZONE)
    except Exception:
        return TZ


def practice_of(service_id: str) -> str | None:
    """The athenahealth practice a clinic belongs to, or None for a mock-calendar clinic.
    Patient and appointment ids are only valid inside their own practice."""
    a = clinics().get(service_id, {}).get("athena")
    return str(a.get("practiceid") or config.ATHENA_PRACTICE_ID) if a and config.ATHENA_ENABLED else None


COORDS = re.compile(r"(-?\d{1,2}\.\d{2,})\s*,\s*(-?\d{1,3}\.\d{2,})")
MAX_GPS_MILES = 60


def find_place(text: str) -> dict | None:
    """A typed ZIP code or town name -> place, or None if not recognized.

    Also takes GPS coordinates in the text, e.g. a shared Apple Maps link
    (maps.apple.com/?ll=39.45,-82.23) or "39.45, -82.23" typed out: the nearest
    known town is used for the reply, the exact point for distances.
    """
    pl = places()
    m = COORDS.search(text)
    if m:
        here = {"lat": float(m.group(1)), "lon": float(m.group(2))}
        near = min(pl, key=lambda p: miles(here, p))
        if miles(here, near) <= MAX_GPS_MILES:
            return {**near, **here}
    for z in re.findall(r"\b(\d{5})\b", text):
        for p in pl:
            if p["zip"] == z:
                return p
    for p in sorted(pl, key=lambda p: -len(p["town"])):
        if p.get("zip_only"):  # e.g. Ray, Shade, Friendship: too easily a normal word in a text
            continue
        if re.search(r"\b" + re.escape(p["town"]) + r"\b", text, re.I):
            return p
    return None


def miles(a: dict, b: dict) -> float:
    r = 3958.8
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    dla, dlo = la2 - la1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def when(ts: float, weekday: bool = True, tz: ZoneInfo | str | None = None) -> str:
    """'Tue 11/17 1:30PM', in the clinic's timezone when given (default Eastern)."""
    d = dt.datetime.fromtimestamp(ts, ZoneInfo(tz) if isinstance(tz, str) else tz or TZ)
    t = d.strftime("%I:%M%p").lstrip("0").replace(":00", "")
    return (f"{DAYS[d.weekday()]} " if weekday else "") + f"{d.month}/{d.day} {t}"


def free_slots(svc: dict, n: int, after: float | None = None, need: str | None = None,
               timeout: float | None = None, strict: bool = False) -> list[dict]:
    """Next n open slots for a clinic, from at least an hour from now.

    Each slot is {"ts": start time, "ext": athenahealth appointmentid or None, "atype": type id}.
    Clinics with an "athena" block read their schedule from athenahealth (when keys are set),
    asking for the appointment type mapped to the patient's need; the rest use the mock calendar
    built from "days" and "times". `timeout` bounds the athenahealth call; with `strict` a failed
    call raises instead of counting as "no open times".
    """
    earliest = max(after or 0, db.now() + 3600)
    if svc.get("athena") and config.ATHENA_ENABLED:
        taken_ext = db.taken_external(svc["id"])
        slots = _athena_slots(svc, need, timeout, strict)
        return [s for s in slots if s["ts"] > earliest and s["ext"] not in taken_ext][:n]
    if not svc.get("days"):  # an athenahealth clinic while athenahealth is switched off
        return []
    taken = db.taken_slots(svc["id"])
    tz = _zone(svc)
    today = dt.datetime.now(tz).date()
    out = []
    for d in range(LOOKAHEAD_DAYS):
        day = today + dt.timedelta(days=d)
        if DAYS[day.weekday()] not in svc["days"]:
            continue
        for t in svc["times"]:
            hh, mm = map(int, t.split(":"))
            ts = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).timestamp()
            if ts > earliest and ts not in taken:
                out.append({"ts": ts, "ext": None, "atype": None})
                if len(out) >= n:
                    return out
    return out


_athena_cache: dict[tuple, tuple[float, list[dict]]] = {}  # (clinic id, visit type) -> (when, slots)
ATHENA_CACHE_SECONDS = 30  # Preview allows only a few requests a second


def forget_athena_slots(service_id: str | None = None) -> None:
    """Drop cached athenahealth times for one clinic, or all of them (after an athena error)."""
    if service_id is None:
        _athena_cache.clear()
    else:
        for key in [k for k in _athena_cache if k[0] == service_id]:
            _athena_cache.pop(key, None)


def visit_type(svc: dict, need: str | None = None) -> dict:
    """What to ask athenahealth for at this clinic: the appointment type (or reason) mapped to
    the patient's need in clinic_overrides.json, else the clinic's default type."""
    a = svc["athena"]
    chosen = svc.get("types", {}).get(need) or {k: a[k] for k in ("appointmenttypeid", "reasonid", "providerid")
                                                 if a.get(k)}
    return {"practiceid": a.get("practiceid") or config.ATHENA_PRACTICE_ID, "departmentid": a["departmentid"],
            **chosen}


def _athena_slots(svc: dict, need: str | None = None, timeout: float | None = None,
                  strict: bool = False) -> list[dict]:
    ask = visit_type(svc, need)
    key = (svc["id"], ask.get("appointmenttypeid") or "r" + str(ask.get("reasonid", "")), ask.get("providerid", ""))
    hit = _athena_cache.get(key)
    if hit and time.time() - hit[0] < ATHENA_CACHE_SECONDS:
        return hit[1]
    from .athena import client  # imported here so the mock calendar never needs it

    tz = _zone(svc)  # athenahealth gives slot times in the department's local time
    today = dt.datetime.now(tz).date()
    try:
        raw = client.open_slots(ask, today, today + dt.timedelta(days=LOOKAHEAD_DAYS), timeout=timeout,
                                deadline=time.monotonic() + timeout if timeout else None)
    except Exception as e:  # athena down or misconfigured: skip this clinic, never crash a text
        log.warning("athenahealth open slots for %s failed: %s", svc["id"], e)
        if strict:
            raise
        return []
    slots = []
    for a in raw:
        try:
            m, d, y = map(int, a["date"].split("/"))
            hh, mm = map(int, a["starttime"].split(":")[:2])
        except (KeyError, ValueError):
            continue
        slots.append({"ts": dt.datetime(y, m, d, hh, mm, tzinfo=tz).timestamp(),
                      "ext": str(a["appointmentid"]), "atype": str(a.get("appointmenttypeid") or "") or None})
    slots.sort(key=lambda x: x["ts"])
    _athena_cache[key] = (time.time(), slots)
    return slots


def uses_athena(service_id: str) -> bool:
    return bool(config.ATHENA_ENABLED and clinics().get(service_id, {}).get("athena"))


def _option(svc: dict, slot: dict, place: dict | None) -> dict:
    return {"service_id": svc["id"], "short": svc["short"], "ts": slot["ts"], "ext": slot["ext"],
            "atype": slot["atype"], "mi": round(miles(place, svc)) if place else None}


def options(income: str, place: dict, limit: int = 3, need: str | None = None) -> list[dict]:
    """Up to `limit` clinics for this patient, each with its next open time (see app/finder.py)."""
    from . import finder  # imported here: finder builds on this module

    return finder.recommend(place, income, need, limit)


def next_times(service_id: str, place: dict | None, limit: int = 3, need: str | None = None) -> list[dict]:
    """Next open times at one clinic (used when a picked slot was just taken)."""
    svc = clinics()[service_id]
    return [_option(svc, slot, place) for slot in free_slots(svc, limit, need=need)]


def option_lines(opts: list[dict], header: str, budget: int = 160) -> str:
    """Numbered options in one text, dropping detail until it fits the budget."""
    known = clinics()
    tz = {o["service_id"]: _zone(known.get(o["service_id"])) for o in opts}

    def at(o, weekday=True):
        return when(o["ts"], weekday, tz[o["service_id"]])

    if len(tz) == 1:  # all at one clinic: the header names it
        return "\n".join([header] + [f"{i}) {at(o)}" for i, o in enumerate(opts, 1)])
    formats = [
        lambda i, o: f"{i}) {o['short']} {o['mi']}mi {at(o)}",
        lambda i, o: f"{i}) {o['short']} {o['mi']}mi {at(o, False)}",
        lambda i, o: f"{i}) {o['short'][:14].strip()} {o['mi']}mi {at(o, False)}",
    ]
    msg = ""
    for f in formats:
        msg = "\n".join([header] + [f(i, o) for i, o in enumerate(opts, 1)])
        if len(msg) <= budget:
            break
    return msg
