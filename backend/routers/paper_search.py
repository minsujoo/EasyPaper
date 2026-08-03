import asyncio
import base64
import os
import tempfile
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from services.paper_search import discover_feed, recommend_semantic_scholar, search_papers
from services.auth import get_current_user
from services.library import list_documents
from services.db import (
    db_get_folder,
    db_get_scholar_feed_state,
    db_list_scholar_bookmarks,
    db_list_scholar_digest_cache,
    db_list_scholar_conference_watch,
    db_list_scholar_feedback,
    db_list_scholar_impressions,
    db_move_document_to_folder,
    db_link_scholar_bookmark,
    db_record_scholar_impressions,
    db_set_scholar_bookmark,
    db_set_scholar_feedback,
    db_set_scholar_interaction,
    db_set_scholar_conference_watch,
    db_touch_scholar_feed,
    db_update_document_metadata,
)

router = APIRouter()


class PaperSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    open_access: bool = False
    sort: Literal["relevance", "newest", "cited"] = "relevance"

    @model_validator(mode="after")
    def validate_years(self):
        current_year = datetime.now().year + 1
        for value in (self.year_from, self.year_to):
            if value is not None and not 1800 <= value <= current_year:
                raise ValueError("연도는 1800년부터 내년 사이여야 합니다.")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("시작 연도는 종료 연도보다 클 수 없습니다.")
        return self


class ScholarFeedbackRequest(BaseModel):
    paper_id: str = Field(min_length=2, max_length=200)
    rating: Literal[-1, 1]
    paper: dict[str, Any]


