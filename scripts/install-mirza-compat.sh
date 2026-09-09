#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${BROUTE_REPO:-xbroute/broute-mirza-reseller-bridge}"
REF="${BROUTE_REF:-main}"
ETC="/etc/broute-mirza-compat"
LIB="/usr/local/lib/broute-mirza-compat"
BACKUPS="/var/backups/broute-mirza-compat"
STATE="/var/lib/broute-mirza-compat"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/$STAMP"
PATCHER="$LIB/mirza_patch.py"
ENVFILE="$ETC/config.env"

log(){ printf '[broute-mirza] %s\n' "$*"; }
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "Run as root. Example: sudo bash install-mirza-compat.sh"
for c in python3 php systemctl sha256sum curl; do command -v "$c" >/dev/null || fail "$c is required"; done

find_mirza_root(){
  if [[ -n "${MIRZA_ROOT:-}" && -f "$MIRZA_ROOT/panels.php" && -f "$MIRZA_ROOT/mirza_agent.php" ]]; then
    printf '%s\n' "$MIRZA_ROOT"; return 0
  fi
  local candidates=(/var/www/html /var/www/mirzabot /opt/mirzabot /root/mirzabot /home/mirzabot)
  local p
  for p in "${candidates[@]}"; do
    if [[ -f "$p/panels.php" && -f "$p/mirza_agent.php" && -f "$p/request.php" ]]; then
      printf '%s\n' "$p"; return 0
    fi
  done
  while IFS= read -r p; do
    p="$(dirname "$p")"
    if [[ -f "$p/mirza_agent.php" && -f "$p/request.php" ]]; then printf '%s\n' "$p"; return 0; fi
  done < <(find /var/www /opt /root /home -maxdepth 4 -name panels.php -type f 2>/dev/null | head -20)
  return 1
}

MIRZA_ROOT="$(find_mirza_root || true)"
if [[ -z "$MIRZA_ROOT" ]]; then
  cat >&2 <<'EOF'
Could not locate MirzaBot automatically.
This installer needs the directory containing panels.php, mirza_agent.php and request.php.
Re-run with:
  sudo MIRZA_ROOT=/path/to/mirzabot bash install-mirza-compat.sh
No files were changed.
EOF
  exit 2
fi

cat <<EOF

Broute Mirza compatibility installer
------------------------------------
MirzaBot directory: $MIRZA_ROOT

What this changes:
  1) Mirza Agent reset becomes a real backend reset instead of fake success.
  2) Mirza's Methodextend value is forwarded to the Bridge.
  3) TLS verification is enabled for Mirza-Agent requests only.

What this does NOT change:
  - Other Mirza panel adapters keep their existing TLS behaviour.
  - Your Mirza database, invoices and users are not migrated by this tool.
  - The Bridge/Reseller credentials are not written by this installer.

Before each patch a versioned copy of the exact changed files is stored in:
  $BACKUPS

Rollback command after install:
  sudo broute-mirza-compat rollback
EOF

mkdir -p "$ETC" "$LIB" "$BACKUPS" "$STATE" "$BACKUP"
chmod 0700 "$ETC" "$BACKUPS" "$STATE"

for f in request.php mirza_agent.php panels.php; do
  cp -a "$MIRZA_ROOT/$f" "$BACKUP/$f"
done
(
  cd "$MIRZA_ROOT"
  sha256sum request.php mirza_agent.php panels.php
) > "$BACKUP/sha256.before"
cat > "$BACKUP/manifest.env" <<EOF
MIRZA_ROOT=$MIRZA_ROOT
CREATED_AT=$STAMP
REPO=$REPO
REF=$REF
EOF
chmod 0600 "$BACKUP/manifest.env"

# Download to a temporary path, compile-check, then atomically replace the local patcher.
tmp_patcher="$(mktemp)"
cleanup(){ rm -f "$tmp_patcher"; }
trap cleanup EXIT
if ! curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/scripts/mirza_patch.py" -o "$tmp_patcher"; then
  fail "Could not download the compatibility patcher. Original Mirza files are unchanged. Backup: $BACKUP"
fi
python3 -m py_compile "$tmp_patcher" || fail "Downloaded patcher failed Python syntax validation; Mirza was not modified"
install -m 0755 "$tmp_patcher" "$PATCHER"

# Fail closed before writing anything.
python3 "$PATCHER" --root "$MIRZA_ROOT" --check || fail "This Mirza version is not safely patchable. No Mirza files were modified. Backup: $BACKUP"

restore_backup(){
  log "Patch validation failed; restoring original Mirza files"
  for f in request.php mirza_agent.php panels.php; do cp -a "$BACKUP/$f" "$MIRZA_ROOT/$f"; done
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then restore_backup; fi; exit $rc' ERR
python3 "$PATCHER" --root "$MIRZA_ROOT" --apply
for f in request.php mirza_agent.php panels.php; do php -l "$MIRZA_ROOT/$f" >/dev/null; done
trap - ERR

cat > "$ENVFILE" <<EOF
MIRZA_ROOT=$MIRZA_ROOT
BROUTE_REPO=$REPO
BROUTE_REF=$REF
EOF
chmod 0600 "$ENVFILE"

cat > /usr/local/bin/broute-mirza-compat <<'CMD'
#!/usr/bin/env bash
set -Eeuo pipefail
ENVFILE=/etc/broute-mirza-compat/config.env
PATCHER=/usr/local/lib/broute-mirza-compat/mirza_patch.py
BACKUPS=/var/backups/broute-mirza-compat
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }
[[ -f "$ENVFILE" ]] || { echo "Compatibility config not found" >&2; exit 2; }
# shellcheck disable=SC1090
source "$ENVFILE"
cmd="${1:-status}"; shift || true

