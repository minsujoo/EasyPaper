"""Personal central synchronisation service.

The desktop backend deliberately keeps AI execution and credentials on each
device.  This service stores only portable application records and immutable
paper files.  It is a separate FastAPI application so it can run on an
always-on Ubuntu machine without starting the desktop-only crawler/Anki jobs.

SQLite is sufficient for the initial single-user deployment because every
write is serialised by this one service.  The API and repository boundary are
kept independent from the desktop database so the central store can later be
moved to PostgreSQL without changing clients.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncChange(BaseModel):
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)
    modified_at: str
    base_version: int = Field(default=0, ge=0)
    deleted: bool = False


class DeviceRegistration(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    device_id: str = Field(min_length=1, max_length=200)
    device_name: str = Field(default="", max_length=200)
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class PushRequest(DeviceRegistration):
    changes: list[SyncChange] = Field(default_factory=list, max_length=1000)


class SyncStore:
    def __init__(self, db_path: str, storage_dir: str):
        self.db_path = os.path.abspath(os.path.expanduser(db_path))
        self.storage_dir = Path(storage_dir).expanduser().resolve()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS sync_devices (
                    username TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL DEFAULT '',
                    registered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (username, device_id)
                );
                CREATE TABLE IF NOT EXISTS sync_records (
                    username TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    origin_device TEXT NOT NULL,
                    PRIMARY KEY (username, entity_type, entity_id)
                );
                CREATE TABLE IF NOT EXISTS sync_changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    origin_device TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sync_changes_user_seq
                    ON sync_changes(username, seq);
                CREATE TABLE IF NOT EXISTS sync_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    incoming_device TEXT NOT NULL,
                    incoming_modified_at TEXT NOT NULL,
                    incoming_payload_json TEXT NOT NULL,
                    current_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_files (
                    sha256 TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _canonical_payload(payload: dict[str, Any]) -> tuple[str, str]:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def register_device(self, registration: DeviceRegistration) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_devices
                    (username, device_id, device_name, registered_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username, device_id) DO UPDATE SET
                    device_name=excluded.device_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    registration.username,
                    registration.device_id,
                    registration.device_name,
                    now,
                    now,
                ),
            )

    def push(self, request: PushRequest) -> dict[str, Any]:
        self.register_device(request)
        accepted: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        now = _utc_now()

        with self.connect() as conn:
            for change in request.changes:
                current = conn.execute(
                    """
                    SELECT * FROM sync_records
                    WHERE username=? AND entity_type=? AND entity_id=?
                    """,
                    (request.username, change.entity_type, change.entity_id),
                ).fetchone()

                # Tombstones retain the last portable payload.  Natural-key
                # entities (translation, folder, vocabulary card, ...) need
                # those fields on another device to delete the matching row.
                effective_payload = change.payload
                if change.deleted and current and not effective_payload:
                    effective_payload = json.loads(current["payload_json"])
                payload_json, payload_hash = self._canonical_payload(effective_payload)

                if current and current["payload_hash"] == payload_hash and bool(current["deleted"]) == change.deleted:
                    accepted.append(self._record_dict(current))
                    continue

                # A stale writer never silently overwrites a newer version.  We
                # keep the rejected body for recovery and return the winner so
                # the client can immediately converge on the server version.
                is_stale_initial = bool(
                    current
                    and change.base_version == 0
                    and change.modified_at <= current["modified_at"]
                )
                is_stale_version = bool(
                    current
                    and change.base_version not in (0, int(current["version"]))
                    and change.modified_at <= current["modified_at"]
                )
                # Vault files must never use timestamp-based last-writer-wins:
                # clock skew or a push race could otherwise discard one of two
                # edits. The client turns the rejected body into a named
                # conflict copy, so any version mismatch is recoverable.
                is_vault_version_conflict = bool(
                    current
                    and change.entity_type == "vault_file"
                    and change.base_version != int(current["version"])
                )
                if current and (is_stale_initial or is_stale_version or is_vault_version_conflict):
                    conn.execute(
                        """
                        INSERT INTO sync_conflicts
                            (username, entity_type, entity_id, incoming_device,
                             incoming_modified_at, incoming_payload_json,
                             current_version, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            request.username,
                            change.entity_type,
                            change.entity_id,
                            request.device_id,
                            change.modified_at,
                            payload_json,
                            int(current["version"]),
                            now,
                        ),
                    )
                    conflicts.append(self._record_dict(current))
                    continue

                version = 1 if current is None else int(current["version"]) + 1
                values = (
                    request.username,
                    change.entity_type,
                    change.entity_id,
                    version,
                    change.modified_at,
                    int(change.deleted),
                    payload_json,
                    payload_hash,
                    request.device_id,
                )
                conn.execute(
                    """
                    INSERT INTO sync_records
                        (username, entity_type, entity_id, version, modified_at,
                         deleted, payload_json, payload_hash, origin_device)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(username, entity_type, entity_id) DO UPDATE SET
                        version=excluded.version,
                        modified_at=excluded.modified_at,
                        deleted=excluded.deleted,
                        payload_json=excluded.payload_json,
                        payload_hash=excluded.payload_hash,
                        origin_device=excluded.origin_device
                    """,
                    values,
                )
                cursor = conn.execute(
                    """
                    INSERT INTO sync_changes
                        (username, entity_type, entity_id, version, modified_at,
                         deleted, payload_json, payload_hash, origin_device, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values + (now,),
                )
                accepted.append(
                    {
                        "entity_type": change.entity_type,
                        "entity_id": change.entity_id,
                        "version": version,
                        "modified_at": change.modified_at,
                        "deleted": change.deleted,
                        "payload": effective_payload,
                        "payload_hash": payload_hash,
                        "origin_device": request.device_id,
                        "seq": cursor.lastrowid,
                    }
                )

            cursor = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM sync_changes WHERE username=?",
                (request.username,),
            ).fetchone()[0]

        return {"accepted": accepted, "conflicts": conflicts, "cursor": int(cursor)}

    @staticmethod
    def _record_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "version": int(row["version"]),
            "modified_at": row["modified_at"],
            "deleted": bool(row["deleted"]),
            "payload": json.loads(row["payload_json"]),
            "payload_hash": row["payload_hash"],
            "origin_device": row["origin_device"],
        }

    def pull(self, username: str, cursor: int, limit: int) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sync_changes
                WHERE username=? AND seq>?
                ORDER BY seq ASC LIMIT ?
                """,
                (username, cursor, limit),
            ).fetchall()
            changes = []
            next_cursor = cursor
            for row in rows:
                item = self._record_dict(row)
                item["seq"] = int(row["seq"])
                changes.append(item)
                next_cursor = int(row["seq"])
            latest = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM sync_changes WHERE username=?",
                (username,),
            ).fetchone()[0]
        return {
            "changes": changes,
            "cursor": next_cursor,
            "latest_cursor": int(latest),
            "has_more": next_cursor < int(latest),
        }

    def status(self) -> dict[str, Any]:
        with self.connect() as conn:
            device_count = conn.execute("SELECT COUNT(*) FROM sync_devices").fetchone()[0]
            record_count = conn.execute("SELECT COUNT(*) FROM sync_records WHERE deleted=0").fetchone()[0]
            conflict_count = conn.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0]
            file_count = conn.execute("SELECT COUNT(*) FROM sync_files").fetchone()[0]
        return {
            "devices": int(device_count),
            "records": int(record_count),
            "conflicts": int(conflict_count),
            "files": int(file_count),
        }

    def file_path(self, sha256: str) -> Path:
        return self.storage_dir / sha256[:2] / sha256[2:4] / sha256

    def has_file(self, sha256: str) -> bool:
        return self.file_path(sha256).is_file()

    def save_file(self, sha256: str, filename: str, body: bytes) -> dict[str, Any]:
        actual = hashlib.sha256(body).hexdigest()
        if actual != sha256:
            raise ValueError("파일 SHA-256이 요청 경로와 일치하지 않습니다.")
        path = self.file_path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temp = path.with_suffix(".tmp")
            temp.write_bytes(body)
            os.replace(temp, path)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_files (sha256, size, filename, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET filename=excluded.filename
                """,
                (sha256, len(body), filename, _utc_now()),
            )
        return {"sha256": sha256, "size": len(body), "filename": filename}


def create_sync_app(
    *,
    db_path: str | None = None,
    storage_dir: str | None = None,
    token: str | None = None,
) -> FastAPI:
    db_path = db_path or os.getenv("SYNC_DB_PATH", "./sync-data/sync.db")
    storage_dir = storage_dir or os.getenv("SYNC_STORAGE_DIR", "./sync-data/files")
    expected_token = token if token is not None else os.getenv("SYNC_TOKEN", "")
    max_file_size = int(os.getenv("SYNC_MAX_FILE_SIZE_MB", "500")) * 1024 * 1024
    store = SyncStore(db_path, storage_dir)

    app = FastAPI(title="Personal Research Sync API", version="0.1.0")
    app.state.sync_store = store

    def authenticate(x_sync_token: str = Header(default="")) -> None:
        if not expected_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SYNC_TOKEN이 설정되지 않았습니다.",
            )
        if not hmac.compare_digest(x_sync_token, expected_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="동기화 토큰이 올바르지 않습니다.")

    @app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "research-sync"}

    @app.post("/v1/devices/register", dependencies=[Depends(authenticate)])
    def register_device(data: DeviceRegistration) -> dict[str, bool]:
        store.register_device(data)
        return {"registered": True}

    @app.post("/v1/push", dependencies=[Depends(authenticate)])
    def push(data: PushRequest) -> dict[str, Any]:
        if any(change.entity_type == "vault_file" for change in data.changes):
            if "vault-files-v1" not in data.capabilities:
                raise HTTPException(
                    status_code=409,
                    detail="Vault 파일 변경은 vault-files-v1 동기화 클라이언트에서만 보낼 수 있습니다.",
                )
        return store.push(data)

    @app.get("/v1/pull", dependencies=[Depends(authenticate)])
    def pull(
        username: str = Query(min_length=1, max_length=200),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict[str, Any]:
        return store.pull(username, cursor, limit)

    @app.get("/v1/status", dependencies=[Depends(authenticate)])
    def sync_status() -> dict[str, Any]:
        return store.status()

    @app.head("/v1/files/{sha256}", dependencies=[Depends(authenticate)])
    def has_file(sha256: str) -> dict[str, bool]:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256.lower()):
            raise HTTPException(status_code=400, detail="올바르지 않은 SHA-256입니다.")
        if not store.has_file(sha256.lower()):
            raise HTTPException(status_code=404, detail="파일이 없습니다.")
        return {"exists": True}

    @app.put("/v1/files/{sha256}", dependencies=[Depends(authenticate)])
    async def put_file(sha256: str, request: Request, filename: str = Query(default="document.pdf")) -> dict[str, Any]:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256.lower()):
            raise HTTPException(status_code=400, detail="올바르지 않은 SHA-256입니다.")
        content_length = int(request.headers.get("content-length", "0") or 0)
        if content_length > max_file_size:
            raise HTTPException(status_code=413, detail="동기화 파일 크기 제한을 초과했습니다.")
        body = await request.body()
        if len(body) > max_file_size:
            raise HTTPException(status_code=413, detail="동기화 파일 크기 제한을 초과했습니다.")
        try:
            return store.save_file(sha256.lower(), os.path.basename(filename), body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/files/{sha256}", dependencies=[Depends(authenticate)])
    def get_file(sha256: str):
        path = store.file_path(sha256.lower())
        if not path.is_file():
            raise HTTPException(status_code=404, detail="파일이 없습니다.")
        # PDFs and arbitrary Vault assets share the content-addressed store.
        return FileResponse(path, media_type="application/octet-stream", filename=sha256)

    return app


app = create_sync_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "sync_server_app:app",
        host=os.getenv("SYNC_HOST", "127.0.0.1"),
        port=int(os.getenv("SYNC_PORT", "8766")),
        reload=False,
    )
