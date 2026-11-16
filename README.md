# Speerpunt Bingo

Bingo als drankspel voor jaarclub Speerpunt (Utrecht, 09). Telefoonvriendelijk (PWA), realtime, met getuigen, vetorecht, jokers, fotobewijs en prijzen per lijn.

Stack: Python 3.12 met uv, FastAPI, SQLite, Server Sent Events, Web Push. Geen build-stap voor de frontend.

## Hoe het spel werkt

- **Meedoen doe je met de evenementcode**: één code per evenement, door iedereen te gebruiken. Op het startscherm vul je die code in, daarna kies je zelf nickname, e-mail en wachtwoord. De admin maakt de code onder **Codes** (ook een eigen eentje zoals `ISTANBUL-2026`).
- Iedereen krijgt een **eigen geschudde kaart**. Zijn er meer vakjes dan cellen, dan krijgt ieder een willekeurige selectie.
- Tik een vakje aan, kies een **getuige**, voeg eventueel een foto en toelichting toe. De getuige krijgt een melding en bevestigt of wijst af.
- **Afgewezen = strafslok**, publiek in de feed.
- **Joker**: één vakje zonder getuige (aantal instelbaar).
- **Veto**: zet twee anderen (instelbaar) een veto op een bevestigde claim, dan is hij weg. Getuige en claimer mogen niet meestemmen.
- **Lijnen**: horizontale rijen en diagonalen (kolommen kun je aanzetten). Bij een 3×4 raster zijn er 4 diagonalen van 3 vakjes.
- **Prijzen**: per lijn, en iedereen die een lijn haalt wint die prijs. Volle kaart is de superprijs.
- **Album**: alle bewijsfoto's van het weekend. Foto's worden op de telefoon verkleind en op de server ontdaan van metadata (ook GPS).

## Lokaal draaien met uv

```bash
uv sync
cp .env.example .env            # vul je eigen admin in
set -a; source .env; set +a
DATA_DIR=./data uv run uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 en log in met je admin. Ga naar **Admin → Uitnodigingen**, maak codes en tik **Delen**: je vrienden krijgen een link waarin de code al is ingevuld.

Tests: `uv run pytest -q`

### Testen op je telefoon

Service workers en pushmeldingen werken alleen via **HTTPS** (of `localhost`). Via `http://192.168.x.x:8000` werkt het spel wel, maar zonder push en offline-modus. Voor een echte test gebruik je een tunnel:

```bash
tailscale serve 8000            # of
cloudflared tunnel --url http://localhost:8000
```

### Lokaal met Docker

```bash
cp .env.example .env
docker compose up --build
```

## Deployen in Coolify

Deze repo is er klaar voor: `Dockerfile` (één uvicorn-worker, `--proxy-headers`, healthcheck op `/healthz`, `curl` in de image
want Coolify prikt zijn healthcheck in de container), `docker-compose.yml` voor lokaal, en `ops/coolify/deploy.py` voor
deployen en controleren vanaf de commandlijn.

Wat er nu live staat (november 2026): project **speerpunt-bingo**, app-uuid `x0c4c80g040o4gc4go8c8csw`, omgeving
`production`, domein `bingo.speerpunt.roelschenk.com`, env vars `ADMIN_EMAIL`, `ADMIN_PASSWORD` (letterlijk),
`ADMIN_NICKNAME`, `ADMIN_RESET_PASSWORD=0`, `COOKIE_SECURE=auto`, `SESSION_DAYS=30` (plus eenmalig `PRUNE_ADMINS` als er een oude admin weg moet). Mijn token: `~/.hermes/mcp-tokens/coolify.env`.

**Opslag is handwerk in de UI**: Coolify → app → **Persistent Storage → Add → Directory Mount**, source
`/data/speerpunt-bingo`, destination `/data`, daarna opnieuw deployen. `custom_docker_run_options`
met `-v …` wordt bij een Dockerfile-build **genegeerd**, en `POST /applications/{uuid}/storages`
geeft op Coolify beta.462 een 404 — dus zonder die mount in de UI zijn je SQLite-DB, foto's en
VAPID-sleutels na elke deploy weg.

Controleer in de log (`python3 ops/coolify/deploy.py logs`) de opstartregel:

```
INFO: data: /data/bingo.db — 4 accounts, 1 admin(s), oudste account 2026-11-16T08:37:15+00:00, PRUNE_ADMINS=-
```

Blijft "oudste account" tussen twee deploys gelijk, dan staat de mount goed. Komt er elke deploy
een nieuw tijdstip, dan wordt de database gewoon opnieuw aangelegd.

