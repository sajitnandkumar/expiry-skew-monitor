# Expiry Skew Monitor

A local web dashboard that connects to **Angel One SmartAPI** and monitors
call–put pricing skew at the same strike on index expiry days, for **Nifty**
and **Sensex** weekly options.

For each of 11 strikes (ATM ± 5), it strips intrinsic value out of the live
call and put LTPs and compares their **time & risk (extrinsic) value**:

- `Call TV = Call LTP − max(spot − strike, 0)`
- `Put TV  = Put LTP − max(strike − spot, 0)`

The table shows both LTPs, both TVs, which side carries the richer TV, the
absolute TV delta, and `% TV Premium = (higher TV / lower TV − 1) × 100` —
with row colours whose intensity scales with the size of the skew.
Intrinsic is recomputed against the live ticking spot on every update.

## Features

- SmartAPI TOTP login flow (session + feed token) from `.env` credentials
- Manual index selector: **NIFTY** (50-pt strikes, NFO) or **SENSEX** (100-pt strikes, BFO)
- Current weekly expiry auto-resolved from the Angel One instrument master
  (nearest future expiry for the selected index)
- Live spot LTP → nearest strike → chain of 5 strikes above/below
- Instrument tokens resolved from the official instrument master (cached per day)
- Live prices via `SmartWebSocketV2` (LTP mode), pushed to the browser over a
  local websocket every ~400 ms
- Header: index, live spot LTP, ATM strike, countdown to 15:30 IST, feed status
- Rows coloured red when the **put's TV** is richer, green when the **call's
  TV** is richer, neutral within a configurable tolerance; ATM row is bold
- Example: spot 75065, strike 75000, CE 105 (intrinsic 65 → TV 40), PE 55
  (intrinsic 0 → TV 55) → Put TV richer, Δ 15, 37.5%

## Setup

### 1. Get Angel One SmartAPI credentials

1. Create a free SmartAPI account at <https://smartapi.angelbroking.com/> and
   sign in with your Angel One client credentials.
2. Go to **My Apps → Create an App**, choose *Trading API*, give it any name
   and redirect URL (e.g. `http://127.0.0.1`). Copy the generated **API Key**.
3. Enable TOTP at <https://smartapi.angelbroking.com/enable-totp>: log in with
   your **client code** and PIN, and it shows a QR code. Scan it with an
   authenticator app **and also copy the plain-text secret shown under the QR
   code** — that string is your `SMARTAPI_TOTP_SECRET` (the app generates codes
   from it automatically).
4. Your **client code** and **PIN** are the ones you use in the Angel One app.

### 2. Configure the environment

```bash
cd expiry-skew-monitor
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Value |
|---|---|
| `SMARTAPI_API_KEY` | API key from *My Apps* |
| `SMARTAPI_CLIENT_CODE` | Your Angel One client code (e.g. `A123456`) |
| `SMARTAPI_PIN` | Your login PIN |
| `SMARTAPI_TOTP_SECRET` | The TOTP secret from the enable-totp page |
| `SKEW_TOLERANCE_PCT` | Neutral band for row colouring (default 5.0; also adjustable live in the UI) |

### 3. Install and run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:8000>, click **NIFTY** or **SENSEX**, and the chain
loads and starts ticking.

## How it works

```
.env creds ──► SmartConnect.generateSession (TOTP) ──► JWT + feed token
                       │
Instrument master (OpenAPIScripMaster.json, cached daily)
                       │
Select index ──► spot LTP ──► ATM strike ──► 11 strikes ──► CE/PE tokens
                       │
SmartWebSocketV2 (LTP mode) ──► FastAPI state ──► /ws ──► browser table
```

- **Backend:** Python + FastAPI (`app/main.py`), SmartAPI client
  (`app/angel.py`), websocket feed manager (`app/feed.py`).
- **Frontend:** plain HTML/CSS/vanilla JS in `app/static/` — no framework.
- The backend broadcasts a full snapshot to connected browsers every 400 ms;
  skew, colours, and the countdown are computed client-side, so changing the
  tolerance re-renders instantly.

## Assumptions (verify against your account)

These follow the documented Angel One formats; flag anything that differs:

- **Instrument master** is fetched from
  `https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json`
  with fields `token, symbol, name, expiry (DDMMMYYYY), strike (in paise),
  instrumenttype, exch_seg`. Index options have `instrumenttype = OPTIDX`,
  `exch_seg = NFO` (Nifty) / `BFO` (Sensex), and symbols ending `CE`/`PE`.
- **Weekly expiry** = the nearest future expiry among the index's OPTIDX rows.
  (If a monthly expiry falls in the current week, it is that same contract.)
- **Spot index tokens:** Nifty 50 = `99926000` (NSE), Sensex = `99919000`
  (BSE), subscribed on the CM segments for the live header LTP.
- **Websocket schema:** handled by the official `smartapi-python` SDK
  (`SmartWebSocketV2`), which decodes ticks into dicts with
  `last_traded_price` in **paise** (divided by 100 here).

## Notes

- One SmartAPI session is created at startup; restart the app if the session
  expires (Angel One sessions last the trading day).
- Outside market hours the login still works but prices won't tick; the last
  traded prices from the REST snapshot/feed may be sparse.
- The instrument master (~100k rows) is cached in `instruments_cache.json`
  and refreshed once per day.
