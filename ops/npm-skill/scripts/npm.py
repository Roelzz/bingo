#!/usr/bin/env python3
"""Nginx Proxy Manager vanaf de commandlijn: hosts, certificaat, access lists.

Standaard-library only. Inloggen en config:
  ~/.hermes/mcp-tokens/npm.env      NPM_URL, NPM_EMAIL, NPM_PASSWORD   (chmod 600)
  ~/.hermes/mcp-tokens/npm/<host>.json                                (sessietoken, 600)

Voorbeeld:
  npm.py doctor
  npm.py hosts
  npm.py find bingo
  npm.py point 12 --host 145.53.228.134 --port 8091
  npm.py sse 12 --stream /api/stream
  npm.py add sub.example.nl --host 10.0.0.5 --port 8080 --ssl-le
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKDIR = Path(os.environ.get("HERMES_TOKEN_DIR", Path.home() / ".hermes" / "mcp-tokens"))
CRED = TOKDIR / "npm.env"
UA = "bingo-npm-cli/1.0"
SSE_CONFIG = (
    "proxy_buffering off;\nproxy_cache off;\nproxy_read_timeout 86400s;\n"
    "proxy_send_timeout 86400s;\nchunked_transfer_encoding on;"
)


# ------------------------------------------------------------------ config --
def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) > 1 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def config(args) -> dict[str, str]:
    conf = read_env_file(CRED)
    for key in ("NPM_URL", "NPM_EMAIL", "NPM_PASSWORD"):
        if os.environ.get(key):
            conf[key] = os.environ[key]
    if getattr(args, "url", None):
        conf["NPM_URL"] = args.url
    if not conf.get("NPM_URL"):
        raise SystemExit(
            f"· geen NPM_URL — zet NPM_URL/NPM_EMAIL/NPM_PASSWORD in {CRED} (chmod 600)\n"
            "  voorbeeld: NPM_URL=https://npm.roelschenk.com"
        )
    conf["NPM_URL"] = conf["NPM_URL"].rstrip("/")
    return conf


def warn_permissions(path: Path) -> None:
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        print(f"· waarschuwing: {path} staat op {stat.S_IMODE(path.stat().st_mode):o}, mag 600 zijn", file=sys.stderr)


# --------------------------------------------------------------------- api --
class Npm:
    def __init__(self, conf: dict[str, str], otp: str | None = None):
        self.base = conf["NPM_URL"]
        self.email = conf.get("NPM_EMAIL", "")
        self.password = conf.get("NPM_PASSWORD", "")
        self.otp = otp
        self.token: str | None = None
        warn_permissions(CRED)

    # -- token cache: één bestand per host, dus nooit wachtwoord-herhaling
    def _cache(self) -> Path:
        host = re.sub(r"[^a-zA-Z0-9._-]", "_", urllib.parse.urlsplit(self.base).netloc)
        return TOKDIR / "npm" / f"{host}.json"

    def _load(self) -> None:
        path = self._cache()
        if path.exists():
            try:
                self.token = json.loads(path.read_text()).get("token")
            except json.JSONDecodeError:
                self.token = None

    def _save(self) -> None:
        path = self._cache()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"token": self.token, "url": self.base}))
        path.chmod(0o600)

    def login(self) -> None:
        if not self.email or not self.password:
            raise SystemExit(f"· NPM_EMAIL / NPM_PASSWORD ontbreken in {CRED}")
        body = {"identity": self.email, "secret": self.password}
        if self.otp:
            body["token"] = self.otp
        elif os.environ.get("NPM_OTP"):
            body["token"] = os.environ["NPM_OTP"]
        res = self.request("POST", "/api/tokens", body, auth=False)
        self.token = res.get("token")
        if not self.token:
            raise SystemExit(f"· login gaf geen token: {res}")
        self._save()

    def request(self, method: str, path: str, payload=None, auth: bool = True, retry: bool = True):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA,
                     **({"Authorization": f"Bearer {self.token}"} if auth and self.token else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
        except urllib.error.HTTPError as err:
            raw = err.read().decode()
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            if err.code == 401 and auth and retry:
                self.token = None
                self._cache().unlink(missing_ok=True)
                if not self.password:
                    raise SystemExit(f"· 401 en geen wachtwoord in {CRED} om opnieuw in te loggen")
                self.login()
                return self.request(method, path, payload, auth=auth, retry=False)
            if isinstance(detail, dict) and (detail.get("code") == "otpRequired" or "otp" in str(detail).lower()):
                code = getpass.getpass("· NPM vraagt een 2FA-code: ").strip()
                if payload is None and path == "/api/tokens":
                    raise
            raise SystemExit(f"· {method} {path} → {err.code}: {json.dumps(detail, ensure_ascii=False)[:400]}")
        except urllib.error.URLError as err:
            raise SystemExit(f"· {method} {path}: {err.reason}") from None

    def ensure(self) -> None:
        self._load()
        if not self.token:
            self.login()

    # -- hosts ----------------------------------------------------------
    def hosts(self) -> list[dict]:
        self.ensure()
        return self.request("GET", "/api/nginx/proxy-hosts?expand=owner,access_list,ssl_certificate") or []

    def host(self, host_id: int) -> dict:
        self.ensure()
        return self.request("GET", f"/api/nginx/proxy-hosts/{host_id}")

    def patch(self, host_id: int, body: dict) -> dict:
        self.ensure()
        try:
            return self.request("PATCH", f"/api/nginx/proxy-hosts/{host_id}", body)
        except SystemExit as err:      # oudere NPM zonder PATCH: volledige payload sturen
            if "405" not in str(err) and "not allowed" not in str(err):
                raise
            merged = self.host(host_id) | body
            return self.put(host_id, merged)

    def put(self, host_id: int, body: dict) -> dict:
        self.ensure()
        return self.request("PUT", f"/api/nginx/proxy-hosts/{host_id}", clean(body))


HOST_FIELDS = (
    "domain_names", "forward_host", "forward_port", "forward_scheme", "access_list_id", "certificate_id",
    "ssl_forced", "hsts_enabled", "hsts_subdomains", "http2_support", "block_exploits", "caching_enabled",
    "allow_websocket_upgrade", "advanced_config", "locations", "enabled", "meta", "nice_name",
)


def clean(host: dict) -> dict:
    return {k: host[k] for k in HOST_FIELDS if k in host}


def table(rows: list[dict], spec: list[tuple[str, str, int]]) -> str:
    head = "  ".join(label.ljust(width) for _, label, width in spec)
    lines = [head]
    for row in rows:
        lines.append("  ".join(str(row.get(key, ""))[:width].ljust(width) for key, _, width in spec))
    return "\n".join(lines)


def resolve_id(npm: Npm, ref: str) -> int:
    if str(ref).isdigit():
        return int(ref)
    needle = str(ref).lower()
    hits = []
    for h in npm.hosts():
        domains = [d.lower() for d in (h.get("domain_names") or [])]
        if any(d == needle or needle in d for d in domains):
            hits.append(h)
    if not hits:
        raise SystemExit(f"· geen proxy-host met id of domein {ref} — zie 'hosts'")
    if len(hits) > 1:
        raise SystemExit("· meer dan één host past bij " + ref + ": " + ", ".join(h["domain_names"][0] for h in hits))
    return hits[0]["id"]


def coerce_id(val: str | None):
    """certificate_id / access_list_id: cijfers worden getallen, "new" blijft tekst."""
    if val is None:
        return None
    return int(val) if str(val).isdigit() else val


def read_value(val: str | None) -> str | None:
    if val is None:
        return None
    if val.startswith("@"):
        return Path(val[1:]).read_text()
    return val


# ---------------------------------------------------------------- commands --
def cmd_health(npm: Npm, args):
    print(json.dumps(npm.request("GET", "/api/", auth=False), ensure_ascii=False))


def cmd_doctor(npm: Npm, args):
    info = npm.request("GET", "/api/", auth=False)
    print("· url    ", npm.base)
    print("· status ", info.get("status"), "| versie", info.get("version"), "| setup", info.get("setup"))
    npm.ensure()
    me = npm.request("GET", "/api/users/me")
    print(f"· ingelogd als {me.get('email')} (id {me.get('id')}, role {me.get('name')})")
    hosts = npm.hosts()
    certs = npm.request("GET", "/api/nginx/certificates") or []
    print(f"· {len(hosts)} proxy-hosts, {len(certs)} certifica(a)t(en)")
    dead = [h for h in hosts if not h.get("enabled")]
    if dead:
        print("· uitgeschakeld:", ", ".join(h["domain_names"][0] for h in dead))
    nohttps = [h for h in hosts if h.get("certificate_id") in (0, None)]
    if nohttps:
        print("· zonder certificaat:", ", ".join(h["domain_names"][0] for h in nohttps))


def cmd_hosts(npm: Npm, args):
    rows = npm.hosts()
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    print(table(rows, [("id", "id", 4), ("domain_names", "domeinen", 42), ("forward_scheme", "proto", 5),
                       ("forward_host", "forward-host", 17), ("forward_port", "port", 6),
                       ("ssl_forced", "https", 5), ("http2_support", "h2", 3), ("enabled", "aan", 5)]))


def cmd_find(npm: Npm, args):
    needle = args.query.lower()
    hits = [h for h in npm.hosts() if any(needle in d.lower() for d in (h.get("domain_names") or []))]
    if not hits:
        raise SystemExit(f"· niets gevonden op {args.query}")
    for h in hits:
        print(json.dumps(h, indent=2, ensure_ascii=False))


def cmd_host(npm: Npm, args):
    print(json.dumps(npm.host(resolve_id(npm, args.ref)), indent=2, ensure_ascii=False))


def cmd_point(npm: Npm, args):
    host_id = resolve_id(npm, args.ref)
    before = npm.host(host_id)
    body = {"forward_host": args.host, "forward_port": args.port, "forward_scheme": args.scheme}
    after = npm.patch(host_id, body)
    print(f"· {after['domain_names'][0]}: {before['forward_scheme']}://{before['forward_host']}:{before['forward_port']}"
          f" → {after['forward_scheme']}://{after['forward_host']}:{after['forward_port']}")


def cmd_set(npm: Npm, args):
    host_id = resolve_id(npm, args.ref)
    body: dict = {}
    if args.domain:
        body["domain_names"] = args.domain
    if args.name is not None:
        body["nice_name"] = args.name
    if args.cert is not None:
        body["certificate_id"] = coerce_id(args.cert)
    for flag, key in ((args.ssl, "ssl_forced"), (args.http2, "http2_support"), (args.ws, "allow_websocket_upgrade"),
                      (args.exploits, "block_exploits"), (args.caching, "caching_enabled"),
                      (args.enabled, "enabled"), (args.hsts, "hsts_enabled")):
        if flag is not None:
            body[key] = flag
    adv = read_value(args.advanced)
    if adv is not None:
        body["advanced_config"] = adv
    if args.location:
        cur = npm.host(host_id)
        locs = list(cur.get("locations") or [])
        for path in args.location:
            locs = [l for l in locs if l.get("path") != path]
            locs.append({"path": path, "forward_host": cur["forward_host"], "forward_port": str(cur["forward_port"]),
                         "forward_scheme": cur["forward_scheme"], "advanced_config": "", "locations_advanced_config": []})
        body["locations"] = locs
    if not body:
        raise SystemExit("· niets te doen — geef vlaggen (zie --help)")
    after = npm.patch(host_id, body)
    print(f"· host {host_id} bijgewerkt: {', '.join(sorted(body))}")
    print(json.dumps(clean(after), indent=2, ensure_ascii=False))


def cmd_sse(npm: Npm, args):
    """SSE/vriendelijke config: niet bufferen, lange timeouts, optioneel losse location."""
    host_id = resolve_id(npm, args.ref)
    cur = npm.host(host_id)
    body: dict = {"allow_websocket_upgrade": True, "advanced_config": (cur.get("advanced_config") or "")}
    if not (cur.get("advanced_config") or "").lstrip().startswith("proxy_buffering off"):
        joiner = "\n" if body["advanced_config"].strip() else ""
        body["advanced_config"] = body["advanced_config"] + joiner + SSE_CONFIG
    if args.stream:
        locs = [l for l in (cur.get("locations") or []) if l.get("path") != args.stream]
        locs.append({"path": args.stream, "forward_host": cur["forward_host"], "forward_port": str(cur["forward_port"]),
                     "forward_scheme": cur["forward_scheme"], "advanced_config": SSE_CONFIG,
                     "locations_advanced_config": []})
        body["locations"] = locs
    if args.body_size:
        body["advanced_config"] += f"\nclient_max_body_size {args.body_size};"
    after = npm.patch(host_id, body)
    print(f"· host {host_id}: SSE-aanpak erbij ({args.stream or 'host-niveau'})")
    print(after.get("advanced_config"))


def cmd_add(npm: Npm, args):
    domains = args.domain or [args.ref]
    cert: object = 0
    meta = {"letsencrypt_email": args.le_email or npm.email, "letsencrypt_agree": bool(args.ssl_le), "dns_challenge": bool(args.dns_challenge)}
    if args.ssl_le:
        cert = "new"
    elif args.cert is not None:
        cert = args.cert
    payload = clean({
        "domain_names": domains, "forward_scheme": args.scheme, "forward_host": args.host, "forward_port": args.port,
        "access_list_id": coerce_id(args.access_list), "certificate_id": cert, "ssl_forced": bool(args.force_https),
        "hsts_enabled": False, "hsts_subdomains": False, "http2_support": bool(args.http2),
        "block_exploits": not args.no_exploits, "caching_enabled": bool(args.caching),
        "allow_websocket_upgrade": True if args.ws is None else bool(args.ws),
        "advanced_config": read_value(args.advanced) or "", "locations": [], "enabled": True, "meta": meta,
        "nice_name": args.name or "",
    })
    npm.ensure()
    created = npm.request("POST", "/api/nginx/proxy-hosts", payload)
    print(f"· host {created['id']} aan: {', '.join(created['domain_names'])} → "
          f"{created['forward_scheme']}://{created['forward_host']}:{created['forward_port']}")
    if args.ssl_le:
        print("· Let's Encrypt aanvraag loopt; check met 'certs' (limiet ~5 certificaten per week per domein)")
    return created


def cmd_del(npm: Npm, args):
    host_id = resolve_id(npm, args.ref)
    host = npm.host(host_id)
    confirm(args, f"proxy-host {host_id} ({', '.join(host['domain_names'])}) verwijderen")
    npm.ensure()
    npm.request("DELETE", f"/api/nginx/proxy-hosts/{host_id}")
    print(f"· host {host_id} weg")


def cmd_toggle(npm: Npm, args, enabled: bool):
    host_id = resolve_id(npm, args.ref)
    npm.patch(host_id, {"enabled": enabled})
    print(f"· host {host_id} {'aan' if enabled else 'uit'}")


def cmd_certs(npm: Npm, args):
    rows = npm.request("GET", "/api/nginx/certificates") or []
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    print(table(rows, [("id", "id", 4), ("friendly_name", "domein", 40), ("provider", "bron", 14),
                       ("expires_at", "verloopt", 26), ("is_renewable", "vernieuwbaar", 12)]))


def cmd_cert_add(npm: Npm, args):
    npm.ensure()
    body = {"provider": "letsencrypt", "friendly_name": args.domain, "nice_name": args.domain,
            "dns_challenge": bool(args.dns_challenge), "provider_json": json.dumps({"dns_provider": "dns_cf"}),
            "domain_names": [args.domain, *([f"*.{args.domain}"] if args.wildcard else [])]}
    res = npm.request("POST", "/api/nginx/certificates", body)
    print(f"· certificaat {res.get('id')} voor {args.domain} ({res.get('provider')})")


def cmd_cert_del(npm: Npm, args):
    confirm(args, f"certificaat {args.cert_id} verwijderen")
    npm.ensure()
    npm.request("DELETE", f"/api/nginx/certificates/{args.cert_id}")
    print(f"· certificaat {args.cert_id} weg")


def cmd_lists(npm: Npm, args):
    npm.ensure()
    print(json.dumps(npm.request("GET", "/api/nginx/access-lists") or [], indent=2, ensure_ascii=False))


def cmd_users(npm: Npm, args):
    npm.ensure()
    print(table(npm.request("GET", "/api/users") or [], [("id", "id", 4), ("name", "naam", 26), ("email", "e-mail", 32),
                                                        ("is_disabled", "uit", 5)]))


def cmd_audit(npm: Npm, args):
    npm.ensure()
    rows = npm.request("GET", f"/api/audit-log?recordsPerPage={args.limit}&pageNumber=1")
    items = rows.get("result", rows) if isinstance(rows, dict) else rows
    print(table(items, [("created_at", "tijd", 26), ("action", "actie", 30), ("object_type", "soort", 14),
                        ("object_id", "id", 5), ("meta", "extra", 40)]))


def cmd_config(npm: Npm, args):
    npm.ensure()
    print(json.dumps(npm.request("GET", "/api/settings/config"), indent=2, ensure_ascii=False))


def cmd_raw(npm: Npm, args):
    body = json.loads(args.data) if args.data else None
    npm.ensure()
    print(json.dumps(npm.request(args.method, args.path, body), indent=2, ensure_ascii=False))


def confirm(args, what: str) -> None:
    if not getattr(args, "yes", False):
        raise SystemExit(f"· {what}? Doe dit met --yes (destructief)")


# ------------------------------------------------------------------ parser --
def build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Nginx Proxy Manager beheren")
    p.add_argument("--url", help="overrule NPM_URL")
    p.add_argument("--otp", help="2FA-code als NPM om een code vraagt")
    p.add_argument("--json", action="store_true", help="ruwe JSON waar mogelijk")
    p.add_argument("--yes", action="store_true", help="destructieve acties bevestigen")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        s = sub.add_parser(name, help=help)
        s.set_defaults(fn=fn)
        return s

    add("health", cmd_health, "NPM API benaderbaar? (geen login nodig)")
    add("doctor", cmd_doctor, "login + host-overzicht + valkuilen")
    add("hosts", cmd_hosts, "alle proxy-hosts")
    f = add("find", cmd_find, "hosts zoeken op (deel van een) domein")
    f.add_argument("query")
    h = add("host", cmd_host, "één host, volledige JSON")
    h.add_argument("ref", help="id of domein")
    po = add("point", cmd_point, "upstream van een host verzetten")
    po.add_argument("ref")
    po.add_argument("--host", required=True, help="forward_host (ip of dns-naam)")
    po.add_argument("--port", required=True, type=int)
    po.add_argument("--scheme", default="http", choices=["http", "https"])
    st = add("set", cmd_set, "vlaggen/config van een host veranderen")
    st.add_argument("ref")
    st.add_argument("--domain", action="append", help="domeinnaam; herhaal voor meerdere (vervangt de lijst)")
    st.add_argument("--name", help="nice_name (label in de UI)")
    st.add_argument("--cert", help="certificate_id: 0, een id, of 'new' voor Let's Encrypt")
    st.add_argument("--ssl", action=BooleanOptional, help="https-afdwingen (ssl_forced)")
    st.add_argument("--http2", action=BooleanOptional, help="http/2")
    st.add_argument("--ws", action=BooleanOptional, help="websocket upgrade toestaan")
    st.add_argument("--exploits", action=BooleanOptional, help="block_exploits (kan grote JSON-POSTs breken)")
    st.add_argument("--caching", action=BooleanOptional, help="caching_enabled")
    st.add_argument("--enabled", action=BooleanOptional, help="host aan/uit")
    st.add_argument("--hsts", action=BooleanOptional, help="hsts_enabled")
    st.add_argument("--advanced", help="advanced_config; @bestand kan ook")
    st.add_argument("--location", action="append", help="extra location-pad (herhaalbaar), overerving van de upstream")
    se = add("sse", cmd_sse, "niet-bufferen + lange timeouts (realtime feeds)")
    se.add_argument("ref")
    se.add_argument("--stream", help="losse location voor dit pad, bijv. /api/stream")
    se.add_argument("--body-size", help="client_max_body_size, bijv. 12m")
    ad = add("add", cmd_add, "nieuwe proxy-host")
    ad.add_argument("ref", help="domein (als --domain ontbreekt)")
    ad.add_argument("--domain", action="append")
    ad.add_argument("--host", required=True)
    ad.add_argument("--port", required=True, type=int)
    ad.add_argument("--scheme", default="http", choices=["http", "https"])
    ad.add_argument("--ssl-le", action="store_true", help="Let's Encrypt certificaat aanvragen")
    ad.add_argument("--cert", help="bestaande certificate_id")
    ad.add_argument("--le-email", help="e-mail voor Let's Encrypt")
    ad.add_argument("--dns-challenge", action="store_true", help="LE via DNS-challenge (nodig voor wildcards)")
    ad.add_argument("--force-https", action="store_true")
    ad.add_argument("--http2", action="store_true")
    ad.add_argument("--ws", action=BooleanOptional, default=None)
    ad.add_argument("--no-exploits", action="store_true")
    ad.add_argument("--caching", action="store_true")
    ad.add_argument("--access-list", default="0")
    ad.add_argument("--advanced")
    ad.add_argument("--name")
    de = add("del", cmd_del, "proxy-host verwijderen (--yes)")
    de.add_argument("ref")
    en = add("enable", cmd_toggle, "host aanzetten")
    en.add_argument("ref")
    en.set_defaults(fn=lambda npm, a: cmd_toggle(npm, a, True))
    di = add("disable", cmd_toggle, "host uitzetten")
    di.add_argument("ref")
    di.set_defaults(fn=lambda npm, a: cmd_toggle(npm, a, False))
    add("certs", cmd_certs, "certificaten")
    ca = add("cert-add", cmd_cert_add, "Let's Encrypt certificaat aanvragen")
    ca.add_argument("domain")
    ca.add_argument("--dns-challenge", action="store_true")
    ca.add_argument("--wildcard", action="store_true", help="ook *.domein (DNS-challenge nodig)")
    cd = add("cert-del", cmd_cert_del, "certificaat verwijderen (--yes)")
    cd.add_argument("cert_id", type=int)
    add("lists", cmd_lists, "access lists")
    add("users", cmd_users, "NPM-gebruikers")
    au = add("audit", cmd_audit, "audit-log")
    au.add_argument("--limit", type=int, default=20)
    add("config", cmd_config, "NPM instellingen (settings/config)")
    ra = add("raw", cmd_raw, "willekeurig API-verzoek")
    ra.add_argument("method")
    ra.add_argument("path")
    ra.add_argument("--data", help="JSON-body")
    return p


class BooleanOptional(argparse.Action):
    """--ssl / --no-ssl zoals argparse dat zou moeten doen."""

    def __init__(self, option_strings, dest, **kwargs):
        opts = []
        for opt in option_strings:
            opts.append(opt)
            opts.append("--no-" + opt.lstrip("-"))
        super().__init__(opts, dest=dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, not option_string.startswith("--no-"))


def main(argv=None) -> int:
    args = build().parse_args(argv)
    conf = config(args)
    npm = Npm(conf, otp=args.otp)
    args.fn(npm, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
