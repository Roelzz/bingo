"""Speerpunt Bingo: drinking-game bingo for jaarclub Speerpunt."""

import asyncio
import base64
import binascii
import io
import json
import logging
import re
import secrets
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps

from . import config, game, push
from .auth import (
    COOKIE, admin_user, bootstrap_admin, create_session, current_user, drop_user, hash_password, login_limiter,
    new_invite_code, normalise_code, prune_admins, require_ajax, set_session_cookie, user_from_request, verify_password,
)
from .db import add_event, connect, get_db, init_db, now, setting_int, settings
from .events import broker

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")
STATIC_VERSION = secrets.token_hex(4)  # cache-buster per deploy
templates.env.globals["v"] = STATIC_VERSION
log = logging.getLogger("bingo")
if not logging.getLogger().handlers:  # uvicorn configureert alleen zijn eigen loggers
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr)
    log.setLevel(logging.INFO)

AVATAR_COLORS = ["#8e1f1f", "#a07a2c", "#2f4f3a", "#3b4a6b", "#6b3e5e", "#7a5230", "#40625f", "#5a2d2d", "#55603a", "#384048"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    push.init_vapid()
    conn = connect()
    try:
        bootstrap_admin(conn)
        removed_admins = prune_admins(conn, config.PRUNE_ADMINS)  # one-shot: legacy admin-accounts opruimen
        if removed_admins:
            log.warning("PRUNE_ADMINS: admin-accounts verwijderd: %s", ", ".join(removed_admins))
        stats = conn.execute("SELECT COUNT(*) AS n, SUM(is_admin) AS admins, MIN(created_at) AS oldest FROM users").fetchone()
        log.info("data: %s — %d accounts, %d admin(s), oudste account %s, PRUNE_ADMINS=%s", config.DB_PATH, stats["n"],
                 stats["admins"] or 0, stats["oldest"], config.PRUNE_ADMINS or "-")
        game.rebuild_everything(conn, reshuffle=False)
        game.auto_grid(conn, protect=True)  # keep the grid optimal, without touching anybody's card
        conn.commit()
    finally:
        conn.close()
    broker.attach(asyncio.get_running_loop())
    yield


app = FastAPI(title="Speerpunt Bingo", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


# ---------------------------------------------------------------- helpers

def color_for(user_id: int) -> str:
    return AVATAR_COLORS[user_id % len(AVATAR_COLORS)]


def player_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "nickname": row["nickname"], "color": color_for(row["id"]), "is_admin": bool(row["is_admin"])}


def jokers_left(conn: sqlite3.Connection, user_id: int, s: dict) -> int:
    used = conn.execute("SELECT COUNT(*) FROM claims WHERE user_id = ? AND joker = 1", (user_id,)).fetchone()[0]
    return max(0, setting_int(s, "jokers_per_player") - used)


def nickname_of(conn: sqlite3.Connection, user_id: int | None) -> str:
    if user_id is None:
        return ""
    row = conn.execute("SELECT nickname FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["nickname"] if row else "?"


def item_text(conn: sqlite3.Connection, item_id: int) -> str:
    row = conn.execute("SELECT text FROM items WHERE id = ?", (item_id,)).fetchone()
    return row["text"] if row else "?"


def announce_bingos(bg: BackgroundTasks, nickname: str, won: list[dict]) -> None:
    for w in won:
        is_full = w["key"] == "full"
        broker.publish({"type": "bingo", "full": is_full, "nickname": nickname, "line": w["label"], "prize": w["prize"]})
        title = f"🏆 VOLLE KAART voor {nickname}!" if is_full else f"🎉 BINGO voor {nickname}!"
        body = f"{w['label']} · {w['prize']}" if w["prize"] else w["label"]
        bg.add_task(push.send, None, title, body)


def refresh_all() -> None:
    broker.publish({"type": "refresh"})


def save_photo(data_url: str) -> str:
    """Decode a data URL, strip metadata (incl. GPS), resize, store as JPEG. Returns the file stem."""
    match = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$", data_url, re.S)
    if not match:
        raise HTTPException(400, "Foto niet herkend")
    try:
        raw = base64.b64decode(match.group(1), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Foto kapot")
    if len(raw) > config.MAX_PHOTO_BYTES:
        raise HTTPException(413, "Foto te groot")
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(400, "Dat is geen foto")
    stem = secrets.token_urlsafe(12)
    big = img.copy()
    big.thumbnail((config.PHOTO_MAX_SIDE, config.PHOTO_MAX_SIDE))
    big.save(config.UPLOAD_DIR / f"{stem}.jpg", "JPEG", quality=80, optimize=True)
    small = img.copy()
    small.thumbnail((420, 420))
    small.save(config.UPLOAD_DIR / f"{stem}_t.jpg", "JPEG", quality=72, optimize=True)
    return stem


def photo_urls(stem: str | None) -> dict | None:
    if not stem:
        return None
    return {"full": f"/uploads/{stem}.jpg", "thumb": f"/uploads/{stem}_t.jpg"}


# ---------------------------------------------------------------- pages

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = user_from_request(request, conn)
    if user is None:
        # het eerste scherm is de claimpagina: eerst de invite code, dan het account
        return claim_page(request, conn)
    s = settings(conn)
    return templates.TemplateResponse(request, "app.html", {"s": s, "me": user})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if user_from_request(request, conn):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"s": settings(conn), "error": None, "email": ""})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    email = email.strip().lower()
    ip = request.client.host if request.client else "?"
    ctx = {"s": settings(conn), "email": email}
    if not login_limiter.check(f"{ip}:{email}"):
        return templates.TemplateResponse(request, "login.html", {**ctx, "error": "Rustig aan. Probeer het over een paar minuten weer."}, status_code=429)
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not verify_password(user["password_hash"], password):
        return templates.TemplateResponse(request, "login.html", {**ctx, "error": "E-mail of wachtwoord klopt niet."}, status_code=401)
    token = create_session(conn, user["id"])
    resp = RedirectResponse("/", status_code=303)
    set_session_cookie(resp, request, token)
    return resp


def claim_page(request: Request, conn: sqlite3.Connection, *, code: str = "", error: str | None = None,
               step: int = 1, email: str = "", nickname: str = "", status: int = 200) -> HTMLResponse:
    """Step 1 asks for the invite code; step 2 lets the guest pick a nickname, e-mail and password."""
    return templates.TemplateResponse(
        request,
        "register.html",
        {"s": settings(conn), "code": code, "error": error, "step": step, "email": email, "nickname": nickname},
        status_code=status,
    )


def invite_status(conn: sqlite3.Connection, code: str) -> str:
    """'' as de code deugdelijk is, anders de reden van afwijzing.

    Codes zijn evenementcodes: iedereen die ze heeft mag een eigen account claimen.
    """
    if conn.execute("SELECT 1 FROM invites WHERE code = ?", (code,)).fetchone() is None:
        return "Die code kennen we niet. Check de letters en het streepje."
    return ""


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, code: str = "", conn: sqlite3.Connection = Depends(get_db)):
    return claim_page(request, conn, code=normalise_code(code))


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    bg: BackgroundTasks,
    code: str = Form(...),
    email: str = Form(""),
    nickname: str = Form(""),
    password: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    code, email, nickname = normalise_code(code), email.strip().lower(), nickname.strip()
    ctx = {"s": settings(conn), "code": code, "email": email, "nickname": nickname}

    def fail(msg: str, status: int = 400):
        return templates.TemplateResponse(request, "register.html", {**ctx, "error": msg, "step": 1 if not (email or nickname or password) else 2}, status_code=status)

    ip = request.client.host if request.client else "?"
    if not login_limiter.check(f"reg:{ip}:{email}"):
        return fail("Even pauze. Probeer het over een paar minuten weer.", 429)
    if not (email or nickname or password):
        # stap 1: de gast vulde alleen de code in
        why = invite_status(conn, code)
        if why:
            return fail(why)
        return claim_page(request, conn, code=code, step=2)
    if not (email and nickname and password):
        return fail("Kies een nickname, e-mail en wachtwoord.")
    invite = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
    if invite is None:
        return fail("Die invite code bestaat niet. Vraag de admin om de code van het evenement.")
    if not EMAIL_RE.match(email):
        return fail("Dat e-mailadres ziet er niet goed uit.")
    if not 2 <= len(nickname) <= 20:
        return fail("Nickname moet tussen 2 en 20 tekens zijn.")
    if len(password) < 8:
        return fail("Wachtwoord moet minstens 8 tekens zijn.")
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return fail("Dat e-mailadres heeft al een account. Log gewoon in.")
    if conn.execute("SELECT 1 FROM users WHERE nickname = ?", (nickname,)).fetchone():
        return fail("Die nickname is al bezet. Wees origineel.")

    cur = conn.execute(
        "INSERT INTO users(email, nickname, password_hash, created_at, invite_code) VALUES (?, ?, ?, ?, ?)",
        (email, nickname, hash_password(password), now(), code),
    )
    user_id = cur.lastrowid
    # de code blijft geldig voor iedereen; we onthouden alleen wie hem als eerste gebruikte
    if invite["used_at"] is None:
        conn.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?", (user_id, now(), code))
    game.build_card(conn, user_id, settings(conn))
    add_event(conn, "joined", user_id)
    conn.commit()
    refresh_all()
    bg.add_task(push.send, None, "Nieuwe speler", f"{nickname} doet mee.")

    token = create_session(conn, user_id)
    resp = RedirectResponse("/", status_code=303)
    set_session_cookie(resp, request, token)
    return resp


