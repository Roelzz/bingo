"""End-to-end flow through the HTTP API. Run: uv run pytest -q"""

import base64
import io
import os
import sqlite3
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=_tmp, ADMIN_EMAIL="roel@test.nl", ADMIN_PASSWORD="geheim123", ADMIN_NICKNAME="Roel")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import config, game  # noqa: E402
from app.auth import login_limiter  # noqa: E402
from app.main import app  # noqa: E402

H = {"X-Requested-With": "tombala"}


def login(client, email, pw):
    r = client.post("/login", data={"email": email, "password": pw}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


@pytest.fixture(scope="module")
def clients():
    with TestClient(app) as admin:
        login(admin, "roel@test.nl", "geheim123")
        codes = admin.post("/api/admin/invites", json={"count": 3}, headers=H).json()["codes"]
        players = {}
        for code, name in zip(codes, ["Bart", "Joost", "Kees"]):
            c = TestClient(app)
            r = c.post("/register", data={"code": code, "email": f"{name.lower()}@test.nl", "nickname": name, "password": "wachtwoord1"}, follow_redirects=False)
            assert r.status_code == 303, r.text
            players[name] = c
        # één evenementcode: dezelfde code mag meerdere accounts aanmaken
        r = TestClient(app).post("/register", data={"code": codes[0].lower(), "email": "x@test.nl", "nickname": "Xavier", "password": "wachtwoord1"}, follow_redirects=False)
        assert r.status_code == 303, r.text
        delete_player(admin, "Xavier")
        yield admin, players


def delete_player(admin, nickname: str) -> None:
    pid = next(p["id"] for p in admin.get("/api/admin", headers=H).json()["players"] if p["nickname"] == nickname)
    assert admin.delete(f"/api/admin/players/{pid}", headers=H).status_code == 200


def register_admin(admin, nickname: str) -> tuple[int, str]:
    """Speler aanmaken en tot admin maken (om admin-ops te kunnen testen)."""
    code = admin.post("/api/admin/invites", json={"count": 1}, headers=H).json()["codes"][0]
    email = f"{nickname.lower()}@test.nl"
    r = TestClient(app).post(
        "/register", data={"code": code, "email": email, "nickname": nickname, "password": "wachtwoord1"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    pid = next(p["id"] for p in admin.get("/api/admin", headers=H).json()["players"] if p["nickname"] == nickname)
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (pid,))
    return pid, email


def nicknames(admin) -> set[str]:
    return {p["nickname"] for p in admin.get("/api/admin", headers=H).json()["players"]}


def ids(client):
    s = client.get("/api/state").json()
    return s, {p["nickname"]: p["id"] for p in s["players"]}


def test_lines_3x4():
    s = {"grid_cols": "3", "grid_rows": "4", "line_rows": "1", "line_cols": "0", "line_diagonals": "1"}
    keys = {l["key"]: l["cells"] for l in game.lines(s)}
    assert [k for k in keys if k.startswith("r")] == ["r0", "r1", "r2", "r3"]
    assert keys["dr0"] == [0, 4, 8] and keys["dr1"] == [3, 7, 11]
    assert keys["dl0"] == [2, 4, 6] and keys["dl1"] == [5, 7, 9]
    assert not any(k.startswith("c") for k in keys)


def test_lines_square_and_wide():
    sq = {l["key"]: l["cells"] for l in game.lines({"grid_cols": "4", "grid_rows": "4", "line_rows": "0", "line_cols": "1", "line_diagonals": "1"})}
    assert sq["dr0"] == [0, 5, 10, 15] and sq["dl0"] == [3, 6, 9, 12] and len(sq) == 6
    wide = {l["key"]: l["cells"] for l in game.lines({"grid_cols": "4", "grid_rows": "3", "line_rows": "0", "line_cols": "0", "line_diagonals": "1"})}
    assert wide["dr1"] == [1, 6, 11] and wide["dl0"] == [2, 5, 8]


def test_cards_are_shuffled_per_player(clients):
    admin, players = clients
    layouts = [tuple(c["item_id"] for c in cl.get("/api/state").json()["card"]) for cl in [admin, *players.values()]]
    assert all(len(l) == 12 and None not in l for l in layouts)
    assert all(set(l) == set(layouts[0]) for l in layouts)  # 12 items for 12 cells: same items
    assert len(set(layouts)) > 1  # but in a different order


def test_claim_flow_starts_with_the_invite_code(clients):
    login_limiter.hits.clear()  # pogingen van elders in deze module niet meerekennen
    admin, _ = clients
    code = admin.post("/api/admin/invites", json={"count": 1}, headers=H).json()["codes"][0]
    guest = TestClient(app)

    # stap 1: alleen de code (kleine letters mogen) -> nu mag het account gekozen worden
    step1 = guest.post("/register", data={"code": code.lower()}, follow_redirects=False)
    assert step1.status_code == 200, step1.text
    assert "geldige code" in step1.text and f'value="{code}"' in step1.text
    assert 'name="password"' in step1.text and "readonly" in step1.text

    # onbekende code blijft op het eerste scherm hangen
    bad = guest.post("/register", data={"code": "ZWEMM-0000"})
    assert bad.status_code == 400 and "kennen we niet" in bad.text

    # half invullen is nog geen claim
    half = guest.post("/register", data={"code": code, "nickname": "Half"})
    assert half.status_code == 400 and "nickname" in half.text

    # stap 2: claim erachteraan; de code blijft daarna gewoon geldig
    claim = guest.post("/register", data={"code": code, "nickname": "Nieuw",
                                         "email": "nieuw@test.nl", "password": "wachtwoord1"},
                       follow_redirects=False)
    assert claim.status_code == 303, claim.text
    login_limiter.hits.clear()
    again = guest.post("/register", data={"code": code})
    assert again.status_code == 200 and "geldige code" in again.text
    delete_player(admin, "Nieuw")

    # wie niet ingelogd is komt op dit claimformulier uit, niet op de login
    page = TestClient(app).get("/").text
    assert "Invite code" in page and 'action="/register"' in page


def test_one_event_code_for_everyone(clients):
    login_limiter.hits.clear()
    admin, _ = clients
    code = admin.post("/api/admin/invites", json={"code": "ISTANBUL2026"}, headers=H).json()["codes"][0]
    assert code == "ISTANBUL2026"
    # kleine letters en spaties worden genegeerd, dus dat is dezelfde code -> afwijzing
    assert admin.post("/api/admin/invites", json={"code": "istanbul 2026"}, headers=H).status_code == 400
    assert admin.post("/api/admin/invites", json={"code": "ISTANBUL2026"}, headers=H).status_code == 400
    assert admin.post("/api/admin/invites", json={"code": "xx"}, headers=H).status_code == 400

    for nick, mail in (("Deel1", "deel1@test.nl"), ("Deel2", "deel2@test.nl")):
        r = TestClient(app).post("/register", data={"code": code, "nickname": nick, "email": mail,
                                                   "password": "wachtwoord1"}, follow_redirects=False)
        assert r.status_code == 303, r.text
        login_limiter.hits.clear()
    uses = next(i["uses"] for i in admin.get("/api/admin", headers=H).json()["invites"] if i["code"] == code)
    assert uses == 2

    # onbekende code deugt niet
    assert "kennen we niet" in TestClient(app).post("/register", data={"code": "BESTAAT-NIET"}).text

    # na het weekend intrekken; bestaande accounts blijven gewoon bestaan
    assert admin.delete(f"/api/admin/invites/{code}", headers=H).json()["removed"] == 1
    assert "kennen we niet" in TestClient(app).post("/register", data={"code": code}).text
    names = {p["nickname"] for p in admin.get("/api/admin", headers=H).json()["players"]}
    assert {"Deel1", "Deel2"} <= names
    for nick in ("Deel1", "Deel2"):
        delete_player(admin, nick)


def test_claim_confirm_reject_and_bingo(clients):
    admin, players = clients
    bart, joost = players["Bart"], players["Joost"]
    state, pid = ids(bart)

    # no witness, self witness, missing header
    item = state["card"][0]["item_id"]
    assert bart.post("/api/claims", json={"item_id": item}, headers=H).status_code == 400
    assert bart.post("/api/claims", json={"item_id": item, "witness_id": pid["Bart"]}, headers=H).status_code == 400
    assert bart.post("/api/claims", json={"item_id": item, "witness_id": pid["Joost"]}).status_code == 400

    # rejected claim = penalty
    r = bart.post("/api/claims", json={"item_id": item, "witness_id": pid["Joost"]}, headers=H)
    assert r.json()["status"] == "pending"
    req = joost.get("/api/state").json()["witness_requests"]
    assert len(req) == 1 and req[0]["claimant"] == "Bart"
    assert admin.post(f"/api/claims/{req[0]['claim_id']}/confirm", headers=H).status_code == 403  # not the witness
    assert joost.post(f"/api/claims/{req[0]['claim_id']}/reject", headers=H).status_code == 200
    board = {b["nickname"]: b for b in bart.get("/api/state").json()["leaderboard"]}
    assert board["Bart"]["penalties"] == 1

    # complete row 1 (positions 0,1,2) with confirmations, with a photo on the first
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), (200, 50, 50)).save(buf, "JPEG")
    photo = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    row = {c["pos"]: c["item_id"] for c in state["card"]}
    for n, pos in enumerate((0, 1, 2)):
        body = {"item_id": row[pos], "witness_id": pid["Joost"], "note": "echt waar"}
        if n == 0:
            body["photo"] = photo
        assert bart.post("/api/claims", json=body, headers=H).status_code == 200, pos
        cid = joost.get("/api/state").json()["witness_requests"][0]["claim_id"]
        assert joost.post(f"/api/claims/{cid}/confirm", headers=H).status_code == 200

    s = bart.get("/api/state").json()
    won = [l["key"] for l in s["lines"] if l["won"]]
    assert won == ["r0"]
    assert s["leaderboard"][0]["nickname"] == "Bart"
    assert any(e["kind"] == "bingo" and e["user"] == "Bart" for e in s["feed"])
    album = bart.get("/api/album").json()
    assert len(album) == 1
    img = Image.open(io.BytesIO(bart.get(album[0]["full"]).content))
    assert max(img.size) <= 1600
    assert TestClient(app).get(album[0]["full"]).status_code == 401  # photos need login

    # double claim blocked
    assert bart.post("/api/claims", json={"item_id": row[0], "witness_id": pid["Joost"]}, headers=H).status_code == 409


