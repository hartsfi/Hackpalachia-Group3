# Hackpalachia-Group3

A text-message line that helps people in rural Southeast Ohio get a clinic visit booked from any phone, with no app, login or data connection.

This repo holds the **agent and data layer**. The SMS transport is built separately and talks to this service over one HTTP endpoint.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-key
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 for the test console: a phone-style chat on the left and a live log of what the service did on the right.

## How the SMS layer plugs in

Send each inbound text to `POST /api/message`:

```json
{ "phone": "+17405550100", "text": "bad cough for 3 weeks, im in 45769" }
```

The response is `{ "reply": "..." }`. Send `reply` back to the same number as an SMS. If `reply` is `null`, send nothing (the person has opted out).

Replies are plain text with no links, aimed at 160 characters and never more than 320. Conversation memory is keyed by `phone`, so the SMS layer does not need to track anything.

Other endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/reminder` `{phone}` | Returns the reminder text for that number's upcoming visit, or `null`. A scheduler would call this the day before. |
| `GET /api/state?phone=` | Audit trail (every inbound text, tool call, tool result and reply), bookings and staff handoffs for one number. |
| `POST /api/demo/take-slot` `{phone}` | Demo only: another patient takes the first time just offered, to show the slot-taken path. |
| `POST /api/demo/reset` | Demo only: wipe and reseed all data. |

## How a message is handled

1. **Keywords.** `STOP` and `START` are handled in code.
2. **Emergency check.** Emergency language (chest pain, can't breathe, overdose, suicidal thoughts and similar) gets a fixed 911 / 988 reply written in code. The model is not called.
3. **Agent loop.** Otherwise the model reads the conversation and calls tools until it has a reply. Every clinic, distance, time and booking in a reply comes from a tool result.

Tools: `find_clinics`, `get_open_slots`, `book_slot`, `get_my_booking`, `cancel_booking`, `handoff_to_staff`.

## Layout

| Path | What it is |
|---|---|
| `app/agent.py` | Keyword and emergency checks, system prompt, agent loop |
| `app/tools.py` | Tool definitions and their implementations |
| `app/db.py` | SQLite tables and demo data seeding |
| `app/main.py` | HTTP endpoints |
| `app/static/index.html` | Test console |
| `app/data/` | Fictional clinics and a ZIP / town lookup for Southeast Ohio |
| `tests/` | Checks that run without a model key |
| `docs/architecture.svg` | Architecture diagram |

## Tests

```bash
.venv/bin/python -m tests.test_data_layer
.venv/bin/python -m tests.test_agent_loop
```

Neither needs an API key. The second uses a scripted stand-in for the model, so it checks the loop's plumbing, not how well the real model converses.

## What is real and what is mocked

- **Mocked:** clinics, addresses, phone numbers and appointment times are fictional and generated at startup. ZIP and town coordinates are approximate.
- **Not built here:** sending and receiving SMS, a reminder scheduler, cost estimates, ride coordination, medication reminders.
- **Storage:** a local SQLite file (`demo.db`), chosen so the demo needs no setup. The tables map directly onto Postgres.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Required for the agent to reply |
| `AGENT_MODEL` | `claude-haiku-5-5` | Model used for the conversation |
| `DB_PATH` | `demo.db` | Where the SQLite file lives |
