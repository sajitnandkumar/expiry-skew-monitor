"""FastAPI app: per-user Angel One sessions, REST for index selection,
websocket pushing live snapshots.

Auth modes (config.AUTH_MODE):
- env: one session auto-created at startup from .env credentials; every
  browser shares it (single-user local mode, the original behaviour).
- publisher: each visitor connects their own Angel One account via the
  SmartAPI publisher-login redirect; sessions are keyed by a cookie.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .angel import AngelClient, ist_now, load_instruments
from .sessions import COOKIE_NAME, SessionStore, UserSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
BROADCAST_INTERVAL = 0.4  # seconds
CLEANUP_INTERVAL = 600    # seconds


class AppState:
    def __init__(self):
        self.store = SessionStore()
        self.startup_error: str | None = None
        self.clients: dict[WebSocket, str] = {}  # ws -> sid
        self.select_lock = asyncio.Lock()


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.SMARTAPI_API_KEY:
        state.startup_error = "SMARTAPI_API_KEY missing in .env"
        log.error(state.startup_error)
    elif config.AUTH_MODE == "env":
        try:
            angel = await asyncio.to_thread(
                AngelClient.from_credentials,
                config.SMARTAPI_API_KEY,
                config.SMARTAPI_CLIENT_CODE,
                config.SMARTAPI_PIN,
                config.SMARTAPI_TOTP_SECRET,
            )
            session = state.store.create(angel, config.SMARTAPI_API_KEY)
            state.store.local_sid = session.sid
        except Exception as exc:
            state.startup_error = str(exc)
            log.error("Startup auth failed: %s", exc)

    # Warm the shared instrument master without blocking startup.
    asyncio.create_task(asyncio.to_thread(_warm_instruments))

    broadcaster = asyncio.create_task(broadcast_loop())
    cleaner = asyncio.create_task(cleanup_loop())
    yield
    broadcaster.cancel()
    cleaner.cancel()


def _warm_instruments():
    try:
        load_instruments()
    except Exception as exc:
        log.error("Instrument master warm-up failed: %s", exc)


app = FastAPI(title="Expiry Skew Monitor", lifespan=lifespan)


class SelectBody(BaseModel):
    index: str  # "NIFTY" | "SENSEX"


def resolve_session(sid: str | None) -> UserSession | None:
    if config.AUTH_MODE == "env":
        return state.store.get(state.store.local_sid)
    return state.store.get(sid)


def build_snapshot(session: UserSession | None) -> dict:
    """Assemble the state pushed to one user's browser(s)."""
    chain = session.chain if session else None
    prices = session.feed.prices if session else {}
    header = {
        "index": chain["index"] if chain else None,
        "expiry": chain["expiry"] if chain else None,
        "spot": None,
        "atm_strike": None,
        "strike_interval": chain["strike_interval"] if chain else None,
        "ws_status": session.feed.status if session else "disconnected",
        "ws_error": session.feed.last_error if session else None,
        "auth_error": state.startup_error,
        "client_code": session.client_code if session else None,
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
        snapshots: dict[str, dict] = {}  # one build per session per tick
        dead = []
        for ws, sid in list(state.clients.items()):
            if sid not in snapshots:
                snapshots[sid] = build_snapshot(resolve_session(sid))
            try:
                await ws.send_json(snapshots[sid])
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.clients.pop(ws, None)


async def cleanup_loop():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        state.store.cleanup_idle()


# ------------------------------------------------------------------- auth
@app.get("/api/me")
async def me(request: Request):
    session = resolve_session(request.cookies.get(COOKIE_NAME))
    return {
        "mode": config.AUTH_MODE,
        "authenticated": session is not None,
        "client_code": session.client_code if session else None,
        "login_url": config.PUBLISHER_LOGIN_URL if config.AUTH_MODE == "publisher" else None,
        "auth_error": state.startup_error,
    }


# The docs say auth_token & feed_token, but Angel One has shipped other
# spellings; accept the known variants.
AUTH_TOKEN_PARAMS = ("auth_token", "authToken", "jwtToken", "jwttoken", "token")
FEED_TOKEN_PARAMS = ("feed_token", "feedToken", "feedtoken")


def _pick(params, names):
    for n in names:
        v = params.get(n)
        if v:
            return v
    return None


@app.get("/callback")
async def publisher_callback(request: Request):
    """Angel One redirects here after publisher login with auth_token & feed_token."""
    auth_token = _pick(request.query_params, AUTH_TOKEN_PARAMS)
    feed_token = _pick(request.query_params, FEED_TOKEN_PARAMS)
    if not auth_token or not feed_token:
        # Surface which params DID arrive (names only, never values).
        names = ", ".join(sorted(request.query_params.keys())) or "none"
        log.error("Publisher redirect without usable tokens; params: %s", names)
        return RedirectResponse(
            "/?login_error=" + quote(f"Angel One returned no tokens (params received: {names})")
        )
    try:
        angel = await asyncio.to_thread(
            AngelClient.from_tokens, config.SMARTAPI_API_KEY, auth_token, feed_token
        )
    except Exception as exc:
        log.error("Publisher token validation failed: %s", exc)
        return RedirectResponse("/?login_error=" + quote(str(exc)))

    session = state.store.create(angel, config.SMARTAPI_API_KEY)
    resp = RedirectResponse("/")
    resp.set_cookie(
        COOKIE_NAME, session.sid,
        httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
        max_age=12 * 3600,
    )
    return resp


class LoginBody(BaseModel):
    client_code: str
    pin: str
    totp: str


@app.post("/api/login")
async def api_login(body: LoginBody):
    """Multi-user credential login: the documented loginByPassword flow with
    the visitor's own client code, PIN and one-time TOTP code. Credentials
    are forwarded to Angel One and never stored; only day-tokens are kept."""
    if config.AUTH_MODE != "publisher":
        raise HTTPException(400, "Not in multi-user mode")
    try:
        angel = await asyncio.to_thread(
            AngelClient.from_login,
            config.SMARTAPI_API_KEY,
            body.client_code.strip().upper(),
            body.pin.strip(),
            body.totp.strip(),
        )
    except Exception as exc:
        log.warning("Credential login failed for %s", body.client_code.strip().upper())
        raise HTTPException(401, str(exc))
    session = state.store.create(angel, config.SMARTAPI_API_KEY)
    resp = JSONResponse({"ok": True, "client_code": session.client_code})
    resp.set_cookie(
        COOKIE_NAME, session.sid,
        httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
        max_age=12 * 3600,
    )
    return resp


class TokenBody(BaseModel):
    auth_token: str
    feed_token: str


@app.post("/api/callback")
async def api_callback(body: TokenBody):
    """Tokens caught client-side (e.g. from a URL fragment, which never
    reaches the server) are posted here to create the session."""
    if config.AUTH_MODE != "publisher":
        raise HTTPException(400, "Not in publisher mode")
    try:
        angel = await asyncio.to_thread(
            AngelClient.from_tokens, config.SMARTAPI_API_KEY, body.auth_token, body.feed_token
        )
    except Exception as exc:
        log.error("Posted token validation failed: %s", exc)
        raise HTTPException(401, str(exc))
    session = state.store.create(angel, config.SMARTAPI_API_KEY)
    resp = JSONResponse({"ok": True, "client_code": session.client_code})
    resp.set_cookie(
        COOKIE_NAME, session.sid,
        httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
        max_age=12 * 3600,
    )
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    if config.AUTH_MODE == "publisher" and sid:
        state.store.remove(sid)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ------------------------------------------------------------------- data
@app.post("/api/select")
async def select_index(body: SelectBody, request: Request):
    index = body.index.upper()
    if index not in config.INDEX_CONFIG:
        raise HTTPException(400, f"Unknown index {index!r}; use NIFTY or SENSEX")
    session = resolve_session(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(401, state.startup_error or "Not connected to Angel One")

    async with state.select_lock:
        try:
            chain = await asyncio.to_thread(session.angel.build_chain, index)
        except Exception as exc:
            log.error("build_chain failed: %s", exc)
            raise HTTPException(502, f"Failed to build option chain: {exc}")

        opt_tokens = [t for r in chain["rows"] for t in (r["ce_token"], r["pe_token"]) if t]
        token_list = [
            {"exchangeType": chain["opt_ws_exchange_type"], "tokens": opt_tokens},
            {"exchangeType": chain["spot_ws_exchange_type"], "tokens": [chain["spot_token"]]},
        ]
        session.chain = chain
        session.feed.set_subscription(token_list)

    return build_snapshot(session)


@app.get("/api/status")
async def status(request: Request):
    return build_snapshot(resolve_session(request.cookies.get(COOKIE_NAME)))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = resolve_session(ws.cookies.get(COOKIE_NAME))
    if session is None:
        await ws.send_json({"type": "auth_required"})
        await ws.close()
        return
    state.clients[ws] = session.sid
    try:
        await ws.send_json(build_snapshot(session))
        while True:
            await ws.receive_text()  # keepalive; client messages ignored
    except WebSocketDisconnect:
        pass
    finally:
        state.clients.pop(ws, None)


@app.get("/")
async def root(request: Request):
    # Angel One's My Apps form sometimes rejects redirect URLs with a path,
    # so the bare domain can be registered instead: accept the publisher
    # redirect (?auth_token=...&feed_token=...) here too.
    params = request.query_params
    if config.AUTH_MODE == "publisher" and params and "login_error" not in params:
        return await publisher_callback(request)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
