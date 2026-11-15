#!/usr/bin/env python3
"""Bingo admin CLI — één commando voor elke admin-actie in Speerpunt Bingo.

Alleen Python-standaardbiblioteek; werkt tegen elke draaiende instantie:
    python3 ~/.hermes/skills/bingo-admin/scripts/bingo.py overview

Config (eerst gevonden wint): omgevingsvariabelen BINGO_URL / BINGO_ADMIN_EMAIL /
BINGO_ADMIN_PASSWORD, dan ~/.hermes/mcp-tokens/bingo.env (KEY=VALUE, chmod 600),
dan --url/--email/--password. De sessie-token wordt gecachet in
~/.hermes/mcp-tokens/bingo/<host>.json (0600) om de login-ratelimiter
(10 pogingen / 5 min per IP+e-mail) te sparen.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COOKIE = "tombala_session"
DEFAULT_URL = "https://bingo.speerpunt.roelschenk.com"
TOKEN_DIR = Path(os.environ.get("BINGO_TOKEN_DIR", "~/.hermes/mcp-tokens")).expanduser()
ENV_FILE = TOKEN_DIR / "bingo.env"
CACHE_DIR = TOKEN_DIR / "bingo"
LINE_KEYS_HELP = "r0..r{rows-1}, c0..c{cols-1}, dr0, dr1, dl0, dl1, full"


def read_env_file(path: Path) -> dict:
    """KEY=VALUE met optionele quotes/#commentaren; stil als het bestand ontbreekt."""
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


# ------------------------------------------------------------------ transport

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


class Bingo:
    def __init__(self, url: str, email: str, password: str | None, timeout: int = 30, quiet: bool = False):
        self.url = url.rstrip("/")
        self.email = email
        self.password = password
        self.timeout = timeout
        self.quiet = quiet
        self.host = urllib.parse.urlsplit(self.url).netloc
        self.cache = CACHE_DIR / f"{self.host}.json"
        self.token = self._load_token()

    def _load_token(self) -> str | None:
        try:
            return json.loads(self.cache.read_text()).get("token")
        except Exception:
            return None

    def _save_token(self, token: str) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps({"url": self.url, "email": self.email, "token": token}, indent=2))
        os.chmod(self.cache, 0o600)

    def _request(self, method: str, path: str, body=None, form=None, raw: bool = False):
        data = None
        headers = {"Accept": "application/json", "X-Requested-With": "tombala"}  # CSRF-bewaking van de app
        if self.token:
            headers["Cookie"] = f"{COOKIE}={self.token}"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(req, timeout=self.timeout)
            payload, status = resp.read(), resp.status
            headers_out = resp.headers
        except urllib.error.HTTPError as exc:
            payload, status, headers_out = exc.read(), exc.code, exc.headers
        except urllib.error.URLError as exc:
            raise ApiError(0, f"{self.url} niet bereikbaar: {exc.reason}") from None
        cookie = headers_out.get("set-cookie") or ""
        if cookie and COOKIE in cookie:
            self._set_token_from(cookie)
        if raw:
            return payload, status
        if status in (301, 302, 303):
            return None, status
        if not payload:
            return {}, status
        try:
            return json.loads(payload), status
        except json.JSONDecodeError:
            return {"_text": payload.decode("utf-8", "replace")}, status

    def _set_token_from(self, header: str) -> None:
        for part in header.split(";"):
            if part.strip().startswith(f"{COOKIE}="):
                token = part.strip()[len(COOKIE) + 1:]
                if token:
                    self.token = token
                    self._save_token(token)
                return

    def login(self) -> None:
        if not self.password:
            raise ApiError(0, f"geen wachtwoord bekend voor {self.email} — zet BINGO_ADMIN_PASSWORD in "
                              f"{ENV_FILE}")
        self.token = None  # geforceerd opnieuw inloggen, anders blijft een verlopen token staan
        _, status = self._request("POST", "/login", form={"email": self.email, "password": self.password})
        if not self.token:
            hint = "te vaak geprobeerd (429): wacht 5 minuten" if status == 429 else "e-mail/wachtwoord klopt niet"
            raise ApiError(status, f"inloggen mislukt: {hint}")
        if not self.quiet:
            print(f"· ingelogd op {self.url} als {self.email}", file=sys.stderr)

    def call(self, method: str, path: str, body=None, retried: bool = False):
        result, status = self._request(method, path, body=body)
        if status in (401, 403) and not retried:
            self.login()
            return self.call(method, path, body, retried=True)
        if status >= 400:
            raise ApiError(status, _detail(result))
        return result

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, body=None):
        return self.call("POST", path, body if body is not None else {})

    def get_bytes(self, path: str) -> bytes:
        payload, status = self._request("GET", path, raw=True)
        if status in (401, 403):
            self.login()
            payload, status = self._request("GET", path, raw=True)
        if status >= 400:
            raise ApiError(status, "ophalen mislukt")
        return payload

