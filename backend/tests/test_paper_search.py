from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from services.paper_search import (
    _apply_taste_model,
    _build_taste_profile,
    _normalise_plan,
    discover_feed,
    explain_results,
    merge_ranked_results,
    recommend_semantic_scholar,
    search_semantic_scholar,
)
from services.scholar_crawler import _crawl_is_due, refresh_scholar_cache
from services.conference_official import (
    _merge_schedule_candidates,
    _related_schedule_urls,
    estimated_schedule_for,
    extract_official_registration,
    extract_official_schedule,
    official_url_for,
    rolling_conference_catalog,
)
from services.scholar_tools import _conference_action, _conference_status, build_paper_graph, list_conferences


def test_ai_paper_search_returns_verified_results(test_client):
    payload = {
        "question": "camera based occupancy prediction",
        "search_query": "camera occupancy prediction",
        "keywords": ["occupancy prediction"],
        "answer": "관련 연구를 찾았습니다.",
        "ai_used": True,
        "results": [{"id": "paper-1", "title": "Paper One", "url": "https://doi.org/10.1/test"}],
        "total": 1,
        "searched_at": "2026-01-01T00:00:00Z",
        "source": "OpenAlex",
    }
    with patch("routers.paper_search.search_papers", new=AsyncMock(return_value=payload)) as search:
        response = test_client.post("/api/paper-search", json={
            "query": "카메라 기반 점유 예측", "year_from": 2023, "open_access": True,
        })
    assert response.status_code == 200
    assert response.json()["results"][0]["title"] == "Paper One"
    search.assert_awaited_once_with(
        "카메라 기반 점유 예측", year_from=2023, year_to=None,
        open_access=True, sort="relevance", library_context=[],
    )


def test_ai_paper_search_validates_query_and_year_range(test_client):
    assert test_client.post("/api/paper-search", json={"query": "x"}).status_code == 422
    response = test_client.post("/api/paper-search", json={
        "query": "valid query", "year_from": 2025, "year_to": 2020,
    })
    assert response.status_code == 422


def test_ai_paper_search_maps_upstream_failure(test_client):
    with patch("routers.paper_search.search_papers", new=AsyncMock(side_effect=RuntimeError("OpenAlex 검색 오류"))):
        response = test_client.post("/api/paper-search", json={"query": "valid query"})
    assert response.status_code == 502
    assert "OpenAlex" in response.json()["detail"]


def test_gaussian_occupancy_query_uses_3d_scene_context_variants():
    plan = _normalise_plan(
        {"queries": ["traffic occupancy prediction Gaussian"]},
        "가우시안을 사용하는 occupancy forecasting 모델",
        ["Cam4DOcc: Camera-Only 4D Occupancy Forecasting"],
    )
    assert "Gaussian splatting 4D occupancy forecasting autonomous driving" in plan["queries"]
    assert any("Gaussian world model" in query for query in plan["queries"])
    assert plan["queries"][0] != "traffic occupancy prediction Gaussian"


def test_multi_query_results_are_fused_and_deduplicated():
    common = {"id": "gaussian-world", "title": "GaussianWorld", "citation_count": 4}
    noisy = {"id": "traffic", "title": "Traffic volume forecasting", "citation_count": 100}
    gem = {"id": "gem", "title": "GEM", "citation_count": 1}
    merged = merge_ranked_results([
        [common, noisy],
        [gem, common],
        [common],
    ])
    assert [paper["id"] for paper in merged[:2]] == ["gaussian-world", "gem"]
    assert len([paper for paper in merged if paper["id"] == "gaussian-world"]) == 1
    assert merged[0]["relevance_score"] == 100


def test_domain_terms_demote_generic_high_citation_results():
    generic = {
        "id": "motion-survey", "title": "Human motion trajectory prediction: a survey",
        "abstract": "Gaussian mixture models for autonomous agents", "citation_count": 900,
    }
    relevant = {
        "id": "gaussian-world", "title": "GaussianWorld: Gaussian World Model for Streaming 3D Occupancy Prediction",
        "abstract": "Forecasting future scene occupancy", "citation_count": 4,
    }
    merged = merge_ranked_results(
        [[generic, relevant], [generic, relevant]],
        ranking_terms=["gaussian", "occupancy", "forecast", "3d", "4d"],
    )
    assert merged[0]["id"] == "gaussian-world"


