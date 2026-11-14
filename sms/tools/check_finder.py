"""Checks the clinic finder (app/clinics.py, app/finder.py) against the fake athenahealth.

Nothing to start and no key: the fake (tools/fake_athena.py) is run inside this process on a
free local port, so the real client code is exercised over HTTP, including paging, 429 retries,
slow and failing departments. The database and the overrides file are temporary.

    python -m tools.check_finder          (from the sms/ folder; add -v to see the app's log lines)
"""
import datetime as dt
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time

TMP = tempfile.mkdtemp()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]

OVERRIDES = {"clinics": {
    "195900:1": {"short": "Athens Health Ctr", "income": "all", "needs": ["mental_health"],
                 "appointment_types": {"default": {"appointmenttypeid": "82"}, "mental_health": {"appointmenttypeid": "101"}}},
    "195900:2": {"short": "Nelsonville Family", "income": "all", "appointment_types": {"default": {"appointmenttypeid": "82"}}},
    "195900:3": {"short": "Logan Clinic", "income": "standard", "needs": ["dental"],
                 "appointment_types": {"default": {"appointmenttypeid": "82"}, "dental": {"appointmenttypeid": "62"}}},
    "195900:4": {"short": "Glouster Health", "income": "low", "needs": ["mental_health"],
                 "appointment_types": {"default": {"appointmenttypeid": "82"}, "mental_health": {"appointmenttypeid": "101"}}},
    "195901:1": {"short": "Pomeroy Family", "income": "all", "appointment_types": {"default": {"appointmenttypeid": "82"}}},
    "195901:2": {"short": "Gallipolis Clinic", "income": "all", "needs": ["mental_health"],
                 "appointment_types": {"default": {"appointmenttypeid": "82"}, "mental_health": {"reasonid": "1101"}}},
    "195901:3": {"short": "Marietta Health", "income": "standard", "hidden": True},
}}
with open(os.path.join(TMP, "overrides.json"), "w") as f:
    json.dump(OVERRIDES, f)

os.environ.update({
    "DB_PATH": os.path.join(TMP, "check.db"), "ANTHROPIC_API_KEY": "", "TWILIO_ACCOUNT_SID": "",
    "ATHENA_CLIENT_ID": "x", "ATHENA_CLIENT_SECRET": "x", "ATHENA_BASE_URL": f"http://127.0.0.1:{PORT}",
    "ATHENA_PRACTICE_IDS": "195900,195901", "ATHENA_PRACTICE_ID": "", "ATHENA_RETRIES": "3",
    "ATHENA_BACKOFF": "0.05", "ATHENA_TIMEOUT": "5", "ATHENA_SLOT_TIMEOUT": "1.5",
    "CLINIC_SOURCE": "athena", "CLINIC_OVERRIDES": os.path.join(TMP, "overrides.json"),
    "CLINIC_DIRECTORY_TTL": "3600", "CLINIC_TIMEZONE": "America/New_York", "MAX_CLINIC_MILES": "45",
    "CANDIDATES_TO_CHECK": "6", "NEED_TAG_BONUS_MILES": "15", "CHW_NUMBERS": "+17405550199",
})

if "-v" not in sys.argv:
    logging.disable(logging.ERROR)  # the failures below are on purpose; their log lines are noise

import uvicorn  # noqa: E402

from app import clinics, config, db, finder, relay, services  # noqa: E402
from tools import fake_athena as fake  # noqa: E402