def test_veto_removes_bingo(clients):
    admin, players = clients
    bart, kees = players["Bart"], players["Kees"]
    s = bart.get("/api/state").json()
    claim_id = next(c["claim"]["id"] for c in s["card"] if c["pos"] == 0)
    assert players["Joost"].post(f"/api/claims/{claim_id}/veto", headers=H).status_code == 403  # was witness
    r1 = kees.post(f"/api/claims/{claim_id}/veto", headers=H).json()
    assert r1 == {"ok": True, "vetoes": 1, "vetoed": False}
    r2 = admin.post(f"/api/claims/{claim_id}/veto", headers=H).json()
    assert r2["vetoed"] is True
    s = bart.get("/api/state").json()
    assert not any(l["won"] for l in s["lines"])
    assert next(c for c in s["card"] if c["pos"] == 0)["status"] == "open"


def test_joker_and_full_card(clients):
    admin, players = clients
    kees = players["Kees"]
    s, pid = ids(kees)
    cells = s["card"]
    r = kees.post("/api/claims", json={"item_id": cells[0]["item_id"], "joker": True}, headers=H).json()
    assert r["status"] == "confirmed"
    assert kees.post("/api/claims", json={"item_id": cells[1]["item_id"], "joker": True}, headers=H).status_code == 409
    for c in cells[1:]:
        kees.post("/api/claims", json={"item_id": c["item_id"], "witness_id": pid["Roel"]}, headers=H)
    for req in admin.get("/api/state").json()["witness_requests"]:
        admin.post(f"/api/claims/{req['claim_id']}/confirm", headers=H)
    s = kees.get("/api/state").json()
    assert s["full_card_won"] is True
    assert all(l["won"] for l in s["lines"])  # 4 rows + 4 diagonals
    assert len(s["lines"]) == 8
    top = s["leaderboard"][0]
    assert top["nickname"] == "Kees" and top["full"] and top["bingos"] == 8


