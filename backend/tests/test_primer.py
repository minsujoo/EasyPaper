"""services/primer.py의 순수 로직(참고문헌 매칭 검증, 관련 논문 추천 검증)에 대한
테스트. 외부 API(resolve_reference) 호출은 모킹해 로직만 검증한다.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.primer import _is_plausible_match, _normalize_words, _resolve_recommended_titles


def test_normalize_words_strips_stopwords_and_short_tokens():
    words = _normalize_words("A Study of the Attention Mechanism for NLP")
    assert "study" in words
    assert "attention" in words
    assert "mechanism" in words
    # 불용어와 2글자 이하 토큰은 제외
    assert "a" not in words
    assert "of" not in words
    assert "the" not in words
    assert "for" not in words


def test_is_plausible_match_accepts_year_match():
    resolved = {"title": "Completely Unrelated Words Here", "year": 2019}
    assert _is_plausible_match("... published in 2019 ...", resolved) is True


def test_is_plausible_match_accepts_title_overlap():
    resolved = {"title": "Attention Is All You Need", "year": 2017}
    assert _is_plausible_match("Vaswani et al. Attention Is All You Need. 2020", resolved) is True


def test_is_plausible_match_rejects_unrelated_result():
    resolved = {"title": "Postoperative Endophthalmitis Analysis", "year": 2020}
    assert _is_plausible_match("Zheng et al. Identifying stable EEG patterns. 2019", resolved) is False


def test_is_plausible_match_rejects_when_no_title():
    assert _is_plausible_match("some query", {"title": "", "year": None}) is False


@pytest.mark.asyncio
async def test_resolve_recommended_titles_keeps_plausible_results():
    async def fake_resolve(title):
        return {"title": title, "url": f"https://example.com/{title}", "year": 2021}

    with patch("services.reference_linker.resolve_reference", new=AsyncMock(side_effect=fake_resolve)):
        results = await _resolve_recommended_titles(["Attention Is All You Need"], [])

    assert len(results) == 1
    assert results[0]["title"] == "Attention Is All You Need"


@pytest.mark.asyncio
async def test_resolve_recommended_titles_drops_unresolved_and_implausible():
    async def fake_resolve(title):
        if title == "Real Paper":
            return {"title": "Real Paper", "url": "https://example.com/real", "year": 2020}
        if title == "Hallucinated Paper":
            # 검색은 되지만 완전히 무관한 논문이 반환된 경우
            return {"title": "Totally Different Topic", "url": "https://example.com/x", "year": 1999}
        return None  # 검색 결과 없음

    with patch("services.reference_linker.resolve_reference", new=AsyncMock(side_effect=fake_resolve)):
        results = await _resolve_recommended_titles(
            ["Real Paper", "Hallucinated Paper", "Nonexistent Paper", ""], []
        )

    assert len(results) == 1
    assert results[0]["title"] == "Real Paper"


@pytest.mark.asyncio
async def test_resolve_recommended_titles_deduplicates_against_library_matches():
    async def fake_resolve(title):
        return {"title": "Attention Is All You Need", "url": "https://example.com/a", "year": 2017}

    already_in_library = [_normalize_words("Attention Is All You Need")]
    with patch("services.reference_linker.resolve_reference", new=AsyncMock(side_effect=fake_resolve)):
        results = await _resolve_recommended_titles(["Attention Is All You Need"], already_in_library)

    assert results == []


@pytest.mark.asyncio
async def test_resolve_recommended_titles_respects_limit():
    call_count = 0

    async def fake_resolve(title):
        nonlocal call_count
        call_count += 1
        return {"title": title, "url": f"https://example.com/{call_count}", "year": 2020}

    titles = [
        "Convolutional Neural Networks for Image Classification",
        "Recurrent Sequence Models for Machine Translation",
        "Graph Attention Networks for Node Embeddings",
        "Variational Autoencoders for Generative Modeling",
        "Reinforcement Learning for Robotic Control",
        "Transformer Architectures for Language Understanding",
        "Contrastive Learning for Visual Representations",
        "Diffusion Models for Image Synthesis",
        "Federated Learning for Privacy Preservation",
        "Meta Learning for Few Shot Classification",
    ]
    with patch("services.reference_linker.resolve_reference", new=AsyncMock(side_effect=fake_resolve)):
        results = await _resolve_recommended_titles(titles, [])

    assert call_count == 5
    assert len(results) == 5
