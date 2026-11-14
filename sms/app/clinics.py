"""The clinic directory: which clinics exist, where they are, and what each one offers.

CLINIC_SOURCE=sample  the hand-written clinics in app/data/services.json (demo data).
CLINIC_SOURCE=athena  every department of every practice in ATHENA_PRACTICE_IDS, read from
                      athenahealth (GET /v1/{practice}/departments, all pages), with
                      app/data/clinic_overrides.json on top for what athenahealth does not know.

A clinic id is "{practiceid}:{departmentid}": each clinic organization is its own practice and
department ids repeat across practices.

The athenahealth directory is kept in memory. After CLINIC_DIRECTORY_TTL seconds the next
lookup starts a reload in the background and is answered from the copy in hand. A practice
whose reload fails keeps its last good clinics.

Clinics are handed to the rest of the app as plain dicts in the shape of a services.json row:
  id, name, short, for (low | standard | all), address, town, phone, lat, lon, tz,
  tags (needs the clinic is known for), hidden, bookable,
  athena {practiceid, departmentid, + the default appointmenttypeid | reasonid},
  types {need: {appointmenttypeid | reasonid, providerid?}}   (athena clinics only)
"""
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

log = logging.getLogger("clinics")
DATA = Path(__file__).parent / "data"
RETRY_SECONDS = 30  # after a failed first load, wait this long before asking athenahealth again
INCOMES = ("low", "standard", "all")
OFFSET_ZONES = {-5: "America/New_York", -6: "America/Chicago", -7: "America/Denver", -8: "America/Los_Angeles"}
SHORTER = (("Community", "Comm"), ("Health Center", "Health Ctr"), ("Center", "Ctr"), ("Family", "Fam"),
           ("Medicine", "Med"), ("Practice", "Prac"))


@dataclass(frozen=True)
class Clinic:
    """One athenahealth department, as mapped by map_department()."""
    id: str              # "{practiceid}:{departmentid}"
    practiceid: str
    departmentid: str
    name: str
    address: str
    town: str
    state: str
    zip: str
    phone: str
    lat: float | None    # None = no coordinates and no known ZIP: the clinic cannot be offered
    lon: float | None
    tz: str
    bookable: bool       # active, and a service / patient department
    missing: tuple[str, ...] = ()  # expected fields athenahealth did not send
    notes: tuple[str, ...] = ()    # what was filled in or decided, for tools/check_athena_live.py


@lru_cache(maxsize=1)
def _zips() -> dict[str, dict]:
    return {p["zip"]: p for p in json.loads((DATA / "places.json").read_text())["places"]}


def _flag(raw: dict, *names: str) -> bool | None:
    """A yes/no field that may arrive as true, "true", 1 or "Y". None when absent."""
    for n in names:
        if raw.get(n) not in (None, ""):
            v = raw[n]
            return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "y", "yes", "on")
    return None


def _number(raw: dict, name: str) -> float | None:
    try:
        return float(raw[name])
    except (KeyError, TypeError, ValueError):
        return None


