"""
파싱된 참고문헌 텍스트를 OpenAlex API로 검색해 실제 논문 링크(가능한 경우 arXiv
링크 우선)를 찾는다. 가입/API 키가 전혀 필요 없는 공개 엔드포인트를 사용하며,
실패/미매칭 시 예외를 던지지 않고 None을 반환해 호출부(참고문헌 링크 연결
기능 전체)가 이 한 건의 실패로 죽지 않게 한다.

원래는 Semantic Scholar를 썼으나, 그 쪽 공개(API 키 없는) 검색 엔드포인트는
IP 기준 공유 레이트리밋이 꽤 엄격해서(실제로 이 프로젝트 배포 환경에서도
반복적으로 429 Too Many Requests를 받았음) 셀프호스팅 환경에서 안정적으로
쓰기 어려웠다. OpenAlex는 API 키/가입 절차 자체가 없고 한도도 훨씬 넉넉해
셀프호스팅 배포에 더 적합하다. config.get_openalex_mailto()에 연락 가능한
이메일을 설정하면(선택 사항) 더 여유로운 한도의 "polite pool"로 분류된다.
"""

import logging
import re
from typing import Optional

import httpx

from config import get_openalex_mailto

logger = logging.getLogger(__name__)

_OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_REQUEST_TIMEOUT_SECONDS = 10.0

# IEEE 등 학술 인용 스타일은 흔히 제목을 따옴표로 감싼다(예: Author, "Title," Venue,
# year.). 저자명·학회명·권/호/페이지 번호까지 통째로 검색하면 관련 없는 논문이
# 최상위로 매칭되는 경우가 실측으로 확인되어(예: "Deep residual learning for image
# recognition" 전체 인용문 검색 시 완전히 무관한 논문이 1위로 나옴, 제목만 검색하면
# 정확히 매칭됨), 따옴표로 감싸진 제목이 있으면 그 부분만 검색어로 쓴다.
_QUOTED_TITLE_RE = re.compile(r'["“]([^"”]{8,300})["”]')


def _extract_search_query(citation_text: str) -> str:
    """인용 원문에서 검색어로 쓸 부분을 뽑는다. 따옴표로 감싼 제목이 있으면 그것만
    쓰고, 없으면(따옴표 없이 저자 뒤에 바로 제목이 오는 스타일 등, 신뢰할 만하게
    분리할 방법이 없음) 원문 전체를 그대로 쓴다."""
    m = _QUOTED_TITLE_RE.search(citation_text)
    if m:
        return m.group(1).strip().rstrip(",.")
    return citation_text


def _pick_url(work: dict) -> Optional[str]:
    """work 메타데이터에서 표시할 링크를 고른다. 원문을 무료로 바로 볼 수 있는
    arXiv 링크를 최우선으로 하고, 그다음은 오픈 액세스 링크, 대표 링크, DOI
    순으로 폴백한다."""
    for location in work.get("locations") or []:
        source = location.get("source") or {}
        display_name = (source.get("display_name") or "").lower()
        if "arxiv" in display_name and location.get("landing_page_url"):
            return location["landing_page_url"]

    open_access = work.get("open_access") or {}
    if open_access.get("oa_url"):
        return open_access["oa_url"]

    primary_location = work.get("primary_location") or {}
    if primary_location.get("landing_page_url"):
        return primary_location["landing_page_url"]

    return work.get("doi") or work.get("id") or None


def _pick_pdf_url(work: dict) -> Optional[str]:
    """OpenAlex location 중 실제 PDF 파일 주소를 고른다. arXiv를 우선하고,
    없으면 공개된 location의 pdf_url을 사용한다."""
    locations = work.get("locations") or []
    for location in locations:
        source = location.get("source") or {}
        if "arxiv" in (source.get("display_name") or "").lower() and location.get("pdf_url"):
            return location["pdf_url"]
    for location in locations:
        if location.get("pdf_url") and (
            location.get("is_oa") or (work.get("open_access") or {}).get("is_oa")
        ):
            return location["pdf_url"]

    # 일부 OpenAlex 레코드는 arXiv landing page만 제공한다. 알려진 arXiv
    # 주소에 한해서 abs URL을 PDF URL로 안전하게 변환한다.
    url = _pick_url(work) or ""
    match = re.match(r"^https?://(?:www\.)?arxiv\.org/abs/([^?#]+)", url)
    if match:
        return f"https://arxiv.org/pdf/{match.group(1)}.pdf"
    return None


def _restore_abstract(inverted_index: Optional[dict]) -> str:
    """Restore OpenAlex's inverted abstract index into readable text."""
    if not inverted_index:
        return ""
    positioned = []
    for word, positions in inverted_index.items():
        for position in positions or []:
            positioned.append((position, word))
    positioned.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned)[:1600]


async def resolve_reference(query_text: str) -> Optional[dict]:
    """참고문헌 원문 텍스트로 OpenAlex를 검색해 가장 관련성 높은 논문의
    제목/링크/연도를 반환합니다. 매칭 실패나 네트워크 오류 시 None.
    """
    raw = (query_text or "").strip()
    if not raw:
        return None
    query = _extract_search_query(raw)[:300]

    params = {"search": query, "per_page": 1}
    mailto = get_openalex_mailto()
    if mailto:
        params["mailto"] = mailto

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(_OPENALEX_WORKS_URL, params=params)

            if resp.status_code != 200:
                logger.warning(f"OpenAlex 검색 실패: status={resp.status_code}")
                return None

            data = resp.json()
            works = data.get("results") or []
            if not works:
                return None

            work = works[0]
            url = _pick_url(work)
            if not url:
                return None

            authors = []
            for authorship in work.get("authorships") or []:
                author = authorship.get("author") or {}
                name = author.get("display_name")
                if name:
                    authors.append(name)

            primary_location = work.get("primary_location") or {}
            source = primary_location.get("source") or {}

            return {
                "title": work.get("title") or work.get("display_name") or "",
                "url": url,
                "pdf_url": _pick_pdf_url(work) or "",
                "year": work.get("publication_year"),
                "authors": authors[:12],
                "venue": source.get("display_name") or "",
                "abstract": _restore_abstract(work.get("abstract_inverted_index")),
                "citation_count": work.get("cited_by_count"),
                "doi": work.get("doi") or "",
                "is_open_access": bool((work.get("open_access") or {}).get("is_oa")),
            }
    except Exception as e:
        logger.warning(f"참고문헌 검색 중 오류: {e}")
        return None
