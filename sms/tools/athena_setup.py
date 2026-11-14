"""Look up athenahealth ids and onboard clinics.

    cd sms
    uv run python tools/athena_setup.py                  # departments, providers, appointment types
    uv run python tools/athena_setup.py open 1           # next open slots in department 1
    uv run python tools/athena_setup.py open 1 195901    # ... of practice 195901
    uv run python tools/athena_setup.py overrides        # write app/data/clinic_overrides.json

`overrides` writes one entry per department athenahealth returns, for every practice in
ATHENA_PRACTICE_IDS, ready to fill in: short name for texts, income group, need tags and the
appointment type to book. Entries already in the file are kept as they are (only "name" is
refreshed); entries for departments athenahealth no longer returns are dropped and listed.
Add `--dry-run` to print the file instead of writing it.

Reads ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET / ATHENA_PRACTICE_IDS from .env. Read-only on
the athenahealth side: it only lists.
"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import clinics, config  # noqa: E402
from app.athena import client  # noqa: E402

if not config.ATHENA_ENABLED:
    sys.exit("Set ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET in sms/.env first.")
client.read_only = True
args = [a for a in sys.argv[1:] if not a.startswith("--")]
print(f"practices {', '.join(config.ATHENA_PRACTICE_IDS)} on {client.host}\n")

NOTE = ("What athenahealth does not know about each clinic, keyed by clinic id = practiceid:departmentid. "
        "Used when CLINIC_SOURCE=athena. Written by `python tools/athena_setup.py overrides`; filled-in "
        "entries are kept when it runs again. Fields: short = name used in texts (18 characters or "
        "fewer). income = who the clinic is for: low (low income / Medicaid / uninsured), standard "
        "(other insurance or able to pay), all (default). needs = triage categories the clinic is known "
        "for (app/triage.py CATEGORIES, e.g. mental_health). appointment_types = what to book: default "
        "for every need, plus one entry per need; each is {appointmenttypeid} or {reasonid}, optionally "
        "with providerid. No default = any open slot. hidden = true keeps the clinic out of offers.")


def write_overrides(dry_run: bool) -> None:
    path = clinics.overrides_path()
    try:
        have = json.loads(path.read_text()).get("clinics", {})
    except (FileNotFoundError, ValueError):
        have = {}
    out, new = {}, []
    for pid in config.ATHENA_PRACTICE_IDS:
        for c in clinics.load_practice(pid):
            if c.id in have:
                out[c.id] = {**have[c.id], "name": c.name}
                continue
            new.append(c.id)
            out[c.id] = {"name": c.name, "short": clinics.short_name(c.name), "income": "all", "needs": [],
                         "appointment_types": {}, "hidden": not c.bookable or c.lat is None}
            why = "; ".join(c.notes)
            print(f"  new  {c.id:<12} {c.name}" + (f"  ({why})" if why else ""))
    for cid in sorted(set(have) - set(out)):
        print(f"  dropped  {cid}  {have[cid].get('name', '')}  (athenahealth does not return it)")
    text = json.dumps({"_note": NOTE, "clinics": out}, indent=2) + "\n"
    if dry_run:
        print(text)
        return
    path.write_text(text)
    print(f"\n{path}: {len(out)} clinics, {len(new)} new, {len(out) - len(new)} kept.")
    print("Fill in short, income, needs and appointment_types for the new ones "
          "(ids: run this tool with no arguments), then restart the server.")


if args[:1] == ["overrides"]:
    write_overrides("--dry-run" in sys.argv)
    sys.exit()

if args[:1] == ["open"]:
    dep = args[1] if len(args) > 1 else "1"
    pid = args[2] if len(args) > 2 else config.ATHENA_PRACTICE_ID
    today = dt.date.today()
    slots = client.open_slots({"practiceid": pid, "departmentid": dep}, today, today + dt.timedelta(days=14), limit=15)
    print(f"open slots in department {dep} of practice {pid}: {len(slots)} shown")
    for a in slots:
        print(f"  appt {a.get('appointmentid')}  {a.get('date')} {a.get('starttime')}  provider "
              f"{a.get('providerid')}  type {a.get('appointmenttypeid')} {a.get('appointmenttype', '')}")
    sys.exit()

for pid in config.ATHENA_PRACTICE_IDS:
    print(f"== practice {pid}")
    print("departments (clinic id = practiceid:departmentid):")
    for x in client.list("/departments", "departments", practice=pid):
        print(f"  {pid}:{x.get('departmentid'):<4}  {x.get('name', '')}  {x.get('city', '')} {x.get('state', '')} {x.get('zip', '')}")
    print("providers (providerid):")
    for x in client.list("/providers", "providers", practice=pid):
        print(f"  {x.get('providerid'):>4}  {x.get('displayname') or x.get('firstname', '') + ' ' + x.get('lastname', '')}")
    print("appointment types (appointmenttypeid):")
    for x in client.list("/appointmenttypes", "appointmenttypes", practice=pid):
        print(f"  {x.get('appointmenttypeid'):>4}  {x.get('name', '')}  {x.get('duration', '')} min")
    print()
