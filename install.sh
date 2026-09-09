#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${BROUTE_REPO:-xbroute/broute-mirza-reseller-bridge}"
REF="${BROUTE_REF:-main}"
BASE="/opt/broute-bridge"
RELEASES="$BASE/releases"
CURRENT="$BASE/current"
ETC="/etc/broute-bridge"
STATE="/var/lib/broute-bridge"
BACKUPS="/var/backups/broute-bridge"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
RELEASE="$RELEASES/$STAMP"
RESTORE="$BACKUPS/install-$STAMP"
PREVIOUS=""
COMMITTED=0
HAD_SERVICE=0; HAD_ENV=0; HAD_KEY=0; HAD_DB=0; HAD_CURRENT=0; HAD_CLI=0; HAD_UPDATER=0; HAD_SETUP=0

log(){ printf '[broute] %s\n' "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "Run as root"
for c in curl tar python3 systemctl; do command -v "$c" >/dev/null || fail "$c is required"; done

mkdir -p "$RELEASES" "$ETC" "$STATE" "$BACKUPS" "$RESTORE"
chmod 0700 "$ETC" "$STATE" "$BACKUPS"

[[ -f /etc/systemd/system/broute-bridge.service ]] && HAD_SERVICE=1
[[ -f "$ETC/bridge.env" ]] && HAD_ENV=1
[[ -f "$ETC/master.key" ]] && HAD_KEY=1
[[ -f "$STATE/bridge.db" ]] && HAD_DB=1
[[ -L "$CURRENT" ]] && HAD_CURRENT=1
[[ -e /usr/local/bin/broute-bridge || -L /usr/local/bin/broute-bridge ]] && HAD_CLI=1
[[ -e /usr/local/bin/broute-bridge-update || -L /usr/local/bin/broute-bridge-update ]] && HAD_UPDATER=1
[[ -e /usr/local/bin/broute-bridge-setup || -L /usr/local/bin/broute-bridge-setup ]] && HAD_SETUP=1

if [[ $HAD_CURRENT -eq 1 ]]; then PREVIOUS="$(readlink -f "$CURRENT")"; fi
[[ $HAD_SERVICE -eq 1 ]] && cp -a /etc/systemd/system/broute-bridge.service "$RESTORE/"
[[ $HAD_ENV -eq 1 ]] && cp -a "$ETC/bridge.env" "$RESTORE/"
[[ $HAD_KEY -eq 1 ]] && cp -a "$ETC/master.key" "$RESTORE/"
[[ $HAD_CLI -eq 1 ]] && cp -aL /usr/local/bin/broute-bridge "$RESTORE/broute-bridge.cli" || true
[[ $HAD_UPDATER -eq 1 ]] && cp -aL /usr/local/bin/broute-bridge-update "$RESTORE/broute-bridge-update.cli" || true
[[ $HAD_SETUP -eq 1 ]] && cp -aL /usr/local/bin/broute-bridge-setup "$RESTORE/broute-bridge-setup.cli" || true
if [[ $HAD_DB -eq 1 ]]; then
python3 - <<PY
import sqlite3
src=sqlite3.connect('$STATE/bridge.db'); dst=sqlite3.connect('$RESTORE/bridge.db'); src.backup(dst); dst.close(); src.close()
PY
fi
cat >"$RESTORE/manifest.env" <<EOF
HAD_SERVICE=$HAD_SERVICE
HAD_ENV=$HAD_ENV
HAD_KEY=$HAD_KEY
HAD_DB=$HAD_DB
HAD_CURRENT=$HAD_CURRENT
HAD_CLI=$HAD_CLI
HAD_UPDATER=$HAD_UPDATER
HAD_SETUP=$HAD_SETUP
PREVIOUS=$PREVIOUS
EOF

rollback(){
  rc=$?
  trap - ERR
  if [[ $COMMITTED -eq 0 ]]; then
    log "Install/update failed; restoring pre-change state"
    systemctl stop broute-bridge >/dev/null 2>&1 || true
    if [[ $HAD_CURRENT -eq 1 && -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then ln -sfn "$PREVIOUS" "$CURRENT"; else rm -f "$CURRENT"; fi
    if [[ $HAD_SERVICE -eq 1 ]]; then cp -a "$RESTORE/broute-bridge.service" /etc/systemd/system/broute-bridge.service; else rm -f /etc/systemd/system/broute-bridge.service; fi
    if [[ $HAD_ENV -eq 1 ]]; then cp -a "$RESTORE/bridge.env" "$ETC/bridge.env"; else rm -f "$ETC/bridge.env"; fi
    if [[ $HAD_KEY -eq 1 ]]; then cp -a "$RESTORE/master.key" "$ETC/master.key"; else rm -f "$ETC/master.key"; fi
    if [[ $HAD_DB -eq 1 ]]; then cp -a "$RESTORE/bridge.db" "$STATE/bridge.db"; else rm -f "$STATE/bridge.db" "$STATE/bridge.db-wal" "$STATE/bridge.db-shm"; fi
    if [[ $HAD_CLI -eq 1 ]]; then install -m 0755 "$RESTORE/broute-bridge.cli" /usr/local/bin/broute-bridge; else rm -f /usr/local/bin/broute-bridge; fi
    if [[ $HAD_UPDATER -eq 1 ]]; then install -m 0755 "$RESTORE/broute-bridge-update.cli" /usr/local/bin/broute-bridge-update; else rm -f /usr/local/bin/broute-bridge-update; fi
    if [[ $HAD_SETUP -eq 1 ]]; then install -m 0755 "$RESTORE/broute-bridge-setup.cli" /usr/local/bin/broute-bridge-setup; else rm -f /usr/local/bin/broute-bridge-setup; fi
    systemctl daemon-reload || true
    if [[ $HAD_SERVICE -eq 1 ]]; then systemctl restart broute-bridge || true; fi
    rm -rf "$RELEASE"
  fi
  exit "$rc"
}
trap rollback ERR

tmp="$(mktemp -d)"
cleanup(){ rm -rf "$tmp"; }
trap cleanup EXIT
log "Downloading $REPO@$REF"
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$REF.tar.gz" -o "$tmp/src.tar.gz"
tar -xzf "$tmp/src.tar.gz" -C "$tmp"
src="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -1)"
cp -a "$src/." "$RELEASE/"
python3 -m compileall -q "$RELEASE/broute_bridge" "$RELEASE/scripts"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install -q --upgrade pip
"$RELEASE/.venv/bin/pip" install -q "$RELEASE"

if ! id broute-bridge >/dev/null 2>&1; then useradd --system --home "$STATE" --shell /usr/sbin/nologin broute-bridge; fi
chown -R broute-bridge:broute-bridge "$STATE"
if [[ ! -f "$ETC/master.key" ]]; then
  "$RELEASE/.venv/bin/python" - <<PY
from pathlib import Path
from broute_bridge.crypto import SecretBox
SecretBox.ensure_key_file(Path('$ETC/master.key'))
PY
fi
chmod 0600 "$ETC/master.key"
if [[ ! -f "$ETC/bridge.env" ]]; then
cat >"$ETC/bridge.env" <<EOF
BROUTE_DB_PATH=$STATE/bridge.db
BROUTE_MASTER_KEY_PATH=$ETC/master.key
BROUTE_BIND_HOST=127.0.0.1
BROUTE_BIND_PORT=8765
BROUTE_VERIFY_RESELLER_TLS=true
BROUTE_REPLAY_WINDOW_SECONDS=120
EOF
fi
chmod 0600 "$ETC/bridge.env"

install -m 0644 "$RELEASE/deploy/systemd/broute-bridge.service" /etc/systemd/system/broute-bridge.service
ln -sfn "$RELEASE" "$CURRENT"
ln -sfn "$CURRENT/.venv/bin/broute-bridge" /usr/local/bin/broute-bridge
cat >/usr/local/bin/broute-bridge-update <<EOF
#!/usr/bin/env bash
exec bash <(curl -fsSL https://raw.githubusercontent.com/$REPO/$REF/install.sh)
EOF
chmod 0755 /usr/local/bin/broute-bridge-update
ln -sfn "$CURRENT/scripts/setup-wizard.sh" /usr/local/bin/broute-bridge-setup
systemctl daemon-reload
systemctl enable --now broute-bridge
for _ in {1..20}; do curl -fsS http://127.0.0.1:8765/healthz >/dev/null && break; sleep 1; done
curl -fsS http://127.0.0.1:8765/healthz >/dev/null || fail "Bridge health check failed"
COMMITTED=1
trap - ERR
log "Installed release $STAMP"
log "Pre-change restore point: $RESTORE"
log "Run production setup wizard: sudo broute-bridge-setup"
log "Or add a seller manually: sudo broute-bridge profile-add --name NAME --reseller-url https://... --inbounds 1,2"
