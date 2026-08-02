"""Device-side change detection and central synchronisation client.

Existing CRUD code continues to write to the local SQLite database.  Rather
than touching every write path, this module creates canonical snapshots of the
portable tables, compares their hashes with ``local_sync_state`` and sends only
changed records.  Remote changes are applied with table-specific adapters so
machine-local paths and credentials never leave the device.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

import httpx

from services import db as local_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(payload: dict[str, Any]) -> tuple[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_id(*parts: Any) -> str:
    raw = "\x1f".join("" if item is None else str(item) for item in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


@dataclass
class LocalRecord:
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    modified_at: str

    @property
    def payload_hash(self) -> str:
        return _canonical(self.payload)[1]


_STATUS_LOCK = threading.Lock()
_STATUS: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_started_at": None,
    "last_completed_at": None,
    "last_error": None,
    "last_result": None,
}


def get_sync_status() -> dict[str, Any]:
    with _STATUS_LOCK:
        result = dict(_STATUS)
    result.update(
        {
            "server_url": get_server_url(),
            "token_set": bool(get_sync_token()),
            "device_id": get_device_id(create=False),
        }
    )
    result["enabled"] = bool(result["server_url"] and result["token_set"])
    return result


def get_server_url() -> str:
    return os.getenv("SYNC_SERVER_URL", "").strip().rstrip("/")


def get_sync_token() -> str:
    return os.getenv("SYNC_TOKEN", "").strip()


def get_sync_interval() -> int:
    try:
        return max(30, int(os.getenv("SYNC_INTERVAL_SECONDS", "300")))
    except ValueError:
        return 300


def init_local_sync_schema() -> None:
    with local_db.get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS local_sync_state (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                server_version INTEGER NOT NULL DEFAULT 0,
                modified_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (entity_type, entity_id)
            );
            CREATE TABLE IF NOT EXISTS local_sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_sync_files (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL
            );
            """
        )
        try:
            conn.execute("ALTER TABLE local_sync_state ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass


def _meta_get(key: str) -> str | None:
    init_local_sync_schema()
    with local_db.get_db() as conn:
        row = conn.execute("SELECT value FROM local_sync_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(key: str, value: str) -> None:
    init_local_sync_schema()
    with local_db.get_db() as conn:
        conn.execute(
            "INSERT INTO local_sync_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_device_id(*, create: bool = True) -> str:
    value = _meta_get("device_id")
    if not value and create:
        value = str(uuid.uuid4())
        _meta_set("device_id", value)
    return value or ""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    return [_row_dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]


def _file_sha256(path: str) -> str | None:
    source = Path(path)
    if not source.is_file():
        return None
    stat = source.stat()
    with local_db.get_db() as conn:
        cached = conn.execute(
            "SELECT sha256 FROM local_sync_files WHERE path=? AND size=? AND mtime_ns=?",
            (str(source), stat.st_size, stat.st_mtime_ns),
        ).fetchone()
    if cached:
        return cached[0]
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with local_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO local_sync_files(path, size, mtime_ns, sha256)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size=excluded.size, mtime_ns=excluded.mtime_ns, sha256=excluded.sha256
            """,
            (str(source), stat.st_size, stat.st_mtime_ns, value),
        )
    return value


def _record(entity_type: str, entity_id: str, payload: dict[str, Any], timestamp: str | None) -> LocalRecord:
    return LocalRecord(entity_type, entity_id, payload, timestamp or _utc_now())


def scan_local_records() -> list[LocalRecord]:
    """Return a portable snapshot, excluding caches, secrets and device paths."""
    init_local_sync_schema()
    records: list[LocalRecord] = []
    with local_db.get_db() as conn:
        folders = {row["id"]: row["name"] for row in _rows(conn, "library_folders")}

        for row in _rows(conn, "library_folders"):
            payload = {key: row.get(key) for key in ("username", "name", "created_at")}
            records.append(
                _record("library_folder", _stable_id(row["username"], row["name"]), payload, row.get("created_at"))
            )

        for row in _rows(conn, "documents"):
            payload = {
                key: row.get(key)
                for key in ("id", "username", "filename", "total_pages", "metadata", "created_at", "is_deleted")
            }
            payload["folder_name"] = folders.get(row.get("folder_id"))
            pdf_path = row.get("pdf_path") or ""
            sha256 = _file_sha256(pdf_path) if pdf_path else None
            payload["file_sha256"] = sha256
            if sha256 and os.path.isfile(pdf_path):
                payload["file_size"] = os.path.getsize(pdf_path)
            records.append(_record("document", row["id"], payload, row.get("created_at")))

        simple_specs: list[tuple[str, str, tuple[str, ...], str | None, set[str]]] = [
            ("translation", "translations", ("doc_id", "page_num", "suffix"), "saved_at", {"id"}),
            ("chat", "chats", ("doc_id", "role", "created_at", "content"), "created_at", {"id"}),
            ("page_insight", "page_insights", ("doc_id", "page_num", "kind", "suffix"), "saved_at", {"id"}),
            ("compare_session", "compare_sessions", ("id",), "updated_at", set()),
            ("paper_note", "paper_notes", ("doc_id",), "updated_at", set()),
            ("scholar_feedback", "scholar_feedback", ("username", "paper_id"), "updated_at", set()),
            ("scholar_impression", "scholar_impressions", ("username", "paper_id"), "last_seen_at", set()),
            ("scholar_bookmark", "scholar_bookmarks", ("username", "paper_id"), "updated_at", set()),
            ("scholar_feed_state", "scholar_feed_state", ("username",), "last_feed_at", set()),
            ("conference_watch", "scholar_conference_watch", ("username", "conference_id"), "created_at", set()),
            ("reading_activity", "reading_activity", ("username", "doc_id", "activity_date"), "last_read_at", set()),
        ]
        for entity_type, table, keys, timestamp_key, excluded in simple_specs:
            for row in _rows(conn, table):
                payload = {key: value for key, value in row.items() if key not in excluded}
                entity_id = _stable_id(*(row.get(key) for key in keys))
                records.append(_record(entity_type, entity_id, payload, row.get(timestamp_key) if timestamp_key else None))

        for row in _rows(conn, "vocabulary_cards"):
            excluded = {"id", "anki_note_id", "anki_status", "anki_error", "obsidian_synced"}
            payload = {key: value for key, value in row.items() if key not in excluded}
            entity_id = _stable_id(row["username"], row["doc_id"], row["normalized_term"])
            records.append(_record("vocabulary_card", entity_id, payload, row.get("updated_at")))

    return records


def collect_local_changes() -> list[dict[str, Any]]:
    current = {(record.entity_type, record.entity_id): record for record in scan_local_records()}
    now = _utc_now()
    changes: list[dict[str, Any]] = []
    with local_db.get_db() as conn:
        states = {
            (row["entity_type"], row["entity_id"]): _row_dict(row)
            for row in conn.execute("SELECT * FROM local_sync_state")
        }
    for key, record in current.items():
        state = states.get(key)
        digest = record.payload_hash
        if state is None or state["payload_hash"] != digest or bool(state["deleted"]):
            changes.append(
                {
                    "entity_type": record.entity_type,
                    "entity_id": record.entity_id,
                    "payload": record.payload,
                    "modified_at": record.modified_at if state is None else now,
                    "base_version": int(state["server_version"]) if state else 0,
                    "deleted": False,
                }
            )
    for key, state in states.items():
        if key not in current and not bool(state["deleted"]):
            changes.append(
                {
                    "entity_type": key[0],
                    "entity_id": key[1],
                    "payload": json.loads(state.get("payload_json") or "{}"),
                    "modified_at": now,
                    "base_version": int(state["server_version"]),
                    "deleted": True,
                }
            )
    return changes


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    payload: dict[str, Any],
    conflict_columns: Iterable[str],
    *,
    preserve: Iterable[str] = (),
) -> None:
    conflict_columns = tuple(conflict_columns)
    columns = list(payload)
    values = [payload[column] for column in columns]
    quoted = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    preserved = set(preserve)
    updates = ", ".join(
        f'"{column}"=excluded."{column}"'
        for column in columns
        if column not in set(conflict_columns) and column not in preserved
    )
    sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
    if conflict_columns and updates:
        targets = ", ".join(f'"{column}"' for column in conflict_columns)
        sql += f" ON CONFLICT({targets}) DO UPDATE SET {updates}"
    elif conflict_columns:
        sql += " ON CONFLICT DO NOTHING"
    conn.execute(sql, values)


_SIMPLE_APPLY: dict[str, tuple[str, tuple[str, ...]]] = {
    "translation": ("translations", ("doc_id", "page_num", "suffix")),
    "page_insight": ("page_insights", ("doc_id", "page_num", "kind", "suffix")),
    "compare_session": ("compare_sessions", ("id",)),
    "paper_note": ("paper_notes", ("doc_id",)),
    "scholar_feedback": ("scholar_feedback", ("username", "paper_id")),
    "scholar_impression": ("scholar_impressions", ("username", "paper_id")),
    "scholar_bookmark": ("scholar_bookmarks", ("username", "paper_id")),
    "scholar_feed_state": ("scholar_feed_state", ("username",)),
    "conference_watch": ("scholar_conference_watch", ("username", "conference_id")),
    "reading_activity": ("reading_activity", ("username", "doc_id", "activity_date")),
}


def _delete_by_payload(conn: sqlite3.Connection, table: str, keys: Iterable[str], payload: dict[str, Any]) -> None:
    keys = tuple(keys)
    if not keys or any(key not in payload for key in keys):
        return
    where = " AND ".join(f'"{key}"=?' for key in keys)
    conn.execute(f'DELETE FROM "{table}" WHERE {where}', [payload[key] for key in keys])


def _apply_record(conn: sqlite3.Connection, change: dict[str, Any]) -> None:
    entity_type = change["entity_type"]
    payload = dict(change.get("payload") or {})
    deleted = bool(change.get("deleted"))

    if entity_type == "library_folder":
        keys = ("username", "name")
        if deleted:
            row = conn.execute(
                "SELECT id FROM library_folders WHERE username=? AND name=?",
                (payload.get("username"), payload.get("name")),
            ).fetchone()
            if row:
                conn.execute("UPDATE documents SET folder_id=NULL WHERE folder_id=?", (row[0],))
            _delete_by_payload(conn, "library_folders", keys, payload)
        else:
            _upsert(conn, "library_folders", payload, keys)
        return

    if entity_type == "document":
        doc_id = payload.get("id") or change["entity_id"]
        if deleted:
            conn.execute("UPDATE documents SET is_deleted=1 WHERE id=?", (doc_id,))
            return
        folder_id = None
        folder_name = payload.pop("folder_name", None)
        payload.pop("file_sha256", None)
        payload.pop("file_size", None)
        if folder_name:
            username = payload.get("username", "")
            conn.execute(
                "INSERT INTO library_folders(username, name, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(username, name) DO NOTHING",
                (username, folder_name, payload.get("created_at") or _utc_now()),
            )
            folder_id = conn.execute(
                "SELECT id FROM library_folders WHERE username=? AND name=?",
                (username, folder_name),
            ).fetchone()[0]
        existing = conn.execute("SELECT pdf_path FROM documents WHERE id=?", (doc_id,)).fetchone()
        if existing:
            pdf_path = existing[0]
        else:
            from config import LIBRARY_DIR

            pdf_path = os.path.abspath(os.path.join(LIBRARY_DIR, doc_id, "document.pdf"))
        payload.update({"id": doc_id, "pdf_path": pdf_path, "folder_id": folder_id})
        _upsert(conn, "documents", payload, ("id",))
        return

    if entity_type == "chat":
        keys = ("doc_id", "role", "content", "created_at")
        if deleted:
            _delete_by_payload(conn, "chats", keys, payload)
        else:
            exists = conn.execute(
                "SELECT 1 FROM chats WHERE doc_id=? AND role=? AND content=? AND created_at=?",
                tuple(payload[key] for key in keys),
            ).fetchone()
            if not exists:
                _upsert(conn, "chats", payload, ())
        return

    if entity_type == "vocabulary_card":
        keys = ("username", "doc_id", "normalized_term")
        if deleted:
            _delete_by_payload(conn, "vocabulary_cards", keys, payload)
        else:
            payload.setdefault("anki_note_id", None)
            payload.setdefault("anki_status", "pending")
            payload.setdefault("anki_error", None)
            payload.setdefault("obsidian_synced", 0)
            _upsert(
                conn,
                "vocabulary_cards",
                payload,
                keys,
                preserve=("anki_note_id", "anki_status", "anki_error", "obsidian_synced"),
            )
        return

    spec = _SIMPLE_APPLY.get(entity_type)
    if spec:
        table, keys = spec
        if deleted:
            _delete_by_payload(conn, table, keys, payload)
        else:
            _upsert(conn, table, payload, keys)


def _state_upsert(conn: sqlite3.Connection, change: dict[str, Any]) -> None:
    payload = change.get("payload") or {}
    payload_json, calculated_hash = _canonical(payload)
    payload_hash = change.get("payload_hash") or calculated_hash
    conn.execute(
        """
        INSERT INTO local_sync_state
            (entity_type, entity_id, payload_hash, payload_json, server_version, modified_at, deleted)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, entity_id) DO UPDATE SET
            payload_hash=excluded.payload_hash,
            payload_json=excluded.payload_json,
            server_version=excluded.server_version,
            modified_at=excluded.modified_at,
            deleted=excluded.deleted
        """,
        (
            change["entity_type"],
            change["entity_id"],
            payload_hash,
            payload_json,
            int(change.get("version", 0)),
            change.get("modified_at") or _utc_now(),
            int(bool(change.get("deleted"))),
        ),
    )


_APPLY_PRIORITY = {
    "library_folder": 10,
    "document": 20,
    "translation": 30,
    "chat": 30,
    "page_insight": 30,
    "compare_session": 30,
    "paper_note": 30,
    "scholar_feedback": 30,
    "scholar_impression": 30,
    "scholar_bookmark": 30,
    "scholar_feed_state": 30,
    "conference_watch": 30,
    "reading_activity": 30,
    "vocabulary_card": 30,
}


def apply_remote_changes(changes: list[dict[str, Any]]) -> None:
    init_local_sync_schema()
    ordered = sorted(
        changes,
        key=lambda item: (
            bool(item.get("deleted")),
            _APPLY_PRIORITY.get(item.get("entity_type", ""), 100)
            * (-1 if item.get("deleted") else 1),
        ),
    )
    with local_db.get_db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for change in ordered:
            _apply_record(conn, change)
            _state_upsert(conn, change)


def _upload_document_files(client: httpx.Client, changes: list[dict[str, Any]]) -> int:
    uploaded = 0
    with local_db.get_db() as conn:
        paths = {
            row["id"]: row["pdf_path"]
            for row in conn.execute("SELECT id, pdf_path FROM documents")
        }
    for change in changes:
        if change["entity_type"] != "document" or change.get("deleted"):
            continue
        payload = change.get("payload") or {}
        sha256 = payload.get("file_sha256")
        path = paths.get(payload.get("id"))
        if not sha256 or not path or not os.path.isfile(path):
            continue
        response = client.head(f"/v1/files/{sha256}")
        if response.status_code == 200:
            continue
        if response.status_code != 404:
            response.raise_for_status()
        with open(path, "rb") as handle:
            response = client.put(
                f"/v1/files/{sha256}",
                params={"filename": os.path.basename(path)},
                content=handle.read(),
            )
        response.raise_for_status()
        uploaded += 1
    return uploaded


def _download_document_files(client: httpx.Client, changes: list[dict[str, Any]]) -> int:
    downloaded = 0
    for change in changes:
        if change["entity_type"] != "document" or change.get("deleted"):
            continue
        payload = change.get("payload") or {}
        sha256 = payload.get("file_sha256")
        doc_id = payload.get("id") or change["entity_id"]
        if not sha256:
            continue
        from config import LIBRARY_DIR

        target = Path(LIBRARY_DIR).expanduser().resolve() / doc_id / "document.pdf"
        if target.is_file() and _file_sha256(str(target)) == sha256:
            continue
        response = client.get(f"/v1/files/{sha256}")
        if response.status_code == 404:
            continue
        response.raise_for_status()
        actual = hashlib.sha256(response.content).hexdigest()
        if actual != sha256:
            raise RuntimeError(f"다운로드한 PDF의 SHA-256이 일치하지 않습니다: {doc_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".sync-tmp")
        temp.write_bytes(response.content)
        os.replace(temp, target)
        with local_db.get_db() as conn:
            conn.execute("UPDATE documents SET pdf_path=? WHERE id=?", (str(target), doc_id))
        downloaded += 1
    return downloaded


def sync_once(*, username: str | None = None) -> dict[str, Any]:
    url = get_server_url()
    token = get_sync_token()
    if not url or not token:
        raise RuntimeError("중앙 동기화 서버 URL과 토큰을 먼저 설정해야 합니다.")
    if username is None:
        from config import get_app_username

        username = get_app_username()
    init_local_sync_schema()
    device_id = get_device_id()
    device_name = os.getenv("SYNC_DEVICE_NAME", "").strip() or platform.node() or device_id[:8]
    pending = collect_local_changes()
    result = {"pushed": 0, "pulled": 0, "uploaded": 0, "downloaded": 0, "conflicts": 0}

    headers = {"X-Sync-Token": token}
    with httpx.Client(base_url=url, headers=headers, timeout=120.0, follow_redirects=False) as client:
        health = client.get("/v1/health")
        health.raise_for_status()
        client.post(
            "/v1/devices/register",
            json={"username": username, "device_id": device_id, "device_name": device_name},
        ).raise_for_status()
        result["uploaded"] = _upload_document_files(client, pending)

        for start in range(0, len(pending), 200):
            batch = pending[start : start + 200]
            response = client.post(
                "/v1/push",
                json={
                    "username": username,
                    "device_id": device_id,
                    "device_name": device_name,
                    "changes": batch,
                },
            )
            response.raise_for_status()
            body = response.json()
            accepted = body.get("accepted", [])
            conflicts = body.get("conflicts", [])
            with local_db.get_db() as conn:
                for change in accepted:
                    _state_upsert(conn, change)
            if conflicts:
                apply_remote_changes(conflicts)
                result["conflicts"] += len(conflicts)
            result["pushed"] += len(accepted)

        cursor = int(_meta_get(f"cursor:{username}") or 0)
        all_remote: list[dict[str, Any]] = []
        while True:
            response = client.get(
                "/v1/pull",
                params={"username": username, "cursor": cursor, "limit": 500},
            )
            response.raise_for_status()
            body = response.json()
            changes = body.get("changes", [])
            if changes:
                apply_remote_changes(changes)
                all_remote.extend(changes)
                result["pulled"] += len(changes)
            cursor = int(body.get("cursor", cursor))
            _meta_set(f"cursor:{username}", str(cursor))
            if not body.get("has_more"):
                break
        result["downloaded"] = _download_document_files(client, all_remote)
    return result


async def sync_once_async(*, username: str | None = None) -> dict[str, Any]:
    with _STATUS_LOCK:
        if _STATUS["running"]:
            return {"skipped": True, "reason": "already_running"}
        _STATUS["running"] = True
        _STATUS["last_started_at"] = _utc_now()
        _STATUS["last_error"] = None
    try:
        result = await asyncio.to_thread(sync_once, username=username)
        with _STATUS_LOCK:
            _STATUS["last_completed_at"] = _utc_now()
            _STATUS["last_result"] = result
        return result
    except Exception as exc:
        with _STATUS_LOCK:
            _STATUS["last_error"] = str(exc)
        raise
    finally:
        with _STATUS_LOCK:
            _STATUS["running"] = False


async def sync_loop() -> None:
    while True:
        try:
            if get_server_url() and get_sync_token():
                await sync_once_async()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The status endpoint exposes the error; transient network failure
            # must never stop the local desktop backend.
            pass
        await asyncio.sleep(get_sync_interval())