class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}" if status else detail)


def _detail(result) -> str:
    if isinstance(result, dict):
        value = result.get("detail")
        if value:
            return value if isinstance(value, str) else json.dumps(value)
        text = result.get("_text") or ""
        import html
        import re as _re
        for match in _re.finditer(r"class=\"error\"[^>]*>([^<]+)", text):
            return html.unescape(match.group(1)).strip()
        flat = html.unescape(_re.sub(r"<[^>]+>", " ", text))
        flat = " ".join(flat.split())
        return flat[:200] or "fout"
    return "fout"


# ------------------------------------------------------------------ helpers

def typed(value: str):
    v = value.strip()
    low = v.lower()
    if low in ("true", "on", "ja", "aan", "1", "yes"):
        return True
    if low in ("false", "off", "nee", "uit", "0", "no"):
        return False
    if v.isdigit():
        return int(v)
    return v


def table(rows: list[dict], cols: list[tuple[str, str, int]]) -> str:
    out = ["  ".join(f"{h:<{w}}" for _, h, w in cols)]
    for r in rows:
        out.append("  ".join(f"{str(r.get(k, ''))[:w]:<{w}}" for k, _, w in cols))
    return "\n".join(out)


def grid_line(plan: dict) -> str:
    s = plan["suggestion"]
    spare = plan["spare_items"]
    reserve = f"{spare} op reserve" if spare >= 0 else f"{-spare} lege plekken"
    state = "optimaal" if plan["optimal"] else f"→ {s['cols']}×{s['rows']} ({s['cells']} vakjes op de kaart)"
    return (f"rooster {plan['cols']}×{plan['rows']} ({plan['cells']} vakjes, {plan['items']} items, "
            f"{reserve}) · autoset {'aan' if plan['auto'] else 'uit'} · {state}")


def show_plan_extras(plan: dict) -> None:
    for w in plan.get("would_lose", []):
        print(f"  ⚠ {w['player']} verliest: {w['label']}")
    if plan.get("min_cells_for_claims"):
        print(f"  minimaal {plan['min_cells_for_claims']} vakjes nodig i.v.m. bestaande claims")


def confirm(args, what: str) -> None:
    if not args.yes:
        raise SystemExit(f"· {what} is niet omkeerbaar — geef --yes als je het echt weet")


# ------------------------------------------------------------------ commands

def cmd_health(b: Bingo, args):
    data = b.get("/healthz")
    print(f"{b.url} → {json.dumps(data)}")


def cmd_whoami(b: Bingo, args):
    st = b.get("/api/state")
    me = st["me"]
    print(f"{me['nickname']} (id {me['id']}, admin={me['is_admin']}) · {b.url}")


def cmd_overview(b: Bingo, args):
    adm = b.get("/api/admin")
    st = b.get("/api/state")
    s = adm["settings"]
    pending = [c for c in adm["claims"] if c["status"] == "pending"]
    confirmed = [c for c in adm["claims"] if c["status"] == "confirmed"]
    board = st["leaderboard"]
    if args.json:
        print(json.dumps({"admin": adm, "state": st}, indent=2, ensure_ascii=False))
        return
    print(f"=== {s['title']} ===")
    print(f"{s['kicker']} · {s['subtitle']}")
    print(("⚠ SPEL BEVROREN" if s["frozen"] == "1" else "spel staat open")
          + f" · jokers {s['jokers_per_player']}/persoon · veto-drempel {s['veto_threshold']}"
          + f" · getuigenlimiet {s['witness_limit']}")
    print(grid_line(adm["grid"]))
    directions = [n for n, k in (("rijen", "line_rows"), ("kolommen", "line_cols"), ("diagonalen", "line_diagonals"))
                  if s[k] == "1"]
    print(f"lijnen: {', '.join(directions) or 'geen'} · {len(adm['lines'])} winlijnen")
    print(f"spelers: {len(adm['players'])} · items: {len(adm['items'])} · claims: {len(confirmed)} bevestigd, "
          f"{len(pending)} in afwachting · open uitnodigingen: "
          f"{sum(1 for i in adm['invites'] if not i['used_at'])}")
    if pending:
        print("\nclaims die op bevestiging wachten:")
        print(table(pending, [("id", "id", 4), ("user", "speler", 16), ("item", "vakje", 44), ("witness", "getuige", 14)]))
    if board:
        print("\nstand:")
        print(table(board, [("nickname", "speler", 16), ("bingos", "bingo's", 8), ("full", "vol", 5),
                            ("cells", "vakjes", 7), ("penalties", "straf", 6), ("witnessed", "getuigd", 8)]))


