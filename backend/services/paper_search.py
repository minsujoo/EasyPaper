"""AI-assisted academic paper search backed by verified OpenAlex records.

The LLM is deliberately kept away from paper discovery: it may translate/expand the
user's question and explain records returned by OpenAlex, but it cannot invent a
result.  Every item exposed to the frontend therefore has an OpenAlex/DOI URL.
"""

import asyncio
import json
import logging
import math
import os
import re
import time
from collections import Counter
from datetime import datetime
from typing import Optional

import httpx

from config import get_openalex_mailto, get_semantic_scholar_api_key
from services.llm_client import stream_chat
from services.reference_linker import _openalex_candidate

logger = logging.getLogger(__name__)

_OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_SEMANTIC_SCHOLAR_RECOMMEND_URL = "https://api.semanticscholar.org/recommendations/v1/papers/"
_REQUEST_TIMEOUT = 20.0
_MAX_RESULTS = 25
_MAX_QUERY_VARIANTS = 4
_RRF_K = 60
_S2_MIN_INTERVAL_SECONDS = 1.5
_s2_rate_lock = asyncio.Lock()
_s2_last_request_started = 0.0


def _extract_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


async def _collect_llm(system_prompt: str, user_prompt: str, timeout: float = 120.0) -> str:
    async def _run() -> str:
        chunks = []
        async for token in stream_chat(system_prompt, [{"role": "user", "content": user_prompt}]):
            chunks.append(token)
        return "".join(chunks)

    return await asyncio.wait_for(_run(), timeout=timeout)


def _domain_query_hints(question: str, library_context: list[str]) -> list[str]:
    """Add high-value domain interpretations for terms that are genuinely ambiguous.

    In the user's library, "occupancy" normally means 3D scene occupancy rather than
    building occupancy or road-volume forecasting.  The hints are still variants,
    not hard filters, so a planner can retain a second plausible interpretation.
    """
    combined = " ".join([question, *library_context]).lower()
    has_occupancy = "occupancy" in combined or "점유" in combined
    has_gaussian = "gaussian" in combined or "가우시안" in combined
    autonomous_context = any(term in combined for term in (
        "cam4docc", "occworld", "forecastocc", "autonomous", "자율주행",
        "4d occupancy", "3d occupancy", "scene occupancy",
    ))
    if has_occupancy and has_gaussian and autonomous_context:
        return [
            "Gaussian splatting 4D occupancy forecasting autonomous driving",
            "Gaussian world model 3D occupancy prediction autonomous driving",
            "3D Gaussian scene occupancy forecasting",
        ]
    return []


def _normalise_plan(parsed: Optional[dict], question: str, library_context: list[str]) -> dict:
    parsed = parsed or {}
    queries = parsed.get("queries") or []
    if isinstance(queries, str):
        queries = [queries]
    legacy_query = str(parsed.get("search_query") or "").strip()
    if legacy_query:
        queries.insert(0, legacy_query)
    hints = _domain_query_hints(question, library_context)
    # Deterministic domain hints go first: a weak model must not be able to collapse
    # an ambiguous 3D-occupancy query into traffic-volume forecasting again.
    queries = hints + [str(value).strip() for value in queries]
    queries.append(question.strip())
    deduplicated = []
    seen = set()
    for query in queries:
        key = re.sub(r"\s+", " ", query).strip().lower()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        deduplicated.append(query[:300])
    keywords = [str(item).strip()[:80] for item in (parsed.get("keywords") or [])]
    return {
        "search_query": deduplicated[0],
        "queries": deduplicated[:_MAX_QUERY_VARIANTS],
        "keywords": [item for item in keywords if item][:8],
        "interpretation": str(parsed.get("interpretation") or "").strip()[:300],
        "domain": str(parsed.get("domain") or "").strip()[:100],
    }


