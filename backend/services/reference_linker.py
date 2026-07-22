"""
파싱된 참고문헌 텍스트를 Semantic Scholar API로 검색해 실제 논문 링크(가능한
경우 arXiv 링크 우선)를 찾는다. API 키 없이 쓸 수 있는 공개 검색
엔드포인트를 사용하며, 실패/미매칭 시 예외를 던지지 않고 None을 반환해
호출부(참고문헌 링크 연결 기능 전체)가 이 한 건의 실패로 죽지 않게 한다.

주의: Semantic Scholar의 공개(API 키 없는) 검색 엔드포인트는 IP 기준
공유 레이트리밋이 꽤 엄격하다(실제로 이 프로젝트 개발 환경에서도 여러 번
429 Too Many Requests를 받았음). 429를 받으면 짧게 재시도하되, 그래도
안 되면 그 한 건만 실패로 처리하고 조용히 넘어간다 - 트래픽이 몰리는
환경(클라우드 공유 IP 등)에서는 이 기능이 간헐적으로 안 될 수 있다는
한계가 있다. 요청량이 많다면 Semantic Scholar API 키(무료 발급)를
`SEMANTIC_SCHOLAR_API_KEY` 헤더로 추가하면 훨씬 높은 한도를 받을 수 있다.
"""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_RETRIES_ON_RATE_LIMIT = 2
_RETRY_BACKOFF_SECONDS = 2.0


async def resolve_reference(query_text: str) -> Optional[dict]:
    """참고문헌 원문 텍스트로 Semantic Scholar를 검색해 가장 관련성 높은
    논문의 제목/링크/연도를 반환합니다. 매칭 실패나 네트워크 오류 시 None.
    """
    query = (query_text or "").strip()[:300]
    if not query:
        return None

    for attempt in range(_MAX_RETRIES_ON_RATE_LIMIT + 1):
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    _SEMANTIC_SCHOLAR_SEARCH_URL,
                    params={
                        "query": query,
                        "limit": 1,
                        "fields": "title,url,externalIds,year",
                    },
                )

                if resp.status_code == 429 and attempt < _MAX_RETRIES_ON_RATE_LIMIT:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue

                if resp.status_code != 200:
                    logger.warning(f"Semantic Scholar 검색 실패: status={resp.status_code}")
                    return None

                data = resp.json()
                papers = data.get("data") or []
                if not papers:
                    return None

                paper = papers[0]
                url = paper.get("url")
                external_ids = paper.get("externalIds") or {}
                arxiv_id = external_ids.get("ArXiv")
                if arxiv_id:
                    # arXiv 링크가 있으면 원문을 무료로 바로 볼 수 있어 우선한다
                    url = f"https://arxiv.org/abs/{arxiv_id}"

                if not url:
                    return None

                return {
                    "title": paper.get("title") or "",
                    "url": url,
                    "year": paper.get("year"),
                }
        except Exception as e:
            logger.warning(f"참고문헌 검색 중 오류: {e}")
            return None

    return None
