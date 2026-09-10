from __future__ import annotations

import sqlite3

from cryptography.fernet import Fernet

from broute_bridge.crypto import SecretBox
from broute_bridge.db import Database


def make_db(tmp_path):
    return Database(tmp_path / "bridge.db", SecretBox(Fernet.generate_key()))


def test_profile_secrets_are_not_stored_raw(tmp_path):
    db = make_db(tmp_path)
    profile, token = db.create_profile(
        name="seller-a",
        reseller_url="https://reseller.example",
        reseller_api_key="xui_live_super_secret",
        default_inbound_ids=[3, 1, 3],
    )
    assert profile.reseller_api_key == "xui_live_super_secret"
    assert profile.default_inbound_ids == [1, 3]
    with sqlite3.connect(db.path) as con:
        row = con.execute("SELECT token_hash,reseller_api_key_enc FROM profiles").fetchone()
    assert token not in row[0]
    assert token not in row[1]
    assert "xui_live_super_secret" not in row[1]


def test_mapping_upsert_and_delete(tmp_path):
    db = make_db(tmp_path)
    profile, _ = db.create_profile(
        name="seller-a", reseller_url="https://r.example", reseller_api_key="xui_live_x", default_inbound_ids=[1]
    )
    db.set_mapping(profile.id, "alice", 7, "sub-a")
    assert db.get_mapping(profile.id, "alice")["reseller_client_id"] == 7
    db.set_mapping(profile.id, "alice", 8, None)
    mapping = db.get_mapping(profile.id, "alice")
    assert mapping["reseller_client_id"] == 8
    assert mapping["sub_id"] == "sub-a"
    db.delete_mapping(profile.id, "alice")
    assert db.get_mapping(profile.id, "alice") is None


def test_replay_guard_expires_after_window(tmp_path, monkeypatch):
    import broute_bridge.db as db_module

    now = {"value": 100}
    monkeypatch.setattr(db_module.time, "time", lambda: now["value"])
    db = make_db(tmp_path)
    profile, _ = db.create_profile(
        name="seller-a", reseller_url="https://r.example", reseller_api_key="xui_live_x", default_inbound_ids=[1]
    )
    state, _ = db.replay_begin(profile_id=profile.id, fingerprint="abc", action="x", username="alice", window_seconds=2)
    assert state == "new"
    db.replay_complete(profile.id, "abc", {"status": True})
    state, response = db.replay_begin(profile_id=profile.id, fingerprint="abc", action="x", username="alice", window_seconds=2)
    assert state == "replay"
    assert response == {"status": True}
    now["value"] = 103
    state, _ = db.replay_begin(profile_id=profile.id, fingerprint="abc", action="x", username="alice", window_seconds=2)
    assert state == "new"
