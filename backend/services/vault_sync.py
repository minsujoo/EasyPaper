"""Whole-Obsidian-vault synchronisation over the personal sync server.

Research database records keep their existing adapter-based synchronisation.
This module adds a separate file manifest and cursor so ordinary notes,
attachments, themes and Obsidian configuration can converge without ever
copying a live SQLite database or machine-specific engine credentials.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from services import db as local_db
from services.sync_client import get_device_id, get_server_url, get_sync_token


VAULT_ENTITY_TYPE = "vault_file"
DEFAULT_SCOPE = "primary"
_EXCLUDED_PARTS = {
    ".git",
    ".trash",
    ".cache",
    ".codex-tmp",
    ".smart-env",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}
_EXCLUDED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_EXCLUDED_EXACT = {
    ".obsidian/workspace.json",
    ".obsidian/workspace-mobile.json",
    ".obsidian/plugins/paper-research-workspace/data.json",
}
_MAX_PATH_LENGTH = 1000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_ns(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc).isoformat()


def _normalize_relative_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("올바르지 않은 Vault 상대 경로입니다.")
    normalized = path.as_posix()
    if len(normalized) > _MAX_PATH_LENGTH or "\x00" in normalized:
        raise ValueError("Vault 상대 경로가 너무 길거나 올바르지 않습니다.")
    return normalized


def _is_excluded(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.name in _EXCLUDED_NAMES or relative_path in _EXCLUDED_EXACT:
        return True
    if path.name.startswith("workspace") and path.suffix == ".json" and path.parent.as_posix() == ".obsidian":
        return True
    return any(part in _EXCLUDED_PARTS for part in path.parts)


def _entity_id(scope: str, relative_path: str) -> str:
    raw = f"{scope}\x1f{relative_path}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class VaultFile:
    path: str
    absolute_path: Path
    size: int
    mtime_ns: int
    sha256: str

    def payload(self, scope: str) -> dict[str, Any]:
        return {
            "scope": scope,
            "path": self.path,
            "kind": "file",
            "sha256": self.sha256,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }


_STATUS_LOCK = threading.Lock()
_STATUS: dict[str, Any] = {
    "running": False,
    "last_started_at": None,
    "last_completed_at": None,
    "last_error": None,
    "last_result": None,
    "vault_root": "",
}


def get_vault_sync_status() -> dict[str, Any]:
    with _STATUS_LOCK:
        return dict(_STATUS)


def init_vault_sync_schema() -> None:
    with local_db.get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS local_vault_sync_state (
                scope TEXT NOT NULL,
                path TEXT NOT NULL,
                local_sha256 TEXT NOT NULL DEFAULT '',
                server_sha256 TEXT NOT NULL DEFAULT '',
                server_version INTEGER NOT NULL DEFAULT 0,
                size INTEGER NOT NULL DEFAULT 0,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (scope, path)
            );
            CREATE TABLE IF NOT EXISTS local_vault_sync_meta (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (scope, key)
            );
            """
        )


def _load_state(scope: str) -> dict[str, dict[str, Any]]:
    init_vault_sync_schema()
    with local_db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM local_vault_sync_state WHERE scope=?", (scope,)
        ).fetchall()
    return {row["path"]: dict(row) for row in rows}