Er draait hier **geen Traefik**, maar Nginx Proxy Manager. De app publiceert daarom een host-poort via
`ports_mappings: 8091:8000` — in NPM wordt de upstream dus `<server-ip>:8091` (net als de andere apps: 8887, 6006, 2011…).
De VPS-firewall laat die poorten van buiten niet binnen, dus alleen NPM komt erbij. Zet in NPM bij `/api/stream`
`proxy_buffering off` en een lange `proxy_read_timeout` (1 dag), anders sneuvelt de realtime feed na 60 seconden.

```bash
python3 ops/coolify/deploy.py show      # status, domein, health
python3 ops/coolify/deploy.py deploy    # nieuwe versie uit GitHub bouwen + wachten
python3 ops/coolify/deploy.py logs      # laatste regels van de container
python3 ops/coolify/deploy.py envs      # welke env-varnamen er staan (nooit de waarden)
```

De token staat in `~/.hermes/mcp-tokens/coolify.env` (`COOLIFY_URL`, `COOLIFY_TOKEN`, chmod 600). Elke push naar `main`
deployt niet vanzelf (er hangt geen GitHub-webhook aan de repo): trek de nieuwe versie zelf met
`python3 ops/coolify/deploy.py deploy`, of zet in Coolify een webhook/git-hook op de repo aan.

Handmatig in de UI, als je dat fijner vindt: **New Resource → Application → repo**, build pack **Dockerfile**, **Ports
Exposes** `8000`, domein erbij, env vars uit `.env.example`, en een **Persistent Storage** mount op `/data`.

Belangrijk: draai **één** container met **één** worker. De realtime feed staat in het geheugen; schaal je op, dan zien
spelers elkaars updates niet meer direct.

Valkuil bij verhuizen: de game-state zit in `data/bingo.db` en `data/uploads/`. Met de mount hierboven
kopieer je ze naar de VPS en de app vindt ze meteen weer:

```bash
rsync -a data/ root@<vps-ip>:/data/speerpunt-bingo/
```

Nog een valkuil: als er vóór Coolify nog een eigen nginx/openresty op je server zit (Coolify zelf draait Traefik), dan
bepaalt *die* config welke instantie het domein krijgt. Controleer na een switch altijd even of het verzoek werkelijk in
`deploy.py logs` verschijnt — anders wijst het domein nog naar de oude app.

## Raster automatisch optimaal

Zet **Admin → Raster & regels → Rooster automatisch optimaal houden** aan (staat standaard aan). Het app‑rooster volgt dan het aantal vakjes: bij 12 vakjes 3×4, bij 16 4×4, bij 20 4×5, bij 48 6×8. De keus regels: zo lang mogelijke lijnen (geen 2‑vakjes‑bingo), dan zoveel mogelijk vakjes op de kaart, dan vierkant, dan hoog (telefoon), en bij gelijke keus blijft het huidige rooster staan.

Belangrijk: optimaliseren **schudt geen kaarten opnieuw**. Geclaimde vakjes blijven op precies dezelfde plek staan, alleen lege plekken worden bijgevuld. En zolang er al bingo's gewonnen zijn, weigert de autoset een roosterwissel die iemand een prijs afpakt ("Raster zou bestaande bingo's kosten"); in Admin staat dan welke lijn verloren zou gaan, en alleen handmatig forceren doet het toch. Zelf kolommen/rijen intikken? Dan skipt de autoset vanzelf, totdat je hem weer aanzet.

API: `GET /api/admin/grid` (huidige + optimale stand en wat een wissel kost), `POST /api/admin/auto-grid` (met `{"force": true}` om bescherming te negeren).

## Data en backup

