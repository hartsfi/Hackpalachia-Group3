"""Checks every acceptance criterion of user story 1 (docs/USER_STORIES.md) against a running
server in dev mode. Use a fresh database:

    rm -f /tmp/tb-check.db; DB_PATH=/tmp/tb-check.db uv run uvicorn app.main:app --port 8794
    python3 tools/check_story.py http://127.0.0.1:8794
"""
import json, sys, time, urllib.parse, urllib.request
B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8794"; W = "+17405550199"
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
def post(path, data=None):
    req = urllib.request.Request(B + path, data=json.dumps(data or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req))
def get(path): return json.load(urllib.request.urlopen(B + path))
def inbox(phone): return [m for m in get("/dev/phone/" + urllib.parse.quote(phone)) if m["to_phone"] == phone]
def text(phone, body):
    n = len(inbox(phone)); t = time.time(); post("/dev/text", {"from_phone": phone, "body": body})
    new = inbox(phone)[n:]; return new, time.time() - t
ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
def bookable(msg):
    """Number of the first option the server books itself (not a call-to-book mobile stop)."""
    for line in msg.split("\n"):
        if line[:2] in ("1)", "2)", "3)") and "Mobile" not in line:
            return line[0]
    return "1"
def show(msgs):
    for m in msgs: print("      > " + m["body"].replace("\n", " / "))

