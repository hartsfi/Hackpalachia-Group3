import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]


TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
CLINIC_NUMBER = os.getenv("CLINIC_NUMBER", "+17405550100").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
CHW_NUMBERS = _list("CHW_NUMBERS") or ["+17405550199"]

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "claude-haiku-5-5").strip()
# Free-text answers to the booking questions (app/understand.py). On when there is a key;
# FREE_TEXT=0 turns it off and the questions accept only exact answers, as before.
UNDERSTAND_MODEL = os.getenv("UNDERSTAND_MODEL", "claude-haiku-5-5").strip()
UNDERSTAND_ENABLED = bool(ANTHROPIC_API_KEY) and os.getenv("FREE_TEXT", "1").strip() != "0"

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
DB_PATH = os.getenv("DB_PATH", "textbridge.db")

# athenahealth (athenaOne) scheduling. Blank client id = every clinic uses the mock calendar.
ATHENA_CLIENT_ID = os.getenv("ATHENA_CLIENT_ID", "").strip()
ATHENA_CLIENT_SECRET = os.getenv("ATHENA_CLIENT_SECRET", "").strip()
# One athenahealth practice per clinic organization. ATHENA_PRACTICE_IDS=195900,123456 lists them;
# the older single ATHENA_PRACTICE_ID still works as a one-item list. 195900 = public Preview sandbox.
ATHENA_PRACTICE_IDS = _list("ATHENA_PRACTICE_IDS") or [os.getenv("ATHENA_PRACTICE_ID", "").strip() or "195900"]
ATHENA_PRACTICE_ID = ATHENA_PRACTICE_IDS[0]  # default practice for calls that name none
ATHENA_ENV = os.getenv("ATHENA_ENV", "preview").strip()                  # preview | production
ATHENA_BASE_URL = os.getenv("ATHENA_BASE_URL", "").strip().rstrip("/")   # override, e.g. a local fake
ATHENA_ENABLED = bool(ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET)
ATHENA_TIMEOUT = float(os.getenv("ATHENA_TIMEOUT", "") or 20)      # seconds per request
ATHENA_RETRIES = int(os.getenv("ATHENA_RETRIES", "") or 3)         # extra tries after a 429 / 5xx
ATHENA_BACKOFF = float(os.getenv("ATHENA_BACKOFF", "") or 0.5)     # first wait in seconds, doubled each try

# Clinic finder (app/clinics.py, app/finder.py).
# sample = the clinics in app/data/services.json; athena = every bookable department of the
# practices in ATHENA_PRACTICE_IDS, with app/data/clinic_overrides.json on top.
CLINIC_SOURCE = (os.getenv("CLINIC_SOURCE", "").strip().lower() or "sample")
CLINIC_OVERRIDES = os.getenv("CLINIC_OVERRIDES", "").strip()             # blank = app/data/clinic_overrides.json
CLINIC_DIRECTORY_TTL = float(os.getenv("CLINIC_DIRECTORY_TTL", "") or 3600)  # seconds between directory reloads
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "").strip() or "America/New_York"  # when a department names none
MAX_CLINIC_MILES = float(os.getenv("MAX_CLINIC_MILES", "") or 45)        # farther clinics are never offered
CANDIDATES_TO_CHECK = int(os.getenv("CANDIDATES_TO_CHECK", "") or 6)     # nearest clinics whose open times are fetched
NEED_TAG_BONUS_MILES = float(os.getenv("NEED_TAG_BONUS_MILES", "") or 15)  # head start for a clinic tagged for the need
ATHENA_SLOT_TIMEOUT = float(os.getenv("ATHENA_SLOT_TIMEOUT", "") or 5)   # seconds to wait for one clinic's open times

TWILIO_ENABLED = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)
DEV_SIMULATOR = (not TWILIO_ENABLED) or os.getenv("DEV_SIMULATOR", "") == "1"
