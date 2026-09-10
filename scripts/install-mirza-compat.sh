#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${BROUTE_REPO:-xbroute/broute-mirza-reseller-bridge}"
REF="${BROUTE_REF:-stable}"
ETC="/etc/broute-mirza-compat"
LIB="/usr/local/lib/broute-mirza-compat"
BACKUPS="/var/backups/broute-mirza-compat"
STATE="/var/lib/broute-mirza-compat"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
PATCHER="$LIB/mirza_patch.py"
ENVFILE="$ETC/config.env"
DISABLED="$ETC/disabled"
BACKUP=""

log(){ printf '[broute-mirza] %s\n' "$*"; }
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "Run as root. Example: sudo bash install-mirza-compat.sh"
for c in python3 php systemctl sha256sum curl find readlink; do command -v "$c" >/dev/null || fail "$c is required"; done

find_mirza_root(){
  if [[ -n "${MIRZA_ROOT:-}" && -f "$MIRZA_ROOT/panels.php" && -f "$MIRZA_ROOT/mirza_agent.php" && -f "$MIRZA_ROOT/request.php" ]]; then
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
MIRZA_ROOT="$(readlink -f "$MIRZA_ROOT")"

cat <<EOF

Broute Mirza compatibility installer
------------------------------------
MirzaBot directory: $MIRZA_ROOT
Source channel:      $REPO@$REF

What this changes:
  1) Mirza Agent reset becomes a real backend reset instead of fake success.
  2) Mirza's Methodextend value is forwarded to the Bridge.
  3) TLS verification is enabled for Mirza-Agent requests only.

What this does NOT change:
  - Other Mirza panel adapters keep their existing TLS behaviour.
  - Your Mirza database, invoices and users are not migrated by this tool.
  - Bridge/Reseller credentials are not written by this installer.

If source files need patching, an exact dated snapshot is created immediately before the write.
If Mirza is already patched, no misleading duplicate source snapshot is created.
Rollback command after install:
  sudo broute-mirza-compat rollback
EOF

mkdir -p "$ETC" "$LIB" "$BACKUPS" "$STATE"
chmod 0700 "$ETC" "$BACKUPS" "$STATE"

# Download to a temporary path, compile-check, then atomically replace our local patcher.
tmp_patcher="$(mktemp)"
cleanup(){ rm -f "$tmp_patcher"; }
trap cleanup EXIT
if ! curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/scripts/mirza_patch.py" -o "$tmp_patcher"; then
  fail "Could not download the compatibility patcher. Original Mirza files are unchanged."
fi
python3 -m py_compile "$tmp_patcher" || fail "Downloaded patcher failed Python syntax validation; Mirza was not modified"
install -m 0755 "$tmp_patcher" "$PATCHER"

# Fail closed before writing anything.
check_output="$(python3 "$PATCHER" --root "$MIRZA_ROOT" --check)" || fail "This Mirza version is not safely patchable. No Mirza files were modified."
printf '%s\n' "$check_output"

make_backup(){
  local dir="$BACKUPS/$(date -u +%Y%m%d-%H%M%S)" f
  mkdir -p "$dir"; chmod 0700 "$dir"
  for f in request.php mirza_agent.php panels.php; do cp -a "$MIRZA_ROOT/$f" "$dir/$f"; done
  (cd "$MIRZA_ROOT" && sha256sum request.php mirza_agent.php panels.php) > "$dir/sha256.before"
  {
    printf 'MIRZA_ROOT=%q\n' "$MIRZA_ROOT"
    printf 'CREATED_AT=%q\n' "$(basename "$dir")"
    printf 'REPO=%q\n' "$REPO"
    printf 'REF=%q\n' "$REF"
  } > "$dir/manifest.env"
  chmod 0600 "$dir/manifest.env"
  printf '%s\n' "$dir"
}

restore_backup(){
  [[ -n "$BACKUP" && -d "$BACKUP" ]] || return 0
  log "Patch validation failed; restoring original Mirza files"
  for f in request.php mirza_agent.php panels.php; do cp -a "$BACKUP/$f" "$MIRZA_ROOT/$f"; done
}

if ! grep -Fq "COMPATIBLE: already patched" <<<"$check_output"; then
  BACKUP="$(make_backup)"
  trap 'rc=$?; if [[ $rc -ne 0 ]]; then restore_backup; fi; exit $rc' ERR
  python3 "$PATCHER" --root "$MIRZA_ROOT" --apply
  for f in request.php mirza_agent.php panels.php; do php -l "$MIRZA_ROOT/$f" >/dev/null; done
  trap - ERR
else
  log "Mirza source is already compatible; source patch step skipped."
fi

{
  printf 'MIRZA_ROOT=%q\n' "$MIRZA_ROOT"
  printf 'BROUTE_REPO=%q\n' "$REPO"
  printf 'BROUTE_REF=%q\n' "$REF"
} > "$ENVFILE"
chmod 0600 "$ENVFILE"
rm -f "$DISABLED"

cat > /usr/local/bin/broute-mirza-compat <<'CMD'
#!/usr/bin/env bash
set -Eeuo pipefail
ENVFILE=/etc/broute-mirza-compat/config.env
PATCHER=/usr/local/lib/broute-mirza-compat/mirza_patch.py
BACKUPS=/var/backups/broute-mirza-compat
DISABLED=/etc/broute-mirza-compat/disabled
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }
[[ -f "$ENVFILE" ]] || { echo "Compatibility config not found" >&2; exit 2; }
# config.env is generated by the root installer with shell-escaped values and mode 0600.
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
  {
    printf 'MIRZA_ROOT=%q\n' "$MIRZA_ROOT"
    printf 'CREATED_AT=%q\n' "$stamp"
    printf 'REPO=%q\n' "$BROUTE_REPO"
    printf 'REF=%q\n' "$BROUTE_REF"
  } > "$dir/manifest.env"
  chmod 0600 "$dir/manifest.env"
  echo "$dir"
}

