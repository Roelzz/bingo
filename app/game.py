"""Bingo rules: shuffled cards, winning lines, bingo bookkeeping and prizes."""

import random
import sqlite3

from .db import add_event, now, setting_int, settings

ACTIVE_STATUSES = ("pending", "confirmed")

# Same bounds the admin form enforces (main.ADMIN_INT_SETTINGS).
GRID_COLS_RANGE = (2, 6)
GRID_ROWS_RANGE = (2, 8)


def grid(s: dict[str, str]) -> tuple[int, int]:
    return setting_int(s, "grid_cols"), setting_int(s, "grid_rows")


def optimal_grid(items: int, current: tuple[int, int] | None = None, min_side: int = 3) -> tuple[int, int]:
    """The shape that plays best for `items` vakjes.

    Preference order:
      1. lines as long as possible: sides of at least 3, and the longest side that fits
         (a 3×7 grid uses more vakjes than a 4×5, but bingo on 3 vakjes is cheap)
      2. as many vakjes on the card as possible, so few vakjes go unused
      3. as square as possible, 4. portrait first (phones are tall)
      5. and on an exact tie: keep the shape people are already playing on, so an autoset
         never moves a card that is already fine.
    """
    items = max(1, items)
    universe = [
        (c, r)
        for c in range(GRID_COLS_RANGE[0], GRID_COLS_RANGE[1] + 1)
        for r in range(GRID_ROWS_RANGE[0], GRID_ROWS_RANGE[1] + 1)
    ]
    candidates = [cr for cr in universe if cr[0] * cr[1] <= items and min(cr) >= min_side]
    if not candidates:  # hardly any vakjes: allow short sides rather than empty cells
        candidates = [cr for cr in universe if cr[0] * cr[1] <= items] or [(GRID_COLS_RANGE[0], GRID_ROWS_RANGE[0])]

    def score(cr: tuple[int, int]) -> tuple:
        c, r = cr
        return (
            -min(c, r),
            -(c * r),
            abs(c - r),
            0 if cr == current else 1,  # ties go to the shape people are already playing on
            0 if r >= c else 1,
            c,
        )

    return min(candidates, key=score)


def claimed_cells_needed(conn: sqlite3.Connection) -> int:
    """Most claims (pending + confirmed) any single player has: the smallest grid that loses nothing."""
    row = conn.execute(
        """SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM claims WHERE status IN (?, ?) GROUP BY user_id)""",
        ACTIVE_STATUSES,
    ).fetchone()
    return int(row[0] or 0)


def lines(s: dict[str, str]) -> list[dict]:
    """All winning lines for the current grid. Positions are row * cols + col."""
    cols, rows = grid(s)
    out: list[dict] = []
    if setting_int(s, "line_rows") and cols >= 2:
        for r in range(rows):
            out.append({"key": f"r{r}", "label": f"Rij {r + 1}", "cells": [r * cols + c for c in range(cols)]})
    if setting_int(s, "line_cols") and rows >= 2:
        for c in range(cols):
            out.append({"key": f"c{c}", "label": f"Kolom {c + 1}", "cells": [r * cols + c for r in range(rows)]})
    length = min(cols, rows)
    if setting_int(s, "line_diagonals") and length >= 2:
        down_right, down_left = [], []
        if rows >= cols:  # tall or square: slide the diagonals down
            for r0 in range(rows - cols + 1):
                down_right.append([(r0 + i) * cols + i for i in range(length)])
                down_left.append([(r0 + i) * cols + (cols - 1 - i) for i in range(length)])
        else:  # wide: slide them sideways
            for c0 in range(cols - rows + 1):
                down_right.append([i * cols + c0 + i for i in range(length)])
                down_left.append([i * cols + c0 + (rows - 1 - i) for i in range(length)])
        for arrow, key, group in (("↘", "dr", down_right), ("↙", "dl", down_left)):
            for n, cells in enumerate(group):
                suffix = f" {n + 1}" if len(group) > 1 else ""
                out.append({"key": f"{key}{n}", "label": f"Diagonaal {arrow}{suffix}", "cells": cells})
    return out