async def plan_query(question: str, library_context: Optional[list[str]] = None) -> dict:
    """Create multiple English retrieval queries after resolving domain ambiguity."""
    library_context = [value for value in (library_context or []) if value][:12]
    fallback = _normalise_plan({}, question, library_context)
    prompt = (
        "Interpret the research intent before searching. Terms such as occupancy and Gaussian are ambiguous: "
        "distinguish 3D/4D scene occupancy for autonomous driving from building occupancy, traffic volume, "
        "trajectory prediction, and Gaussian-process forecasting. Recent library titles are contextual clues, "
        "not papers that must appear in the result. Produce 3 diverse, concise English retrieval queries that "
        "preserve named methods/datasets and cover the most plausible intended technical terminology. "
        "Return JSON only with this shape: "
        '{"domain":"...","interpretation":"...","queries":["...","...","..."],'
        '"keywords":["...","..."]}. Do not recommend or invent paper titles.\n\n'
        f"Question: {question.strip()}\nRecent library context: {json.dumps(library_context, ensure_ascii=False)}"
    )
    try:
        parsed = _extract_json(await _collect_llm(
            "You are a careful academic search-query planner.", prompt, timeout=90.0
        ))
    except Exception as exc:
        logger.warning("AI 검색어 확장 실패, 원문 검색어 사용: %s", exc)
        return fallback

    return _normalise_plan(parsed, question, library_context)


def _stable_id(candidate: dict) -> str:
    value = candidate.get("doi") or candidate.get("url") or candidate.get("title") or ""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:160]


async def search_openalex(
    search_query: str,
    *,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    open_access: bool = False,
    sort: str = "relevance",
) -> list[dict]:
    params = {"search": search_query, "per-page": _MAX_RESULTS}
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if open_access:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)
    if sort == "newest":
        params["sort"] = "publication_date:desc"
    elif sort == "cited":
        params["sort"] = "cited_by_count:desc"
    mailto = get_openalex_mailto()
    if mailto:
        params["mailto"] = mailto

    headers = {"User-Agent": "EasyPaper/0.1 (AI paper search)"}
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        response = await client.get(_OPENALEX_WORKS_URL, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"OpenAlex 검색 오류 (HTTP {response.status_code})")

    results = []
    for work in response.json().get("results") or []:
        candidate = _openalex_candidate(work)
        if not candidate:
            continue
        candidate["id"] = _stable_id(candidate)
        candidate["type"] = (work.get("type") or "").replace("_", " ")
        candidate["publication_date"] = work.get("publication_date") or ""
        candidate["is_retracted"] = bool(work.get("is_retracted"))
        if candidate["is_retracted"]:
            continue
        results.append(candidate)
    return results


async def _wait_for_semantic_scholar_slot() -> None:
    """승인된 계정 제한(누적 1 req/s)보다 여유 있게 요청 시작을 직렬화한다."""
    global _s2_last_request_started
    async with _s2_rate_lock:
        loop = asyncio.get_running_loop()
        remaining = _S2_MIN_INTERVAL_SECONDS - (loop.time() - _s2_last_request_started)
        if remaining > 0:
            await asyncio.sleep(remaining)
        # 데스크톱 백엔드와 systemd 사용자 타이머가 동시에 실행될 수도 있다.
        # Linux에서는 같은 타임스탬프 파일을 잠가 프로세스 사이에서도 승인된
        # 누적 1 req/s 한도를 지킨다.
        if os.name == "posix":
            await asyncio.to_thread(_reserve_cross_process_s2_slot)
        _s2_last_request_started = loop.time()


