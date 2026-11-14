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

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
DB_PATH = os.getenv("DB_PATH", "textbridge.db")

# athenahealth (athenaOne) scheduling. Blank client id = every clinic uses the mock calendar.
ATHENA_CLIENT_ID = os.getenv("ATHENA_CLIENT_ID", "").strip()
ATHENA_CLIENT_SECRET = os.getenv("ATHENA_CLIENT_SECRET", "").strip()
ATHENA_PRACTICE_ID = os.getenv("ATHENA_PRACTICE_ID", "195900").strip()  # 195900 = public Preview sandbox
ATHENA_ENV = os.getenv("ATHENA_ENV", "preview").strip()                  # preview | production
ATHENA_BASE_URL = os.getenv("ATHENA_BASE_URL", "").strip().rstrip("/")   # override, e.g. a local fake
ATHENA_ENABLED = bool(ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET)

TWILIO_ENABLED = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)
DEV_SIMULATOR = (not TWILIO_ENABLED) or os.getenv("DEV_SIMULATOR", "") == "1"
