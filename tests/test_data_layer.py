"""Checks for everything that does not need the model: tools, safety checks, keywords.

Run from the repo root:  python -m tests.test_data_layer
"""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from app import agent, db, tools  # noqa: E402

PHONE = "+17405550100"


def main():
    db.init(reset=True)

    # Location lookup: ZIP inside free text, town name, unknown place.
    assert tools.resolve_place("im in 45769")["town"] == "Pomeroy"
    assert tools.resolve_place("near athens")["zip"] == "45701"
    assert tools.resolve_place("somewhere") is None
    assert tools.find_clinics(PHONE, "Columbus")["error"] == "unknown_location"

    # Nearest clinic first, with open slots.
    found = tools.find_clinics(PHONE, "45769")
    assert found["clinics"][0]["name"] == "Riverbend Clinic", found
    assert len(found["clinics"]) == 3
    slot = found["clinics"][0]["open_slots"][0]["slot_id"]

    # Booking, then the same slot again is refused with alternatives.
    booked = tools.book_slot(PHONE, slot)
    assert booked["booked"] and booked["clinic"] == "Riverbend Clinic", booked
    again = tools.book_slot("+17405550199", slot)
    assert again["error"] == "slot_taken" and again["alternatives"], again

    # Lookup, reminder, cancel (which reopens the slot).
    assert tools.get_my_booking(PHONE)["booking"]["clinic"] == "Riverbend Clinic"
    assert tools.reminder_text(PHONE).startswith("Reminder: your visit at Riverbend Clinic")
    assert tools.cancel_booking(PHONE)["cancelled"]
    assert tools.get_my_booking(PHONE)["booking"] is None
    assert tools.book_slot("+17405550199", slot)["booked"]
    assert tools.cancel_booking(PHONE)["error"] == "no_booking"

    # Bad tool input never raises.
    assert tools.run_tool(PHONE, "book_slot", {"wrong": 1})["error"] == "bad_arguments"
    assert tools.run_tool(PHONE, "nope", {})["error"] == "unknown_tool"
    assert tools.handoff_to_staff(PHONE, "asked for a person")["flagged"]

    # Emergency language is caught in code; ordinary messages are not.
    for text in ["I have chest pain", "cant breathe", "i want to die", "my dad passed out", "I think I overdosed"]:
        assert agent.is_emergency(text), text
    for text in ["bad cough for 3 weeks", "need a checkup in 45701", "1", "sore knee"]:
        assert not agent.is_emergency(text), text

    # Emergency and keyword paths reply without calling the model.
    assert agent.handle_message(PHONE, "I have chest pain") == agent.EMERGENCY_REPLY
    assert len(agent.EMERGENCY_REPLY) <= 160
    assert agent.handle_message(PHONE, "STOP") == agent.STOP_REPLY
    assert agent.handle_message(PHONE, "hello?") is None  # opted out: nothing is sent
    assert agent.handle_message(PHONE, "start") == agent.START_REPLY

    # Reply clamping keeps texts within two SMS segments.
    long = "This is a sentence. " * 40
    assert len(agent.clamp_sms(long)) <= 320
    assert agent.clamp_sms("  short   reply ") == "short reply"

    # Every step above left an audit trail.
    kinds = {e["kind"] for e in db.get_events(PHONE)}
    assert {"inbound", "outbound", "suppressed"} <= kinds, kinds

    print("all data-layer checks passed")


if __name__ == "__main__":
    main()
