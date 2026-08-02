import json
from pathlib import Path


def test_portable_snapshot_excludes_absolute_pdf_path_and_device_anki_state(isolated_dirs):
    from services import sync_client

    db = isolated_dirs["db"]
    pdf = Path(isolated_dirs["library_dir"]) / "paper.pdf"
    pdf.write_bytes(b"%PDF-portable")
    db.db_save_document("doc-1", "testuser", "paper.pdf", str(pdf), 3, {"title": "Paper"})
    db.db_upsert_vocabulary_card("testuser", {
        "doc_id": "doc-1", "page_num": 1, "term": "occupancy",
        "meaning_ko": "점유", "context_en": "occupancy", "context_ko": "점유",
        "paper_title": "Paper",
    })

    records = sync_client.scan_local_records()
    document = next(item for item in records if item.entity_type == "document")
    card = next(item for item in records if item.entity_type == "vocabulary_card")
    assert "pdf_path" not in document.payload
    assert len(document.payload["file_sha256"]) == 64
    assert "anki_note_id" not in card.payload
    assert "anki_status" not in card.payload


def test_remote_records_apply_in_dependency_order_and_do_not_echo(isolated_dirs, monkeypatch):
    from services import sync_client
    import config

    monkeypatch.setattr(config, "LIBRARY_DIR", str(isolated_dirs["library_dir"]))
    changes = [
        {
            "entity_type": "reading_activity",
            "entity_id": sync_client._stable_id("testuser", "doc-1", "2026-08-03"), "version": 1,
            "modified_at": "2026-08-03T03:00:00+00:00", "deleted": False,
            "payload": {
                "username": "testuser", "doc_id": "doc-1", "activity_date": "2026-08-03",
                "first_opened_at": "2026-08-03T02:00:00+00:00",
                "last_read_at": "2026-08-03T03:00:00+00:00",
                "last_page": 2, "furthest_page": 4, "total_pages": 10,
            },
        },
        {
            "entity_type": "document", "entity_id": "doc-1", "version": 1,
            "modified_at": "2026-08-03T01:00:00+00:00", "deleted": False,
            "payload": {
                "id": "doc-1", "username": "testuser", "filename": "paper.pdf",
                "total_pages": 10, "metadata": json.dumps({"title": "Paper"}),
                "created_at": "2026-08-03T01:00:00+00:00", "is_deleted": 0,
                "folder_name": "Occupancy", "file_sha256": None,
            },
        },
        {
            "entity_type": "library_folder",
            "entity_id": sync_client._stable_id("testuser", "Occupancy"), "version": 1,
            "modified_at": "2026-08-03T00:00:00+00:00", "deleted": False,
            "payload": {
                "username": "testuser", "name": "Occupancy",
                "created_at": "2026-08-03T00:00:00+00:00",
            },
        },
    ]
    sync_client.apply_remote_changes(changes)

    document = isolated_dirs["db"].db_get_document("doc-1")
    assert document["metadata"]["title"] == "Paper"
    assert document["folder_id"] is not None
    assert isolated_dirs["db"].db_list_reading_activity("testuser", 2026, 8)[0]["last_page"] == 2
    assert sync_client.collect_local_changes() == []


def test_remote_tombstone_deletes_natural_key_record(isolated_dirs):
    from services import sync_client

    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "testuser", "paper.pdf", "/tmp/paper.pdf", 1, {})
    db.db_save_translation("doc-1", 1, "translated", suffix="ko")
    payload = {
        "doc_id": "doc-1", "page_num": 1, "suffix": "ko",
        "translation": "translated", "saved_at": "2026-08-03T01:00:00+00:00",
    }
    sync_client.apply_remote_changes([{
        "entity_type": "translation", "entity_id": "translation-1", "version": 2,
        "modified_at": "2026-08-03T02:00:00+00:00", "deleted": True, "payload": payload,
    }])
    assert db.db_get_translation("doc-1", 1, suffix="ko") is None
