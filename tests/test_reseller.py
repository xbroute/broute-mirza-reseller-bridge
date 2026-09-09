from __future__ import annotations

import httpx

from broute_bridge.db import Profile
from broute_bridge.reseller import ResellerClient

GIB = 1024 ** 3


def profile():
    return Profile(
        id=77,
        name="seller",
        token_prefix="br_live_x",
        reseller_url="https://reseller.test",
        reseller_api_key="xui_live_test",
        default_inbound_ids=[1, 2],
        active=True,
    )


def test_get_user_merges_live_summary_and_caches_list(monkeypatch):
    monkeypatch.setenv("BROUTE_USERS_CACHE_SECONDS", "30")
    ResellerClient.clear_all_caches()
    calls = {"users": 0, "details": 0, "patch": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/users":
            calls["users"] += 1
            return httpx.Response(200, json={"ok": True, "users": [{
                "id": 7, "username": "alice", "traffic_limit_bytes": 50 * GIB,
                "used_bytes": 12 * GIB, "expire_at_ms": 2_000_000_000_000,
                "status_code": "active", "last_online_at": "2026-09-10 01:02:03",
            }]})
        if request.method == "GET" and request.url.path == "/api/v1/users/7":
            calls["details"] += 1
            return httpx.Response(200, json={"ok": True, "user": {
                "id": 7, "username": "alice", "enabled": True, "inbound_ids": [1, 2], "limit_ip": 2,
            }})
        if request.method == "PATCH" and request.url.path == "/api/v1/users/7":
            calls["patch"] += 1
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    client = ResellerClient(profile(), transport=httpx.MockTransport(handler))
    first = client.get_user(7)["user"]
    second = client.get_user(7)["user"]
    assert first["used_bytes"] == 12 * GIB
    assert first["traffic_limit_bytes"] == 50 * GIB
    assert first["inbound_ids"] == [1, 2]
    assert second["used_bytes"] == 12 * GIB
    assert calls == {"users": 1, "details": 2, "patch": 0}

    client.patch_user(7, {"traffic_gb": 60})
    client.get_user(7)
    assert calls["users"] == 2
    assert calls["patch"] == 1


def test_authorization_header_is_kept_when_idempotency_header_is_added():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["idem"] = request.headers.get("Idempotency-Key")
        return httpx.Response(200, json={"ok": True})

    client = ResellerClient(profile(), transport=httpx.MockTransport(handler))
    client.renew(7, {"traffic_gb": 1}, "mirza:renew:abc")
    assert seen["auth"] == "Bearer xui_live_test"
    assert seen["idem"] == "mirza:renew:abc"
