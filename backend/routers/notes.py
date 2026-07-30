import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from services.auth import get_current_user
from services.db import db_get_document, db_set_paper_note_status
from services.paper_note import (
    generate_paper_note,
    get_note_asset_path,
    get_paper_note,
    list_paper_notes,
)

router = APIRouter()
_pending_notes: dict[str, asyncio.Task] = {}


def _require_owned_document(doc_id: str, username: str) -> dict:
    doc = db_get_document(doc_id)
    if not doc or doc.get("username") != username or doc.get("is_deleted"):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return doc


def _start_generation(doc_id: str, username: str, language: str) -> bool:
    existing = _pending_notes.get(doc_id)
    if existing and not existing.done():
        return False

    from routers.upload import require_session_owner

    session = require_session_owner(doc_id, username)
    db_set_paper_note_status(doc_id, language, "generating")

    async def _run():
        try:
            await generate_paper_note(
                doc_id,
                session["pages"],
                session["metadata"],
                session["pdf_path"],
                target_lang=language,
                session_id=doc_id,
            )
        except Exception as exc:
            # generate_paper_note가 상태를 error로 기록한다. 여기서는 백그라운드
            # 태스크 예외가 이벤트 루프 경고로 남지 않도록 소비하고 로그만 남긴다.
            print(f"[paper-note] 생성 실패 ({doc_id}): {exc}")
        finally:
            _pending_notes.pop(doc_id, None)

    _pending_notes[doc_id] = asyncio.create_task(_run())
    return True


@router.get("/notes")
async def get_notes(current_user: str = Depends(get_current_user)):
    return {"notes": list_paper_notes(current_user)}


@router.get("/notes/{doc_id}")
async def get_note(doc_id: str, current_user: str = Depends(get_current_user)):
    doc = _require_owned_document(doc_id, current_user)
    note = get_paper_note(doc_id)
    if not note:
        return {
            "doc_id": doc_id,
            "status": "not_started",
            "content": None,
            "filename": doc["filename"],
            "metadata": doc.get("metadata") or {},
        }
    return note


@router.post("/notes/{doc_id}/regenerate")
async def regenerate_note(
    doc_id: str,
    language: str = "한국어",
    current_user: str = Depends(get_current_user),
):
    _require_owned_document(doc_id, current_user)
    _start_generation(doc_id, current_user, language)
    return {"status": "generating"}


@router.get("/notes/{doc_id}/assets/{asset_index}")
async def get_note_asset(
    doc_id: str,
    asset_index: int,
    current_user: str = Depends(get_current_user),
):
    _require_owned_document(doc_id, current_user)
    if asset_index < 0 or asset_index > 20:
        raise HTTPException(status_code=404, detail="노트 이미지를 찾을 수 없습니다.")
    path = await asyncio.to_thread(get_note_asset_path, doc_id, asset_index)
    if not path:
        raise HTTPException(status_code=404, detail="노트 이미지를 찾을 수 없습니다.")
    return FileResponse(path, media_type="image/png")
