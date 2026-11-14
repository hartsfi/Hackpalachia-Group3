"""First look at a real athenahealth connection. READ-ONLY.

    cd sms
    uv run python tools/check_athena_live.py                  # ZIPs 45701 45764 45769
    uv run python tools/check_athena_live.py 45701 43138      # your own ZIPs
    uv run python tools/check_athena_live.py --need mental_health

Run it the day the credentials arrive, before CLINIC_SOURCE is switched to athena. It
  1. lists the departments of every practice in ATHENA_PRACTICE_IDS,
  2. prints how each one mapped to a clinic and which expected fields athenahealth did not send,
  3. runs the clinic finder for a few ZIP codes and prints the texts a patient would get.

It never books, cancels, reschedules or creates a patient: the client is switched to read-only
before the first call and refuses anything but a GET (the one POST is the OAuth token request,
which changes nothing). It uses a throwaway database, so the real one is not touched either.

Reads ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET / ATHENA_PRACTICE_IDS / ATHENA_ENV from .env.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "live-check.db")  # never the real database

from app import clinics, config, db, finder, services  # noqa: E402
from app.athena import AthenaError, client  # noqa: E402

client.read_only = True  # before anything else: from here on only GET calls can leave this process
calls = {"GET": 0, "other": 0}
_call = client.call


def _counted(method, path, *a, **kw):
    calls["GET" if method == "GET" else "other"] += 1
    return _call(method, path, *a, **kw)


client.call = _counted

if not config.ATHENA_ENABLED:
    sys.exit("Set ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET in sms/.env first.")
config.CLINIC_SOURCE = "athena"  # look at athenahealth whatever .env says
db.init()
args = sys.argv[1:]
need = args.pop(args.index("--need") + 1) if "--need" in args and args.index("--need") + 1 < len(args) else None
zips = [a for a in args if a.isdigit() and len(a) == 5] or ["45701", "45764", "45769"]
problems = []

print(f"athenahealth at {client.host}")
print(f"practices: {', '.join(config.ATHENA_PRACTICE_IDS)}   overrides: {clinics.overrides_path()}\n")

print("== 1. Departments and how they map")
overrides = clinics.load_overrides()
for pid in config.ATHENA_PRACTICE_IDS:
    try:
        raw = client.list("/departments", "departments", practice=pid)
    except AthenaError as e:
        problems.append(f"practice {pid} could not be listed")
        print(f"practice {pid}: FAILED  {e}\n")
        continue
    fields = sorted({k for r in raw for k in r})
    print(f"practice {pid}: {len(raw)} departments")
    print(f"  fields athenahealth sent: {', '.join(fields)}")
    offered = 0
    for r in raw:
        try:
            c = clinics.map_department(r, pid)
        except ValueError as e:
            print(f"  ??  skipped: {e}")
            continue
        svc = clinics.as_service(c, overrides.get(c.id))
        can = svc["bookable"] and not svc["hidden"]
        offered += can
        state = "offered" if can else "hidden in overrides" if svc["bookable"] else "NOT offered"
        where = f"{c.lat:.3f},{c.lon:.3f}" if c.lat is not None else "no location"
        print(f"  {c.id:<12} {state:<19} {svc['short']:<18} {c.town or '?'} {c.zip or '?'}  {where}  {c.tz}"
              f"  for={svc['for']}  needs={','.join(svc['tags']) or '-'}"
              f"  type={svc['athena'].get('appointmenttypeid') or svc['athena'].get('reasonid') or 'any'}")
        if c.missing:
            print(f"  {'':<12} missing: {', '.join(c.missing)}")
        for n in c.notes:
            print(f"  {'':<12} note: {n}")
        if c.id not in overrides:
            print(f"  {'':<12} no entry in the overrides file (run: python tools/athena_setup.py overrides)")
    print(f"  -> {offered} of {len(raw)} can be offered to patients\n")
    if not offered:
        problems.append(f"practice {pid} has no clinic that can be offered")

print("== 2. What a patient would be offered" + (f" (need: {need})" if need else ""))
for z in zips:
    place = services.find_place(z)
    if not place:
        print(f"{z}: not a ZIP in app/data/places.json\n")
        continue
    for income, label in (("low", "low income / Medicaid / uninsured"), ("standard", "other insurance or can pay")):
        services.forget_athena_slots()
        cands = finder.candidates(place, income, need)
        opts = finder.recommend(place, income, need)
        print(f"{place['town']} {z}, {label}: {len(cands)} clinics within {config.MAX_CLINIC_MILES:g} miles checked")
        if not opts:
            print("  (nothing to offer: the patient would be told a health worker will set a visit up)\n")
            continue
        msg = services.option_lines(opts, "Reply 1-{n} to book (0 = none):".format(n=len(opts)))
        print("\n".join("  | " + line for line in msg.split("\n")))
        print(f"  {len(msg)} characters" + ("  TOO LONG FOR ONE SMS" if len(msg) > 160 else "") + "\n")
        if len(msg) > 160:
            problems.append(f"the options text for {z} is {len(msg)} characters")

print(f"== Read-only: {calls['GET']} GET calls, {calls['other']} other calls (must be 0). "
      "Nothing was booked, cancelled or created.")
if calls["other"]:
    problems.append("a non-GET call was attempted (and refused)")
if problems:
    print("\nLOOK AT THESE FIRST:\n" + "\n".join("  - " + p for p in problems))
    sys.exit(1)
print("\nOK. Next: fill in the overrides file, then set CLINIC_SOURCE=athena (see TESTING.md, \"Going live\").")
