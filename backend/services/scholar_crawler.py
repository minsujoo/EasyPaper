"""Persist a lightweight personal Scholar digest on a regular schedule.

The crawler intentionally reuses the same verified OpenAlex/Semantic Scholar
records as interactive search.  It does not fabricate papers and it avoids the
LLM query-planning path so a background refresh cannot consume model credits.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from config import get_app_username, get_semantic_scholar_api_key
from services.db import (
    db_get_scholar_feed_state,
    db_list_scholar_bookmarks,
    db_list_scholar_digest_cache,
    db_list_scholar_feedback,
    db_save_scholar_digest_cache,
    db_touch_scholar_feed,
)
from services.library import list_documents
from services.paper_search import (
    _title_key,
    merge_ranked_results,
    recommend_semantic_scholar,
    search_openalex,
    search_semantic_scholar,
)

logger = logging.getLogger(__name__)

_crawl_lock = asyncio.Lock()
_LOOP_CHECK_SECONDS = 60 * 60
_STARTUP_DELAY_SECONDS = 20


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _crawl_is_due(state: dict, *, now: datetime | None = None) -> bool:
    last_crawl = _parse_datetime(state.get("last_crawl_at"))
    if last_crawl is None:
        return True
    interval = max(1, int(state.get("crawl_interval_hours") or 24))
    return (now or datetime.now(timezone.utc)) - last_crawl >= timedelta(hours=interval)


def _profile(documents: list[dict], feedback: list[dict], bookmarks: list[dict]) -> tuple[list[str], list[str], str]:
    positive_ids: list[str] = []
    negative_ids: list[str] = []
    seed_terms: list[str] = []

    for document in documents[:30]:
        metadata = document.get("metadata") or {}
        if metadata.get("semantic_scholar_id"):
            positive_ids.append(str(metadata["semantic_scholar_id"]))
        seed_terms.extend(str(value) for value in (metadata.get("categories") or []) if value)
        title = metadata.get("title") or document.get("filename")
        if title:
            seed_terms.append(str(title))

    for row in feedback:
        paper = row.get("paper") or {}
        s2_id = paper.get("semantic_scholar_id")
        if s2_id and row.get("rating", 0) > 0:
            positive_ids.append(str(s2_id))
        elif s2_id and row.get("rating", 0) < 0:
            negative_ids.append(str(s2_id))
        if row.get("rating", 0) > 0 and paper.get("title"):
            seed_terms.insert(0, str(paper["title"]))

    for row in bookmarks:
        paper = row.get("paper") or {}
        if paper.get("semantic_scholar_id"):
            positive_ids.append(str(paper["semantic_scholar_id"]))
        if paper.get("title"):
            seed_terms.insert(0, str(paper["title"]))

    # A title is a useful lexical fallback when imported records predate S2 IDs.
    seed_query = next((term for term in seed_terms if len(term.strip()) >= 3), "")[:300]
    return list(dict.fromkeys(positive_ids))[:100], list(dict.fromkeys(negative_ids))[:100], seed_query


async def refresh_scholar_cache(username: str, *, force: bool = False) -> dict:
    """Refresh a user's persisted digest if the configured interval has elapsed."""
    async with _crawl_lock:
        state = db_get_scholar_feed_state(username)
        cached = db_list_scholar_digest_cache(username)
        if not force and not _crawl_is_due(state):
            return {"refreshed": False, "reason": "not_due", "cached": len(cached), **state}

        documents = list_documents(username=username)
        feedback = db_list_scholar_feedback(username)
        bookmarks = db_list_scholar_bookmarks(username)
        positive_ids, negative_ids, seed_query = _profile(documents, feedback, bookmarks)
        if not positive_ids and not seed_query:
            return {"refreshed": False, "reason": "empty_profile", "cached": len(cached), **state}

        result_sets: list[list[dict]] = []
        if positive_ids and get_semantic_scholar_api_key().strip():
            try:
                result_sets.append(await recommend_semantic_scholar(
                    positive_ids, negative_ids, limit=25,
                ))
            except Exception as exc:
                logger.warning("정기 Semantic Scholar 추천 수집 실패: %s", exc)

        if seed_query:
            year_from = datetime.now().year - 1
            lexical = await asyncio.gather(
                search_openalex(seed_query, year_from=year_from, sort="newest"),
                search_semantic_scholar(seed_query, year_from=year_from)
                if get_semantic_scholar_api_key().strip() else asyncio.sleep(0, result=[]),
                return_exceptions=True,
            )
            for result in lexical:
                if isinstance(result, Exception):
                    logger.warning("정기 학술 레코드 수집 실패: %s", result)
                elif result:
                    result_sets.append(result)

        papers = merge_ranked_results(result_sets, sort="newest") if result_sets else []
        library_titles = {
            _title_key((doc.get("metadata") or {}).get("title") or doc.get("filename") or "")
            for doc in documents
        }
        papers = [paper for paper in papers if _title_key(paper.get("title") or "") not in library_titles]
        if papers:
            db_save_scholar_digest_cache(username, papers)
        new_state = db_touch_scholar_feed(username, visit=False, crawl=True)
        return {"refreshed": True, "added": len(papers), "cached": len(db_list_scholar_digest_cache(username)), **new_state}


async def scholar_crawl_loop() -> None:
    """Run while the desktop backend is alive; a missed interval catches up at startup."""
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await refresh_scholar_cache(get_app_username())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("정기 Scholar 수집을 다음 주기로 미룹니다: %s", exc)
        await asyncio.sleep(_LOOP_CHECK_SECONDS)
