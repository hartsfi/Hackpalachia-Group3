"""Checks the agent (app/agent.py): a patient's own questions once no booking question is waiting.

By default Claude is replaced by a scripted stand-in, so this needs no key and no network: it checks
the wiring (tools return real data, actions run the real flows, failures never leave silence).

    python -m tools.check_agent           (from the sms/ folder)
    python -m tools.check_agent --live    (the real model; needs ANTHROPIC_API_KEY, ~10 short calls)
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "check.db")
LIVE = "--live" in sys.argv
if not LIVE:
    os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = os.environ["ATHENA_CLIENT_ID"] = ""  # dev mode, mock calendar
os.environ.setdefault("LLM_PROVIDER", "rules")

from app import agent, config, db, relay  # noqa: E402

db.init()
ok = True
WORKER = config.CHW_NUMBERS[0]


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def text(phone, body):
    before = len(db.phone_messages(phone))
    relay.handle_incoming(phone, body)
    new = [m["body"] for m in db.phone_messages(phone)[before:] if m["to_phone"] == phone]
    for m in new:
        print("      > " + m.replace("\n", " / "))
    return new


def worker_saw(body):
    return any(body in m["body"] for m in db.phone_messages(WORKER) if m["to_phone"] == WORKER)


# ---- scripted stand-in for Claude: per message, a list of steps (tool calls, then a reply) ----
SCRIPT, calls = {}, []


def fake_create(system, messages):
    msg = messages[0]["content"].split("<<<", 1)[1].rsplit(">>>", 1)[0]
    step = sum(1 for m in messages if m["role"] == "assistant")
    plan = SCRIPT.get(msg, [])
    calls.append((msg, step))
    if step >= len(plan):
        return NS(content=[NS(type="text", text="")])
    kind, a, b = plan[step]
    if kind == "tool":
        return NS(content=[NS(type="tool_use", id=f"t{len(calls)}", name=a, input=b)])
    last = messages[-1]["content"][0]["content"] if step else ""  # the previous tool's result
    return NS(content=[NS(type="text", text=a(json.loads(last)) if callable(a) else a)])


if not LIVE:
    agent._create = fake_create
    config.AGENT_ENABLED = True
    SCRIPT.update({
        "What's the address you booked?": [("tool", "visit_details", {}),
                                           ("text", lambda v: f"Your visit: {v['short']}, {v['address']}, "
                                                              f"{v['town']}, {v['when']}.", None)],
        "where do i get the bus thing, who drives me": [("tool", "ride_status", {}),
                                                        ("text", "A health worker is booking your ride and will "
                                                                 "text you the pickup time.", None)],
        "is there a pharmacy near me": [("tool", "nearest_pharmacies", {}),
                                        ("text", lambda p: f"Nearest: {p[0]['name'][:40]}, {p[0]['phone']}.", None)],
        "actually can we push it to later in the week": [("tool", "reschedule_visit", {})],
        "thank you so much!": [("text", "You're welcome, Pat! Text us any time.", None)],
        "my tooth is killing me now too": [("tool", "start_booking", {"need": "dental", "summary": "toothache"})],
        "can someone call me about my bill": [("tool", "health_worker", {"reason": "billing question"})],
        "why do you need to know that?": [("text", "So we can show free and low-cost clinics first.", None)],
    })

print(f"== {'LIVE model ' + config.AGENT_MODEL if LIVE else 'scripted stand-in'}; agent enabled: {agent.enabled()}")
if LIVE and not agent.enabled():
    sys.exit("Set ANTHROPIC_API_KEY (and leave AGENT unset) to run --live.")

P = "+17405553001"
text(P, "need a checkup, 45764")
text(P, "Pat Lee")
text(P, "2")
r = text(P, "1")
check("set-up: visit booked", any(x.startswith("Booked:") for x in r))
text(P, "RIDE")
text(P, "1")
r = text(P, "12 Main St, Nelsonville")
check("set-up: ride requested", r and "health worker will book your ride" in r[-1])
visit = db.active_booking(P)

print("== The screenshot: a question after the ride request gets an answer")
r = text(P, "What's the address you booked?")
fields = relay._visit_fields(visit["service_id"], visit["start_ts"])
check("answered (not silence)", bool(r))
check("with the visit's real address", r and fields["address"] in r[-1], r[-1] if r else "")

print("== Questions answered from tools")
if not LIVE:
    r = text(P, "where do i get the bus thing, who drives me")
    check("ride question answered", r and "ride" in r[-1].lower())
r = text(P, "is there a pharmacy near me")
check("pharmacy question answered", r and any(ch.isdigit() for ch in r[-1]), r[-1] if r else "")
r = text(P, "thank you so much!")
check("small talk gets a short reply", r and len(r[-1]) <= 160)

print("== Actions run the real flows")
r = text(P, "actually can we push it to later in the week")
check("reschedule offers new times", r and "1)" in r[-1], r[-1] if r else "")
text(P, "0")  # keep the current visit
r = text(P, "can someone call me about my bill")
check("hand-off: patient told", r and "health worker" in r[-1].lower())
check("hand-off: worker told", worker_saw("can someone call me about my bill"))

print("== Every agent text is one plain SMS")
GSM7 = agent.GSM7
mine = [m["body"] for m in db.phone_messages(P) if m["to_phone"] == P]
check("all <= 160 characters", all(len(b) <= 160 for b in mine), [b for b in mine if len(b) > 160][:1])
check("all GSM-7", all(not set(b) - GSM7 for b in mine))

if not LIVE:
    print("== A new need starts booking")
    Q = "+17405553002"
    text(Q, "need a checkup, 45764"); text(Q, "Sam Ray"); text(Q, "2"); text(Q, "1")
    r = text(Q, "my tooth is killing me now too")
    check("asks the booking question for the new need", r and ("1 if low income" in r[-1] or "1)" in r[-1]), r[-1] if r else "")

    print("== A question back while a booking question waits: answered, then asked again")
    S = "+17405553004"
    text(S, "need a checkup, 45764"); text(S, "Kim Day")
    r = text(S, "why do you need to know that?")
    check("answers the question", r and "low-cost" in r[0], r[0] if r else "")
    check("then repeats the income question", r and "Reply 1 if low income" in r[-1])
    check("still waiting for the income answer", db.open_case_for(S)["stage"] == "ask_income")
    r = text(S, "2")
    check("the plain answer still works", r and "1)" in r[-1])
    n = len(calls)
    text(S, "1")
    check("a plain pick never reaches the agent", len(calls) == n)

    print("== Model failure: a person takes over, the patient is told")
    agent._create = lambda *a: (_ for _ in ()).throw(RuntimeError("down"))
    r = text(P, "what time do they open")
    check("patient told a health worker will text", r and "health worker will text you" in r[-1])
    check("worker gets the question", worker_saw("what time do they open"))

    print("== Health worker mid-conversation: replies go straight to them, no agent")
    agent._create = fake_create
    relay.handle_incoming(WORKER, f"#{db.open_case_for(P)['id']} Hi Pat, this is Jo. Is Tuesday OK?")
    n = len(calls)
    r = text(P, "yes tuesday works")
    check("no agent call", len(calls) == n)
    check("no automatic reply", not r)
    check("worker gets it", worker_saw("yes tuesday works"))

    print("== Switched off: hand-off with an acknowledgement, as a fallback")
    config.AGENT_ENABLED = False
    R = "+17405553003"
    text(R, "need a checkup, 45764"); text(R, "Al Fox"); text(R, "2"); text(R, "1")
    r = text(R, "what should i bring")
    check("patient told a health worker will text", r and "health worker will text you" in r[-1])

print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
