# User stories

## 1. Book a visit by text

As a rural resident with only a basic phone, I want to book a healthcare appointment near me
entirely by text message, so that I can get care without internet, an app, or a phone call during
work hours.

### Acceptance criteria

- **Start:** Given I text anything to the service number, when the message arrives, then I get a
  reply within 10 seconds asking what I need care for and where I am.
- **Location:** Given I reply with a ZIP code or town name, when it is recognized, then the service
  confirms the place back to me; if it is not recognized, it asks again.
- **Options:** Given my location and need, when clinics are found, then I get up to three numbered
  options, each with name, distance and next available time.
- **Booking:** Given I reply with an option number, when the slot is still open, then the
  appointment is booked and I get a confirmation text with date, time, address and clinic phone number.
- **Slot taken:** Given the slot was taken in the meantime, when I pick it, then I am told and
  offered the next available times.
- **Change or cancel:** Given I have a booking, when I text CANCEL or RESCHEDULE, then the booking
  is updated and I get a confirmation.
- **Reminder:** Given I have a booking, when it is 24 hours away, then I get a reminder text.
- **Emergency:** Given my message describes an emergency (chest pain, can't breathe, suicidal
  thoughts), when it is detected, then I am told to call 911 or 988 and no booking flow starts.
- **Plain SMS only:** Every step works on a phone with no data connection: no links required, and
  each message fits in 160 characters.
- **Opt out:** Given I text STOP, then I receive no further messages.

### Assumptions

- Location is typed, not shared. SMS cannot send GPS coordinates, so "given your location" means a
  ZIP code or town name.
- Booking method is undecided. For the hackathon, the realistic choices are a mock clinic calendar
  we control or an outbound voice call to the clinic front desk.

Status of each criterion: see the table at the bottom of this file.

## 2. Medication prices and nearest hospitals by text

As a rural resident on a tight budget with only a basic phone, I want to text a medication name and
get the lowest prices at pharmacies near me, and text HOSPITAL to find the nearest hospital or ER,
so that I can afford my prescriptions and know where to go without internet.

### Acceptance criteria

- **Price lookup:** Given I text `PRICE metformin 500` (or "how much is metformin"), when the drug
  and strength are recognized, then I get up to three nearby pharmacies, cheapest first, each with
  name, distance, cash price for a 30-day supply, and phone number.
- **Unclear drug:** Given the name is misspelled or matches several strengths or forms, when I
  text it, then I get a short numbered list to pick from (e.g. `1) metformin 500mg 2) metformin 1000mg`)
  instead of a guess.
- **Cheaper option:** Given a lower-cost choice exists (the generic of a brand drug, a store
  discount generic list, or a patient assistance program), then the reply mentions it in one line
  and suggests asking the pharmacist. It never suggests changing a dose or stopping a medicine.
- **Location:** Given I already gave a ZIP code or town in this conversation, then it is reused;
  otherwise I am asked for one, using the same lookup as story 1.
- **Nearest hospital:** Given I text `HOSPITAL` or `ER`, then I get the three nearest hospitals
  with an emergency department: name, distance, phone, and whether it is open 24 hours.
- **Emergency first:** Given my message describes an emergency, then I am told to call 911 (or
  988 for a crisis) before any list is sent.
- **Honest prices:** Every price reply says how old the price is and "call to confirm", because
  pharmacy cash prices change.
- **Plain SMS only:** No links required, and each message fits in 160 characters.
- **Opt out:** Given I text STOP, then I receive no further messages.

### Assumptions

- Location is typed (ZIP or town). SMS cannot send GPS. A web or app version could use the
  phone's GPS, but this story is for basic phones.
- Real pharmacy cash prices are not published in one public feed. For the hackathon we combine a
  public baseline (NADAC) with a small hand-collected table for local pharmacies, clearly dated.

### Dataset needed

| Data | Fields | Public source |
|------|--------|---------------|
| Drug names | name, brand/generic, strength, form, RxCUI | NLM RxNorm (RxNav API) |
| Price baseline | NDC, price per unit, as-of date | CMS NADAC, weekly (what pharmacies pay, so a floor, not the shelf price) |
| Local cash prices | pharmacy, drug, strength, qty, price, date collected | Collected by the team (phone calls or store generic lists) |
| Pharmacies | name, address, phone, lat/lon, hours | NPPES NPI Registry (pharmacy taxonomy), geocoded with the Census Geocoder |
| Hospitals | name, address, phone, lat/lon, emergency services yes/no, type | CMS Provider Data: Hospital General Information |
| Low-cost clinics | name, address, phone, lat/lon, sliding scale | HRSA Health Center Service Delivery Sites |
| Places | town, ZIP, centre lat/lon | Census ZCTA Gazetteer file |

## Story 1 status

Checked by `sms/tools/check_story.py` (all passing) and by hand in the phone simulator.
Real phones over Twilio are not tested yet.

| Criterion | Status | How it works |
|-----------|--------|--------------|
| Start | Done | Any first text gets "What do you need care for, and what is your ZIP code or town?" in milliseconds. If the first text already names a place, it skips straight to confirming it. |
| Location | Done | 5-digit ZIP or town name (18 Southeast Ohio towns in `sms/app/data/places.json`), confirmed back as "Got it: Nelsonville, OH 45764". Unknown places are asked again. Smartphones can also send a shared Apple Maps location. |
| Options | Done | After one income question (1 = low income / Medicaid / uninsured, 2 = other insurance or can pay), up to three numbered clinics, nearest first, with distance and next open time. Low income is offered the mobile health unit and free or sliding-scale clinics; others get clinics and hospitals. |
| Booking | Done | Reply a number: booked in a mock clinic calendar, confirmation with date, time, address and clinic phone. |
| Slot taken | Done | One patient per slot, enforced by the database. A taken slot gets "Sorry, that time was just taken" and the next three times at that clinic. |
| Change or cancel | Done, with a change | `CANCEL VISIT` and `RESCHEDULE`. A bare `CANCEL` is a carrier opt-out keyword on Twilio (it would unsubscribe the patient), so it is treated like STOP. To allow a bare CANCEL, remove it from Twilio's Advanced Opt-Out keywords first. Reschedule keeps the old visit until a new time is picked. |
| Reminder | Done | Checked every minute; one text 24 hours before. Visits booked less than a day ahead get no separate reminder. |
| Emergency | Done | Chest pain, can't breathe, stroke, overdose and similar: "call 911". Suicidal thoughts: 988. Works at any step, and no booking flow starts. The health worker is alerted. |
| Plain SMS only | Done | Every patient text is at most 160 characters in the GSM-7 alphabet (one SMS segment), with no links. Spanish replies avoid á/í/ó/ú for the same reason. |
| Opt out | Done | STOP (and the other carrier keywords) stops every text from the server, including reminders and health worker replies. START opts back in. |

Assumptions taken: location is typed (or shared from a smartphone); booking uses a mock clinic
calendar we control. Clinics in `sms/app/data/services.json` are sample data to be replaced with
the data room's real services.
