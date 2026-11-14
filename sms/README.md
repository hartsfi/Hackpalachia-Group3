# CareLine

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
or sliding-scale clinics first. Clinics and towns live in `app/data/`.

**Low on medicine.** When the need is medication ("i am low in pilles", typos included), CareLine
texts the 3 nearest **real** pharmacies with phone numbers (`app/data/pharmacies.json`, 135
community pharmacies within 50 miles of Athens from the CMS NPI Registry; rebuild with
`tools/fetch_pharmacies.py`; Rite Aid left out, as it closed in 2025), suggests asking the pharmacist
for an emergency refill, and offers 1 = a doctor visit for a new prescription, 2 = a health worker
call. The health worker is told. `PHARMACY` or `PHARMACY 45764` lists pharmacies any time. The
registry has no hours, so people are told to call first.

**Returning patients.** Someone with an account is greeted with their next visit, their saved town
is used instead of asking again, and "when is my appointment" / `MY VISIT` replies with date, time,
place and phone. In the simulator, **Existing patient** loads such a patient (Sam Miller,
Nelsonville, one booked visit).

**Real vs sample data.** The mobile clinic route in `app/data/mobile_route.json` is real: the
Heritage Community Clinic stops (Ohio University), copied from
https://www.ohio.edu/medicine/community-health/community-clinic/primary-care on 2026-11-14. CareLine
cannot book those; picking one texts the real number (740-593-2432) and offers a health worker to
call for the patient. Text `MOBILE` for the next stops. The other clinics in `app/data/services.json`
are still made-up samples (names, addresses, 555 phones, hours) for showing the booking flow.
Clinics can be connected to **athenahealth** (athenaOne): their open times come live from
athenahealth and visits are booked, moved and cancelled there (see "athenahealth booking" in
[TESTING.md](TESTING.md)). The others use the mock calendar.

**Accounts.** Each phone number gets one account in the `patients` table, stored in the server's
SQLite database (`textbridge.db`, kept across restarts). A new number is asked for a first and last
name (or SKIP) and gets a CareLine ID like `CL-1001`; a returning number is welcomed back by name.
`MY ID` texts the ID back. The account remembers town, ZIP and insurance answer, and the dashboard
shows the name and ID on each case. Emergencies are answered before any sign-up question.

**LLM brain.** Free-text messages are read by a local model through Ollama (default
`llama3.1:8b`, set `LLM_PROVIDER` / `OLLAMA_MODEL` in `.env`), so patient texts never leave the
machine. It picks out the need, urgency, place (typos included) and a one-line summary for the
health worker, in about a second. Claude (`LLM_PROVIDER=anthropic`) or keyword rules (`rules`) are
alternatives; if the model is down, the rules answer. Emergency words always trigger 911/988, and
the model can raise urgency but never lower it. Booking, clinics and times never come from the model.

With `ANTHROPIC_API_KEY` set, patients can answer the booking questions in their own words: "im on
medicaid" instead of `1`, "the tuesday one" instead of `2`, a misspelled town instead of a ZIP.
Claude only picks which allowed answer was meant ([`app/understand.py`](app/understand.py)); every
reply and every booking still comes from the scripted flow, and an unclear answer just gets the
question asked again. Check it with `python -m tools.check_understand` (no key needed).

Each new text is triaged by need (rides, medicine, appointments, food, housing, mental health, insurance, utilities) and urgency. Claude does this when
`ANTHROPIC_API_KEY` is set, keyword rules otherwise. Emergency and crisis words always get an
immediate 911 / 988 reply, in English or Spanish.

## Run locally (no Twilio needed)

Full test steps, from the simulator to real phones: [TESTING.md](TESTING.md).

```bash
cp .env.example .env
uv run uvicorn app.main:app --reload --port 8790
```

- Phone simulator: http://127.0.0.1:8790/simulator (the patient's phone; add `?worker=1` to show a health worker phone beside it)
- Dashboard: http://127.0.0.1:8790/dashboard

## Health worker text commands

| Text            | Does                                   |
|-----------------|----------------------------------------|
| `#12 message`   | Sends the message to case 12's patient (first reply claims the case) |
| `#12 close`     | Closes case 12                         |
| `LIST`          | Lists open cases                       |

Patients can text `MY VISIT`, `PHARMACY`, `MOBILE`, `MY ID`, `CANCEL VISIT`, `RESCHEDULE`, `HELP`, `STOP` and `START`.

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
