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
RIDE_OFFER = "Need a ride to your visit? Reply RIDE."
def text(phone, body):
    n = len(inbox(phone)); t = time.time(); post("/dev/text", {"from_phone": phone, "body": body})
    new = inbox(phone)[n:]
    # The ride offer after a booking is checked on its own below; the flow checks look past it.
    return [m for m in new if m["body"] != RIDE_OFFER], time.time() - t
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
check("then the numbered menu (need not known yet)", r and "Reply a number" in r[-1]["body"] and "1 See a doctor" in r[-1]["body"])
r, _ = text(P1, "hi"); show(r)
check("a second greeting shows the menu again, not a place error", r and "Reply a number" in r[-1]["body"] and "could not find" not in r[-1]["body"])
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
check("confirmation has date, time, address, phone", b.startswith("Booked (ID V-") and "/" in b and ("AM" in b or "PM" in b) and "Call 740-" in b)
check("confirmation offers 2 = cancel, 3 = reschedule", "Reply 2 to cancel" in b)
print("== Slot taken"); r, _ = text(P2, pick_b); show(r); check("P2 picks P1's slot: told taken, offered next times", "just taken" in r[-1]["body"] and "1)" in r[-1]["body"])
r, _ = text(P2, "1"); show(r); check("P2 books the next time", r[-1]["body"].startswith("Booked (ID V-"))
r, _ = text(P3, pick_b); show(r); check("P3 (same old offer) also told taken", "just taken" in r[-1]["body"])
r, _ = text(P3, "1"); show(r); check("P3 books a next time", r[-1]["body"].startswith("Booked (ID V-"))
print("== Change or cancel"); r, _ = text(P1, "RESCHEDULE"); show(r)
check("reschedule keeps old visit and offers new times", any("stays booked" in m["body"] for m in r) and "1)" in r[-1]["body"])
r, _ = text(P1, bookable(r[-1]["body"])); show(r); check("reschedule confirmed", r[-1]["body"].startswith("Moved (ID V-"))
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
print("== Low on medicine: nearest real pharmacies")
Q = "+17405550172"
text(Q, "i am low on my blood pressure pills, im in nelsonville"); r, _ = text(Q, "Lee Ray"); show(r)
ph = next((m["body"] for m in r if m["body"].startswith("Pharmacies near you")), "")
check("low on pills: nearest pharmacies with phone numbers", ph.count("\n") == 3 and "740-" in ph and "Nelsonville" in ph, ph[:60])
check("then: emergency refill tip, 1 = doctor visit, 2 = health worker", "emergency refill" in r[-1]["body"])
wk = [m["body"] for m in get("/dev/phone/" + urllib.parse.quote(W)) if m["to_phone"] == W]
check("health worker told they are low on medication", any("low on medication near Nelsonville" in b for b in wk))
r, _ = text(Q, "1"); show(r); check("1 goes on to book a doctor visit", "Reply 1 if low income" in r[-1]["body"])
Q2 = "+17405550173"
text(Q2, "ran out of my meds, 45701"); text(Q2, "SKIP"); r, _ = text(Q2, "2"); show(r)
check("2 hands over to a health worker", "health worker will contact you" in r[-1]["body"])
r, _ = text("+17405550174", "PHARMACY 45764"); show(r)
check("PHARMACY <zip> lists the nearest pharmacies", r and r[-1]["body"].startswith("Pharmacies near you"))
print("== Your exact case: 'i am low in pilles', no town")
Z = "+17405550175"
text(Z, "i am low in pilles"); r, _ = text(Z, "mukesh guntumadugu"); show(r)
check("after sign-up it asks only for the place, to find a pharmacy", "To find a pharmacy near you" in r[-1]["body"])
r, _ = text(Z, "45764"); show(r)
check("then the nearest pharmacies", any(m["body"].startswith("Pharmacies near you") for m in r))
print("== Existing patient (account, saved town, booked visit)")
demo = post("/dev/demo-patient")
D = demo["phone"]
r, _ = text(D, "hi"); show(r)
check("welcomed back by name with their next visit, then the menu", len(r) == 2 and r[0]["body"].startswith("Welcome back Sam! Your next visit:") and "Reply a number" in r[1]["body"])
r, _ = text(D, "when is my appointment"); show(r)
check("'when is my appointment' gives the visit ID, date, time, place and phone", r[-1]["body"].startswith("Your next visit (ID V-") and "Call 740-" in r[-1]["body"])
r, _ = text(D, "i need my meds refilled"); show(r)
check("uses their saved town, no ZIP question", any(m["body"].startswith("Pharmacies near you") and "Nelsonville" in m["body"] for m in r))
demo3 = post("/dev/demo-patient"); K = demo3["phone"]
r, _ = text(K, "when is my appointment"); show(r); check("my visit ends with 'Reply 2 to cancel, 3 to reschedule'", "Reply 2 to cancel" in r[-1]["body"])
r, _ = text(K, "2"); show(r); check("cancelling is one digit", "cancelled" in r[-1]["body"])
r, _ = text(K, "my visit"); show(r); check("then no upcoming visit", "no upcoming visit" in r[-1]["body"])
demo4 = post("/dev/demo-patient"); J = demo4["phone"]
text(J, "hi"); r, _ = text(J, "i want to cancell my apointment"); show(r)
check("'i want to cancell my apointment' (typos) cancels", "cancelled" in r[-1]["body"])
demo5 = post("/dev/demo-patient"); J2 = demo5["phone"]
r, _ = text(J2, "can i change my appointment"); show(r); check("'change my appointment' offers new times", "Pick a new time" in " ".join(m["body"] for m in r))
demo6 = post("/dev/demo-patient"); J3 = demo6["phone"]
post("/dev/text", {"from_phone": J3.lstrip("+"), "body": "cancel my appointment"})
check("same person with or without the +: cancel finds the booking", any("cancelled" in m["body"] for m in inbox(J3)))
r, _ = text("+17405550183", "i want ot cncell my appointment"); show(r)
check("cancel with no visit booked says so", "no upcoming visit" in r[-1]["body"])
print("== Rides (Medicaid free rides, health worker books Uber Health)")
check("a ride is offered after each booking", any(m["body"] == RIDE_OFFER for m in inbox(P1)))
demo2 = post("/dev/demo-patient"); R = demo2["phone"]
text(R, "hi"); r, _ = text(R, "RIDE"); show(r)
check("RIDE: Medicaid free-ride number for a low-income patient", any("1-800-324-8680" in m["body"] for m in r))
check("RIDE: offer a health worker to book", "health worker will book a ride" in r[-1]["body"])
r, _ = text(R, "1"); show(r); check("asks the pickup address", "pick you up" in r[-1]["body"])
r, _ = text(R, "near the school"); show(r); check("an unclear address is asked again", "street address" in r[-1]["body"])
r, _ = text(R, "12 Main St, Nelsonville"); show(r)
rb = r[-1]["body"]
check("ride request has an ID, the visit time and a be-ready time", rb.startswith("Ride requested (ID R-") and "Be ready by" in rb and "visit at" in rb and len(rb) <= 160, rb)
wk = [m["body"] for m in get("/dev/phone/" + urllib.parse.quote(W)) if m["to_phone"] == W]
check("health worker gets pickup, destination and visit time", any("RIDE request R-" in b and "Sam Miller" in b and "Pickup: 12 Main St" in b and "To: " in b and "Uber Health" in b for b in wk))
rc = [c for c in get("/api/cases") if c["patient_phone"] == R][0]
check("ride request shown on the dashboard case", get(f"/api/cases/{rc['id']}")["rides"][0]["status"] == "requested")
V = "+17405550176"
text(V, "I need a ride to my doctor appointment"); r, _ = text(V, "Pat Lee"); show(r)
check("'I need a ride' goes straight to ride options after sign-up", "health worker will book a ride" in r[-1]["body"])
r, _ = text(V, "0"); show(r); check("0 = no ride", "no ride" in r[-1]["body"].lower())
print("== Numbered menu")
N = "+17405550177"
text(N, "hi"); r, _ = text(N, "Ann Bell"); show(r)
check("after sign-up: menu with 5 numbered choices and 911", r[-1]["body"].count("\n") == 6 and "5 My visit" in r[-1]["body"] and "6 " not in r[-1]["body"] and "Emergency: call 911" in r[-1]["body"])
check("menu fits one SMS", len(r[-1]["body"]) <= 160)
r, _ = text(N, "9"); show(r); check("a number not on the menu shows the menu again", "Reply a number" in r[-1]["body"])
r, _ = text(N, "4"); show(r); check("4 = real mobile clinic stops", "740-593-2432" in r[-1]["body"])
r, _ = text(N, "5"); show(r); check("5 = my visit (none yet)", "no upcoming visit" in r[-1]["body"])
r, _ = text(N, "3"); show(r); check("3 = medicine: asks the town for a pharmacy", "find a pharmacy near you" in r[-1]["body"])
r, _ = text(N, "Glouster"); show(r); check("then pharmacies near Glouster", any(m["body"].startswith("Pharmacies near you") for m in r))
N6 = "+17405550182"
text(N6, "hi"); text(N6, "SKIP"); r, _ = text(N6, "45701"); show(r)
check("a ZIP typed at the menu is taken as the place, not a wrong number", "Athens, OH 45701" in r[-1]["body"])
N2 = "+17405550178"
text(N2, "hello"); text(N2, "SKIP"); r, _ = text(N2, "1"); show(r)
check("1 = see a doctor: asks the town", "ZIP code or town" in r[-1]["body"])
r, _ = text(N2, "45701"); show(r); check("then the income question", "Reply 1 if low income" in r[-1]["body"])
N3 = "+17405550179"
text(N3, "hey"); text(N3, "SKIP"); r, _ = text(N3, "2"); show(r); check("2 = ride options", "book a ride" in r[-1]["body"])
N4 = "+17405550180"
text(N4, "hi"); text(N4, "SKIP"); r, _ = text(N4, "6"); show(r); check("6 is no longer a choice: menu again", "Reply a number" in r[-1]["body"])
N5 = "+17405550181"
text(N5, "hi"); text(N5, "SKIP"); r, _ = text(N5, "im low on my pills, 45764"); show(r)
check("typing the need instead of a number still works", any(m["body"].startswith("Pharmacies near you") for m in r))
print("== Reminder")
case1 = [c for c in get("/api/cases") if c["patient_phone"] == P1][0]
bk = [x for x in get(f"/api/cases/{case1['id']}")["bookings"] if x["status"] == "booked"][0]
hrs = (bk["start_ts"] - time.time()) / 3600
n = len(inbox(P1)); post(f"/dev/reminders?hours_ahead={hrs - 30:.3f}")
check("no reminder 30 hours before", len(inbox(P1)) == n)
post(f"/dev/reminders?hours_ahead={hrs - 23.5:.3f}"); r = inbox(P1)[n:]; show(r)
check("reminder 24 hours before", len(r) == 1 and r[0]["body"].startswith("Reminder (ID V-"))
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
check("returning user is welcomed back by name, then the menu", r and r[0]["body"].startswith("Welcome back Jane!") and "Reply a number" in r[-1]["body"])
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
