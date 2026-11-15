"""Passwords, sessions, invite codes and the current-user dependency."""

import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from . import config
from .db import get_db, now

COOKIE = "tombala_session"
_hasher = PasswordHasher()

INVITE_WORDS = ["RAKI", "SIMIT", "AYRAN", "LOKUM", "MEZE", "DONER", "KEBAP", "CAY", "BAKLAVA", "BOSPHORUS"]
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_invite_code() -> str:
    tail = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(4))
    return f"{secrets.choice(INVITE_WORDS)}-{tail}"


def normalise_code(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    conn.execute(
        "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now(), expires.isoformat(timespec="seconds")),
    )
    conn.commit()
    return token


def cookie_secure(request: Request) -> bool:
    if config.COOKIE_SECURE in ("1", "true", "yes"):
        return True
    if config.COOKIE_SECURE in ("0", "false", "no"):
        return False
    return request.url.scheme == "https"


def set_session_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        path="/",
    )


def user_from_request(request: Request, conn: sqlite3.Connection) -> sqlite3.Row | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = conn.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, now()),
    ).fetchone()
    return row


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    user = user_from_request(request, conn)
    if user is None:
        raise HTTPException(401, "Niet ingelogd")
    return user


def admin_user(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    if not user["is_admin"]:
        raise HTTPException(403, "Alleen voor de admin")
    return user


def require_ajax(request: Request) -> None:
    """Simple CSRF guard for the JSON API: browsers can't add this header cross-site without CORS."""
    if request.headers.get("x-requested-with") != "tombala":
        raise HTTPException(400, "Ongeldige aanvraag")


def bootstrap_admin(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT * FROM users WHERE email = ?", (config.ADMIN_EMAIL,)).fetchone()
    if row is None:
        nickname = config.ADMIN_NICKNAME or "Admin"
        if conn.execute("SELECT 1 FROM users WHERE nickname = ?", (nickname,)).fetchone():
            nickname = f"{nickname}-admin"
        conn.execute(
            "INSERT INTO users(email, nickname, password_hash, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
            (config.ADMIN_EMAIL, nickname, hash_password(config.ADMIN_PASSWORD), now()),
        )
    else:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (row["id"],))
        if config.ADMIN_RESET_PASSWORD:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(config.ADMIN_PASSWORD), row["id"]),
            )
    conn.commit()


class RateLimiter:
    """Tiny in-memory limiter for login and registration attempts."""

    def __init__(self, attempts: int, window_s: int):
        self.attempts = attempts
        self.window = window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> bool:
        t = time.monotonic()
        q = self.hits[key]
        while q and t - q[0] > self.window:
            q.popleft()
        if len(q) >= self.attempts:
            return False
        q.append(t)
        return True


login_limiter = RateLimiter(attempts=10, window_s=300)
