"""Outbound texts. Goes through Twilio when keys are set, otherwise only
recorded in the database so the dev phone simulator can show it."""
import logging
import re

from . import config, db

log = logging.getLogger("sms")
_client = None


def _twilio():
    global _client
    if _client is None:
        from twilio.rest import Client

        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def is_fictional(phone: str) -> bool:
    """555-0100..555-0199 numbers are reserved for fiction and never belong to a real phone.
    They stay in the browser simulator even when Twilio is on, so one real phone can play
    the patient while the health worker is simulated on the laptop."""
    digits = re.sub(r"\D", "", phone)[-10:]
    return len(digits) == 10 and digits[3:6] == "555" and digits[6:8] == "01"


def send(to: str, body: str, case_id: int | None, sender: str) -> None:
    if db.is_opted_out(to):
        log.info("not texting %s: opted out", to)
        return
    sid, channel = None, "dev"
    if config.TWILIO_ENABLED and not is_fictional(to):
        try:
            msg = _twilio().messages.create(to=to, from_=config.CLINIC_NUMBER, body=body)
            sid, channel = msg.sid, "twilio"
        except Exception as e:
            log.error("Twilio send to %s failed: %s", to, e)
            channel = "failed"
    else:
        log.info("[dev] -> %s: %s", to, body)
    db.add_message(case_id, sender, config.CLINIC_NUMBER, to, body, channel, sid)