def prizes(conn: sqlite3.Connection, s: dict[str, str]) -> dict[str, str]:
    custom = {r["line_key"]: r["prize"] for r in conn.execute("SELECT line_key, prize FROM line_prizes")}
    default = s.get("default_line_prize", "")
    result = {line["key"]: custom.get(line["key"]) or default for line in lines(s)}
    result["full"] = s.get("full_card_prize", "")
    return result


def line_label(s: dict[str, str], key: str) -> str:
    if key == "full":
        return "Volle kaart"
    for line in lines(s):
        if line["key"] == key:
            return line["label"]
    return key


def active_item_ids(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM items WHERE active = 1 ORDER BY id")]


def claimed_item_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT item_id FROM claims WHERE user_id = ? AND status IN ('pending', 'confirmed')", (user_id,)
    )
    return {r[0] for r in rows}


def build_card(conn: sqlite3.Connection, user_id: int, s: dict[str, str], reshuffle: bool = False) -> None:
    """Create, repair or reshuffle a player's card.

    Cells that are claimed (pending or confirmed) keep their item, and keep their spot when
    possible, so a reshuffle or grid change never throws away what someone already did.
    """
    cols, rows = grid(s)
    cells = cols * rows
    current = {r["position"]: r["item_id"] for r in conn.execute(
        "SELECT position, item_id FROM cards WHERE user_id = ?", (user_id,))}
    active = set(active_item_ids(conn))
    claimed = claimed_item_ids(conn, user_id) & active

    if reshuffle:
        keep = {p: i for p, i in current.items() if p < cells and i in claimed}
    else:
        keep = {p: i for p, i in current.items() if p < cells and i in active}
    placed = set(keep.values())

    free = [p for p in range(cells) if p not in keep]
    random.shuffle(free)
    for item in sorted(claimed - placed):  # claimed items pushed off the grid get a new spot
        if not free:
            break
        keep[free.pop()] = item
        placed.add(item)

    pool = list(active - placed)
    random.shuffle(pool)
    for p in free:
        keep[p] = pool.pop() if pool else None

    new = {p: keep.get(p) for p in range(cells)}
    if new == current:
        return
    conn.execute("DELETE FROM cards WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO cards(user_id, position, item_id) VALUES (?, ?, ?)",
        [(user_id, p, i) for p, i in new.items()],
    )


def card(conn: sqlite3.Connection, user_id: int, s: dict[str, str]) -> dict[int, int | None]:
    cols, rows = grid(s)
    return {r["position"]: r["item_id"] for r in conn.execute(
        "SELECT position, item_id FROM cards WHERE user_id = ? AND position < ? ORDER BY position",
        (user_id, cols * rows))}


def completed_lines(conn: sqlite3.Connection, user_id: int, the_card: dict[int, int | None], s: dict[str, str]) -> set[str]:
    """Which line keys this card has completed under settings s (no writes)."""
    confirmed = {r[0] for r in conn.execute(
        "SELECT item_id FROM claims WHERE user_id = ? AND status = 'confirmed'", (user_id,))}

    def done(cells: list[int]) -> bool:
        return all(the_card.get(p) is not None and the_card.get(p) in confirmed for p in cells)

    complete = {line["key"] for line in lines(s) if done(line["cells"])}
    cols, rows = grid(s)
    if the_card and done(list(range(cols * rows))):
        complete.add("full")
    return complete