validate_backup_target(){
  local raw="$1" resolved root
  if [[ -z "$raw" || "$raw" == "latest" ]]; then
    raw="$(find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d | sort -r | head -1)"
  elif [[ "$raw" != /* ]]; then
    raw="$BACKUPS/$raw"
  fi
  [[ -n "$raw" && -d "$raw" ]] || { echo "Invalid backup: $raw" >&2; return 2; }
  resolved="$(readlink -f "$raw")"; root="$(readlink -f "$BACKUPS")"
  case "$resolved" in "$root"/*) ;; *) echo "Refusing backup outside $BACKUPS" >&2; return 2;; esac
  [[ -f "$resolved/manifest.env" ]] || { echo "Backup manifest missing: $resolved" >&2; return 2; }
  printf '%s\n' "$resolved"
}

apply_patch(){
  local check_output dir f r
  [[ ! -e "$DISABLED" ]] || { echo "Automatic compatibility is disabled after rollback. Use: sudo broute-mirza-compat watch-enable" >&2; return 3; }
  refresh_patcher || true
  check_output="$(python3 "$PATCHER" --root "$MIRZA_ROOT" --check)"
  printf '%s\n' "$check_output"
  if grep -Fq "COMPATIBLE: already patched" <<<"$check_output"; then
    echo "No compatibility changes are required."
    return 0
  fi
  dir="$(snapshot)"
  if ! python3 "$PATCHER" --root "$MIRZA_ROOT" --apply; then
    for f in request.php mirza_agent.php panels.php; do cp -a "$dir/$f" "$MIRZA_ROOT/$f"; done
    echo "Patch failed; restored $dir" >&2
    return 1
  fi
  for f in request.php mirza_agent.php panels.php; do
    if ! php -l "$MIRZA_ROOT/$f" >/dev/null; then
      for r in request.php mirza_agent.php panels.php; do cp -a "$dir/$r" "$MIRZA_ROOT/$r"; done
      echo "PHP lint failed; restored $dir" >&2
      return 1
    fi
  done
  echo "Compatibility patch OK. Restore point: $dir"
}

case "$cmd" in
  check)
    python3 "$PATCHER" --root "$MIRZA_ROOT" --check
    ;;
  apply)
    apply_patch
    ;;
  rollback)
    touch "$DISABLED"; chmod 0600 "$DISABLED"
    systemctl disable --now broute-mirza-compat.path broute-mirza-compat.timer >/dev/null 2>&1 || true
    target="$(validate_backup_target "${1:-latest}")" || exit $?
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
    echo "Automatic watcher is disabled. Re-enable only after review with: sudo broute-mirza-compat watch-enable"
    ;;
  watch-disable)
    touch "$DISABLED"; chmod 0600 "$DISABLED"
    systemctl disable --now broute-mirza-compat.path broute-mirza-compat.timer >/dev/null 2>&1 || true
    echo "Automatic Mirza compatibility watcher disabled. Source files were not changed."
    ;;
  watch-enable)
    refresh_patcher || true
    python3 "$PATCHER" --root "$MIRZA_ROOT" --check
    rm -f "$DISABLED"
    if ! apply_patch; then
      touch "$DISABLED"; chmod 0600 "$DISABLED"
      echo "Watcher remains disabled because compatibility apply failed." >&2
      exit 1
    fi
    systemctl enable --now broute-mirza-compat.path broute-mirza-compat.timer
    echo "Automatic Mirza compatibility watcher enabled."
    ;;
  status)
    echo "Mirza root: $MIRZA_ROOT"
    echo "Source channel: $BROUTE_REPO@$BROUTE_REF"
    if [[ -e "$DISABLED" ]]; then echo "Watcher policy: DISABLED after rollback/manual disable"; else echo "Watcher policy: enabled"; fi
    python3 "$PATCHER" --root "$MIRZA_ROOT" --check || true
    systemctl is-active broute-mirza-compat.path 2>/dev/null || true
    systemctl is-active broute-mirza-compat.timer 2>/dev/null || true
    ;;
  *)
    echo "Usage: broute-mirza-compat {check|apply|rollback [backup]|watch-disable|watch-enable|status}" >&2
    exit 2
    ;;
esac
CMD
chmod 0755 /usr/local/bin/broute-mirza-compat

cat > /etc/systemd/system/broute-mirza-compat.service <<EOF
[Unit]
Description=Broute Mirza compatibility re-check
After=network-online.target
Wants=network-online.target

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

Compatibility integration installed successfully.
Source channel: $REPO@$REF
$(if [[ -n "$BACKUP" ]]; then printf 'Source restore point: %s' "$BACKUP"; else printf 'Source restore point: not needed (already patched)'; fi)

Next step on this Hong Kong Mirza server:
  Do NOT put your xui_live_ Reseller API key in Mirza.
  Mirza only receives the br_live_ Bridge token generated on the Germany server.

Useful commands:
  sudo broute-mirza-compat status
  sudo broute-mirza-compat check
  sudo broute-mirza-compat apply
  sudo broute-mirza-compat rollback
  sudo broute-mirza-compat watch-disable
  sudo broute-mirza-compat watch-enable

If a future Mirza update changes the expected source layout, this tool fails closed:
it leaves the new upstream files untouched instead of guessing a patch.
EOF
