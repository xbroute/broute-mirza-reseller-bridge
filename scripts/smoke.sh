#!/usr/bin/env bash
set -Eeuo pipefail

URL="${1:-${BROUTE_BRIDGE_URL:-}}"
[[ -n "$URL" ]] || { echo "Usage: smoke.sh https://bridge.example.com" >&2; exit 2; }
URL="${URL%/}"
printf 'Bridge token (br_live_..., hidden): '
IFS= read -r -s TOKEN
printf '\n'
[[ -n "$TOKEN" ]] || { echo "Bridge token is required" >&2; exit 2; }

cfg="$(mktemp)"
chmod 0600 "$cfg"
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$cfg"
unset TOKEN
trap 'rm -f "$cfg"' EXIT

echo "[1/3] Public health endpoint"
curl -fsS "$URL/healthz"; echo

echo "[2/3] Authenticated Bridge -> Reseller doctor"
curl -fsS --config "$cfg" "$URL/admin/doctor"; echo

echo "[3/3] Mirza Agent list_panel contract"
curl -fsS --config "$cfg" "$URL/?actions=list_panel"; echo

echo "Smoke checks passed. No user was created or modified."