def refresh_bingos(conn: sqlite3.Connection, user_id: int, s: dict[str, str] | None = None) -> list[dict]:
    """Recalculate a player's completed lines. Returns newly won lines (for announcements)."""
    s = s or settings(conn)
    the_card = card(conn, user_id, s)
    complete = completed_lines(conn, user_id, the_card, s)

    existing = {r[0] for r in conn.execute("SELECT line_key FROM bingos WHERE user_id = ?", (user_id,))}
    for key in existing - complete:
        conn.execute("DELETE FROM bingos WHERE user_id = ? AND line_key = ?", (user_id, key))

    prize_map = prizes(conn, s)
    won = []
    for key in sorted(complete - existing, key=lambda k: (k == "full", k)):
        conn.execute("INSERT INTO bingos(user_id, line_key, achieved_at) VALUES (?, ?, ?)", (user_id, key, now()))
        entry = {"key": key, "label": line_label(s, key), "prize": prize_map.get(key, "")}
        add_event(conn, "full_card" if key == "full" else "bingo", user_id, line=entry["label"], prize=entry["prize"])
        won.append(entry)
    return won


def rebuild_everything(conn: sqlite3.Connection, reshuffle: bool) -> None:
    s = settings(conn)
    for (uid,) in conn.execute("SELECT id FROM users").fetchall():
        build_card(conn, uid, s, reshuffle=reshuffle)
        refresh_bingos(conn, uid, s)


# ------------------------------------------------------------------ auto grid

def grid_plan(conn: sqlite3.Connection, s: dict[str, str] | None = None) -> dict:
    """Current grid vs the optimal one for the number of active vakjes, plus what changing would cost.

    Nothing is written: this is also what the admin panel shows before anyone clicks anything.
    """
    s = s or settings(conn)
    cols, rows = grid(s)
    items = len(active_item_ids(conn))
    best = optimal_grid(items, current=(cols, rows))
    plan = {
        "cols": cols, "rows": rows, "cells": cols * rows,
        "items": items, "spare_items": items - cols * rows,
        "suggestion": {"cols": best[0], "rows": best[1], "cells": best[0] * best[1]},
        "optimal": best == (cols, rows),
        "auto": setting_int(s, "grid_auto") == 1,
        "min_cells_for_claims": claimed_cells_needed(conn),
        "would_lose": [],
    }
    if best == (cols, rows):
        return plan
    candidate = {**s, "grid_cols": str(best[0]), "grid_rows": str(best[1])}
    for r in conn.execute("SELECT id, nickname FROM users"):
        uid = r["id"]
        won = {row[0] for row in conn.execute("SELECT line_key FROM bingos WHERE user_id = ?", (uid,))}
        if not won:
            continue
        # cards keep their positions (build_card only fills/repairs), so this predicts the damage
        kept = card(conn, uid, candidate)
        for key in sorted(won - completed_lines(conn, uid, kept, candidate)):
            plan["would_lose"].append({"player": r["nickname"], "line": line_label(s, key)})
    return plan


def set_grid(conn: sqlite3.Connection, cols: int, rows: int) -> None:
    conn.execute("UPDATE settings SET value = ? WHERE key = 'grid_cols'", (str(cols),))
    conn.execute("UPDATE settings SET value = ? WHERE key = 'grid_rows'", (str(rows),))


def auto_grid(conn: sqlite3.Connection, s: dict[str, str] | None = None, protect: bool = True) -> dict:
    """Set the optimal grid without reshuffling anybody's card.

    protect=True refuses to move a grid that would take a bingo away from a player; that keeps a
    live game safe when vakjes are added or removed mid-play. Returns the plan plus what happened.
    """
    s = s or settings(conn)
    plan = grid_plan(conn, s)
    if plan["optimal"]:
        return {**plan, "applied": False, "reason": "Raster staat al optimaal"}
    if protect and plan["would_lose"]:
        return {**plan, "applied": False, "reason": "Raster zou bestaande bingo's kosten"}
    best = plan["suggestion"]
    set_grid(conn, best["cols"], best["rows"])
    rebuild_everything(conn, reshuffle=False)  # never reshuffles: claimed vakjes stay put
    applied_plan = grid_plan(conn, settings(conn))
    # the caller wants to see the situation after the move, plus what it cost if we forced it
    return {**applied_plan, "applied": True, "reason": "", "would_lose": plan["would_lose"], "applied_grid": best}
