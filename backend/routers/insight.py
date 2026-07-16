import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from routers.upload import sessions, ensure_session
from services.llm_client import stream_page_insight
from services.library import save_page_insight, get_page_insight

router = APIRouter()

VALID_KINDS = ("keywords", "summary")


@router.get("/insight/{session_id}/{page_num}")
async def get_page_insight_stream(
    session_id: str,
    page_num: int,
    kind: str,
    target_lang: str = "한국어",
    force: bool = False
):
    """
    특정 페이지의 키워드/단어 설명(kind=keywords) 또는 요약(kind=summary)을
    SSE 스트리밍으로 반환합니다. 이미 생성된 적이 있으면 캐시에서 즉시 반환하고,
    force=true면 캐시를 무시하고 새로 생성합니다.
    """
    if kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"kind는 {VALID_KINDS} 중 하나여야 합니다.")

    if not ensure_session(session_id):
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    session = sessions[session_id]
    total_pages = session["total_pages"]

    if page_num < 1 or page_num > total_pages:
        raise HTTPException(
            status_code=400,
            detail=f"페이지 번호는 1~{total_pages} 사이여야 합니다."
        )

    pages = session["pages"]
    page_data = next((p for p in pages if p["page_num"] == page_num), None)
    if not page_data:
        raise HTTPException(status_code=404, detail="페이지를 찾을 수 없습니다.")

    page_text = page_data.get("text", "").strip()
    suffix = target_lang
    doc_title = session.get("metadata", {}).get("title") or session.get("filename", "")

    async def event_stream():
        if not force:
            cached = get_page_insight(session_id, page_num, kind, suffix)
            if cached:
                chunk_size = 100
                for i in range(0, len(cached), chunk_size):
                    data = json.dumps({"content": cached[i:i + chunk_size], "done": False, "cached": True}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                yield f"data: {json.dumps({'content': '', 'done': True, 'cached': True}, ensure_ascii=False)}\n\n"
                return

        if not page_text:
            msg = "이 페이지에는 분석할 텍스트가 없습니다."
            yield f"data: {json.dumps({'content': msg, 'done': False, 'cached': False}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'content': '', 'done': True, 'cached': False}, ensure_ascii=False)}\n\n"
            return

        full_result = []
        try:
            async for token in stream_page_insight(
                kind,
                page_text,
                target_lang=target_lang,
                doc_title=doc_title,
                session_id=session_id
            ):
                full_result.append(token)
                data = json.dumps({"content": token, "done": False, "cached": False}, ensure_ascii=False)
                yield f"data: {data}\n\n"

            complete_result = "".join(full_result).strip()
            save_page_insight(session_id, page_num, kind, complete_result, suffix)
            yield f"data: {json.dumps({'content': '', 'done': True, 'cached': False}, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_data = json.dumps({"error": str(e), "done": True})
            yield f"data: {error_data}\n\n"
            return

    return StreamingResponse(event_stream(), media_type="text/event-stream")