class ScholarImportRequest(BaseModel):
    paper_id: str = Field(min_length=2, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    pdf_url: str = Field(min_length=8, max_length=2000)
    url: str = Field(default="", max_length=2000)
    doi: str = Field(default="", max_length=300)
    authors: list[str] = Field(default_factory=list, max_length=100)
    year: Optional[int] = None
    venue: str = Field(default="", max_length=500)
    semantic_scholar_id: str = Field(default="", max_length=200)
    folder_id: Optional[int] = None
    target_lang: str = Field(default="한국어", max_length=80)
    style: str = Field(default="academic", max_length=40)
    ignore_math: bool = False
    ignore_table: bool = True
    ignore_refs: bool = False
    translation_mode: Literal["auto", "pane", "scroll"] = "auto"


class ScholarPreviewRequest(BaseModel):
    pdf_url: str = Field(min_length=8, max_length=2000)


class ScholarBookmarkRequest(BaseModel):
    paper_id: str = Field(min_length=2, max_length=200)
    paper: dict[str, Any]
    folder_id: Optional[int] = None
    saved: bool = True


class ScholarInteractionRequest(BaseModel):
    paper_id: str = Field(min_length=2, max_length=200)
    action: Literal["open", "hide", "unhide"]


class ScholarSimilarRequest(BaseModel):
    paper: dict[str, Any]
    limit: int = Field(default=20, ge=1, le=50)


class ScholarPaperRequest(BaseModel):
    paper: dict[str, Any]


class ScholarConferenceWatchRequest(BaseModel):
    conference_id: str = Field(min_length=2, max_length=120)
    conference: dict[str, Any]
    watched: bool = True


def _safe_scholar_paper(paper: dict[str, Any]) -> dict:
    return {
        key: paper.get(key)
        for key in (
            "id", "title", "url", "pdf_url", "doi", "year", "authors", "venue",
            "abstract", "semantic_scholar_id", "source", "sources", "publication_date",
            "citation_count", "relevance", "highlight", "relevance_score",
        )
        if paper.get(key) is not None
    }


def _annotate_downloaded(papers: list[dict], current_user: str) -> None:
    documents = list_documents(username=current_user)
    by_paper_id = {}
    by_s2_id = {}
    by_doi = {}
    by_title = {}
    for document in documents:
        metadata = document.get("metadata") or {}
        if metadata.get("scholar_paper_id"):
            by_paper_id[str(metadata["scholar_paper_id"])] = document["id"]
        if metadata.get("semantic_scholar_id"):
            by_s2_id[str(metadata["semantic_scholar_id"])] = document["id"]
        doi = str(metadata.get("doi") or "").lower().removeprefix("https://doi.org/").strip()
        if doi:
            by_doi[doi] = document["id"]
        title = _normalise_title(metadata.get("title") or document.get("filename") or "")
        if title:
            by_title[title] = document["id"]
    for paper in papers:
        doi = str(paper.get("doi") or "").lower().removeprefix("https://doi.org/").strip()
        doc_id = (
            by_paper_id.get(str(paper.get("id") or ""))
            or by_s2_id.get(str(paper.get("semantic_scholar_id") or ""))
            or (by_doi.get(doi) if doi else None)
            or by_title.get(_normalise_title(paper.get("title") or ""))
        )
        if doc_id:
            paper["downloaded"] = True
            paper["saved_document_id"] = doc_id


@router.post("/paper-search")
async def paper_search(payload: PaperSearchRequest, current_user: str = Depends(get_current_user)):
    documents = list_documents(username=current_user)
    library_context = []
    for document in documents[:12]:
        metadata = document.get("metadata") or {}
        title = metadata.get("title") or document.get("filename") or ""
        categories = ", ".join(metadata.get("categories") or [])
        library_context.append(f"{title} ({categories})" if categories else title)
    try:
        result = await search_papers(
            payload.query.strip(),
            year_from=payload.year_from,
            year_to=payload.year_to,
            open_access=payload.open_access,
            sort=payload.sort,
            library_context=library_context,
        )
        bookmarks = {
            item["paper_id"] for item in db_list_scholar_bookmarks(current_user)
        }
        for paper in result.get("results") or []:
            paper["bookmarked"] = str(paper.get("id") or "") in bookmarks
        _annotate_downloaded(result.get("results") or [], current_user)
        # 일반 검색 결과에서도 열기·숨기기 상태가 다음 실행까지 유지되도록
        # 추천 피드와 동일한 노출 기록에 포함한다.
        db_record_scholar_impressions(current_user, result.get("results") or [], "search")
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/scholar/feed")
async def scholar_feed(
    mode: Literal["recommended", "latest", "catchup", "learn"] = "recommended",
    folder_id: Optional[int] = None,
    since: Optional[str] = None,
    current_user: str = Depends(get_current_user),
):
    documents = list_documents(username=current_user)
    if folder_id is not None:
        documents = [doc for doc in documents if doc.get("folder_id") == folder_id]
    try:
        data = await discover_feed(
            documents,
            db_list_scholar_feedback(current_user),
            mode="recommended" if mode == "learn" else mode,
            impressions=db_list_scholar_impressions(current_user),
            bookmarks=db_list_scholar_bookmarks(current_user, folder_id=folder_id),
            last_visit_at=since or db_get_scholar_feed_state(current_user).get("last_visit_at"),
            cached_results=db_list_scholar_digest_cache(current_user),
        )
        if mode == "learn":
            data["results"] = sorted(
                data.get("results") or [],
                key=lambda paper: abs(float(paper.get("relevance_score", 50)) - 50),
            )[:10]
            data["total"] = len(data["results"])
            data["feed_mode"] = "learn"
            data["answer"] = "추천 모델이 아직 확신하지 못한 논문입니다. 평가하면 취향 경계가 더 정확해집니다."
        db_record_scholar_impressions(current_user, data.get("results") or [], mode)
        _annotate_downloaded(data.get("results") or [], current_user)
        db_touch_scholar_feed(current_user, visit=False, crawl=False)
        return data
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/scholar/feedback")
async def scholar_feedback(
    payload: ScholarFeedbackRequest,
    current_user: str = Depends(get_current_user),
):
    safe_paper = _safe_scholar_paper(payload.paper)
    return db_set_scholar_feedback(current_user, payload.paper_id, payload.rating, safe_paper)


@router.post("/scholar/session")
async def scholar_session(current_user: str = Depends(get_current_user)):
    previous = db_get_scholar_feed_state(current_user)
    current = db_touch_scholar_feed(current_user, visit=True)
    return {
        "previous_visit_at": previous.get("last_visit_at"),
        "session_started_at": current.get("last_visit_at"),
    }


@router.get("/scholar/crawl/status")
async def scholar_crawl_status(current_user: str = Depends(get_current_user)):
    state = db_get_scholar_feed_state(current_user)
    return {**state, "cached": len(db_list_scholar_digest_cache(current_user))}


@router.post("/scholar/crawl")
async def scholar_crawl(
    force: bool = False,
    current_user: str = Depends(get_current_user),
):
    from services.scholar_crawler import refresh_scholar_cache

    try:
        return await refresh_scholar_cache(current_user, force=force)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"새 논문 수집에 실패했습니다: {exc}") from exc


