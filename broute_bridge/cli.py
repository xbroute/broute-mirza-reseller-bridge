from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import subprocess
import sys
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

    # Validate credentials and the requested inbound set before storing secrets.
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
        if inbounds and allowed:
            forbidden = sorted(set(inbounds) - allowed)
            if forbidden:
                raise ValueError(f"Requested inbound IDs are not available to this reseller: {forbidden}")
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


def cmd_backup(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = open_db(settings)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    root = Path(args.directory or "/var/backups/broute-bridge") / stamp
    root.mkdir(parents=True, exist_ok=False)
    db.online_backup(root / "bridge.db")
    manifest = {
        "created_at": stamp,
        "db_path": str(settings.db_path),
        "master_key_path": str(settings.master_key_path),
        "note": args.note or "manual backup",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(root)
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
    health = subprocess.run(["curl", "-fsS", "http://127.0.0.1:8765/healthz"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if health.returncode != 0:
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        temp_link.symlink_to(previous)
        os.replace(temp_link, current)
        subprocess.run(["systemctl", "restart", "broute-bridge"], check=False)
        print("Rollback target failed health check; restored previous release", file=sys.stderr)
        return 1
    print(f"Rolled back to {target.name}")
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

    p = sub.add_parser("doctor")
    p.add_argument("--live", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("rollback")
    p.add_argument("release", nargs="?")
    p.set_defaults(func=cmd_rollback)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
