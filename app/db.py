"""SQLite storage. One connection per request, WAL mode, plain SQL."""

import json
import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    nickname TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    invite_code TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
-- één code per evenement: iedereen met die code mag een eigen account claimen
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- wie de code als eerste gebruikte
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_id INTEGER REFERENCES items(id),
    PRIMARY KEY (user_id, position)
);
CREATE TABLE IF NOT EXISTS line_prizes (
    line_key TEXT PRIMARY KEY,
    prize TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES items(id),
    witness_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL,              -- pending | confirmed | rejected | vetoed | revoked | cancelled
    joker INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    photo TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_claims_user ON claims(user_id, status);
CREATE INDEX IF NOT EXISTS ix_claims_witness ON claims(witness_id, status);
CREATE TABLE IF NOT EXISTS vetoes (
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (claim_id, user_id)
);
CREATE TABLE IF NOT EXISTS bingos (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    line_key TEXT NOT NULL,
    achieved_at TEXT NOT NULL,
    PRIMARY KEY (user_id, line_key)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "title": "Speerpunt Bingo",
    "kicker": "Editie Istanbul",
    "subtitle": "20-22 nov · volle rij = bingo",
    "footer": "Jaarclub Speerpunt · 09",
    "grid_cols": "3",
    "grid_rows": "4",
    "grid_auto": "1",
    "line_rows": "1",
    "line_cols": "0",
    "line_diagonals": "1",
    "default_line_prize": "Een rondje van de rest",
    "full_card_prize": "Superprijs: de hele groep trakteert",
    "jokers_per_player": "1",
    "veto_threshold": "2",
    "witness_limit": "0",
    "frozen": "0",
}

SEED_ITEMS = [
    "Een pils in Europa én Azië in hetzelfde dagdeel",
    "Rakı zonder vies gezicht, vóór 12:00",
    "Op de foto met een Turkse schone",
    "Een Turkse slurf in twee happen op",
    "Een vette hap na het zuipen",
    "Een vissandwich of rol eten",
    "Gezakkenrold worden",
    "Een veel te groot product afdingen tot onder de marge",
    "Tapijtwinkel in, zonder tapijt maar mét de eigenaar eruit",
    "Op de foto met een hooligan",
    "De groep kwijtraken",
    "Niet binnenkomen in een tent of eruit gezet worden",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    config.ensure_dirs()
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # migratie: users.invite_code is nieuw (codes zijn nu herbruikbaar per evenement)
        if "invite_code" not in {r["name"] for r in conn.execute("PRAGMA table_info(users)")}:  # type: ignore[index]
            conn.execute("ALTER TABLE users ADD COLUMN invite_code TEXT")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
        if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO items(text, created_at) VALUES (?, ?)",
                [(text, now()) for text in SEED_ITEMS],
            )
        conn.commit()
    finally:
        conn.close()


def get_db():
    """FastAPI dependency: a fresh connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def setting_int(s: dict[str, str], key: str) -> int:
    try:
        return int(s.get(key, DEFAULT_SETTINGS.get(key, "0")))
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def add_event(conn: sqlite3.Connection, kind: str, user_id: int | None, **data) -> None:
    conn.execute(
        "INSERT INTO events(kind, user_id, data, created_at) VALUES (?, ?, ?, ?)",
        (kind, user_id, json.dumps(data, ensure_ascii=False), now()),
    )
