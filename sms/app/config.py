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

# LLM brain that reads patient texts: ollama (local, default) | anthropic | rules
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5").strip()      # used when LLM_PROVIDER=anthropic
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))                 # seconds, then keyword rules

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

# SMS Gateway for Android (https://sms-gate.app): an Android phone with a SIM is the clinic number.
ANDROID_GATEWAY_URL = os.getenv("ANDROID_GATEWAY_URL", "https://api.sms-gate.app/3rdparty/v1").strip().rstrip("/")
ANDROID_GATEWAY_USER = os.getenv("ANDROID_GATEWAY_USER", "").strip()
ANDROID_GATEWAY_PASSWORD = os.getenv("ANDROID_GATEWAY_PASSWORD", "").strip()
ANDROID_SIGNING_KEY = os.getenv("ANDROID_SIGNING_KEY", "").strip()  # app: Settings > Webhooks > Signing Key
ANDROID_ENABLED = bool(ANDROID_GATEWAY_USER and ANDROID_GATEWAY_PASSWORD)

# Which service sends texts: "android" or "twilio". Default: Android if set up, else Twilio.
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "").strip().lower() or (
    "android" if ANDROID_ENABLED else "twilio" if TWILIO_ENABLED else "dev")
SMS_ENABLED = (SMS_PROVIDER == "android" and ANDROID_ENABLED) or (SMS_PROVIDER == "twilio" and TWILIO_ENABLED)
DEV_SIMULATOR = (not SMS_ENABLED) or os.getenv("DEV_SIMULATOR", "") == "1"
