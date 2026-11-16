#!/usr/bin/env bash
# Installeert de nginx-proxy-manager skill voor zowel Hermes als Pi.
# Repo is de bron; ~/.hermes/skills en ~/.pi/agent/skills zijn wat de agents laden.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NAME=nginx-proxy-manager

DESTS=()
DESTS+=("${HERMES_SKILLS_DIR:-$HOME/.hermes/skills}/$NAME")
DESTS+=("${PI_SKILLS_DIR:-$HOME/.pi/agent/skills}/$NAME")

for DEST in "${DESTS[@]}"; do
  mkdir -p "$DEST"
  cp -a "$SRC/SKILL.md" "$SRC/scripts" "$SRC/references" "$DEST/"
  chmod +x "$DEST"/scripts/*.py
  echo "geïnstalleerd: $DEST"
done

ENVF="${HERMES_TOKEN_DIR:-$HOME/.hermes/mcp-tokens}/npm.env"
if [ -f "$ENVF" ]; then
  echo "config gevonden: $ENVF"
  [ "$(stat -c '%a' "$ENVF" 2>/dev/null || stat -f '%Lp' "$ENVF")" = "600" ] || echo "· zet chmod 600 op $ENVF"
else
  cat <<EOF
config ontbreekt: $ENVF  (chmod 600)
  NPM_URL=https://npm.roelschenk.com
  NPM_EMAIL=<npm-admin>
  NPM_PASSWORD='<wachtwoord>'
EOF
fi

for DEST in "${DESTS[@]}"; do
  echo "-- health ($DEST)"
  python3 "$DEST/scripts/npm.py" health || true
done
echo "-- doctor"
python3 "${DESTS[0]}/scripts/npm.py" doctor 2>&1 | sed 's/^/  /' || echo "  (doctor heeft NPM_EMAIL/NPM_PASSWORD nodig in $ENVF)"
