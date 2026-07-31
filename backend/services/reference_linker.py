"""참고문헌 문자열을 여러 공개 학술 메타데이터 소스에서 찾아 검증한다.

OpenAlex, Crossref, arXiv의 복수 후보를 수집한 뒤 제목·출판연도·저자 일치도를
직접 계산한다. 검색 API의 첫 결과를 그대로 쓰지 않으므로, 인용문 전체가 색인된
"현재 논문"을 실제 인용 논문으로 잘못 연결하는 문제를 막는다.
"""

import asyncio
from difflib import SequenceMatcher
import logging
import re
from typing import Optional
import xml.etree.ElementTree as ET

import httpx

from config import get_openalex_mailto

logger = logging.getLogger(__name__)

_OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_CROSSREF_WORKS_URL = "https://api.crossref.org/works"
_ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
_REQUEST_TIMEOUT_SECONDS = 12.0
_RESULTS_PER_SOURCE = 8
_MIN_MATCH_SCORE = 0.68
REFERENCE_RESOLVER_VERSION = 2

_QUOTED_TITLE_RE = re.compile(r'["“]([^"”]{8,300})["”]')
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)
_ARXIV_RE = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7})(?:\.pdf)?",
    re.I,
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_VENUE_PREFIX_RE = re.compile(
    r"^(?:in\s+|proceedings?\s+of\s+|arxiv\s+preprint|"
    r"(?:ieee|acm|springer)\s+|vol(?:ume)?\.?\s+|pp?\.?\s+|pages?\s+)",
    re.I,
)


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalise_title(value: str) -> str:
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return " ".join(_TOKEN_RE.findall(value.lower()))


def _title_similarity(expected: str, actual: str) -> float:
    left = _normalise_title(expected)
    right = _normalise_title(actual)
    if not left or not right:
        return 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = len(left_tokens & right_tokens)
    token_f1 = (2 * overlap / (len(left_tokens) + len(right_tokens))) if overlap else 0.0
    containment = 0.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 12 and shorter in longer:
        containment = min(0.97, len(shorter) / len(longer) + 0.2)
    return max(sequence, token_f1, containment)


def _citation_segments(citation_text: str) -> list[str]:
    """저자 이니셜의 마침표는 보존하면서 인용문의 의미 단위를 분리한다."""
    raw = re.sub(r"^\s*(?:\[\d+\]|\d+[.)])\s*", "", _clean_space(citation_text))
    return [
        part.strip(" ,.;")
        for part in re.split(r"(?<=[a-z0-9\)])\.\s+(?=[A-Za-z0-9“\"])", raw)
        if part.strip(" ,.;")
    ]


def _extract_search_query(citation_text: str) -> str:
    """인용문에서 가장 제목다운 구간을 골라 검색어로 사용한다."""
    if not _clean_space(citation_text):
        return ""
    quoted = _QUOTED_TITLE_RE.search(citation_text or "")
    if quoted:
        return _clean_space(quoted.group(1)).rstrip(",.")

    segments = _citation_segments(citation_text)
    if len(segments) == 1:
        return segments[0] if segments else _clean_space(citation_text)

    best = ""
    best_score = float("-inf")
    for index, segment in enumerate(segments):
        words = _TOKEN_RE.findall(segment)
        if len(words) < 3 or len(words) > 40:
            continue
        score = min(len(words), 16) / 16
        if ":" in segment:
            score += 0.22
        if index > 0:
            score += 0.28
        if index == 0 and segment.count(",") >= 2:
            score -= 1.0
        score -= min(segment.count(","), 5) * 0.12
        if _VENUE_PREFIX_RE.search(segment):
            score -= 1.2
        if _YEAR_RE.search(segment):
            score -= 0.35
        if "http://" in segment.lower() or "https://" in segment.lower():
            score -= 1.0
        if score > best_score:
            best = segment
            best_score = score
    return best or (segments[1] if len(segments) > 1 else (segments[0] if segments else ""))


def _extract_citation_metadata(citation_text: str) -> dict:
    raw = _clean_space(citation_text)
    title = _extract_search_query(raw)[:300]
    years = [int(value) for value in _YEAR_RE.findall(raw)]
    title_pos = raw.lower().find(title.lower()) if title else -1
    author_text = raw[:title_pos] if title_pos > 0 else (_citation_segments(raw)[0] if _citation_segments(raw) else "")
    author_tokens = {
        token for token in _TOKEN_RE.findall(author_text.lower())
        if len(token) >= 3 and token not in {"and", "the", "with", "from", "etal"}
    }
    doi_match = _DOI_RE.search(raw)
    arxiv_match = _ARXIV_RE.search(raw)
    return {
        "raw": raw,
        "title": title,
        "year": years[-1] if years else None,
        "author_tokens": author_tokens,
        "doi": doi_match.group(1).rstrip(".,;)") if doi_match else "",
        "arxiv_id": arxiv_match.group(1) if arxiv_match else "",
    }


