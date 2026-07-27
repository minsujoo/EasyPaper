"""POST /settings/clear-pages-cache 테스트 - 설정 화면에서 PDF 텍스트 추출
디스크 캐시를 정리하는 엔드포인트."""

from services.cache import save_pages_cache


def test_clear_pages_cache_removes_files_and_reports_stats(test_client, isolated_dirs, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake" + b"\0" * 300)
    save_pages_cache("doc-1", str(pdf_path), [{"page_num": 1, "text": "a"}])
    save_pages_cache("doc-2", str(pdf_path), [{"page_num": 1, "text": "b"}])

    res = test_client.post("/api/settings/clear-pages-cache")

    assert res.status_code == 200
    body = res.json()
    assert body["cleared_files"] == 2
    assert body["freed_bytes"] > 0

    from services.cache import get_cached_pages
    assert get_cached_pages("doc-1", str(pdf_path)) is None


def test_clear_pages_cache_is_a_no_op_when_nothing_cached(test_client, isolated_dirs):
    res = test_client.post("/api/settings/clear-pages-cache")
    assert res.status_code == 200
    assert res.json() == {"cleared_files": 0, "freed_bytes": 0}
