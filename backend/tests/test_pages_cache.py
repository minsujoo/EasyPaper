"""services/cache.py의 페이지 추출 결과 디스크 캐시(get_cached_pages /
save_pages_cache / clear_all_pages_cache) 테스트.

서버(또는 Tauri 앱 백엔드) 재시작 후 문서를 다시 열 때 PDF를 매번
재파싱하지 않도록, extract_pages() 결과를 디스크에 캐싱해두는 기능이다."""

import json
import os

from services.cache import get_cached_pages, save_pages_cache, clear_all_pages_cache


def _make_pdf(path, size_bytes=200):
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4 fake" + b"\0" * size_bytes)


def test_cache_miss_when_never_saved(isolated_dirs, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path)
    assert get_cached_pages("doc-1", str(pdf_path)) is None


def test_saved_pages_are_returned_on_cache_hit(isolated_dirs, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path)
    pages = [{"page_num": 1, "text": "hello"}]

    save_pages_cache("doc-1", str(pdf_path), pages)

    assert get_cached_pages("doc-1", str(pdf_path)) == pages


def test_cache_invalidated_when_pdf_content_changes(isolated_dirs, tmp_path):
    """PDF가 (같은 경로에서) 다른 내용으로 교체되면(mtime/크기 변경),
    오래된 캐시를 그대로 신뢰하지 않고 재추출을 유도해야 한다."""
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path, size_bytes=200)
    save_pages_cache("doc-1", str(pdf_path), [{"page_num": 1, "text": "old"}])

    # 파일 크기와 mtime이 모두 바뀌도록 다시 씀
    import time
    time.sleep(0.01)
    _make_pdf(pdf_path, size_bytes=500)

    assert get_cached_pages("doc-1", str(pdf_path)) is None


def test_cache_invalidated_when_parser_version_changes(isolated_dirs, tmp_path):
    """PDF 파일이 그대로여도 추출 파서 규칙이 바뀐 릴리스에서는 이전 캐시를
    재사용하지 않아야 한다."""
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path)
    save_pages_cache("doc-1", str(pdf_path), [{"page_num": 1, "text": "old parser"}])

    cache_path = isolated_dirs["cache_dir"] / "doc-1_pages_extract.json"
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    data["version"] = 1
    cache_path.write_text(json.dumps(data), encoding="utf-8")

    assert get_cached_pages("doc-1", str(pdf_path)) is None


def test_clear_all_pages_cache_removes_only_pages_cache_files(isolated_dirs, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path)
    save_pages_cache("doc-1", str(pdf_path), [{"page_num": 1, "text": "a"}])
    save_pages_cache("doc-2", str(pdf_path), [{"page_num": 1, "text": "b"}])

    # 다른 종류의 캐시 파일(번역 캐시)은 영향을 받으면 안 됨
    from services.cache import save_translation_cache
    save_translation_cache("doc-1", 1, "번역 결과")

    count, freed_bytes = clear_all_pages_cache()

    assert count == 2
    assert freed_bytes > 0
    assert get_cached_pages("doc-1", str(pdf_path)) is None
    assert get_cached_pages("doc-2", str(pdf_path)) is None

    from services.cache import get_cached_translation
    assert get_cached_translation("doc-1", 1) == "번역 결과", "번역 캐시는 지워지면 안 됨"