def map_department(raw: dict, practiceid: str = "") -> Clinic:
    """One row of GET /v1/{practice}/departments -> Clinic. All athenahealth field mapping is here.

    Fields we expect (every one may be missing; what happens then is in brackets):
      departmentid           the id                                  [required: ValueError]
      patientdepartmentname  the name patients know, else `name`     [name: "Clinic"]
      address, city, state   street address and town                 [blank; town from the ZIP]
      zip                    5-digit ZIP (ZIP+4 is cut)              [blank]
      phone                  any format, sent to patients as 740-555-0112   [blank]
      latitude, longitude    decimal degrees                         [centre of `zip` in
                             app/data/places.json; ZIP unknown too -> lat/lon None, and the
                             directory skips the clinic and logs it. No outside geocoding.]
      timezonename           IANA name, e.g. America/New_York        [timezoneoffset / timezone
                             (-5 .. -8 hours) -> a US zone, else CLINIC_TIMEZONE]
      servicedepartment      false = no patients are seen there      [absent: taken as true]
      patientdepartment      same meaning, if the practice sends it  [absent: taken as true]
      active / isactive / inactive / deleted / status
                             a closed department. athenahealth's public material shows no such
                             field (closed departments are simply left out of the list), so
                             these names are guesses that cost nothing when absent.  [active]
    `missing` lists the expected fields that were absent, `notes` what was done about it.
    """
    if raw.get("departmentid") in (None, ""):
        raise ValueError("department without a departmentid")
    dep = str(raw["departmentid"])
    missing = [f for f in ("name", "address", "city", "state", "zip", "phone", "latitude", "longitude",
                           "timezonename", "servicedepartment") if raw.get(f) in (None, "")]
    notes = []

    zip_code = re.sub(r"\D", "", str(raw.get("zip") or ""))[:5]
    place = _zips().get(zip_code)
    lat, lon = _number(raw, "latitude"), _number(raw, "longitude")
    if lat is None or lon is None or (lat == 0 and lon == 0):
        lat, lon = (place["lat"], place["lon"]) if place else (None, None)
        notes.append(f"coordinates: centre of ZIP {zip_code}" if place else "no coordinates and ZIP not known")

    city = str(raw.get("city") or "").strip()
    if place and (not city or city.lower() == place["town"].lower()):
        town = place["town"]  # our own spelling, e.g. "McArthur" for "MCARTHUR"
    else:
        town = city.title() if city.isupper() or city.islower() else city

    tz = str(raw.get("timezonename") or "").strip()
    try:
        ZoneInfo(tz)
    except Exception:
        offset = _number(raw, "timezoneoffset")
        offset = _number(raw, "timezone") if offset is None else offset
        tz = OFFSET_ZONES.get(int(offset)) if offset is not None else None
        notes.append(f"timezone: {'from the UTC offset' if tz else 'CLINIC_TIMEZONE'}")
        tz = tz or config.CLINIC_TIMEZONE

    digits = re.sub(r"\D", "", str(raw.get("phone") or ""))[-10:]
    phone = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else str(raw.get("phone") or "").strip()

    address = str(raw.get("address") or "").strip()
    closed = (_flag(raw, "active", "isactive") is False or _flag(raw, "inactive", "deleted") is True
              or str(raw.get("status") or "").strip().lower() in ("inactive", "deleted", "closed"))
    serves = _flag(raw, "servicedepartment") is not False and _flag(raw, "patientdepartment") is not False
    if closed:
        notes.append("not offered: inactive")
    elif not serves:
        notes.append("not offered: not a service / patient department")

    return Clinic(
        id=f"{practiceid}:{dep}", practiceid=str(practiceid), departmentid=dep,
        name=str(raw.get("patientdepartmentname") or raw.get("name") or "Clinic").strip(),
        address=address.title() if address.isupper() else address, town=town, state=str(raw.get("state") or "").strip(), zip=zip_code, phone=phone,
        lat=lat, lon=lon, tz=tz, bookable=not closed and serves,
        missing=tuple(missing), notes=tuple(notes))


def short_name(name: str, width: int = 18) -> str:
    """A clinic name cut down for a 160-character text ("Athens Health Ctr")."""
    name = re.sub(r"\s*\(.*?\)", "", name).strip()
    for long, short in SHORTER:
        if len(name) > width:
            name = name.replace(long, short)
    while len(name) > width and " " in name:  # still too long: drop whole words from the end
        name = name.rsplit(" ", 1)[0]
    return name[:width].strip()


# ---------------------------------------------------------------- overrides
def overrides_path() -> Path:
    return Path(config.CLINIC_OVERRIDES) if config.CLINIC_OVERRIDES else DATA / "clinic_overrides.json"


def load_overrides() -> dict[str, dict]:
    """clinic id -> what athenahealth does not know. A missing or broken file means none."""
    try:
        return json.loads(overrides_path().read_text()).get("clinics", {})
    except FileNotFoundError:
        return {}
    except (ValueError, AttributeError) as e:
        log.error("clinic overrides %s could not be read (%s): using none", overrides_path(), e)
        return {}


def _visit_type(v) -> dict:
    """{"appointmenttypeid": "82"} or {"reasonid": "1082"} (+ "providerid"), ids as strings."""
    if not isinstance(v, dict):
        return {}
    return {k: str(v[k]) for k in ("appointmenttypeid", "reasonid", "providerid") if v.get(k) not in (None, "")}


def as_service(c: Clinic, o: dict | None = None) -> dict:
    """A Clinic plus its override entry, in the shape of a services.json row."""
    o = o or {}
    income = o.get("income") if o.get("income") in INCOMES else "all"
    types = {need: _visit_type(v) for need, v in (o.get("appointment_types") or {}).items() if _visit_type(v)}
    return {"id": c.id, "name": c.name, "short": str(o.get("short") or "").strip() or short_name(c.name),
            "for": income, "address": c.address, "town": c.town, "phone": c.phone, "lat": c.lat, "lon": c.lon,
            "tz": c.tz, "tags": [str(t) for t in o.get("needs") or []], "hidden": bool(o.get("hidden")),
            "bookable": c.bookable and c.lat is not None,
            "athena": {"practiceid": c.practiceid, "departmentid": c.departmentid, **types.get("default", {})},
            "types": {need: t for need, t in types.items() if need != "default"}}


