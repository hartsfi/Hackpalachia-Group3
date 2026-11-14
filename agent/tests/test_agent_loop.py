"""Checks the agent loop's plumbing with a scripted stand-in for the model.

This does not test how well the real model converses. It tests that tool calls are
executed, results are sent back, history is stored and reloaded, and failures roll back.

Run from the repo root:  python -m tests.test_agent_loop
"""
import json
import os
import tempfile
from types import SimpleNamespace

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import anthropic  # noqa: E402
from anthropic.types import TextBlock, ToolUseBlock  # noqa: E402

from app import agent, db  # noqa: E402

PHONE = "+17405550100"


def text(t):
    return SimpleNamespace(stop_reason="end_turn", content=[TextBlock(type="text", text=t)])


def call(name, tool_input, call_id):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[ToolUseBlock(type="tool_use", id=call_id, name=name, input=tool_input)],
    )


class FakeClient:
    """Plays back a script. A callable step receives the messages sent so far."""

    script = []
    seen = []

    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kwargs):
        FakeClient.seen.append(kwargs)
        json.dumps(kwargs["messages"])  # history must always be plain JSON
        step = FakeClient.script.pop(0)
        return step(kwargs["messages"]) if callable(step) else step


def last_tool_result(messages):
    return json.loads(messages[-1]["content"][0]["content"])


def main():
    db.init(reset=True)
    agent.anthropic.Anthropic = FakeClient

    # Turn 1: the model looks up clinics, then offers options.
    FakeClient.script = [
        call("find_clinics", {"location": "45769"}, "t1"),
        lambda m: text("1) " + last_tool_result(m)["clinics"][0]["name"] + ". Reply 1 to book."),
    ]
    reply = agent.handle_message(PHONE, "bad cough, im in 45769")
    assert reply == "1) Riverbend Clinic. Reply 1 to book.", reply

    # Turn 2: the person replies "1". History from turn 1 must be sent again.
    def book(messages):
        assert messages[0] == {"role": "user", "content": "bad cough, im in 45769"}
        assert messages[-1] == {"role": "user", "content": "1"}
        slot = json.loads(messages[2]["content"][0]["content"])["clinics"][0]["open_slots"][0]["slot_id"]
        return call("book_slot", {"slot_id": slot}, "t2")

    FakeClient.script = [book, lambda m: text("Booked at " + last_tool_result(m)["clinic"] + ".")]
    assert agent.handle_message(PHONE, "1") == "Booked at Riverbend Clinic."
    assert db.query("SELECT COUNT(*) AS n FROM bookings WHERE phone = ? AND status = 'booked'", (PHONE,))[0]["n"] == 1

    # A tool error is passed back to the model flagged as an error.
    def check_error(messages):
        block = messages[-1]["content"][0]
        assert block["is_error"] is True and "unknown_location" in block["content"]
        return text("What town or ZIP are you in?")

    FakeClient.script = [call("find_clinics", {"location": "Mars"}, "t3"), check_error]
    assert agent.handle_message(PHONE, "find me something on mars") == "What town or ZIP are you in?"

    # Haiku runs with thinking off; tools are always sent.
    assert FakeClient.seen[0]["thinking"] == {"type": "disabled"}
    assert len(FakeClient.seen[0]["tools"]) == 6

    # An over-long model reply is cut to two SMS segments.
    FakeClient.script = [text("Too long. " * 100)]
    assert len(agent.handle_message(PHONE, "tell me everything")) <= 320

    # A model failure gives the fallback and does not leave a half turn in history.
    before = len(db.get_conversation(PHONE)["messages"])

    def boom(_):
        raise anthropic.APIConnectionError(request=None)

    FakeClient.script = [boom]
    assert agent.handle_message(PHONE, "hello") == agent.FALLBACK_REPLY
    assert len(db.get_conversation(PHONE)["messages"]) == before

    # A model stuck calling tools forever is stopped.
    FakeClient.script = [call("get_my_booking", {}, f"loop{i}") for i in range(10)]
    assert agent.handle_message(PHONE, "loop") == agent.FALLBACK_REPLY

    kinds = [e["kind"] for e in db.get_events(PHONE)]
    assert kinds.count("tool_call") == kinds.count("tool_result") >= 3
    assert "error" in kinds
    print("all agent-loop checks passed")


if __name__ == "__main__":
    main()