@router.get("/scholar/bookmarks")
async def scholar_bookmarks(
    folder_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
):
    rows = db_list_scholar_bookmarks(current_user, folder_id=folder_id)
    papers = [item["paper"] for item in rows]
    _annotate_downloaded(papers, current_user)
    return {"results": papers, "total": len(rows), "feed_mode": "bookmarks"}


@router.post("/scholar/bookmark")
async def scholar_bookmark(
    payload: ScholarBookmarkRequest,
    current_user: str = Depends(get_current_user),
):
    if payload.folder_id is not None:
        folder = db_get_folder(payload.folder_id)
        if not folder or folder.get("username") != current_user:
            raise HTTPException(status_code=404, detail="북마크 폴더를 찾을 수 없습니다.")
    return db_set_scholar_bookmark(
        current_user, payload.paper_id, _safe_scholar_paper(payload.paper),
        payload.folder_id, payload.saved,
    )


@router.post("/scholar/interaction")
async def scholar_interaction(
    payload: ScholarInteractionRequest,
    current_user: str = Depends(get_current_user),
):
    return db_set_scholar_interaction(current_user, payload.paper_id, payload.action)


@router.post("/scholar/similar")
async def scholar_similar(
    payload: ScholarSimilarRequest,
    current_user: str = Depends(get_current_user),
):
    paper = payload.paper
    results = []
    if paper.get("semantic_scholar_id"):
        try:
            results = await recommend_semantic_scholar(
                [str(paper["semantic_scholar_id"])], limit=payload.limit,
            )
        except RuntimeError:
            results = []
    if not results:
        query = f"{paper.get('title') or ''} {str(paper.get('abstract') or '')[:350]}".strip()
        if len(query) < 2:
            raise HTTPException(status_code=422, detail="유사 논문을 찾을 문맥이 없습니다.")
        data = await search_papers(query, sort="relevance")
        results = data.get("results") or []
    source_id = str(paper.get("id") or "")
    results = [item for item in results if str(item.get("id") or "") != source_id][:payload.limit]
    return {
        "answer": f"‘{paper.get('title') or '선택 논문'}’과 의미적으로 가까운 논문입니다.",
        "results": results, "total": len(results), "source": "Semantic Scholar Recommendations",
    }


@router.post("/scholar/resolve-pdf")
async def scholar_resolve_pdf(
    payload: ScholarPaperRequest,
    current_user: str = Depends(get_current_user),
):
    from services.scholar_tools import resolve_paper_pdf
    return await resolve_paper_pdf(_safe_scholar_paper(payload.paper))