def _pick_url(work: dict) -> Optional[str]:
    for location in work.get("locations") or []:
        source = location.get("source") or {}
        if "arxiv" in (source.get("display_name") or "").lower() and location.get("landing_page_url"):
            return location["landing_page_url"]
    open_access = work.get("open_access") or {}
    if open_access.get("oa_url"):
        return open_access["oa_url"]
    primary_location = work.get("primary_location") or {}
    if primary_location.get("landing_page_url"):
        return primary_location["landing_page_url"]
    return work.get("doi") or work.get("id") or None


def _https_arxiv_url(url: str) -> str:
    return re.sub(r"^http://(?:www\.)?arxiv\.org/", "https://arxiv.org/", url or "")


def _pick_pdf_url(work: dict) -> Optional[str]:
    locations = work.get("locations") or []
    for location in locations:
        source = location.get("source") or {}
        if "arxiv" in (source.get("display_name") or "").lower() and location.get("pdf_url"):
            return _https_arxiv_url(location["pdf_url"])
    for location in locations:
        if location.get("pdf_url") and (
            location.get("is_oa") or (work.get("open_access") or {}).get("is_oa")
        ):
            return location["pdf_url"]
    url = _https_arxiv_url(_pick_url(work) or "")
    match = re.match(r"^https://arxiv\.org/abs/([^?#]+)", url)
    return f"https://arxiv.org/pdf/{match.group(1)}.pdf" if match else None


def _restore_abstract(inverted_index: Optional[dict]) -> str:
    if not inverted_index:
        return ""
    positioned = [
        (position, word)
        for word, positions in inverted_index.items()
        for position in (positions or [])
    ]
    positioned.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned)[:1600]


def _openalex_candidate(work: dict) -> Optional[dict]:
    url = _pick_url(work)
    if not url:
        return None
    authors = [
        (authorship.get("author") or {}).get("display_name")
        for authorship in (work.get("authorships") or [])
    ]
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "url": _https_arxiv_url(url),
        "pdf_url": _pick_pdf_url(work) or "",
        "year": work.get("publication_year"),
        "authors": [name for name in authors if name][:12],
        "venue": source.get("display_name") or "",
        "abstract": _restore_abstract(work.get("abstract_inverted_index")),
        "citation_count": work.get("cited_by_count"),
        "doi": work.get("doi") or "",
        "is_open_access": bool((work.get("open_access") or {}).get("is_oa")),
        "source": "OpenAlex",
    }


def _crossref_year(item: dict) -> Optional[int]:
    for field in ("published-print", "published-online", "published", "issued", "created"):
        parts = ((item.get(field) or {}).get("date-parts") or [])
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                pass
    return None


def _crossref_candidate(item: dict) -> Optional[dict]:
    titles = item.get("title") or []
    title = _clean_space(titles[0] if titles else "")
    doi = item.get("DOI") or ""
    url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
    if not title or not url:
        return None
    authors = []
    for author in item.get("author") or []:
        name = _clean_space(" ".join(filter(None, [author.get("given"), author.get("family")])))
        if name:
            authors.append(name)
    pdf_url = ""
    for link in item.get("link") or []:
        candidate_url = link.get("URL") or ""
        content_type = (link.get("content-type") or "").lower()
        if candidate_url.startswith("https://") and ("pdf" in content_type or candidate_url.lower().endswith(".pdf")):
            pdf_url = candidate_url
            break
    containers = item.get("container-title") or []
    abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
    return {
        "title": title,
        "url": url,
        "pdf_url": pdf_url,
        "year": _crossref_year(item),
        "authors": authors[:12],
        "venue": _clean_space(containers[0] if containers else ""),
        "abstract": _clean_space(abstract)[:1600],
        "citation_count": item.get("is-referenced-by-count"),
        "doi": f"https://doi.org/{doi}" if doi else "",
        "is_open_access": bool(pdf_url),
        "source": "Crossref",
    }


def _xml_text(entry: ET.Element, name: str, namespace: dict) -> str:
    node = entry.find(name, namespace)
    return _clean_space(node.text if node is not None and node.text else "")


def _arxiv_candidates(xml_text: str) -> list[dict]:
    namespace = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_text)
    results = []
    for entry in root.findall("atom:entry", namespace):
        title = _xml_text(entry, "atom:title", namespace)
        abs_url = _https_arxiv_url(_xml_text(entry, "atom:id", namespace))
        if not title or not abs_url:
            continue
        authors = [
            _xml_text(author, "atom:name", namespace)
            for author in entry.findall("atom:author", namespace)
        ]
        pdf_url = ""
        for link in entry.findall("atom:link", namespace):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = _https_arxiv_url(link.attrib.get("href") or "")
                break
        published = _xml_text(entry, "atom:published", namespace)
        doi = _xml_text(entry, "arxiv:doi", namespace)
        results.append({
            "title": title,
            "url": abs_url,
            "pdf_url": pdf_url or abs_url.replace("/abs/", "/pdf/") + ".pdf",
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "authors": [name for name in authors if name][:12],
            "venue": "arXiv",
            "abstract": _xml_text(entry, "atom:summary", namespace)[:1600],
            "citation_count": None,
            "doi": f"https://doi.org/{doi}" if doi else "",
            "is_open_access": True,
            "source": "arXiv",
        })
    return results


