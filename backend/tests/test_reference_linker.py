"""services/reference_linker.py 테스트.

Semantic Scholar 공개 API는 이 프로젝트 개발 환경에서도 실제로 여러 차례
429 Too Many Requests를 반환할 만큼 레이트리밋이 엄격해서, 실제 네트워크
호출에 의존하는 테스트는 신뢰할 수 없다. httpx 응답을 모킹해 로직만
검증한다.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.reference_linker import resolve_reference


def _make_response(status_code: int, json_data: dict = None):
    request = httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search")
    return httpx.Response(status_code=status_code, json=json_data or {}, request=request)


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_for_empty_query():
    assert await resolve_reference("") is None
    assert await resolve_reference("   ") is None


@pytest.mark.asyncio
async def test_resolve_reference_prefers_arxiv_link_when_available():
    mock_response = _make_response(200, {
        "data": [{
            "title": "Attention Is All You Need",
            "url": "https://www.semanticscholar.org/paper/abc123",
            "externalIds": {"ArXiv": "1706.03762"},
            "year": 2017,
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("Vaswani et al. Attention Is All You Need. 2017.")

    assert result is not None
    assert result["url"] == "https://arxiv.org/abs/1706.03762"
    assert result["title"] == "Attention Is All You Need"
    assert result["year"] == 2017


@pytest.mark.asyncio
async def test_resolve_reference_falls_back_to_semantic_scholar_url_without_arxiv():
    mock_response = _make_response(200, {
        "data": [{
            "title": "Some Paper",
            "url": "https://www.semanticscholar.org/paper/xyz789",
            "externalIds": {},
            "year": 2020,
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("Some Paper query text")

    assert result["url"] == "https://www.semanticscholar.org/paper/xyz789"


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_when_no_results():
    mock_response = _make_response(200, {"data": []})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("a query with no matches")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_reference_retries_on_rate_limit_then_succeeds():
    responses = [
        _make_response(429, {"message": "Too Many Requests"}),
        _make_response(200, {"data": [{"title": "Retried Paper", "url": "https://example.com/p", "externalIds": {}, "year": 2021}]}),
    ]
    mock_get = AsyncMock(side_effect=responses)
    with patch("httpx.AsyncClient.get", new=mock_get):
        with patch("asyncio.sleep", new=AsyncMock()):  # 테스트 속도를 위해 실제 대기는 건너뜀
            result = await resolve_reference("query that gets rate limited once")

    assert result is not None
    assert result["title"] == "Retried Paper"
    assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_resolve_reference_gives_up_after_max_retries_on_persistent_rate_limit():
    mock_get = AsyncMock(return_value=_make_response(429, {"message": "Too Many Requests"}))
    with patch("httpx.AsyncClient.get", new=mock_get):
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await resolve_reference("persistently rate limited query")

    assert result is None
    assert mock_get.call_count == 3  # 최초 시도 + 재시도 2회


@pytest.mark.asyncio
async def test_resolve_reference_handles_network_exception_gracefully():
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ConnectError("연결 실패"))):
        result = await resolve_reference("query during network outage")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_when_no_url_available():
    mock_response = _make_response(200, {"data": [{"title": "No URL Paper", "url": None, "externalIds": {}, "year": 2019}]})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("paper with no url")
    assert result is None
