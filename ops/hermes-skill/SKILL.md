---
name: bingo-admin
description: Drive the Speerpunt Bingo / tombala web app as admin via its HTTP API (production: https://bingo.speerpunt.roelschenk.com, Coolify). Use for anything in the bingo game — players, invite codes, vakjes/items, win lines and prizes, grid/rooster optimisation, claims and witnesses, vetoes, feed, leaderboard, inbreng-modratie (speler-suggesties voor vakjes/prijzen vrijgeven en aannemen), freeze, announcements + push, reshuffle, new game. One stdlib CLI: scripts/bingo.py. Irreversible actions are gated behind --yes.
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
| `players` · `player-name REF NIEUW` · `player-pass REF [pw]` · `player-reset REF` · `player-del REF` | accounts (REF = id, nickname of e-mail; `players` toont ook de jokers per persoon); `--yes` voor reset/del |
| `joker REF geef|neem [aantal]` | jokers los geven / afnemen aan één persoon (`jokers_per_player` blijft de groepsbasis) |
| `invites [--code CODE] [--new N] [--del-code CODE]` | evenementcodes: `--code ISTANBUL-2026` maakt één eigen code voor het hele weekend (`--new N` maakt losse woordcodes), `--del-code` trekt in. Codes zijn **herbruikbaar**: iedereen met de code maakt een eigen account |
| `items` · `items-add "tekst" ...` / `--file vakjes.txt` / stdin · `item-set ID "tekst"` · `item-del ID` | the vakjes; adding fills empty card cells only |
| `grid` · `grid-set C R` · `grid-auto on\|off` · `grid-optimize [--force]` | rooster; auto keeps it optimal for the item count without reshuffling cards. `grid-set` is bounded 2–6 × 2–8 (the API clamps silently otherwise) and turns `grid_auto` off |
| `lines [--rijen on\|off] [--kolommen ...] [--diagonalen ...]` | which directions pay (vertical is off by default in code) |
| `prizes` · `prizes r0="Rakı" full="..."` · `settings [key=waarde ...]` | prizes per line (`full=` maps to the `full_card_prize` setting), all settings (title/kicker/jokers/veto/witness/frozen, plus `feedback_open`/`feedback_premod`/`feedback_per_player`) |
| `feedback` · `feedback-add "tekst" [--kind item\|prize] [--line r0]` · `feedback-approve ID` · `feedback-adopt ID` · `feedback-reject ID` · `feedback-reopen ID` | de inbreng-inbox: wat de club oppert aan vakjes en prijzen, vrijgeven, afkeuren, of er echt een vakje/prijs van maken (`adopt`) |
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
- Settings keys + ranges: `grid_cols` 2–6, `grid_rows` 2–8, `jokers_per_player` 0–5 (standaard **0**: de
  groepsbasis is uit; een joker is iets individueels), `joker_earn_claims` 0–20 (standaard **0** = uit;
  elke N bevestigde claims levert 1 joker op, automatisch max 3 p.p.),
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
  `joker` claims are auto-confirmed and kosten een joker uit het persoonlijke budget: groepsbasis
  `jokers_per_player` + verdiende (`joker_awards`, elke `joker_earn_claims` bevestigde claims = 1, max 3)
  + losse gaven (`jokers_bonus`). Standaard is er niets; geef er een met
  `joker <speler> geef` (of zet `joker_earn_claims=3` en laat ze zelf verdienen). A claim gains vetoes
  from other players; at `veto_threshold` it becomes `vetoed` (penalty for the claimant).
- Deleting an item soft-deletes it (`active=0`) and revokes its open claims — history stays.
- Inbreng (proposals): `proposals.kind` is `item` or `prize`, `line_key` only for prizes
  (`r0`/`c0`/`dr0`/`full`/`default` = elke lijn). Statussen: `pending` (alleen maker + admin),
  `visible` (iedereen, mag stemmen 👍👎), `adopted`, `rejected`. Met `feedback_premod=on`
  (default) komt een spelers-inbreng in de wacht te liggen: géén feed-event en géén SSE-refresh
  tot `feedback-approve`. Een admin-inbreng is altijd direct `visible` (handig als poll).
  Max open inbrengen per speler: `feedback_per_player` (standaard **0 = onbeperkt**), plus de vaste
  spamwacht van 8 pogingen / 5 min per speler.
- `adopt` is de knop die ertoe doet: `kind=item` voegt een echt vakje toe (kaarten worden alleen
  aangevuld, claims blijven staan, autoset doet het rooster meebewegen als dat veilig is),
  `kind=prize` overschrijft de prijs op die lijn (of `full_card_prize`/`default_line_prize`).
  Een gewonnen lijn onthoudt de prijsteks van het moment van winnen (`bingos.prize`), dus latere
  prijs-wijzigingen herschrijven de history niet.

## Recipes

**Nieuwe editie klaarzetten** (vakjes uit een tekstbestand, rooster volgt automatisch):

```bash
python3 $B items-add --file /path/vakjes.txt   # éénn vakje per regel
python3 $B grid                                # laat het voorgestelde rooster zien
python3 $B prizes full="1 fles rakı" r0="small pakje"
python3 $B invites --code ISTANBUL-2026          # één code voor het hele evenement + claimlink
python3 $B settings jokers_per_player=1 veto_threshold=2 witness_limit=3
python3 $B joker BigD geef                 # één joker los aan één persoon (neem = weer intrekken)
python3 $B settings joker_earn_claims=3    # of laat iedereen ze zelf verdienen
python3 $B overview
```

**Midden in een spel**: `overview` (wachtende claims bovenin), `claims --status pending`,
`confirm ID`/`reject ID` alleen als de admin zelf de getuige is, anders `revoke ID`.
Een spel stilzetten: `freeze on` (spelers zien "Klaar is Kees"), `freeze off` om door te gaan.
Winnaars aanmoedigen: `announce "Gefeliciteerd, Volle kaart voor X!"` (gaat ook als push uit).

**Einde spel / volgende ronde**: laat `new-game` niet uit een eigen aanname lopen — Roel
moet het expliciet vragen. Anders alleen `reshuffle` (kaarten nieuw, claims blijven).

**Inbreng modereren** (spelers mogen meedenken over vakjes en prijzen):

```bash
python3 $B feedback                       # wachtrij bovenaan, met 👍/👎 per suggestie
python3 $B feedback-approve 7             # vrij voor de club: feed + stand worden live
python3 $B feedback-adopt 7               # van suggestie naar echt vakje / echte prijs
python3 $B feedback-reject 9              # afkeuren (blijft staan in de history)
python3 $B feedback-add "patat voor de diagonaal" --kind prize --line dr0   # eigen poll
python3 $B settings feedback_open=off feedback_premod=off feedback_per_player=3
```

`adopt` met `kind=item` kan zeggen dat het rooster niet meebeweegt (`"Raster zou bestaande
bingo's kosten"`): dan staat het vakje in de lijst maar op geen enkele kaart; kies met
`grid-set` handmatig een groter rooster. In de app heeft elk account de tab **Inbreng**.

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
Since 2026-11-17 every start also writes a rolling snapshot to `/data/backups/bingo-<timestamp>.db`
(5 kept, `DB_SNAPSHOT_KEEP=0` disables it, a failed snapshot never blocks startup). That is the undo button
for a bad migration when you have no shell on the host: copy it over `/data/bingo.db` and remove the
`-wal`/`-shm` files. The startup log line `db-snapshot: /data/backups/…` confirms it ran.

```bash
python3 ops/coolify/deploy.py show|deploy|wait|logs|envs|domains   # token: ~/.hermes/mcp-tokens/coolify.env (600)
python3 ../nginx-proxy-manager/scripts/npm.py hosts               # NPM: upstreams, certs, SSE-aanpak
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
