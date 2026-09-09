#!/usr/bin/env bash
set -Eeuo pipefail

CURRENT="/opt/broute-bridge/current"
BACKUPS="/var/backups/broute-bridge"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
RESTORE="$BACKUPS/setup-$STAMP"
SITE="/etc/nginx/sites-available/broute-bridge"
ENABLED="/etc/nginx/sites-enabled/broute-bridge"
PROFILE_CREATED=0
NGINX_CHANGED=0
HAD_SITE=0
HAD_ENABLED=0
HAD_CERT=0
PROFILE=""
DOMAIN=""

log(){ printf '[broute-setup] %s\n' "$*"; }
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "Run with sudo: sudo broute-bridge-setup"
[[ -x /usr/local/bin/broute-bridge ]] || fail "Bridge is not installed. Run install.sh first."
[[ -d "$CURRENT" ]] || fail "Bridge release symlink is missing."

rollback_completed_setup(){
  local requested="${1:-latest}" target current_snapshot had_site_now=0 had_enabled_now=0
  if [[ "$requested" == "latest" || -z "$requested" ]]; then
    target="$(find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d -name 'setup-*' | sort -r | head -1)"
  elif [[ "$requested" = /* ]]; then
    target="$requested"
  else
    target="$BACKUPS/$requested"
  fi
  [[ -n "$target" && -d "$target" && -f "$target/manifest.env" ]] || fail "Restore point not found. Use: sudo broute-bridge-setup rollback latest"
  case "$(readlink -f "$target")" in
    "$(readlink -f "$BACKUPS")"/setup-*) ;;
    *) fail "Refusing restore point outside $BACKUPS/setup-*" ;;
  esac

  # manifest.env is generated only by this root-owned wizard in a 0700 directory.
  # shellcheck disable=SC1090
  source "$target/manifest.env"
  [[ "${PROFILE:-}" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || fail "Restore point contains an invalid profile name"
  [[ "${DOMAIN:-}" =~ ^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "Restore point contains an invalid domain"

  current_snapshot="$BACKUPS/pre-setup-rollback-$STAMP"
  mkdir -p "$current_snapshot"; chmod 0700 "$current_snapshot"
  [[ -f "$SITE" ]] && { had_site_now=1; cp -a "$SITE" "$current_snapshot/nginx-site"; }
  [[ -e "$ENABLED" || -L "$ENABLED" ]] && {
    had_enabled_now=1
    cp -aL "$ENABLED" "$current_snapshot/nginx-enabled-copy" 2>/dev/null || true
    readlink "$ENABLED" > "$current_snapshot/nginx-enabled-link" 2>/dev/null || true
  }

  cat <<EOF

ROLLBACK PREVIEW
================
Restore point: $target
Profile created by that setup: ${PROFILE}
Domain: ${DOMAIN}

This will restore the exact pre-setup Nginx site state, then remove only the Bridge profile
created by that setup. It does NOT downgrade x-ui-reseller-panel or 3x-ui.
A snapshot of the current Nginx state is being kept at:
  $current_snapshot
EOF
  read -r -p "Continue with this completed-setup rollback? [y/N]: " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "Cancelled. Nothing changed."; return 1; }

  if [[ "${HAD_SITE:-0}" -eq 1 ]]; then cp -a "$target/nginx-site" "$SITE"; else rm -f "$SITE"; fi
  if [[ "${HAD_ENABLED:-0}" -eq 1 ]]; then
    if [[ -s "$target/nginx-enabled-link" ]]; then
      ln -sfn "$(cat "$target/nginx-enabled-link")" "$ENABLED"
    elif [[ -f "$target/nginx-enabled-copy" ]]; then
      rm -f "$ENABLED"; cp -a "$target/nginx-enabled-copy" "$ENABLED"
    else
      fail "Restore point says Nginx site was enabled but contains no enabled-state backup"
    fi
  else
    rm -f "$ENABLED"
  fi

  if ! nginx -t; then
    log "Pre-setup Nginx state failed validation; restoring the pre-rollback Nginx state"
    if [[ $had_site_now -eq 1 ]]; then cp -a "$current_snapshot/nginx-site" "$SITE"; else rm -f "$SITE"; fi
    if [[ $had_enabled_now -eq 1 ]]; then
      if [[ -s "$current_snapshot/nginx-enabled-link" ]]; then ln -sfn "$(cat "$current_snapshot/nginx-enabled-link")" "$ENABLED";
      elif [[ -f "$current_snapshot/nginx-enabled-copy" ]]; then rm -f "$ENABLED"; cp -a "$current_snapshot/nginx-enabled-copy" "$ENABLED"; fi
    else rm -f "$ENABLED"; fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    fail "Rollback target Nginx config was invalid; current Nginx state was restored and the Bridge profile was NOT deleted"
  fi
  systemctl reload nginx

  if /usr/local/bin/broute-bridge profile-list | python3 -c 'import json,sys; name=sys.argv[1]; raise SystemExit(0 if any(json.loads(x).get("name")==name for x in sys.stdin if x.strip()) else 1)' "$PROFILE"; then
    /usr/local/bin/broute-bridge profile-delete --name "$PROFILE" --yes || fail "Nginx was restored but Bridge profile deletion failed; remove profile $PROFILE manually after inspection"
  else
    log "Bridge profile $PROFILE is already absent; continuing"
  fi

  if [[ "${HAD_CERT:-0}" -eq 0 && -d "/etc/letsencrypt/live/$DOMAIN" ]] && command -v certbot >/dev/null; then
    certbot delete --cert-name "$DOMAIN" --non-interactive >/dev/null 2>&1 || log "Certificate was left in place because Certbot could not remove it; it is no longer referenced by the restored Nginx site"
  fi

  cat <<EOF

ROLLBACK COMPLETE
=================
Restored from: $target
Current-state safety snapshot: $current_snapshot
Removed Bridge profile (if it still existed): $PROFILE

The Bridge application itself is still installed. If you also need to roll back its release:
  sudo broute-bridge rollback
EOF
}

if [[ "${1:-}" == "rollback" ]]; then
  rollback_completed_setup "${2:-latest}"
  exit $?
fi

cat <<'EOF'

Broute Bridge production setup
==============================
This wizard connects ONE reseller profile to the Bridge and publishes the Bridge
through HTTPS for your MirzaBot server.

You will be asked for:
  - Reseller API URL: the x-ui-reseller-panel public API base (not 3x-ui directly).
  - xui_live_ API key: created for that reseller in x-ui-reseller-panel.
  - Allowed/default inbound IDs: Bridge uses these when Mirza does not send an inbound.
  - Bridge domain: a DNS name pointing to this Germany server.
  - Mirza source IP/CIDR: only this Hong Kong source is allowed through Nginx.

Security model:
  - xui_live_ stays encrypted on this Germany server.
  - Mirza receives only a separate br_live_ token.
  - 3x-ui admin credentials are never sent to Mirza.
  - Every Nginx/profile change below has a local restore point.
EOF

read -r -p "Profile name [main]: " PROFILE
PROFILE="${PROFILE:-main}"
[[ "$PROFILE" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || fail "Profile name may contain only letters, numbers, dot, underscore and dash."

read -r -p "Reseller API base URL (example https://reseller.example.com): " RESELLER_URL
[[ -n "$RESELLER_URL" ]] || fail "Reseller URL is required."

printf 'Reseller API key (xui_live_..., hidden; never sent to Mirza): '
IFS= read -r -s RESELLER_KEY
printf '\n'
[[ -n "$RESELLER_KEY" ]] || fail "Reseller API key is required."

key_file="$(mktemp)"
probe_file="$(mktemp)"
chmod 0600 "$key_file" "$probe_file"
printf '%s' "$RESELLER_KEY" > "$key_file"
unset RESELLER_KEY
cleanup_tmp(){ rm -f "$key_file" "$probe_file"; }
trap cleanup_tmp EXIT

log "Checking Reseller credentials and loading allowed inbounds before storing any secret..."
if ! "$CURRENT/.venv/bin/python" - "$RESELLER_URL" "$key_file" > "$probe_file" <<'PY'
import json, sys
from pathlib import Path
import httpx
url=sys.argv[1].rstrip('/')
key=Path(sys.argv[2]).read_text().strip()
headers={'Authorization': f'Bearer {key}', 'Accept':'application/json'}
try:
    with httpx.Client(base_url=url, headers=headers, verify=True, timeout=15) as c:
        me=c.get('/api/v1/me'); me.raise_for_status()
        ib=c.get('/api/v1/inbounds'); ib.raise_for_status()
        payload=ib.json()
except Exception as exc:
    print(f'PRECHECK_ERROR={exc}')
    raise SystemExit(2)
rows=[]
if isinstance(payload, dict):
    rows=payload.get('inbounds') or ((payload.get('obj') or {}).get('inbounds') if isinstance(payload.get('obj'),dict) else []) or []
print('ME=' + json.dumps(me.json(), ensure_ascii=False))
for row in rows:
    if not isinstance(row, dict):
        continue
    iid=row.get('id')
    label=row.get('label') or row.get('remark') or row.get('tag') or f'Inbound {iid}'
    proto=row.get('protocol') or '?'; network=row.get('network') or '?'; port=row.get('port') or '?'
    print(f'INBOUND={iid}\t{label}\t{proto}/{network}\t:{port}')
PY
then
  cat "$probe_file" >&2
  cat >&2 <<'EOF'
The Reseller live check failed, so no profile/secret was stored.
Common causes:
  - wrong xui_live_ key or missing scopes
  - wrong API base URL
  - invalid/expired TLS certificate on the Reseller URL
  - Reseller account disabled
Fix that first, then run this wizard again.
EOF
  exit 2
fi

grep '^ME=' "$probe_file" | sed 's/^ME=/Reseller identity: /'
echo
echo "Allowed inbounds reported by x-ui-reseller-panel:"
if ! grep '^INBOUND=' "$probe_file" | sed $'s/^INBOUND=/  /'; then true; fi
if ! grep -q '^INBOUND=' "$probe_file"; then
  fail "Reseller returned no allowed inbounds. Configure allowed inbounds for this reseller first."
fi
cat <<'EOF'

Enter the inbound IDs this Mirza profile should use by default.
Example: 12,18,24
These must be from the list above. If Mirza sends no inbound selection, these defaults are used.
EOF
read -r -p "Default inbound IDs: " INBOUNDS
[[ -n "$INBOUNDS" ]] || fail "At least one inbound ID is required."

python3 - "$INBOUNDS" "$probe_file" <<'PY' || fail "Default inbound IDs must be numeric and must all exist in the allowed list above."
import re, sys
requested_raw=sys.argv[1].strip()
requested=[]
try:
    for value in re.split(r'[,;\s]+', requested_raw):
        if not value:
            continue
        number=int(value)
        if number <= 0:
            raise ValueError
        if number not in requested:
            requested.append(number)
except ValueError:
    raise SystemExit(2)
allowed=set()
with open(sys.argv[2], encoding='utf-8') as handle:
    for line in handle:
        if not line.startswith('INBOUND='):
            continue
        raw=line.split('\t',1)[0].split('=',1)[1]
        try:
            allowed.add(int(raw))
        except ValueError:
            pass
if not requested or any(value not in allowed for value in requested):
    bad=[value for value in requested if value not in allowed]
    print(f'Requested={requested}; allowed={sorted(allowed)}; forbidden={bad}', file=sys.stderr)
    raise SystemExit(3)
print(','.join(str(value) for value in requested))
PY
INBOUNDS="$(python3 - "$INBOUNDS" <<'PY'
import re,sys
values=[]
for raw in re.split(r'[,;\s]+', sys.argv[1].strip()):
    if raw:
        n=int(raw)
        if n not in values: values.append(n)
print(','.join(map(str,values)))
PY
)"

read -r -p "Bridge public domain (DNS must point to this Germany server): " DOMAIN
DOMAIN="${DOMAIN,,}"
[[ "$DOMAIN" =~ ^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "Enter a valid hostname such as bridge.example.com"

read -r -p "Hong Kong Mirza public source IP or CIDR (example 203.0.113.10 or 203.0.113.10/32): " MIRZA_SOURCE
[[ -n "$MIRZA_SOURCE" ]] || fail "Mirza source IP/CIDR is required."
python3 - "$MIRZA_SOURCE" <<'PY' || fail "Invalid Mirza IP/CIDR"
import ipaddress,sys
v=sys.argv[1]
try:
    ipaddress.ip_network(v, strict=False) if '/' in v else ipaddress.ip_address(v)
except ValueError as e:
    raise SystemExit(str(e))
PY

if ! command -v nginx >/dev/null || ! command -v certbot >/dev/null; then
  cat <<'EOF'

Nginx and Certbot are required to publish the Bridge safely.
The wizard can install them with apt. Package installation itself is NOT removed by
our rollback because removing shared packages could break other sites.
EOF
  read -r -p "Install nginx + certbot + python3-certbot-nginx now? [Y/n]: " ans
  ans="${ans:-Y}"
  [[ "$ans" =~ ^[Yy]$ ]] || fail "Install the required packages, then run the wizard again."
  command -v apt-get >/dev/null || fail "apt-get is unavailable; install nginx, certbot and the nginx certbot plugin manually."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx
fi

mkdir -p "$RESTORE"
chmod 0700 "$RESTORE"
[[ -f "$SITE" ]] && { HAD_SITE=1; cp -a "$SITE" "$RESTORE/nginx-site"; }
[[ -e "$ENABLED" || -L "$ENABLED" ]] && { HAD_ENABLED=1; cp -aL "$ENABLED" "$RESTORE/nginx-enabled-copy" 2>/dev/null || true; readlink "$ENABLED" > "$RESTORE/nginx-enabled-link" 2>/dev/null || true; }
[[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]] && HAD_CERT=1
cat > "$RESTORE/manifest.env" <<EOF
PROFILE=$PROFILE
DOMAIN=$DOMAIN
MIRZA_SOURCE=$MIRZA_SOURCE
HAD_SITE=$HAD_SITE
HAD_ENABLED=$HAD_ENABLED
HAD_CERT=$HAD_CERT
EOF
chmod 0600 "$RESTORE/manifest.env"

rollback_setup(){
  rc=$?
  trap - ERR
  log "Setup failed; rolling back changes made by this wizard"
  if [[ $PROFILE_CREATED -eq 1 ]]; then /usr/local/bin/broute-bridge profile-delete --name "$PROFILE" --yes >/dev/null 2>&1 || true; fi
  if [[ $NGINX_CHANGED -eq 1 ]]; then
    if [[ $HAD_SITE -eq 1 ]]; then cp -a "$RESTORE/nginx-site" "$SITE"; else rm -f "$SITE"; fi
    if [[ $HAD_ENABLED -eq 1 ]]; then
      if [[ -s "$RESTORE/nginx-enabled-link" ]]; then ln -sfn "$(cat "$RESTORE/nginx-enabled-link")" "$ENABLED"; elif [[ -f "$RESTORE/nginx-enabled-copy" ]]; then cp -a "$RESTORE/nginx-enabled-copy" "$ENABLED"; fi
    else
      rm -f "$ENABLED"
    fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  fi
  if [[ $HAD_CERT -eq 0 ]] && command -v certbot >/dev/null && [[ -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
    certbot delete --cert-name "$DOMAIN" --non-interactive >/dev/null 2>&1 || true
  fi
  echo "Restore point kept at: $RESTORE" >&2
  exit "$rc"
}
trap rollback_setup ERR

sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s#__MIRZA_SOURCE__#$MIRZA_SOURCE#g" \
  "$CURRENT/deploy/nginx/broute-bridge.conf.template" > "$SITE"
ln -sfn "$SITE" "$ENABLED"
NGINX_CHANGED=1
nginx -t
systemctl reload nginx

read -r -p "Email for Let's Encrypt expiry notices (leave blank to register without email): " LE_EMAIL
cert_args=(--nginx --non-interactive --agree-tos --redirect -d "$DOMAIN")
if [[ -n "$LE_EMAIL" ]]; then cert_args+=(--email "$LE_EMAIL"); else cert_args+=(--register-unsafely-without-email); fi
certbot "${cert_args[@]}"
nginx -t
systemctl reload nginx

log "Creating the encrypted Bridge profile. The xui_live_ key will be encrypted at rest."
profile_out="$(mktemp)"
chmod 0600 "$profile_out"
if ! cat "$key_file" | /usr/local/bin/broute-bridge profile-add \
    --name "$PROFILE" \
    --reseller-url "$RESELLER_URL" \
    --reseller-api-key-stdin \
    --inbounds "$INBOUNDS" > "$profile_out"; then
  rm -f "$profile_out"
  fail "Profile creation failed. Nginx/TLS will be rolled back automatically."
fi
PROFILE_CREATED=1
BRIDGE_TOKEN="$(grep '^br_live_' "$profile_out" | tail -1 || true)"
[[ -n "$BRIDGE_TOKEN" ]] || { cat "$profile_out"; rm -f "$profile_out"; fail "Profile was created but the one-time Bridge token could not be parsed"; }
rm -f "$profile_out"

# Verify Bridge -> Reseller without exposing br_live_ in process arguments.
bridge_curl_cfg="$(mktemp)"
chmod 0600 "$bridge_curl_cfg"
printf 'header = "Authorization: Bearer %s"\n' "$BRIDGE_TOKEN" > "$bridge_curl_cfg"
if ! curl -fsS --config "$bridge_curl_cfg" http://127.0.0.1:8765/admin/doctor >/dev/null; then
  rm -f "$bridge_curl_cfg"
  fail "Bridge -> Reseller doctor failed. Setup will be rolled back automatically."
fi
rm -f "$bridge_curl_cfg"
trap - ERR

cat <<EOF

SETUP COMPLETE
==============
Bridge URL: https://$DOMAIN
Profile:    $PROFILE
Defaults:   $INBOUNDS
Allowed source at Nginx: $MIRZA_SOURCE
Restore point: $RESTORE

BRIDGE TOKEN — COPY IT NOW; IT IS SHOWN ONCE:
$BRIDGE_TOKEN

Important: this is the br_live_ token for Mirza.
Do NOT put the xui_live_ Reseller key or any 3x-ui admin token into MirzaBot.

Now on the Hong Kong MirzaBot server run:
  curl -fsSL https://raw.githubusercontent.com/xbroute/broute-mirza-reseller-bridge/main/scripts/install-mirza-compat.sh | sudo bash

Then add/configure the panel in MirzaBot as Mirza Agent:
  Panel type: mirza_agent / Mirza Agent
  URL:        https://$DOMAIN
  Password/API key: the br_live_ token printed above

Inbound note:
  You do NOT need Mirza's sample-user set_inbounds flow for this integration.
  The Bridge profile already enforces the Reseller-approved defaults: $INBOUNDS

Verification after Mirza is configured:
  sudo broute-mirza-compat status        # on Hong Kong
  sudo broute-bridge doctor --live      # on Germany

Rollback if this Germany-side setup causes a problem:
  sudo broute-bridge-setup rollback latest   # restore pre-setup Nginx + remove this profile
  sudo broute-bridge rollback                # additionally roll back the Bridge application release
The exact pre-setup Nginx files are preserved in:
  $RESTORE
EOF
