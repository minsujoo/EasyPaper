def _create_doc(isolated_dirs, doc_id="reading-doc", username="testuser", total_pages=10, metadata=None):
    isolated_dirs["db"].db_save_document(
        doc_id,
        username,
        f"{doc_id}.pdf",
        f"/tmp/{doc_id}.pdf",
        total_pages,
        metadata or {"title": "Reading Paper"},
    )


def test_reading_progress_tracks_last_and_furthest_page(test_client, isolated_dirs):
    _create_doc(isolated_dirs)

    first = test_client.post(
        "/api/library/reading-doc/reading-progress",
        json={"page": 7, "total_pages": 10, "activity_date": "2026-08-01"},
    )
    assert first.status_code == 200

    second = test_client.post(
        "/api/library/reading-doc/reading-progress",
        json={"page": 4, "total_pages": 10, "activity_date": "2026-08-01"},
    )
    assert second.status_code == 200

    history = test_client.get("/api/library/reading-history?year=2026&month=8")
    assert history.status_code == 200
    data = history.json()
    assert data["active_days"] == 1
    assert data["paper_count"] == 1
    assert data["activities"][0]["last_page"] == 4
    assert data["activities"][0]["furthest_page"] == 7
    assert isolated_dirs["db"].db_get_document("reading-doc")["metadata"]["last_page"] == 4


def test_history_can_record_activity_without_remembering_position(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    response = test_client.post(
        "/api/library/reading-doc/reading-progress",
        json={
            "page": 3,
            "total_pages": 10,
            "activity_date": "2026-08-02",
            "remember_position": False,
        },
    )
    assert response.status_code == 200
    metadata = isolated_dirs["db"].db_get_document("reading-doc")["metadata"]
    assert "last_page" not in metadata
    assert test_client.get("/api/library/reading-history?year=2026&month=8").json()["paper_count"] == 1


def test_legacy_completed_paper_appears_in_calendar(test_client, isolated_dirs):
    _create_doc(
        isolated_dirs,
        metadata={"title": "Legacy Paper", "read": True, "read_at": "2026-08-03T12:00:00+00:00"},
    )
    activities = test_client.get("/api/library/reading-history?year=2026&month=8").json()["activities"]
    assert activities[0]["activity_date"] == "2026-08-03"
    assert activities[0]["completed"] is True


def test_cannot_record_progress_for_another_users_document(test_client, isolated_dirs):
    _create_doc(isolated_dirs, username="otheruser")
    response = test_client.post(
        "/api/library/reading-doc/reading-progress",
        json={"page": 1, "total_pages": 10, "activity_date": "2026-08-01"},
    )
    assert response.status_code == 404
