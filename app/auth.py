"""Passwort-Login mit Server-Session und einfachem Brute-Force-Schutz."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Request

SESSION_COOKIE = "dui_session"
SESSION_TTL = int(os.environ.get("SESSION_TTL", str(12 * 3600)))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0").lower() in {
    "1",
    "true",
    "yes",
}
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

MAX_FAILURES = 5
FAILURE_WINDOW = 300.0

_sessions: dict[str, float] = {}
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def password_configured() -> bool:
    return bool(WEB_PASSWORD)


def check_password(candidate: str) -> bool:
    if not WEB_PASSWORD:
        return False
    return hmac.compare_digest(
        candidate.encode("utf-8"), WEB_PASSWORD.encode("utf-8")
    )


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_rate_limited(request: Request) -> bool:
    key = _client_key(request)
    now = time.time()
    with _lock:
        attempts = [ts for ts in _failures.get(key, []) if now - ts < FAILURE_WINDOW]
        _failures[key] = attempts
    return len(attempts) >= MAX_FAILURES


def register_failure(request: Request) -> None:
    key = _client_key(request)
    with _lock:
        _failures.setdefault(key, []).append(time.time())


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = time.time() + SESSION_TTL
    return token


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    now = time.time()
    with _lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if expiry < now:
            _sessions.pop(token, None)
            return False
        return True


def is_authenticated(request: Request) -> bool:
    return session_valid(request.cookies.get(SESSION_COOKIE))


def cookie_params() -> dict:
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "samesite": "strict",
        "secure": COOKIE_SECURE,
        "max_age": SESSION_TTL,
        "path": "/",
    }


# ---------------------------------------------------------------------------
# CSRF-Schutz (Herkunftsprüfung für schreibende Anfragen)
# ---------------------------------------------------------------------------


def csrf_protection_enabled() -> bool:
    value = os.environ.get("CSRF_PROTECTION", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _hostname(value: str) -> str:
    """Hostnamen aus Host-/Origin-Angaben ziehen (Port und Schema entfernen)."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "//" + value
    try:
        return (urlsplit(value).hostname or "").rstrip(".")
    except ValueError:
        return ""


def allowed_origin_hosts() -> set[str]:
    """Hostnamen aus ALLOWED_ORIGINS (kommagetrennt, mit oder ohne Schema)."""
    hosts = set()
    for entry in os.environ.get("ALLOWED_ORIGINS", "").split(","):
        host = _hostname(entry)
        if host:
            hosts.add(host)
    return hosts


def origin_check(request: Request) -> tuple[bool, dict]:
    """Prüfen, ob eine schreibende Anfrage von dieser Oberfläche stammt.

    Ein Reverse Proxy kann Host oder Port verändern. Deshalb gilt die Anfrage
    als zulässig, wenn exakt Host/Port passen, der Hostname passt (Port
    abweichend) oder der Hostname in ALLOWED_ORIGINS hinterlegt ist. Der
    Browser sendet Origin immer mit; fremde Seiten haben einen anderen
    Hostnamen und werden weiterhin abgelehnt.
    """
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    forwarded_host = request.headers.get("x-forwarded-host", "")
    forwarded_server = request.headers.get("x-forwarded-server", "")
    info = {
        "origin": origin,
        "host": host,
        "forwarded_host": forwarded_host,
    }

    if not csrf_protection_enabled() or not origin:
        return True, info

    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, info

    origin_host = parsed.hostname.lower().rstrip(".")
    origin_netloc = parsed.netloc.lower()

    for candidate in (
        host,
        forwarded_host,
        forwarded_server,
        *forwarded_host.split(","),
        *forwarded_server.split(","),
    ):
        value = (candidate or "").strip()
        if not value:
            continue
        netloc = urlsplit(value if "://" in value else f"//{value}").netloc.lower()
        if netloc == origin_netloc:
            return True, info
        if _hostname(value) == origin_host:
            return True, info

    if origin_host in allowed_origin_hosts():
        return True, info

    return False, info
