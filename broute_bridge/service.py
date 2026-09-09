from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .db import Database, Profile
from .reseller import ResellerClient, ResellerError

GIB = 1024 ** 3
SUPPORTED_EXTEND_METHODS = {
    "resetVolumeTime",
    "addTimeVolumeNextMonth",
    "resetTimeAddVolume",
    "resetVolumeAddTime",
    "addTimeConvertVolume",
}
MUTATING_ACTIONS = {
    "user_create",
    "add_time_service",
    "add_volume_service",
    "extend_service",
    "reset_usage",
    "user_delete",
    "change_link",
}


class AgentService:
    def __init__(
        self,
        db: Database,
        *,
        timeout: float = 15.0,
        verify_tls: bool = True,
        replay_window_seconds: int = 120,
        client_factory: Callable[[Profile], ResellerClient] | None = None,
    ):
        self.db = db
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.replay_window_seconds = replay_window_seconds
        self.client_factory = client_factory

    def client(self, profile: Profile) -> ResellerClient:
        if self.client_factory:
            return self.client_factory(profile)
        return ResellerClient(profile, self.timeout, self.verify_tls)

    @staticmethod
    def success(obj: Any = None, msg: str = "successful") -> dict[str, Any]:
        payload = {"status": True, "msg": msg}
        if obj is not None:
            payload["obj"] = obj
        return payload

    @staticmethod
    def failure(msg: str, *, code: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": False, "msg": msg}
        if code:
            payload["code"] = code
        return payload

    @staticmethod
    def _fingerprint(action: str, data: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"action": action, "data": data},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def dispatch(self, profile: Profile, action: str, data: dict[str, Any]) -> dict[str, Any]:
        action = str(action or "").strip()
        handlers = {
            "list_panel": self.list_panel,
            "user_create": self.user_create,
            "get_user_data": self.get_user_data,
            "add_time_service": self.add_time_service,
            "add_volume_service": self.add_volume_service,
            "extend_service": self.extend_service,
            "reset_usage": self.reset_usage,
            "user_delete": self.user_delete,
            "change_link": self.change_link,
        }
        handler = handlers.get(action)
        if not handler:
            return self.failure(f"Unsupported action: {action}", code="unsupported_action")

        if action not in MUTATING_ACTIONS:
            return self._safe_call(handler, profile, data)

        fingerprint = self._fingerprint(action, data)
        state, response = self.db.replay_begin(
            profile_id=profile.id,
            fingerprint=fingerprint,
            action=action,
            username=str(data.get("username") or "") or None,
            window_seconds=self.replay_window_seconds,
        )
        if state == "replay" and response is not None:
            return response
        if state == "uncertain":
            return self.failure(
                "An identical mutation is already in progress or its final state is uncertain; refusing a duplicate write.",
                code="duplicate_or_uncertain",
            )
        result = self._safe_call(handler, profile, data)
        if result.get("status") is True:
            self.db.replay_complete(profile.id, fingerprint, result)
        return result

    def _safe_call(self, handler: Callable[[Profile, dict[str, Any]], dict[str, Any]], profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        try:
            return handler(profile, data)
        except ResellerError as exc:
            return self.failure(str(exc), code=f"reseller_http_{exc.status_code}" if exc.status_code else "reseller_transport")
        except (KeyError, ValueError, TypeError) as exc:
            return self.failure(str(exc), code="invalid_request")

    @staticmethod
    def _extract_users(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            if isinstance(payload.get("users"), list):
                return [x for x in payload["users"] if isinstance(x, dict)]
            obj = payload.get("obj")
            if isinstance(obj, dict) and isinstance(obj.get("users"), list):
                return [x for x in obj["users"] if isinstance(x, dict)]
        return []

    @staticmethod
    def _unwrap_user(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid user response from reseller")
        for key in ("user", "client"):
            if isinstance(payload.get(key), dict):
                return payload[key]
        obj = payload.get("obj")
        if isinstance(obj, dict):
            for key in ("user", "client"):
                if isinstance(obj.get(key), dict):
                    return obj[key]
            if "id" in obj or "username" in obj or "email" in obj:
                return obj
        if "id" in payload or "username" in payload or "email" in payload:
            return payload
        raise ValueError("Unable to locate user object in reseller response")

    @staticmethod
    def _client_id(user: dict[str, Any]) -> int:
        value = user.get("id", user.get("client_id"))
        if value is None:
            raise ValueError("Reseller user response has no client id")
        return int(value)

    @staticmethod
    def _username(user: dict[str, Any]) -> str:
        value = user.get("username", user.get("email"))
        return str(value or "")

    def _resolve_user(self, profile: Profile, username: str, *, verify_mapping: bool = True) -> tuple[int, dict[str, Any]]:
        client = self.client(profile)
        mapping = self.db.get_mapping(profile.id, username)
        if mapping:
            client_id = int(mapping["reseller_client_id"])
            if not verify_mapping:
                return client_id, {}
            try:
                payload = client.get_user(client_id)
                user = self._unwrap_user(payload)
                if self._username(user) == username:
                    self.db.set_mapping(profile.id, username, client_id, user.get("sub_id"))
                    return client_id, user
            except ResellerError as exc:
                if exc.status_code not in {404, 400}:
                    raise
            self.db.delete_mapping(profile.id, username)

        payload = client.users()
        matches = [u for u in self._extract_users(payload) if self._username(u) == username]
        if not matches:
            raise ValueError("User not found")
        if len(matches) > 1:
            raise ValueError("Multiple reseller users matched the same username")
        user = matches[0]
        client_id = self._client_id(user)
        self.db.set_mapping(profile.id, username, client_id, user.get("sub_id"))
        try:
            details = self._unwrap_user(client.get_user(client_id))
        except Exception:
            details = user
        return client_id, details

    @staticmethod
    def _parse_inbounds(raw: Any, default: list[int]) -> list[int]:
        if raw is None or raw == "" or raw == []:
            return list(default)
        if isinstance(raw, list):
            values = raw
        elif isinstance(raw, (int, float)):
            values = [raw]
        else:
            text = str(raw).strip()
            try:
                parsed = json.loads(text)
                values = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                values = [part.strip() for part in text.split(",") if part.strip()]
        out: list[int] = []
        for value in values:
            number = int(value)
            if number not in out:
                out.append(number)
        return out or list(default)

    @staticmethod
    def _expiry_ms(user: dict[str, Any]) -> int:
        for key in ("expire_at_ms", "expiryTime", "expiry_time"):
            if user.get(key) is not None:
                return int(user.get(key) or 0)
        expire = user.get("expire")
        if expire is not None:
            value = int(expire or 0)
            return value * 1000 if abs(value) < 10_000_000_000 else value
        return 0

    @staticmethod
    def _limit_bytes(user: dict[str, Any]) -> int:
        for key in ("traffic_limit_bytes", "total_limit_bytes", "data_limit", "totalGB"):
            if user.get(key) is not None:
                return max(0, int(user.get(key) or 0))
        return 0

    @staticmethod
    def _used_bytes(user: dict[str, Any]) -> int:
        for key in ("used_bytes", "panel_used_bytes", "used_traffic"):
            if user.get(key) is not None:
                return max(0, int(user.get(key) or 0))
        return 0

    @staticmethod
    def _expiry_date_from_ms(expiry_ms: int) -> str:
        if expiry_ms <= 0:
            return ""
        dt = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")

    def _existing_user_matches_create(self, user: dict[str, Any], body: dict[str, Any]) -> bool:
        """Conservatively decide whether a duplicate can be a lost create response."""
        requested_limit = int(round(float(body.get("traffic_gb") or 0) * GIB))
        if self._limit_bytes(user) != requested_limit:
            return False

        requested_inbounds = {int(x) for x in body.get("inbound_ids") or []}
        existing_raw = user.get("inbound_ids")
        if existing_raw is not None:
            try:
                existing_inbounds = set(self._parse_inbounds(existing_raw, []))
            except (TypeError, ValueError):
                return False
            if existing_inbounds != requested_inbounds:
                return False

        if body.get("expiry_date"):
            expiry_ms = self._expiry_ms(user)
            if expiry_ms > 0:
                existing_date = self._expiry_date_from_ms(expiry_ms)
                if existing_date != str(body["expiry_date"]):
                    return False

        if user.get("enabled") is False:
            return False
        return True

    def list_panel(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        payload = self.client(profile).inbounds()
        inbounds: list[Any] = []
        if isinstance(payload, dict):
            raw = payload.get("inbounds")
            if raw is None and isinstance(payload.get("obj"), dict):
                raw = payload["obj"].get("inbounds")
            if isinstance(raw, list):
                inbounds = raw
        return self.success(inbounds)

    def user_create(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        if len(username) < 3:
            raise ValueError("username must be at least 3 characters")
        traffic_gb = float(data.get("data_limit_gb") or 0)
        expire_days = int(math.ceil(float(data.get("expire_days") or 0)))
        inbounds = self._parse_inbounds(data.get("panel_id"), profile.default_inbound_ids)
        if not inbounds:
            raise ValueError("No inbound IDs are configured for this reseller profile")
        body: dict[str, Any] = {
            "username": username,
            "traffic_gb": max(0.0, traffic_gb),
            "enabled": True,
            "inbound_ids": inbounds,
            "comment": "created by broute-mirza-reseller-bridge",
        }
        if expire_days > 0:
            expiry = datetime.fromtimestamp(time.time() + expire_days * 86400, tz=timezone.utc)
            body["expiry_date"] = expiry.strftime("%Y-%m-%d")
        try:
            response = self.client(profile).create_user(body)
        except ResellerError as exc:
            if exc.status_code not in {400, 409}:
                raise
            client_id, user = self._resolve_user(profile, username)
            if not self._existing_user_matches_create(user, body):
                raise ResellerError(
                    "Duplicate username exists but does not match the requested service; refusing to adopt it as a retry",
                    exc.status_code,
                    exc.body,
                )
        else:
            user = self._unwrap_user(response)
            client_id = self._client_id(user)
            self.db.set_mapping(profile.id, username, client_id, user.get("sub_id"))

        access = self.client(profile).access(client_id)
        obj = self._access_to_create_obj(username, access)
        return self.success(obj)

    @staticmethod
    def _access_to_create_obj(username: str, access: dict[str, Any]) -> dict[str, Any]:
        raw = access.get("obj") if isinstance(access.get("obj"), dict) else access
        links = raw.get("links", raw.get("configs", [])) if isinstance(raw, dict) else []
        if isinstance(links, str):
            links = [line for line in links.splitlines() if line.strip()]
        sub = raw.get("subscription_url", raw.get("subscription", "")) if isinstance(raw, dict) else ""
        return {"username": username, "subscription_url": sub or "", "links": links or []}

    def get_user_data(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        client_id, user = self._resolve_user(profile, username)
        access = self.client(profile).access(client_id)
        raw_access = access.get("obj") if isinstance(access.get("obj"), dict) else access
        links = raw_access.get("links", raw_access.get("configs", [])) if isinstance(raw_access, dict) else []
        if isinstance(links, str):
            links = [line for line in links.splitlines() if line.strip()]
        sub_url = raw_access.get("subscription_url", raw_access.get("subscription", "")) if isinstance(raw_access, dict) else ""
        expiry_ms = self._expiry_ms(user)
        expiry_seconds = expiry_ms // 1000 if expiry_ms > 0 else 0
        status = str(user.get("status_code") or user.get("status") or "active").lower()
        if not bool(user.get("enabled", True)):
            status = "disabled"
        now_ms = int(time.time() * 1000)
        if expiry_ms > 0 and expiry_ms <= now_ms:
            status = "expired"
        limit_bytes = self._limit_bytes(user)
        used_bytes = self._used_bytes(user)
        if limit_bytes > 0 and used_bytes >= limit_bytes:
            status = "limited"
        if expiry_ms < -10000:
            status = "on_hold"
            expiry_seconds = 0
        online_at = user.get("last_online_at") or user.get("online_at")
        mirza_user = {
            "status": status,
            "username": username,
            "data_limit": limit_bytes,
            "expire": expiry_seconds,
            "online_at": online_at,
            "used_traffic": used_bytes,
            "links": links or [],
            "subscription_url": sub_url or "",
            "sub_updated_at": raw_access.get("sub_updated_at") if isinstance(raw_access, dict) else None,
            "sub_last_user_agent": raw_access.get("sub_last_user_agent") if isinstance(raw_access, dict) else None,
            "uuid": raw_access.get("uuid", user.get("uuid")) if isinstance(raw_access, dict) else user.get("uuid"),
            "data_limit_reset": user.get("data_limit_reset", "no_reset"),
        }
        return self.success({"user": mirza_user})

    def add_volume_service(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        add_bytes = int(round(float(data.get("data_limit_gb") or 0) * GIB))
        client_id, user = self._resolve_user(profile, username)
        new_total_gb = (self._limit_bytes(user) + add_bytes) / GIB
        self.client(profile).patch_user(client_id, {"traffic_gb": new_total_gb, "enabled": True})
        return self.success()

    def add_time_service(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        days = int(data.get("time_day") or 0)
        client_id, user = self._resolve_user(profile, username)
        old_ms = self._expiry_ms(user)
        base_ms = max(old_ms, int(time.time() * 1000)) if old_ms > 0 else int(time.time() * 1000)
        new_ms = 0 if days == 0 else base_ms + days * 86400 * 1000
        self.client(profile).patch_user(
            client_id,
            {"expiry_date": self._expiry_date_from_ms(new_ms), "enabled": True},
        )
        return self.success()

    def reset_usage(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        client_id, _ = self._resolve_user(profile, username)
        self.client(profile).reset_usage(client_id)
        return self.success()

    def extend_service(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        new_limit_gb = float(data.get("data_limit_gb") or 0)
        time_day = int(data.get("time_day") or 0)
        method = str(data.get("method_extend") or "resetVolumeTime").strip()
        if method not in SUPPORTED_EXTEND_METHODS:
            raise ValueError(f"Unsupported Mirza Methodextend: {method}")

        client_id, user = self._resolve_user(profile, username)
        old_limit = self._limit_bytes(user)
        used = self._used_bytes(user)
        now_ms = int(time.time() * 1000)
        old_expiry = self._expiry_ms(user)
        old_expiry_base = max(old_expiry, now_ms) if old_expiry > 0 else now_ms
        new_limit = 0 if new_limit_gb == 0 else int(round(new_limit_gb * GIB))
        new_limit_add = 0 if new_limit_gb == 0 else old_limit + int(round(new_limit_gb * GIB))
        time_new = 0 if time_day == 0 else now_ms + time_day * 86400 * 1000
        time_new_add = 0 if time_day == 0 else old_expiry_base + time_day * 86400 * 1000
        should_reset = False

        if method == "resetVolumeTime":
            should_reset = True
        elif method == "addTimeVolumeNextMonth":
            new_limit = new_limit_add
            time_new = time_new_add
        elif method == "resetTimeAddVolume":
            new_limit = new_limit_add
        elif method == "resetVolumeAddTime":
            should_reset = True
            time_new = time_new_add
        elif method == "addTimeConvertVolume":
            should_reset = True
            time_new = time_new_add
            remaining = max(0, old_limit - used)
            new_limit = new_limit + remaining

        if should_reset:
            self.client(profile).reset_usage(client_id)
        patch = {
            "traffic_gb": new_limit / GIB,
            "expiry_date": self._expiry_date_from_ms(time_new),
            "enabled": True,
        }
        self.client(profile).patch_user(client_id, patch)
        return self.success()

    def user_delete(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        client_id, _ = self._resolve_user(profile, username)
        self.client(profile).delete(client_id)
        self.db.delete_mapping(profile.id, username)
        return self.success()

    def change_link(self, profile: Profile, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        client_id, _ = self._resolve_user(profile, username)
        self.client(profile).revoke(client_id)
        access = self.client(profile).access(client_id)
        return self.success(access.get("obj", access))
