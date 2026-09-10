from __future__ import annotations

import copy
import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .db import Profile


class ResellerError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


_CACHE_LOCK = threading.Lock()
_USERS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# These fields are intentionally sourced from the reseller users summary because
# that endpoint runs the panel's live-quota sync and represents the freshest
# usage/status view. Future details endpoints must not accidentally override them
# with stale local/config values.
_LIVE_SUMMARY_FIELDS = {
    "traffic_limit_bytes",
    "used_bytes",
    "total_used_bytes",
    "usage_percent",
    "expire_at_ms",
    "expires_in",
    "status",
    "status_code",
    "online",
    "last_online_at",
}


def _cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("BROUTE_USERS_CACHE_SECONDS", "5")))
    except ValueError:
        return 5.0


def _extract_users(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("users"), list):
        return [x for x in payload["users"] if isinstance(x, dict)]
    for wrapper in ("obj", "data", "result"):
        inner = payload.get(wrapper)
        if isinstance(inner, dict) and isinstance(inner.get("users"), list):
            return [x for x in inner["users"] if isinstance(x, dict)]
    return []


def _user_object(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for key in ("user", "client"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    for wrapper in ("obj", "data", "result"):
        inner = payload.get(wrapper)
        if isinstance(inner, dict):
            for key in ("user", "client"):
                if isinstance(inner.get(key), dict):
                    return inner[key]
            if "id" in inner or "username" in inner or "email" in inner:
                return inner
    if "id" in payload or "username" in payload or "email" in payload:
        return payload
    return None


def _merge_user_payload(payload: Any, summary: dict[str, Any] | None) -> Any:
    if not summary or not isinstance(payload, dict):
        return payload
    result = copy.deepcopy(payload)
    target = _user_object(result)
    if target is None:
        return result

    # Details is authoritative for configuration/identity. Summary is then
    # re-applied only for fields whose freshness contract is stronger there.
    merged = dict(summary)
    merged.update(target)
    for field in _LIVE_SUMMARY_FIELDS:
        if field in summary:
            merged[field] = summary[field]

    if isinstance(result.get("user"), dict):
        result["user"] = merged
        return result
    if isinstance(result.get("client"), dict):
        result["client"] = merged
        return result
    for wrapper in ("obj", "data", "result"):
        inner = result.get(wrapper)
        if not isinstance(inner, dict):
            continue
        if isinstance(inner.get("user"), dict):
            inner["user"] = merged
            return result
        if isinstance(inner.get("client"), dict):
            inner["client"] = merged
            return result
        if "id" in inner or "username" in inner or "email" in inner:
            result[wrapper] = merged
            return result
    return merged


@dataclass
class ResellerClient:
    profile: Profile
    timeout: float = 15.0
    verify_tls: bool = True
    transport: httpx.BaseTransport | None = None

    def _cache_key(self) -> str:
        material = f"{self.profile.id}|{self.profile.reseller_url}|{self.profile.reseller_api_key}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _invalidate_users_cache(self) -> None:
        with _CACHE_LOCK:
            _USERS_CACHE.pop(self._cache_key(), None)

    @staticmethod
    def clear_all_caches() -> None:
        with _CACHE_LOCK:
            _USERS_CACHE.clear()

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

    def users(self, *, force: bool = False) -> dict[str, Any]:
        ttl = _cache_ttl()
        key = self._cache_key()
        now = time.monotonic()
        if not force and ttl > 0:
            with _CACHE_LOCK:
                cached = _USERS_CACHE.get(key)
                if cached and cached[0] > now:
                    return copy.deepcopy(cached[1])
        payload = self.request("GET", "/api/v1/users")
        if not isinstance(payload, dict):
            raise ResellerError("Reseller /api/v1/users returned a non-object response")
        if ttl > 0:
            with _CACHE_LOCK:
                _USERS_CACHE[key] = (now + ttl, copy.deepcopy(payload))
        return payload

    def create_user(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.request("POST", "/api/v1/users", json=body)
        self._invalidate_users_cache()
        return result

    def get_user(self, client_id: int) -> dict[str, Any]:
        details = self.request("GET", f"/api/v1/users/{client_id}")
        summary = None
        try:
            for row in _extract_users(self.users()):
                if int(row.get("id") or row.get("client_id") or 0) == int(client_id):
                    summary = row
                    break
        except (ResellerError, TypeError, ValueError):
            # Details remain useful if the summary endpoint is temporarily unavailable.
            summary = None
        return _merge_user_payload(details, summary)

    def patch_user(self, client_id: int, body: dict[str, Any]) -> dict[str, Any]:
        result = self.request("PATCH", f"/api/v1/users/{client_id}", json=body)
        self._invalidate_users_cache()
        return result

    def renew(self, client_id: int, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        result = self.request(
            "POST",
            f"/api/v1/users/{client_id}/renew",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        self._invalidate_users_cache()
        return result

    def reset_usage(self, client_id: int) -> dict[str, Any]:
        result = self.request("POST", f"/api/v1/users/{client_id}/reset-usage")
        self._invalidate_users_cache()
        return result

    def revoke(self, client_id: int) -> dict[str, Any]:
        result = self.request("POST", f"/api/v1/users/{client_id}/revoke-subscription")
        self._invalidate_users_cache()
        return result

    def access(self, client_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/users/{client_id}/access")

    def delete(self, client_id: int) -> dict[str, Any]:
        result = self.request("DELETE", f"/api/v1/users/{client_id}")
        self._invalidate_users_cache()
        return result