def cmd_state(b: Bingo, args):
    print(json.dumps(b.get("/api/state"), indent=2, ensure_ascii=False))


def cmd_board(b: Bingo, args):
    board = b.get("/api/state")["leaderboard"]
    print(table(board, [("nickname", "speler", 18), ("bingos", "bingo's", 8), ("full", "volle kaart", 12),
                        ("cells", "vakjes", 7), ("penalties", "straf", 6), ("witnessed", "getuigd", 8),
                        ("prizes", "prijzen", 40)]))


def cmd_feed(b: Bingo, args):
    feed = b.get("/api/state")["feed"][: args.limit]
    for e in reversed(feed):
        data = {k: v for k, v in e.items() if k not in ("id", "kind", "user", "user_id", "color", "at", "photo",
                                                        "status", "vetoes", "i_vetoed", "can_veto")}
        extra = " · ".join(f"{k}={v}" for k, v in data.items() if v not in (None, "", "None"))
        veto = f" [{e['vetoes']}× veto]" if e.get("vetoes") else ""
        print(f"{e['at']}  {e['kind']:<16} {e.get('user', ''):<16}{veto} {extra}")


def cmd_album(b: Bingo, args):
    rows = b.get("/api/album")
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for r in rows:
        print(f"{r['at']}  {r['nickname']:<16} {r['status']:<10} {r['full']}  ← {r['text'][:48]}")
    if args.out_dir:
        out = Path(args.out_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        for r in rows:
            name = r["full"].rsplit("/", 1)[-1]
            (out / name).write_bytes(b.get_bytes(r["full"]))
            print(f"· {r['full']} → {out / name}")


def cmd_get(b: Bingo, args):
    print(json.dumps(b.get(args.path), indent=2, ensure_ascii=False))


def cmd_download(b: Bingo, args):
    data = b.get_bytes(args.path)
    Path(args.out).write_bytes(data)
    print(f"{args.path} → {args.out} ({len(data)} bytes)")


def cmd_grid(b: Bingo, args):
    plan = b.get("/api/admin/grid")
    print(grid_line(plan))
    show_plan_extras(plan)


def cmd_grid_set(b: Bingo, args):
    if not (2 <= args.cols <= 6 and 2 <= args.rows <= 8):
        raise SystemExit(f"· kolommen 2–6 en rijen 2–8 (de app clant anders stil in) — nu: {args.cols}×{args.rows}")
    b.post("/api/admin/settings", {"grid_cols": args.cols, "grid_rows": args.rows})
    plan = b.get("/api/admin/grid")
    print("handmatig gezet; autoset staat nu uit")
    print(grid_line(plan))
    show_plan_extras(plan)


def cmd_grid_auto(b: Bingo, args):
    out = b.post("/api/admin/settings", {"grid_auto": args.state == "on"})
    plan = out.get("grid")
    print(f"autoset {args.state}")
    if plan:
        print(grid_line(plan))
        show_plan_extras(plan)


def cmd_grid_optimize(b: Bingo, args):
    out = b.post("/api/admin/auto-grid", {"force": args.force})
    if out.get("applied"):
        print(f"rooster staat nu optimaal: {out['cols']}×{out['rows']} (kaarten zijn niet hergeschud)")
    else:
        print(f"niets gedaan: {out.get('reason') or 'rooster is al optimaal'}")
        show_plan_extras(out)
        if not args.force:
            print("· met force toch zetten: grid-optimize --force")


def cmd_lines(b: Bingo, args):
    payload = {}
    for name, key in (("rijen", "line_rows"), ("kolommen", "line_cols"), ("diagonalen", "line_diagonals")):
        value = getattr(args, name)
        if value:
            payload[key] = value == "on"
    if not payload:
        s = b.get("/api/admin")["settings"]
        for n, k in (("rijen", "line_rows"), ("kolommen", "line_cols"), ("diagonalen", "line_diagonals")):
            print(f"{n:<12} {'aan' if s[k] == '1' else 'uit'}")
        return
    b.post("/api/admin/settings", payload)
    print("· " + ", ".join(f"{k}={v}" for k, v in payload.items()))


def cmd_settings(b: Bingo, args):
    if not args.pairs:
        s = b.get("/api/admin")["settings"]
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return
    payload = {}
    for pair in args.pairs:
        if "=" not in pair:
            raise SystemExit(f"· {pair} is niet key=waarde")
        key, value = pair.split("=", 1)
        payload[key] = typed(value)
    out = b.post("/api/admin/settings", payload)
    print("· instellingen opgeslagen: " + ", ".join(f"{k}={v}" for k, v in payload.items()))
    if out.get("grid"):
        print(grid_line(out["grid"]))
        show_plan_extras(out["grid"])


def cmd_freeze(b: Bingo, args):
    state = args.state == "on"
    b.post("/api/admin/settings", {"frozen": state})
    print("· spel bevroren ( Klaar is Kees )" if state else "· spel weer open")


def cmd_items(b: Bingo, args):
    items = b.get("/api/admin")["items"]
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return
    print(table(items, [("id", "id", 4), ("on_cards", "op kaarten", 10), ("confirmed", "bevestigd", 10),
                        ("text", "tekst", 76)]))


def cmd_items_add(b: Bingo, args):
    lines: list[str] = []
    if args.file:
        lines += Path(args.file).read_text(encoding="utf-8").splitlines()
    if not args.text and not args.file and not sys.stdin.isatty():
        lines += sys.stdin.read().splitlines()
    lines += args.text
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        raise SystemExit("· geen vakjes gegeven (tekst, --file of stdin)")
    out = b.post("/api/admin/items", {"text": "\n".join(lines)})
    print(f"· {out['added']} vakjes toegevoegd")
    if out.get("grid"):
        print(grid_line(out["grid"]))
        show_plan_extras(out["grid"])


def cmd_item_set(b: Bingo, args):
    b.call("PUT", f"/api/admin/items/{args.id}", {"text": args.text})
    print(f"· vakje {args.id} hernoemd")


def cmd_item_del(b: Bingo, args):
    confirm(args, f"vakje {args.id} verwijderen")
    out = b.call("DELETE", f"/api/admin/items/{args.id}")
    print(f"· vakje {args.id} verwijderd (claims ervan zijn ingetrokken)")
    if out.get("grid"):
        print(grid_line(out["grid"]))


def cmd_prizes(b: Bingo, args):
    adm = b.get("/api/admin")
    if not args.pairs:
        for line in adm["lines"]:
            print(f"{line['key']:<6} {line['label']:<28} {line['prize'] or '(standaard)'}")
        print(f"{'full':<6} {'Volle kaart':<28} {adm['settings']['full_card_prize'] or '(standaard)'}")
        return
    payload = {}
    full_prize = None
    for pair in args.pairs:
        if "=" not in pair:
            raise SystemExit(f"· {pair} is niet key=tekst")
        key, value = pair.split("=", 1)
        key, value = key.strip(), value.strip()
        if key == "full":  # de volle-kaart-prijs is een instelling, geen lijn
            full_prize = value
            continue
        if key not in {l["key"] for l in adm["lines"]}:
            raise SystemExit(f"· onbekende lijn {key} — geldig: {LINE_KEYS_HELP}")
        payload[key] = value
    if payload:
        b.post("/api/admin/prizes", payload)
    if full_prize is not None:
        b.post("/api/admin/settings", {"full_card_prize": full_prize})
    print("· prijzen opgeslagen (lege tekst = terug naar standaard)")


def cmd_players(b: Bingo, args):
    players = b.get("/api/admin")["players"]
    if args.json:
        print(json.dumps(players, indent=2, ensure_ascii=False))
        return
    print(table(players, [("id", "id", 4), ("nickname", "bijnaam", 18), ("email", "e-mail", 32),
                          ("is_admin", "admin", 6), ("created_at", "geregistreerd", 26)]))


def cmd_player_pass(b: Bingo, args):
    password = args.password or getpass.getpass("nieuw wachtwoord: ")
    if len(password) < 8:
        raise SystemExit("· minstens 8 tekens")
    b.post(f"/api/admin/players/{args.id}/password", {"password": password})
    print(f"· wachtwoord speler {args.id} veranderd (al hun sessies zijn uitgelogd)")


def cmd_player_reset(b: Bingo, args):
    confirm(args, f"speler {args.id} resetten (claims + bingo's weg, nieuwe kaart)")
    b.post(f"/api/admin/players/{args.id}/reset")
    print(f"· speler {args.id}: claims en bingo's weg, verse kaart")


def cmd_player_del(b: Bingo, args):
    confirm(args, f"speler {args.id} verwijderen")
    b.call("DELETE", f"/api/admin/players/{args.id}")
    print(f"· speler {args.id} verwijderd")


def cmd_invites(b: Bingo, args):
    if args.code:
        out = b.post("/api/admin/invites", {"code": args.code})
        for c in out["codes"]:
            print(f"· evenementcode {c}")
            print(f"  {b.url}/register?code={c}")
        print("· één code per evenement is genoeg; iedereen maakt ermee een eigen account")
        return
    if args.new:
        out = b.post("/api/admin/invites", {"count": args.new})
        print("nieuwe losse codes (voor de rest is één evenementcode genoeg):")
        for c in out["codes"]:
            print(f"  {c:<14} {b.url}/register?code={c}")
        return
    if args.del_code:
        code = args.del_code.upper()
        out = b.call("DELETE", f"/api/admin/invites/{urllib.parse.quote(code)}")
        if out.get("removed"):
            print(f"· {code} ingetrokken (accounts die ermee zijn aangemaakt blijven bestaan)")
        else:
            print(f"· {code} stond niet in de lijst")
        return
    invites = b.get("/api/admin")["invites"]
    print(table(invites, [("code", "code", 14), ("uses", "accounts", 9), ("created_at", "gemaakt", 26),
                          ("used_by_name", "eerste", 16)]))
    if invites:
        print(f"\n· {len(invites)} code(s); een code trek je in na het evenement, spelers blijven")


def cmd_claims(b: Bingo, args):
    claims = b.get("/api/admin")["claims"]
    if args.status:
        claims = [c for c in claims if c["status"] == args.status]
    print(table(claims, [("id", "id", 4), ("status", "status", 10), ("user", "speler", 16),
                         ("witness", "getuige", 16), ("joker", "joker", 6), ("item", "vakje", 48)]))


def cmd_witness(b: Bingo, args):
    rows = b.get("/api/state")["witness_requests"]
    if not rows:
        print("· geen verzoeken aan jou als getuige")
        return
    for r in rows:
        photo = f" foto {r['photo']['full']}" if r.get("photo") else ""
        print(f"claim {r['claim_id']:<4} van {r['claimant']:<16} {r['text'][:48]}{photo}")
        print(f"    bevestig: confirm {r['claim_id']} · wijs af: reject {r['claim_id']}")


def cmd_confirm(b: Bingo, args):
    b.post(f"/api/claims/{args.id}/confirm")
    print(f"· claim {args.id} bevestigd")


def cmd_reject(b: Bingo, args):
    b.post(f"/api/claims/{args.id}/reject")
    print(f"· claim {args.id} afgewezen (straight flush voor de indiener)")


def cmd_veto(b: Bingo, args):
    b.post(f"/api/claims/{args.id}/veto")
    print(f"· veto uitgebracht op claim {args.id}")


def cmd_revoke(b: Bingo, args):
    confirm(args, f"claim {args.id} door de admin intrekken")
    b.post(f"/api/admin/claims/{args.id}/revoke")
    print(f"· claim {args.id} ingetrokken")


def cmd_announce(b: Bingo, args):
    b.post("/api/admin/announce", {"text": args.text})
    print("· omroep bericht gestuurd (+ push naar alle apparaten)")


def cmd_reshuffle(b: Bingo, args):
    confirm(args, "alle kaarten hutschudden")
    b.post("/api/admin/reshuffle")
    print("· kaarten hergeschud; bestaande claims blijven staan")


def cmd_new_game(b: Bingo, args):
    confirm(args, "nieuw spel (claims, bingo's, feed en veto's weg)")
    b.post("/api/admin/new-game")
    print("· nieuw spel: bord leeg, kaarten hergeschud")


def cmd_raw(b: Bingo, args):
    body = None
    if args.json_file:
        body = json.loads(Path(args.json_file).read_text())
    elif isinstance(args.json, str):
        body = json.loads(args.json)
    result = b.call(args.method.upper(), args.path, body)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_doctor(b: Bingo, args):
    print(f"URL      {b.url}")
    health = b.get("/healthz")
    print(f"gezond   {json.dumps(health)}")
    st = b.get("/api/state")
    adm = b.get("/api/admin")
    s = adm["settings"]
    print(f"ingelogd als {st['me']['nickname']} (admin={st['me']['is_admin']})")
    print(grid_line(adm["grid"]))
    show_plan_extras(adm["grid"])
    print(f"duwknop  {'VAPID klaar' if st.get('vapid_public_key') else 'GEEN VAPID-sleutels (duw-uit staat uit)'}")
    print(f"HTTPS    {'ja' if b.url.startswith('https') else 'nee — PWA-installatie en push werken alleen op https'}")
    print(f"sessie   {b.cache}")
    if not adm["grid"]["optimal"]:
        print("· tip: grid-optimize (of: grid-auto on) om het rooster op het aantal vakjes te zetten")


# ------------------------------------------------------------------ cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bingo.py", description="Admin-CLI voor Speerpunt Bingo")
    p.add_argument("--url", help="basis-URL (of $BINGO_URL)")
    p.add_argument("--email", help="admin e-mail (of $BINGO_ADMIN_EMAIL)")
    p.add_argument("--password", help="admin wachtwoord (of $BINGO_ADMIN_PASSWORD)")
    p.add_argument("--json", action="store_true", help="rauwe JSON op stdout")
    p.add_argument("--yes", action="store_true", help="bevestig een niet-omkeerbare actie")
    p.add_argument("--quiet", action="store_true", help="login-regel onderdrukken")
    sub = p.add_subparsers(dest="cmd", required=True)
    # dezelfde vlaggen mogen ook ná het subcommando; SUPPRESS voorkomt dat de subparser
    # een al gezette vlag overschrijft
    common = argparse.ArgumentParser(add_help=False)
    for flag, kwargs in (("--json", {"action": "store_true"}), ("--yes", {"action": "store_true"}),
                        ("--quiet", {"action": "store_true"})):
        common.add_argument(flag, **kwargs, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    def add(name, func, doc):
        sp = sub.add_parser(name, help=doc, parents=[common])
        sp.set_defaults(func=func)
        return sp

    add("health", cmd_health, "hoeft maar één keer: staat de app?")
    add("whoami", cmd_whoami, "wie ben ik op deze instantie")
    add("doctor", cmd_doctor, "spoedcheck: health, login, rooster, VAPID, HTTPS")
    add("overview", cmd_overview, "alles in één oogopslag")
    add("state", cmd_state, "rauwe /api/state")
    add("board", cmd_board, "stand /SCOREBORD")
    feed = add("feed", cmd_feed, "laatste events")
    feed.add_argument("--limit", type=int, default=25)
    album = add("album", cmd_album, "fotoclaims")
    album.add_argument("--out-dir", dest="out_dir", help="foto's ook direct downloaden naar deze map")
    get = add("get", cmd_get, "willekeurig GET-endpoint, bijv. /api/admin/grid")
    get.add_argument("path")
    dl = add("download", cmd_download, "bestand ophalen, bijv. /uploads/abc.jpg")
    dl.add_argument("path")
    dl.add_argument("--out", required=True)

    add("grid", cmd_grid, "huidig + optimaal rooster en wat een wissel kost")
    gs = add("grid-set", cmd_grid_set, "rooster handmatig zetten (zet autoset uit)")
    gs.add_argument("cols", type=int)
    gs.add_argument("rows", type=int)
    ga = add("grid-auto", cmd_grid_auto, "rooster automatisch optimaal houden")
    ga.add_argument("state", choices=["on", "off"])
    go = add("grid-optimize", cmd_grid_optimize, "nu het optimale rooster zetten")
    go.add_argument("--force", action="store_true", help="ook als het bestaande bingo's kost")
    ln = add("lines", cmd_lines, "welke richtingen tellen")
    ln.add_argument("--rijen", choices=["on", "off"])
    ln.add_argument("--kolommen", choices=["on", "off"])
    ln.add_argument("--diagonalen", choices=["on", "off"])
    se = add("settings", cmd_settings, "instellingen tonen of zetten: key=waarde ...")
    se.add_argument("pairs", nargs="*")
    fr = add("freeze", cmd_freeze, "spel bevriezen / ontdooien")
    fr.add_argument("state", choices=["on", "off"])

    it = add("items", cmd_items, "alle vakjes met gebruik")
    ia = add("items-add", cmd_items_add, "vakjes toevoegen (tekst, --file of stdin)")
    ia.add_argument("text", nargs="*")
    ia.add_argument("--file")
    iset = add("item-set", cmd_item_set, "tekst van één vakje wijzigen")
    iset.add_argument("id", type=int)
    iset.add_argument("text")
    ide = add("item-del", cmd_item_del, "vakje verwijderen")
    ide.add_argument("id", type=int)

    pr = add("prizes", cmd_prizes, "prijzen tonen of zetten: lijn=tekst ...")
    pr.add_argument("pairs", nargs="*")

    pl = add("players", cmd_players, "alle spelers")
    pp = add("player-pass", cmd_player_pass, "wachtwoord van een speler zetten")
    pp.add_argument("id", type=int)
    pp.add_argument("password", nargs="?")
    prst = add("player-reset", cmd_player_reset, "claims + bingo's van een speler weg, nieuwe kaart")
    prst.add_argument("id", type=int)
    pdel = add("player-del", cmd_player_del, "speler verwijderen")
    pdel.add_argument("id", type=int)

    inv = add("invites", cmd_invites, "evenementcodes tonen / maken / intrekken")
    inv.add_argument("--new", type=int, help="N losse woordcodes maken")
    inv.add_argument("--code", help="eigen evenementcode, bijv. ISTANBUL2026")
    inv.add_argument("--del-code", dest="del_code", help="code intrekken")

    cl = add("claims", cmd_claims, "open en bevestigde claims")
    cl.add_argument("--status", choices=["pending", "confirmed"])
    add("witness", cmd_witness, "verzoeken aan mij als getuige")
    cf = add("confirm", cmd_confirm, "claim bevestigen als getuige")
    cf.add_argument("id", type=int)
    rj = add("reject", cmd_reject, "claim afwijzen als getuige")
    rj.add_argument("id", type=int)
    vt = add("veto", cmd_veto, "veto tegen een bevestigde claim")
    vt.add_argument("id", type=int)
    rv = add("revoke", cmd_revoke, "claim door admin intrekken")
    rv.add_argument("id", type=int)

    an = add("announce", cmd_announce, "omroep + push naar alle spelers")
    an.add_argument("text")
    add("reshuffle", cmd_reshuffle, "alle kaarten hutschudden (claims blijven)")
    add("new-game", cmd_new_game, "nieuw spel: claims, bingo's en feed weg")
    rw = add("raw", cmd_raw, "willekeurig API-verzoek, bijv. raw POST /api/push/test")
    rw.add_argument("method")
    rw.add_argument("path")
    rw.add_argument("--json-body", dest="json", help="JSON-body als string")
    rw.add_argument("--json-file", dest="json_file")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    for flag in ("json", "yes", "quiet"):
        if not hasattr(args, flag):
            setattr(args, flag, False)
    conf = read_env_file(ENV_FILE)
    url = args.url or os.environ.get("BINGO_URL") or conf.get("BINGO_URL") or DEFAULT_URL
    email = args.email or os.environ.get("BINGO_ADMIN_EMAIL") or conf.get("BINGO_ADMIN_EMAIL")
    password = args.password or os.environ.get("BINGO_ADMIN_PASSWORD") or conf.get("BINGO_ADMIN_PASSWORD")
    if not email:
        print(f"· geen admin-e-mail: zet BINGO_ADMIN_EMAIL (en BINGO_ADMIN_PASSWORD, BINGO_URL) in {ENV_FILE}",
              file=sys.stderr)
        return 2
    bingo = Bingo(url, email, password, quiet=args.quiet)
    try:
        args.func(bingo, args)
    except ApiError as exc:
        print(f"FOUT {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
