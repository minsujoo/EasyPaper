"""OpenAlex/Crossref/arXiv 복수 후보 검색과 검증 로직 테스트."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.reference_linker import resolve_reference, _extract_search_query


def _make_response(status_code: int, json_data: dict = None):
    request = httpx.Request("GET", "https://api.openalex.org/works")
    return httpx.Response(status_code=status_code, json=json_data or {}, request=request)


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_for_empty_query():
    assert await resolve_reference("") is None
    assert await resolve_reference("   ") is None


@pytest.mark.asyncio
async def test_resolve_reference_prefers_arxiv_link_when_available():
    mock_response = _make_response(200, {
        "results": [{
            "title": "Attention Is All You Need",
            "publication_year": 2017,
            "open_access": {"oa_url": "https://example.com/oa.pdf"},
            "primary_location": {"landing_page_url": "https://example.com/primary"},
            "doi": "https://doi.org/10.0000/xyz",
            "locations": [
                {"source": {"display_name": "NeurIPS"}, "landing_page_url": "https://example.com/neurips"},
                {
                    "source": {"display_name": "arXiv (Cornell University)"},
                    "landing_page_url": "https://arxiv.org/abs/1706.03762",
                    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
                    "is_oa": True,
                },
            ],
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference('Vaswani et al. "Attention Is All You Need." 2017.')

    assert result is not None
    assert result["url"] == "https://arxiv.org/abs/1706.03762"
    assert result["pdf_url"] == "https://arxiv.org/pdf/1706.03762.pdf"
    assert result["title"] == "Attention Is All You Need"
    assert result["year"] == 2017


@pytest.mark.asyncio
async def test_resolve_reference_falls_back_through_open_access_then_primary_then_doi():
    # open_access.oa_url 우선
    mock_response = _make_response(200, {
        "results": [{
            "title": "Some Paper",
            "publication_year": 2020,
            "open_access": {"oa_url": "https://example.com/oa.pdf"},
            "primary_location": {"landing_page_url": "https://example.com/primary"},
            "doi": "https://doi.org/10.0000/xyz",
            "locations": [],
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("Some Paper")
    assert result["url"] == "https://example.com/oa.pdf"

    # open_access 없으면 primary_location
    mock_response = _make_response(200, {
        "results": [{
            "title": "Some Paper",
            "publication_year": 2020,
            "open_access": {},
            "primary_location": {"landing_page_url": "https://example.com/primary"},
            "doi": "https://doi.org/10.0000/xyz",
            "locations": [],
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("Some Paper")
    assert result["url"] == "https://example.com/primary"

    # 그마저 없으면 doi
    mock_response = _make_response(200, {
        "results": [{
            "title": "Some Paper",
            "publication_year": 2020,
            "open_access": {},
            "primary_location": {},
            "doi": "https://doi.org/10.0000/xyz",
            "locations": [],
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("Some Paper")
    assert result["url"] == "https://doi.org/10.0000/xyz"


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_when_no_results():
    mock_response = _make_response(200, {"results": []})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("a query with no matches")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_on_non_200_status():
    mock_response = _make_response(429, {"message": "Too Many Requests"})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("some query")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_reference_handles_network_exception_gracefully():
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ConnectError("연결 실패"))):
        result = await resolve_reference("query during network outage")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_when_no_url_available():
    mock_response = _make_response(200, {
        "results": [{
            "title": "No URL Paper",
            "publication_year": 2019,
            "open_access": {},
            "primary_location": {},
            "doi": None,
            "locations": [],
        }]
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await resolve_reference("paper with no url")
    assert result is None


def test_extract_search_query_uses_quoted_title_when_present():
    citation = (
        'K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning for '
        'image recognition," in Proc. CVPR, 2016, pp. 770-778.'
    )
    assert _extract_search_query(citation) == "Deep residual learning for image recognition"


def test_extract_search_query_finds_unquoted_title_between_authors_and_venue():
    citation = "A. Vaswani, N. Shazeer, N. Parmar, et al. Attention is all you need. NeurIPS, 2017."
    assert _extract_search_query(citation) == "Attention is all you need"


def test_extract_search_query_finds_powerbev_title_from_full_citation():
    citation = (
        "Peizheng Li, Shuxiao Ding, Xieyuanli Chen, Niklas Hanselmann, "
        "Marius Cordts, and Juergen Gall. Powerbev: A powerful yet lightweight "
        "framework for instance prediction in bird’s-eye view. In IJCAI, "
        "pages 1080–1088, 2023."
    )
    assert _extract_search_query(citation) == (
        "Powerbev: A powerful yet lightweight framework for instance prediction in bird’s-eye view"
    )


def test_extract_search_query_handles_title_starting_with_lowercase_brand_name():
    citation = (
        "Holger Caesar, Varun Bankiti, Alex H Lang, and Oscar Beijbom. "
        "nuscenes: A multimodal dataset for autonomous driving. "
        "In CVPR, pages 11621–11631, 2020."
    )
    assert _extract_search_query(citation) == "nuscenes: A multimodal dataset for autonomous driving"


@pytest.mark.asyncio
async def test_resolve_reference_sends_extracted_title_as_search_param():
    mock_response = _make_response(200, {"results": []})
    mock_get = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.get", new=mock_get):
        await resolve_reference('Author, "The Real Title," Venue, 2020, pp. 1-10.')

    openalex_calls = [
        call for call in mock_get.call_args_list
        if str(call.args[0]).startswith("https://api.openalex.org/")
    ]
    assert openalex_calls[0].kwargs["params"]["search"] == "The Real Title"


@pytest.mark.asyncio
async def test_resolve_reference_rejects_citing_paper_and_uses_verified_crossref_match():
    citation = (
        "Peizheng Li, Shuxiao Ding, Xieyuanli Chen, Niklas Hanselmann, "
        "Marius Cordts, and Juergen Gall. Powerbev: A powerful yet lightweight "
        "framework for instance prediction in bird’s-eye view. In IJCAI, "
        "pages 1080–1088, 2023."
    )
    wrong_openalex = {
        "results": [{
            "title": "Cam4DOcc: Benchmark for Camera-Only 4D Occupancy Forecasting",
            "publication_year": 2023,
            "open_access": {"oa_url": "https://arxiv.org/pdf/2311.17663"},
            "primary_location": {"landing_page_url": "https://arxiv.org/abs/2311.17663"},
            "locations": [],
        }]
    }
    correct_crossref = {
        "message": {"items": [{
            "title": ["PowerBEV: A Powerful Yet Lightweight Framework for Instance Prediction in Bird's-Eye View"],
            "DOI": "10.24963/ijcai.2023/120",
            "URL": "https://doi.org/10.24963/ijcai.2023/120",
            "published": {"date-parts": [[2023]]},
            "author": [
                {"given": "Peizheng", "family": "Li"},
                {"given": "Shuxiao", "family": "Ding"},
            ],
            "container-title": ["IJCAI"],
        }]}
    }
    empty_arxiv = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )

    async def fake_get(_client, url, **kwargs):
        request = httpx.Request("GET", url)
        if "openalex" in url:
            return httpx.Response(200, json=wrong_openalex, request=request)
        if "crossref" in url:
            return httpx.Response(200, json=correct_crossref, request=request)
        return httpx.Response(200, text=empty_arxiv, request=request)

    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await resolve_reference(citation)

    assert result is not None
    assert result["title"].startswith("PowerBEV")
    assert result["source"] == "Crossref"
    assert "Cam4DOcc" not in result["title"]
