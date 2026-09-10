from __future__ import annotations

import argparse
import getpass
import grp
import ipaddress
import json
import os
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .crypto import SecretBox
from .db import Database, Profile
from .reseller import ResellerClient


def open_db(settings: Settings) -> Database:
    SecretBox.ensure_key_file(settings.master_key_path)
    return Database(settings.db_path, SecretBox.from_file(settings.master_key_path))


def _read_api_key(args: argparse.Namespace) -> str:
    if getattr(args, "reseller_api_key", None):
        return str(args.reseller_api_key).strip()
    if getattr(args, "reseller_api_key_stdin", False):
        value = sys.stdin.readline().strip()
    else:
        value = getpass.getpass("Reseller API key (xui_live_...): ").strip()
    if not value:
        raise ValueError("Reseller API key is required")
    return value


def _validate_reseller_url(url: str, *, allow_insecure_http: bool = False) -> str:
    normalized = str(url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Reseller URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("Do not embed credentials in the Reseller URL")
    if parsed.scheme == "http":
        host = parsed.hostname
        is_loopback = False
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host in {"localhost"}
        if not is_loopback and not allow_insecure_http:
            raise ValueError(
                "Plain HTTP is refused for a non-loopback Reseller URL. "
                "Use HTTPS or pass --allow-insecure-http explicitly."
            )
    return normalized


def _extract_inbound_ids(payload: object) -> set[int]:
    candidates: list[object] = []
    if isinstance(payload, dict):
        for key in ("inbounds", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        obj = payload.get("obj")
        if isinstance(obj, dict):
            for key in ("inbounds", "items", "data"):
                value = obj.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
    elif isinstance(payload, list):
        candidates.extend(payload)

    result: set[int] = set()
    for item in candidates:
        value = item.get("id") if isinstance(item, dict) else item
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def cmd_profile_add(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = open_db(settings)
    api_key = _read_api_key(args)
    reseller_url = _validate_reseller_url(
        args.reseller_url,
        allow_insecure_http=bool(args.allow_insecure_http),
    )
    inbounds = sorted({int(x.strip()) for x in args.inbounds.split(",") if x.strip()}) if args.inbounds else []

    # Validate credentials and requested inbound membership before storing secrets.
    probe = Profile(
        id=0,
        name="preflight",
        token_prefix="",
        reseller_url=reseller_url,
        reseller_api_key=api_key,
        default_inbound_ids=inbounds,
        active=True,
    )
    if not args.skip_live_check:
        client = ResellerClient(probe, settings.request_timeout, settings.verify_reseller_tls)
        me = client.me()
        inbound_payload = client.inbounds()
        allowed = _extract_inbound_ids(inbound_payload)
        if inbounds:
            forbidden = sorted(set(inbounds) - allowed)
            if forbidden:
                raise ValueError(
                    f"Requested inbound IDs are not available to this reseller: {forbidden}. "
                    f"Allowed IDs: {sorted(allowed)}"
                )
        print(f"Reseller preflight OK: {me}", file=sys.stderr)

    profile, token = db.create_profile(
        name=args.name,
        reseller_url=reseller_url,
        reseller_api_key=api_key,
        default_inbound_ids=inbounds,
    )
    print(f"Profile created: {profile.name} (id={profile.id})")
    print("Bridge token (shown once):")
    print(token)
    return 0


def cmd_profile_delete(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = open_db(settings)
    profile = db.get_profile_by_name(args.name)
    if not profile:
        print("Profile not found", file=sys.stderr)
        return 2
    if not args.yes:
        answer = input(f"Delete profile {profile.name!r} and its mappings/replay state? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled")
            return 1
    if not db.delete_profile(profile.name):
        print("Profile delete failed", file=sys.stderr)
        return 1
    print(f"Deleted profile: {profile.name}")
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = open_db(settings)
    for p in db.list_profiles():
        print(json.dumps({"id": p.id, "name": p.name, "token_prefix": p.token_prefix, "reseller_url": p.reseller_url, "inbounds": p.default_inbound_ids, "active": p.active}))
    return 0


def _write_state_backup(settings: Settings, root: Path, note: str) -> Path:
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(root, 0o700)
    db = open_db(settings)
    db.online_backup(root / "bridge.db")
    os.chmod(root / "bridge.db", 0o600)
    if not settings.master_key_path.is_file():
        raise RuntimeError("Master key is missing; refusing to create an unusable backup")
    shutil.copy2(settings.master_key_path, root / "master.key")
    os.chmod(root / "master.key", 0o600)
    manifest = {
        "created_at": time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
        "db_path": str(settings.db_path),
        "master_key_path": str(settings.master_key_path),
        "note": note,
        "contains": ["bridge.db", "master.key"],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.chmod(root / "manifest.json", 0o600)
    return root


def cmd_backup(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    root = Path(args.directory or "/var/backups/broute-bridge") / stamp
    _write_state_backup(settings, root, args.note or "manual backup")
    print(root)
    return 0


def _verify_state_pair(db_path: Path, key_path: Path) -> None:
    key_box = SecretBox.from_file(key_path)
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        result = con.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise RuntimeError(f"Backup SQLite integrity check failed: {result}")
        row = con.execute("SELECT reseller_api_key_enc FROM profiles LIMIT 1").fetchone()
    if row and row[0]:
        key_box.decrypt(str(row[0]))


def _state_backup_candidates(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(
        [
            p for p in base.iterdir()
            if p.is_dir() and (p / "bridge.db").is_file() and (p / "master.key").is_file()
        ],
        reverse=True,
    )


def _resolve_state_backup(value: str | None) -> Path:
    base = Path("/var/backups/broute-bridge").resolve()
    raw = str(value or "latest").strip()
    if raw == "latest":
        candidates = _state_backup_candidates(base)
        if not candidates:
            raise RuntimeError("No state backup containing both bridge.db and master.key was found")
        return candidates[0].resolve()
    target = Path(raw)
    if not target.is_absolute():
        target = base / target
    target = target.resolve()
    if target != base and base not in target.parents:
        raise RuntimeError(f"Refusing state backup outside {base}")
    if not (target / "bridge.db").is_file() or not (target / "master.key").is_file():
        raise RuntimeError("Selected backup must contain both bridge.db and master.key")
    return target


def _runtime_ids() -> tuple[int, int]:
    try:
        return pwd.getpwnam("broute-bridge").pw_uid, grp.getgrnam("broute-bridge").gr_gid
    except KeyError as exc:
        raise RuntimeError("broute-bridge service account is missing") from exc


def _atomic_copy(source: Path, destination: Path, mode: int, uid: int, gid: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.restore-", dir=str(destination.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        shutil.copyfile(source, temp)
        os.chmod(temp, mode)
        os.chown(temp, uid, gid)
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


def _service_health() -> bool:
    check = subprocess.run(
        ["curl", "-fsS", "http://127.0.0.1:8765/healthz"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return check.returncode == 0


def _restore_state_pair(settings: Settings, source: Path) -> None:
    service_uid, service_gid = _runtime_ids()
    root_uid = 0
    subprocess.run(["systemctl", "stop", "broute-bridge"], check=False)
    for suffix in ("-wal", "-shm"):
        Path(str(settings.db_path) + suffix).unlink(missing_ok=True)
    _atomic_copy(source / "master.key", settings.master_key_path, 0o640, root_uid, service_gid)
    _atomic_copy(source / "bridge.db", settings.db_path, 0o600, service_uid, service_gid)
    subprocess.run(["systemctl", "start", "broute-bridge"], check=False)


def cmd_restore_state(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("State restore must run as root: sudo broute-bridge restore-state ...")
    settings = Settings.from_env()
    target = _resolve_state_backup(args.backup)
    _verify_state_pair(target / "bridge.db", target / "master.key")

    safety_root = Path("/var/backups/broute-bridge") / (
        "pre-state-restore-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    )
    _write_state_backup(settings, safety_root, f"automatic safety snapshot before restoring {target.name}")

    print("STATE RESTORE PREVIEW")
    print(f"  restore from: {target}")
    print(f"  safety copy:  {safety_root}")
    print("  changes: encrypted Bridge SQLite state + its matching master key")
    print("  unchanged: code release, Nginx, 3x-ui, x-ui-reseller-panel, MirzaBot")
    if not args.yes:
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled. Nothing restored.")
            return 1

    _restore_state_pair(settings, target)
    if not _service_health():
        print("Restored state failed health check; restoring the pre-restore safety snapshot", file=sys.stderr)
        _restore_state_pair(settings, safety_root)
        if not _service_health():
            raise RuntimeError(
                "Both target and safety state failed health checks. Inspect: journalctl -u broute-bridge -n 100 --no-pager"
            )
        raise RuntimeError("Target state was rejected; pre-restore state is active again")

    print(f"State restored successfully from: {target}")
    print(f"Pre-restore safety snapshot kept at: {safety_root}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = open_db(settings)
    failures = []
    if not settings.master_key_path.exists():
        failures.append("master key missing")
    if not settings.db_path.exists():
        failures.append("database missing")
    profiles = db.list_profiles()
    print(f"profiles={len(profiles)}")
    if args.live:
        for p in profiles:
            try:
                client = ResellerClient(p, settings.request_timeout, settings.verify_reseller_tls)
                me = client.me()
                client.inbounds()
                print(f"{p.name}: live=ok representative={me}")
            except Exception as exc:
                failures.append(f"{p.name}: {exc}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    releases = Path("/opt/broute-bridge/releases")
    current = Path("/opt/broute-bridge/current")
    if not releases.exists() or not current.exists():
        print("Release layout not found", file=sys.stderr)
        return 2
    target = Path(args.release) if args.release else None
    if target and not target.is_absolute():
        target = releases / target
    if target is None:
        candidates = sorted([p for p in releases.iterdir() if p.is_dir()], reverse=True)
        resolved_current = current.resolve()
        candidates = [p for p in candidates if p.resolve() != resolved_current]
        if not candidates:
            print("No previous release available", file=sys.stderr)
            return 2
        target = candidates[0]
    if not target.exists() or target.parent.resolve() != releases.resolve():
        print("Invalid release", file=sys.stderr)
        return 2
    previous = current.resolve()
    temp_link = current.with_name("current.rollback-new")
    if temp_link.exists() or temp_link.is_symlink():
        temp_link.unlink()
    temp_link.symlink_to(target)
    os.replace(temp_link, current)
    subprocess.run(["systemctl", "restart", "broute-bridge"], check=False)
    time.sleep(1)
    if not _service_health():
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        temp_link.symlink_to(previous)
        os.replace(temp_link, current)
        subprocess.run(["systemctl", "restart", "broute-bridge"], check=False)
        print("Rollback target failed health check; restored previous release", file=sys.stderr)
        return 1
    print(f"Rolled back code release to {target.name}")
    print("If this rollback follows a schema/state problem, inspect available state snapshots and use:")
    print("  sudo broute-bridge restore-state latest")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="broute-bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profile-add")
    p.add_argument("--name", required=True)
    p.add_argument("--reseller-url", required=True)
    p.add_argument("--reseller-api-key", help="Discouraged: visible in shell history/process arguments")
    p.add_argument("--reseller-api-key-stdin", action="store_true", help="Read one line from stdin instead of prompting")
    p.add_argument("--inbounds", default="")
    p.add_argument("--allow-insecure-http", action="store_true")
    p.add_argument("--skip-live-check", action="store_true")
    p.set_defaults(func=cmd_profile_add)

    p = sub.add_parser("profile-list")
    p.set_defaults(func=cmd_profile_list)

    p = sub.add_parser("profile-delete")
    p.add_argument("--name", required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_profile_delete)

    p = sub.add_parser("backup")
    p.add_argument("--directory")
    p.add_argument("--note")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore-state")
    p.add_argument("backup", nargs="?", default="latest", help="Backup directory/name under /var/backups/broute-bridge, or latest")
    p.add_argument("--yes", action="store_true", help="Skip interactive confirmation")
    p.set_defaults(func=cmd_restore_state)

    p = sub.add_parser("doctor")
    p.add_argument("--live", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("rollback")
    p.add_argument("release", nargs="?")
    p.set_defaults(func=cmd_rollback)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
