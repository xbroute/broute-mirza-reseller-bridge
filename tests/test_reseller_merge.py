from __future__ import annotations

from broute_bridge.reseller import _merge_user_payload


def test_live_summary_fields_override_future_stale_details_fields():
    details = {
        "ok": True,
        "user": {
            "id": 7,
            "username": "alice",
            "used_bytes": 10,
            "traffic_limit_bytes": 20,
            "expire_at_ms": 100,
            "status_code": "active",
            "comment": "details stays authoritative",
        },
    }
    summary = {
        "id": 7,
        "username": "alice",
        "used_bytes": 111,
        "traffic_limit_bytes": 222,
        "expire_at_ms": 333,
        "status_code": "limited",
        "comment": "summary comment must not replace config detail",
    }

    merged = _merge_user_payload(details, summary)["user"]

    assert merged["used_bytes"] == 111
    assert merged["traffic_limit_bytes"] == 222
    assert merged["expire_at_ms"] == 333
    assert merged["status_code"] == "limited"
    assert merged["comment"] == "details stays authoritative"