P1, P2, P3 = "+17405551001", "+17405551002", "+17405551003"
print("== New user: account"); r, dt = text(P1, "hi"); show(r)
check("new number gets the welcome, then is asked for a name", len(r) == 2 and "first and last name" in r[-1]["body"], f"{dt*1000:.0f} ms")
check("reply within 10 seconds", dt < 10)
r, _ = text(P1, "hi"); show(r); check("a greeting is not taken as a name", "first and last name" in r[-1]["body"])
r, _ = text(P1, "Jane Doe"); show(r)
check("account created: name + CareLine ID sent", any("Thanks Jane! Your CareLine ID is CL-" in m["body"] for m in r))
print("== Start")
check("then asks what care + where", r and "ZIP" in r[-1]["body"] and "care" in r[-1]["body"])
r, _ = text(P1, "hi"); show(r)
check("a second greeting gets the first question again, not a place error", r and "ZIP" in r[-1]["body"] and "could not find" not in r[-1]["body"])
print("== Location"); r, _ = text(P1, "I live in Chicago"); show(r)
check("unknown place asks again", r and "could not find" in r[-1]["body"])
r, _ = text(P1, "need a checkup, 45764"); show(r)
check("ZIP recognized and confirmed back", r and "Nelsonville, OH 45764" in r[-1]["body"])
text(P3, "Nelsonville"); r, _ = text(P3, "John Roe"); show(r)
check("town name recognized (from the first text, after sign-up)", r and "Nelsonville, OH 45764" in r[-1]["body"])
text(P2, "https://maps.apple.com/?ll=39.4612,-82.2290&q=My%20Location sore throat"); r, _ = text(P2, "SKIP"); show(r)
check("SKIP still gives an ID", any("Your CareLine ID is CL-" in m["body"] for m in r))
check("shared GPS link recognized (smartphones)", r and "Nelsonville" in r[-1]["body"])
print("== Options"); r, _ = text(P1, "1"); show(r)
lines = [l for l in r[-1]["body"].split("\n") if l[:2] in ("1)", "2)", "3)")]
check("up to 3 numbered options with name, distance, time", 1 <= len(lines) <= 3 and all("mi " in l and ("AM" in l or "PM" in l) for l in lines))
r2, _ = text(P2, "1"); r3, _ = text(P3, "1")
check("two other patients see the same first slot", r2[-1]["body"].split("\n")[1] == r3[-1]["body"].split("\n")[1], r2[-1]["body"].split("\n")[1])
print("== Real mobile clinic route")
mob = next((l for l in lines if "Mobile" in l), None)
check("a real mobile clinic stop is offered", mob, mob)
r, _ = text("+17405550170", "MOBILE"); show(r)
check("MOBILE lists the next stops and the booking phone", r and "740-593-2432" in r[-1]["body"] and r[-1]["body"].count("\n") >= 1)
pick_b = bookable(r2[-1]["body"])
print("== Booking"); r, _ = text(P1, pick_b); show(r); b = r[-1]["body"]
check("confirmation has date, time, address, phone", b.startswith("Booked:") and "/" in b and ("AM" in b or "PM" in b) and "Call 740-" in b)
print("== Slot taken"); r, _ = text(P2, pick_b); show(r); check("P2 picks P1's slot: told taken, offered next times", "just taken" in r[-1]["body"] and "1)" in r[-1]["body"])
r, _ = text(P2, "1"); show(r); check("P2 books the next time", r[-1]["body"].startswith("Booked:"))
r, _ = text(P3, pick_b); show(r); check("P3 (same old offer) also told taken", "just taken" in r[-1]["body"])
r, _ = text(P3, "1"); show(r); check("P3 books a next time", r[-1]["body"].startswith("Booked:"))
print("== Change or cancel"); r, _ = text(P1, "RESCHEDULE"); show(r)
check("reschedule keeps old visit and offers new times", any("stays booked" in m["body"] for m in r) and "1)" in r[-1]["body"])
r, _ = text(P1, bookable(r[-1]["body"])); show(r); check("reschedule confirmed", r[-1]["body"].startswith("Moved:"))
r, _ = text(P2, "cancel visit"); show(r); check("CANCEL VISIT confirmed", "cancelled" in r[-1]["body"])
r, _ = text(P2, "cancel visit"); show(r); check("cancel with nothing booked is explained", "no upcoming visit" in r[-1]["body"])
print("== Mobile stop: call to book")
M = "+17405550171"
text(M, "need a checkup in Nelsonville"); text(M, "Kim Lee"); r, _ = text(M, "1")
mline = next((l for l in r[-1]["body"].split("\n") if "Mobile" in l), "")
r, _ = text(M, mline[:1] or "1"); show(r)
check("picking a mobile stop gives the real phone, no fake booking", "740-593-2432" in r[-1]["body"] and not r[-1]["body"].startswith("Booked"))
r, _ = text(M, "1"); show(r); check("reply 1: a health worker will call for them", "health worker will call" in r[-1]["body"])
wk = [m["body"] for m in get("/dev/phone/" + urllib.parse.quote(W)) if m["to_phone"] == W]
check("health worker is asked to call and book", any("please call 740-593-2432" in b and "Kim Lee" in b for b in wk))
print("== Reminder")
case1 = [c for c in get("/api/cases") if c["patient_phone"] == P1][0]
bk = [x for x in get(f"/api/cases/{case1['id']}")["bookings"] if x["status"] == "booked"][0]
hrs = (bk["start_ts"] - time.time()) / 3600
n = len(inbox(P1)); post(f"/dev/reminders?hours_ahead={hrs - 30:.3f}")
check("no reminder 30 hours before", len(inbox(P1)) == n)
post(f"/dev/reminders?hours_ahead={hrs - 23.5:.3f}"); r = inbox(P1)[n:]; show(r)
check("reminder 24 hours before", len(r) == 1 and r[0]["body"].startswith("Reminder:"))
post(f"/dev/reminders?hours_ahead={hrs - 23:.3f}"); check("reminder sent only once", len(inbox(P1)) == n + 1)
print("== Emergency"); r, _ = text("+17405551004", "I can't breathe"); show(r)
check("emergency -> 911, no booking question", len(r) == 1 and "911" in r[0]["body"] and "ZIP" not in r[0]["body"])
r, _ = text("+17405551005", "I want to end my life"); show(r); check("crisis -> 988", "988" in r[-1]["body"])
text("+17405551006", "checkup in Athens"); r, _ = text("+17405551006", "my dad has chest pain"); show(r)
check("emergency mid-booking stops the flow", "911" in r[-1]["body"])
r, _ = text("+17405551006", "1"); check("next '1' is not treated as a booking answer", not r, f"{len(r)} texts")
print("== Spanish"); r, _ = text("+17405551007", "hola, necesito una cita en Athens"); show(r)
check("Spanish reply", r and "Para crear su cuenta" in r[-1]["body"])
print("== Returning user")
c1 = [c for c in get("/api/cases") if c["patient_phone"] == P1][0]["id"]
post("/dev/text", {"from_phone": W, "body": f"#{c1} close"})
r, _ = text(P1, "hi"); show(r)
check("returning user is welcomed back by name, no sign-up again", r and r[-1]["body"].startswith("Welcome back Jane!") and len(r) == 1)
r, _ = text(P1, "my id"); show(r); check("MY ID returns the ID and name", r and "CL-" in r[-1]["body"] and "Jane Doe" in r[-1]["body"])
print("== Opt out"); r, _ = text(P3, "STOP"); check("STOP gets no reply from us (Twilio confirms)", not r)
n = len(inbox(P3)); post("/dev/text", {"from_phone": W, "body": f"#{[c for c in get('/api/cases') if c['patient_phone']==P3][0]['id']} hello?"})
post("/dev/reminders?hours_ahead=400"); check("no texts after STOP (worker reply, reminders)", len(inbox(P3)) == n)
r, _ = text(P3, "START"); check("START opts back in", r and "again" in r[-1]["body"])
print("== Plain SMS only")
pats = [P1, P2, P3] + [f"+1740555100{i}" for i in range(4, 8)]
msgs = [m for p in pats for m in inbox(p)]
too_long = [m["body"] for m in msgs if len(m["body"]) > 160]
not_gsm = [m["body"] for m in msgs if set(m["body"]) - GSM7]
links = [m["body"] for m in msgs if "http" in m["body"]]
check(f"all {len(msgs)} patient texts <= 160 chars", not too_long, "; ".join(f"{len(b)}: {b[:50]}" for b in too_long))
check("all in the GSM-7 alphabet (one SMS segment)", not not_gsm, "; ".join(b[:60] for b in not_gsm))
check("no links", not links)
print("ALL PASS" if ok else "SOME FAILED")
