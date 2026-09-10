from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

import broute_bridge.service as service_module
from broute_bridge.crypto import SecretBox
from broute_bridge.db import Database
from broute_bridge.reseller import ResellerError
from broute_bridge.service import AgentService, GIB


class FakeClient:
    def __init__(self, now: int):
        self.user = {
            "id": 7,
            "username": "alice",
            "traffic_limit_bytes": 100 * GIB,
            "used_bytes": 40 * GIB,
            "expire_at_ms": (now + 10 * 86400) * 1000,
            "enabled": True,
            "status_code": "active",
            "inbound_ids": [1, 2],
        }
        self.patches = []
        self.reset_count = 0
        self.deleted = 0
        self.revoked = 0
        self.create_error = None

    def me(self): return {"ok": True}
    def inbounds(self): return {"ok": True, "inbounds": [{"id": 1, "label": "DE"}, {"id": 2, "label": "TR"}]}
    def users(self): return {"ok": True, "users": [dict(self.user)]}
    def get_user(self, client_id):
        if client_id != 7:
            raise ResellerError("not found", 404)
        return {"ok": True, "user": dict(self.user)}
    def access(self, client_id):
        return {"ok": True, "username": "alice", "uuid": "u", "subscription_url": "https://sub/alice", "links": ["vless://x"]}
    def create_user(self, body):
        if self.create_error:
            raise self.create_error
        self.user["username"] = body["username"]
        self.user["traffic_limit_bytes"] = int(body["traffic_gb"] * GIB)
        self.user["inbound_ids"] = body["inbound_ids"]
        return {"ok": True, "user": dict(self.user)}
    def patch_user(self, client_id, body):
        self.patches.append(dict(body))
        if "traffic_gb" in body:
            self.user["traffic_limit_bytes"] = int(round(body["traffic_gb"] * GIB))
        return {"ok": True}
    def reset_usage(self, client_id):
        self.reset_count += 1
        self.user["used_bytes"] = 0
        return {"ok": True}
    def delete(self, client_id): self.deleted += 1; return {"ok": True}
    def revoke(self, client_id): self.revoked += 1; return {"ok": True}


def make_service(tmp_path, now=2_000_000_000):
    db = Database(tmp_path / "bridge.db", SecretBox(Fernet.generate_key()))
    profile, _ = db.create_profile(
        name="seller", reseller_url="https://r.example", reseller_api_key="xui_live_x", default_inbound_ids=[1, 2]
    )
    fake = FakeClient(now)
    service = AgentService(db, client_factory=lambda _: fake)
    return service, profile, fake


def expiry_date(ts_seconds: int) -> str:
    return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


@pytest.mark.parametrize(
    "method,expected_gb,expected_base_days,reset_count",
    [
        ("resetVolumeTime", 20, 30, 1),
        ("addTimeVolumeNextMonth", 120, 40, 0),
        ("resetTimeAddVolume", 120, 30, 0),
        ("resetVolumeAddTime", 20, 40, 1),
        ("addTimeConvertVolume", 80, 40, 1),
    ],
)
def test_mirza_extend_methods_match_current_semantics(tmp_path, monkeypatch, method, expected_gb, expected_base_days, reset_count):
    now = 2_000_000_000
    monkeypatch.setattr(service_module.time, "time", lambda: now)
    service, profile, fake = make_service(tmp_path, now)
    result = service.extend_service(profile, {
        "username": "alice", "data_limit_gb": 20, "time_day": 30, "method_extend": method,
    })
    assert result["status"] is True
    patch = fake.patches[-1]
    assert patch["traffic_gb"] == expected_gb
    assert patch["expiry_date"] == expiry_date(now + expected_base_days * 86400)
    assert fake.reset_count == reset_count


def test_add_time_does_not_change_traffic(tmp_path, monkeypatch):
    now = 2_000_000_000
    monkeypatch.setattr(service_module.time, "time", lambda: now)
    service, profile, fake = make_service(tmp_path, now)
    service.add_time_service(profile, {"username": "alice", "time_day": 5})
    assert "traffic_gb" not in fake.patches[-1]
    assert fake.patches[-1]["expiry_date"] == expiry_date(now + 15 * 86400)


def test_add_volume_does_not_change_expiry(tmp_path):
    service, profile, fake = make_service(tmp_path)
    service.add_volume_service(profile, {"username": "alice", "data_limit_gb": 5})
    assert fake.patches[-1] == {"traffic_gb": 105.0, "enabled": True}


def test_get_user_data_uses_live_usage_shape(tmp_path, monkeypatch):
    now = 2_000_000_000
    monkeypatch.setattr(service_module.time, "time", lambda: now)
    service, profile, fake = make_service(tmp_path, now)
    out = service.get_user_data(profile, {"username": "alice"})
    user = out["obj"]["user"]
    assert user["data_limit"] == 100 * GIB
    assert user["used_traffic"] == 40 * GIB
    assert user["expire"] == now + 10 * 86400
    assert user["subscription_url"] == "https://sub/alice"


def test_stale_mapping_self_heals(tmp_path):
    service, profile, fake = make_service(tmp_path)
    service.db.set_mapping(profile.id, "alice", 999)
    out = service.get_user_data(profile, {"username": "alice"})
    assert out["status"] is True
    assert service.db.get_mapping(profile.id, "alice")["reseller_client_id"] == 7


def test_duplicate_mutation_is_replayed_not_applied_twice(tmp_path):
    service, profile, fake = make_service(tmp_path)
    payload = {"actions": "add_volume_service", "username": "alice", "data_limit_gb": 5}
    first = service.dispatch(profile, "add_volume_service", payload)
    second = service.dispatch(profile, "add_volume_service", payload)
    assert first == second
    assert len(fake.patches) == 1


def test_duplicate_create_mismatch_is_rejected(tmp_path):
    service, profile, fake = make_service(tmp_path)
    fake.create_error = ResellerError("duplicate", 409)
    out = service.dispatch(profile, "user_create", {"actions": "user_create", "username": "alice", "data_limit_gb": 20, "expire_days": 0})
    assert out["status"] is False
    assert out["code"] == "reseller_http_409"
