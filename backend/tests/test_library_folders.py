def _create_doc(isolated_dirs, doc_id="doc-1", username="testuser"):
    isolated_dirs["db"].db_save_document(
        doc_id, username, f"{doc_id}.pdf", f"/tmp/{doc_id}.pdf", 2, {"title": doc_id}
    )


def test_create_list_move_and_unclassify_document(test_client, isolated_dirs):
    _create_doc(isolated_dirs)

    created = test_client.post("/api/library/folders", json={"name": "  3D   Occupancy  "})
    assert created.status_code == 201
    folder = created.json()
    assert folder["name"] == "3D Occupancy"

    moved = test_client.put(f"/api/library/doc-1/folder", json={"folder_id": folder["id"]})
    assert moved.status_code == 200
    assert test_client.get("/api/library/doc-1").json()["folder_id"] == folder["id"]

    folders = test_client.get("/api/library/folders").json()["folders"]
    assert folders[0]["document_count"] == 1

    unclassified = test_client.put("/api/library/doc-1/folder", json={"folder_id": None})
    assert unclassified.status_code == 200
    assert test_client.get("/api/library/doc-1").json()["folder_id"] is None


def test_delete_folder_keeps_documents_and_unassigns_them(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    folder_id = test_client.post("/api/library/folders", json={"name": "Delete me"}).json()["id"]
    test_client.put("/api/library/doc-1/folder", json={"folder_id": folder_id})

    deleted = test_client.delete(f"/api/library/folders/{folder_id}")
    assert deleted.status_code == 200
    doc = test_client.get("/api/library/doc-1").json()
    assert doc["folder_id"] is None
    assert isolated_dirs["db"].db_get_document("doc-1") is not None


def test_folder_names_must_be_unique_per_user(test_client, isolated_dirs):
    assert test_client.post("/api/library/folders", json={"name": "Research"}).status_code == 201
    duplicate = test_client.post("/api/library/folders", json={"name": "Research"})
    assert duplicate.status_code == 409


def test_cannot_use_another_users_folder(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    other = isolated_dirs["db"].db_create_folder("otheruser", "Private")

    moved = test_client.put("/api/library/doc-1/folder", json={"folder_id": other["id"]})
    assert moved.status_code == 404
    assert test_client.put(f"/api/library/folders/{other['id']}", json={"name": "Hijacked"}).status_code == 404
    assert test_client.delete(f"/api/library/folders/{other['id']}").status_code == 404


def test_cannot_move_another_users_document(test_client, isolated_dirs):
    _create_doc(isolated_dirs, "other-doc", "otheruser")
    folder_id = test_client.post("/api/library/folders", json={"name": "Mine"}).json()["id"]
    moved = test_client.put("/api/library/other-doc/folder", json={"folder_id": folder_id})
    assert moved.status_code == 404
