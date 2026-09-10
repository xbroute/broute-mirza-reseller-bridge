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
MIRZA_SOURCE=""
TX_ACTIVE=0
COMMITTED=0
TMPDIR_BRIDGE=""

log(){ printf '[broute-setup] %s\n' "$*"; }
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "Run with sudo: sudo broute-bridge-setup"
[[ -x /usr/local/bin/broute-bridge ]] || fail "Bridge is not installed. Run the stable install.sh first."
[[ -d "$CURRENT" ]] || fail "Bridge release symlink is missing."

manifest_value(){
  local file="$1" key="$2"
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

restore_nginx_snapshot(){
  local dir="$1" had_site="$2" had_enabled="$3"
  if [[ "$had_site" == "1" ]]; then
    cp -a "$dir/nginx-site" "$SITE"
  else
    rm -f "$SITE"
  fi

  if [[ "$had_enabled" == "1" ]]; then
    if [[ -s "$dir/nginx-enabled-link" ]]; then
      ln -sfn "$(cat "$dir/nginx-enabled-link")" "$ENABLED"
    elif [[ -f "$dir/nginx-enabled-copy" ]]; then
      rm -f "$ENABLED"
      cp -a "$dir/nginx-enabled-copy" "$ENABLED"
    else
      return 2
    fi
  else
    rm -f "$ENABLED"
  fi
}

rollback_completed_setup(){
  local requested="${1:-latest}" target manifest profile domain had_site had_enabled had_cert
  local current_snapshot had_site_now=0 had_enabled_now=0

  if [[ "$requested" == "latest" || -z "$requested" ]]; then
    target="$(find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d -name 'setup-*' | sort -r | head -1)"
  elif [[ "$requested" = /* ]]; then
    target="$requested"
  else
    target="$BACKUPS/$requested"
  fi
  [[ -n "$target" && -d "$target" && -f "$target/manifest.env" ]] || fail "Restore point not found. Use: sudo broute-bridge-setup rollback latest"
  target="$(readlink -f "$target")"
  case "$target" in
    "$(readlink -f "$BACKUPS")"/setup-*) ;;
    *) fail "Refusing restore point outside $BACKUPS/setup-*" ;;
  esac

  manifest="$target/manifest.env"
  profile="$(manifest_value "$manifest" PROFILE)"
  domain="$(manifest_value "$manifest" DOMAIN)"
  had_site="$(manifest_value "$manifest" HAD_SITE)"
  had_enabled="$(manifest_value "$manifest" HAD_ENABLED)"
  had_cert="$(manifest_value "$manifest" HAD_CERT)"

  [[ "$profile" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || fail "Restore point contains an invalid profile name"
  [[ "$domain" =~ ^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "Restore point contains an invalid domain"
  [[ "$had_site" =~ ^[01]$ && "$had_enabled" =~ ^[01]$ && "$had_cert" =~ ^[01]$ ]] || fail "Restore point flags are invalid"
  [[ "$had_site" == "0" || -f "$target/nginx-site" ]] || fail "Restore point is missing the previous Nginx site"

  current_snapshot="$BACKUPS/pre-setup-rollback-$STAMP"
  mkdir -p "$current_snapshot"; chmod 0700 "$current_snapshot"
  if [[ -f "$SITE" ]]; then had_site_now=1; cp -a "$SITE" "$current_snapshot/nginx-site"; fi
  if [[ -e "$ENABLED" || -L "$ENABLED" ]]; then
    had_enabled_now=1
    cp -aL "$ENABLED" "$current_snapshot/nginx-enabled-copy" 2>/dev/null || true
    readlink "$ENABLED" > "$current_snapshot/nginx-enabled-link" 2>/dev/null || true
  fi

  cat <<EOF

ROLLBACK PREVIEW
================
Restore point: $target
Profile created by that setup: $profile
Domain: $domain

This restores the exact pre-setup Nginx state and then removes only the Bridge profile
created by that setup. It does NOT downgrade x-ui-reseller-panel or 3x-ui.
Current Nginx safety snapshot:
  $current_snapshot
EOF
  read -r -p "Continue with this completed-setup rollback? [y/N]: " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "Cancelled. Nothing changed."; return 1; }

  if ! restore_nginx_snapshot "$target" "$had_site" "$had_enabled"; then
    restore_nginx_snapshot "$current_snapshot" "$had_site_now" "$had_enabled_now" || true
    fail "Selected restore point has an incomplete enabled-site backup; current Nginx state was restored"
  fi

  if ! nginx -t; then
    log "Pre-setup Nginx state failed validation; restoring pre-rollback Nginx state"
    restore_nginx_snapshot "$current_snapshot" "$had_site_now" "$had_enabled_now" || true
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    fail "Rollback target Nginx config was invalid; current Nginx state was restored and the Bridge profile was NOT deleted"
  fi
  systemctl reload nginx

  if /usr/local/bin/broute-bridge profile-list | python3 -c 'import json,sys; name=sys.argv[1]; raise SystemExit(0 if any(json.loads(x).get("name")==name for x in sys.stdin if x.strip()) else 1)' "$profile"; then
    /usr/local/bin/broute-bridge profile-delete --name "$profile" --yes || fail "Nginx was restored but Bridge profile deletion failed; remove profile $profile manually after inspection"
  else
    log "Bridge profile $profile is already absent; continuing"
  fi

  if [[ "$had_cert" == "0" && -d "/etc/letsencrypt/live/$domain" ]] && command -v certbot >/dev/null; then
    certbot delete --cert-name "$domain" --non-interactive >/dev/null 2>&1 || log "Certificate was left in place because Certbot could not remove it; restored Nginx no longer references it"
  fi

  cat <<EOF

ROLLBACK COMPLETE
=================
Restored from: $target
Current-state safety snapshot: $current_snapshot
Removed Bridge profile if it still existed: $profile

The Bridge application is still installed. To roll back its code release too:
  sudo broute-bridge rollback
For paired DB/master-key recovery when needed:
  sudo broute-bridge restore-state latest
EOF
}

if [[ "${1:-}" == "rollback" ]]; then
  rollback_completed_setup "${2:-latest}"
  exit $?
fi

cleanup_tmp(){
  [[ -n "$TMPDIR_BRIDGE" ]] && rm -rf "$TMPDIR_BRIDGE"
}

rollback_setup(){
  log "Setup failed; rolling back project-owned changes made by this wizard"
  if [[ $PROFILE_CREATED -eq 1 ]]; then
    /usr/local/bin/broute-bridge profile-delete --name "$PROFILE" --yes >/dev/null 2>&1 || true
  fi
  if [[ $NGINX_CHANGED -eq 1 ]]; then
    restore_nginx_snapshot "$RESTORE" "$HAD_SITE" "$HAD_ENABLED" || true
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  fi
  if [[ $HAD_CERT -eq 0 && -n "$DOMAIN" && -d "/etc/letsencrypt/live/$DOMAIN" ]] && command -v certbot >/dev/null; then
    certbot delete --cert-name "$DOMAIN" --non-interactive >/dev/null 2>&1 || true
  fi
  printf 'Restore point kept at: %s\n' "$RESTORE" >&2
}

on_exit(){
  local rc=$?
  trap - EXIT ERR
  set +e
  cleanup_tmp
  if [[ $rc -ne 0 && $TX_ACTIVE -eq 1 && $COMMITTED -eq 0 ]]; then
    rollback_setup
  fi
  exit "$rc"
}
trap on_exit EXIT

cat <<'EOF'

Broute Bridge production setup
==============================
This wizard connects ONE x-ui-reseller-panel representative to the Bridge and publishes
that Bridge through HTTPS for your Hong Kong MirzaBot server.

You will be asked for:
  - Reseller API URL: the x-ui-reseller-panel base URL; never the 3x-ui admin API.
  - xui_live_ API key: created for this representative in x-ui-reseller-panel.
  - Default inbound IDs: selected only from inbounds the Reseller API says are allowed.
  - Bridge domain: DNS name pointing to this Germany server.
  - Mirza source IP/CIDR: only that source is allowed through this Nginx vhost.

Security/recovery:
  - xui_live_ stays encrypted on Germany and is never sent to Mirza.
  - Mirza gets only a separate br_live_ token.
  - 3x-ui admin credentials never enter this workflow.
  - Reseller credentials/inbounds are checked before any secret is stored.
  - Nginx/profile changes get a restore point and every unsuccessful exit rolls them back.
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

TMPDIR_BRIDGE="$(mktemp -d)"
chmod 0700 "$TMPDIR_BRIDGE"
key_file="$TMPDIR_BRIDGE/reseller.key"
probe_file="$TMPDIR_BRIDGE/probe.txt"
printf '%s' "$RESELLER_KEY" > "$key_file"
chmod 0600 "$key_file"
unset RESELLER_KEY

log "Checking Reseller credentials and loading allowed inbounds before storing any secret..."
if ! "$CURRENT/.venv/bin/python" - "$RESELLER_URL" "$key_file" > "$probe_file" <<'PY'
import json, sys
from pathlib import Path
from urllib.parse import urlparse
import httpx

url=sys.argv[1].strip().rstrip('/')
parsed=urlparse(url)
if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
    print('PRECHECK_ERROR=Reseller URL must be a credential-free HTTPS URL')
    raise SystemExit(2)
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
No project configuration was changed and no Reseller secret was stored.
Common causes:
  - wrong xui_live_ key or missing API scopes
  - wrong API base URL
  - URL is HTTP instead of HTTPS
  - invalid/expired TLS certificate
  - representative account is disabled
Fix the cause and run the wizard again.
EOF
  exit 2
fi

grep '^ME=' "$probe_file" | sed 's/^ME=/Reseller identity: /'
echo
echo "Allowed inbounds reported by x-ui-reseller-panel:"
grep '^INBOUND=' "$probe_file" | sed $'s/^INBOUND=/  /' || true
grep -q '^INBOUND=' "$probe_file" || fail "Reseller returned no allowed inbounds. Configure allowed inbounds for this representative first."

cat <<'EOF'

Enter default inbound IDs for this Mirza profile.
Example: 12,18,24
Only IDs shown above are accepted. When Mirza does not specify an inbound, these defaults
are used. An invalid or forbidden ID stops here without changing the server.
EOF
read -r -p "Default inbound IDs: " INBOUNDS
[[ -n "$INBOUNDS" ]] || fail "At least one inbound ID is required."

INBOUNDS="$(python3 - "$INBOUNDS" "$probe_file" <<'PY'
import re, sys
requested=[]
try:
    for value in re.split(r'[,;\s]+', sys.argv[1].strip()):
        if not value: continue
        n=int(value)
        if n <= 0: raise ValueError
        if n not in requested: requested.append(n)
except ValueError:
    print('Inbound IDs must be positive integers.', file=sys.stderr)
    raise SystemExit(2)
allowed=set()
with open(sys.argv[2], encoding='utf-8') as handle:
    for line in handle:
        if line.startswith('INBOUND='):
            try: allowed.add(int(line.split('\t',1)[0].split('=',1)[1]))
            except ValueError: pass
bad=[x for x in requested if x not in allowed]
if not requested or bad:
    print(f'Forbidden/unknown IDs: {bad}; allowed IDs: {sorted(allowed)}', file=sys.stderr)
    raise SystemExit(3)
print(','.join(map(str, requested)))
PY
)" || fail "Default inbound selection is invalid. Nothing was changed."

read -r -p "Bridge public domain (DNS must already point to this Germany server): " DOMAIN
DOMAIN="${DOMAIN,,}"
[[ "$DOMAIN" =~ ^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || fail "Enter a valid hostname such as bridge.example.com"

read -r -p "Hong Kong Mirza public source IP or CIDR (example 203.0.113.10 or 203.0.113.10/32): " MIRZA_SOURCE
[[ -n "$MIRZA_SOURCE" ]] || fail "Mirza source IP/CIDR is required."
python3 - "$MIRZA_SOURCE" <<'PY' || fail "Invalid Mirza IP/CIDR. Nothing was changed."
import ipaddress,sys
v=sys.argv[1]
ipaddress.ip_network(v, strict=False) if '/' in v else ipaddress.ip_address(v)
PY

if ! command -v nginx >/dev/null || ! command -v certbot >/dev/null; then
  cat <<'EOF'

Nginx and Certbot are required to publish the Bridge safely.
The wizard can install them on apt-based systems. Package installation is intentionally NOT
removed by rollback, because those shared packages may later be used by other sites.
EOF
  read -r -p "Install nginx + certbot + python3-certbot-nginx now? [Y/n]: " ans
  ans="${ans:-Y}"
  [[ "$ans" =~ ^[Yy]$ ]] || fail "Install those packages and run the wizard again."
  command -v apt-get >/dev/null || fail "apt-get is unavailable; install nginx, certbot and the Nginx Certbot plugin manually."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx
fi

# Do not overwrite an unrelated site that happens to use our file name.
if [[ -f "$SITE" ]] && ! grep -Fq 'proxy_pass http://127.0.0.1:8765;' "$SITE"; then
  fail "$SITE already exists and does not look like a Broute Bridge vhost. Rename/remove it manually; the wizard will not overwrite it."
fi
# Refuse a duplicate server_name in other enabled sites; Certbot/Nginx behavior would be ambiguous.
if grep -Rsl --include='*' -E "^[[:space:]]*server_name[[:space:]]+([^;[:space:]]+[[:space:]]+)*${DOMAIN//./\.}([[:space:]]+[^;]+)*;" /etc/nginx/sites-enabled 2>/dev/null | grep -Fvx "$ENABLED" | grep -q .; then
  fail "Another enabled Nginx site already declares server_name $DOMAIN. Resolve that conflict first; nothing was changed."
fi

mkdir -p "$RESTORE"
chmod 0700 "$RESTORE"
if [[ -f "$SITE" ]]; then HAD_SITE=1; cp -a "$SITE" "$RESTORE/nginx-site"; fi
if [[ -e "$ENABLED" || -L "$ENABLED" ]]; then
  HAD_ENABLED=1
  cp -aL "$ENABLED" "$RESTORE/nginx-enabled-copy" 2>/dev/null || true
  readlink "$ENABLED" > "$RESTORE/nginx-enabled-link" 2>/dev/null || true
fi
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
TX_ACTIVE=1

NGINX_CHANGED=1
sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s#__MIRZA_SOURCE__#$MIRZA_SOURCE#g" \
  "$CURRENT/deploy/nginx/broute-bridge.conf.template" > "$SITE"
ln -sfn "$SITE" "$ENABLED"
nginx -t
systemctl reload nginx

read -r -p "Email for Let's Encrypt expiry notices (blank = register without email): " LE_EMAIL
cert_args=(--nginx --non-interactive --agree-tos --redirect -d "$DOMAIN")
if [[ -n "$LE_EMAIL" ]]; then cert_args+=(--email "$LE_EMAIL"); else cert_args+=(--register-unsafely-without-email); fi
certbot "${cert_args[@]}"
nginx -t
systemctl reload nginx

log "Creating encrypted Bridge profile. The xui_live_ key remains on Germany."
profile_out="$TMPDIR_BRIDGE/profile.out"
if ! /usr/local/bin/broute-bridge profile-add \
    --name "$PROFILE" \
    --reseller-url "$RESELLER_URL" \
    --reseller-api-key-stdin \
    --inbounds "$INBOUNDS" < "$key_file" > "$profile_out"; then
  fail "Profile creation failed. Nginx/TLS will be rolled back automatically."
fi
PROFILE_CREATED=1
BRIDGE_TOKEN="$(grep '^br_live_' "$profile_out" | tail -1 || true)"
[[ -n "$BRIDGE_TOKEN" ]] || fail "Profile was created but the one-time Bridge token could not be parsed; setup will roll back."

bridge_curl_cfg="$TMPDIR_BRIDGE/bridge-curl.conf"
printf 'header = "Authorization: Bearer %s"\n' "$BRIDGE_TOKEN" > "$bridge_curl_cfg"
chmod 0600 "$bridge_curl_cfg"
if ! curl -fsS --config "$bridge_curl_cfg" http://127.0.0.1:8765/admin/doctor >/dev/null; then
  fail "Bridge -> Reseller doctor failed. Setup will be rolled back automatically."
fi

COMMITTED=1

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

This is the br_live_ token for Mirza.
Do NOT put the xui_live_ Reseller key or any 3x-ui admin token into MirzaBot.

Next, on the Hong Kong MirzaBot server run the stable compatibility installer:
  curl -fsSL https://raw.githubusercontent.com/xbroute/broute-mirza-reseller-bridge/stable/scripts/install-mirza-compat.sh | sudo env BROUTE_REF=stable bash

Then add/configure the panel in MirzaBot:
  Panel type:       mirza_agent / Mirza Agent
  URL:              https://$DOMAIN
  Password/API key: the br_live_ token printed above

Inbound note:
  Do NOT use Mirza's sample-user set_inbounds flow for this integration.
  The Bridge profile already validates/enforces Reseller-approved defaults: $INBOUNDS

Verification:
  Hong Kong: sudo broute-mirza-compat status
  Germany:   sudo broute-bridge doctor --live

Rollback if this Germany-side setup causes a problem:
  sudo broute-bridge-setup rollback latest
If the Bridge application release itself must be rolled back:
  sudo broute-bridge rollback
If paired DB/master-key state must also be restored:
  sudo broute-bridge restore-state latest
EOF