def _reserve_cross_process_s2_slot() -> None:
    try:
        import fcntl
        config_dir = os.getenv("EASYPAPER_CONFIG_DIR") or os.path.dirname(os.path.dirname(__file__))
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, ".semantic-scholar-rate-limit")
        with open(path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                previous = float(handle.read().strip() or 0)
            except ValueError:
                previous = 0
            remaining = _S2_MIN_INTERVAL_SECONDS - (time.time() - previous)
            if remaining > 0:
                time.sleep(remaining)
            handle.seek(0); handle.truncate(); handle.write(str(time.time())); handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        logger.debug("프로세스 간 Semantic Scholar 속도 제한 파일 사용 실패: %s", exc)


async def search_semantic_scholar(
    search_query: str,
    *,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    open_access: bool = False,
) -> list[dict]:
    api_key = get_semantic_scholar_api_key().strip()
    if not api_key:
        return []

    params = {
        "query": search_query.replace("-", " "),
        "limit": _MAX_RESULTS,
        "fields": (
            "title,url,abstract,authors,year,venue,citationCount,externalIds,"
            "openAccessPdf,publicationDate,publicationTypes,fieldsOfStudy,tldr"
        ),
    }
    if year_from or year_to:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    if open_access:
        params["openAccessPdf"] = ""

    await _wait_for_semantic_scholar_slot()
    headers = {"x-api-key": api_key, "User-Agent": "PaperResearchWorkspace/2.4"}
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        response = await client.get(_SEMANTIC_SCHOLAR_SEARCH_URL, params=params)
    if response.status_code == 429:
        await asyncio.sleep(2.0)
        await _wait_for_semantic_scholar_slot()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
            response = await client.get(_SEMANTIC_SCHOLAR_SEARCH_URL, params=params)
        if response.status_code == 429:
            raise RuntimeError("Semantic Scholar 요청 한도에 도달했습니다.")
    if response.status_code != 200:
        raise RuntimeError(f"Semantic Scholar 검색 오류 (HTTP {response.status_code})")

    return [
        candidate for paper in (response.json().get("data") or [])
        if (candidate := _semantic_scholar_candidate(paper))
    ]


def _semantic_scholar_candidate(paper: dict) -> Optional[dict]:
    title = (paper.get("title") or "").strip()
    if not title:
        return None
    external_ids = paper.get("externalIds") or {}
    doi_value = str(external_ids.get("DOI") or "").removeprefix("https://doi.org/")
    doi = f"https://doi.org/{doi_value}" if doi_value else ""
    open_pdf = paper.get("openAccessPdf") or {}
    url = paper.get("url") or doi
    if not url:
        return None
    tldr = (paper.get("tldr") or {}).get("text") or ""
    candidate = {
        "title": title,
        "url": url,
        "pdf_url": open_pdf.get("url") or "",
        "year": paper.get("year"),
        "authors": [
            author.get("name") for author in (paper.get("authors") or [])
            if author.get("name")
        ][:12],
        "venue": paper.get("venue") or "",
        "abstract": paper.get("abstract") or tldr,
        "citation_count": paper.get("citationCount"),
        "doi": doi,
        "is_open_access": bool(open_pdf.get("url")),
        "source": "Semantic Scholar",
        "semantic_scholar_id": paper.get("paperId") or "",
        "publication_date": paper.get("publicationDate") or "",
        "type": ", ".join(paper.get("publicationTypes") or []),
    }
    candidate["id"] = _stable_id(candidate)
    return candidate


async def recommend_semantic_scholar(
    positive_ids: list[str],
    negative_ids: Optional[list[str]] = None,
    *,
    limit: int = 25,
) -> list[dict]:
    """Semantic Scholar의 전용 추천 모델을 긍정·부정 논문 ID로 호출한다."""
    api_key = get_semantic_scholar_api_key().strip()
    positive_ids = list(dict.fromkeys(value for value in positive_ids if value))[:100]
    negative_ids = list(dict.fromkeys(value for value in (negative_ids or []) if value))[:100]
    if not api_key or not positive_ids:
        return []

    fields = (
        "title,url,abstract,authors,year,venue,citationCount,externalIds,"
        "openAccessPdf,publicationDate,publicationTypes"
    )
    headers = {"x-api-key": api_key, "User-Agent": "PaperResearchWorkspace/2.5"}
    await _wait_for_semantic_scholar_slot()
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        if len(positive_ids) == 1 and not negative_ids:
            url = f"{_SEMANTIC_SCHOLAR_RECOMMEND_URL}forpaper/{positive_ids[0]}"
            response = await client.get(url, params={"limit": limit, "fields": fields, "from": "recent"})
        else:
            response = await client.post(
                _SEMANTIC_SCHOLAR_RECOMMEND_URL,
                params={"limit": limit, "fields": fields},
                json={"positivePaperIds": positive_ids, "negativePaperIds": negative_ids},
            )
    if response.status_code == 429:
        await asyncio.sleep(2.0)
        await _wait_for_semantic_scholar_slot()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
            if len(positive_ids) == 1 and not negative_ids:
                url = f"{_SEMANTIC_SCHOLAR_RECOMMEND_URL}forpaper/{positive_ids[0]}"
                response = await client.get(url, params={"limit": limit, "fields": fields, "from": "recent"})
            else:
                response = await client.post(
                    _SEMANTIC_SCHOLAR_RECOMMEND_URL,
                    params={"limit": limit, "fields": fields},
                    json={"positivePaperIds": positive_ids, "negativePaperIds": negative_ids},
                )
        if response.status_code == 429:
            raise RuntimeError("Semantic Scholar 요청 한도에 도달했습니다.")
    if response.status_code != 200:
        raise RuntimeError(f"Semantic Scholar 추천 오류 (HTTP {response.status_code})")
    return [
        candidate for paper in (response.json().get("recommendedPapers") or [])
        if (candidate := _semantic_scholar_candidate(paper))
    ]


def _term_match_score(candidate: dict, ranking_terms: list[str]) -> float:
    title = (candidate.get("title") or "").lower()
    abstract = (candidate.get("abstract") or "").lower()
    score = 0.0
    for term in ranking_terms:
        term = term.lower().strip()
        if not term:
            continue
        if term in title:
            score += 0.018
        elif term in abstract:
            score += 0.004
    # For Gaussian occupancy searches, co-occurrence is the key discriminator
    # against Gaussian trajectory models and unrelated occupancy forecasting.
    if "gaussian" in ranking_terms and "occupancy" in ranking_terms:
        combined = f"{title} {abstract}"
        if "gaussian" in combined and "occupan" in combined:
            score += 0.025
    return score


def merge_ranked_results(
    result_sets: list[list[dict]],
    sort: str = "relevance",
    ranking_terms: Optional[list[str]] = None,
) -> list[dict]:
    """Fuse multiple relevance lists using Reciprocal Rank Fusion (RRF).

    Results repeated across independent query formulations receive a higher score,
    which is much more robust than trusting one LLM rewrite or one lexical ranking.
    """
    merged: dict[str, dict] = {}
    for result_set in result_sets:
        seen_in_query = set()
        for rank, candidate in enumerate(result_set, 1):
            candidate_id = candidate["id"]
            title_key = re.sub(r"[^a-z0-9]+", " ", (candidate.get("title") or "").lower()).strip()
            merge_key = title_key or candidate_id
            if merge_key in seen_in_query:
                continue
            seen_in_query.add(merge_key)
            if merge_key not in merged:
                merged[merge_key] = {
                    **candidate,
                    "sources": [candidate.get("source")] if candidate.get("source") else [],
                    "_rrf_score": 0.0,
                    "_query_hits": 0,
                }
            else:
                existing = merged[merge_key]
                for field in ("pdf_url", "abstract", "venue", "doi", "semantic_scholar_id"):
                    if not existing.get(field) and candidate.get(field):
                        existing[field] = candidate[field]
                source = candidate.get("source")
                if source and source not in existing["sources"]:
                    existing["sources"].append(source)
            merged[merge_key]["_rrf_score"] += 1.0 / (_RRF_K + rank)
            merged[merge_key]["_query_hits"] += 1

    ranking_terms = [term.lower() for term in (ranking_terms or [])]
    relevance_ranked = sorted(
        merged.values(),
        key=lambda item: (
            item["_rrf_score"] + _term_match_score(item, ranking_terms),
            item["_query_hits"], item.get("citation_count") or 0,
        ),
        reverse=True,
    )[:_MAX_RESULTS]
    if sort == "newest":
        relevance_ranked.sort(key=lambda item: (item.get("year") or 0, item["_rrf_score"]), reverse=True)
    elif sort == "cited":
        relevance_ranked.sort(key=lambda item: (item.get("citation_count") or 0, item["_rrf_score"]), reverse=True)
    top_score = max((item.get("_rrf_score", 0.0) + _term_match_score(item, ranking_terms) for item in relevance_ranked), default=1.0)
    for item in relevance_ranked:
        raw_score = item.get("_rrf_score", 0.0) + _term_match_score(item, ranking_terms)
        item["relevance_score"] = max(0, min(100, round(100 * raw_score / top_score)))
        item.pop("_rrf_score", None)
        item.pop("_query_hits", None)
        item["source"] = " + ".join(item.get("sources") or [])
    return relevance_ranked


def _fallback_explanation(result: dict) -> str:
    abstract = re.sub(r"\s+", " ", result.get("abstract") or "").strip()
    if abstract:
        sentence = re.split(r"(?<=[.!?])\s+", abstract)[0]
        return sentence[:260]
    return "초록이 제공되지 않아 제목과 서지정보를 중심으로 확인해야 합니다."


def _abstract_sentences(result: dict, limit: int = 8) -> list[str]:
    abstract = re.sub(r"\s+", " ", result.get("abstract") or "").strip()
    if not abstract:
        return []
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", abstract)
        if sentence.strip()
    ]
    return sentences[:limit] or [abstract[:500]]