@router.post("/scholar/map")
async def scholar_map(
    payload: ScholarPaperRequest,
    current_user: str = Depends(get_current_user),
):
    from services.scholar_tools import build_paper_graph
    try:
        return await build_paper_graph(_safe_scholar_paper(payload.paper))
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/scholar/tools/papers")
async def scholar_tool_papers(current_user: str = Depends(get_current_user)):
    papers = []
    seen = set()
    for document in list_documents(username=current_user):
        metadata = document.get("metadata") or {}
        paper = {
            "id": metadata.get("scholar_paper_id") or document["id"],
            "title": metadata.get("title") or document.get("filename"),
            "authors": metadata.get("authors") or [], "year": metadata.get("year"),
            "doi": metadata.get("doi") or "", "url": metadata.get("source_url") or "",
            "semantic_scholar_id": metadata.get("semantic_scholar_id") or "",
            "saved_document_id": document["id"], "downloaded": True,
        }
        key = str(paper.get("semantic_scholar_id") or paper.get("doi") or paper.get("title") or "")
        if key and key not in seen:
            seen.add(key); papers.append(paper)
    for row in db_list_scholar_bookmarks(current_user):
        paper = row["paper"]
        key = str(paper.get("semantic_scholar_id") or paper.get("doi") or paper.get("title") or "")
        if key and key not in seen:
            seen.add(key); papers.append(paper)
    return {"papers": papers, "total": len(papers)}


@router.get("/scholar/conferences")
async def scholar_conferences(current_user: str = Depends(get_current_user)):
    from services.conference_official import official_cache_info
    from services.scholar_tools import conference_source_info, list_conferences
    watched = {item["conference_id"] for item in db_list_scholar_conference_watch(current_user)}
    conferences = await list_conferences()
    for conference in conferences:
        conference["watched"] = str(conference.get("id") or "") in watched
    return {
        "conferences": conferences, "total": len(conferences),
        "source": "사용자 학회 목록 + 공식 사이트", **conference_source_info(),
        **official_cache_info(),
    }


@router.post("/scholar/conferences/refresh")
async def scholar_conference_refresh(current_user: str = Depends(get_current_user)):
    from services.conference_official import refresh_official_conferences
    try:
        return await refresh_official_conferences(force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"공식 학회 일정 확인에 실패했습니다: {exc}") from exc


@router.post("/scholar/conferences/watch")
async def scholar_conference_watch(
    payload: ScholarConferenceWatchRequest,
    current_user: str = Depends(get_current_user),
):
    safe = {key: payload.conference.get(key) for key in (
        "id", "title", "description", "year", "deadline", "abstract_deadline",
        "timezone", "date", "place", "url", "rank", "source",
        "priority", "tentative", "status", "source_row", "official_url",
        "official_checked_at", "schedule_source", "registration_url",
        "registration_open", "author_registration_deadline",
        "early_registration_deadline", "registration_deadline",
    ) if payload.conference.get(key) is not None}
    return db_set_scholar_conference_watch(
        current_user, payload.conference_id, safe, payload.watched,
    )


@router.get("/scholar/background-status")
async def scholar_background_status(current_user: str = Depends(get_current_user)):
    from services.scholar_tools import scholar_timer_status
    return scholar_timer_status()


@router.post("/scholar/preview")
async def scholar_visual_preview(
    payload: ScholarPreviewRequest,
    current_user: str = Depends(get_current_user),
):
    """공개 PDF를 임시 분석해 번호가 붙은 Figure/Table 미리보기만 반환한다."""
    from config import MAX_FILE_SIZE_MB
    from services.paper_note import _select_visuals
    from services.pdf_parser import render_image_crop
    from services.remote_pdf import RemotePdfError, download_public_pdf

    try:
        with tempfile.TemporaryDirectory(prefix="scholar-preview-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "paper.pdf")
            await download_public_pdf(
                payload.pdf_url,
                pdf_path,
                max_bytes=MAX_FILE_SIZE_MB * 1024 * 1024,
            )
            visuals = await asyncio.to_thread(_select_visuals, pdf_path)
            previews = []
            for index, visual in enumerate(visuals[:5]):
                output_path = os.path.join(temp_dir, f"preview-{index}.png")
                rendered = await asyncio.to_thread(
                    render_image_crop,
                    pdf_path,
                    int(visual["page"]),
                    visual,
                    output_path,
                )
                if not rendered or not os.path.isfile(output_path):
                    continue
                with open(output_path, "rb") as image_file:
                    image_data = base64.b64encode(image_file.read()).decode("ascii")
                previews.append({
                    "kind": visual.get("kind") or "figure",
                    "label": visual.get("label") or "Figure/Table",
                    "caption": visual.get("caption") or "",
                    "page": visual.get("page"),
                    "image_data": f"data:image/png;base64,{image_data}",
                })
            return {"visuals": previews, "total": len(previews)}
    except RemotePdfError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"시각 자료를 추출하지 못했습니다: {exc}") from exc


