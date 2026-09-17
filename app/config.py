"""Configuration loaded from .env."""
import os

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