def _fallback_highlight(result: dict) -> str:
    sentences = _abstract_sentences(result, limit=1)
    return sentences[0][:500] if sentences else ""


async def explain_results(question: str, results: list[dict]) -> tuple[str, dict, dict, bool]:
    if not results:
        return "조건에 맞는 논문을 찾지 못했습니다. 검색어 또는 필터를 바꿔보세요.", {}, {}, False

    compact = []
    for index, item in enumerate(results[:12], 1):
        compact.append({
            "index": index,
            "title": item.get("title"),
            "year": item.get("year"),
            "citation_count": item.get("citation_count"),
            "abstract": (item.get("abstract") or "")[:900],
            "sentences": _abstract_sentences(item),
        })
    prompt = (
        "아래 검색 결과만 근거로 사용하세요. 검색 결과에 없는 논문이나 사실을 추가하지 마세요. "
        "질문에 대한 전체 검색 요약 2~4문장과, 각 논문이 질문과 관련된 이유 한 문장을 한국어로 작성하세요. "
        "각 논문의 sentences 중 질문과 가장 직접적으로 관련된 문장 번호도 하나 선택하세요. 문장을 새로 만들지 마세요. "
        "검색 요약의 모든 구체적인 주장 뒤에는 근거가 된 검색 결과 번호를 [1] 또는 [1][3] 형태로 표시하세요. "
        'JSON만 반환하세요: {"answer":"...","relevance":{"1":"...","2":"..."},'
        '"highlights":{"1":1,"2":2}}.\n\n'
        f"질문: {question}\n검색 결과: {json.dumps(compact, ensure_ascii=False)}"
    )
    try:
        parsed = _extract_json(await _collect_llm(
            "당신은 제공된 학술 검색 결과를 근거 중심으로 요약하는 연구 도우미입니다.",
            prompt,
        ))
        answer = str((parsed or {}).get("answer") or "").strip()
        raw_relevance = (parsed or {}).get("relevance") or {}
        relevance = {
            results[int(key) - 1]["id"]: str(value).strip()
            for key, value in raw_relevance.items()
            if str(key).isdigit() and 1 <= int(key) <= min(len(results), 12) and str(value).strip()
        }
        highlights = {}
        for key, value in ((parsed or {}).get("highlights") or {}).items():
            if not str(key).isdigit() or not str(value).isdigit():
                continue
            result_index = int(key) - 1
            sentence_index = int(value) - 1
            if not 0 <= result_index < min(len(results), 12):
                continue
            sentences = _abstract_sentences(results[result_index])
            if 0 <= sentence_index < len(sentences):
                highlights[results[result_index]["id"]] = sentences[sentence_index][:500]
        if answer:
            return answer, relevance, highlights, True
    except Exception as exc:
        logger.warning("AI 검색 결과 설명 실패: %s", exc)

    return (
        f"‘{question}’와 관련성이 높은 검증된 학술 레코드 {len(results)}건을 찾았습니다. "
        "아래 제목과 초록을 확인해 연구 범위에 맞는 논문을 선택하세요.",
        {},
        {},
        False,
    )


