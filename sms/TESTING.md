# Testing the SMS line

Three levels. Start with level 1: it needs no accounts and sends no real texts.

## Level 1: laptop, phone simulator (no Twilio)

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
cd sms
cp .env.example .env        # first time only
uv run uvicorn app.main:app --port 8790
```

Open http://127.0.0.1:8790/simulator?worker=1 (patient phone on the left, health worker phone on the right; without `?worker=1` only the patient's phone is shown)
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

## athenahealth booking

Clinics with an `athena` block in `app/data/services.json` (now: Athens Health Ctr →
department 1) take their open times from athenahealth and book there. The first time a patient
books one, they are asked once for name and date of birth, so athenahealth can find or create
their patient record. Everything else stays the same.

**Without athenahealth keys (fake athenahealth on your laptop):**

```bash
cd sms
uv run uvicorn tools.fake_athena:app --port 8796
# second terminal:
ATHENA_CLIENT_ID=x ATHENA_CLIENT_SECRET=x ATHENA_BASE_URL=http://127.0.0.1:8796 \
  uv run uvicorn app.main:app --port 8790
```

In the simulator: `checkup in Athens`, `2`, pick the "Athens Health Ctr" number, then
`Jane Doe 03/14/1980`. You get "Booked: Athens Health Ctr…", and http://127.0.0.1:8796/_state
shows the booking on the athenahealth side. `RESCHEDULE` and `CANCEL VISIT` update it there too.
The fake has several practices and clinics; this section uses only department 1 of the first
practice ("Clinic finder" below uses all of them).
Automatic check (14 checks, incl. a slot taken at the front desk and athenahealth being down):
start the fake on 8796 and the server on 8797 with a fresh `DB_PATH`, then
`python3 tools/check_athena.py`.

**With the real athenahealth Preview sandbox:**

1. Sign up at https://developer.athenahealth.com, create an app with 2-legged (client
   credentials) access, and copy its Preview client id and secret.
2. In `.env`: `ATHENA_CLIENT_ID`, `ATHENA_CLIENT_SECRET`, `ATHENA_PRACTICE_ID=195900` (the
   shared Preview practice), `ATHENA_ENV=preview`. Leave `ATHENA_BASE_URL` blank.
3. `uv run python tools/athena_setup.py` lists the practice's departments, providers and
   appointment types; `uv run python tools/athena_setup.py open 1` lists open slots in
   department 1. Put a department with open slots into the clinic's `athena` block
   (add `"providerid"` / `"appointmenttypeid"` to narrow it).
4. Restart the server and book an Athens Health Ctr visit as above. If a department has no
   open slots in the next two weeks, that clinic simply isn't offered.

The Preview sandbox is shared test data: never use real patient names or birthdays there.

## Clinic finder

After the ZIP and the income question, the finder (`app/finder.py`) picks up to three clinics:
nearest first, only clinics that take the patient's income group, nothing farther than 45 miles,
and a clinic known for the patient's need (for now: mental health) counts as 15 miles closer.
Each option is the clinic's next open time. Where the clinics come from is one setting.

### Sample mode (default)

`CLINIC_SOURCE=sample`, or nothing set: the clinics in `app/data/services.json`, as in level 1.
Nothing to set up.

### Fake athenahealth mode

`CLINIC_SOURCE=athena` reads the clinics from athenahealth: every department of every practice
in `ATHENA_PRACTICE_IDS`, plus `app/data/clinic_overrides.json` for what athenahealth does not
know (short name, income group, needs, which appointment type to book). `tools/fake_athena.py`
plays athenahealth with two made-up clinic organizations and seven bookable clinics.

Automatic check, nothing to start (55 checks, about 10 seconds):

```bash
cd sms
uv run python -m tools.check_finder
```

By hand in the phone simulator:

```bash
cd sms
uv run uvicorn tools.fake_athena:app --port 8796
# second terminal:
rm -f /tmp/careline-finder.db
DB_PATH=/tmp/careline-finder.db CLINIC_SOURCE=athena ATHENA_CLIENT_ID=x ATHENA_CLIENT_SECRET=x \
  ATHENA_BASE_URL=http://127.0.0.1:8796 ATHENA_PRACTICE_IDS=195900,195901 \
  uv run uvicorn app.main:app --port 8790