async def _search_openalex(client: httpx.AsyncClient, metadata: dict) -> list[dict]:
    params = {"search": metadata["title"], "per_page": _RESULTS_PER_SOURCE}
    mailto = get_openalex_mailto()
    if mailto:
        params["mailto"] = mailto
    response = await client.get(_OPENALEX_WORKS_URL, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code}")
    return [
        candidate
        for work in (response.json().get("results") or [])
        if (candidate := _openalex_candidate(work))
    ]


async def _search_crossref(client: httpx.AsyncClient, metadata: dict) -> list[dict]:
    params = {"query.title": metadata["title"], "rows": _RESULTS_PER_SOURCE}
    if metadata["year"]:
        params["filter"] = f"from-pub-date:{metadata['year'] - 1},until-pub-date:{metadata['year'] + 1}"
    mailto = get_openalex_mailto()
    if mailto:
        params["mailto"] = mailto
    response = await client.get(_CROSSREF_WORKS_URL, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code}")
    items = ((response.json().get("message") or {}).get("items") or [])
    return [candidate for item in items if (candidate := _crossref_candidate(item))]


async def _search_arxiv(client: httpx.AsyncClient, metadata: dict) -> list[dict]:
    search_query = f'id:{metadata["arxiv_id"]}' if metadata["arxiv_id"] else f'ti:"{metadata["title"]}"'
    response = await client.get(
        _ARXIV_QUERY_URL,
        params={"search_query": search_query, "start": 0, "max_results": _RESULTS_PER_SOURCE},
    )
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code}")
    return _arxiv_candidates(response.text)


def _candidate_score(metadata: dict, candidate: dict) -> float:
    title_score = _title_similarity(metadata["title"], candidate.get("title") or "")
    score = title_score
    expected_year = metadata.get("year")
    actual_year = candidate.get("year")
    if expected_year and actual_year:
        delta = abs(expected_year - actual_year)
        score += 0.05 if delta == 0 else (0.02 if delta == 1 else -0.10)
    expected_authors = metadata.get("author_tokens") or set()
    actual_authors = set(_TOKEN_RE.findall(" ".join(candidate.get("authors") or []).lower()))
    if expected_authors and actual_authors:
        overlap = len(expected_authors & actual_authors)
        score += min(0.08, overlap * 0.02)
    if metadata.get("doi") and metadata["doi"].lower() in (candidate.get("doi") or "").lower():
        score += 0.25
    if metadata.get("arxiv_id") and metadata["arxiv_id"].lower() in (candidate.get("url") or "").lower():
        score += 0.25
    return score


def _deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    deduplicated = {}
    for candidate in candidates:
        key = (candidate.get("doi") or "").lower() or _normalise_title(candidate.get("title") or "")
        if not key:
            continue
        existing = deduplicated.get(key)
        if not existing:
            deduplicated[key] = candidate
            continue
        # 같은 논문을 여러 소스가 찾았다면 공개 PDF·초록이 풍부한 레코드를 유지한다.
        if bool(candidate.get("pdf_url")) > bool(existing.get("pdf_url")):
            existing, candidate = candidate, existing
            deduplicated[key] = existing
        for field in ("pdf_url", "abstract", "venue", "doi"):
            if not existing.get(field) and candidate.get(field):
                existing[field] = candidate[field]
        if len(candidate.get("authors") or []) > len(existing.get("authors") or []):
            existing["authors"] = candidate["authors"]
    return list(deduplicated.values())


async def resolve_reference(query_text: str) -> Optional[dict]:
    """참고문헌을 세 공개 소스에서 찾아 검증된 최적 후보를 반환한다."""
    metadata = _extract_citation_metadata(query_text)
    if not metadata["title"]:
        return None

    headers = {"User-Agent": "EasyPaper/0.1 (reference resolver)"}
    async with httpx.AsyncClient(
        timeout=_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=headers,
    ) as client:
        searches = await asyncio.gather(
            _search_openalex(client, metadata),
            _search_crossref(client, metadata),
            _search_arxiv(client, metadata),
            return_exceptions=True,
        )

    candidates = []
    for source_name, result in zip(("OpenAlex", "Crossref", "arXiv"), searches):
        if isinstance(result, Exception):
            logger.warning("참고문헌 %s 검색 실패: %s", source_name, result)
        else:
            candidates.extend(result)
    if not candidates:
        return None

    ranked = sorted(
        (
            (_candidate_score(metadata, candidate), candidate)
            for candidate in _deduplicate_candidates(candidates)
        ),
        key=lambda item: (item[0], bool(item[1].get("pdf_url")), len(item[1].get("abstract") or "")),
        reverse=True,
    )
    score, best = ranked[0]
    if score < _MIN_MATCH_SCORE:
        logger.info(
            "참고문헌 후보 불일치: expected=%r best=%r score=%.3f",
            metadata["title"], best.get("title"), score,
        )
        return None
    best["match_score"] = round(min(score, 1.0), 3)
    best["_resolver_version"] = REFERENCE_RESOLVER_VERSION
    return best