def test_long_term_taste_model_uses_positive_and_negative_feedback():
    feedback = [
        {"rating": 1, "paper": {"title": "Gaussian 4D occupancy forecasting", "abstract": "autonomous driving scene occupancy"}},
        {"rating": -1, "paper": {"title": "Building occupancy energy forecast", "abstract": "office HVAC demand"}},
    ]
    profile, samples = _build_taste_profile([], feedback, [])
    candidates = [
        {"title": "Gaussian scene occupancy prediction", "abstract": "4D autonomous driving", "relevance_score": 60},
        {"title": "Office building occupancy forecast", "abstract": "HVAC energy demand", "relevance_score": 60},
    ]
    _apply_taste_model(candidates, profile, samples)
    assert candidates[0]["personalized_score"] > candidates[1]["personalized_score"]
    assert samples == 2


async def test_semantic_scholar_search_uses_key_header_and_maps_verified_record():
    response = httpx.Response(200, request=httpx.Request("GET", "https://api.semanticscholar.org"), json={
        "data": [{
            "paperId": "s2-paper-1",
            "title": "Gaussian Occupancy Forecasting",
            "url": "https://www.semanticscholar.org/paper/s2-paper-1",
            "abstract": "We forecast future occupancy with Gaussian primitives.",
            "authors": [{"name": "Researcher One"}],
            "year": 2026,
            "venue": "CVPR",
            "citationCount": 7,
            "externalIds": {"DOI": "10.1000/example"},
            "openAccessPdf": {"url": "https://example.org/paper.pdf"},
            "publicationDate": "2026-06-01",
            "publicationTypes": ["Conference"],
        }],
    })
    client = AsyncMock()
    client.get.return_value = response
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch("services.paper_search.get_semantic_scholar_api_key", return_value="test-key"), \
         patch("services.paper_search._wait_for_semantic_scholar_slot", new=AsyncMock()), \
         patch("services.paper_search.httpx.AsyncClient", return_value=context) as client_factory:
        results = await search_semantic_scholar(
            "Gaussian-occupancy", year_from=2025, year_to=2026, open_access=True,
        )

    assert client_factory.call_args.kwargs["headers"]["x-api-key"] == "test-key"
    params = client.get.await_args.kwargs["params"]
    assert params["query"] == "Gaussian occupancy"
    assert params["year"] == "2025-2026"
    assert "openAccessPdf" in params
    assert results[0]["semantic_scholar_id"] == "s2-paper-1"
    assert results[0]["pdf_url"] == "https://example.org/paper.pdf"
    assert results[0]["source"] == "Semantic Scholar"


async def test_semantic_scholar_dedicated_recommendations_use_paper_id():
    response = httpx.Response(200, request=httpx.Request("GET", "https://api.semanticscholar.org"), json={
        "recommendedPapers": [{
            "paperId": "nearby-paper", "title": "Nearby Work",
            "url": "https://www.semanticscholar.org/paper/nearby-paper",
            "authors": [], "externalIds": {}, "openAccessPdf": None,
        }],
    })
    client = AsyncMock(); client.get.return_value = response
    context = MagicMock(); context.__aenter__ = AsyncMock(return_value=client); context.__aexit__ = AsyncMock(return_value=False)
    with patch("services.paper_search.get_semantic_scholar_api_key", return_value="test-key"), \
         patch("services.paper_search._wait_for_semantic_scholar_slot", new=AsyncMock()), \
         patch("services.paper_search.httpx.AsyncClient", return_value=context):
        results = await recommend_semantic_scholar(["seed-paper"], limit=12)
    assert "forpaper/seed-paper" in client.get.await_args.args[0]
    assert client.get.await_args.kwargs["params"]["limit"] == 12
    assert results[0]["semantic_scholar_id"] == "nearby-paper"


