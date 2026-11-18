"""Small, dependency-free authentication for the LAN dashboard.

The Telegram allowlist and the dashboard session protect different entry points. This
module protects the human-facing HTTP dashboard with a short-lived signed
session and a per-session CSRF token.  It deliberately stores no server-side
session data, which keeps the Raspberry Pi footprint negligible.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Callable


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PASSWORD_ITERATIONS) -> str:
    """Return a versioned PBKDF2 hash suitable for ``DASHBOARD_PASSWORD_HASH``."""
    if len(password) < 12:
        raise ValueError("A senha do dashboard precisa ter pelo menos 12 caracteres")
    if iterations < 100_000:
        raise ValueError("Número de iterações inseguro")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PASSWORD_SCHEME}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without leaking parsing or comparison timing details."""
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(raw_iterations)
        if not 100_000 <= iterations <= 2_000_000:
            return False
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (binascii.Error, TypeError, ValueError):
        return False


def valid_password_hash(encoded: str) -> bool:
    """Validate the stored hash format without doing a password probe."""
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        digest = _b64decode(raw_digest)
        return bool(
            scheme == PASSWORD_SCHEME
            and 100_000 <= iterations <= 2_000_000
            and 16 <= len(salt) <= 64
            and len(digest) == hashlib.sha256().digest_size
        )
    except (binascii.Error, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class DashboardSession:
    username: str
    expires_at: int
    csrf_token: str


class DashboardAuth:
    """Password verification, signed sessions and a tiny login rate limiter."""

    def __init__(
        self,
        *,
        enabled: bool,
        username: str,
        password_hash: str,
        session_secret: str,
        session_ttl_seconds: int = 8 * 60 * 60,
        max_login_failures: int = 5,
        login_window_seconds: int = 5 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.enabled = bool(enabled)
        self.username = username.strip()
        self.password_hash = password_hash.strip()
        self.session_secret = session_secret.encode("utf-8")
        self.session_ttl_seconds = max(300, min(int(session_ttl_seconds), 24 * 60 * 60))
        self.max_login_failures = max(3, min(int(max_login_failures), 20))
        self.login_window_seconds = max(60, min(int(login_window_seconds), 60 * 60))
        self.clock = clock
        self._login_failures: dict[str, list[float]] = {}

    @property
    def configured(self) -> bool:
        return bool(
            self.username
            and valid_password_hash(self.password_hash)
            and len(self.session_secret) >= 32
        )

    def credentials_match(self, username: str, password: str) -> bool:
        username_ok = hmac.compare_digest((username or "").encode(), self.username.encode())
        password_ok = verify_password(password or "", self.password_hash)
        return bool(username_ok & password_ok)

    def login_allowed(self, client_key: str) -> bool:
        now = self.clock()
        failures = self._recent_failures(client_key, now)
        return len(failures) < self.max_login_failures

    def record_login_failure(self, client_key: str) -> None:
        now = self.clock()
        failures = self._recent_failures(client_key, now)
        if client_key not in self._login_failures and len(self._login_failures) >= 256:
            oldest = min(
                self._login_failures,
                key=lambda key: self._login_failures[key][-1] if self._login_failures[key] else 0,
            )
            self._login_failures.pop(oldest, None)
        failures.append(now)
        self._login_failures[client_key] = failures

    def clear_login_failures(self, client_key: str) -> None:
        self._login_failures.pop(client_key, None)

    def _recent_failures(self, client_key: str, now: float) -> list[float]:
        cutoff = now - self.login_window_seconds
        failures = [stamp for stamp in self._login_failures.get(client_key, []) if stamp >= cutoff]
        if failures:
            self._login_failures[client_key] = failures
        else:
            self._login_failures.pop(client_key, None)
        return failures

    def issue_session(self) -> tuple[str, DashboardSession]:
        session = DashboardSession(
            username=self.username,
            expires_at=int(self.clock()) + self.session_ttl_seconds,
            csrf_token=secrets.token_urlsafe(24),
        )
        payload = _b64encode(
            json.dumps(
                {"sub": session.username, "exp": session.expires_at, "csrf": session.csrf_token},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signature = _b64encode(hmac.new(self.session_secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}", session

    def read_session(self, token: str | None) -> DashboardSession | None:
        try:
            payload, signature = (token or "").split(".", 1)
            expected = _b64encode(hmac.new(self.session_secret, payload.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            data = json.loads(_b64decode(payload))
            username = str(data["sub"])
            expires_at = int(data["exp"])
            csrf_token = str(data["csrf"])
            if not hmac.compare_digest(username.encode(), self.username.encode()):
                return None
            if expires_at <= int(self.clock()) or not csrf_token:
                return None
            return DashboardSession(username, expires_at, csrf_token)
        except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
