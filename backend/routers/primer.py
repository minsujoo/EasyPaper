from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse

from routers.upload import require_session_owner
from routers.library import _require_owned_document
from services.auth import get_current_user
from services.primer import get_cached_primer, generate_primer
from services.library import get_primer_figure_path

router = APIRouter()


@router.get("/library/{doc_id}/primer")
async def get_primer(doc_id: str, target_lang: str = "한국어", current_user: str = Depends(get_current_user)):
    """읽기 전 브리핑 콘텐츠를 반환합니다. 업로드 직후 백그라운드로 이미 생성되어
    있으면 캐시에서 즉시 반환하고, 아직 없으면(구버전 문서 등) 그 자리에서 생성합니다."""
    cached = get_cached_primer(doc_id, target_lang=target_lang)
    if cached:
        return cached

    session = require_session_owner(doc_id, current_user)
    return await generate_primer(
        doc_id,
        session["pages"],
        session["metadata"],
        username=current_user,
        pdf_path=session["pdf_path"],
        target_lang=target_lang,
        session_id=doc_id,
    )


@router.get("/library/{doc_id}/primer-figure")
async def get_primer_figure(doc_id: str, current_user: str = Depends(get_current_user)):
    """읽기 전 브리핑에 쓰이는 대표 Figure 크롭 이미지를 서빙합니다."""
    _require_owned_document(doc_id, current_user)
    figure_path = get_primer_figure_path(doc_id)
    if not figure_path:
        raise HTTPException(status_code=404, detail="Figure 이미지가 없습니다.")
    return FileResponse(figure_path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