async def test_discover_feed_excludes_recent_impressions_and_marks_bookmarks():
    candidates = [
        {"id": "seen", "title": "Seen Paper", "url": "https://example.org/seen", "relevance_score": 90},
        {"id": "fresh", "title": "Fresh Paper", "url": "https://example.org/fresh", "relevance_score": 75},
    ]
    search_result = {"results": candidates, "answer": "", "source": "OpenAlex", "total": 2}
    with patch("services.paper_search.search_papers", new=AsyncMock(return_value=search_result)), \
         patch("services.paper_search.recommend_semantic_scholar", new=AsyncMock(return_value=[])):
        result = await discover_feed(
            [{"filename": "Seed.pdf", "metadata": {"title": "Seed Work"}}], [],
            impressions=[{
                "paper_id": "seen", "last_seen_at": "2099-01-01T00:00:00+00:00", "hidden_at": None,
            }],
            bookmarks=[{"paper_id": "fresh", "paper": {"id": "fresh", "title": "Fresh Paper"}}],
        )
    assert [paper["id"] for paper in result["results"]] == ["fresh"]
    assert result["results"][0]["bookmarked"] is True


async def test_scheduled_crawl_persists_verified_digest(isolated_dirs):
    documents = [{"filename": "Seed.pdf", "metadata": {
        "title": "Seed Work", "semantic_scholar_id": "seed-s2",
    }}]
    recommendation = [{
        "id": "new-paper", "title": "New Work", "url": "https://example.org/new",
        "source": "Semantic Scholar", "publication_date": "2026-07-10",
    }]
    with patch("services.scholar_crawler.list_documents", return_value=documents), \
         patch("services.scholar_crawler.get_semantic_scholar_api_key", return_value="test-key"), \
         patch("services.scholar_crawler.recommend_semantic_scholar", new=AsyncMock(return_value=recommendation)), \
         patch("services.scholar_crawler.search_openalex", new=AsyncMock(return_value=[])), \
         patch("services.scholar_crawler.search_semantic_scholar", new=AsyncMock(return_value=[])):
        result = await refresh_scholar_cache("testuser", force=True)

    assert result["refreshed"] is True
    assert result["cached"] == 1
    cached = isolated_dirs["db"].db_list_scholar_digest_cache("testuser")
    assert cached[0]["title"] == "New Work"
    assert isolated_dirs["db"].db_get_scholar_feed_state("testuser")["last_crawl_at"]


async def test_paper_graph_combines_references_citations_and_similar():
    root = {"id": "root", "title": "Root", "semantic_scholar_id": "s2-root"}
    reference = {"id": "ref", "title": "Reference", "url": "https://example.org/ref"}
    citation = {"id": "cite", "title": "Citation", "url": "https://example.org/cite"}
    similar = {"id": "near", "title": "Similar", "url": "https://example.org/near"}
    with patch("services.scholar_tools._s2_relation", new=AsyncMock(side_effect=[[reference], [citation]])), \
         patch("services.scholar_tools.recommend_semantic_scholar", new=AsyncMock(return_value=[similar])):
        graph = await build_paper_graph(root)
    assert {node["group"] for node in graph["nodes"]} == {"root", "reference", "citation", "similar"}
    assert len(graph["edges"]) == 3


async def test_paper_graph_resolves_local_pdf_by_title():
    root = {"id": "local-doc", "title": "A Local Research Paper"}
    resolved = {
        "id": "resolved", "title": "A Local Research Paper",
        "semantic_scholar_id": "s2-resolved", "url": "https://example.org/paper",
    }
    with patch("services.scholar_tools.search_semantic_scholar", new=AsyncMock(return_value=[resolved])), \
         patch("services.scholar_tools._s2_relation", new=AsyncMock(side_effect=[[], []])) as relation, \
         patch("services.scholar_tools.recommend_semantic_scholar", new=AsyncMock(return_value=[])):
        graph = await build_paper_graph(root)

    assert graph["root_id"] == "local-doc"
    assert graph["nodes"][0]["semantic_scholar_id"] == "s2-resolved"
    assert relation.await_args_list[0].args[0] == "s2-resolved"


def test_scheduled_crawl_obeys_interval():
    assert _crawl_is_due({"last_crawl_at": None, "crawl_interval_hours": 24}) is True
    assert _crawl_is_due({
        "last_crawl_at": "2099-01-01T00:00:00+00:00", "crawl_interval_hours": 24,
    }) is False