async def search_papers(
    question: str,
    *,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    open_access: bool = False,
    sort: str = "relevance",
    library_context: Optional[list[str]] = None,
) -> dict:
    plan = await plan_query(question, library_context=library_context)
    openalex_searches = await asyncio.gather(*(
        search_openalex(
            query, year_from=year_from, year_to=year_to,
            open_access=open_access, sort="relevance",
        )
        for query in plan["queries"]
    ), return_exceptions=True)
    successful = []
    for query, result in zip(plan["queries"], openalex_searches):
        if isinstance(result, Exception):
            logger.warning("OpenAlex 다중 검색 실패 (%s): %s", query, result)
        else:
            successful.append(result)

    semantic_scholar_used = False
    if get_semantic_scholar_api_key().strip():
        semantic_searches = await asyncio.gather(*(
            search_semantic_scholar(
                query, year_from=year_from, year_to=year_to, open_access=open_access,
            )
            for query in plan["queries"]
        ), return_exceptions=True)
        for query, result in zip(plan["queries"], semantic_searches):
            if isinstance(result, Exception):
                logger.warning("Semantic Scholar 다중 검색 실패 (%s): %s", query, result)
            else:
                successful.append(result)
                semantic_scholar_used = True
    if not successful:
        raise RuntimeError("OpenAlex의 모든 검색 요청이 실패했습니다.")
    ranking_terms = list(plan["keywords"])
    if _domain_query_hints(question, library_context or []):
        ranking_terms.extend(["gaussian", "occupancy", "forecast", "3d", "4d"])
    results = merge_ranked_results(successful, sort=sort, ranking_terms=ranking_terms)
    answer, explanations, highlights, ai_used = await explain_results(question, results)
    for item in results:
        item["relevance"] = explanations.get(item["id"]) or _fallback_explanation(item)
        item["highlight"] = highlights.get(item["id"]) or _fallback_highlight(item)
        item["abstract"] = (item.get("abstract") or "")[:1600]
    return {
        "question": question,
        "search_query": plan["search_query"],
        "search_queries": plan["queries"],
        "keywords": plan["keywords"],
        "interpretation": plan["interpretation"],
        "domain": plan["domain"],
        "answer": answer,
        "ai_used": ai_used,
        "results": results,
        "total": len(results),
        "searched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source": "OpenAlex + Semantic Scholar" if semantic_scholar_used else "OpenAlex",
    }


