---
name: nginx-proxy-manager
description: Manage Nginx Proxy Manager (NPM) over its REST API — list/inspect proxy hosts, repoint a domain to another server:port, create or delete proxy hosts, request Let's Encrypt certificates, toggle SSL/HTTP2/websockets, add SSE-friendly settings, inspect access lists, users and the audit log. Use when the user mentions Nginx Proxy Manager, proxy hosts, upstream/port changes, SSL or cert issuance, "point the domain at", 502s from the proxy, or realtime feeds that drop. One stdlib CLI: scripts/npm.py. Destructive actions are gated behind --yes.
version: 1.0.0
author: Roel (based on aiskill.market nginx-proxy-manager / clawhub npm-proxy)
license: MIT
platforms: [linux, macos]
metadata:
  app: nginx-proxy-manager
  api: NPM 2.16 REST, /api/nginx/proxy-hosts + /api/nginx/certificates
  here: https://npm.roelschenk.com (NPM 2.16.0), front for *.roelschenk.com
---

# Nginx Proxy Manager

Everything is a JSON API behind a bearer token. Use the CLI instead of hand-rolled curl — it
keeps the token cached (NPM logins are rate limited and 2FA-aware) and sends the payload shape
NPM actually accepts.

```bash
N=~/.hermes/skills/nginx-proxy-manager/scripts/npm.py   # of ~/.pi/agent/skills/…
python3 $N doctor
```

Config: `~/.hermes/mcp-tokens/npm.env` (chmod 600)

```bash
NPM_URL=https://npm.roelschenk.com
NPM_EMAIL=<npm-admin@…>
NPM_PASSWORD='<wachtwoord>'
```

Token cache: `~/.hermes/mcp-tokens/npm/<host>.json` (600). Env vars van dezelfde naam winnen het
van het bestand; `--url` wint van alles. Zet het wachtwoord tussen **enkele quotes** als het `#`,
`<`, `"` of spaties bevat.

## Commando's

| commando | wat |
| --- | --- |
| `health` | API bereikbaar? (geen login) |
| `doctor` | login + host-counts + waarschuwingen (geen cert, hosts uit) |
| `hosts` · `host REF` · `find DEEL` | opsommen / één host / zoeken op domein (REF = id of domein) |
| `point REF --host H --port P [--scheme]` | **upstream verzetten**, de rest blijft intact |
| `set REF [--ssl/--no-ssl] [--http2] [--ws] [--enabled] [--cert ID\|new] [--advanced TEXT] [--location PAD] [--domain D] [--name NAAM]` | vlaggen en config |
| `sse REF [--stream /api/stream] [--body-size 12m]` | niet bufferen, 1 dag timeout, websocket-upgrade, upload-limiet |
| `add DOMEIN --host H --port P [--ssl-le] [--force-https] [--http2] [--dns-challenge]` | nieuwe proxy-host |
| `del REF --yes` · `enable REF` · `disable REF` | host weg / aan / uit |
| `certs` · `cert-add DOMEIN [--dns-challenge] [--wildcard]` · `cert-del ID --yes` | certificaten |
| `lists` · `users` · `audit [--limit N]` · `config` | access lists, gebruikers, audit-log, instellingen |
| `raw METHOD PATH [--data JSON]` | alles wat er niet staat |

Global: `--json` (ruwe output), `--url`, `--otp CODE` (2FA), `--yes`.

## Valkuilen

- **`locations` is een vervangende lijst**: een bestaande location verdwijnt als je de hele lijst
  opnieuw stuurt. `set --location` en `sse` doen dat samenvoegen voor je.
- **`PUT` wil de volledige payload**; de CLI gebruikt `PATCH` (NPM ≥ 2.10) en valt automatisch
  terug op een volledige `PUT`.
- **`certificate_id`**: `0` = geen cert, een getal = bestaand cert, `"new"` = Let's Encrypt bij het
  aanmaken. LE heeft een limiet van ~5 uitgiften per week per (wildcard)domein — bij een cap of
  fouten niet tien keer opnieuw proberen, eerst `certs` kijken.
- **`block_exploits` aan** kan POST-body's met rare tekens breken (JSON met `..`, `|`, `<`). Staat
  bij deze app op aan; als claims/foto's 403'en, eerst `set REF --no-exploits` proberen.
- **Realtime/SSE**: zonder `proxy_buffering off` en een lange `proxy_read_timeout` knipt NPM een
  SSE-feed na 60 s af (symptoom: feed stopt, browser blijft "verbinding zoekend"). Gebruik `sse`.
- **Eigen Locations erven** de upstream niet automatisch; geef altijd path + forward met bijbehorende
  geavanceerde config.
- **Toegang**: de NPM-UI/API-poort (81) staat hier niet open op de VPS. De API bereik je via het
  bestaande proxy-host `npm.roelschenk.com` (443). Lukt dat niet, dan is er een host/forward nodig
  in NPM zelf — niet via de API oplossen.
- **Gevaar**: `del` en `cert-del` slopen een live domein. Nooit `--yes` bij `del` zonder eerst
  `host REF` te tonen in het antwoord.

## Recept: bingo op Coolify (deze setup)

Upstream van `bingo.speerpunt.roelschenk.com` naar de Coolify-container (host-poort 8091), met SSE

```bash
python3 $N find bingo                       # id + huidige forward_host/opmerkingen bekijken
python3 $N point <id> --host 145.53.228.134 --port 8091
python3 $N sse <id> --stream /api/stream --body-size 12m
curl -s https://bingo.speerpunt.roelschenk.com/healthz          # {"ok":true}
python3 ../bingo-admin/scripts/bingo.py doctor                  # en de spelerlijst moet de prod-DB tonen
```

Controleer achteraf welke database antwoordt: de Coolify-instantie heeft alleen `roelschenk@gmail.com`
als account, de LAN-instantie heeft de testspelers `Speler1..3`. Zie ook de `bingo-admin` skill
(`ops/hermes-skill/SKILL.md`) voor de Coolify- en NPM-feiten van deze app.
