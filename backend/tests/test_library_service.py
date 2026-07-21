"""services/library.py 테스트 - 특히 문서 영구삭제 시 관련 파일이 전부
정리되는지(지난 세션 버그 수정)를 확인한다."""

import os


def test_permanently_delete_document_cleans_up_all_locations(isolated_dirs):
    db = isolated_dirs["db"]
    cache = isolated_dirs["cache"]
    library = isolated_dirs["library"]
    doc_id = "doc-cleanup-test"

    upload_dir = os.path.join(str(isolated_dirs["upload_dir"]), doc_id)
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, "document.pdf"), "w") as f:
        f.write("original upload")

    lib_dir = os.path.join(str(isolated_dirs["library_dir"]), doc_id)
    os.makedirs(lib_dir, exist_ok=True)
    with open(os.path.join(lib_dir, "document.pdf"), "w") as f:
        f.write("library copy")

    with open(os.path.join(str(isolated_dirs["cache_dir"]), f"{doc_id}_page_1.json"), "w") as f:
        f.write("{}")
    with open(os.path.join(str(isolated_dirs["cache_dir"]), f"{doc_id}_page_2.json"), "w") as f:
        f.write("{}")

    db.db_save_document(doc_id, "admin", "test.pdf", "/x", 1, {})

    # 삭제 전 사전 조건 확인
    assert os.path.exists(upload_dir)
    assert os.path.exists(lib_dir)
    assert len([f for f in os.listdir(str(isolated_dirs["cache_dir"])) if f.startswith(doc_id)]) == 2
    assert db.db_get_document(doc_id) is not None

    result = library.permanently_delete_document(doc_id)

    assert result is True
    assert not os.path.exists(upload_dir), "업로드 원본 디렉터리가 삭제되어야 한다"
    assert not os.path.exists(lib_dir), "라이브러리 보관 디렉터리가 삭제되어야 한다"
    assert len([f for f in os.listdir(str(isolated_dirs["cache_dir"])) if f.startswith(doc_id)]) == 0, \
        "페이지별 번역 캐시 파일이 모두 삭제되어야 한다"
    assert db.db_get_document(doc_id) is None, "DB 레코드도 삭제되어야 한다"


def test_permanently_delete_nonexistent_document_returns_false(isolated_dirs):
    library = isolated_dirs["library"]
    assert library.permanently_delete_document("no-such-doc") is False
