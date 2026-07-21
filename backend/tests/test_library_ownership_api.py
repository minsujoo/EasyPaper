"""라이브러리 엔드포인트의 문서 소유자 확인(IDOR 수정) 회귀 테스트.

get_current_user를 "testuser"로 고정하는 test_client fixture 위에서, 다른
사용자("otheruser") 소유의 문서에 접근을 시도해 전부 404로 막히는지 확인한다.
"""


def _create_doc_owned_by(isolated_dirs, doc_id: str, username: str):
    db = isolated_dirs["db"]
    db.db_save_document(doc_id, username, "paper.pdf", "/x/paper.pdf", 3, {"title": "Other's Paper"})


def test_get_document_owned_by_other_user_returns_404(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-other-1", "otheruser")
    res = test_client.get("/api/library/doc-other-1")
    assert res.status_code == 404


def test_get_own_document_succeeds(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-mine-1", "testuser")
    res = test_client.get("/api/library/doc-mine-1")
    assert res.status_code == 200
    assert res.json()["id"] == "doc-mine-1"


def test_delete_document_owned_by_other_user_returns_404(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-other-2", "otheruser")
    res = test_client.delete("/api/library/doc-other-2")
    assert res.status_code == 404

    # 실제로 지워지지 않았어야 한다
    db = isolated_dirs["db"]
    assert db.db_get_document("doc-other-2") is not None


def test_permanently_delete_document_owned_by_other_user_returns_404(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-other-3", "otheruser")
    res = test_client.delete("/api/library/doc-other-3/permanent")
    assert res.status_code == 404

    db = isolated_dirs["db"]
    assert db.db_get_document("doc-other-3") is not None, "다른 사용자 문서는 영구삭제도 막혀야 한다"


def test_restore_document_owned_by_other_user_returns_404(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-other-4", "otheruser")
    isolated_dirs["db"].db_soft_delete_document("doc-other-4")
    res = test_client.post("/api/library/doc-other-4/restore")
    assert res.status_code == 404


def test_update_metadata_owned_by_other_user_returns_404(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-other-5", "otheruser")
    res = test_client.put("/api/library/doc-other-5/metadata", json={"title": "Hijacked"})
    assert res.status_code == 404


def test_update_metadata_owned_by_self_succeeds(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-mine-2", "testuser")
    res = test_client.put("/api/library/doc-mine-2/metadata", json={"title": "My New Title"})
    assert res.status_code == 200
    assert res.json()["metadata"]["title"] == "My New Title"


def test_library_list_only_returns_own_documents(test_client, isolated_dirs):
    _create_doc_owned_by(isolated_dirs, "doc-mine-3", "testuser")
    _create_doc_owned_by(isolated_dirs, "doc-other-6", "otheruser")

    res = test_client.get("/api/library")
    assert res.status_code == 200
    ids = {d["id"] for d in res.json()["documents"]}
    assert ids == {"doc-mine-3"}
