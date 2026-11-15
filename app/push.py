"""Web Push with VAPID. Keys are generated on first start and kept in the data folder."""

import json
import logging

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode
from pywebpush import WebPushException, webpush

from . import config
from .db import connect

log = logging.getLogger("tombala.push")
_key_path = config.DATA_DIR / "vapid_private.pem"
_public_key = ""


def init_vapid() -> None:
    global _public_key
    config.ensure_dirs()
    if _key_path.exists():
        v = Vapid.from_file(str(_key_path))
    else:
        v = Vapid()
        v.generate_keys()
        v.save_key(str(_key_path))
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _public_key = b64urlencode(raw)


def public_key() -> str:
    return _public_key


def send(user_ids: list[int] | None, title: str, body: str, url: str = "/") -> None:
    """Send a notification. user_ids=None means everyone. Runs as a background task."""
    conn = connect()
    try:
        if user_ids is None:
            subs = conn.execute("SELECT * FROM push_subs").fetchall()
        elif not user_ids:
            return
        else:
            marks = ",".join("?" * len(user_ids))
            subs = conn.execute(f"SELECT * FROM push_subs WHERE user_id IN ({marks})", user_ids).fetchall()
        data = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
        for sub in subs:
            info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
            try:
                webpush(info, data, vapid_private_key=str(_key_path),
                        vapid_claims={"sub": config.VAPID_SUBJECT}, ttl=3600, timeout=10)
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    conn.execute("DELETE FROM push_subs WHERE endpoint = ?", (sub["endpoint"],))
                    conn.commit()
                else:
                    log.warning("push failed (%s): %s", status, exc)
            except Exception as exc:  # network trouble should never break the game
                log.warning("push error: %s", exc)
    finally:
        conn.close()
