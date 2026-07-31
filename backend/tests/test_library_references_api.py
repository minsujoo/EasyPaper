"""GET /library/{doc_id}/references, GET /library/{doc_id}/references/{ref_num}
HTTP 레벨 테스트 - 캐싱, 소유자 격리, 외부 API 미매칭 처리를 확인한다.
"""

import json
from unittest.mock import AsyncMock, patch
import httpx


def _create_doc(isolated_dirs, doc_id="doc-1", username="testuser"):
    db = isolated_dirs["db"]
    db.db_save_document(doc_id, username, "paper.pdf", "/x/paper.pdf", 3, {"title": "Test Paper"})


def test_get_references_parses_and_caches(test_client, isolated_dirs):
    _create_doc(isolated_dirs)

    fake_pages = [{"page_num": 1, "text": "References\n\n[1] Someone. A Paper. 2020."}]
    with patch("services.pdf_parser.extract_pages", return_value=fake_pages), \
         patch("routers.library.get_pdf_path", return_value="/fake/path.pdf"):
        res = test_client.get("/api/library/doc-1/references")

    assert res.status_code == 200
    assert res.json()["references"] == {"1": "Someone. A Paper. 2020."}
    assert "mentions" in res.json()

    # 두 번째 호출은 캐시를 써야 하므로 extract_pages가 다시 호출되지 않아야 한다
    with patch("services.pdf_parser.extract_pages") as mock_extract:
        res2 = test_client.get("/api/library/doc-1/references")
    assert res2.status_code == 200
    assert res2.json()["references"] == {"1": "Someone. A Paper. 2020."}
    assert "mentions" in res2.json()
    mock_extract.assert_not_called()


def test_get_references_other_users_document_returns_404(test_client, isolated_dirs):
    _create_doc(isolated_dirs, doc_id="doc-other", username="otheruser")
    res = test_client.get("/api/library/doc-other/references")
    assert res.status_code == 404


def test_resolve_reference_returns_url_when_matched(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    isolated_dirs["db"].db_save_page_insight(
        "doc-1", 0, "reference_list", json.dumps({"1": "Vaswani et al. Attention Is All You Need."})
    )

    mock_result = {"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "year": 2017}
    with patch("services.reference_linker.resolve_reference", new=AsyncMock(return_value=mock_result)):
        res = test_client.get("/api/library/doc-1/references/1")

    assert res.status_code == 200
    assert res.json()["url"] == "https://arxiv.org/abs/1706.03762"


def test_resolve_reference_caches_result(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    isolated_dirs["db"].db_save_page_insight(
        "doc-1", 0, "reference_list", json.dumps({"1": "Some paper title."})
    )

    mock_result = {"title": "Some paper title.", "url": "https://example.com/paper", "year": 2020}
    mock_resolve = AsyncMock(return_value=mock_result)
    with patch("services.reference_linker.resolve_reference", new=mock_resolve):
        test_client.get("/api/library/doc-1/references/1")

    # 두 번째 호출은 캐시를 써야 하므로 resolve_reference가 다시 호출되지 않아야 한다
    with patch("services.reference_linker.resolve_reference", new=AsyncMock()) as mock_resolve_2:
        res2 = test_client.get("/api/library/doc-1/references/1")
    assert res2.status_code == 200
    mock_resolve_2.assert_not_called()


def test_resolve_reference_returns_404_when_no_match_found(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    isolated_dirs["db"].db_save_page_insight(
        "doc-1", 0, "reference_list", json.dumps({"1": "unmatchable nonsense query"})
    )

    with patch("services.reference_linker.resolve_reference", new=AsyncMock(return_value=None)):
        res = test_client.get("/api/library/doc-1/references/1")

    assert res.status_code == 404


def test_resolve_reference_returns_404_when_ref_num_unknown(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    isolated_dirs["db"].db_save_page_insight(
        "doc-1", 0, "reference_list", json.dumps({"1": "the only reference"})
    )
    res = test_client.get("/api/library/doc-1/references/999")
    assert res.status_code == 404


def test_resolve_reference_other_users_document_returns_404(test_client, isolated_dirs):
    _create_doc(isolated_dirs, doc_id="doc-other2", username="otheruser")
    res = test_client.get("/api/library/doc-other2/references/1")
    assert res.status_code == 404


def test_download_reference_returns_open_access_pdf(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    resolved = {
        "title": "Attention Is All You Need",
        "url": "https://arxiv.org/abs/1706.03762",
        "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
    }
    pdf_response = httpx.Response(
        200,
        content=b"%PDF-1.7 fake citation paper",
        request=httpx.Request("GET", resolved["pdf_url"]),
    )
    public_address = [(2, 1, 6, "", ("151.101.1.42", 443))]

    with patch("routers.library._get_resolved_reference", new=AsyncMock(return_value=resolved)), \
         patch("socket.getaddrinfo", return_value=public_address), \
         patch("httpx.AsyncClient.get", new=AsyncMock(return_value=pdf_response)):
        res = test_client.get("/api/library/doc-1/references/1/download")

    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]


def test_reference_insight_returns_and_caches_llm_analysis(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    isolated_dirs["db"].db_save_page_insight(
        "doc-1", 0, "reference_list",
        json.dumps({"1": "Vaswani et al. Attention Is All You Need."}),
    )
    resolved = {
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani"],
        "year": 2017,
        "venue": "NeurIPS",
        "abstract": "A transformer architecture.",
    }

    async def fake_stream_chat(*args, **kwargs):
        yield "## 이 논문이 인용된 이유\nTransformer의 근거입니다.\n"
        yield "## 인용 논문 개요\nSelf-attention 모델을 제안합니다."

    with patch("routers.library._get_resolved_reference", new=AsyncMock(return_value=resolved)), \
         patch("services.llm_client.stream_chat", new=fake_stream_chat):
        res = test_client.post(
            "/api/library/doc-1/references/1/insight",
            json={"surrounding_context": "We build on the Transformer [1]."},
        )

    assert res.status_code == 200
    assert "이 논문이 인용된 이유" in res.json()["content"]
    assert "인용 논문 개요" in res.json()["content"]

    with patch("services.llm_client.stream_chat") as stream_again:
        cached = test_client.post(
            "/api/library/doc-1/references/1/insight",
            json={"surrounding_context": "We build on the Transformer [1]."},
        )
    assert cached.status_code == 200
    stream_again.assert_not_called()