def test_admin_items_grid_prizes(clients):
    admin, players = clients
    bart = players["Bart"]
    before = {c["pos"]: c for c in bart.get("/api/state").json()["card"]}
    confirmed_items = {c["item_id"] for c in before.values() if c["status"] == "confirmed"}

    assert bart.get("/api/admin").status_code == 403
    admin.post("/api/admin/items", json={"text": "Een\nTwee\nDrie\nVier"}, headers=H)
    admin.post("/api/admin/settings", json={"grid_cols": 4, "grid_rows": 4, "line_cols": True}, headers=H)
    s = bart.get("/api/state").json()
    assert len(s["card"]) == 16 and all(c["item_id"] for c in s["card"])
    assert confirmed_items <= {c["item_id"] for c in s["card"] if c["status"] == "confirmed"}  # nothing lost

    admin.post("/api/admin/prizes", json={"r0": "Gratis baklava", "bogus": "x"}, headers=H)
    lines = {l["key"]: l for l in bart.get("/api/state").json()["lines"]}
    assert lines["r0"]["prize"] == "Gratis baklava"
    assert lines["r1"]["prize"] == "Een rondje van de rest"
    assert "c0" in lines and "dr0" in lines

    admin.post("/api/admin/reshuffle", headers=H)
    after = bart.get("/api/state").json()["card"]
    assert confirmed_items <= {c["item_id"] for c in after if c["status"] == "confirmed"}

    admin.post("/api/admin/settings", json={"frozen": True}, headers=H)
    open_cell = next(c for c in after if c["status"] == "open")
    assert bart.post("/api/claims", json={"item_id": open_cell["item_id"], "joker": True}, headers=H).status_code == 409
    admin.post("/api/admin/settings", json={"frozen": False}, headers=H)


