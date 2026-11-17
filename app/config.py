"""Configuration from environment variables."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "bingo.db"
# Hoeveel start-snapshots van de SQLite-DB we bewaren (0 = geen snapshots maken).
DB_SNAPSHOT_KEEP = int(os.environ.get("DB_SNAPSHOT_KEEP", "5"))

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme123")
ADMIN_NICKNAME = os.environ.get("ADMIN_NICKNAME", "Admin").strip()
# Set to 1 to overwrite the admin password from ADMIN_PASSWORD on startup.
ADMIN_RESET_PASSWORD = os.environ.get("ADMIN_RESET_PASSWORD", "0") == "1"
# One-shot ops hatch: comma-separated e-mail addresses of stale admin accounts to delete at
# startup (ADMIN_EMAIL itself is never touched). Meant for "the old admin must go" when you have
# no shell at the database; clear it again after that deploy.
PRUNE_ADMINS = os.environ.get("PRUNE_ADMINS", "")

# "auto" sets the Secure flag when the request came in over HTTPS (also behind a proxy).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").lower()
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))

VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", f"mailto:{ADMIN_EMAIL}")

MAX_PHOTO_BYTES = 6 * 1024 * 1024
PHOTO_MAX_SIDE = 1600


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)