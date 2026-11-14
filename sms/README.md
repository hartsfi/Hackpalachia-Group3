# TextBridge

A text line that connects rural patients with community health workers. It works on any phone,
flip phones included, so patients don't need home internet.

```
Patient (any carrier) ──text──▶ clinic number ──▶ server ──▶ health worker phones + dashboard
Health worker texts "#12 msg" ──▶ server ──▶ patient, sent from the clinic number
```

Neither side ever sees the other's personal number.

Patients can also **book a visit entirely by text** (user story 1 in
[`docs/USER_STORIES.md`](../docs/USER_STORIES.md)): ZIP or town, one income question, up to three
nearby visits with distance and next time, booking in a mock clinic calendar, `CANCEL VISIT` /
`RESCHEDULE`, and a reminder 24 hours before. Low income is offered the mobile health unit and free
or sliding-scale clinics first. Clinics and towns live in `app/data/` (sample data for now).
Clinics can be connected to **athenahealth** (athenaOne): their open times come live from
athenahealth and visits are booked, moved and cancelled there (see "athenahealth booking" in
[TESTING.md](TESTING.md)). The others use the mock calendar.

**Accounts.** Each phone number gets one account in the `patients` table, stored in the server's
SQLite database (`textbridge.db`, kept across restarts). A new number is asked for a first and last
name (or SKIP) and gets a TextBridge ID like `TB-1001`; a returning number is welcomed back by name.
`MY ID` texts the ID back. The account remembers town, ZIP and insurance answer, and the dashboard
shows the name and ID on each case. Emergencies are answered before any sign-up question.

**LLM brain.** Free-text messages are read by a local model through Ollama (default
`llama3.1:8b`, set `LLM_PROVIDER` / `OLLAMA_MODEL` in `.env`), so patient texts never leave the
machine. It picks out the need, urgency, place (typos included) and a one-line summary for the
health worker, in about a second. Claude (`LLM_PROVIDER=anthropic`) or keyword rules (`rules`) are
alternatives; if the model is down, the rules answer. Emergency words always trigger 911/988, and
the model can raise urgency but never lower it. Booking, clinics and times never come from the model.

Each new text is triaged by need (rides, medicine, appointments, food, housing, mental health, insurance, utilities) and urgency. Claude does this when
`ANTHROPIC_API_KEY` is set, keyword rules otherwise. Emergency and crisis words always get an
immediate 911 / 988 reply, in English or Spanish.

## Run locally (no Twilio needed)

Full test steps, from the simulator to real phones: [TESTING.md](TESTING.md).

```bash
cp .env.example .env
uv run uvicorn app.main:app --reload --port 8790
```

- Phone simulator: http://127.0.0.1:8790/simulator (a patient phone and a health worker phone side by side)
- Dashboard: http://127.0.0.1:8790/dashboard

## Health worker text commands

| Text            | Does                                   |
|-----------------|----------------------------------------|
| `#12 message`   | Sends the message to case 12's patient (first reply claims the case) |
| `#12 close`     | Closes case 12                         |
| `LIST`          | Lists open cases                       |

Patients can text `MY ID`, `CANCEL VISIT`, `RESCHEDULE`, `HELP`, `STOP` and `START`.

## Connect real phones

**Today, no registration:** an Android phone as the clinic number with SMS Gateway for Android, see
"Real SMS today with an Android phone" in [TESTING.md](TESTING.md).

**Twilio** (toll-free or 10DLC number, needs carrier registration, takes days):


1. Buy a number with SMS in the Twilio console (a 740 area code is local to Southeast Ohio).
2. Fill in `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `CLINIC_NUMBER`, `CHW_NUMBERS` and
   `PUBLIC_BASE_URL` in `.env`, and set `DASHBOARD_PASSWORD`.
3. Under the number's Messaging settings, set "A message comes in" to a webhook:
   `POST {PUBLIC_BASE_URL}/sms/inbound`.
4. For US numbers, register for A2P 10DLC (trial accounts can only text verified numbers).

For a quick public URL while testing: `ngrok http 8790`.

## Health data

Use made-up data in demos. Real patient texts need a Twilio BAA (HIPAA) or a self-hosted model
for triage. With athenahealth on, patients text their name and date of birth once; that text is kept
in the message log like every other text, and only the athenahealth patient id is stored separately.