def test_new_game_and_pages(clients):
    admin, _ = clients
    assert admin.post("/api/admin/new-game", headers=H).status_code == 200
    s = admin.get("/api/state").json()
    assert all(c["status"] == "open" for c in s["card"])
    assert [e["kind"] for e in s["feed"]] == ["announce"]
    assert admin.get("/").status_code == 200
    guest = TestClient(app).get("/", follow_redirects=False)
    assert guest.status_code == 200 and "Invite code" in guest.text  # startscherm = claimformulier
    assert admin.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert admin.get("/sw.js").status_code == 200


# --- bingo in every direction: horizontal, vertical, diagonal -----------------

def set_grid(admin, cols: int, rows: int) -> None:
    r = admin.post(
        "/api/admin/settings",
        json={
            "grid_cols": cols,
            "grid_rows": rows,
            "line_rows": True,      # horizontal
            "line_cols": True,      # vertical
            "line_diagonals": True,  # diagonal
        },
        headers=H,
    )
    assert r.status_code == 200, r.text


def claim_and_confirm(clients, nickname: str, positions: list[int]) -> None:
    """Claim the given cells for `nickname`, confirmed by admin as getuige."""
    admin, players = clients
    player = players[nickname]
    pid = ids(player)[1]
    by_pos = {c["pos"]: c for c in player.get("/api/state").json()["card"]}
    for pos in positions:
        cell = by_pos[pos]
        if cell["status"] != "open":
            continue  # already claimed in an earlier line
        r = player.post("/api/claims", json={"item_id": cell["item_id"], "witness_id": pid["Roel"]}, headers=H)
        assert r.status_code == 200, r.text
        cid = admin.get("/api/state").json()["witness_requests"][0]["claim_id"]
        assert admin.post(f"/api/claims/{cid}/confirm", headers=H).status_code == 200


def won_lines(player) -> dict[str, str]:
    return {l["key"]: l["label"] for l in player.get("/api/state").json()["lines"] if l["won"]}


def test_bingo_horizontal_vertical_diagonal_tall(clients):
    admin, players = clients
    set_grid(admin, 3, 4)  # 3 wide, 4 tall
    bart = players["Bart"]
    assert not won_lines(bart)

    claim_and_confirm(clients, "Bart", [0, 1, 2])
    assert won_lines(bart) == {"r0": "Rij 1"}                      # horizontal

    claim_and_confirm(clients, "Bart", [3, 6, 9])                  # pos 0 already claimed
    assert won_lines(bart) == {"r0": "Rij 1", "c0": "Kolom 1"}     # vertical

    claim_and_confirm(clients, "Bart", [4, 8])
    # dr0 = [0,4,8]; dl0 = [2,4,6] completes for free since 2 (r0) and 6 (c0) were already claimed
    assert won_lines(bart) == {
        "r0": "Rij 1", "c0": "Kolom 1", "dr0": "Diagonaal ↘ 1", "dl0": "Diagonaal ↙ 1",
    }
    assert any(e["kind"] == "bingo" and e["line"] == "Kolom 1" for e in bart.get("/api/state").json()["feed"])


def test_bingo_square_grid_all_directions(clients):
    admin, players = clients
    set_grid(admin, 4, 4)
    joost = players["Joost"]

    claim_and_confirm(clients, "Joost", [8, 9, 10, 11])            # horizontal
    claim_and_confirm(clients, "Joost", [3, 7, 11, 15])            # vertical
    claim_and_confirm(clients, "Joost", [0, 5, 10, 15])            # diagonal ↘
    claim_and_confirm(clients, "Joost", [3, 6, 9, 12])             # diagonal ↙
    assert won_lines(joost) == {
        "r2": "Rij 3", "c3": "Kolom 4", "dr0": "Diagonaal ↘", "dl0": "Diagonaal ↙",
    }