# ---------------------------------------------------------------- loading
def sample() -> dict[str, dict]:
    return {s["id"]: s for s in json.loads((DATA / "services.json").read_text())["services"]}


def load_practice(practiceid: str) -> list[Clinic]:
    """Every department of one practice, mapped. Raises AthenaError when athenahealth fails."""
    from .athena import client  # imported here so sample mode never needs it

    out = []
    for raw in client.list("/departments", "departments", practice=practiceid):
        try:
            out.append(map_department(raw, practiceid))
        except ValueError as e:
            log.warning("practice %s: skipped a department (%s)", practiceid, e)
    return out


_lock = threading.Lock()
_state = {"by_practice": {}, "clinics": None, "at": 0.0, "tried": 0.0, "busy": False}


def refresh() -> bool:
    """Reload every practice now. True when at least one practice answered."""
    _state["tried"] = time.time()
    overrides, fresh, ok = load_overrides(), dict(_state["by_practice"]), False
    for pid in config.ATHENA_PRACTICE_IDS:
        try:
            fresh[pid] = load_practice(pid)
            ok = True
        except Exception as e:  # keep this practice's last good copy
            log.error("clinic directory: practice %s not loaded, keeping %d clinics from before: %s",
                      pid, len(fresh.get(pid, [])), e)
    if not ok and _state["clinics"] is None:
        return False
    clinics = {}
    for pid in config.ATHENA_PRACTICE_IDS:
        for c in fresh.get(pid, []):
            if c.lat is None:
                log.warning("clinic %s (%s) skipped: no coordinates and ZIP %r is not in places.json",
                            c.id, c.name, c.zip)
                continue
            clinics[c.id] = as_service(c, overrides.get(c.id))
    for cid in set(overrides) - set(clinics):
        log.info("clinic overrides: %s is not a department athenahealth returned", cid)
    _state.update(by_practice=fresh, clinics=clinics, at=time.time() if ok else _state["at"])
    log.info("clinic directory: %d clinics from %d practices (%d can be offered)", len(clinics),
             len(config.ATHENA_PRACTICE_IDS), sum(c["bookable"] and not c["hidden"] for c in clinics.values()))
    return ok


def _refresh_in_background() -> None:
    try:
        refresh()
    except Exception:
        log.exception("clinic directory reload failed")
    finally:
        _state["busy"] = False


def _athena_directory() -> dict[str, dict]:
    if not config.ATHENA_ENABLED:
        log.error("CLINIC_SOURCE=athena needs ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET: no clinics to offer")
        return {}
    now = time.time()
    if _state["clinics"] is None:  # first use: wait for it
        with _lock:
            if _state["clinics"] is None and now - _state["tried"] >= RETRY_SECONDS:
                refresh()
        return _state["clinics"] or {}
    if now - _state["at"] > config.CLINIC_DIRECTORY_TTL and now - _state["tried"] >= RETRY_SECONDS:
        with _lock:
            if not _state["busy"]:
                _state["busy"], _state["tried"] = True, now
                threading.Thread(target=_refresh_in_background, daemon=True, name="clinic-directory").start()
    return _state["clinics"]


def reset() -> None:
    """Forget the directory, so the next lookup loads it again (tools and checks)."""
    _state.update(by_practice={}, clinics=None, at=0.0, tried=0.0, busy=False)


def every() -> dict[str, dict]:
    """Every clinic we know by id, offered or not: for confirmation texts, reminders, cancelling.

    With CLINIC_SOURCE=athena the sample clinics stay in this lookup (never in offers), so a
    visit booked at one before the switch can still be reminded and cancelled."""
    if config.CLINIC_SOURCE != "athena":
        return sample()
    return {**sample(), **_athena_directory()}


def offered() -> list[dict]:
    """Clinics the finder may offer: from the chosen source, bookable and not hidden."""
    if config.CLINIC_SOURCE != "athena":
        return list(sample().values())
    return [c for c in _athena_directory().values() if c["bookable"] and not c["hidden"]]