def _normalise_title(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9가-힣]+", " ", (value or "").lower()).strip()


@router.post("/scholar/import")
async def scholar_import(
    payload: ScholarImportRequest,
    current_user: str = Depends(get_current_user),
):
    """검색 결과의 공개 PDF를 받아 기존 업로드·노트·번역 파이프라인에 넣는다."""
    if payload.folder_id is not None:
        folder = db_get_folder(payload.folder_id)
        if not folder or folder.get("username") != current_user:
            raise HTTPException(status_code=404, detail="저장할 폴더를 찾을 수 없습니다.")

    existing_title = _normalise_title(payload.title)
    existing_doi = payload.doi.lower().removeprefix("https://doi.org/").strip()
    for document in list_documents(username=current_user):
        metadata = document.get("metadata") or {}
        title = metadata.get("title") or document.get("filename") or ""
        doi = str(metadata.get("doi") or "").lower().removeprefix("https://doi.org/").strip()
        if (existing_doi and doi == existing_doi) or _normalise_title(title) == existing_title:
            db_link_scholar_bookmark(current_user, payload.paper_id, document["id"])
            raise HTTPException(status_code=409, detail="이미 라이브러리에 저장된 논문입니다.")

    import os
    import re
    import tempfile
    from starlette.datastructures import UploadFile
    from config import MAX_FILE_SIZE_MB
    from routers.upload import sessions, upload_pdf
    from services.remote_pdf import RemotePdfError, download_public_pdf

    filename = re.sub(r'[\\/:*?"<>|]+', "_", payload.title).strip(" .")[:140] or "paper"
    filename += ".pdf"
    try:
        with tempfile.TemporaryDirectory(prefix="scholar-pdf-") as temp_dir:
            temp_path = os.path.join(temp_dir, "paper.pdf")
            await download_public_pdf(
                payload.pdf_url, temp_path, max_bytes=MAX_FILE_SIZE_MB * 1024 * 1024,
            )
            with open(temp_path, "rb") as source:
                upload = UploadFile(source, filename=filename)
                result = await upload_pdf(
                    file=upload,
                    target_lang=payload.target_lang,
                    style=payload.style,
                    ignore_math=payload.ignore_math,
                    ignore_table=payload.ignore_table,
                    ignore_refs=payload.ignore_refs,
                    translation_mode=payload.translation_mode,
                    current_user=current_user,
                )
    except RemotePdfError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    metadata = dict(result.metadata or {})
    metadata.update({
        "title": payload.title,
        "authors": payload.authors,
        "year": payload.year,
        "venue": payload.venue,
        "doi": payload.doi,
        "source_url": payload.url,
        "scholar_paper_id": payload.paper_id,
        "semantic_scholar_id": payload.semantic_scholar_id,
    })
    metadata = {key: value for key, value in metadata.items() if value not in (None, "", [])}
    db_update_document_metadata(result.session_id, metadata)
    if payload.folder_id is not None:
        db_move_document_to_folder(result.session_id, current_user, payload.folder_id)
    if result.session_id in sessions:
        sessions[result.session_id]["metadata"] = metadata
    db_link_scholar_bookmark(current_user, payload.paper_id, result.session_id)
    return {
        **result.model_dump(), "metadata": metadata, "folder_id": payload.folder_id, "saved": True,
    }