Alles staat in `/data`: `tombala.db` (SQLite), `uploads/` (foto's) en `vapid_private.pem` (pushsleutel). Verlies de pushsleutel niet, anders moet iedereen meldingen opnieuw aanzetten.

Backup terwijl de app draait:

```bash
docker exec <container> python -c "import sqlite3; s=sqlite3.connect('/data/tombala.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
```

## Tips voor de admin

- **Codes**: één code per evenement is genoeg. Maak hem zelf, bijv. `ISTANBUL-2026`, en deel de link `https://…/register?code=ISTANBUL-2026`; dan hoeft je gast alleen te bevestigen en zijn account te kiezen. Na het weekend trek je de code in (✕); accounts die ermee zijn aangemaakt blijven gewoon bestaan.
- **Omroep** stuurt een bericht plus pushmelding naar iedereen.
- **Vakjes** toevoegen kan in bulk, één per regel. Nieuwe vakjes komen alleen op lege plekken; wil je ze verdelen, gebruik **Alle kaarten opnieuw schudden**. Geclaimde vakjes blijven daarbij staan.
- **Raster** aanpassen mag tijdens het spel; claims gaan niet verloren. Met **Rooster automatisch optimaal** kiest de app zelf het beste rooster bij het aantal vakjes (zie *Raster automatisch optimaal*).
- **Spel bevriezen** aan het eind, zodat niemand om 04:00 nog snel zijn kaart volgooit.
- **Nieuw spel** wist claims, bingo's en feed. Spelers en vakjes blijven.
- Iemand zijn wachtwoord kwijt? **Spelers → 🔑**.
- **Nickname**: iedereen kan die zelf wijzigen in het 👤-venster (2 tot 20 tekens, uniek, hoofdletters maken niet uit). Als admin hernoem je iemand anders met **Spelers → ✏️**. Score, kaart en claims blijven gewoon aan hetzelfde account plakken.
- Oude admin weg? **Spelers → 🗑** mag nu ook admin-accounts verwijderen (het eigen account nooit, en de app houdt altijd minimaal één admin over). Staat de oude admin in een omgeving waar je niet bij de database kunt, zet dan eenmalig `PRUNE_ADMINS=oud@adres.nl` in Coolify en deploy; na die start de variabele weer leegmaken.

## Telefoon-installatie voor je vrienden

- **iPhone**: open de link in Safari → Deel → *Zet op beginscherm*. Daarna in de app: tik je avatar → *Meldingen aanzetten*. Push werkt op iOS alleen vanaf het beginscherm (iOS 16.4 of nieuwer).
- **Android**: Chrome → menu → *App installeren*, daarna meldingen aanzetten.

## Embleem en iconen

Het embleem staat in `assets/emblem-source.jpg`. Het script `scripts/make_assets.py` vult de linten met "UTRECHT" en "09", haalt de papierachtergrond weg en maakt `app/static/img/crest.jpg` plus alle app-iconen. Nieuw embleem of andere lintteksten? Pas de bovenste regels van het script aan en draai:

```bash
uv run python scripts/make_assets.py
```

Lettertype: Cinzel (SIL Open Font License), lokaal meegeleverd zodat het geen roamingdata kost.

Teksten zoals titel, editie en voetregel pas je aan in **Admin → Raster & regels**. Volgend jaar wordt het dus gewoon "Editie Lissabon".

## Mappenstructuur

```
app/
  main.py      routes: pagina's, spel-API, admin-API, SSE
  game.py      kaarten schudden, lijnen, bingo's, prijzen
  db.py        schema, standaardinstellingen, startvakjes
  auth.py      wachtwoorden (argon2), sessies, invite codes
  push.py      Web Push met automatisch gegenereerde VAPID-sleutels
  events.py    realtime broker
  templates/   login, registratie, app-shell
  static/      app.js, app.css, sw.js, iconen, embleem, lettertype
assets/        bronbestand van het embleem
scripts/       make_assets.py
ops/hermes-skill/  Hermes-skill (SKILL.md + CLI + API-referentie) voor adminwerk op afstand
ops/coolify/       deploy.py: builds, status en logs via de Coolify-API
tests/         end-to-end tests via de API
```

## Beheer vanaf je agent (Hermes-skill)

`ops/hermes-skill/` is een [Agent Skill](https://agentskills.io): één CLI (standaard Python) die
elke admin-actie in de app doet, zodat je de web-UI niet hoeft aan te klikken.

```bash
ops/hermes-skill/install.sh          # kopieert naar ~/.hermes/skills/bingo-admin
```

Toen de install voor het eerst draait maakt `~/.hermes/mcp-tokens/bingo.env` (chmod 600) met:

```bash
BINGO_URL=https://bingo.speerpunt.roelschenk.com
BINGO_ADMIN_EMAIL=admin@speerpunt.nl
BINGO_ADMIN_PASSWORD=<zelfde waarde als ADMIN_PASSWORD in Coolify>
```

Daarna, vanuit elke agent op deze machine:

```bash
B=~/.hermes/skills/bingo-admin/scripts/bingo.py
python3 $B doctor && python3 $B overview
python3 $B items-add --file vakjes.txt     # rooster volgt het aantal vakjes vanzelf
python3 $B prizes full="1 fles rakı"
python3 $B invites --new 25
python3 $B announce "Klaar is Kees, spel bevroren"
python3 $B grid-optimize                   # kaarten worden hierbij nooit hutschud
```

Niet-omkeerbare acties (`new-game`, `reshuffle`, `player-reset`, `player-del`, `item-del`,
`revoke`) vragen expliciet `--yes`. De sessie-token wordt gecachet in
`~/.hermes/mcp-tokens/bingo/<host>.json`, dus de login-ratelimiter (10 per 5 min) is geen probleem.