refresh_patcher(){
  local tmp
  tmp="$(mktemp)"
  if curl -fsSL --connect-timeout 5 --max-time 15 "https://raw.githubusercontent.com/${BROUTE_REPO}/${BROUTE_REF}/scripts/mirza_patch.py" -o "$tmp" \
     && python3 -m py_compile "$tmp" >/dev/null 2>&1; then
    install -m 0755 "$tmp" "$PATCHER"
  fi
  rm -f "$tmp"
}

snapshot(){
  local stamp dir f
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  dir="$BACKUPS/$stamp"
  mkdir -p "$dir"; chmod 0700 "$dir"
  for f in request.php mirza_agent.php panels.php; do cp -a "$MIRZA_ROOT/$f" "$dir/$f"; done
  (cd "$MIRZA_ROOT" && sha256sum request.php mirza_agent.php panels.php) > "$dir/sha256.before"
  cat > "$dir/manifest.env" <<EOF
MIRZA_ROOT=$MIRZA_ROOT
CREATED_AT=$stamp
REPO=$BROUTE_REPO
REF=$BROUTE_REF
EOF
  chmod 0600 "$dir/manifest.env"
  echo "$dir"
}

case "$cmd" in
  check)
    python3 "$PATCHER" --root "$MIRZA_ROOT" --check
    ;;
  apply)
    refresh_patcher || true
    check_output="$(python3 "$PATCHER" --root "$MIRZA_ROOT" --check)"
    printf '%s\n' "$check_output"
    if grep -Fq "COMPATIBLE: already patched" <<<"$check_output"; then
      echo "No compatibility changes are required."
      exit 0
    fi
    dir="$(snapshot)"
    if ! python3 "$PATCHER" --root "$MIRZA_ROOT" --apply; then
      for f in request.php mirza_agent.php panels.php; do cp -a "$dir/$f" "$MIRZA_ROOT/$f"; done
      echo "Patch failed; restored $dir" >&2
      exit 1
    fi
    for f in request.php mirza_agent.php panels.php; do
      if ! php -l "$MIRZA_ROOT/$f" >/dev/null; then
        for r in request.php mirza_agent.php panels.php; do cp -a "$dir/$r" "$MIRZA_ROOT/$r"; done
        echo "PHP lint failed; restored $dir" >&2
        exit 1
      fi
    done
    echo "Compatibility patch OK. Restore point: $dir"
    ;;
  rollback)
    systemctl disable --now broute-mirza-compat.path broute-mirza-compat.timer >/dev/null 2>&1 || true
    target="${1:-}"
    if [[ -z "$target" || "$target" == "latest" ]]; then target="$(find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d | sort -r | head -1)"; fi
    [[ -d "$target" ]] || target="$BACKUPS/$target"
    [[ -f "$target/manifest.env" ]] || { echo "Invalid backup: $target" >&2; exit 2; }
    for f in request.php mirza_agent.php panels.php; do [[ -f "$target/$f" ]] || { echo "Backup missing $f" >&2; exit 2; }; done
    current="$(snapshot)"
    for f in request.php mirza_agent.php panels.php; do cp -a "$target/$f" "$MIRZA_ROOT/$f"; done
    for f in request.php mirza_agent.php panels.php; do
      if ! php -l "$MIRZA_ROOT/$f" >/dev/null; then
        for r in request.php mirza_agent.php panels.php; do cp -a "$current/$r" "$MIRZA_ROOT/$r"; done
        echo "Rollback target failed PHP lint; restored pre-rollback state" >&2
        exit 1
      fi
    done
    echo "Rolled back Mirza compatibility files from: $target"
    echo "Automatic watcher is disabled. Re-enable only after: sudo broute-mirza-compat check"
    ;;
  status)
    echo "Mirza root: $MIRZA_ROOT"
    python3 "$PATCHER" --root "$MIRZA_ROOT" --check || true
    systemctl is-active broute-mirza-compat.path 2>/dev/null || true
    systemctl is-active broute-mirza-compat.timer 2>/dev/null || true
    ;;
  *)
    echo "Usage: broute-mirza-compat {check|apply|rollback [backup]|status}" >&2
    exit 2
    ;;
esac
CMD
chmod 0755 /usr/local/bin/broute-mirza-compat

cat > /etc/systemd/system/broute-mirza-compat.service <<EOF
[Unit]
Description=Broute Mirza compatibility re-check
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/broute-mirza-compat apply
EOF

cat > /etc/systemd/system/broute-mirza-compat.path <<EOF
[Unit]
Description=Watch MirzaBot integration files for upstream updates

[Path]
PathChanged=$MIRZA_ROOT/request.php
PathChanged=$MIRZA_ROOT/mirza_agent.php
PathChanged=$MIRZA_ROOT/panels.php
Unit=broute-mirza-compat.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/broute-mirza-compat.timer <<'EOF'
[Unit]
Description=Periodic Broute Mirza compatibility check

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Unit=broute-mirza-compat.service
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now broute-mirza-compat.path broute-mirza-compat.timer
find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +21 | xargs -r rm -rf

cat <<EOF

Compatibility patch installed successfully.
Restore point: $BACKUP

Next step on this Hong Kong Mirza server:
  Do NOT put your xui_live_ Reseller API key in Mirza.
  Mirza only receives the br_live_ Bridge token generated on the Germany server.

Useful commands:
  sudo broute-mirza-compat status
  sudo broute-mirza-compat check
  sudo broute-mirza-compat apply
  sudo broute-mirza-compat rollback

If a future Mirza update changes the expected source layout, this tool fails closed:
it leaves the new upstream files untouched instead of guessing a patch.
EOF