def _state_upsert(scope: str, change: dict[str, Any], *, local_sha256: str = "") -> None:
    payload = change.get("payload") or {}
    relative_path = _normalize_relative_path(payload.get("path", ""))
    deleted = bool(change.get("deleted"))
    sha256 = "" if deleted else str(payload.get("sha256") or "")
    with local_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO local_vault_sync_state
                (scope, path, local_sha256, server_sha256, server_version,
                 size, mtime_ns, deleted, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, path) DO UPDATE SET
                local_sha256=excluded.local_sha256,
                server_sha256=excluded.server_sha256,
                server_version=excluded.server_version,
                size=excluded.size,
                mtime_ns=excluded.mtime_ns,
                deleted=excluded.deleted,
                synced_at=excluded.synced_at
            """,
            (
                scope,
                relative_path,
                "" if deleted else (local_sha256 or sha256),
                sha256,
                int(change.get("version", 0)),
                int(payload.get("size") or 0),
                int(payload.get("mtime_ns") or 0),
                int(deleted),
                _utc_now(),
            ),
        )


def _meta_get(scope: str, key: str) -> str | None:
    init_vault_sync_schema()
    with local_db.get_db() as conn:
        row = conn.execute(
            "SELECT value FROM local_vault_sync_meta WHERE scope=? AND key=?",
            (scope, key),
        ).fetchone()
    return row[0] if row else None


def _meta_set(scope: str, key: str, value: str) -> None:
    init_vault_sync_schema()
    with local_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO local_vault_sync_meta(scope, key, value) VALUES (?, ?, ?)
            ON CONFLICT(scope, key) DO UPDATE SET value=excluded.value
            """,
            (scope, key, value),
        )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_vault_files(vault_root: str | Path, *, scope: str = DEFAULT_SCOPE) -> dict[str, VaultFile]:
    root = Path(vault_root).expanduser().resolve()
    if not root.is_dir() or not (root / ".obsidian").is_dir():
        raise ValueError("선택한 경로는 Obsidian Vault가 아닙니다.")
    state = _load_state(scope)
    found: dict[str, VaultFile] = {}
    for current_root, dirs, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        relative_dir = current.relative_to(root)
        dirs[:] = [
            name for name in dirs
            if not (current / name).is_symlink()
            and not _is_excluded((relative_dir / name).as_posix())
        ]
        for name in files:
            absolute = current / name
            relative = (relative_dir / name).as_posix()
            if absolute.is_symlink() or _is_excluded(relative):
                continue
            try:
                stat = absolute.stat()
            except FileNotFoundError:
                continue
            cached = state.get(relative)
            if (
                cached
                and not bool(cached["deleted"])
                and int(cached["size"]) == stat.st_size
                and int(cached["mtime_ns"]) == stat.st_mtime_ns
                and cached["local_sha256"]
            ):
                sha256 = cached["local_sha256"]
            else:
                sha256 = _hash_file(absolute)
            found[relative] = VaultFile(relative, absolute, stat.st_size, stat.st_mtime_ns, sha256)
    return found


def _local_changes(
    files: dict[str, VaultFile], state: dict[str, dict[str, Any]], scope: str
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    now = _utc_now()
    for relative, item in files.items():
        previous = state.get(relative)
        if previous is None or bool(previous["deleted"]) or previous["local_sha256"] != item.sha256:
            changes[relative] = {
                "entity_type": VAULT_ENTITY_TYPE,
                "entity_id": _entity_id(scope, relative),
                "payload": item.payload(scope),
                "modified_at": _iso_from_ns(item.mtime_ns) if previous is None else now,
                "base_version": int(previous["server_version"]) if previous else 0,
                "deleted": False,
            }
    for relative, previous in state.items():
        if relative not in files and not bool(previous["deleted"]):
            changes[relative] = {
                "entity_type": VAULT_ENTITY_TYPE,
                "entity_id": _entity_id(scope, relative),
                "payload": {
                    "scope": scope,
                    "path": relative,
                    "kind": "file",
                    "sha256": previous["server_sha256"],
                    "size": int(previous["size"]),
                    "mtime_ns": int(previous["mtime_ns"]),
                },
                "modified_at": now,
                "base_version": int(previous["server_version"]),
                "deleted": True,
            }
    return changes


def _conflict_path(root: Path, relative: str, device_id: str) -> Path:
    source = root / PurePosixPath(relative)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = source.suffix
    stem = source.name[: max(20, 180 - len(suffix))] if source.name else "file"
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    candidate = source.with_name(f"{stem}.sync-conflict-{device_id[:8]}-{stamp}{suffix}")
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"{stem}.sync-conflict-{device_id[:8]}-{stamp}-{counter}{suffix}")
        counter += 1
    return candidate


