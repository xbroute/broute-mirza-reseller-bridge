from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .db import Profile


class ResellerError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class ResellerClient:
    profile: Profile
    timeout: float = 15.0
    verify_tls: bool = True
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.profile.reseller_url.rstrip("/"),
            timeout=self.timeout,
            verify=self.verify_tls,
            transport=self.transport,
            headers={
                "Authorization": f"Bearer {self.profile.reseller_api_key}",
                "Accept": "application/json",
                "User-Agent": "broute-mirza-reseller-bridge/0.1",
            },
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ResellerError(f"Reseller transport error: {exc}") from exc
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ResellerError(
                f"Reseller API returned HTTP {response.status_code}: {detail}",
                response.status_code,
                body,
            )
        return body

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/me")

    def inbounds(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/inbounds")

    def users(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/users")

    def create_user(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/v1/users", json=body)

    def get_user(self, client_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/users/{client_id}")

    def patch_user(self, client_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/api/v1/users/{client_id}", json=body)

    def renew(self, client_id: int, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/v1/users/{client_id}/renew",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
        )

    def reset_usage(self, client_id: int) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/users/{client_id}/reset-usage")

    def revoke(self, client_id: int) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/users/{client_id}/revoke-subscription")

    def access(self, client_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/users/{client_id}/access")

    def delete(self, client_id: int) -> dict[str, Any]:
        return self.request("DELETE", f"/api/v1/users/{client_id}")
