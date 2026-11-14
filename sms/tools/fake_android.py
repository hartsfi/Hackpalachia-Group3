"""Stand-in for the SMS Gateway for Android cloud API, for testing without a phone.

    uv run uvicorn tools.fake_android:app --port 8798
    # .env: ANDROID_GATEWAY_URL=http://127.0.0.1:8798/3rdparty/v1  ANDROID_GATEWAY_USER=u  ANDROID_GATEWAY_PASSWORD=p

POST /_incoming {"sender": "+1...", "message": "..."} delivers a signed sms:received webhook,
GET /_sent lists texts the "phone" sent.
"""
import hashlib
import hmac
import json
import time
import uuid

import requests
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
KEY = "fake-signing-key"
S = {"hooks": [], "sent": []}


def _auth(request: Request):
    if not request.headers.get("authorization", "").startswith("Basic "):
        raise HTTPException(401)


@app.post("/3rdparty/v1/message")
async def message(request: Request):
    _auth(request)
    body = await request.json()
    mid = uuid.uuid4().hex[:12]
    S["sent"].append({"id": mid, "to": body["phoneNumbers"], "text": body["textMessage"]["text"]})
    return {"id": mid, "state": "Pending"}


@app.get("/3rdparty/v1/webhooks")
def list_hooks(request: Request):
    _auth(request)
    return S["hooks"]


@app.post("/3rdparty/v1/webhooks")
async def add_hook(request: Request):
    _auth(request)
    h = {**(await request.json()), "id": uuid.uuid4().hex[:8]}
    S["hooks"].append(h)
    return h


@app.delete("/3rdparty/v1/webhooks/{hid}")
def del_hook(hid: str, request: Request):
    _auth(request)
    S["hooks"] = [h for h in S["hooks"] if h["id"] != hid]
    return {}


@app.post("/_incoming")
def incoming(m: dict):  # plain def: runs in a thread, so the app can call /message meanwhile
    sign = m.pop("sign", True)
    out = []
    for h in S["hooks"]:
        body = json.dumps({"deviceId": "dev1", "event": "sms:received", "id": uuid.uuid4().hex, "webhookId": h["id"],
                           "payload": {"messageId": uuid.uuid4().hex, "message": m["message"], "sender": m["sender"],
                                       "recipient": "+17405550177", "simNumber": 1,
                                       "receivedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}}).encode()
        ts = str(int(time.time()))
        sig = hmac.new(KEY.encode(), body + ts.encode(), hashlib.sha256).hexdigest() if sign else "bad"
        r = requests.post(h["url"], data=body, timeout=20,
                          headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig})
        out.append(r.status_code)
    return {"delivered": out}


@app.get("/_sent")
def sent():
    return S["sent"]