```

Open http://127.0.0.1:8790/simulator and type as the patient:

| # | Type | You should see |
|---|------|----------------|
| 1 | `need a checkup, 45764` | "Got it: Nelsonville, OH 45764. Reply 1 if low income…" |
| 2 | `1` | `1) Nelsonville Family 0mi …` `2) Glouster Health 8mi …` `3) Athens Health Ctr 11mi …`, each with its next open time |
| 3 | `1` | "To book at Nelsonville Family, reply with your first and last name and date of birth…" (once per clinic organization) |
| 4 | `Jane Doe 03/14/1980` | "Booked: Nelsonville Family, …, 15 Canal St, Nelsonville. Call 740-555-0121…" |
| 5 | `RESCHEDULE`, then `3` | "Pick a new time…", the options again, then "Moved: Athens Health Ctr, …, 40 Elm St, Athens. Call 740-555-0112. Old time cancelled." No second identity question: same organization. |
| 6 | `CANCEL VISIT` | "Your visit … at Athens Health Ctr is cancelled." |

http://127.0.0.1:8796/_state shows the athenahealth side: after step 4 one appointment under
`practices → 195900 → booked`; after step 5 that id is in `cancelled` and a new one is booked
(one reschedule call); after step 6 `booked` is empty.

More to try (use a new patient number each time, e.g. `+17405552001`):

- `I feel anxious and want to talk to someone, 45764`, then `1`: Glouster Health comes first
  although it is 8 miles away, with a behavioral health time, because it is tagged for mental
  health. Nelsonville Family, untagged, is third.
- `checkup 45769`, `1`, `1`, `Pat Sample 02/03/1970`: books Pomeroy Family, which belongs to the
  second organization (practice 195901 in `/_state`). Then `RESCHEDULE` and pick Athens Health
  Ctr: name and date of birth are asked again, because Athens belongs to the other organization
  and patient records are per organization. Afterwards the visit is booked in 195900 and
  cancelled in 195901.
- `checkup 44101` (Cleveland): no clinic within 45 miles, so "No open times near you right now.
  A health worker will text you…" and the worker phone gets the alert.
- One clinic broken: `curl -X POST "http://127.0.0.1:8796/_fail?departmentid=2"`. Nelsonville
  Family is left out and the others are still offered, a few seconds later than usual because
  the failing call is retried first (`&on=0` mends it). Slow instead of broken:
  `curl -X POST "http://127.0.0.1:8796/_slow?departmentid=2&seconds=8"`; the text arrives after
  5 seconds (`ATHENA_SLOT_TIMEOUT`) without that clinic (`&seconds=0` ends it).
- athenahealth busy: `curl -X POST "http://127.0.0.1:8796/_throttle?n=3"` answers the next three
  calls with 429; the options still arrive. Down: `curl -X POST http://127.0.0.1:8796/_down`
  (and `/_up`); the patient is told a health worker will set a visit up.
- `curl -X POST http://127.0.0.1:8796/_reset` puts the fake back to the start.

Options are cached for 30 seconds per clinic, so wait that long (or restart the server) after
changing something on the fake.

### Going live checklist

Going live takes changes in `.env` and the overrides file only, no code.

1. In `.env`: `ATHENA_CLIENT_ID`, `ATHENA_CLIENT_SECRET`, `ATHENA_PRACTICE_IDS` (one practice
   id per clinic organization, comma-separated), `ATHENA_ENV=preview` or `production`. Leave
   `ATHENA_BASE_URL` blank. Leave `CLINIC_SOURCE` alone for now.
2. `uv run python tools/check_athena_live.py`. Read-only: it lists the departments, shows how
   each mapped and which fields athenahealth did not send, and prints the options texts for a
   few ZIP codes (add your own: `tools/check_athena_live.py 45701 43138`). Look for:
   - "missing: latitude, longitude": that clinic sits at the centre of its ZIP code. Fine for
     ranking; a department with no known ZIP either is not offered.
   - "NOT offered" on a clinic that should be, or the other way round. Which field marks a closed
     department was not confirmed without credentials; if it differs, the fix is in one
     place, `map_department()` in `app/clinics.py`.
3. `uv run python tools/athena_setup.py overrides` writes `app/data/clinic_overrides.json` with
   one entry per department (it replaces the fake's entries and keeps ones you already filled
   in). For each clinic fill in `short` (18 characters or fewer), `income`, `needs`,
   `appointment_types` and set `hidden` for departments patients should not book.
   `uv run python tools/athena_setup.py` lists the appointment type ids.
4. Run `tools/check_athena_live.py` again and read the texts as a patient would.
5. Still in Preview, with a made-up name and birthday: set `CLINIC_SOURCE=athena`, start the
   server and go through steps 1–6 of the table above once. Booking, the reschedule call and
   cancelling have only been run against the fake so far; this is their first real run.
6. Production: the production client id and secret, `ATHENA_ENV=production`, the real
   `ATHENA_PRACTICE_IDS`, `CLINIC_SOURCE=athena`. Repeat steps 2 to 4 there, then restart.

The directory is reloaded every hour (`CLINIC_DIRECTORY_TTL`); a restart reloads it at once,
together with the overrides file.

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