server = uvicorn.Server(uvicorn.Config(fake.app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

db.init()
ok = True
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
           "abcdefghijklmnopqrstuvwxyzäöñüà")


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def place(zip_code):
    return services.find_place(zip_code)


def find(zip_code, income, need=None, limit=3):
    services.forget_athena_slots()  # each check starts without cached times
    return finder.recommend(place(zip_code), income, need, limit)


def ids(opts):
    return [o["service_id"] for o in opts]


def fresh():
    """Fake back to its starting state, directory loaded again."""
    fake.reset()
    clinics.reset()
    services.forget_athena_slots()


def text(phone, body):
    before = len(db.phone_messages(phone))
    relay.handle_incoming(phone, body)
    new = [m["body"] for m in db.phone_messages(phone)[before:] if m["to_phone"] == phone]
    for m in new:
        print("      > " + m.replace("\n", " / "))
    return new


NELSONVILLE, ATHENS, POMEROY, GALLIPOLIS, MARIETTA, MCARTHUR, CLEVELAND = (
    "45764", "45701", "45769", "45631", "45750", "45651", "44101")

print("== Directory: every practice, every page, clinic ids")
known = clinics.every()
athena_ids = sorted(i for i in known if ":" in i)
check("both practices loaded past the fake's 3-row pages", len(athena_ids) == 9, athena_ids)
check("clinic id is practiceid:departmentid; department 1 exists in both practices",
      "195900:1" in known and "195901:1" in known and known["195900:1"]["town"] == "Athens"
      and known["195901:1"]["town"] == "Pomeroy")
check("town and phone are tidied for a text", known["195900:1"]["phone"] == "740-555-0112"
      and known["195900:5"]["town"] == "McArthur", known["195900:1"]["phone"])
offered = {c["id"] for c in clinics.offered()}
check("inactive department is known but never offered", "195900:5" in known and "195900:5" not in offered)
check("non-service department (billing office) is never offered", "195900:6" not in offered)
check("hidden clinic is never offered", "195901:3" in known and "195901:3" not in offered)

print("== Field mapping (map_department)")
glouster = place("45732")
check("no coordinates -> centre of the ZIP", (known["195900:4"]["lat"], known["195900:4"]["lon"])
      == (glouster["lat"], glouster["lon"]), (known["195900:4"]["lat"], known["195900:4"]["lon"]))
c = clinics.map_department({"departmentid": 7, "name": "X", "zip": "45732-1234", "latitude": "", "longitude": None}, "9")
check("ZIP+4 and blank coordinates handled, and reported as missing", c.id == "9:7" and c.lat == glouster["lat"]
      and "latitude" in c.missing and any("ZIP 45732" in n for n in c.notes), c.notes)
c = clinics.map_department({"departmentid": 8, "name": "Nowhere", "zip": "99999"}, "9")
check("no coordinates and unknown ZIP -> no location", c.lat is None and c.lon is None)
fake.PRACTICES["195900"]["departments"].append(fake._dept(9, "Lost Clinic", "1 Nowhere Rd", "Nowhere", "99999", "(740) 555-0190"))
fake.PRACTICES["195900"]["schedule"]["9"] = []
clinics.reset()
check("...and the directory skips that clinic", "195900:9" not in clinics.every() and "195900:1" in clinics.every())
fake.PRACTICES["195900"]["departments"].pop()
c = clinics.map_department({"departmentid": 1, "servicedepartment": "false", "timezoneoffset": -6, "latitude": 39.3, "longitude": -82.1}, "9")
check("flags as text, timezone from the UTC offset", not c.bookable and c.tz == "America/Chicago", c.tz)
c = clinics.map_department({"departmentid": 1, "zip": "45701", "status": "INACTIVE"}, "9")
check("a closed department is not bookable", not c.bookable)
fresh()

print("== Nearest first, income filter, radius")
config.CANDIDATES_TO_CHECK = 10
low = find(NELSONVILLE, "low", limit=10)
miles = [o["mi"] for o in low]
check("nearest first", ids(low)[:3] == ["195900:2", "195900:4", "195900:1"] and miles == sorted(miles), list(zip(ids(low), miles)))
check("options have the shape services._option() makes",
      all(list(o) == ["service_id", "short", "ts", "ext", "atype", "mi"] for o in low))
check("low income: standard-only clinic (Logan) left out, low-only clinic (Glouster) offered",
      "195900:3" not in ids(low) and "195900:4" in ids(low))
std = find(NELSONVILLE, "standard", limit=10)
check("other insurance: low-only clinic left out, standard clinic offered",
      "195900:4" not in ids(std) and "195900:3" in ids(std), ids(std))
check("one option per clinic", len(set(ids(low))) == len(low) and len(set(ids(std))) == len(std))
check("default limit is 3", len(find(NELSONVILLE, "low")) == 3)
check("nothing within 45 miles (Cleveland) -> []", find(CLEVELAND, "low") == [])
config.MAX_CLINIC_MILES = 10
check("radius cut-off: 10 miles keeps only the two nearest", ids(find(NELSONVILLE, "low", limit=10)) == ["195900:2", "195900:4"])
config.MAX_CLINIC_MILES = 45
check("hidden clinic not offered in its own town", "195901:3" not in ids(find(MARIETTA, "standard", limit=10)))
check("inactive clinic not offered in its own town", "195900:5" not in ids(find(MCARTHUR, "standard", limit=10)))

print("== Open times are fetched only for the candidates")
config.CANDIDATES_TO_CHECK = 2
fake.S["open_calls"].clear()
two = find(NELSONVILLE, "low", limit=3)
check("2 candidates -> 2 clinics asked, 2 options", sorted(fake.S["open_calls"]) == ["195900:2", "195900:4"] and len(two) == 2,
      fake.S["open_calls"])
config.CANDIDATES_TO_CHECK = 6
fake.S["open_calls"].clear()
find(NELSONVILLE, "low")
first = dict(fake.S["open_calls"])
finder.recommend(place(NELSONVILLE), "low")
check("a second look within 30 seconds uses the slot cache", fake.S["open_calls"] == first, first)
services.forget_athena_slots("195900:2")
finder.recommend(place(NELSONVILLE), "low")
again = {k for k in first if fake.S["open_calls"][k] > first[k]}
check("forget_athena_slots(clinic) refetches only that clinic", again == {"195900:2"}, again)

print("== Need tags and appointment types")
plain = find(NELSONVILLE, "low")
check("no need: default appointment type everywhere", {o["atype"] for o in plain} == {"82"})
mh = find(NELSONVILLE, "low", "mental_health")
check("tagged clinic 8 mi away beats an untagged one 0 mi away (within the 15-mile head start)",
      ids(mh)[0] == "195900:4" and ids(mh).index("195900:4") < ids(mh).index("195900:2"), ids(mh))
by = {o["service_id"]: o["atype"] for o in mh}
check("appointment type mapped to the need; default type where the clinic has none",
      by["195900:4"] == "101" and by["195900:1"] == "101" and by["195900:2"] == "82", by)
config.NEED_TAG_BONUS_MILES = 5
check("...but not when the untagged one is much closer (head start 5 mi)",
      ids(find(NELSONVILLE, "low", "mental_health"))[0] == "195900:2")
config.NEED_TAG_BONUS_MILES = 15
gal = {o["service_id"]: o["atype"] for o in find(GALLIPOLIS, "low", "mental_health")}
check("a need mapped by reasonid books that reason's type", gal.get("195901:2") == "101", gal)
check("a need no clinic is tagged for ranks plainly by distance",
      ids(find(NELSONVILLE, "low", "appointment")) == ids(plain))

print("== Two practices in one answer")
pom = find(POMEROY, "low", limit=10)
check("clinics of both practices offered together", {i.split(":")[0] for i in ids(pom)} == {"195900", "195901"}, ids(pom))
a, b = (next(o for o in pom if o["service_id"] == i) for i in ("195900:1", "195901:1"))
check("same appointment id in two practices stays two different visits",
      a["ext"] == b["ext"] and a["service_id"] != b["service_id"], (a["ext"], b["ext"]))

print("== Failures: one clinic, slow clinic, 429, everything")
fake.S["fail"].add("195900:2")
one = find(NELSONVILLE, "low", limit=10)
check("a department that answers 500 is skipped, the rest are offered",
      "195900:2" not in ids(one) and ids(one)[:2] == ["195900:4", "195900:1"], ids(one))
fake.S["fail"].clear()
fake.S["slow"]["195900:4"] = 4
t = time.monotonic()
slow = find(NELSONVILLE, "low", limit=10)
took = time.monotonic() - t
check("a department slower than ATHENA_SLOT_TIMEOUT is skipped without holding up the text",
      "195900:4" not in ids(slow) and "195900:2" in ids(slow) and took < 3.2, f"{took:.1f}s")
fake.S["slow"].clear()
time.sleep(2.6)  # let the slow call end before the next check counts calls
fake.S["throttle"], fake.S["retry_after"] = 2, 0.1
check("429 with Retry-After is retried", len(find(NELSONVILLE, "low")) == 3 and fake.S["throttle"] == 0)
fake.S["down"] = True
t = time.monotonic()
check("every call failing -> []", find(NELSONVILLE, "low") == [], f"{time.monotonic() - t:.1f}s")
P0 = "+17405553000"
text(P0, "need a checkup, 45764")
r = text(P0, "1")
chw = [m["body"] for m in db.phone_messages("+17405550199")]
check("...and the patient is told a health worker will help; the worker is alerted",
      r and "health worker" in r[-1] and any("no open times near Nelsonville" in m for m in chw))
check("directory kept from the last good load while athenahealth is down", len(clinics.offered()) == 6)
clinics._state.update(at=0.0, tried=0.0)  # as if the hour had passed
before = clinics.every()
while clinics._state["busy"]:
    time.sleep(0.05)
check("a failed background reload keeps serving the last good copy", clinics.every() == before and len(before) > 9)
fake.S["down"] = False
fake.PRACTICES["195901"]["departments"][0]["phone"] = "(740) 555-0139"
clinics._state.update(at=0.0, tried=0.0)
clinics.every()
while clinics._state["busy"]:
    time.sleep(0.05)
check("the next reload picks up changes", clinics.every()["195901:1"]["phone"] == "740-555-0139")
fake.PRACTICES["195901"]["departments"][0]["phone"] = "(740) 555-0131"
fresh()

print("== Department timezone")
logan = next(d for d in fake.PRACTICES["195900"]["departments"] if d["departmentid"] == "3")
eastern = next(o for o in find(NELSONVILLE, "standard", limit=10) if o["service_id"] == "195900:3")
logan["timezonename"] = "America/Chicago"
fresh()
central = next(o for o in find(NELSONVILLE, "standard", limit=10) if o["service_id"] == "195900:3")
shown = services.when(central["ts"], tz=services.clinic_tz("195900:3"))
check("slot times are read and shown in the department's timezone",
      central["ext"] == eastern["ext"] and central["ts"] - eastern["ts"] == 3600 and shown.endswith(("10AM", "2:30PM")), shown)
logan["timezonename"] = "America/New_York"
fresh()

print("== One plain SMS")
config.CANDIDATES_TO_CHECK = 10
texts = []
for z in (NELSONVILLE, ATHENS, POMEROY, GALLIPOLIS, MARIETTA, MCARTHUR, "43138"):
    for income in ("low", "standard"):
        for need in (None, "mental_health"):
            opts = find(z, income, need)
            for lang in ("en", "es"):
                if opts:
                    texts.append(services.option_lines(opts, relay._t(lang, "options", n=len(opts))))
config.CANDIDATES_TO_CHECK = 6
late = dt.datetime(2026, 12, 28, 10, 30, tzinfo=services.TZ).timestamp()
worst = [{"service_id": f"195900:{i}", "short": "W" * 18, "ts": late + i * 86400, "ext": "1", "atype": "82", "mi": 45}
         for i in (1, 2, 3)]  # longest name, two-digit miles, a 10:30AM time in late December
texts += [services.option_lines(worst, relay._t(lang, "options", n=3)) for lang in ("en", "es")]
print("      > " + texts[0].replace("\n", " / "))
print("      > " + texts[-1].replace("\n", " / "))
long = [f"{len(m)}: {m[:40]}" for m in texts if len(m) > 160]
check(f"all {len(texts)} option texts fit 160 characters, longest names included", not long, long)
check("GSM-7 alphabet only, no links", not [m for m in texts if set(m) - GSM7 or "http" in m.lower()])
check("each line has clinic, miles and next open time",
      all(l.split(") ")[1] and "mi " in l and l.endswith(("AM", "PM")) for m in texts for l in m.split("\n")[1:]))

print("== Booking across two practices (patient ids are per practice)")
P1 = "+17405553001"
text(P1, "need a checkup, 45769")
r = text(P1, "1")
check("Pomeroy patient is offered the Pomeroy clinic first", r and r[-1].split("\n")[1].startswith("1) Pomeroy Family 0mi"))
r = text(P1, "1")
check("first booking in a practice asks name and date of birth", r and "date of birth" in r[-1])
r = text(P1, "Pat Sample 02/03/1970")
st = fake.S["p"]
check("booked in practice 195901 only", r and r[-1].startswith("Booked: Pomeroy Family") and len(st["195901"]["booked"]) == 1
      and not st["195900"]["booked"] and len(st["195901"]["patients"]) == 1 and not st["195900"]["patients"])
check("the confirmation fits one SMS and has address and phone", len(r[-1]) <= 160 and "110 Court St" in r[-1] and "740-555-0131" in r[-1])
old_appt = next(iter(st["195901"]["booked"]))
r = text(P1, "RESCHEDULE")
gallipolis = next(l for l in r[-1].split("\n") if "Gallipolis" in l)[0]
r = text(P1, gallipolis)
check("reschedule inside the practice: one athenahealth reschedule call, no second identity question",
      r and r[-1].startswith("Moved: Gallipolis Clinic") and st["195901"]["cancelled"] == [old_appt]
      and len(st["195901"]["booked"]) == 1 and old_appt not in st["195901"]["booked"])
r = text(P1, "RESCHEDULE")
athens = next(l for l in r[-1].split("\n") if "Athens" in l)[0]
r = text(P1, athens)
check("moving to a clinic of another practice asks for identity there", r and "date of birth" in r[-1])
r = text(P1, "Pat Sample 02/03/1970")
check("across practices: booked in the new practice, cancelled in the old one",
      r and r[-1].startswith("Moved: Athens Health Ctr") and len(st["195900"]["booked"]) == 1
      and not st["195901"]["booked"] and len(st["195901"]["cancelled"]) == 2)
pids = (db.athena_patient(P1, "195900"), db.athena_patient(P1, "195901"))
check("a separate patient id is kept for each practice", pids[0] and pids[1] and pids[0] != pids[1], pids)
r = text(P1, "CANCEL VISIT")
check("cancel reaches the right practice", r and "cancelled" in r[-1] and not st["195900"]["booked"]
      and len(st["195900"]["cancelled"]) == 1)

print("== Sample mode is unchanged")
config.CLINIC_SOURCE = "sample"
config.ATHENA_ENABLED = False
sample = finder.recommend(place(NELSONVILLE), "low")
check("CLINIC_SOURCE=sample offers the services.json clinics, mobile unit first",
      ids(sample)[0] == "mobile-nelsonville" and all(":" not in i for i in ids(sample)), ids(sample))

print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
