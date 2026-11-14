"""Connect SMS Gateway for Android to this server.

    uv run python tools/android_setup.py                 # register the incoming-text webhook
    uv run python tools/android_setup.py list            # show registered webhooks
    uv run python tools/android_setup.py test +1740...   # send a test text from the Android phone

Reads ANDROID_GATEWAY_USER / ANDROID_GATEWAY_PASSWORD / PUBLIC_BASE_URL from .env.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402

if not config.ANDROID_ENABLED:
    sys.exit("Set ANDROID_GATEWAY_USER and ANDROID_GATEWAY_PASSWORD in sms/.env first.")
API, AUTH = config.ANDROID_GATEWAY_URL, (config.ANDROID_GATEWAY_USER, config.ANDROID_GATEWAY_PASSWORD)
cmd = sys.argv[1] if len(sys.argv) > 1 else "register"


def hooks():
    r = requests.get(f"{API}/webhooks", auth=AUTH, timeout=20)
    r.raise_for_status()
    return r.json()


if cmd == "list":
    for h in hooks():
        print(h.get("id"), h.get("event"), h.get("url"))
elif cmd == "test":
    to = sys.argv[2]
    r = requests.post(f"{API}/message", auth=AUTH, timeout=20,
                      json={"textMessage": {"text": "TextBridge test: the Android gateway works."}, "phoneNumbers": [to]})
    print(r.status_code, r.text[:300])
else:
    if not config.PUBLIC_BASE_URL:
        sys.exit("Set PUBLIC_BASE_URL in .env (the https tunnel address) first.")
    url = config.PUBLIC_BASE_URL + "/sms/android"
    for h in hooks():  # one webhook only: drop old ones (e.g. an old tunnel address)
        if h.get("event") == "sms:received" and h.get("url") != url:
            requests.delete(f"{API}/webhooks/{h['id']}", auth=AUTH, timeout=20)
            print("removed old webhook", h.get("url"))
    if not any(h.get("url") == url and h.get("event") == "sms:received" for h in hooks()):
        r = requests.post(f"{API}/webhooks", auth=AUTH, timeout=20, json={"url": url, "event": "sms:received"})
        r.raise_for_status()
    print("incoming texts now go to", url)
