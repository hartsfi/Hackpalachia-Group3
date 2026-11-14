# Testing the SMS line

There are three levels. Start with level 1; it needs no accounts and sends no real texts.

## Level 1: on your laptop with the phone simulator (no Twilio, ~2 minutes)

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
cd sms
cp .env.example .env
uv run uvicorn app.main:app --port 8790
```

Open two tabs:
- http://127.0.0.1:8790/simulator : a patient phone and a health worker phone side by side
- http://127.0.0.1:8790/dashboard : what the health worker sees on a computer

Try these in order:

| # | Where | Type | You should see |
|---|-------|------|----------------|
| 1 | Patient phone | `I ran out of my insulin and have no ride` | Patient gets "Thanks, we got your message…". Worker phone gets `[#1 HIGH …]` with the message. Case #1 appears on the dashboard. |
| 2 | Worker phone | `#1 I can pick you up at 2pm` | The patient phone receives "I can pick you up at 2pm" from the clinic number. |
| 3 | Patient phone | `thank you` | Worker phone gets `[#1] ...1234: thank you`. |
| 4 | Dashboard | Open case #1, type a reply, Send | It appears on the patient phone. |
| 5 | Patient phone, change the number to `+17405559876` | `I have chest pain` | Patient is told to call 911. Case shows EMERGENCY at the top of the dashboard. |
| 6 | Patient phone, another new number | `hola, necesito una cita` | Reply comes back in Spanish. |
| 7 | Worker phone | `LIST`, then `#1 close` | List of open cases, then case #1 closes. |
| 8 | Worker phone | `hello` (no case number) | A hint explaining the `#case message` format. |

To start over, stop the server and delete `sms/textbridge.db`.

Without `ANTHROPIC_API_KEY` the sorting uses keyword rules (e.g. "insulin + no ride" lands under
transportation). Put a key in `.env` to have Claude sort and summarise messages instead.

## Level 2: real phones with a Twilio trial (free, ~15 minutes)

1. Sign up at twilio.com, get a trial phone number (pick a 740 area code if offered).
2. In the console, verify every phone that will take part (trial accounts can only text verified numbers).
3. Fill in `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `CLINIC_NUMBER` (the Twilio number),
   `CHW_NUMBERS` (the phone playing the health worker).
4. Give the server a public address: `ngrok http 8790`, then set `PUBLIC_BASE_URL` to the ngrok https URL.
5. Restart the server. In Twilio → Phone Numbers → your number → Messaging, set
   "A message comes in" to Webhook, `POST https://<ngrok-url>/sms/inbound`.
6. Text the Twilio number from a verified phone and repeat the table above with real phones.
   The simulator is switched off once Twilio keys are set; the dashboard still works.

Trial texts start with "Sent from your Twilio trial account".

## Level 3: always-on for the demo

Run the same server on a host with https (a VPS behind nginx, Render, Railway, Fly.io), set
`DASHBOARD_PASSWORD`, and point the Twilio webhook at it. Use made-up patient details on stage.
