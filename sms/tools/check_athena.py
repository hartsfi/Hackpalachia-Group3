"""Checks booking through athenahealth against tools/fake_athena.py.

    uv run uvicorn tools.fake_athena:app --port 8796
    rm -f /tmp/tb-athena.db; DB_PATH=/tmp/tb-athena.db ATHENA_CLIENT_ID=x ATHENA_CLIENT_SECRET=x \
      ATHENA_BASE_URL=http://127.0.0.1:8796 uv run uvicorn app.main:app --port 8797
    python3 tools/check_athena.py
"""
import json, sys, urllib.parse, urllib.request

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8797"
F = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8796"
ok = True


def post(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req))


def get(url): return json.load(urllib.request.urlopen(url))
def inbox(p): return [m for m in get(B + "/dev/phone/" + urllib.parse.quote(p)) if m["to_phone"] == p]


def text(p, body):
    n = len(inbox(p)); post(B + "/dev/text", {"from_phone": p, "body": body}); new = inbox(p)[n:]
    for m in new: print("      > " + m["body"].replace("\n", " / "))
    return new


def check(name, cond, detail=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def athena_line(msg):
    for line in msg.split("\n"):
        if "Athens Health Ctr" in line:
            return line.split(")")[0]


A, C, D = "+17405550131", "+17405550132", "+17405550133"
print("== Book an athenahealth clinic")
text(A, "checkup in Athens"); r = text(A, "2")
pick = athena_line(r[-1]["body"]); check("athena clinic offered with a live open time", pick, pick)
r = text(A, pick); check("asks name + date of birth once", "date of birth" in r[-1]["body"])
r = text(A, "Jane"); check("unclear answer is asked again", "MM/DD/YYYY" in r[-1]["body"])
r = text(A, "Jane Doe 03/14/1980"); check("booked in athenahealth, confirmation sent", r[-1]["body"].startswith("Booked: Athens Health Ctr"))
st = get(F + "/_state"); check("fake athena has the booking and a new patient", len(st["booked"]) == 1 and st["patients"] == 1, st)
first = list(st["booked"])[0]

print("== Reschedule within athenahealth (no second identity question)")
r = text(A, "RESCHEDULE"); pick = athena_line(r[-1]["body"]); check("new athena times offered", pick, pick)
r = text(A, pick); check("moved without asking name again", r[-1]["body"].startswith("Moved: Athens Health Ctr"))
st = get(F + "/_state"); check("old athena visit cancelled, new one booked", first in st["cancelled"] and len(st["booked"]) == 1 and first not in st["booked"], st)

print("== Cancel")
r = text(A, "CANCEL VISIT"); st = get(F + "/_state")
check("cancelled in athenahealth too", "cancelled" in r[-1]["body"] and not st["booked"], st)

print("== Slot taken at the front desk in the meantime")
text(C, "checkup in Athens"); r = text(C, "2"); pick = athena_line(r[-1]["body"])
opts = get(B + "/api/cases")
case = [c for c in opts if c["patient_phone"] == C][0]
offer = [o for o in json.loads(get(B + f"/api/cases/{case['id']}")["case"]["offers"]) if o["short"] == "Athens Health Ctr"][0]
post(F + f"/_take/{offer['ext']}")
text(C, pick); r = text(C, "John Roe 7/4/1975")
check("told it was taken, offered next athena times", "just taken" in r[-1]["body"] and "1)" in r[-1]["body"])
r = text(C, "1"); check("books the next time", r[-1]["body"].startswith("Booked: Athens Health Ctr"))

print("== athenahealth down")
text(D, "checkup in Athens"); r = text(D, "2"); pick = athena_line(r[-1]["body"])
post(F + "/_down"); text(D, pick); r = text(D, "Ann Poe 1/2/1990")
check("patient told, no crash", "could not reach" in r[-1]["body"])
r = text("+17405550134", "checkup in Athens"); r = text("+17405550134", "2")
check("athena clinic left out of options while down, others still offered",
      "Athens Health Ctr" not in r[-1]["body"] and "1)" in r[-1]["body"])
post(F + "/_up")

msgs = [m for p in (A, C, D) for m in inbox(p)]
check(f"all {len(msgs)} texts <= 160 chars", all(len(m["body"]) <= 160 for m in msgs),
      [m["body"][:40] for m in msgs if len(m["body"]) > 160])
print("ALL PASS" if ok else "SOME FAILED")
