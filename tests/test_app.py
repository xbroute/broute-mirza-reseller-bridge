from __future__ import annotations

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from broute_bridge.app import create_app
from broute_bridge.config import Settings
from broute_bridge.crypto import SecretBox
from broute_bridge.db import Database


class EchoService:
    def dispatch(self, profile, action, data):
        return {"status": True, "obj": {"profile": profile.name, "action": action, "data": data}}
    def client(self, profile):
        class C:
            def me(self): return {"ok": True, "data": {"username": "seller"}}
            def inbounds(self): return {"ok": True, "inbounds": [{"id": 1}]}
        return C()


def make_client(tmp_path):
    db = Database(tmp_path / "bridge.db", SecretBox(Fernet.generate_key()))
    _, token = db.create_profile(
        name="seller", reseller_url="https://r.example", reseller_api_key="xui_live_x", default_inbound_ids=[1]
    )
    settings = Settings(
        db_path=tmp_path / "bridge.db", master_key_path=tmp_path / "key", bind_host="127.0.0.1", bind_port=8765,
        request_timeout=1, replay_window_seconds=120, verify_reseller_tls=True, log_level="INFO",
    )
    app = create_app(settings, db=db, service=EchoService())
    return TestClient(app), token


def test_health_is_public_but_agent_requires_bridge_token(tmp_path):
    client, token = make_client(tmp_path)
    assert client.get("/healthz").status_code == 200
    assert client.get("/?actions=list_panel").status_code == 401
    response = client.get("/?actions=list_panel", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["obj"]["action"] == "list_panel"


def test_mutation_body_contract(tmp_path):
    client, token = make_client(tmp_path)
    response = client.put(
        "/", headers={"Authorization": f"Bearer {token}"},
        json={"actions": "add_time_service", "username": "alice", "time_day": 3},
    )
    assert response.status_code == 200
    assert response.json()["obj"]["data"]["username"] == "alice"
