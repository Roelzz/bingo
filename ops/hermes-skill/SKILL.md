---
name: bingo-admin
description: Drive the Speerpunt Bingo / tombala web app as admin via its HTTP API (production: https://bingo.speerpunt.roelschenk.com, Coolify). Use for anything in the bingo game — players, invite codes, vakjes/items, win lines and prizes, grid/rooster optimisation, claims and witnesses, vetoes, feed, leaderboard, freeze, announcements + push, reshuffle, new game. One stdlib CLI: scripts/bingo.py. Irreversible actions are gated behind --yes.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  app: speerpunt-bingo
  stack: FastAPI + SQLite + SSE + Web Push, no build step
  deploy: github → Coolify, single uvicorn worker
---

# Bingo admin (Speerpunt Bingo / tombala)

Every admin action in the app is a JSON endpoint behind one session cookie. Use the CLI; do
not hand-roll curl (it forgets the `X-Requested-With` header the app requires).

```bash
B=~/.hermes/skills/bingo-admin/scripts/bingo.py
python3 $B doctor            # first thing: health + login + grid + push keys + HTTPS
python3 $B overview          # one-glance state of the game
```

Flags go **after** the subcommand (`$B item-del 7 --yes`, `$B items --json`). Commands: `--yes`
for anything irreversible, `--json` where it helps, `--quiet` to drop the login line.

## Setup (once)

Secrets live in `~/.hermes/mcp-tokens/bingo.env` (chmod 600), same convention as bunq/domcity:

```bash
mkdir -p ~/.hermes/mcp-tokens && cat > ~/.hermes/mcp-tokens/bingo.env <<'EOF'
BINGO_URL=https://bingo.speerpunt.roelschenk.com
BINGO_ADMIN_EMAIL=admin@speerpunt.nl
BINGO_ADMIN_PASSWORD=<same value as ADMIN_PASSWORD in Coolify>
EOF
chmod 600 ~/.hermes/mcp-tokens/bingo.env
```

`ADMIN_EMAIL`/`ADMIN_PASSWORD` in Coolify create/keep the admin account; `ADMIN_RESET_PASSWORD=1`
for one boot forces that password. Test instance on this machine: `--url http://127.0.0.1:8000`.
Session token cache: `~/.hermes/mcp-tokens/bingo/<host>.json` (0600). Delete it to force a re-login.

## Commands

| Command | What it does |
| --- | --- |
| `health` · `whoami` · `doctor` | reachability, identity, full self-check |
| `overview` · `state` · `board` · `feed --limit N` · `album [--out-dir DIR]` | read the game (leaderboard, events, photo claims; `--out-dir` also downloads the JPEGs) |
| `players` · `player-name REF NIEUW` · `player-pass REF [pw]` · `player-reset REF` · `player-del REF` | accounts; REF = id, nickname of e-mail (`--yes` voor reset/del) |
| `invites [--code CODE] [--new N] [--del-code CODE]` | evenementcodes: `--code ISTANBUL-2026` maakt één eigen code voor het hele weekend (`--new N` maakt losse woordcodes), `--del-code` trekt in. Codes zijn **herbruikbaar**: iedereen met de code maakt een eigen account |
| `items` · `items-add "tekst" ...` / `--file vakjes.txt` / stdin · `item-set ID "tekst"` · `item-del ID` | the vakjes; adding fills empty card cells only |
| `grid` · `grid-set C R` · `grid-auto on\|off` · `grid-optimize [--force]` | rooster; auto keeps it optimal for the item count without reshuffling cards. `grid-set` is bounded 2–6 × 2–8 (the API clamps silently otherwise) and turns `grid_auto` off |
| `lines [--rijen on\|off] [--kolommen ...] [--diagonalen ...]` | which directions pay (vertical is off by default in code) |
| `prizes` · `prizes r0="Rakı" full="..."` · `settings [key=waarde ...]` | prizes per line (`full=` maps to the `full_card_prize` setting), all settings (title/kicker/jokers/veto/witness/frozen) |
| `freeze on\|off` | stop/resume the game (players see "Klaar is Kees") |
| `claims [--status pending\|confirmed]` · `witness` · `confirm ID` · `reject ID` · `veto ID` · `revoke ID` | claim handling |
| `announce "tekst"` | feed announcement **plus web-push to every subscribed phone** |
| `reshuffle` · `new-game` | reshuffle cards (claims stay) / wipe claims+bingos+feed and reshuffle |
| `get PATH` · `download /uploads/x.jpg --out f` · `raw POST /api/push/test` | anything not wrapped |

Global flags: `--url`, `--email`, `--password`, `--json` (raw JSON), `--yes` (destructive),
`--quiet`. Exit code 1 with `FOUT HTTP <code>: <detail>` on API errors (details are Dutch).
`get <path>` fetches any GET, `download /uploads/x.jpg --out f` fetches bytes, and
`raw POST /api/push/test --json-body '{...}'` covers the rest.

## Safety rules

- `new-game`, `reshuffle`, `player-reset`, `player-del`, `item-del`, `revoke` need `--yes`.
- Nicknames are editable by everyone (`player-name`, or the 👤 sheet in the app). Renaming keeps the card,
  score and claims — it is display only. `player-del` now removes admin accounts too (never yourself, never
  the last admin); on a host without DB access use `PRUNE_ADMINS=<emails>` in Coolify for one deploy.
  Never add `--yes` without an explicit go-ahead from Roel in this conversation — `new-game`
  wipes every claim, bingo and feed entry of a live jaarclub game.
- `announce` is a broadcast to all phones; phrase it like something that should be shouted in a bar.
- Read-only inspection (`overview`, `claims`, `feed`, `album`, `grid`, `doctor`) is always safe.
- Photos in `album`/claims are user content: describe, don't redistribute.

## API facts (validated against the running app)

- Auth: `POST /login` with form `email`, `password` → 303 + cookie `tombala_session`
  (HttpOnly, 30 days). **The script already caches the token; don't log in per command.**
  Login is rate-limited: 10 attempts / 5 min per IP+e-mail (429 = wait, don't retry).
- Every `/api/*` write needs header `X-Requested-With: tombala` (CSRF guard) → without it:
  `400 Ongeldige aanvraag`. Admin-only routes: 401 not logged in, 403 not admin.
- `GET /api/admin` = settings, items (with usage counts), invites, players, line prizes,
  claims (pending+confirmed, last 200), grid plan. `GET /api/state` = player view: card,
  lines, leaderboard, feed, witness requests, `vapid_public_key`.
- Public `/api/state` settings use **`cols`/`rows`** (ints), while admin settings use
  **`grid_cols`/`grid_rows`** (strings). Don't mix them up.
- Settings keys + ranges: `grid_cols` 2–6, `grid_rows` 2–8, `jokers_per_player` 0–5,
  `veto_threshold` 0–20, `witness_limit` 0–50; text: `title`, `kicker`, `subtitle`, `footer`,
  `default_line_prize`, `full_card_prize`; bool: `line_rows`, `line_cols`, `line_diagonals`,
  `grid_auto`, `frozen`. Item text ≤160 chars, prize ≤200, announce ≤300, invites 1–30/run.
- Line keys for `prizes`: rows `r0..r{rows-1}`, columns `c0..c{cols-1}`, diagonals
  `dr0 dr1 dl0 dl1`, plus `full` (volle kaart). Keys not in the current layout are ignored
  silently — the CLI validates and refuses.
- Grid autoset (`grid_auto`, default on): picks the best grid for the number of active items
  (longest sides → most cells → squarest → portrait → keep current). It **never reshuffles
  cards** (claimed vakjes keep their exact cell) and refuses a change that would strip an
  already-won bingo unless `grid-optimize --force`. Hand-setting `grid-set` switches
  `grid_auto` off until re-enabled.
- Claims: a player claim needs a witness (not themselves, capped by `witness_limit`);
  `confirm`/`reject` only work for the witness — the admin can revoke with `revoke`.
  `joker` claims are auto-confirmed and cost one of `jokers_per_player`. A claim gains vetoes
  from other players; at `veto_threshold` it becomes `vetoed` (penalty for the claimant).
- Deleting an item soft-deletes it (`active=0`) and revokes its open claims — history stays.

## Recipes

**Nieuwe editie klaarzetten** (vakjes uit een tekstbestand, rooster volgt automatisch):

```bash
python3 $B items-add --file /path/vakjes.txt   # éénn vakje per regel
python3 $B grid                                # laat het voorgestelde rooster zien
python3 $B prizes full="1 fles rakı" r0="small pakje"
python3 $B invites --code ISTANBUL-2026          # één code voor het hele evenement + claimlink
python3 $B settings jokers_per_player=1 veto_threshold=2 witness_limit=3
python3 $B overview
```

**Midden in een spel**: `overview` (wachtende claims bovenin), `claims --status pending`,
`confirm ID`/`reject ID` alleen als de admin zelf de getuige is, anders `revoke ID`.
Een spel stilzetten: `freeze on` (spelers zien "Klaar is Kees"), `freeze off` om door te gaan.
Winnaars aanmoedigen: `announce "Gefeliciteerd, Volle kaart voor X!"` (gaat ook als push uit).

**Einde spel / volgende ronde**: laat `new-game` niet uit een eigen aanname lopen — Roel
moet het expliciet vragen. Anders alleen `reshuffle` (kaarten nieuw, claims blijven).

**Argeloze spelers / klachten**: `feed --limit 40` en `album` laten zien wat er echt gebeurd
is; `state --json` geeft de volledige kaart + claims per speler.

## Deploy: wat er op de server staat (Coolify)

Project `speerpunt-bingo`, app-uuid `x0c4c80g040o4gc4go8c8csw`, omgeving `production`, build pack Dockerfile,
`ports_exposes 8000`, healthcheck `/healthz`. Domain `bingo.speerpunt.roelschenk.com`: `COOKIE_SECURE=auto` is
correct there (Secure cookie over HTTPS; push + PWA install need HTTPS). Keep **one worker**
(`uvicorn app.main:app --host 0.0.0.0 --port 8000`, no `--workers/--reload`) — the SSE feed
and push state are in-process, and the SQLite DB + uploads + VAPID keys must live on `/data`
or a redeploy resets the game. Persistence is **UI-only here** and it is **in place since 2026-11-16**: Coolify → app → **Persistent
Storage → Directory Mount**, source `/data/speerpunt-bingo`, destination `/data` (verified: two deploys
in a row keep `oudste account 2026-11-16T09:06:56`).
`custom_docker_run_options` is **ignored** for Dockerfile build packs (it can look set and do nothing), and
`POST /applications/{uuid}/storages` is 404 on Coolify beta.462.
Verify with `deploy.py logs`: the startup line `data: /data/bingo.db — N accounts, …, oudste account <ts>`
must show the *same* timestamp after two deploys; a fresh timestamp means the DB was recreated.

```bash
python3 ops/coolify/deploy.py show|deploy|wait|logs|envs|domains   # token: ~/.hermes/mcp-tokens/coolify.env (600)
```

Image needs `curl`: Coolify injects its own healthcheck inside the container (curl/wget), so on
`python:slim` a deploy "fails" with *curl: not found* even though uvicorn started fine.
Pushes do not auto-deploy (no webhook on the repo) — run `deploy.py deploy` after merging.
Front of this VPS is **Nginx Proxy Manager** (no Traefik in use), so the app publishes a host port
through `ports_mappings: 8091:8000` — the NPM proxy host upstream must be `<server-ip>:8091` (same
pattern as the other Coolify apps: 8887, 6006, 2011). Those ports are firewalled from the internet,
only NPM reaches them. NPM needs `proxy_buffering off` + long `proxy_read_timeout` on `/api/stream`
(SSE). Prove which instance answers by checking `deploy.py logs` for the request — if it is missing,
the NPM proxy host still points at the old (LAN) instance.

## Gotchas

- Plain-HTTP LAN instance needs `COOKIE_SECURE=0`, otherwise login appears to work and the
  session cookie is then dropped → every admin call 401s.
- `feed`/`state` return Dutch text and ISO timestamps; `overview` is the compact view.
- Codes zijn evenementcodes, dus herbruikbaar (vóór 16 nov waren ze eenmalig; oude codes zijn
  ingetrokken met `invites --del-code`). Registratie is twee stappen: code invullen op `/`
  (= `/register`), dan nickname + e-mail + wachtwoord. De claimlink is
  `{BINGO_URL}/register?code=CODE` — die print het CLI-commando bij het maken.
- Photos arrive as data URLs on claim creation; `album` gives `/uploads/<stem>.jpg` and
  `<stem>_t.jpg` thumbnails — fetch with `download` or `album --out-dir`.
- After a Coolify deploy the token cache is still valid (sessions live in the DB), unless
  `DATA_DIR` wasn't on a volume — then the DB is empty and login fails with a fresh 401. The
  Coolify prod DB started empty on 16 nov, dus daar is nog geen demo-data van de LAN-instantie.
- Read `references/api.md` before using `raw` for anything not in the table above.