async def test_result_highlight_is_selected_from_original_abstract_sentence():
    results = [{
        "id": "paper-1",
        "title": "Forecasting Paper",
        "abstract": "We introduce a baseline. Our Gaussian representation forecasts 4D occupancy. It is efficient.",
    }]
    payload = '{"answer":"요약 [1]","relevance":{"1":"직접 관련됨"},"highlights":{"1":2}}'
    with patch("services.paper_search._collect_llm", new=AsyncMock(return_value=payload)):
        answer, relevance, highlights, ai_used = await explain_results("Gaussian occupancy", results)

    assert answer == "요약 [1]"
    assert relevance["paper-1"] == "직접 관련됨"
    assert highlights["paper-1"] == "Our Gaussian representation forecasts 4D occupancy."
    assert ai_used is True


def test_scholar_feedback_toggles_and_persists(test_client, isolated_dirs):
    payload = {
        "paper_id": "paper-1", "rating": 1,
        "paper": {"id": "paper-1", "title": "GaussianWorld"},
    }
    first = test_client.post("/api/scholar/feedback", json=payload)
    assert first.status_code == 200
    assert first.json()["rating"] == 1
    rows = isolated_dirs["db"].db_list_scholar_feedback("testuser")
    assert rows[0]["paper"]["title"] == "GaussianWorld"

    toggled = test_client.post("/api/scholar/feedback", json=payload)
    assert toggled.status_code == 200
    assert toggled.json()["rating"] == 0
    assert isolated_dirs["db"].db_list_scholar_feedback("testuser") == []


def test_scholar_bookmark_works_without_public_pdf(test_client, isolated_dirs):
    folder = isolated_dirs["db"].db_create_folder("testuser", "Reading Queue")
    payload = {
        "paper_id": "metadata-only", "folder_id": folder["id"], "saved": True,
        "paper": {"id": "metadata-only", "title": "Metadata Only", "url": "https://example.org/paper"},
    }
    saved = test_client.post("/api/scholar/bookmark", json=payload)
    assert saved.status_code == 200
    assert saved.json()["saved"] is True
    listed = test_client.get(f"/api/scholar/bookmarks?folder_id={folder['id']}").json()
    assert listed["results"][0]["title"] == "Metadata Only"
    assert listed["results"][0]["bookmarked"] is True
    isolated_dirs["db"].db_link_scholar_bookmark("testuser", "metadata-only", "saved-doc")
    linked = isolated_dirs["db"].db_list_scholar_bookmarks("testuser")[0]["paper"]
    assert linked["downloaded"] is True
    assert linked["saved_document_id"] == "saved-doc"


def test_scholar_resolve_pdf_route_uses_broad_resolver(test_client):
    with patch("services.scholar_tools.resolve_paper_pdf", new=AsyncMock(return_value={
        "pdf_url": "https://arxiv.org/pdf/2601.00001.pdf", "source": "arXiv", "resolved": True,
    })):
        response = test_client.post("/api/scholar/resolve-pdf", json={"paper": {
            "id": "paper", "title": "A Paper", "doi": "https://doi.org/10.1/test",
        }})
    assert response.status_code == 200
    assert response.json()["source"] == "arXiv"


def test_scholar_conference_watch_persists(test_client, isolated_dirs):
    payload = {
        "conference_id": "cvpr27", "watched": True,
        "conference": {"id": "cvpr27", "title": "CVPR", "year": 2027, "url": "https://cvpr.thecvf.com"},
    }
    response = test_client.post("/api/scholar/conferences/watch", json=payload)
    assert response.status_code == 200 and response.json()["watched"] is True
    rows = isolated_dirs["db"].db_list_scholar_conference_watch("testuser")
    assert rows[0]["conference"]["title"] == "CVPR"


