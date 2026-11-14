"""Places, clinics and the mock clinic calendar (app/data/*.json + the bookings table)."""
import datetime as dt
import json
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db

DATA = Path(__file__).parent / "data"
TZ = ZoneInfo("America/New_York")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LOOKAHEAD_DAYS = 14


def _load(name: str, key: str) -> list[dict]:
    return json.loads((DATA / name).read_text())[key]


def places() -> list[dict]:
    return _load("places.json", "places")


def clinics() -> dict[str, dict]:
    return {s["id"]: s for s in _load("services.json", "services")}


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


def free_slots(svc: dict, n: int, after: float | None = None) -> list[float]:
    """Next n open start times for a clinic, from at least an hour from now."""
    earliest = max(after or 0, db.now() + 3600)
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
                out.append(ts)
                if len(out) >= n:
                    return out
    return out


def _option(svc: dict, ts: float, place: dict | None) -> dict:
    return {"service_id": svc["id"], "short": svc["short"], "ts": ts,
            "mi": round(miles(place, svc)) if place else None}


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
    return [_option(svc, ts, place) for ts in free_slots(svc, limit)]


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
