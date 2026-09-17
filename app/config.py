"""Configuration loaded from .env."""
import os
from urllib.parse import quote as _urlquote

from dotenv import load_dotenv

load_dotenv()

SMARTAPI_API_KEY = os.getenv("SMARTAPI_API_KEY", "")
SMARTAPI_CLIENT_CODE = os.getenv("SMARTAPI_CLIENT_CODE", "")
SMARTAPI_PIN = os.getenv("SMARTAPI_PIN", "")
SMARTAPI_TOTP_SECRET = os.getenv("SMARTAPI_TOTP_SECRET", "")

# Rows within this % premium of each other are treated as neutral (no colour).
SKEW_TOLERANCE_PCT = float(os.getenv("SKEW_TOLERANCE_PCT", "5.0"))

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# Strikes on each side of ATM (5 -> 11 rows total).
STRIKES_EACH_SIDE = int(os.getenv("STRIKES_EACH_SIDE", "5"))

# "env": single-user, auto-login with the credentials above (local use).
# "publisher": multi-user, visitors connect their own Angel One account via
# the SmartAPI publisher-login redirect flow (hosted use; only the API key
# is needed). Defaults to env mode when full credentials are present.
_has_full_creds = all([SMARTAPI_API_KEY, SMARTAPI_CLIENT_CODE, SMARTAPI_PIN, SMARTAPI_TOTP_SECRET])
AUTH_MODE = os.getenv("AUTH_MODE", "env" if _has_full_creds else "publisher")

# Publisher-login URL. When several apps (redirect URLs) share one API key,
# LOGIN_REDIRECT_URL picks which registered redirect Angel One sends users
# back to — set it to this deployment's own URL (e.g. https://x.onrender.com).
LOGIN_REDIRECT_URL = os.getenv("LOGIN_REDIRECT_URL", "")
PUBLISHER_LOGIN_URL = f"https://smartapi.angelone.in/publisher-login?api_key={SMARTAPI_API_KEY}"
if LOGIN_REDIRECT_URL:
    PUBLISHER_LOGIN_URL += f"&redirect_url={_urlquote(LOGIN_REDIRECT_URL, safe='')}"

# Set the session cookie's Secure flag (any non-empty value). Render sets
# RENDER=true automatically; enable manually behind any other HTTPS proxy.
COOKIE_SECURE = bool(os.getenv("COOKIE_SECURE") or os.getenv("RENDER"))

INDEX_CONFIG = {
    "NIFTY": {
        "name": "NIFTY",
        "spot_exchange": "NSE",
        "spot_symbol": "Nifty 50",
        "spot_token": "99926000",   # NSE index token for Nifty 50
        "opt_seg": "NFO",
        "strike_interval": 50,
        # SmartWebSocketV2 exchange types
        "spot_ws_exchange_type": 1,  # NSE_CM
        "opt_ws_exchange_type": 2,   # NSE_FO
    },
    "SENSEX": {
        "name": "SENSEX",
        "spot_exchange": "BSE",
        "spot_symbol": "SENSEX",
        "spot_token": "99919000",   # BSE index token for Sensex
        "opt_seg": "BFO",
        "strike_interval": 100,
        "spot_ws_exchange_type": 3,  # BSE_CM
        "opt_ws_exchange_type": 4,   # BSE_FO
    },
}
