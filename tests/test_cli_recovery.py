from __future__ import annotations

import os
from pathlib import Path

from broute_bridge.cli import _verify_state_pair, _write_state_backup
from broute_bridge.config import Settings
from broute_bridge.crypto import SecretBox
from broute_bridge.db import Database


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "state" / "bridge.db",
        master_key_path=tmp_path / "etc" / "master.key",
        bind_host="127.0.0.1",
        bind_port=8765,
        request_timeout=1,
        replay_window_seconds=120,
        verify_reseller_tls=True,
        log_level="INFO",
    )


def test_state_backup_contains_matching_key_and_database(tmp_path):
    settings = make_settings(tmp_path)
    SecretBox.ensure_key_file(settings.master_key_path)
    db = Database(settings.db_path, SecretBox.from_file(settings.master_key_path))
    db.create_profile(
        name="seller",
        reseller_url="https://reseller.example",
        reseller_api_key="xui_live_secret",
        default_inbound_ids=[1],
    )

    backup = _write_state_backup(settings, tmp_path / "backups" / "snapshot", "test")

    assert (backup / "bridge.db").is_file()
    assert (backup / "master.key").is_file()
    assert (backup / "manifest.json").is_file()
    assert (os.stat(backup / "bridge.db").st_mode & 0o777) == 0o600
    assert (os.stat(backup / "master.key").st_mode & 0o777) == 0o600
    _verify_state_pair(backup / "bridge.db", backup / "master.key")


def test_state_pair_rejects_wrong_master_key(tmp_path):
    settings = make_settings(tmp_path)
    SecretBox.ensure_key_file(settings.master_key_path)
    db = Database(settings.db_path, SecretBox.from_file(settings.master_key_path))
    db.create_profile(
        name="seller",
        reseller_url="https://reseller.example",
        reseller_api_key="xui_live_secret",
        default_inbound_ids=[1],
    )
    backup = _write_state_backup(settings, tmp_path / "backups" / "snapshot", "test")

    wrong_key = tmp_path / "wrong.key"
    SecretBox.ensure_key_file(wrong_key)

    try:
        _verify_state_pair(backup / "bridge.db", wrong_key)
    except ValueError:
        pass
    else:
        raise AssertionError("A backup encrypted with another master key must be rejected")
