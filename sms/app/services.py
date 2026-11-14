"""Places, clinics and the mock clinic calendar (app/data/*.json + the bookings table)."""
import datetime as dt
import difflib
import json
import logging
import math
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

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
    out = {s["id"]: s for s in _load("services.json", "services")}
    out.update({s["id"]: s for s in mobile_stops()})
    return out


def mobile_route() -> dict:
    return json.loads((DATA / "mobile_route.json").read_text())


def mobile_stops() -> list[dict]:
    """Each real mobile clinic stop as a call-to-book service on one date (no local booking)."""
    route = mobile_route()
    where = {p["town"]: p for p in places()}
    stops = []
    for st in route["stops"]:
        town = where.get(st["town"])
        if not town:
            continue
        stops.append({
            "id": f"mobile-{st['date']}-{st['town'].lower()}", "name": route["name"], "kind": "call",
            "short": f"Mobile {st['town']}", "for": route["for"], "address": st["where"], "town": st["town"],
            "phone": route["phone"], "lat": town["lat"], "lon": town["lon"],
            "date": st["date"], "start": st["start"], "end": st["end"],
        })
    return stops


def upcoming_mobile(limit: int = 3) -> list[dict]:
    soon = [s for s in mobile_stops() if free_slots(s, 1)]
    return sorted(soon, key=lambda s: (s["date"], s["start"]))[:limit]


def hours(svc: dict) -> str:
    """'9AM-3:30PM' for a dated stop."""
    def fmt(t):
        h, m = map(int, t.split(":"))
        d = dt.datetime(2000, 1, 1, h, m)
        return d.strftime("%I:%M%p").lstrip("0").replace(":00", "")
    return f"{fmt(svc['start'])}-{fmt(svc['end'])}"


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
        if re.search(r"\b" + re.escape(p["town"]) + r"\b", text, re.I):
            return p
    # Typos ("nelsonvile", "glouser"): close match on single words and word pairs, 5+ letters.
    words = re.findall(r"[a-z]+", text.lower())
    grams = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
    towns = {p["town"].lower(): p for p in pl}
    for g in grams:
        if len(g) >= 5:
            hit = difflib.get_close_matches(g, towns, n=1, cutoff=0.85)
            if hit:
                return towns[hit[0]]
    return None


def miles(a: dict, b: dict) -> float:
    r = 3958.8
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    dla, dlo = la2 - la1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def when(ts: float, weekday: bool = True) -> str:
    """'Tue 11/17 1:30PM' (Eastern time)."""
    d = dt.datetime.fromtimestamp(ts, TZ)
    t = d.strftime("%I:%M%p").lstrip("0").replace(":00", "")
    return (f"{DAYS[d.weekday()]} " if weekday else "") + f"{d.month}/{d.day} {t}"


def free_slots(svc: dict, n: int, after: float | None = None) -> list[dict]:
    """Next n open slots for a clinic, from at least an hour from now.

    Each slot is {"ts": start time, "ext": athenahealth appointmentid or None, "atype": type id}.
    Clinics with an "athena" block read their schedule from athenahealth (when keys are set);
    the rest use the mock calendar built from "days" and "times".
    """
    earliest = max(after or 0, db.now() + 3600)
    if svc.get("date"):  # a dated mobile clinic stop: one opening, its start time
        y, m, d = map(int, svc["date"].split("-"))
        hh, mm = map(int, svc["start"].split(":"))
        ts = dt.datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp()
        return [{"ts": ts, "ext": None, "atype": None}] if ts > earliest else []
    if svc.get("athena") and config.ATHENA_ENABLED:
        taken_ext = db.taken_external(svc["id"])
        return [s for s in _athena_slots(svc) if s["ts"] > earliest and s["ext"] not in taken_ext][:n]
    taken = db.taken_slots(svc["id"])
    today = dt.datetime.now(TZ).date()
    out = []
    for d in range(LOOKAHEAD_DAYS):
        day = today + dt.timedelta(days=d)
        if DAYS[day.weekday()] not in svc["days"]:
            continue
        for t in svc["times"]:
            hh, mm = map(int, t.split(":"))
            ts = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ).timestamp()
            if ts > earliest and ts not in taken:
                out.append({"ts": ts, "ext": None, "atype": None})
                if len(out) >= n:
                    return out
    return out