async def test_conferences_use_user_spreadsheet_catalog():
    conferences = await list_conferences(today=date(2026, 8, 2))
    assert conferences
    assert all(item["status"] != "past" for item in conferences)
    assert all(item["official_url"].startswith(("http://", "https://")) for item in conferences)
    assert all(len(item["about_ko"]) >= 25 for item in conferences)
    assert all(item["submission_status"] in {"open", "closed"} for item in conferences)
    assert all(item["deadline"] for item in conferences)
    open_deadlines = [item["deadline"] for item in conferences if item["submission_status"] == "open"]
    closed_deadlines = [item["deadline"] for item in conferences if item["submission_status"] == "closed"]
    assert open_deadlines == sorted(open_deadlines)
    assert closed_deadlines == sorted(closed_deadlines, reverse=True)
    assert {item["priority"] for item in conferences} == {
        "recommended", "supported", "discuss", "special",
    }
    cvpr = next(item for item in conferences if item["title"] == "CVPR" and item["year"] == 2027)
    assert cvpr["date"] == "2027-06-19"
    assert cvpr["deadline"] == "2026-11-13"
    assert cvpr["deadline_estimated"] is True
    assert "컴퓨터 비전" in cvpr["about_ko"]


def test_conference_catalog_rolls_forward_without_rebuild():
    editions_2027 = rolling_conference_catalog(date(2027, 1, 1))
    aaai = next(item for item in editions_2027 if item["title"] == "AAAI" and item["year"] == 2028)
    assert aaai["generated"] is True
    assert aaai["priority"] == "recommended"
    assert aaai["deadline"] == "" and aaai["date"] == ""
    assert any(item["title"] == "ECCV" and item["year"] == 2028 for item in editions_2027)

    editions_2030 = rolling_conference_catalog(date(2030, 1, 1))
    assert any(item["title"] == "AAAI" and item["year"] == 2031 for item in editions_2030)
    assert not any(int(item["year"]) < 2030 for item in editions_2030)


def test_missing_future_dates_are_estimated_from_prior_editions():
    with patch("services.conference_official._read_cache", return_value={"items": {}}):
        estimate = estimated_schedule_for({
            "title": "CVPR", "year": 2027,
            "deadline": "2026-11-??", "date": "2027-06-19",
        })
        rolling = estimated_schedule_for({
            "title": "WSDM", "year": 2028,
            "deadline": "", "date": "",
        })
    assert estimate["deadline"] == "2026-11-13"
    assert estimate["deadline_based_on_year"] == 2026
    assert "date" not in estimate
    assert rolling["deadline"] == "2027-08-24"
    assert rolling["date"] == "2028-02-15"


def test_conference_status_uses_future_event_after_deadline_and_hides_past():
    today = date(2026, 8, 2)
    assert _conference_status({"deadline": "2026-08-24"}, today) == ("upcoming", 22)
    assert _conference_status({"deadline": "2026-07-28"}, today) == ("past", -5)
    assert _conference_action({"deadline": "2026-11-??", "date": "2027-06-19"}, today) == (
        "upcoming", 321, "event", "2027-06-19",
    )
    assert _conference_status({"date": "2026-07-02", "date_end": "2026-08-22"}, today) == ("past", None)


def test_conference_registration_keeps_near_event_actionable():
    conference = {
        "deadline": "2026-03-29", "date": "2026-08-24", "date_end": "2026-08-28",
        "registration_open": True, "registration_deadline": "2026-08-03",
    }
    assert _conference_action(conference, date(2026, 8, 3)) == (
        "upcoming", 0, "registration", "2026-08-03",
    )


def test_official_schedule_requires_identity_and_extracts_submission_deadline():
    conference = {
        "title": "WSDM", "description": "ACM International Conference on Web Search and Data Mining",
        "year": 2027,
    }
    html = """
      <html><head><title>WSDM 2027 Hong Kong</title></head><body>
      <h1>The 20th ACM International Conference on Web Search and Data Mining</h1>
      <p>Paper submission deadline: August 24, 2026</p>
      <p>Author notification: October 20, 2026</p>
      <p>Conference dates: February 15, 2027</p>
      </body></html>
    """
    result = extract_official_schedule(html, conference)
    assert result["verified"] is True
    assert result["deadline"] == "2026-08-24"
    assert result["date"] == "2027-02-15"
    assert result["confidence"] >= 65


def test_official_schedule_rejects_wrong_conference_year():
    result = extract_official_schedule(
        "<h1>WSDM 2026</h1><p>Paper submission deadline: August 24, 2025</p>",
        {"title": "WSDM", "description": "Web Search and Data Mining", "year": 2027},
    )
    assert result["verified"] is False and result["deadline"] == ""