def _title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", (value or "").lower()).strip()


_TASTE_STOPWORDS = {
    "the", "and", "for", "with", "from", "using", "based", "via", "towards",
    "paper", "method", "model", "models", "learning", "approach", "study", "new",
    "대한", "위한", "사용한", "기반", "방법", "모델", "연구", "논문",
}


def _taste_terms(value: str) -> set[str]:
    tokens = [
        token for token in re.findall(r"[a-z0-9가-힣]+", (value or "").lower())
        if len(token) >= 3 and token not in _TASTE_STOPWORDS
    ]
    terms = set(tokens)
    terms.update(f"{left} {right}" for left, right in zip(tokens, tokens[1:]))
    return terms


def _build_taste_profile(
    library_documents: list[dict], feedback: list[dict], bookmarks: list[dict]
) -> tuple[Counter, int]:
    profile: Counter = Counter()
    samples = 0

    def add(text: str, weight: float) -> None:
        nonlocal samples
        terms = _taste_terms(text)
        if not terms:
            return
        samples += 1
        normalised = weight / math.sqrt(len(terms))
        for term in terms:
            profile[term] += normalised

    for document in library_documents[:80]:
        metadata = document.get("metadata") or {}
        add(" ".join(filter(None, [
            str(metadata.get("title") or document.get("filename") or ""),
            " ".join(metadata.get("categories") or []),
        ])), 0.45)
    for row in bookmarks:
        paper = row.get("paper") or {}
        add(f"{paper.get('title') or ''} {paper.get('abstract') or ''}", 0.8)
    for row in feedback:
        paper = row.get("paper") or {}
        add(
            f"{paper.get('title') or ''} {paper.get('abstract') or ''}",
            1.35 if row.get("rating", 0) > 0 else -1.55,
        )
    return profile, samples


