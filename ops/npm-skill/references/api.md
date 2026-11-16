# NPM API — wat er echt werkt (2.16.0)

Basis: `NPM_URL` (hier `https://npm.roelschenk.com`). Alles onder `/api`.

## Login en sessie

| verzoek | opmerking |
| --- | --- |
| `GET /api/` | geen auth nodig; `{"status":"OK","setup":true,"version":{…}}` — gebruikt voor `health` |
| `POST /api/tokens` | `{"identity":"<email>","secret":"<wachtwoord>"}` → `{"token":"…","expires_at":…}`. Met 2FA: veld `token` (6 cijfers) meesturen, anders komt `{"code":"otpRequired"}` |
| elke andere call | header `Authorization: Bearer <token>`; 401 → opnieuw inloggen (de CLI doet dat automatisch en cacht de token) |
| `GET /api/users/me` | wie ben ik (id, e-mail, role) |

Zonder JSON-body geeft `POST /api/tokens` een 500 in plaats van een nette 400 — niet verwarren
met een kapotte API.

## Proxy hosts

| verzoek | body / opmerking |
| --- | --- |
| `GET /api/nginx/proxy-hosts?expand=owner,access_list,ssl_certificate` | lijst; `expand` geeft namen i.p.v. ids |
| `GET /api/nginx/proxy-hosts/{id}` | één host, inclusief `locations` en `advanced_config` |
| `POST /api/nginx/proxy-hosts` | verplicht: `domain_names[]`, `forward_scheme`, `forward_host`, `forward_port`; `certificate_id` is `0`, een id, of `"new"`; `meta.{letsencrypt_agree,letsencrypt_email,dns_challenge}` bij LE |
| `PATCH /api/nginx/proxy-hosts/{id}` | partieel (≥ 2.10) — dit gebruikt de CLI |
| `PUT /api/nginx/proxy-hosts/{id}` | wil de **volledige** schrijfbare set: `domain_names, forward_host, forward_port, forward_scheme, access_list_id, certificate_id, ssl_forced, hsts_enabled, hsts_subdomains, http2_support, block_exploits, caching_enabled, allow_websocket_upgrade, advanced_config, locations, enabled, meta, nice_name` |
| `DELETE /api/nginx/proxy-hosts/{id}` | host weg |

Veldbetekenis: `enabled` (aan/uit), `ssl_forced` (http → https redirect), `http2_support`,
`allow_websocket_upgrade`, `block_exploits`, `caching_enabled`, `hsts_enabled`, `nice_name`.

### `locations` en `advanced_config`

`advanced_config` is ruwe nginx die binnen de `server`-block komt, bijv.

```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 86400s;
client_max_body_size 12m;
```

`locations` is een lijst van `{path, forward_scheme, forward_host, forward_port, advanced_config,
locations_advanced_config}`. Twee eigenschappen die verraderlijk zijn:

1. de lijst **vervangt** de bestaande locations bij een PUT/PATCH met een `locations`-veld;
2. een location erft **niet** de upstream van de host — geef altijd zelf scheme/host/port mee.

Een SSE-feed (`/api/stream`, `/ws`, …) moet `proxy_buffering off` + `proxy_read_timeout` > de
idle-time-out van de client krijgen, anders knipt nginx na 60 s af zonder foutmelding.

## Certificaten

| verzoek | opmerking |
| --- | --- |
| `GET /api/nginx/certificates` | lijst met `friendly_name`, `provider`, `expires_at`, `is_renewable` |
| `POST /api/nginx/certificates` | `{"provider":"letsencrypt","friendly_name":"domein","domain_names":["domein","*.domein"],"dns_challenge":true,"provider_json":"{\"dns_provider\":\"dns_cf\"}"}` — wildcards vereisen DNS-challenge |
| `DELETE /api/nginx/certificates/{id}` | weg (hosts die het gebruikten breken) |
| `POST /api/nginx/proxy-hosts/{id}` met `certificate_id:"new"` | LE cert + `ssl_forced` in één beweging |

Let's Encrypt: ~5 uitgiften per week per (wildcard)domein, en een mislukte poging telt mee.
Een host met `certificate_id: 0` serveert geen HTTPS — `curl -v` geeft dan een cert-fout of HTTP.

## Rest

| verzoek | opmerking |
| --- | --- |
| `GET /api/nginx/access-lists` | access lists (auth-bescherming) |
| `POST /api/nginx/proxy-hosts/{id}/tokens` | temporary tokens voor een access list |
| `GET /api/users` · `POST /api/users` | NPM-accounts; een token van een verwijderde user is dood |
| `GET /api/audit-log?recordsPerPage=N&pageNumber=1` | `{"result":[…]}` — laat zien wie wat veranderde (handig na "wie heeft dit gedaan?") |
| `GET /api/settings/config` | NPM-instellingen (default ports, LE-email, …) |

## Deze setup

- `bingo.speerpunt.roelschenk.com` → upstream `145.53.228.134:8091` (Coolify host-poort, zie de
  `bingo-admin` skill). Verzetten: `point <id> --host 145.53.228.134 --port 8091`.
- `npm.roelschenk.com` is zelf ook een proxy-host op NPM (de UI/API bereikbaar via 443); de
  NPM-poort 81 staat niet open op de firewall van de VPS.
- Coolify-apps publiceren host-poorten (8070, 8887, 6006, 2011, 8091…); die zijn alleen intern
  bereikbaar, alleen NPM mag erbij.
