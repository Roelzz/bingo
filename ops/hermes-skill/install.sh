#!/usr/bin/env bash
# Installeert/updated de Hermes-skill: repo is de bron, ~/.hermes/skills is wat Hermes laadt.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${HERMES_SKILLS_DIR:-$HOME/.hermes/skills}/bingo-admin"
mkdir -p "$DEST"
cp -a "$SRC/SKILL.md" "$SRC/scripts" "$SRC/references" "$DEST/"
chmod +x "$DEST"/scripts/*.py
echo "skill geïnstalleerd: $DEST"
ENVF="${HERMES_TOKEN_DIR:-$HOME/.hermes/mcp-tokens}/bingo.env"
if [ -f "$ENVF" ]; then
  echo "config gevonden: $ENVF"
else
  echo "config ontbreekt: $ENVF — zet BINGO_URL, BINGO_ADMIN_EMAIL, BINGO_ADMIN_PASSWORD en chmod 600"
fi
python3 "$DEST/scripts/bingo.py" doctor 2>&1 | sed 's/^/  /' || echo "  (doctor faalde — draait BINGO_URL? check $ENVF)"
