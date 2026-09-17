"""Per-user sessions: each visitor gets their own Angel client + live feed."""
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from .angel import AngelClient
from .feed import FeedManager

log = logging.getLogger("sessions")

COOKIE_NAME = "skew_sid"
# Angel One tokens die at midnight anyway; drop sessions idle for 4 hours.
MAX_IDLE_SECONDS = 4 * 3600


@dataclass
class UserSession:
    sid: str
    angel: AngelClient
    feed: FeedManager
    chain: dict | None = None
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @property
    def client_code(self) -> str | None:
        return self.angel.client_code


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, UserSession] = {}
        self._lock = threading.Lock()
        self.local_sid: str | None = None  # set in env (single-user) mode

    def create(self, angel: AngelClient, api_key: str) -> UserSession:
        sid = secrets.token_urlsafe(32)
        feed = FeedManager()
        feed.start(angel.jwt_token, api_key, angel.client_code, angel.feed_token)
        session = UserSession(sid=sid, angel=angel, feed=feed)
        with self._lock:
            self._sessions[sid] = session
        log.info("Session created for %s (%d active)", angel.client_code, len(self._sessions))
        return session

    def get(self, sid: str | None) -> UserSession | None:
        if not sid:
            return None
        session = self._sessions.get(sid)
        if session:
            session.last_seen = time.time()
        return session

    def remove(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session:
            session.feed.stop()
            log.info("Session removed for %s (%d active)", session.client_code, len(self._sessions))

    def cleanup_idle(self) -> None:
        now = time.time()
        stale = [
            sid for sid, s in self._sessions.items()
            if sid != self.local_sid and now - s.last_seen > MAX_IDLE_SECONDS
        ]
        for sid in stale:
            self.remove(sid)

    def count(self) -> int:
        return len(self._sessions)
