from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path
    master_key_path: Path
    bind_host: str
    bind_port: int
    request_timeout: float
    replay_window_seconds: int
    verify_reseller_tls: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=Path(os.getenv("BROUTE_DB_PATH", "/var/lib/broute-bridge/bridge.db")),
            master_key_path=Path(os.getenv("BROUTE_MASTER_KEY_PATH", "/etc/broute-bridge/master.key")),
            bind_host=os.getenv("BROUTE_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.getenv("BROUTE_BIND_PORT", "8765")),
            request_timeout=float(os.getenv("BROUTE_REQUEST_TIMEOUT", "15")),
            replay_window_seconds=int(os.getenv("BROUTE_REPLAY_WINDOW_SECONDS", "120")),
            verify_reseller_tls=_env_bool("BROUTE_VERIFY_RESELLER_TLS", True),
            log_level=os.getenv("BROUTE_LOG_LEVEL", "INFO").upper(),
        )
