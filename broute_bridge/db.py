from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .crypto import SecretBox


@dataclass(frozen=True)
class Profile:
    id: int
    name: str
    token_prefix: str
    reseller_url: str
    reseller_api_key: str
    default_inbound_ids: list[int]
    active: bool


class Database:
    def __init__(self, path: Path, secret_box: SecretBox):
        self.path = path
        self.secret_box = secret_box
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def migrate(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profiles(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    token_prefix TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    reseller_url TEXT NOT NULL,
                    reseller_api_key_enc TEXT NOT NULL,
                    default_inbound_ids TEXT NOT NULL DEFAULT '[]',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS client_mappings(
                    profile_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    reseller_client_id INTEGER NOT NULL,
                    sub_id TEXT,
                    last_verified_at INTEGER NOT NULL,
                    PRIMARY KEY(profile_id, username),
                    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS replay_guard(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    action TEXT NOT NULL,
                    username TEXT,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    UNIQUE(profile_id, fingerprint),
                    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_replay_created
                    ON replay_guard(profile_id, created_at);
                """
            )
            con.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','1')"
            )
            con.commit()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_profile(
        self,
        *,
        name: str,
        reseller_url: str,
        reseller_api_key: str,
        default_inbound_ids: list[int],
    ) -> tuple[Profile, str]:
        raw_token = "br_live_" + secrets.token_urlsafe(32)
        token_prefix = raw_token[:16]
        now = int(time.time())
        url = reseller_url.rstrip("/")
        encrypted_key = self.secret_box.encrypt(reseller_api_key)
        inbound_json = json.dumps(sorted(set(int(x) for x in default_inbound_ids)))
        with self.connect() as con:
            cur = con.execute(
                """
                INSERT INTO profiles(
                    name, token_prefix, token_hash, reseller_url,
                    reseller_api_key_enc, default_inbound_ids,
                    active, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,1,?,?)
                """,
                (
                    name.strip(), token_prefix, self._token_hash(raw_token), url,
                    encrypted_key, inbound_json, now, now,
                ),
            )
            con.commit()
            profile_id = int(cur.lastrowid)
        return self.get_profile_by_id(profile_id), raw_token

    def list_profiles(self) -> list[Profile]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        return [self._row_to_profile(row) for row in rows]

    def get_profile_by_id(self, profile_id: int) -> Profile:
        with self.connect() as con:
            row = con.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            raise KeyError("Profile not found")
        return self._row_to_profile(row)

    def get_profile_by_name(self, name: str) -> Profile | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM profiles WHERE name=? LIMIT 1",
                (str(name).strip(),),
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def delete_profile(self, name: str) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "DELETE FROM profiles WHERE name=?",
                (str(name).strip(),),
            )
            con.commit()
        return cur.rowcount > 0

    def authenticate(self, token: str) -> Profile | None:
        if not token:
            return None
        token_hash = self._token_hash(token)
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM profiles WHERE token_hash=? AND active=1 LIMIT 1",
                (token_hash,),
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def _row_to_profile(self, row: sqlite3.Row) -> Profile:
        return Profile(
            id=int(row["id"]),
            name=str(row["name"]),
            token_prefix=str(row["token_prefix"]),
            reseller_url=str(row["reseller_url"]),
            reseller_api_key=self.secret_box.decrypt(str(row["reseller_api_key_enc"])),
            default_inbound_ids=[int(x) for x in json.loads(row["default_inbound_ids"] or "[]")],
            active=bool(row["active"]),
        )

    def set_mapping(self, profile_id: int, username: str, client_id: int, sub_id: str | None = None) -> None:
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO client_mappings(profile_id, username, reseller_client_id, sub_id, last_verified_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(profile_id, username) DO UPDATE SET
                    reseller_client_id=excluded.reseller_client_id,
                    sub_id=COALESCE(excluded.sub_id, client_mappings.sub_id),
                    last_verified_at=excluded.last_verified_at
                """,
                (profile_id, username, client_id, sub_id, now),
            )
            con.commit()

    def get_mapping(self, profile_id: int, username: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM client_mappings WHERE profile_id=? AND username=?",
                (profile_id, username),
            ).fetchone()
        return dict(row) if row else None

    def delete_mapping(self, profile_id: int, username: str) -> None:
        with self.connect() as con:
            con.execute(
                "DELETE FROM client_mappings WHERE profile_id=? AND username=?",
                (profile_id, username),
            )
            con.commit()

    def replay_begin(
        self,
        *,
        profile_id: int,
        fingerprint: str,
        action: str,
        username: str | None,
        window_seconds: int,
    ) -> tuple[str, dict[str, Any] | None]:
        now = int(time.time())
        cutoff = now - max(1, int(window_seconds))
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM replay_guard WHERE profile_id=? AND created_at<?",
                (profile_id, cutoff),
            )
            row = con.execute(
                "SELECT status,response_json FROM replay_guard WHERE profile_id=? AND fingerprint=?",
                (profile_id, fingerprint),
            ).fetchone()
            if row:
                if row["status"] == "completed" and row["response_json"]:
                    con.commit()
                    return "replay", json.loads(row["response_json"])
                con.commit()
                return "uncertain", None
            con.execute(
                """
                INSERT INTO replay_guard(profile_id,fingerprint,action,username,status,created_at)
                VALUES(?,?,?,?, 'pending', ?)
                """,
                (profile_id, fingerprint, action, username, now),
            )
            con.commit()
        return "new", None

    def replay_complete(self, profile_id: int, fingerprint: str, response: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE replay_guard
                SET status='completed',response_json=?,completed_at=?
                WHERE profile_id=? AND fingerprint=?
                """,
                (json.dumps(response, separators=(",", ":")), int(time.time()), profile_id, fingerprint),
            )
            con.commit()

    def online_backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
