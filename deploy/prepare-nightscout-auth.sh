#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/mi-health-link}"
AUTH_DIR="$HOME/.config/mi-health-link"
AUTH_FILE="$AUTH_DIR/nightscout.env"

command -v docker >/dev/null 2>&1 || {
  echo "Nightscout auth preparation requires Docker access." >&2
  exit 1
}

cid="$(docker ps --format '{{.ID}}|{{.Image}}|{{.Names}}' 2>/dev/null | awk -F'|' 'tolower($0) ~ /nightscout|cgm-remote-monitor/ {print $1; exit}')"
[ -n "$cid" ] || {
  echo "Nightscout container not found." >&2
  exit 1
}

secret="$(docker inspect "$cid" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n 's/^API_SECRET=//p' | head -1)"
[ -n "$secret" ] || {
  echo "Nightscout API_SECRET not found in container environment." >&2
  exit 1
}

hash="$(printf '%s' "$secret" | "$APP_DIR/.venv/bin/python" -c 'import hashlib,sys; print(hashlib.sha1(sys.stdin.buffer.read()).hexdigest())')"
unset secret

install -d -m 700 "$AUTH_DIR"
umask 077
printf 'NIGHTSCOUT_API_SECRET_SHA1=%s\n' "$hash" > "$AUTH_FILE"
chmod 600 "$AUTH_FILE"
unset hash