_athena_cache: dict[str, tuple[float, list[dict]]] = {}
ATHENA_CACHE_SECONDS = 30  # Preview allows only a few requests a second


def forget_athena_slots(service_id: str | None = None) -> None:
    """Drop cached athenahealth times for one clinic, or all of them (after an athena error)."""
    if service_id is None:
        _athena_cache.clear()
    else:
        _athena_cache.pop(service_id, None)


def _athena_slots(svc: dict) -> list[dict]:
    hit = _athena_cache.get(svc["id"])
    if hit and time.time() - hit[0] < ATHENA_CACHE_SECONDS:
        return hit[1]
    from .athena import client  # imported here so the mock calendar never needs it

    today = dt.datetime.now(TZ).date()
    try:
        raw = client.open_slots(svc["athena"], today, today + dt.timedelta(days=LOOKAHEAD_DAYS))
    except Exception as e:  # athena down or misconfigured: skip this clinic, never crash a text
        log.warning("athenahealth open slots for %s failed: %s", svc["id"], e)
        return []
    slots = []
    for a in raw:
        try:
            m, d, y = map(int, a["date"].split("/"))
            hh, mm = map(int, a["starttime"].split(":"))
        except (KeyError, ValueError):
            continue
        slots.append({"ts": dt.datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp(),
                      "ext": str(a["appointmentid"]), "atype": str(a.get("appointmenttypeid") or "") or None})
    slots.sort(key=lambda x: x["ts"])
    _athena_cache[svc["id"]] = (time.time(), slots)
    return slots


def uses_athena(service_id: str) -> bool:
    return bool(config.ATHENA_ENABLED and clinics().get(service_id, {}).get("athena"))


def _option(svc: dict, slot: dict, place: dict | None) -> dict:
    return {"service_id": svc["id"], "short": svc["short"], "ts": slot["ts"], "ext": slot["ext"],
            "atype": slot["atype"], "mi": round(miles(place, svc)) if place else None}


def options(income: str, place: dict, limit: int = 3) -> list[dict]:
    """Nearest clinics for this income group, each with its next open time."""
    group = [s for s in clinics().values() if s["for"] in (income, "all")]
    group.sort(key=lambda s: miles(place, s))
    opts = []
    for s in group:
        slot = free_slots(s, 1)
        if slot:
            opts.append(_option(s, slot[0], place))
        if len(opts) >= limit:
            break
    return opts


def next_times(service_id: str, place: dict | None, limit: int = 3) -> list[dict]:
    """Next open times at one clinic (used when a picked slot was just taken)."""
    svc = clinics()[service_id]
    return [_option(svc, slot, place) for slot in free_slots(svc, limit)]


def option_lines(opts: list[dict], header: str, budget: int = 160) -> str:
    """Numbered options in one text, dropping detail until it fits the budget."""
    if len({o["service_id"] for o in opts}) == 1:  # all at one clinic: the header names it
        return "\n".join([header] + [f"{i}) {when(o['ts'])}" for i, o in enumerate(opts, 1)])
    formats = [
        lambda i, o: f"{i}) {o['short']} {o['mi']}mi {when(o['ts'])}",
        lambda i, o: f"{i}) {o['short']} {o['mi']}mi {when(o['ts'], weekday=False)}",
        lambda i, o: f"{i}) {o['short'][:14]} {o['mi']}mi {when(o['ts'], weekday=False)}",
    ]
    msg = ""
    for f in formats:
        msg = "\n".join([header] + [f(i, o) for i, o in enumerate(opts, 1)])
        if len(msg) <= budget:
            break
    return msg
