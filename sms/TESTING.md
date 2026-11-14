# Testing the SMS line

Three levels. Start with level 1: it needs no accounts and sends no real texts.

## Level 1: laptop, phone simulator (no Twilio)

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
cd sms
cp .env.example .env        # first time only
uv run uvicorn app.main:app --port 8790
```

Open http://127.0.0.1:8790/simulator (patient phone on the left, health worker phone on the right)
and http://127.0.0.1:8790/dashboard in another tab. Restart the server after pulling new code.
To start over, stop the server and delete `sms/textbridge.db`.

### Booking a visit (user story 1)

| # | Phone | Type | You should see |
|---|-------|------|----------------|
| 1 | Patient | `hi` | "What do you need care for, and what is your ZIP code or town?" |
| 2 | Patient | `Columbus` | "Sorry, I could not find that place…" (asks again) |
| 3 | Patient | `need a checkup, 45764` | "Got it: Nelsonville, OH 45764. Reply 1 if low income…" |
| 4 | Patient | `1` | Up to 3 numbered visits: name, miles, next time. Mobile health unit first. |
| 5 | Patient | `1` | "Booked: Nelsonville Mobile, Tue 11/17 10AM, Library parking lot… Call 740-555-0110…". Dashboard shows "booked". |
| 6 | Patient | `RESCHEDULE`, then `2` | New times, then "Moved: …". |
| 7 | Patient | `CANCEL VISIT` | "Your visit … is cancelled." |
| 8 | Patient (new number, e.g. `+17405552222`) | `checkup in Nelsonville`, `2` | Options for "other insurance": clinics and hospitals instead of the mobile unit. |

**Slot taken:** use two patient numbers. Get both to the options list (steps 3–4) before either
books, then book `1` on the first and `1` on the second. The second gets "Sorry, that time was
just taken" and the next times.

**Reminder:** reminders go out 24 hours before a visit, checked every minute. To see one now:
`curl -X POST "http://127.0.0.1:8790/dev/reminders?hours_ahead=400"` and the patient phone gets
"Reminder: …".

**GPS from a smartphone:** paste `https://maps.apple.com/?ll=39.4612,-82.2290` as the patient.
It is read as Nelsonville. Basic phones type a ZIP or town.

### Safety and opt-out

| Phone | Type | You should see |
|-------|------|----------------|
| Patient (new number) | `I can't breathe` | "call 911", no booking question. Case is EMERGENCY at the top of the dashboard. |
| Patient (new number) | `I want to end my life` | 988 crisis line |
| Patient, in the middle of booking | `my dad has chest pain` | "call 911", booking stops |
| Patient | `STOP` | No reply from us (Twilio sends its own). Nothing more reaches that phone, even worker replies and reminders. `START` turns it back on. |
| Patient (new number) | `hola, necesito una cita en Athens` | Replies in Spanish |

### Health worker side

| Phone | Type | You should see |
|-------|------|----------------|
| Worker | `#1 I can pick you up at 2pm` | Arrives on patient #1's phone from the clinic number |
| Worker | `LIST` | Open cases, with "booked" where a visit is booked |
| Worker | `#1 close` | Case closed |
| Worker | `hello` | Hint about the `#case message` format |
| Dashboard | Open a case, type, Send | Arrives on the patient's phone |

### Automatic check of every acceptance criterion

```bash
cd sms
rm -f /tmp/tb-check.db; DB_PATH=/tmp/tb-check.db uv run uvicorn app.main:app --port 8794
# in a second terminal:
python3 tools/check_story.py http://127.0.0.1:8794
```

It prints PASS/FAIL per criterion (31 checks) and ends with ALL PASS. It also checks every patient
text is at most 160 characters, one SMS segment, with no links.

Without `ANTHROPIC_API_KEY`, needs are sorted by keyword rules. Put a key in `.env` to have Claude
sort and summarise messages instead.

## Level 2: real phones with a Twilio trial (free, ~15 minutes)

**Only one phone?** Your phone is the patient; the health worker stays on the laptop. Leave
`CHW_NUMBERS=+17405550199` (a fictional 555-01xx number: those never go to Twilio) and add
`DEV_SIMULATOR=1` to `.env`. Worker texts then appear on the simulator's worker phone, and you can
answer there (`#1 …`) or from the dashboard. Every reply reaches your real phone over Twilio.
Turn `DEV_SIMULATOR` off again before a public demo: while it is on, anyone who finds the address
can post fake texts to `/dev/text`.

**Two phones?** Put the second phone's number in `CHW_NUMBERS` and it gets the worker texts for real.

1. Sign up at twilio.com and get a trial phone number (pick a 740 area code if offered).
2. Verify every phone that will take part. Trial accounts can only text verified numbers.
3. In `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `CLINIC_NUMBER` (the Twilio number),
   `CHW_NUMBERS` (the phone playing the health worker, or leave the fictional one, see above).
4. Public address: `ngrok http 8790`, then set `PUBLIC_BASE_URL` to the ngrok https URL.
5. Restart the server. Twilio console → Phone Numbers → your number → Messaging: "A message comes
   in" → Webhook, `POST https://<ngrok-url>/sms/inbound`.
6. Text the Twilio number from a verified phone and repeat the tables above. Two phones: one is the
   patient, the one in `CHW_NUMBERS` is the health worker. The simulator is off once Twilio keys
   are set; the dashboard still works.

Trial texts start with "Sent from your Twilio trial account", which uses up part of the 160
characters. A bare `CANCEL` is a Twilio opt-out keyword: use `CANCEL VISIT`.

## Level 3: always on for the demo

Run the same server on a host with https (a VPS behind nginx, Render, Railway, Fly.io), set
`DASHBOARD_PASSWORD`, and point the Twilio webhook at it. Use made-up patient details on stage.