def test_bingo_wide_grid_all_directions(clients):
    admin, players = clients
    set_grid(admin, 5, 3)  # 5 wide, 3 tall: the diagonals slide sideways
    kees = players["Kees"]

    claim_and_confirm(clients, "Kees", [5, 6, 7, 8, 9])            # middle row (horizontal)
    claim_and_confirm(clients, "Kees", [0, 10])                    # Kolom 1 = [0,5,10] (vertical, 5 already claimed)
    claim_and_confirm(clients, "Kees", [12])                       # Diagonaal ↘ 1 = [0,6,12]
    claim_and_confirm(clients, "Kees", [2])                        # Diagonaal ↙ 1 = [2,6,10]; Kolom 3 = [2,7,12] falls out too
    assert won_lines(kees) == {
        "r1": "Rij 2",
        "c0": "Kolom 1",
        "c2": "Kolom 3",
        "dr0": "Diagonaal ↘ 1",
        "dl0": "Diagonaal ↙ 1",
    }


# --- autoset: keep the grid optimal without touching anybody's card ------------

def test_optimal_grid_shapes():
    og = game.optimal_grid
    assert og(12) == (3, 4) and og(15) == (3, 5) and og(16) == (4, 4)
    assert og(20) == (4, 5) and og(24) == (4, 6) and og(25) == (5, 5)
    assert og(30) == (5, 6) and og(48) == (6, 8) and og(22) == (4, 5)
    assert og(14) == (3, 4)  # 12 cells beats a 2-cell wide grid: no short win lines
    assert og(21) == (4, 5)  # long lines beat one extra vakje: not (3, 7)
    assert og(8) == (2, 4)   # hardly any vakjes: short sides beat empty cells
    assert og(1) == (2, 2)
    assert og(12, current=(4, 3)) == (4, 3)  # equal quality: leave the playing field alone
    assert og(12, current=(2, 6)) == (3, 4)  # measurably better: change it


def test_auto_grid_stays_optimal_without_reshuffling(clients):
    admin, players = clients
    bart = players["Bart"]
    bart_id = next(p["id"] for p in admin.get("/api/admin").json()["players"] if p["nickname"] == "Bart")
    admin.post(f"/api/admin/players/{bart_id}/reset", headers=H)
    # hand-set grid + autoset off: if the admin types numbers, the app stops second-guessing them
    admin.post(
        "/api/admin/settings",
        json={"grid_auto": False, "grid_cols": 3, "grid_rows": 4, "line_rows": True, "line_cols": True, "line_diagonals": True},
        headers=H,
    )
    # make sure there are more vakjes than cells, so 3×4 is provably not the best fit anymore
    missing = max(0, 16 - admin.get("/api/admin").json()["grid"]["items"])
    if missing:
        admin.post("/api/admin/items", json={"text": "\n".join(f"Vak {n}" for n in range(missing))}, headers=H)

    st = admin.get("/api/admin").json()
    assert st["settings"]["grid_auto"] == "0"
    assert st["grid"]["cells"] == 12 and st["grid"]["optimal"] is False
    assert st["grid"]["suggestion"] == {"cols": 4, "rows": 4, "cells": 16}

    claim_and_confirm(clients, "Bart", [0, 3, 6, 9])  # Kolom 1
    assert won_lines(bart) == {"c0": "Kolom 1"}
    claimed = {0, 3, 6, 9}
    before = {c["pos"]: c["item_id"] for c in bart.get("/api/state").json()["card"]}

    # turning the autoset on must not rob Bart of a prize he already won
    r = admin.post("/api/admin/settings", json={"grid_auto": True}, headers=H).json()
    assert r["grid"]["applied"] is False and "bingo" in r["grid"]["reason"]
    assert admin.get("/api/state").json()["settings"]["cols"] == 3
    assert won_lines(bart) == {"c0": "Kolom 1"}

    # the admin button sees the same thing and says so
    r = admin.post("/api/admin/auto-grid", headers=H).json()
    assert r["applied"] is False and any(w["player"] == "Bart" for w in r["would_lose"])

    # force it: grid changes, claimed vakjes keep their exact spot, cards are not reshuffled
    r = admin.post("/api/admin/auto-grid", json={"force": True}, headers=H).json()
    assert r["applied"] is True and r["suggestion"] == {"cols": 4, "rows": 4, "cells": 16}
    mid = {c["pos"]: c for c in bart.get("/api/state").json()["card"]}
    assert len(mid) == 16 and all(mid[p]["item_id"] == before[p] for p in before)
    assert {p for p, c in mid.items() if c["status"] == "confirmed"} == claimed
    assert won_lines(bart) == {}  # and this is exactly why the protected version refuses

    # from here on the grid follows the number of vakjes by itself
    admin.post("/api/admin/new-game", headers=H)  # clean slate: no prizes left to protect
    fresh = {c["pos"]: c["item_id"] for c in bart.get("/api/state").json()["card"]}
    r = admin.post("/api/admin/items", json={"text": "\n".join(f"Nog {n}" for n in range(6))}, headers=H).json()
    assert r["grid"]["applied"] is True, r["grid"]
    s = admin.get("/api/state").json()["settings"]
    assert (s["cols"], s["rows"]) == (4, 5)  # 22 vakjes → 4×5, 2 als reserve
    after = {c["pos"]: c for c in bart.get("/api/state").json()["card"]}
    assert len(after) == 20 and all(after[p]["item_id"] == fresh[p] for p in fresh)


