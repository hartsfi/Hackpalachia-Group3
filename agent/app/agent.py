"""Turns one inbound text into one reply.

Order of checks, before any model call:
  1. STOP / START keywords
  2. emergency language -> fixed 911 / 988 reply, written in code
Only then does the model run, in a loop, calling tools until it has a reply.
"""
import json
import os
import re
from datetime import datetime

import anthropic

from . import db, tools

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-5-5")
MAX_TOOL_ROUNDS = 6
MAX_HISTORY = 40
SMS_HARD_LIMIT = 320

EMERGENCY_REPLY = (
    "This may be an emergency. Call 911 now. For thoughts of suicide or self-harm, "
    "call or text 988. I can't help with emergencies by text."
)
FALLBACK_REPLY = "Sorry, we're having trouble right now. Please try again in a few minutes or call your clinic."
STOP_REPLY = "You're unsubscribed and will get no more texts. Reply START to resume."
START_REPLY = "You're resubscribed. Text what you need care for and your ZIP code to book a visit."

EMERGENCY_PATTERNS = re.compile(
    r"chest pain|chest hurts|heart attack|can'?t breathe|cant breathe|cannot breathe|not breathing|"
    r"trouble breathing|hard to breathe|overdos|suicid|kill myself|end my life|want to die|"
    r"stroke|unconscious|passed out|won'?t wake|severe bleeding|bleeding (badly|a lot|heavily)|"
    r"choking|seizure",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are the text-message assistant for a rural healthcare booking line in Southeast Ohio. \
People text this number from any kind of phone, often with poor signal, to get a clinic visit booked. \
Many are not comfortable with technology, so every reply should read like a short, plain text from a helpful front-desk person.

What you do: find nearby clinics, offer appointment times, book, cancel and reschedule. Nothing else.

How to reply:
- Plain text only. No links, emojis, markdown or lists with symbols. Aim for under 160 characters; never go over 320.
- Ask for one thing at a time. To offer times you need two things: what they need care for and where they are (ZIP code or town). If they gave both, go straight to finding clinics.
- When offering appointments, give at most 3 numbered options, each with clinic name, miles away and time, then ask them to reply with a number.
- When someone picks an option, book it and confirm with the day, time, clinic and street address.

Facts come from tools, never from memory. Only mention a clinic, distance, time or booking if a tool returned it in this conversation. \
If a tool reports an error, tell the person plainly and offer the next step.

Limits:
- You cannot give medical advice, diagnose, or suggest treatment or medicine. If asked, say so in one sentence and offer to book a visit.
- If the person asks for a human, or you have not understood them twice in a row, use handoff_to_staff and tell them someone will call back.
- If anything sounds like an emergency, tell them to call 911.

Today is {today}."""


def is_emergency(text: str) -> bool:
    return bool(EMERGENCY_PATTERNS.search(text))


def clamp_sms(text: str) -> str:
    """Flatten whitespace and keep a reply within two SMS segments."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
    if len(text) <= SMS_HARD_LIMIT:
        return text
    cut = text[:SMS_HARD_LIMIT]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[: end + 1] if end > 80 else cut[: SMS_HARD_LIMIT - 1].rstrip() + "…"


def _trim(messages: list) -> list:
    """Drop the oldest turns, always starting the kept history on a plain user text."""
    while len(messages) > MAX_HISTORY:
        for i in range(1, len(messages)):
            if messages[i]["role"] == "user" and isinstance(messages[i]["content"], str):
                messages = messages[i:]
                break
        else:
            break
    return messages


def _run_model(phone: str, messages: list) -> str:
    client = anthropic.Anthropic()
    system = SYSTEM_PROMPT.format(today=datetime.now().strftime("%A, %B %d, %Y, %I:%M %p"))
    extra = {}
    if MODEL.startswith("claude-haiku"):
        # Short replies and simple tool use: skip thinking so texts come back fast.
        extra["thinking"] = {"type": "disabled"}

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL, max_tokens=2000, system=system, tools=tools.TOOL_DEFS, messages=messages, **extra
        )
        if response.stop_reason == "refusal":
            db.log_event(phone, "error", {"where": "model", "error": "refusal"})
            return FALLBACK_REPLY

        messages.append({
            "role": "assistant",
            "content": [b.model_dump(mode="json", exclude_unset=True) for b in response.content],
        })
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            text = " ".join(b.text for b in response.content if b.type == "text").strip()
            return text or FALLBACK_REPLY

        results = []
        for call in tool_uses:
            db.log_event(phone, "tool_call", {"name": call.name, "input": call.input})
            result = tools.run_tool(phone, call.name, dict(call.input))
            db.log_event(phone, "tool_result", {"name": call.name, "result": result})
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(result),
                "is_error": "error" in result,
            })
        messages.append({"role": "user", "content": results})

    db.log_event(phone, "error", {"where": "agent", "error": "too many tool rounds"})
    return FALLBACK_REPLY


def handle_message(phone: str, text: str) -> str | None:
    """Process one inbound SMS. Returns the reply, or None if nothing should be sent."""
    text = text.strip()
    db.log_event(phone, "inbound", {"text": text})
    convo = db.get_conversation(phone)
    keyword = text.upper().strip(" .!")

    if keyword in {"STOP", "UNSUBSCRIBE", "END", "QUIT"}:
        db.set_opted_out(phone, True)
        return _send(phone, STOP_REPLY, kind="keyword")
    if keyword in {"START", "UNSTOP"}:
        db.set_opted_out(phone, False)
        return _send(phone, START_REPLY, kind="keyword")
    if convo["opted_out"]:
        db.log_event(phone, "suppressed", {"reason": "opted out"})
        return None

    messages = convo["messages"]

    if is_emergency(text):
        # Fixed reply from code: the safety path never depends on the model.
        messages += [{"role": "user", "content": text}, {"role": "assistant", "content": EMERGENCY_REPLY}]
        db.save_messages(phone, _trim(messages))
        return _send(phone, EMERGENCY_REPLY, kind="emergency")

    saved = len(messages)
    messages.append({"role": "user", "content": text})
    try:
        reply = clamp_sms(_run_model(phone, messages))
    except anthropic.AuthenticationError:
        reply = _fail(phone, messages, saved, "No valid Anthropic credentials. Set ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        reply = _fail(phone, messages, saved, "Rate limited by the model API.")
    except anthropic.APIStatusError as exc:
        reply = _fail(phone, messages, saved, f"Model API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        reply = _fail(phone, messages, saved, "Could not reach the model API.")
    except anthropic.AnthropicError as exc:
        reply = _fail(phone, messages, saved, str(exc))
    except Exception as exc:  # a text line must always answer; e.g. the SDK raises TypeError with no credentials
        reply = _fail(phone, messages, saved, f"{type(exc).__name__}: {exc}")
    db.save_messages(phone, _trim(messages))
    return _send(phone, reply)


def _fail(phone: str, messages: list, saved: int, error: str) -> str:
    """Record the failure and roll the history back so a half-finished turn is not stored."""
    del messages[saved:]
    db.log_event(phone, "error", {"where": "model", "error": error})
    return FALLBACK_REPLY


def _send(phone: str, reply: str, kind: str = "agent") -> str:
    db.log_event(phone, "outbound", {"text": reply, "via": kind, "chars": len(reply)})
    return reply
