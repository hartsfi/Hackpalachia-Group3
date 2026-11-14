"""Outbound texts. Goes through Twilio, an Android phone or this Mac's Messages app when set up, otherwise only
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


def _android_send(to: str, body: str) -> str:
    """Send through SMS Gateway for Android: the phone texts `to` from its own SIM."""
    import requests

    r = requests.post(f"{config.ANDROID_GATEWAY_URL}/message", timeout=20,
                      auth=(config.ANDROID_GATEWAY_USER, config.ANDROID_GATEWAY_PASSWORD),
                      json={"textMessage": {"text": body}, "phoneNumbers": [to]})
    if r.status_code >= 400:
        raise RuntimeError(f"gateway {r.status_code}: {r.text[:200]}")
    return str(r.json().get("id", ""))


def normalize(phone: str) -> str:
    """'(740) 555-1234' / '7405551234' / '17405551234' -> '+17405551234'."""
    phone = (phone or "").strip()
    digits = re.sub(r"\D", "", phone)
    if phone.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return phone


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
    if config.SMS_ENABLED and not is_fictional(to):
        try:
            if config.SMS_PROVIDER == "mac":
                from . import mac_messages

                sid, channel = mac_messages.send(to, body), "mac"
            elif config.SMS_PROVIDER == "android":
                sid, channel = _android_send(to, body), "android"
            else:
                sid, channel = _twilio().messages.create(to=to, from_=config.CLINIC_NUMBER, body=body).sid, "twilio"
        except Exception as e:
            log.error("%s send to %s failed: %s", config.SMS_PROVIDER, to, e)
            channel = "failed"
    else:
        log.info("[dev] -> %s: %s", to, body)
    db.add_message(case_id, sender, config.CLINIC_NUMBER, to, body, channel, sid)