def test_hand_setting_the_grid_steps_the_autoset_aside(clients):
    admin, _ = clients
    assert admin.post("/api/admin/settings", json={"grid_auto": True}, headers=H).json()["grid"]["optimal"] is True
    admin.post("/api/admin/settings", json={"grid_cols": 2, "grid_rows": 4}, headers=H)
    st = admin.get("/api/admin").json()
    assert (st["grid"]["cols"], st["grid"]["rows"]) == (2, 4)
    assert st["settings"]["grid_auto"] == "0"


def test_everyone_can_change_their_nickname(clients):
    admin, players = clients
    kees = players["Kees"]
    r = kees.post("/api/profile/nickname", json={"nickname": "Keesje"}, headers=H)
    assert r.status_code == 200 and r.json()["nickname"] == "Keesje", r.text
    assert "Keesje" in nicknames(admin) and "Kees" not in nicknames(admin)
    # uniek, en hoofdletters doen er niet toe
    assert kees.post("/api/profile/nickname", json={"nickname": "bart"}, headers=H).status_code == 409
    assert kees.post("/api/profile/nickname", json={"nickname": "K"}, headers=H).status_code == 400
    assert kees.post("/api/profile/nickname", json={"nickname": "<b>Bas</b>"}, headers=H).status_code == 400
    # de admin mag iemand anders hernoemen
    pid = next(p["id"] for p in admin.get("/api/admin", headers=H).json()["players"] if p["nickname"] == "Keesje")
    assert admin.post(f"/api/admin/players/{pid}/nickname", json={"nickname": "Kees"}, headers=H).status_code == 200
    assert "Kees" in nicknames(admin)
    # en een admin mag zichzelf hernoemen
    me = next(p["id"] for p in admin.get("/api/admin", headers=H).json()["players"] if p["email"] == "roel@test.nl")
    assert admin.post(f"/api/admin/players/{me}/nickname", json={"nickname": "Baas"}, headers=H).status_code == 200
    assert admin.post(f"/api/admin/players/{me}/nickname", json={"nickname": "Roel"}, headers=H).status_code == 200


def test_admin_accounts_can_be_removed_but_yourself_never(clients):
    admin, _ = clients
    pid, _ = register_admin(admin, "Wilma")
    assert admin.delete(f"/api/admin/players/{pid}", headers=H).status_code == 200
    assert "Wilma" not in nicknames(admin)
    me = next(p["id"] for p in admin.get("/api/admin", headers=H).json()["players"] if p["email"] == "roel@test.nl")
    r = admin.delete(f"/api/admin/players/{me}", headers=H)
    assert r.status_code == 400 and "Jezelf" in r.json()["detail"], r.text
    assert admin.delete("/api/admin/players/999999", headers=H).status_code == 404


def test_prune_admins_env_clears_legacy_admins(clients):
    from app.auth import prune_admins

    admin, _ = clients
    pid, email = register_admin(admin, "Prunella")
    with sqlite3.connect(config.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        # de env-admin en gewone spelers blijven staan, de legacy admin gaat weg
        assert prune_admins(c, "roel@test.nl, bart@test.nl, " + email.upper()) == [email]
    players = admin.get("/api/admin", headers=H).json()["players"]
    assert "Prunella" not in {p["nickname"] for p in players}
    assert {"roel@test.nl", "bart@test.nl"} <= {p["email"] for p in players}
