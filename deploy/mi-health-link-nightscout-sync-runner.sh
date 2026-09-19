#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/mi-health-link"

cd "$APP_DIR"

# Import Nightscout samples into Supabase before running the Xiaomi CGM mirror.
sync_output=""
if ! sync_output="$("$APP_DIR/.venv/bin/mi-health-link-nightscout-sync" 2>&1)"; then
  printf '%s\n' "$sync_output"
  exit 1
fi
printf '%s\n' "$sync_output"

# Mirror Nightscout into Xiaomi's real blood_sugar CGM channel. This v2 path
# normalizes timestamps to minute precision and tracks accepted points as
# pending instead of requiring Xiaomi Cloud to expose them immediately.
cgm_output=""
if ! cgm_output="$("$APP_DIR/.venv/bin/python" -m mi_health_link.cgm_mirror 2>&1)"; then
  printf '%s\n' "$cgm_output"
  detail="$(printf '%s\n' "$cgm_output" | grep -F 'Xiaomi CGM mirror failed:' | tail -1 || true)"
  detail="${detail#Xiaomi CGM mirror failed: }"
  printf '[cgm-mirror-error] %s\n' "${detail:-unknown}" >&2
  exit 1
fi
printf '%s\n' "$cgm_output"