def _apply_taste_model(results: list[dict], profile: Counter, samples: int) -> None:
    for paper in results:
        terms = _taste_terms(f"{paper.get('title') or ''} {paper.get('abstract') or ''}")
        raw = sum(profile.get(term, 0.0) for term in terms)
        taste_score = round(50 + 48 * math.tanh(raw / 2.5)) if profile else 50
        retrieval_score = float(paper.get("relevance_score", 50) or 50)
        # 명시적 평가가 쌓일수록 취향 모델의 비중을 높이되, 검색 근거를 완전히
        # 덮어쓰지는 않는다.
        taste_weight = min(0.55, 0.20 + samples * 0.015)
        paper["taste_score"] = max(0, min(100, taste_score))
        paper["personalized_score"] = round(
            retrieval_score * (1 - taste_weight) + paper["taste_score"] * taste_weight,
        )


async def discover_feed(
    library_documents: list[dict],
    feedback: list[dict],
    *,
    mode: str = "recommended",
    impressions: Optional[list[dict]] = None,
    bookmarks: Optional[list[dict]] = None,
    last_visit_at: Optional[str] = None,
    cached_results: Optional[list[dict]] = None,
) -> dict:
    """보관함과 명시적 평가를 연구 관심사로 사용해 개인화 후보를 만든다.

    한 사용자용 데스크톱에서는 별도 상시 학습 서버보다, 긍정 신호를 검색
    문맥에 포함하고 부정 신호를 제외하는 방식이 투명하고 재현 가능하다.
    """
    existing_titles = set()
    context = []
    positive_s2_ids = []
    for document in library_documents[:20]:
        metadata = document.get("metadata") or {}
        title = metadata.get("title") or document.get("filename") or ""
        if title:
            existing_titles.add(_title_key(title))
        categories = ", ".join(metadata.get("categories") or [])
        context.append(f"{title} ({categories})" if categories else title)
        if metadata.get("semantic_scholar_id"):
            positive_s2_ids.append(str(metadata["semantic_scholar_id"]))

    ratings = {item["paper_id"]: item["rating"] for item in feedback}
    negative_s2_ids = []
    for item in feedback:
        paper = item.get("paper") or {}
        s2_id = paper.get("semantic_scholar_id")
        if item["rating"] > 0:
            title = paper.get("title") or ""
            if title:
                context.insert(0, title)
            if s2_id:
                positive_s2_ids.append(str(s2_id))
        elif s2_id:
            negative_s2_ids.append(str(s2_id))

    bookmark_ids = set()
    for item in bookmarks or []:
        paper = item.get("paper") or {}
        if paper.get("id"):
            bookmark_ids.add(str(paper["id"]))
        if paper.get("semantic_scholar_id"):
            positive_s2_ids.append(str(paper["semantic_scholar_id"]))
        if paper.get("title"):
            context.insert(0, str(paper["title"]))

    context = [value for value in context if value][:20]
    if not context:
        return {
            "question": "", "answer": "보관함에 논문을 추가하거나 관심 논문을 평가하면 맞춤 추천이 시작됩니다.",
            "results": [], "total": 0, "source": "OpenAlex", "ai_used": False,
            "keywords": [], "search_queries": [], "interpretation": "",
        }

    current_year = datetime.now().year
    if mode in ("latest", "catchup"):
        question = "내 연구 관심사와 관련된 가장 최근의 새 논문"
        year_from = current_year - 1
    else:
        question = "내 연구 보관함의 주제와 방법론에 밀접하게 관련된 후속 연구"
        year_from = current_year - 3

    data = await search_papers(
        question,
        year_from=year_from,
        sort="newest" if mode == "latest" else "relevance",
        library_context=context,
    )
    try:
        model_recommendations = await recommend_semantic_scholar(
            positive_s2_ids, negative_s2_ids, limit=_MAX_RESULTS,
        )
    except Exception as exc:
        logger.warning("Semantic Scholar 맞춤 추천 실패: %s", exc)
        model_recommendations = []
    candidate_sets = []
    if model_recommendations:
        candidate_sets.append(model_recommendations)
    if cached_results:
        candidate_sets.append(cached_results)
    candidate_sets.append(data.get("results") or [])
    if len(candidate_sets) > 1:
        data["results"] = merge_ranked_results(
            candidate_sets,
            sort="newest" if mode in ("latest", "catchup") else "relevance",
        )
        if model_recommendations:
            data["recommendation_model"] = "Semantic Scholar embeddings + explicit feedback"
        else:
            data["recommendation_model"] = "scheduled discovery cache + library context"
    else:
        data["recommendation_model"] = "library context + explicit feedback"

    taste_profile, taste_samples = _build_taste_profile(
        library_documents, feedback, bookmarks or [],
    )
    _apply_taste_model(data.get("results") or [], taste_profile, taste_samples)
    data["taste_model_samples"] = taste_samples

    now = datetime.now().astimezone()
    recently_seen = set()
    hidden_ids = set()
    for item in impressions or []:
        if item.get("hidden_at"):
            hidden_ids.add(str(item.get("paper_id")))
        try:
            seen_at = datetime.fromisoformat(str(item.get("last_seen_at") or ""))
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=now.tzinfo)
            if (now - seen_at.astimezone(now.tzinfo)).days < 90:
                recently_seen.add(str(item.get("paper_id")))
        except (TypeError, ValueError):
            continue

    visit_date = ""
    if last_visit_at:
        try:
            visit_date = datetime.fromisoformat(last_visit_at).date().isoformat()
        except ValueError:
            visit_date = ""
    filtered = []
    repeated = []
    for paper in data.get("results") or []:
        if _title_key(paper.get("title") or "") in existing_titles:
            continue
        paper_id = str(paper.get("id") or "")
        if paper_id in hidden_ids:
            continue
        if mode in ("latest", "catchup") and visit_date:
            publication_date = str(paper.get("publication_date") or "")
            if publication_date and publication_date < visit_date:
                continue
        rating = ratings.get(paper.get("id"), 0)
        if rating < 0:
            continue
        paper["rating"] = rating
        paper["bookmarked"] = paper_id in bookmark_ids
        paper["relevance"] = paper.get("relevance") or _fallback_explanation(paper)
        paper["highlight"] = paper.get("highlight") or _fallback_highlight(paper)
        if paper_id in recently_seen:
            repeated.append(paper)
        else:
            filtered.append(paper)
    # 새 후보가 한 건도 없을 때 빈 화면을 만드는 대신 마지막 추천을 다시
    # 보여준다. 새 후보가 있으면 반복 노출 억제를 그대로 유지한다.
    if not filtered:
        filtered = repeated
    if mode in ("latest", "catchup"):
        filtered.sort(
            key=lambda item: (item.get("publication_date") or str(item.get("year") or "")),
            reverse=True,
        )
    else:
        filtered.sort(
            key=lambda item: (
                item.get("personalized_score") or 0,
                item.get("publication_date") or str(item.get("year") or ""),
            ),
            reverse=True,
        )
    data["results"] = filtered
    data["total"] = len(filtered)
    data["feed_mode"] = mode
    feed_label = "놓친 기간의 논문" if mode == "catchup" else ("최근 발표 논문" if mode == "latest" else "맞춤 논문")
    data["answer"] = (
        f"보관함·북마크·관심 평가를 기준으로 {feed_label} "
        f"{len(filtered)}편을 골랐습니다. 평가할수록 다음 추천에 반영됩니다."
    )
    data["last_visit_at"] = last_visit_at
    return data