def test_official_registration_extracts_author_early_and_open_status():
    result = extract_official_registration(
        """
        <html><title>ECML PKDD 2026 Registration</title><body>
        <h1>Registration</h1><p>Registration is open. Register here.</p>
        <p>Early registration expires 31st July 2026.</p>
        <p>Author registration expires 20th of June 2026.</p>
        </body></html>
        """,
        {"title": "PKDD", "description": "European Conference on Machine Learning", "year": 2026},
        "https://ecmlpkdd.org/2026/attending-registration/",
    )
    assert result["registration_verified"] is True
    assert result["registration_open"] is True
    assert result["early_registration_deadline"] == "2026-07-31"
    assert result["author_registration_deadline"] == "2026-06-20"
    assert result["registration_deadline"] == ""


def test_registration_parser_does_not_treat_navigation_menu_as_registration_page():
    result = extract_official_registration(
        """
        <html><title>KDD 2026</title><body>
        <nav><a>Registration</a><a>Authors</a></nav>
        <p>Author notification: October 13, 2026.</p>
        </body></html>
        """,
        {"title": "KDD", "description": "Knowledge Discovery and Data Mining", "year": 2026},
        "https://kdd2026.kdd.org/",
    )
    assert result["registration_verified"] is False
    assert result["author_registration_deadline"] == ""


def test_registration_parser_handles_abbreviated_months_and_fee_table_columns():
    result = extract_official_registration(
        """
        <html><title>2026 IEEE RO-MAN Registration</title><body>
        <h1>Registration System</h1><p>Please access the registration system using the button below.</p>
        <div>Early bird (~June 29, 2026)</div>
        <div>Standard (~Aug. 3, 2026)</div>
        <div>On-site Reg. (Aug. 24~28, 2026)</div>
        </body></html>
        """,
        {"title": "RO-MAN", "description": "Robot and Human Interactive Communication", "year": 2026},
        "https://ro-man2026.org/registration/",
    )
    assert result["registration_open"] is True
    assert result["early_registration_deadline"] == "2026-06-29"
    assert result["registration_deadline"] == "2026-08-28"


def test_official_link_discovery_includes_registration_page():
    links = _related_schedule_urls(
        """
        <a href='/research-track-call-for-papers/'>Research Track Call for Papers</a>
        <a href='/program/'>Program</a>
        <a href='/registration/'>Conference Registration</a>
        """,
        "https://example.org/2026/", 2026,
    )
    assert "https://example.org/registration/" in links


def test_registration_parser_ignores_author_and_standard_prose_dates():
    result = extract_official_registration(
        """
        <html><title>IEEE ITSC 2026 Registration</title><body>
        <h1>Registration</h1><p>Register here</p>
        <p>Information For Authors</p>
        <p>Final paper upload deadline: May 15, 2026.</p>
        <p>IEEE Standards</p>
        <p>Videos are available September 15 - October 15, 2026.</p>
        <p>Onsite attendance registration is available.</p>
        </body></html>
        """,
        {"title": "ITSC", "description": "Intelligent Transportation Systems", "year": 2026},
        "https://ieee-itsc.org/2026/attend/registration/",
    )
    assert result["registration_open"] is True
    assert result["author_registration_deadline"] == ""
    assert result["registration_deadline"] == ""


def test_registration_parser_reads_compact_pricing_headers():
    result = extract_official_registration(
        """
        <html><title>Registration - KDD 2026</title><body>
        <h1>Registration</h1><p>Registration is now live here!</p>
        <div>Early Bird Ends June 17th, AoE</div>
        <div>Standard Ends August 8th, AoE</div>
        <div>Onsite Starting August 9th, AoE</div>
        </body></html>
        """,
        {"title": "KDD", "description": "Knowledge Discovery and Data Mining", "year": 2026},
        "https://kdd2026.kdd.org/registration/",
    )
    assert result["registration_open"] is True
    assert result["early_registration_deadline"] == "2026-06-17"
    assert result["registration_deadline"] == "2026-08-08"


