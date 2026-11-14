"""List the athenahealth ids needed for the "athena" blocks in app/data/services.json.

    cd sms && uv run python tools/athena_setup.py            # departments, providers, appointment types
    cd sms && uv run python tools/athena_setup.py open 1     # next open slots in department 1

Reads ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET / ATHENA_PRACTICE_ID from .env.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402
from app.athena import client  # noqa: E402

if not config.ATHENA_ENABLED:
    sys.exit("Set ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET in sms/.env first.")
print(f"practice {config.ATHENA_PRACTICE_ID} on {client.host}\n")

if sys.argv[1:2] == ["open"]:
    dep = sys.argv[2] if len(sys.argv) > 2 else "1"
    today = dt.date.today()
    slots = client.open_slots({"departmentid": dep}, today, today + dt.timedelta(days=14), limit=15)
    print(f"open slots in department {dep}: {len(slots)}")
    for a in slots:
        print(f"  appt {a.get('appointmentid')}  {a.get('date')} {a.get('starttime')}  provider "
              f"{a.get('providerid')}  type {a.get('appointmenttypeid')} {a.get('appointmenttype', '')}")
    sys.exit()

deps = client.call("GET", "/departments", params={"limit": 50}).get("departments", [])
print("departments (departmentid):")
for x in deps:
    print(f"  {x.get('departmentid'):>4}  {x.get('name', '')}  {x.get('city', '')} {x.get('state', '')} {x.get('zip', '')}")
provs = client.call("GET", "/providers", params={"limit": 50}).get("providers", [])
print("\nproviders (providerid):")
for x in provs:
    print(f"  {x.get('providerid'):>4}  {x.get('displayname') or x.get('firstname', '') + ' ' + x.get('lastname', '')}")
types = client.call("GET", "/appointmenttypes", params={"limit": 50}).get("appointmenttypes", [])
print("\nappointment types (appointmenttypeid):")
for x in types:
    print(f"  {x.get('appointmenttypeid'):>4}  {x.get('name', '')}  {x.get('duration', '')} min")
