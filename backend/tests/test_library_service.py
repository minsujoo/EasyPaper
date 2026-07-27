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


# list_documents()/get_document()의 translated_pages 계산은 예전에 문서마다
# 새 SQLite 커넥션을 열어 최대 3개의 쿼리를 던지는 N+1 패턴이었다(성능
# 문제). 여러 문서의 번역 행을 한 번에 모아 메모리에서 매칭하도록
# 바꿨는데, 이 리팩터링이 기존 suffix-fallback 규칙과 문서별 격리를 그대로
# 유지하는지가 핵심 리스크라 아래에서 확인한다.

def test_list_documents_returns_pages_for_matching_suffix(isolated_dirs):
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})
    db.db_save_translation("doc-1", 1, "번역1", suffix="ko_formal")
    db.db_save_translation("doc-1", 2, "번역2", suffix="ko_formal")

    docs = library.list_documents("admin", target_lang="ko", style="formal", ignore_math=False, ignore_table=False, ignore_refs=False)
    assert docs[0]["translated_pages"] == [1, 2]


def test_list_documents_falls_back_to_most_recent_suffix_when_no_match(isolated_dirs):
    """요청한 suffix(예: 새 target_lang/style)로 번역된 페이지가 없으면,
    가장 최근에 저장된 다른 suffix의 번역 페이지를 대신 보여줘야 한다."""
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})
    db.db_save_translation("doc-1", 1, "old", suffix="en_casual")

    docs = library.list_documents("admin", target_lang="ko", style="formal", ignore_math=False, ignore_table=False, ignore_refs=False)
    assert docs[0]["translated_pages"] == [1]


def test_list_documents_isolates_translated_pages_per_document(isolated_dirs):
    """여러 문서의 번역 행을 한 번의 쿼리로 모아오는 방식이라, 다른 문서의
    페이지가 서로 뒤섞이지 않아야 한다."""
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})
    db.db_save_document("doc-2", "admin", "p2.pdf", "/x", 3, {})
    db.db_save_translation("doc-1", 1, "d1p1")
    db.db_save_translation("doc-1", 2, "d1p2")
    db.db_save_translation("doc-2", 5, "d2p5")

    docs = library.list_documents("admin")
    by_id = {d["id"]: d["translated_pages"] for d in docs}
    assert by_id["doc-1"] == [1, 2]
    assert by_id["doc-2"] == [5]


def test_list_documents_with_no_translations_returns_empty_pages(isolated_dirs):
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})

    docs = library.list_documents("admin")
    assert docs[0]["translated_pages"] == []


def test_get_document_returns_pages_for_matching_suffix(isolated_dirs):
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})
    db.db_save_translation("doc-1", 1, "번역1", suffix="ko_formal")

    doc = library.get_document("doc-1", target_lang="ko", style="formal", ignore_math=False, ignore_table=False, ignore_refs=False)
    assert doc["translated_pages"] == [1]


def test_get_document_falls_back_to_most_recent_suffix_when_no_match(isolated_dirs):
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})
    db.db_save_translation("doc-1", 1, "old", suffix="en_casual")

    doc = library.get_document("doc-1", target_lang="ko", style="formal", ignore_math=False, ignore_table=False, ignore_refs=False)
    assert doc["translated_pages"] == [1]


def test_get_document_without_suffix_uses_most_recent_translation(isolated_dirs):
    library = isolated_dirs["library"]
    db = isolated_dirs["db"]
    db.db_save_document("doc-1", "admin", "p1.pdf", "/x", 3, {})
    db.db_save_translation("doc-1", 1, "번역1")

    doc = library.get_document("doc-1")
    assert doc["translated_pages"] == [1]
