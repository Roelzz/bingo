#!/usr/bin/env python3
"""Bingo vanaf de commandlijn bij Coolify houden.

Token staat in ~/.hermes/mcp-tokens/coolify.env (600):
    COOLIFY_URL=https://coolify.roelschenk.com
    COOLIFY_TOKEN='1|xxxx'
App-uuid van speerpunt-bingo (project speerpunt-bingo, omgeving production):
    x0c4c80g040o4gc4go8c8csw

Commando's:
    python3 ops/coolify/deploy.py show            # status, domein, commit
    python3 ops/coolify/deploy.py deploy          # nieuwe versie bouwen + draaien (haal uit GitHub)
    python3 ops/coolify/deploy.py wait            # wachten tot de deploy klaar is, log erbij
    python3 ops/coolify/deploy.py logs            # recente containerlog
    python3 ops/coolify/deploy.py envs            # env-var namen tonen (nooit de waarden)
    python3 ops/coolify/deploy.py domains https://bingo.speerpunt.roelschenk.com
    python3 ops/coolify/deploy.py restart

Alleen standaardbibliotheek, dus overal te draaien.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_UUID = "x0c4c80g040o4gc4go8c8csw"
CRED = Path.home() / ".hermes/mcp-tokens/coolify.env"
STATE = Path("/tmp/coolify-bingo-deploy.json")


def conf() -> dict[str, str]:
    if not CRED.exists():
        sys.exit(f"geen {CRED} — zet daar COOLIFY_URL en COOLIFY_TOKEN neer (chmod 600)")
    out: dict[str, str] = {}
    for line in CRED.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


CF = conf()


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        CF["COOLIFY_URL"].rstrip("/") + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {CF['COOLIFY_TOKEN']}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} {method} {path}\n{e.read().decode()[:800]}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"{method} {path}: {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def show() -> None:
    a = api("GET", f"/api/v1/applications/{APP_UUID}")
    for k in ("uuid", "name", "fqdn", "status", "git_commit_sha", "ports_exposes", "custom_docker_run_options",
              "health_check_path", "is_auto_deploy_enabled"):
        print(f"{k}: {a.get(k)}")


def envs() -> None:
    for e in api("GET", f"/api/v1/applications/{APP_UUID}/envs"):
        print(f"{e.get('key'):<24} {'(letterlijk)' if e.get('is_literal') else ''}")


def deploy() -> None:
    out = api("POST", f"/api/v1/applications/{APP_UUID}/start")
    print(json.dumps(out)[:300])
    if isinstance(out, dict) and out.get("deployment_uuid"):
        STATE.write_text(json.dumps({"deployment_uuid": out["deployment_uuid"]}))
        wait()


def restart() -> None:
    print(json.dumps(api("POST", f"/api/v1/applications/{APP_UUID}/restart"))[:300])


def logs() -> None:
    out = api("GET", f"/api/v1/applications/{APP_UUID}/logs")
    if isinstance(out, dict):
        print("\n".join((out.get("logs") or "").splitlines()[-40:]))
    else:
        print(out)


def wait() -> None:
    if not STATE.exists():
        sys.exit("geen vorige deploy in de staat; run eerst: deploy")
    uuid = json.loads(STATE.read_text())["deployment_uuid"]
    for _ in range(60):
        d = api("GET", f"/api/v1/deployments/{uuid}")
        status = d.get("status")
        print(f"{time.strftime('%H:%M:%S')} {status}")
        if status in ("finished", "failed", "cancelled"):
            log = d.get("logs")
            if isinstance(log, str):
                log = json.loads(log)
            for entry in (log or [])[-6:]:
                out = (entry.get("output") if isinstance(entry, dict) else str(entry) or "")
                if out:
                    print(out[:600])
            return
        time.sleep(10)


def domains(urls: str) -> None:
    out = api("PATCH", f"/api/v1/applications/{APP_UUID}", {"domains": urls})
    print(out)
    show()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "domains":
        if len(sys.argv) < 3:
            sys.exit("gebruik: domains https://bingo.speerpunt.roelschenk.com")
        domains(sys.argv[2])
    else:
        {"show": show, "envs": envs, "deploy": deploy, "restart": restart, "logs": logs, "wait": wait}[cmd]()
