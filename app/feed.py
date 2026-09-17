"""Live feed via SmartWebSocketV2, running on its own thread.

The SDK parses the binary tick into a dict; prices arrive in paise under
`last_traded_price` and are stored here in rupees, keyed by token.
"""
import logging
import threading

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

log = logging.getLogger("feed")

MODE_LTP = 1
CORRELATION_ID = "skew-monitor"


class FeedManager:
    def __init__(self):
        self._sws: SmartWebSocketV2 | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.prices: dict[str, float] = {}   # token -> LTP in rupees
        self.status = "disconnected"          # disconnected | connecting | connected | error
        self.last_error: str | None = None
        self._current_sub: list[dict] | None = None  # token_list currently wanted

    # ------------------------------------------------------------- lifecycle
    def start(self, auth_token: str, api_key: str, client_code: str, feed_token: str) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.status = "connecting"
        self._sws = SmartWebSocketV2(
            auth_token, api_key, client_code, feed_token, max_retry_attempt=5
        )
        self._sws.on_open = self._on_open
        self._sws.on_data = self._on_data
        self._sws.on_error = self._on_error
        self._sws.on_close = self._on_close

        def run():
            try:
                self._sws.connect()
            except Exception as exc:  # connect() blocks; ends on close/failure
                log.error("Websocket thread ended: %s", exc)
                self.status = "error"
                self.last_error = str(exc)

        self._thread = threading.Thread(target=run, name="smartws", daemon=True)
        self._thread.start()

    # ---------------------------------------------------------- subscription
    def set_subscription(self, token_list: list[dict]) -> None:
        """Replace the active subscription with `token_list`
        ([{exchangeType, tokens}]). Safe to call before the socket is open."""
        with self._lock:
            old = self._current_sub
            self._current_sub = token_list
            self.prices = {}
            if self.status == "connected" and self._sws:
                if old:
                    try:
                        self._sws.unsubscribe(CORRELATION_ID, MODE_LTP, old)
                    except Exception:
                        log.warning("Unsubscribe failed (ignored)", exc_info=True)
                try:
                    self._sws.subscribe(CORRELATION_ID, MODE_LTP, token_list)
                except Exception as exc:
                    log.error("Subscribe failed: %s", exc)
                    self.last_error = str(exc)

    # ------------------------------------------------------------- callbacks
    def _on_open(self, wsapp):
        log.info("Websocket connected")
        self.status = "connected"
        self.last_error = None
        with self._lock:
            if self._current_sub:
                try:
                    self._sws.subscribe(CORRELATION_ID, MODE_LTP, self._current_sub)
                except Exception as exc:
                    log.error("Subscribe on open failed: %s", exc)

    def _on_data(self, wsapp, message):
        # message: dict with 'token' and 'last_traded_price' (paise)
        try:
            token = str(message.get("token", "")).strip('"')
            ltp = message.get("last_traded_price")
            if token and ltp is not None:
                self.prices[token] = float(ltp) / 100.0
        except Exception:
            log.warning("Bad tick: %r", message, exc_info=True)

    def _on_error(self, wsapp, error):
        log.error("Websocket error: %s", error)
        self.status = "error"
        self.last_error = str(error)

    def _on_close(self, wsapp, *args):
        log.info("Websocket closed")
        if self.status != "error":
            self.status = "disconnected"
