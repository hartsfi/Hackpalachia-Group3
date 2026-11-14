"""Checks the free-text hook (app/understand.py) end to end through the booking flow.

Claude is replaced by a scripted stand-in, so this needs no key and no network. It checks
the wiring: that a free-text reply is turned into the exact answer, that answers which
already parse never reach the model, that out-of-range or unclear picks are refused, and
that with the feature off the flow behaves exactly as before.

    python -m tools.check_understand          (from the sms/ folder)

Pass --live to use the real model instead (needs ANTHROPIC_API_KEY; makes about ten short calls).
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "check.db")
LIVE = "--live" in sys.argv
if not LIVE:
    os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = os.environ["ATHENA_CLIENT_ID"] = ""  # dev mode, mock calendar

from app import config, db, relay, understand  # noqa: E402

db.init()
ok = True
calls = []


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def text(phone, body):
    """Send a text as a patient; return the texts they got back."""
    before = len(db.phone_messages(phone))
    relay.handle_incoming(phone, body)
    new = [m["body"] for m in db.phone_messages(phone)[before:] if m["to_phone"] == phone]
    for m in new:
        print("      > " + m.replace("\n", " / "))
    return new


def notes(phone):
    case = db.open_case_for(phone)
    return [m["body"] for m in db.case_messages(case["id"]) if m["channel"] == "ai"]


# ---- scripted stand-in for Claude: answers by which field the schema asks for ----
SCRIPT = {}


def fake_ask(task, message, schema):
    field = next(iter(schema["properties"]))
    calls.append((field, message))
    return SCRIPT.get(message)


if not LIVE:
    understand._ask = fake_ask
    config.UNDERSTAND_ENABLED = True
    SCRIPT.update({
        "bad cough, im out in nelsonvile": {"zip": "45764"},
        "im on medicaid": {"answer": "1"},
        "why do you need to know": {"answer": "unclear"},
        "the second one works": {"option": 2},
        "none of those work, i need evenings": {"option": 0},
        "sure": {"option": -1},
        "whichever": {"option": 9},
        "somewhere near mars": {"zip": "99999"},
    })
print(f"== {'LIVE model' if LIVE else 'scripted stand-in'}; understanding enabled: {understand.enabled()}")
if LIVE and not understand.enabled():
    sys.exit("Set ANTHROPIC_API_KEY (and leave FREE_TEXT unset) to run --live.")

A, B, C, D = "+17405552001", "+17405552002", "+17405552003", "+17405552004"

print("== Misspelled town in the first text")
r = text(A, "bad cough, im out in nelsonvile")
check("place understood and confirmed back", r and "Nelsonville, OH 45764" in r[-1])

print("== Income in the patient's own words")
r = text(A, "im on medicaid")
check("moves on to visit options", r and "1)" in r[-1] and "book" in r[-1])
check("interpretation recorded next to the original words", any("im on medicaid" in n and "as: 1" in n for n in notes(A)))

print("== Picking a visit in words")
offered = r[-1].split("\n")
r = text(A, "the second one works")
check("books the visit", r and r[-1].startswith("Booked:"))
second = offered[2].split(") ", 1)[1].split(" ")[0] if len(offered) > 2 else ""
check("booked the option that was second in the list", second and second in r[-1], second)

print("== Answers that already parse never reach the model")
n = len(calls)
text(B, "need a checkup, 45764")
text(B, "1")
r = text(B, "1")
if not LIVE:
    check("no model calls for ZIP, 1, 1", len(calls) == n, f"{len(calls) - n} calls")
check("exact answers still book", r and r[-1].startswith("Booked:"))

print("== Unclear or out-of-range answers ask again, exactly as before")
text(C, "hi")
r = text(C, "somewhere near mars")
check("place not on our list is refused", r and "could not find" in r[-1])
text(C, "45701")
r = text(C, "why do you need to know")
check("unclear income asks again", r and r[-1].startswith("Please reply 1"))
text(C, "2")
r = text(C, "sure")
check("unclear pick asks again", r and r[-1].startswith("Reply a number"))
if not LIVE:
    r = text(C, "whichever")
    check("a number that was not offered is refused", r and r[-1].startswith("Reply a number"))
r = text(C, "none of those work, i need evenings")
check("declining all hands over to a health worker", r and "health worker will text" in r[-1])

print("== Emergencies never reach the model")
n = len(calls)
r = text(D, "my chest pain is getting worse")
check("911 reply", r and "911" in r[-1])
if not LIVE:
    check("no model call", len(calls) == n)

if not LIVE:
    print("== Model failure falls back to the scripted question")
    understand._ask = lambda *a: None
    E = "+17405552005"
    text(E, "need a doctor 45701")
    r = text(E, "im on medicaid")
    check("asks the income question again", r and r[-1].startswith("Please reply 1"))

    print("== Switched off: identical to the scripted flow")
    config.UNDERSTAND_ENABLED = False
    understand._ask = fake_ask
    n = len(calls)
    F = "+17405552006"
    r = text(F, "bad cough, im out in nelsonvile")
    check("misspelled town is not understood", r and "ZIP" in r[-1] and "Nelsonville" not in r[-1])
    text(F, "45764")
    r = text(F, "im on medicaid")
    check("free-text income asks again", r and r[-1].startswith("Please reply 1"))
    check("no model calls while off", len(calls) == n)

print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
