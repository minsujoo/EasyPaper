import hashlib

from fastapi.testclient import TestClient

from sync_server_app import create_sync_app


def _client(tmp_path):
    app = create_sync_app(
        db_path=str(tmp_path / "sync.db"),
        storage_dir=str(tmp_path / "files"),
        token="test-sync-token",
    )
    return TestClient(app), {"X-Sync-Token": "test-sync-token"}


def _push_payload(*, modified_at="2026-08-03T01:00:00+00:00", base_version=0, title="Paper"):
    return {
        "username": "alice",
        "device_id": "device-a",
        "device_name": "Laptop",
        "changes": [
            {
                "entity_type": "document",
                "entity_id": "doc-1",
                "payload": {"id": "doc-1", "title": title},
                "modified_at": modified_at,
                "base_version": base_version,
                "deleted": False,
            }
        ],
    }


def test_sync_server_requires_token_and_round_trips_changes(tmp_path):
    client, headers = _client(tmp_path)
    assert client.get("/v1/health").status_code == 200
    assert client.post("/v1/push", json=_push_payload()).status_code == 401

    pushed = client.post("/v1/push", headers=headers, json=_push_payload())
    assert pushed.status_code == 200
    assert pushed.json()["accepted"][0]["version"] == 1

    pulled = client.get("/v1/pull", headers=headers, params={"username": "alice", "cursor": 0})
    assert pulled.status_code == 200
    assert pulled.json()["changes"][0]["payload"]["title"] == "Paper"
    assert pulled.json()["cursor"] == 1


def test_stale_first_sync_preserves_server_winner_and_records_conflict(tmp_path):
    client, headers = _client(tmp_path)
    client.post("/v1/push", headers=headers, json=_push_payload()).raise_for_status()

    stale = _push_payload(
        modified_at="2026-08-02T01:00:00+00:00",
        base_version=0,
        title="Older local copy",
    )
    stale["device_id"] = "device-b"
    response = client.post("/v1/push", headers=headers, json=stale)
    assert response.status_code == 200
    assert response.json()["accepted"] == []
    assert response.json()["conflicts"][0]["payload"]["title"] == "Paper"
    assert client.get("/v1/status", headers=headers).json()["conflicts"] == 1


def test_tombstone_retains_natural_key_payload(tmp_path):
    client, headers = _client(tmp_path)
    initial = _push_payload()
    first = client.post("/v1/push", headers=headers, json=initial).json()["accepted"][0]
    deleted = _push_payload(modified_at="2026-08-03T02:00:00+00:00", base_version=first["version"])
    deleted["changes"][0]["payload"] = {}
    deleted["changes"][0]["deleted"] = True
    item = client.post("/v1/push", headers=headers, json=deleted).json()["accepted"][0]
    assert item["deleted"] is True
    assert item["payload"]["id"] == "doc-1"


def test_vault_file_version_mismatch_always_preserves_server_winner(tmp_path):
    client, headers = _client(tmp_path)
    initial = _push_payload(title="first")
    initial["capabilities"] = ["vault-files-v1"]
    initial["changes"][0].update({
        "entity_type": "vault_file",
        "entity_id": "vault-note",
        "payload": {"scope": "primary", "path": "note.md", "sha256": "1" * 64},
    })
    first = client.post("/v1/push", headers=headers, json=initial).json()["accepted"][0]

    concurrent = _push_payload(
        modified_at="2026-08-04T01:00:00+00:00",
        base_version=0,
        title="ignored",
    )
    concurrent["device_id"] = "device-b"
    concurrent["capabilities"] = ["vault-files-v1"]
    concurrent["changes"][0].update({
        "entity_type": "vault_file",
        "entity_id": "vault-note",
        "payload": {"scope": "primary", "path": "note.md", "sha256": "2" * 64},
    })
    result = client.post("/v1/push", headers=headers, json=concurrent).json()
    assert result["accepted"] == []
    assert result["conflicts"][0]["version"] == first["version"]
    assert result["conflicts"][0]["payload"]["sha256"] == "1" * 64


def test_legacy_client_cannot_push_vault_tombstones(tmp_path):
    client, headers = _client(tmp_path)
    legacy = _push_payload()
    legacy["changes"][0].update({
        "entity_type": "vault_file",
        "entity_id": "vault-note",
        "deleted": True,
        "payload": {"scope": "primary", "path": "note.md", "sha256": "1" * 64},
    })
    response = client.post("/v1/push", headers=headers, json=legacy)
    assert response.status_code == 409
    assert client.get("/v1/status", headers=headers).json()["records"] == 0


def test_content_addressed_file_upload_validates_hash(tmp_path):
    client, headers = _client(tmp_path)
    body = b"%PDF-test-sync"
    digest = hashlib.sha256(body).hexdigest()
    assert client.head(f"/v1/files/{digest}", headers=headers).status_code == 404
    uploaded = client.put(
        f"/v1/files/{digest}",
        headers=headers,
        params={"filename": "paper.pdf"},
        content=body,
    )
    assert uploaded.status_code == 200
    assert client.head(f"/v1/files/{digest}", headers=headers).status_code == 200
    assert client.get(f"/v1/files/{digest}", headers=headers).content == body
    assert client.get(f"/v1/files/{digest}", headers=headers).headers["content-type"].startswith(
        "application/octet-stream"
    )

    bad = client.put("/v1/files/" + "0" * 64, headers=headers, content=body)
    assert bad.status_code == 400
