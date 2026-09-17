"""FastAPI app: REST for index selection, websocket pushing live snapshots."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .angel import AngelClient, ist_now
from .feed import FeedManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
BROADCAST_INTERVAL = 0.4  # seconds


class AppState:
    def __init__(self):
        self.angel: AngelClient | None = None
        self.feed = FeedManager()
        self.chain: dict | None = None       # result of AngelClient.build_chain
        self.auth_error: str | None = None
        self.clients: set[WebSocket] = set()
        self.select_lock = asyncio.Lock()


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [k for k, v in {
        "SMARTAPI_API_KEY": config.SMARTAPI_API_KEY,
        "SMARTAPI_CLIENT_CODE": config.SMARTAPI_CLIENT_CODE,
        "SMARTAPI_PIN": config.SMARTAPI_PIN,
        "SMARTAPI_TOTP_SECRET": config.SMARTAPI_TOTP_SECRET,
    }.items() if not v]
    if missing:
        state.auth_error = f"Missing in .env: {', '.join(missing)}"
        log.error(state.auth_error)
    else:
        angel = AngelClient(
            config.SMARTAPI_API_KEY,
            config.SMARTAPI_CLIENT_CODE,
            config.SMARTAPI_PIN,
            config.SMARTAPI_TOTP_SECRET,
        )
        try:
            await asyncio.to_thread(angel.login)
            await asyncio.to_thread(angel.load_instruments)
            state.angel = angel
            state.feed.start(angel.jwt_token, config.SMARTAPI_API_KEY,
                             config.SMARTAPI_CLIENT_CODE, angel.feed_token)
        except Exception as exc:
            state.auth_error = str(exc)
            log.error("Startup auth failed: %s", exc)

    broadcaster = asyncio.create_task(broadcast_loop())
    yield
    broadcaster.cancel()


app = FastAPI(title="Expiry Skew Monitor", lifespan=lifespan)


class SelectBody(BaseModel):
    index: str  # "NIFTY" | "SENSEX"


def build_snapshot() -> dict:
    """Assemble the full state pushed to browsers."""
    chain = state.chain
    prices = state.feed.prices
    header = {
        "index": chain["index"] if chain else None,
        "expiry": chain["expiry"] if chain else None,
        "spot": None,
        "atm_strike": None,
        "strike_interval": chain["strike_interval"] if chain else None,
        "ws_status": state.feed.status,
        "ws_error": state.feed.last_error,
        "auth_error": state.auth_error,
        "tolerance_pct": config.SKEW_TOLERANCE_PCT,
        "server_time_ist": ist_now().isoformat(),
    }
    rows = []
    if chain:
        spot = prices.get(chain["spot_token"]) or chain["spot"]
        header["spot"] = spot
        interval = chain["strike_interval"]
        header["atm_strike"] = int(round(spot / interval) * interval)
        for r in chain["rows"]:
            rows.append({
                "strike": r["strike"],
                "call": prices.get(r["ce_token"]) if r["ce_token"] else None,
                "put": prices.get(r["pe_token"]) if r["pe_token"] else None,
            })
    return {"type": "snapshot", "header": header, "rows": rows}


async def broadcast_loop():
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL)
        if not state.clients:
            continue
        snapshot = build_snapshot()
        dead = []
        for ws in state.clients:
            try:
                await ws.send_json(snapshot)
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.clients.discard(ws)


@app.post("/api/select")
async def select_index(body: SelectBody):
    index = body.index.upper()
    if index not in config.INDEX_CONFIG:
        raise HTTPException(400, f"Unknown index {index!r}; use NIFTY or SENSEX")
    if state.angel is None:
        raise HTTPException(503, state.auth_error or "SmartAPI session not available")

    async with state.select_lock:
        try:
            chain = await asyncio.to_thread(state.angel.build_chain, index)
        except Exception as exc:
            log.error("build_chain failed: %s", exc)
            raise HTTPException(502, f"Failed to build option chain: {exc}")

        opt_tokens = [t for r in chain["rows"] for t in (r["ce_token"], r["pe_token"]) if t]
        token_list = [
            {"exchangeType": chain["opt_ws_exchange_type"], "tokens": opt_tokens},
            {"exchangeType": chain["spot_ws_exchange_type"], "tokens": [chain["spot_token"]]},
        ]
        state.chain = chain
        state.feed.set_subscription(token_list)

    return build_snapshot()


@app.get("/api/status")
async def status():
    return build_snapshot()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    state.clients.add(ws)
    try:
        await ws.send_json(build_snapshot())
        while True:
            await ws.receive_text()  # keepalive; client messages ignored
    except WebSocketDisconnect:
        pass
    finally:
        state.clients.discard(ws)


@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
