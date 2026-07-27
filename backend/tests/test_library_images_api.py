"""GET /library/{doc_id}/images HTTP 레벨 테스트.

extract_pdf_images()는 페이지마다 find_tables()/get_drawings() 등을 두
차례씩 훑는 무거운 연산이다. 텍스트 추출(extract_pages)과 달리 예전에는
디스크 캐시가 전혀 없어 문서를 열 때마다(같은 세션 안에서도) 매번
처음부터 재계산했다 - 이 테스트는 그 디스크 캐시가 실제로 재계산을
막아주는지 확인한다."""

from unittest.mock import patch


def _create_doc(isolated_dirs, doc_id="doc-1", username="testuser"):
    db = isolated_dirs["db"]
    db.db_save_document(doc_id, username, "paper.pdf", "/x/paper.pdf", 3, {"title": "Test Paper"})


def test_get_images_extracts_and_caches(test_client, isolated_dirs, tmp_path):
    _create_doc(isolated_dirs)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake" + b"\0" * 200)

    fake_images = [{"page": 1, "bbox": [0.1, 0.1, 0.5, 0.5], "label": "Figure 1"}]
    with patch("services.pdf_parser.extract_pdf_images", return_value=fake_images) as mock_extract, \
         patch("routers.library.get_pdf_path", return_value=str(pdf_path)):
        res = test_client.get("/api/library/doc-1/images")

    assert res.status_code == 200
    assert res.json()["images"] == fake_images
    mock_extract.assert_called_once()

    # 두 번째 호출은 디스크 캐시를 써야 하므로 extract_pdf_images가 다시 호출되지 않아야 한다
    with patch("services.pdf_parser.extract_pdf_images") as mock_extract2, \
         patch("routers.library.get_pdf_path", return_value=str(pdf_path)):
        res2 = test_client.get("/api/library/doc-1/images")

    assert res2.status_code == 200
    assert res2.json()["images"] == fake_images
    mock_extract2.assert_not_called()


def test_get_images_other_users_document_returns_404(test_client, isolated_dirs):
    _create_doc(isolated_dirs, doc_id="doc-other", username="otheruser")
    res = test_client.get("/api/library/doc-other/images")
    assert res.status_code == 404
