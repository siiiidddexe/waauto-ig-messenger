"""
Central config — all values have real defaults baked in.
Environment variables override these when set (e.g. in Dokploy).
"""
import os
from dotenv import load_dotenv

load_dotenv()

def _e(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()

# ── Instagram ──────────────────────────────────────────────────────────────
INSTAGRAM_ACCESS_TOKEN = _e(
    "INSTAGRAM_ACCESS_TOKEN",
    "IGAF3ISP3gXMpBZAGFGbm1TRHI0VGRianVVV1d0endQbUhrTENrR1VWWS0ybTNlcVNaN2NxMFdVTnhveWM0ZAnRjOXRadVdIeHctdWwxZAGQwYVpuX250WS1faGRwMWI5Sl9Wd2JSVGJfNjAyREF1ZADdiQnJhSThoUjQxVWJ3ZAElhTQZDZD",
)
INSTAGRAM_USER_ID = _e("INSTAGRAM_USER_ID", "17841436408611252")
INSTAGRAM_APP_SECRET = _e("INSTAGRAM_APP_SECRET", "485262dab0b87840af99d009de82cb19")

# ── Webhook ────────────────────────────────────────────────────────────────
WEBHOOK_VERIFY_TOKEN = _e("WEBHOOK_VERIFY_TOKEN", "waauto_ig_verify_2026")

# ── Admin login ────────────────────────────────────────────────────────────
ADMIN_EMAIL    = _e("ADMIN_EMAIL",    "siddhantsundar2016@gmail.com")
ADMIN_PASSWORD = _e("ADMIN_PASSWORD", "!Hesoyam3451")
SECRET_KEY     = _e("SECRET_KEY",     "instacontrol-flask-s3ss10n-k3y-logiclaunch-2026")

# ── Gemini AI ──────────────────────────────────────────────────────────────
GEMINI_API_KEY = _e("GEMINI_KEY_1", "AIzaSyDj6aQyjaSn") + _e("GEMINI_KEY_2", "EG0nb8v038jj0cUhzq6c4")

# ── Database ───────────────────────────────────────────────────────────────
DB_PATH = _e("DB_PATH", "/data/messages.db")

# ── Server ─────────────────────────────────────────────────────────────────
PORT        = int(_e("PORT", "5000"))
FLASK_DEBUG = _e("FLASK_DEBUG", "0") == "1"