def _preserve_conflict(root: Path, relative: str, device_id: str) -> str | None:
    source = root / PurePosixPath(relative)
    if not source.is_file():
        return None
    target = _conflict_path(root, relative, device_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.relative_to(root).as_posix()


def _download_and_apply(
    client: httpx.Client,
    root: Path,
    change: dict[str, Any],
) -> None:
    payload = change.get("payload") or {}
    relative = _normalize_relative_path(payload.get("path", ""))
    if _is_excluded(relative):
        return
    target = (root / PurePosixPath(relative)).resolve()
    if root not in target.parents:
        raise ValueError("Vault 밖의 파일에는 동기화를 적용할 수 없습니다.")
    if change.get("deleted"):
        if target.is_file() or target.is_symlink():
            target.unlink()
        return
    sha256 = str(payload.get("sha256") or "")
    if len(sha256) != 64:
        raise ValueError("원격 Vault 파일의 SHA-256이 올바르지 않습니다.")
    if target.is_file() and _hash_file(target) == sha256:
        return
    response = client.get(f"/v1/files/{sha256}")
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != sha256:
        raise RuntimeError(f"다운로드한 Vault 파일의 SHA-256이 일치하지 않습니다: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.vault-sync-tmp")
    temporary.write_bytes(response.content)
    os.replace(temporary, target)
    mtime_ns = int(payload.get("mtime_ns") or 0)
    if mtime_ns > 0:
        os.utime(target, ns=(mtime_ns, mtime_ns))


def _apply_remote_batch(
    client: httpx.Client,
    root: Path,
    scope: str,
    changes: list[dict[str, Any]],
    locally_changed: dict[str, dict[str, Any]],
    device_id: str,
) -> tuple[int, int, int, int, list[str]]:
    applied = 0
    downloaded = 0
    deleted = 0
    conflicts = 0
    conflict_paths: list[str] = []
    # A new client may pull several historical versions of one path. Applying
    # only the latest avoids manufacturing conflicts against obsolete states.
    latest: dict[str, dict[str, Any]] = {}
    for change in changes:
        if change.get("entity_type") != VAULT_ENTITY_TYPE:
            continue
        payload = change.get("payload") or {}
        if payload.get("scope", DEFAULT_SCOPE) != scope:
            continue
        try:
            relative = _normalize_relative_path(payload.get("path", ""))
        except ValueError:
            continue
        latest[relative] = change

    state = _load_state(scope)
    for relative, change in latest.items():
        if _is_excluded(relative):
            continue
        previous = state.get(relative)
        if previous and int(change.get("version", 0)) <= int(previous["server_version"]):
            continue
        payload = change.get("payload") or {}
        remote_sha = "" if change.get("deleted") else str(payload.get("sha256") or "")
        local = locally_changed.get(relative)
        local_sha = "" if not local or local.get("deleted") else str((local.get("payload") or {}).get("sha256") or "")
        is_concurrent = local is not None and local_sha != remote_sha
        if is_concurrent:
            preserved = _preserve_conflict(root, relative, device_id)
            if preserved:
                conflict_paths.append(preserved)
            conflicts += 1
        target = root / PurePosixPath(relative)
        if change.get("deleted"):
            deleted += int(target.is_file() or target.is_symlink())
        else:
            downloaded += int(not target.is_file() or _hash_file(target) != remote_sha)
        _download_and_apply(client, root, change)
        _state_upsert(scope, change, local_sha256=remote_sha)
        applied += 1
    return applied, downloaded, deleted, conflicts, conflict_paths


def _upload_files(client: httpx.Client, files: dict[str, VaultFile], changes: list[dict[str, Any]]) -> int:
    uploaded = 0
    for change in changes:
        if change.get("deleted"):
            continue
        payload = change.get("payload") or {}
        sha256 = payload.get("sha256")
        item = files.get(payload.get("path"))
        if not sha256 or not item:
            continue
        response = client.head(f"/v1/files/{sha256}")
        if response.status_code == 200:
            continue
        if response.status_code != 404:
            response.raise_for_status()
        with item.absolute_path.open("rb") as handle:
            response = client.put(
                f"/v1/files/{sha256}",
                params={"filename": item.absolute_path.name},
                content=iter(lambda: handle.read(1024 * 1024), b""),
            )
        response.raise_for_status()
        uploaded += 1
    return uploaded


def sync_vault_once(
    vault_root: str,
    *,
    scope: str = DEFAULT_SCOPE,
    username: str | None = None,
) -> dict[str, Any]:
    root = Path(vault_root).expanduser().resolve()
    if not root.is_dir() or not (root / ".obsidian").is_dir():
        raise ValueError("선택한 경로는 Obsidian Vault가 아닙니다.")
    scope = str(scope or DEFAULT_SCOPE).strip()[:100] or DEFAULT_SCOPE
    url = get_server_url()
    token = get_sync_token()
    if not url or not token:
        raise RuntimeError("중앙 동기화 서버 URL과 토큰을 먼저 설정해야 합니다.")
    if username is None:
        from config import get_app_username

        username = get_app_username()

    device_id = get_device_id()
    device_name = os.getenv("SYNC_DEVICE_NAME", "").strip() or platform.node() or device_id[:8]
    initial_files = scan_vault_files(root, scope=scope)
    initial_state = _load_state(scope)
    initial_local_changes = _local_changes(initial_files, initial_state, scope)
    result: dict[str, Any] = {
        "scanned": len(initial_files),
        "pushed": 0,
        "pulled": 0,
        "uploaded": 0,
        "downloaded": 0,
        "deleted": 0,
        "conflicts": 0,
        "conflict_paths": [],
    }

    headers = {"X-Sync-Token": token}
    with httpx.Client(base_url=url, headers=headers, timeout=300.0, follow_redirects=False) as client:
        client.get("/v1/health").raise_for_status()
        client.post(
            "/v1/devices/register",
            json={"username": username, "device_id": device_id, "device_name": device_name},
        ).raise_for_status()

        cursor = int(_meta_get(scope, f"cursor:{username}") or 0)
        remote: list[dict[str, Any]] = []
        while True:
            response = client.get(
                "/v1/pull",
                params={"username": username, "cursor": cursor, "limit": 500},
            )
            response.raise_for_status()
            body = response.json()
            remote.extend(body.get("changes", []))
            cursor = int(body.get("cursor", cursor))
            _meta_set(scope, f"cursor:{username}", str(cursor))
            if not body.get("has_more"):
                break

        applied, downloaded, deleted, conflicts, conflict_paths = _apply_remote_batch(
            client, root, scope, remote, initial_local_changes, device_id
        )
        result["pulled"] = applied
        result["downloaded"] = downloaded
        result["deleted"] = deleted
        result["conflicts"] += conflicts
        result["conflict_paths"].extend(conflict_paths)

        files = scan_vault_files(root, scope=scope)
        state = _load_state(scope)
        pending_map = _local_changes(files, state, scope)
        pending = list(pending_map.values())
        result["scanned"] = len(files)
        result["uploaded"] = _upload_files(client, files, pending)

        for start in range(0, len(pending), 100):
            batch = pending[start : start + 100]
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
            for change in accepted:
                payload = change.get("payload") or {}
                _state_upsert(scope, change, local_sha256=str(payload.get("sha256") or ""))
            result["pushed"] += len(accepted)
            server_conflicts = body.get("conflicts", [])
            if server_conflicts:
                current_pending = {
                    (item.get("payload") or {}).get("path", ""): item for item in batch
                }
                applied, downloaded, deleted, count, paths = _apply_remote_batch(
                    client, root, scope, server_conflicts, current_pending, device_id
                )
                result["pulled"] += applied
                result["downloaded"] += downloaded
                result["deleted"] += deleted
                result["conflicts"] += count
                result["conflict_paths"].extend(paths)
    return result


def run_vault_sync(vault_root: str, *, scope: str = DEFAULT_SCOPE, username: str | None = None) -> dict[str, Any]:
    with _STATUS_LOCK:
        if _STATUS["running"]:
            return {"skipped": True, "reason": "already_running"}
        _STATUS["running"] = True
        _STATUS["last_started_at"] = _utc_now()
        _STATUS["last_error"] = None
        _STATUS["vault_root"] = str(Path(vault_root).expanduser().resolve())
    try:
        result = sync_vault_once(vault_root, scope=scope, username=username)
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
