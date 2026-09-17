"""Angel One SmartAPI client: login, instrument master, option-chain resolution.

Supports two ways of establishing a session:
- from_credentials(): classic TOTP login with the owner's own credentials
- from_tokens(): publisher-login flow — Angel One redirects the visitor back
  with auth_token & feed_token query params; the client code is fetched via
  Get Profile using that token.
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import pyotp
import requests
from SmartApi import SmartConnect

from .config import INDEX_CONFIG, STRIKES_EACH_SIDE

log = logging.getLogger("angel")

INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "instruments_cache.json")

IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    return datetime.now(IST)


# --------------------------------------------------------- instrument master
# Shared across all user sessions; downloaded once per day.
_master_lock = threading.Lock()
_master: dict = {"date": None, "rows": None}


def load_instruments(force: bool = False) -> list[dict]:
    """Download the Angel One instrument master (cached per IST day)."""
    today = ist_now().date().isoformat()
    with _master_lock:
        if not force and _master["rows"] is not None and _master["date"] == today:
            return _master["rows"]

        if not force and os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    cached = json.load(f)
                if cached.get("date") == today:
                    _master.update(date=today, rows=cached["rows"])
                    log.info("Instrument master loaded from cache (%d rows)", len(cached["rows"]))
                    return cached["rows"]
            except Exception:
                log.warning("Instrument cache unreadable, re-downloading")

        log.info("Downloading instrument master...")
        r = requests.get(INSTRUMENT_MASTER_URL, timeout=120)
        r.raise_for_status()
        rows = r.json()
        _master.update(date=today, rows=rows)
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"date": today, "rows": rows}, f)
        except Exception:
            log.warning("Could not write instrument cache", exc_info=True)
        log.info("Instrument master downloaded (%d rows)", len(rows))
        return rows


def _nearest_expiry_date(index_key: str, instruments: list[dict]):
    cfg = INDEX_CONFIG[index_key]
    today = ist_now().date()
    best = None
    for row in instruments:
        if (
            row.get("name") == cfg["name"]
            and row.get("instrumenttype") == "OPTIDX"
            and row.get("exch_seg") == cfg["opt_seg"]
        ):
            try:
                d = datetime.strptime(row.get("expiry", ""), "%d%b%Y").date()
            except ValueError:
                continue
            if d >= today and (best is None or d < best):
                best = d
    return best


def default_index() -> str | None:
    """The index to preselect: the one expiring soonest (today on expiry
    days); NIFTY wins ties."""
    try:
        instruments = load_instruments()
    except Exception:
        return None
    dates = {k: _nearest_expiry_date(k, instruments) for k in INDEX_CONFIG}
    dates = {k: d for k, d in dates.items() if d}
    if not dates:
        return None
    return min(dates, key=lambda k: (dates[k], k != "NIFTY"))


class AngelClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.smart: SmartConnect | None = None
        self.jwt_token: str | None = None
        self.feed_token: str | None = None
        self.client_code: str | None = None

    # ------------------------------------------------------------------ auth
    @classmethod
    def from_credentials(cls, api_key: str, client_code: str, pin: str, totp_secret: str) -> "AngelClient":
        """TOTP-secret login (env mode): derive the current code, then log in."""
        return cls.from_login(api_key, client_code, pin, pyotp.TOTP(totp_secret).now())

    @classmethod
    def from_login(cls, api_key: str, client_code: str, pin: str, totp_code: str) -> "AngelClient":
        """Standard SmartAPI loginByPassword flow with a one-time TOTP code."""
        c = cls(api_key)
        c.smart = SmartConnect(api_key=api_key)
        resp = c.smart.generateSession(client_code, pin, totp_code)
        if not resp or not resp.get("status"):
            msg = resp.get("message") if isinstance(resp, dict) else str(resp)
            raise RuntimeError(f"SmartAPI login failed: {msg}")
        data = resp["data"]
        # SmartWebSocketV2 wants the raw JWT (no "Bearer " prefix).
        c.jwt_token = data["jwtToken"].replace("Bearer ", "")
        c.feed_token = data.get("feedToken") or c.smart.getfeedToken()
        c.client_code = client_code
        log.info("SmartAPI login OK for %s", client_code)
        return c

    @classmethod
    def from_tokens(cls, api_key: str, auth_token: str, feed_token: str) -> "AngelClient":
        """Publisher-login flow: validate the redirected tokens via Get Profile."""
        c = cls(api_key)
        c.smart = SmartConnect(api_key=api_key)
        auth_token = auth_token.replace("Bearer ", "")
        c.smart.setAccessToken(auth_token)
        c.jwt_token = auth_token
        c.feed_token = feed_token
        resp = c.smart.getProfile("")  # header token is what authenticates
        if not resp or not resp.get("status"):
            msg = resp.get("message") if isinstance(resp, dict) else str(resp)
            raise RuntimeError(f"Token validation failed: {msg}")
        c.client_code = resp["data"]["clientcode"]
        log.info("Publisher login OK for %s", c.client_code)
        return c

    # ------------------------------------------------------------- quotes
    def spot_ltp(self, index_key: str) -> float:
        cfg = INDEX_CONFIG[index_key]
        resp = self.smart.ltpData(cfg["spot_exchange"], cfg["spot_symbol"], cfg["spot_token"])
        if not resp or not resp.get("status"):
            msg = resp.get("message") if isinstance(resp, dict) else str(resp)
            raise RuntimeError(f"ltpData failed for {index_key}: {msg}")
        return float(resp["data"]["ltp"])

    # -------------------------------------------------------- option chain
    def build_chain(self, index_key: str) -> dict:
        """Resolve the current weekly expiry chain: ATM +/- N strikes.

        Returns {index, spot, expiry, atm_strike, strike_interval, rows,
                 spot_token, spot_ws_exchange_type, opt_ws_exchange_type}
        where rows = [{strike, ce_token, pe_token, ce_symbol, pe_symbol}].
        """
        cfg = INDEX_CONFIG[index_key]
        instruments = load_instruments()
        spot = self.spot_ltp(index_key)

        interval = cfg["strike_interval"]
        atm = int(round(spot / interval) * interval)
        wanted = [atm + i * interval for i in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1)]

        # Filter this index's options; strikes in the master are in paise.
        options = [
            row for row in instruments
            if row.get("name") == cfg["name"]
            and row.get("instrumenttype") == "OPTIDX"
            and row.get("exch_seg") == cfg["opt_seg"]
        ]
        if not options:
            raise RuntimeError(f"No OPTIDX instruments found for {index_key} in master")

        today = ist_now().date()
        expiries = {}
        for row in options:
            exp = row.get("expiry", "")
            if exp not in expiries:
                try:
                    expiries[exp] = datetime.strptime(exp, "%d%b%Y").date()
                except ValueError:
                    expiries[exp] = None
        future = {e: d for e, d in expiries.items() if d and d >= today}
        if not future:
            raise RuntimeError(f"No future expiry found for {index_key}")
        expiry_str = min(future, key=future.get)  # nearest = current weekly

        by_strike: dict[int, dict] = {}
        for row in options:
            if row.get("expiry") != expiry_str:
                continue
            try:
                strike = int(round(float(row["strike"]) / 100.0))
            except (KeyError, ValueError):
                continue
            if strike not in wanted:
                continue
            side = "CE" if row["symbol"].endswith("CE") else "PE" if row["symbol"].endswith("PE") else None
            if side:
                by_strike.setdefault(strike, {})[side] = row

        rows = []
        for strike in wanted:
            pair = by_strike.get(strike, {})
            ce, pe = pair.get("CE"), pair.get("PE")
            rows.append({
                "strike": strike,
                "ce_token": ce["token"] if ce else None,
                "pe_token": pe["token"] if pe else None,
                "ce_symbol": ce["symbol"] if ce else None,
                "pe_symbol": pe["symbol"] if pe else None,
            })
        missing = [r["strike"] for r in rows if not (r["ce_token"] and r["pe_token"])]
        if missing:
            log.warning("Strikes missing CE/PE in master for %s %s: %s", index_key, expiry_str, missing)

        return {
            "index": index_key,
            "spot": spot,
            "expiry": expiry_str,
            "expiry_date": future[expiry_str].isoformat(),
            "atm_strike": atm,
            "strike_interval": interval,
            "rows": rows,
            "spot_token": cfg["spot_token"],
            "spot_ws_exchange_type": cfg["spot_ws_exchange_type"],
            "opt_ws_exchange_type": cfg["opt_ws_exchange_type"],
        }