def test_registration_parser_accepts_schedule_embedded_on_official_homepage():
    result = extract_official_registration(
        """
        <html><title>VTC2026-Fall Boston</title><body>
        <h1>IEEE Vehicular Technology Conference 2026</h1>
        <p>Registration and Final Paper Upload sites are now available!</p>
        <p>30 June 2026: Regular Paper Author Registration Due</p>
        <p>26 July 2026: Early Bird Registration Ends</p>
        <p>23 August 2026: Regular Registration Due</p>
        </body></html>
        """,
        {"title": "VTC-Fall", "description": "Vehicular Technology Conference", "year": 2026},
        "https://events.vtsociety.org/vtc2026-fall/",
    )
    assert result["registration_verified"] is True
    assert result["registration_open"] is True
    assert result["author_registration_deadline"] == "2026-06-30"
    assert result["early_registration_deadline"] == "2026-07-26"
    assert result["registration_deadline"] == "2026-08-23"


def test_official_schedule_uses_latest_full_paper_cycle_not_abstract_date():
    result = extract_official_schedule(
        """
        <h1>KDD 2026</h1>
        <p>First cycle abstract submission deadline: July 24, 2025</p>
        <p>First cycle paper submission deadline: July 31, 2025</p>
        <p>Second cycle abstract submission deadline: February 1, 2026</p>
        <p>Second cycle paper submission deadline: February 8, 2026</p>
        <p>Author notification: May 16, 2026</p>
        """,
        {
            "title": "KDD", "description": "Knowledge Discovery and Data Mining",
            "year": 2026,
        },
    )
    assert result["verified"] is True
    assert result["deadline"] == "2026-02-08"


def test_official_schedule_understands_same_month_event_range():
    result = extract_official_schedule(
        """
        <h1>ECAI 2026 — European Conference on Artificial Intelligence</h1>
        <p>The main conference will take place from 18 to 21 August 2026.</p>
        """,
        {"title": "ECAI", "description": "European Conference on Artificial Intelligence", "year": 2026},
    )
    assert result["date"] == "2026-08-18"
    assert result["date_end"] == "2026-08-21"


def test_official_schedule_prefers_labeled_main_conference_range():
    result = extract_official_schedule(
        """
        <h1>UAI 2026 — Conference on Uncertainty in Artificial Intelligence</h1>
        <p>The conference will be held in Amsterdam on these dates:</p>
        <p>Tutorials: Monday, August 17th, 2026</p>
        <p>Main conference: Tuesday, August 18th to Thursday, August 20th, 2026</p>
        <p>Workshops: Friday, August 21st, 2026</p>
        """,
        {"title": "UAI", "description": "Conference on Uncertainty in Artificial Intelligence", "year": 2026},
    )
    assert result["date"] == "2026-08-18"
    assert result["date_end"] == "2026-08-20"


def test_official_schedule_does_not_treat_notification_as_event_date():
    result = extract_official_schedule(
        """
        <h1>IEEE IV 2027 Intelligent Vehicles Symposium</h1>
        <p>June 15 – 18 2027 Perth, Australia</p>
        <p>Conference Paper Submission Deadline: November 15, 2026
        Conference Paper Notification Date: January 15, 2027
        Conference Camera-ready Submission Date: February 01, 2027</p>
        """,
        {"title": "IV", "description": "IEEE Intelligent Vehicles Symposium", "year": 2027},
    )
    assert result["date"] == "2027-06-15"
    assert result["date_end"] == "2027-06-18"


def test_ranked_official_pages_keep_research_track_over_secondary_track():
    merged = _merge_schedule_candidates([
        {"verified": True, "deadline": "", "date": "", "date_end": "", "place": "", "evidence": "", "confidence": 0},
        {"verified": True, "deadline": "2026-02-08", "date": "", "date_end": "", "place": "", "evidence": "research", "confidence": 60},
        {"verified": True, "deadline": "2026-03-12", "date": "", "date_end": "", "place": "", "evidence": "blue sky", "confidence": 60},
    ])
    assert merged["deadline"] == "2026-02-08"
    assert merged["evidence"] == "research"


def test_official_deadline_on_or_after_event_is_rejected():
    merged = _merge_schedule_candidates([
        {"verified": True, "deadline": "2027-12-31", "date": "2027-02-27", "date_end": "2027-03-03", "place": "", "evidence": "event", "confidence": 65},
    ])
    assert merged["deadline"] == ""
    assert merged["date"] == "2027-02-27"
    assert merged["rejected"] == ["deadline"]