@app.post("/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest(conn: sqlite3.Connection = Depends(get_db)):
    s = settings(conn)
    data = {
        "name": s.get("title", "Speerpunt Bingo"),
        "short_name": "Speerpunt",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#1b1916",
        "theme_color": "#1b1916",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return JSONResponse(data, media_type="application/manifest+json")


@app.get("/uploads/{name}")
def upload(name: str, user=Depends(current_user)):
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.jpg", name):
        raise HTTPException(404)
    path = config.UPLOAD_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------------------------------------------------------------- state

@app.get("/api/state")
def state(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    s = settings(conn)
    me = user["id"]
    game.build_card(conn, me, s)  # repairs the card if items changed
    conn.commit()
    cols, rows = game.grid(s)

    players = conn.execute("SELECT * FROM users ORDER BY nickname COLLATE NOCASE").fetchall()
    nick = {p["id"]: p["nickname"] for p in players}

    my_claims = {}
    for c in conn.execute(
        "SELECT * FROM claims WHERE user_id = ? AND status IN ('pending', 'confirmed') ORDER BY id", (me,)
    ):
        my_claims[c["item_id"]] = c
    veto_counts = {r[0]: r[1] for r in conn.execute("SELECT claim_id, COUNT(*) FROM vetoes GROUP BY claim_id")}

    items = {r["id"]: r["text"] for r in conn.execute("SELECT id, text FROM items")}
    cells = []
    for pos, item_id in game.card(conn, me, s).items():
        c = my_claims.get(item_id)
        cells.append({
            "pos": pos,
            "item_id": item_id,
            "text": items.get(item_id, "") if item_id else "",
            "status": c["status"] if c else "open",
            "claim": None if not c else {
                "id": c["id"], "witness": nick.get(c["witness_id"], ""), "joker": bool(c["joker"]),
                "note": c["note"], "photo": photo_urls(c["photo"]), "created_at": c["created_at"],
                "vetoes": veto_counts.get(c["id"], 0),
            },
        })

    prize_map = game.prizes(conn, s)
    my_bingos = {r[0]: r[1] for r in conn.execute("SELECT line_key, achieved_at FROM bingos WHERE user_id = ?", (me,))}
    line_list = [
        {**line, "prize": prize_map.get(line["key"], ""), "won": line["key"] in my_bingos}
        for line in game.lines(s)
    ]

    witness_requests = [
        {
            "claim_id": c["id"], "claimant": nick.get(c["user_id"], "?"), "claimant_id": c["user_id"],
            "text": items.get(c["item_id"], "?"), "note": c["note"], "photo": photo_urls(c["photo"]),
            "created_at": c["created_at"],
        }
        for c in conn.execute("SELECT * FROM claims WHERE witness_id = ? AND status = 'pending' ORDER BY id", (me,))
    ]

    witness_load = {r[0]: r[1] for r in conn.execute(
        "SELECT witness_id, COUNT(*) FROM claims WHERE user_id = ? AND status IN ('pending','confirmed') AND witness_id IS NOT NULL GROUP BY witness_id",
        (me,))}

    # leaderboard
    board = []
    for p in players:
        pid = p["id"]
        bingos = conn.execute("SELECT line_key, achieved_at FROM bingos WHERE user_id = ? ORDER BY achieved_at", (pid,)).fetchall()
        confirmed = conn.execute("SELECT COUNT(*) FROM claims WHERE user_id = ? AND status = 'confirmed'", (pid,)).fetchone()[0]
        penalties = conn.execute("SELECT COUNT(*) FROM claims WHERE user_id = ? AND status IN ('rejected','vetoed')", (pid,)).fetchone()[0]
        confirmations = conn.execute("SELECT COUNT(*) FROM claims WHERE witness_id = ? AND status = 'confirmed'", (pid,)).fetchone()[0]
        line_bingos = [b for b in bingos if b["line_key"] != "full"]
        board.append({
            **player_dict(p),
            "bingos": len(line_bingos),
            "full": any(b["line_key"] == "full" for b in bingos),
            "cells": confirmed,
            "penalties": penalties,
            "witnessed": confirmations,
            "first_bingo_at": bingos[0]["achieved_at"] if bingos else None,
            "prizes": [{"label": game.line_label(s, b["line_key"]), "prize": prize_map.get(b["line_key"], "")} for b in bingos],
        })
    board.sort(key=lambda b: (-int(b["full"]), -b["bingos"], -b["cells"], b["first_bingo_at"] or "9999"))
    first_ever = min((b["first_bingo_at"] for b in board if b["first_bingo_at"]), default=None)

    return {
        "me": {**player_dict(user), "jokers_left": jokers_left(conn, me, s)},
        "settings": {
            "title": s["title"], "kicker": s["kicker"], "subtitle": s["subtitle"], "footer": s["footer"],
            "cols": cols, "rows": rows, "frozen": s["frozen"] == "1",
            "veto_threshold": setting_int(s, "veto_threshold"), "witness_limit": setting_int(s, "witness_limit"),
            "full_card_prize": prize_map.get("full", ""),
        },
        "card": cells,
        "lines": line_list,
        "full_card_won": "full" in my_bingos,
        "players": [player_dict(p) for p in players],
        "witness_requests": witness_requests,
        "witness_load": witness_load,
        "leaderboard": board,
        "first_bingo_at": first_ever,
        "feed": feed(conn, me, nick, items, veto_counts),
        "vapid_public_key": push.public_key(),
    }


def feed(conn, me: int, nick: dict, items: dict, veto_counts: dict, limit: int = 80) -> list[dict]:
    events = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    claim_ids = {json.loads(e["data"]).get("claim_id") for e in events} - {None}
    claims, my_vetoes = {}, set()
    if claim_ids:
        marks = ",".join("?" * len(claim_ids))
        claims = {c["id"]: c for c in conn.execute(f"SELECT * FROM claims WHERE id IN ({marks})", list(claim_ids))}
        my_vetoes = {r[0] for r in conn.execute(
            f"SELECT claim_id FROM vetoes WHERE user_id = ? AND claim_id IN ({marks})", [me, *claim_ids])}
    out = []
    for e in events:
        data = json.loads(e["data"])
        entry = {"id": e["id"], "kind": e["kind"], "user": nick.get(e["user_id"], ""), "user_id": e["user_id"],
                 "color": color_for(e["user_id"]) if e["user_id"] else None, "at": e["created_at"], **data}
        cid = data.get("claim_id")
        if cid and cid in claims:
            c = claims[cid]
            entry["status"] = c["status"]
            entry["photo"] = photo_urls(c["photo"])
            entry["vetoes"] = veto_counts.get(cid, 0)
            entry["i_vetoed"] = cid in my_vetoes
            entry["can_veto"] = c["status"] == "confirmed" and me not in (c["user_id"], c["witness_id"])
        out.append(entry)
    return out


@app.get("/api/album")
def album(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        """SELECT c.photo, c.created_at, c.status, i.text, u.nickname, u.id AS uid FROM claims c
           JOIN items i ON i.id = c.item_id JOIN users u ON u.id = c.user_id
           WHERE c.photo IS NOT NULL AND c.status IN ('confirmed', 'pending') ORDER BY c.id DESC"""
    ).fetchall()
    return [{**photo_urls(r["photo"]), "text": r["text"], "nickname": r["nickname"], "color": color_for(r["uid"]),
             "at": r["created_at"], "status": r["status"]} for r in rows]


# ---------------------------------------------------------------- claims

@app.post("/api/claims", dependencies=[Depends(require_ajax)])
def create_claim(bg: BackgroundTasks, payload: dict = Body(...), user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    s = settings(conn)
    me = user["id"]
    if s["frozen"] == "1":
        raise HTTPException(409, "Het spel is bevroren door de admin.")
    try:
        item_id = int(payload.get("item_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Welk vakje?")
    if item_id not in game.card(conn, me, s).values():
        raise HTTPException(400, "Dat vakje staat niet op jouw kaart.")
    if item_id in game.claimed_item_ids(conn, me):
        raise HTTPException(409, "Dit vakje is al geclaimd.")

    joker = bool(payload.get("joker"))
    note = (payload.get("note") or "").strip()[:200] or None
    photo = save_photo(payload["photo"]) if payload.get("photo") else None
    text = item_text(conn, item_id)

    if joker:
        if jokers_left(conn, me, s) <= 0:
            raise HTTPException(409, "Je joker is al op.")
        cur = conn.execute(
            "INSERT INTO claims(user_id, item_id, status, joker, note, photo, created_at, decided_at) VALUES (?, ?, 'confirmed', 1, ?, ?, ?, ?)",
            (me, item_id, note, photo, now(), now()),
        )
        add_event(conn, "claim_joker", me, claim_id=cur.lastrowid, item=text)
        won = game.refresh_bingos(conn, me, s)
        conn.commit()
        refresh_all()
        announce_bingos(bg, user["nickname"], won)
        return {"ok": True, "status": "confirmed", "won": won}

    try:
        witness_id = int(payload.get("witness_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Kies een getuige.")
    if witness_id == me:
        raise HTTPException(400, "Jezelf als getuige? Leuk geprobeerd.")
    witness = conn.execute("SELECT * FROM users WHERE id = ?", (witness_id,)).fetchone()
    if witness is None:
        raise HTTPException(400, "Die getuige bestaat niet.")
    limit = setting_int(s, "witness_limit")
    if limit > 0:
        used = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE user_id = ? AND witness_id = ? AND status IN ('pending','confirmed')",
            (me, witness_id)).fetchone()[0]
        if used >= limit:
            raise HTTPException(409, f"{witness['nickname']} heeft al {limit}× voor jou getuigd. Kies iemand anders.")

    cur = conn.execute(
        "INSERT INTO claims(user_id, item_id, witness_id, status, note, photo, created_at) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
        (me, item_id, witness_id, note, photo, now()),
    )
    add_event(conn, "claim_pending", me, claim_id=cur.lastrowid, item=text, witness=witness["nickname"])
    conn.commit()
    broker.publish({"type": "witness_request", "nickname": user["nickname"], "text": text}, to={witness_id})
    refresh_all()
    bg.add_task(push.send, [witness_id], f"🧐 {user['nickname']} heeft je nodig", f"Bevestig: “{text}”")
    return {"ok": True, "status": "pending"}


def load_claim(conn, claim_id: int) -> sqlite3.Row:
    c = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if c is None:
        raise HTTPException(404, "Claim niet gevonden.")
    return c


@app.post("/api/claims/{claim_id}/confirm", dependencies=[Depends(require_ajax)])
def confirm_claim(claim_id: int, bg: BackgroundTasks, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    s = settings(conn)
    c = load_claim(conn, claim_id)
    if c["witness_id"] != user["id"]:
        raise HTTPException(403, "Jij bent niet de getuige.")
    if c["status"] != "pending":
        raise HTTPException(409, "Deze claim is al afgehandeld.")
    if s["frozen"] == "1":
        raise HTTPException(409, "Het spel is bevroren door de admin.")
    conn.execute("UPDATE claims SET status = 'confirmed', decided_at = ? WHERE id = ?", (now(), claim_id))
    text = item_text(conn, c["item_id"])
    claimant = nickname_of(conn, c["user_id"])
    add_event(conn, "claim_confirmed", c["user_id"], claim_id=claim_id, item=text, witness=user["nickname"])
    won = game.refresh_bingos(conn, c["user_id"], s)
    conn.commit()
    broker.publish({"type": "confirmed", "witness": user["nickname"], "text": text}, to={c["user_id"]})
    refresh_all()
    bg.add_task(push.send, [c["user_id"]], "✅ Bevestigd!", f"{user['nickname']} bevestigt: “{text}”")
    announce_bingos(bg, claimant, won)
    return {"ok": True}


@app.post("/api/claims/{claim_id}/reject", dependencies=[Depends(require_ajax)])
def reject_claim(claim_id: int, bg: BackgroundTasks, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    c = load_claim(conn, claim_id)
    if c["witness_id"] != user["id"]:
        raise HTTPException(403, "Jij bent niet de getuige.")
    if c["status"] != "pending":
        raise HTTPException(409, "Deze claim is al afgehandeld.")
    conn.execute("UPDATE claims SET status = 'rejected', decided_at = ? WHERE id = ?", (now(), claim_id))
    text = item_text(conn, c["item_id"])
    add_event(conn, "claim_rejected", c["user_id"], claim_id=claim_id, item=text, witness=user["nickname"])
    conn.commit()
    broker.publish({"type": "rejected", "witness": user["nickname"], "text": text}, to={c["user_id"]})
    refresh_all()
    bg.add_task(push.send, [c["user_id"]], "❌ Afgewezen. Strafslok!", f"{user['nickname']} trapt er niet in: “{text}”")
    return {"ok": True}


@app.post("/api/claims/{claim_id}/cancel", dependencies=[Depends(require_ajax)])
def cancel_claim(claim_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    c = load_claim(conn, claim_id)
    if c["user_id"] != user["id"] or c["status"] != "pending":
        raise HTTPException(409, "Kan niet meer ingetrokken worden.")
    conn.execute("UPDATE claims SET status = 'cancelled', decided_at = ? WHERE id = ?", (now(), claim_id))
    conn.execute("DELETE FROM events WHERE kind = 'claim_pending' AND json_extract(data, '$.claim_id') = ?", (claim_id,))
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.post("/api/claims/{claim_id}/veto", dependencies=[Depends(require_ajax)])
def veto_claim(claim_id: int, bg: BackgroundTasks, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    s = settings(conn)
    threshold = setting_int(s, "veto_threshold")
    if threshold <= 0:
        raise HTTPException(409, "Vetorecht staat uit.")
    c = load_claim(conn, claim_id)
    if c["status"] != "confirmed":
        raise HTTPException(409, "Alleen bevestigde claims kun je wegstemmen.")
    if user["id"] in (c["user_id"], c["witness_id"]):
        raise HTTPException(403, "Over je eigen zaak mag je niet stemmen.")
    conn.execute("INSERT OR IGNORE INTO vetoes(claim_id, user_id, created_at) VALUES (?, ?, ?)", (claim_id, user["id"], now()))
    count = conn.execute("SELECT COUNT(*) FROM vetoes WHERE claim_id = ?", (claim_id,)).fetchone()[0]
    vetoed = count >= threshold
    if vetoed:
        conn.execute("UPDATE claims SET status = 'vetoed', decided_at = ? WHERE id = ?", (now(), claim_id))
        text = item_text(conn, c["item_id"])
        add_event(conn, "claim_vetoed", c["user_id"], claim_id=claim_id, item=text)
        game.refresh_bingos(conn, c["user_id"], s)
        bg.add_task(push.send, [c["user_id"]], "🚩 Weggestemd!", f"De groep gelooft niks van “{text}”. Strafslok.")
    conn.commit()
    refresh_all()
    return {"ok": True, "vetoes": count, "vetoed": vetoed}


# ---------------------------------------------------------------- push

@app.post("/api/push/subscribe", dependencies=[Depends(require_ajax)])
def push_subscribe(payload: dict = Body(...), user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        endpoint = payload["endpoint"]
        keys = payload["keys"]
        p256dh, auth_key = keys["p256dh"], keys["auth"]
    except (KeyError, TypeError):
        raise HTTPException(400, "Ongeldig abonnement")
    if not str(endpoint).startswith("https://"):
        raise HTTPException(400, "Ongeldig endpoint")
    conn.execute(
        "INSERT OR REPLACE INTO push_subs(endpoint, user_id, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)",
        (endpoint, user["id"], p256dh, auth_key, now()),
    )
    conn.commit()
    return {"ok": True}


@app.post("/api/push/test", dependencies=[Depends(require_ajax)])
def push_test(bg: BackgroundTasks, user=Depends(current_user)):
    bg.add_task(push.send, [user["id"]], "Şerefe! 🍻", "Pushmeldingen werken.")
    return {"ok": True}


# ---------------------------------------------------------------- realtime

@app.get("/api/stream")
async def stream(request: Request):
    conn = connect()
    try:
        user = user_from_request(request, conn)
    finally:
        conn.close()
    if user is None:
        raise HTTPException(401)
    queue = broker.subscribe(user["id"])

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- admin

ADMIN_TEXT_SETTINGS = {"title", "kicker", "subtitle", "footer", "default_line_prize", "full_card_prize"}
ADMIN_INT_SETTINGS = {
    "grid_cols": (2, 6), "grid_rows": (2, 8), "jokers_per_player": (0, 5),
    "veto_threshold": (0, 20), "witness_limit": (0, 50),
}
ADMIN_BOOL_SETTINGS = {"line_rows", "line_cols", "line_diagonals", "grid_auto", "frozen"}
LAYOUT_KEYS = {"grid_cols", "grid_rows", "line_rows", "line_cols", "line_diagonals"}


@app.get("/api/admin")
def admin_state(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    s = settings(conn)
    usage = {r[0]: r[1] for r in conn.execute("SELECT item_id, COUNT(*) FROM cards GROUP BY item_id")}
    done = {r[0]: r[1] for r in conn.execute("SELECT item_id, COUNT(*) FROM claims WHERE status = 'confirmed' GROUP BY item_id")}
    items = [{"id": r["id"], "text": r["text"], "on_cards": usage.get(r["id"], 0), "confirmed": done.get(r["id"], 0)}
             for r in conn.execute("SELECT * FROM items WHERE active = 1 ORDER BY id")]
    uses = {r[0]: r[1] for r in conn.execute("SELECT invite_code, COUNT(*) FROM users WHERE invite_code IS NOT NULL GROUP BY invite_code")}
    invites = [dict(r) | {
        "used_by_name": nickname_of(conn, r["used_by"]) if r["used_by"] else None,
        "uses": uses.get(r["code"], 0),
    } for r in conn.execute("SELECT * FROM invites ORDER BY created_at DESC")]
    players = [{**player_dict(r), "email": r["email"], "created_at": r["created_at"]}
               for r in conn.execute("SELECT * FROM users ORDER BY created_at")]
    custom = {r["line_key"]: r["prize"] for r in conn.execute("SELECT * FROM line_prizes")}
    claims = [
        {"id": r["id"], "user": r["nickname"], "item": r["text"], "status": r["status"], "joker": bool(r["joker"]),
         "witness": nickname_of(conn, r["witness_id"]), "at": r["created_at"]}
        for r in conn.execute(
            """SELECT c.*, u.nickname, i.text FROM claims c JOIN users u ON u.id = c.user_id JOIN items i ON i.id = c.item_id
               WHERE c.status IN ('pending', 'confirmed') ORDER BY c.id DESC LIMIT 200""")
    ]
    cols, rows = game.grid(s)
    return {
        "settings": s,
        "cells": cols * rows,
        "grid": game.grid_plan(conn),
        "items": items,
        "invites": invites,
        "players": players,
        "lines": [{"key": l["key"], "label": l["label"], "prize": custom.get(l["key"], "")} for l in game.lines(s)],
        "claims": claims,
    }


@app.post("/api/admin/settings", dependencies=[Depends(require_ajax)])
def admin_settings(payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    before = settings(conn)
    for key, value in payload.items():
        if key in ADMIN_TEXT_SETTINGS:
            val = str(value).strip()[:200]
        elif key in ADMIN_INT_SETTINGS:
            lo, hi = ADMIN_INT_SETTINGS[key]
            try:
                val = str(min(hi, max(lo, int(value))))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} moet een getal zijn")
        elif key in ADMIN_BOOL_SETTINGS:
            val = "1" if value in (True, 1, "1", "true", "on") else "0"
        else:
            continue
        # upsert: a settings row may not exist yet when upgrading an old database
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, val),
        )
    after = settings(conn)
    # A hand-set grid means the admin knows better: switch the autoset off for this game.
    if "grid_auto" not in payload and setting_int(after, "grid_auto") == 1 and any(
        key in payload and before.get(key) != after.get(key) for key in ("grid_cols", "grid_rows")
    ):
        conn.execute("INSERT INTO settings(key, value) VALUES ('grid_auto', '0') ON CONFLICT(key) DO UPDATE SET value = '0'")
        after = settings(conn)
    if any(before.get(k) != after.get(k) for k in LAYOUT_KEYS):
        game.rebuild_everything(conn, reshuffle=False)
    grid_plan = game.auto_grid(conn, after, protect=True) if setting_int(after, "grid_auto") == 1 else None
    if before.get("frozen") != after.get("frozen"):
        add_event(conn, "announce", user["id"], text="Het spel is bevroren. Klaar is Kees." if after["frozen"] == "1" else "Het spel is weer open. Proost!")
    conn.commit()
    refresh_all()
    return {"ok": True, "grid": grid_plan}


@app.post("/api/admin/items", dependencies=[Depends(require_ajax)])
def admin_add_items(payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    texts = [t.strip()[:160] for t in str(payload.get("text", "")).splitlines() if t.strip()]
    if not texts:
        raise HTTPException(400, "Geen tekst")
    conn.executemany("INSERT INTO items(text, created_at) VALUES (?, ?)", [(t, now()) for t in texts])
    game.rebuild_everything(conn, reshuffle=False)  # fills empty cells only
    grid_plan = game.auto_grid(conn, protect=True) if setting_int(settings(conn), "grid_auto") == 1 else None
    conn.commit()
    refresh_all()
    return {"ok": True, "added": len(texts), "grid": grid_plan}


@app.put("/api/admin/items/{item_id}", dependencies=[Depends(require_ajax)])
def admin_edit_item(item_id: int, payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    text = str(payload.get("text", "")).strip()[:160]
    if not text:
        raise HTTPException(400, "Lege tekst")
    conn.execute("UPDATE items SET text = ? WHERE id = ?", (text, item_id))
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.delete("/api/admin/items/{item_id}", dependencies=[Depends(require_ajax)])
def admin_delete_item(item_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.execute("UPDATE claims SET status = 'revoked', decided_at = ? WHERE item_id = ? AND status IN ('pending','confirmed')", (now(), item_id))
    game.rebuild_everything(conn, reshuffle=False)
    grid_plan = game.auto_grid(conn, protect=True) if setting_int(settings(conn), "grid_auto") == 1 else None
    conn.commit()
    refresh_all()
    return {"ok": True, "grid": grid_plan}


@app.get("/api/admin/grid", dependencies=[Depends(require_ajax)])
def admin_grid_plan(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return game.grid_plan(conn)


@app.post("/api/admin/auto-grid", dependencies=[Depends(require_ajax)])
def admin_auto_grid(payload: dict = Body(default={}), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Set the optimal grid. Cards are never reshuffled; claimed vakjes stay where they are."""
    plan = game.auto_grid(conn, protect=not payload.get("force"))
    conn.commit()
    refresh_all()
    return {"ok": True, **plan}


@app.post("/api/admin/reshuffle", dependencies=[Depends(require_ajax)])
def admin_reshuffle(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    game.rebuild_everything(conn, reshuffle=True)
    add_event(conn, "announce", user["id"], text="Alle kaarten zijn opnieuw geschud. Wat je al had, blijft staan.")
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.post("/api/admin/prizes", dependencies=[Depends(require_ajax)])
def admin_prizes(payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    valid = {l["key"] for l in game.lines(settings(conn))}
    for key, prize in payload.items():
        if key not in valid:
            continue
        prize = str(prize).strip()[:200]
        if prize:
            conn.execute("INSERT OR REPLACE INTO line_prizes(line_key, prize) VALUES (?, ?)", (key, prize))
        else:
            conn.execute("DELETE FROM line_prizes WHERE line_key = ?", (key,))
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.post("/api/admin/invites", dependencies=[Depends(require_ajax)])
def admin_invites(payload: dict = Body(default={}), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Maak evenementcodes. Zonder `code` genereert de app een woordcode; met `code` kies je
    er zelf een (die mag je daarna voor het hele weekend aan iedereen uitdelen)."""
    codes: list[str] = []
    custom = normalise_code(str(payload.get("code", "")))
    if custom:
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,23}", custom):
            raise HTTPException(400, "Code moet 3–24 tekens zijn: letters, cijfers en streepjes")
        if conn.execute("SELECT 1 FROM invites WHERE code = ?", (custom,)).fetchone():
            raise HTTPException(400, f"Code {custom} bestaat al")
        conn.execute("INSERT INTO invites(code, created_at) VALUES (?, ?)", (custom, now()))
        codes.append(custom)
    else:
        count = max(1, min(30, int(payload.get("count", 1) or 1)))
        while len(codes) < count:
            code = new_invite_code()
            if conn.execute("SELECT 1 FROM invites WHERE code = ?", (code,)).fetchone():
                continue
            conn.execute("INSERT INTO invites(code, created_at) VALUES (?, ?)", (code, now()))
            codes.append(code)
    conn.commit()
    return {"ok": True, "codes": codes}


@app.delete("/api/admin/invites/{code}", dependencies=[Depends(require_ajax)])
def admin_delete_invite(code: str, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Code intrekken (bijv. na het evenement). Accounts die ermee zijn aangemaakt blijven bestaan."""
    cur = conn.execute("DELETE FROM invites WHERE code = ?", (normalise_code(code),))
    conn.commit()
    return {"ok": True, "removed": cur.rowcount}


@app.post("/api/profile/nickname", dependencies=[Depends(require_ajax)])
def set_my_nickname(payload: dict = Body(...), user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Iedereen mag zijn eigen nickname kiezen, ook tussendoor."""
    nickname = apply_nickname(conn, user["id"], str(payload.get("nickname", "")))
    conn.commit()
    refresh_all()
    return {"ok": True, "nickname": nickname}


@app.post("/api/admin/players/{player_id}/nickname", dependencies=[Depends(require_ajax)])
def admin_set_nickname(player_id: int, payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Admin hernoemt iemand (ook andere admins)."""
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (player_id,)).fetchone() is None:
        raise HTTPException(404, "Speler niet gevonden.")
    nickname = apply_nickname(conn, player_id, str(payload.get("nickname", "")))
    conn.commit()
    refresh_all()
    return {"ok": True, "nickname": nickname}


def apply_nickname(conn: sqlite3.Connection, user_id: int, nickname: str) -> str:
    nickname = nickname.strip()
    if not 2 <= len(nickname) <= 20:
        raise HTTPException(400, "Nickname moet tussen 2 en 20 tekens zijn.")
    if re.search(r"[<>&\"']", nickname):
        raise HTTPException(400, "Zoiets mag niet als nickname.")
    # de kolom is UNIQUE COLLATE NOCASE, dus hoofdletters doen er niet toe
    if conn.execute("SELECT 1 FROM users WHERE nickname = ? AND id <> ?", (nickname, user_id)).fetchone():
        raise HTTPException(409, "Die nickname is al bezet. Wees origineel.")
    old = conn.execute("SELECT nickname FROM users WHERE id = ?", (user_id,)).fetchone()["nickname"]
    conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (nickname, user_id))
    if old != nickname:
        add_event(conn, "renamed", user_id, old=old, nickname=nickname)
    return nickname


@app.post("/api/admin/players/{player_id}/password", dependencies=[Depends(require_ajax)])
def admin_set_password(player_id: int, payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    password = str(payload.get("password", ""))
    if len(password) < 8:
        raise HTTPException(400, "Minstens 8 tekens")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), player_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (player_id,))
    conn.commit()
    return {"ok": True}


@app.post("/api/admin/players/{player_id}/reset", dependencies=[Depends(require_ajax)])
def admin_reset_player(player_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("DELETE FROM claims WHERE user_id = ?", (player_id,))
    conn.execute("DELETE FROM bingos WHERE user_id = ?", (player_id,))
    game.build_card(conn, player_id, settings(conn), reshuffle=True)
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.delete("/api/admin/players/{player_id}", dependencies=[Depends(require_ajax)])
def admin_delete_player(player_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if player_id == user["id"]:
        raise HTTPException(400, "Jezelf verwijderen? Liever niet.")
    target = conn.execute("SELECT id, nickname, is_admin FROM users WHERE id = ?", (player_id,)).fetchone()
    if target is None:
        raise HTTPException(404, "Speler niet gevonden.")
    if target["is_admin"] and conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1").fetchone()["n"] <= 1:
        raise HTTPException(400, "Dat is de laatste admin. Maak eerst iemand anders admin.")
    drop_user(conn, player_id)
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.post("/api/admin/claims/{claim_id}/revoke", dependencies=[Depends(require_ajax)])
def admin_revoke(claim_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    c = load_claim(conn, claim_id)
    conn.execute("UPDATE claims SET status = 'revoked', decided_at = ? WHERE id = ?", (now(), claim_id))
    add_event(conn, "claim_revoked", c["user_id"], claim_id=claim_id, item=item_text(conn, c["item_id"]))
    game.refresh_bingos(conn, c["user_id"])
    conn.commit()
    refresh_all()
    return {"ok": True}


@app.post("/api/admin/announce", dependencies=[Depends(require_ajax)])
def admin_announce(bg: BackgroundTasks, payload: dict = Body(...), user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    text = str(payload.get("text", "")).strip()[:300]
    if not text:
        raise HTTPException(400, "Leeg bericht")
    add_event(conn, "announce", user["id"], text=text)
    conn.commit()
    broker.publish({"type": "announce", "text": text, "nickname": user["nickname"]})
    refresh_all()
    bg.add_task(push.send, None, f"📣 {user['nickname']}", text)
    return {"ok": True}


@app.post("/api/admin/new-game", dependencies=[Depends(require_ajax)])
def admin_new_game(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    for table in ("vetoes", "bingos", "claims", "events"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("UPDATE settings SET value = '0' WHERE key = 'frozen'")
    game.rebuild_everything(conn, reshuffle=True)
    add_event(conn, "announce", user["id"], text="Nieuw spel! Alle kaarten zijn leeg en opnieuw geschud.")
    conn.commit()
    refresh_all()
    return {"ok": True}
