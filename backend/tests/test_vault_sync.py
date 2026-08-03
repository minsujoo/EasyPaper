import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from sync_server_app import create_sync_app


def _make_vault(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / ".obsidian").mkdir()
    return path


def test_vault_scan_includes_notes_and_plugins_but_excludes_machine_state(isolated_dirs):
    from services import vault_sync

    root = _make_vault(Path(isolated_dirs["library_dir"]) / "vault")
    (root / "note.md").write_text("hello", encoding="utf-8")
    plugin = root / ".obsidian" / "plugins" / "example"
    plugin.mkdir(parents=True)
    (plugin / "main.js").write_text("module.exports = {};", encoding="utf-8")
    own_plugin = root / ".obsidian" / "plugins" / "paper-research-workspace"
    own_plugin.mkdir(parents=True)
    (own_plugin / "data.json").write_text('{"backendExecutable":"local"}', encoding="utf-8")
    (root / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")
    cache = root / ".codex-tmp"
    cache.mkdir()
    (cache / "scratch.txt").write_text("temporary", encoding="utf-8")

    files = vault_sync.scan_vault_files(root)

    assert "note.md" in files
    assert ".obsidian/plugins/example/main.js" in files
    assert ".obsidian/plugins/paper-research-workspace/data.json" not in files
    assert ".obsidian/workspace.json" not in files
    assert ".codex-tmp/scratch.txt" not in files


def test_database_record_sync_ignores_vault_file_entities(isolated_dirs):
    from services import sync_client

    sync_client.apply_remote_changes([
        {
            "entity_type": "vault_file",
            "entity_id": "vault-note",
            "version": 1,
            "modified_at": "2026-08-03T01:00:00+00:00",
            "deleted": False,
            "payload": {
                "scope": "primary",
                "path": "note.md",
                "kind": "file",
                "sha256": "0" * 64,
                "size": 1,
                "mtime_ns": 1,
            },
        }
    ])

    with isolated_dirs["db"].get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM local_sync_state WHERE entity_type='vault_file'"
        ).fetchone()[0]
    assert count == 0


def test_whole_vault_round_trip_and_conflict_copy(tmp_path, monkeypatch):
    from services import db, vault_sync

    server = create_sync_app(
        db_path=str(tmp_path / "server.db"),
        storage_dir=str(tmp_path / "server-files"),
        token="vault-test-token",
    )

    def client_factory(**kwargs):
        return TestClient(server, headers=kwargs.get("headers"))

    monkeypatch.setattr(vault_sync.httpx, "Client", client_factory)
    monkeypatch.setenv("SYNC_SERVER_URL", "http://sync.test")
    monkeypatch.setenv("SYNC_TOKEN", "vault-test-token")
    monkeypatch.setenv("SYNC_DEVICE_NAME", "test-device")

    vault_a = _make_vault(tmp_path / "vault-a")
    vault_b = _make_vault(tmp_path / "vault-b")
    (vault_a / "shared.md").write_text("version one", encoding="utf-8")

    db_a = tmp_path / "device-a.db"
    db_b = tmp_path / "device-b.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_a))
    db.init_db()
    first = vault_sync.sync_vault_once(str(vault_a), username="alice")
    assert first["pushed"] == 1

    monkeypatch.setattr(db, "DB_PATH", str(db_b))
    db.init_db()
    second = vault_sync.sync_vault_once(str(vault_b), username="alice")
    assert second["downloaded"] == 1
    assert (vault_b / "shared.md").read_text(encoding="utf-8") == "version one"

    # Device A publishes a newer server winner while B edits its prior copy.
    monkeypatch.setattr(db, "DB_PATH", str(db_a))
    (vault_a / "shared.md").write_text("from device A", encoding="utf-8")
    updated = vault_sync.sync_vault_once(str(vault_a), username="alice")
    assert updated["pushed"] == 1

    monkeypatch.setattr(db, "DB_PATH", str(db_b))
    (vault_b / "shared.md").write_text("from device B", encoding="utf-8")
    conflicted = vault_sync.sync_vault_once(str(vault_b), username="alice")
    assert conflicted["conflicts"] == 1
    assert (vault_b / "shared.md").read_text(encoding="utf-8") == "from device A"
    conflict_files = list(vault_b.glob("shared.sync-conflict-*.md"))
    assert len(conflict_files) == 1
    assert conflict_files[0].read_text(encoding="utf-8") == "from device B"

    digest = hashlib.sha256(b"from device A").hexdigest()
    assert (tmp_path / "server-files" / digest[:2] / digest[2:4] / digest).is_file()
