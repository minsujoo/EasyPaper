#!/usr/bin/env python3
"""Restore Vault files retained by central sync tombstones.

This is an offline recovery tool: stop the sync server and device clients
first. It never mutates the central database. Without --apply it only reports
what would be restored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path, PurePosixPath

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.vault_sync import _is_excluded, _normalize_relative_path  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="central sync SQLite database")
    parser.add_argument("--storage", required=True, help="central content-addressed file directory")
    parser.add_argument("--vault", required=True, help="Vault directory to restore")
    parser.add_argument("--origin-device", default="", help="only tombstones written by this device")
    parser.add_argument("--apply", action="store_true", help="write files; default is dry-run")
    args = parser.parse_args()

    database = Path(args.db).expanduser().resolve()
    storage = Path(args.storage).expanduser().resolve()
    vault = Path(args.vault).expanduser().resolve()
    if not database.is_file() or not storage.is_dir() or not (vault / ".obsidian").is_dir():
        parser.error("database, storage, or Vault path is invalid")

    query = """
        SELECT payload_json, origin_device
        FROM sync_records
        WHERE entity_type='vault_file' AND deleted=1
    """
    params: tuple[str, ...] = ()
    if args.origin_device:
        query += " AND origin_device=?"
        params = (args.origin_device,)

    counts = {"candidates": 0, "restored": 0, "already_ok": 0, "missing_blob": 0, "skipped": 0, "preserved": 0}
    with sqlite3.connect(database) as conn:
        rows = conn.execute(query, params).fetchall()
    for payload_json, _origin_device in rows:
        payload = json.loads(payload_json)
        try:
            relative = _normalize_relative_path(payload.get("path", ""))
        except ValueError:
            counts["skipped"] += 1
            continue
        if _is_excluded(relative):
            counts["skipped"] += 1
            continue
        sha256 = str(payload.get("sha256") or "")
        if len(sha256) != 64:
            counts["skipped"] += 1
            continue
        blob = storage / sha256[:2] / sha256[2:4] / sha256
        if not blob.is_file() or _sha256(blob) != sha256:
            counts["missing_blob"] += 1
            continue
        counts["candidates"] += 1
        target = (vault / PurePosixPath(relative)).resolve()
        if vault not in target.parents:
            counts["skipped"] += 1
            continue
        if target.is_file() and _sha256(target) == sha256:
            counts["already_ok"] += 1
            continue
        if not args.apply:
            counts["restored"] += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            preserved = target.with_name(f"{target.name}.recovery-conflict")
            counter = 2
            while preserved.exists():
                preserved = target.with_name(f"{target.name}.recovery-conflict-{counter}")
                counter += 1
            shutil.copy2(target, preserved)
            counts["preserved"] += 1
        temporary = target.with_name(f".{target.name}.vault-recovery-tmp")
        shutil.copy2(blob, temporary)
        os.replace(temporary, target)
        mtime_ns = int(payload.get("mtime_ns") or 0)
        if mtime_ns > 0:
            os.utime(target, ns=(mtime_ns, mtime_ns))
        counts["restored"] += 1

    print(json.dumps({"dry_run": not args.apply, **counts}, ensure_ascii=False))
    return 1 if counts["missing_blob"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