def test_every_conference_series_has_an_official_link():
    assert official_url_for({"title": "CVPR", "year": 2026})[0].startswith("https://")
    assert official_url_for({"title": "ITSC", "year": 2027})[0].startswith("https://")
    assert official_url_for({"title": "ECAI", "year": 2026})[0] == "https://eurai.org/ecai"
    assert "ieee-ras.org/event/2027" in official_url_for({"title": "IROS", "year": 2027})[0]
    assert official_url_for({"title": "IV", "year": 2027})[0] == "https://ieee-iv.org/2027/"


def test_scholar_impression_can_be_opened_and_hidden(test_client, isolated_dirs):
    isolated_dirs["db"].db_record_scholar_impressions(
        "testuser", [{"id": "paper-state", "title": "Stateful Paper"}], "recommended",
    )
    opened = test_client.post("/api/scholar/interaction", json={"paper_id": "paper-state", "action": "open"})
    hidden = test_client.post("/api/scholar/interaction", json={"paper_id": "paper-state", "action": "hide"})
    assert opened.status_code == 200 and hidden.status_code == 200
    row = isolated_dirs["db"].db_list_scholar_impressions("testuser")[0]
    assert row["opened_at"] and row["hidden_at"]


def test_scholar_feed_uses_selected_mode(test_client):
    response_payload = {
        "answer": "맞춤 논문입니다.", "results": [], "total": 0, "source": "OpenAlex",
    }
    with patch("routers.paper_search.discover_feed", new=AsyncMock(return_value=response_payload)) as discover:
        response = test_client.get("/api/scholar/feed?mode=latest")
    assert response.status_code == 200
    discover.assert_awaited_once()
    assert discover.await_args.kwargs["mode"] == "latest"


def test_scholar_import_reuses_upload_pipeline(test_client, tmp_path):
    from models.schemas import UploadResponse

    async def fake_download(url, destination, max_bytes):
        with open(destination, "wb") as output:
            output.write(b"%PDF-1.7 test")
        return 13

    uploaded = UploadResponse(
        session_id="saved-paper", filename="GaussianWorld.pdf", total_pages=2,
        file_size_mb=0.1, metadata={"title": "old"},
    )
    with patch("services.remote_pdf.download_public_pdf", new=fake_download), \
         patch("routers.upload.upload_pdf", new=AsyncMock(return_value=uploaded)), \
         patch("routers.paper_search.db_update_document_metadata") as update_metadata, \
         patch("routers.paper_search.db_get_folder", return_value={"id": 7, "username": "testuser"}), \
         patch("routers.paper_search.db_move_document_to_folder", return_value=True) as move_document:
        response = test_client.post("/api/scholar/import", json={
            "paper_id": "gaussian-world",
            "title": "GaussianWorld",
            "pdf_url": "https://example.org/paper.pdf",
            "url": "https://example.org/paper",
            "authors": ["Researcher One"],
            "year": 2025,
            "folder_id": 7,
            "translation_mode": "scroll",
        })
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert response.json()["metadata"]["title"] == "GaussianWorld"
    assert response.json()["folder_id"] == 7
    update_metadata.assert_called_once()
    move_document.assert_called_once_with("saved-paper", "testuser", 7)


def test_scholar_preview_returns_detected_visuals(test_client):
    async def fake_download(url, destination, max_bytes):
        with open(destination, "wb") as output:
            output.write(b"%PDF-1.7 test")
        return 13

    def fake_render(pdf_path, page, visual, output_path):
        with open(output_path, "wb") as output:
            output.write(b"png-image")
        return True

    visuals = [{
        "kind": "figure", "label": "Figure 1", "caption": "Architecture",
        "page": 2, "left": 1, "top": 2, "width": 3, "height": 4,
    }]
    with patch("services.remote_pdf.download_public_pdf", new=fake_download), \
         patch("services.paper_note._select_visuals", return_value=visuals), \
         patch("services.pdf_parser.render_image_crop", side_effect=fake_render):
        response = test_client.post("/api/scholar/preview", json={
            "pdf_url": "https://example.org/paper.pdf",
        })

    assert response.status_code == 200
    assert response.json()["visuals"][0]["label"] == "Figure 1"
    assert response.json()["visuals"][0]["image_data"].startswith("data:image/png;base64,")
