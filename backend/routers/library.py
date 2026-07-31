from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from services.auth import get_current_user
from services.library import (
    list_documents, search_documents, get_document, permanently_delete_document,
    soft_delete_document, restore_document, empty_trash,
    get_translation, get_pdf_path, get_cover_path, update_document_metadata
)
from pydantic import BaseModel
import json

router = APIRouter()


from typing import Optional


class CitationInsightRequest(BaseModel):
    surrounding_context: str = ""


def _require_owned_document(doc_id: str, current_user: str, doc: Optional[dict] = None) -> dict:
    """문서가 존재하고 현재 로그인한 사용자 소유인지 확인한다.

    다른 사용자의 문서는 존재 여부조차 알려주지 않도록, 존재하지 않는 경우와
    동일하게 404로 응답한다(문서는 있지만 권한이 없다는 403은 doc_id가
    실제로 존재한다는 사실 자체를 노출하게 됨).
    """
    if doc is None:
        doc = get_document(doc_id)
    if not doc or doc.get("username") != current_user:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return doc

@router.get("/library")
async def get_library(
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None,
    current_user: str = Depends(get_current_user)
):
    """라이브러리의 모든 문서 목록을 반환합니다."""
    docs = list_documents(current_user, target_lang, style, ignore_math, ignore_table, ignore_refs)
    return {"documents": docs, "total": len(docs)}


@router.get("/library/trash")
async def get_library_trash(
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None,
    current_user: str = Depends(get_current_user)
):
    """휴지통에 보관 중인 문서 목록을 반환합니다."""
    docs = list_documents(current_user, target_lang, style, ignore_math, ignore_table, ignore_refs, only_trash=True)
    return {"documents": docs, "total": len(docs)}


@router.delete("/library/trash/empty")
async def empty_library_trash(current_user: str = Depends(get_current_user)):
    """휴지통을 완전히 비웁니다(영구 삭제)."""
    if not empty_trash(current_user):
        raise HTTPException(status_code=500, detail="휴지통 비우기에 실패했습니다.")
    return {"message": "휴지통이 비워졌습니다."}


@router.get("/library/search")
async def search_library(q: str = "", current_user: str = Depends(get_current_user)):
    """파일명/제목·카테고리와 번역된 본문 텍스트를 가로질러 검색합니다.

    /library/{doc_id}보다 먼저 등록해야 한다 - 그렇지 않으면 "search"가
    doc_id 경로 파라미터로 잘못 매칭된다.
    """
    docs = search_documents(current_user, q)
    return {"documents": docs, "total": len(docs)}


@router.get("/library/{doc_id}")
async def get_library_document(
    doc_id: str,
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None,
    current_user: str = Depends(get_current_user)
):
    """특정 문서의 메타데이터와 번역 완료 페이지 목록을 반환합니다."""
    doc = get_document(doc_id, target_lang, style, ignore_math, ignore_table, ignore_refs)
    _require_owned_document(doc_id, current_user, doc)
    return doc


@router.get("/library/{doc_id}/translation/{page_num}")
async def get_library_translation(
    doc_id: str,
    page_num: int,
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None,
    current_user: str = Depends(get_current_user)
):
    """라이브러리에서 특정 페이지 번역을 가져옵니다."""
    _require_owned_document(doc_id, current_user)
    suffix = ""
    if target_lang is not None and style is not None:
        suffix = f"{target_lang}_{style}_math{int(ignore_math)}_table{int(ignore_table)}_refs{int(ignore_refs)}"
        
    from services.library import get_translation_full
    full_cached = get_translation_full(doc_id, page_num, suffix)
    if not full_cached.get("translation"):
        raise HTTPException(status_code=404, detail="번역이 없습니다.")
    return {
        "page": page_num,
        "translation": full_cached["translation"],
        "sentences": full_cached.get("sentences", [])
    }


class UpdateTranslationPayload(BaseModel):
    translation: str
    sentences: list[dict]


@router.put("/library/{doc_id}/translation/{page_num}")
async def update_library_translation(
    doc_id: str,
    page_num: int,
    payload: UpdateTranslationPayload,
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None,
    current_user: str = Depends(get_current_user)
):
    """라이브러리의 특정 페이지 번역 데이터를 수정하여 캐시 및 DB에 저장합니다."""
    _require_owned_document(doc_id, current_user)
    suffix = ""
    if target_lang is not None and style is not None:
        suffix = f"{target_lang}_{style}_math{int(ignore_math)}_table{int(ignore_table)}_refs{int(ignore_refs)}"

    from services.library import save_translation
    from services.cache import save_translation_cache

    payload_dict = {
        "translation": payload.translation,
        "sentences": payload.sentences
    }
    payload_json = json.dumps(payload_dict, ensure_ascii=False)

    # DB에 영구 저장
    save_translation(doc_id, page_num, payload_json, suffix)
    # 메모리 캐시 최신화
    save_translation_cache(doc_id, page_num, payload_json, suffix)

    return {"status": "success"}


@router.get("/library/{doc_id}/pdf")
async def get_library_pdf(doc_id: str, current_user: str = Depends(get_current_user)):
    """라이브러리 PDF 파일을 서빙합니다."""
    _require_owned_document(doc_id, current_user)
    pdf_path = get_pdf_path(doc_id)
    if not pdf_path:
        raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")
    return FileResponse(pdf_path, media_type="application/pdf")


class ExportPdfRequest(BaseModel):
    annotations: dict = {}
    memos: dict = {}
    target_lang: Optional[str] = None
    style: Optional[str] = None
    ignore_math: Optional[bool] = None
    ignore_table: Optional[bool] = None
    ignore_refs: Optional[bool] = None


@router.post("/library/{doc_id}/export-pdf")
async def export_annotated_pdf(
    doc_id: str,
    payload: ExportPdfRequest,
    current_user: str = Depends(get_current_user)
):
    """번역/하이라이트/밑줄/메모가 반영된 PDF를 생성해 반환합니다.

    하이라이트·밑줄·메모는 브라우저 localStorage에만 있으므로 요청 본문으로
    전달받는다(서버는 이 데이터를 저장하지 않고 즉시 PDF 생성에만 사용).
    """
    doc = _require_owned_document(doc_id, current_user)
    pdf_path = get_pdf_path(doc_id)
    if not pdf_path:
        raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")

    suffix = ""
    if payload.target_lang is not None and payload.style is not None:
        suffix = f"{payload.target_lang}_{payload.style}_math{int(payload.ignore_math)}_table{int(payload.ignore_table)}_refs{int(payload.ignore_refs)}"

    from services.library import get_translation_full
    total_pages = doc.get("total_pages", 0) or 0
    translations = {}
    for page_num in range(1, total_pages + 1):
        full = get_translation_full(doc_id, page_num, suffix)
        text = (full or {}).get("translation")
        if text:
            translations[str(page_num)] = text

    try:
        from services.pdf_export import generate_annotated_pdf
        pdf_bytes = generate_annotated_pdf(
            pdf_path, payload.annotations, translations, payload.memos
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {str(e)}")

    from fastapi.responses import Response
    from urllib.parse import quote
    base_filename = (doc.get("filename") or "document").rsplit(".", 1)[0]
    export_filename = f"{base_filename}_번역_주석.pdf"
    encoded_filename = quote(export_filename)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"export.pdf\"; filename*=UTF-8''{encoded_filename}"
        }
    )


@router.get("/library/{doc_id}/cover")
async def get_library_cover(doc_id: str, current_user: str = Depends(get_current_user)):
    """라이브러리 카드 미리보기용 1페이지 상단(제목+abstract) 캡쳐 이미지를 서빙합니다.

    최초 1회만 PyMuPDF로 렌더링하고 이후엔 파일만 서빙하지만(get_cover_path
    내부에서 이미 캐싱), 그 최초 렌더링 자체가 동기 CPU 작업이라 이벤트
    루프를 블로킹하지 않도록 스레드로 넘긴다."""
    import asyncio
    _require_owned_document(doc_id, current_user)
    cover_path = await asyncio.to_thread(get_cover_path, doc_id)
    if not cover_path:
        raise HTTPException(status_code=404, detail="미리보기 이미지를 생성할 수 없습니다.")
    return FileResponse(cover_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.delete("/library/{doc_id}")
async def delete_library_document(doc_id: str, current_user: str = Depends(get_current_user)):
    """라이브러리 문서를 휴지통으로 이동(Soft Delete)합니다."""
    _require_owned_document(doc_id, current_user)
    if not soft_delete_document(doc_id):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return {"message": "문서가 휴지통으로 이동되었습니다."}

@router.post("/library/{doc_id}/restore")
async def restore_library_document(doc_id: str, current_user: str = Depends(get_current_user)):
    """휴지통에서 문서를 복원합니다."""
    _require_owned_document(doc_id, current_user)
    if not restore_document(doc_id):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return {"message": "문서가 성공적으로 복원되었습니다."}

@router.delete("/library/{doc_id}/permanent")
async def delete_library_document_permanently(doc_id: str, current_user: str = Depends(get_current_user)):
    """라이브러리에서 문서를 영구히 삭제(Hard Delete)합니다."""
    _require_owned_document(doc_id, current_user)
    if not permanently_delete_document(doc_id):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return {"message": "문서가 영구적으로 삭제되었습니다."}


@router.get("/library/{doc_id}/images")
async def get_library_document_images(doc_id: str, current_user: str = Depends(get_current_user)):
    """특정 문서의 모든 페이지에서 이미지/Figure 좌표 정보(백분율) 목록을 반환합니다.

    find_tables()/get_drawings() 등을 페이지마다 두 차례 훑는 무거운 연산이라,
    extract_pages()와 동일하게 디스크에 캐싱해 서버 재시작 이후에도 문서를
    다시 열 때마다 재계산하지 않게 한다. 또한 이 연산은 완전히 동기
    CPU-bound(PyMuPDF, GIL 점유)라 캐시 미스 시 asyncio 이벤트 루프를 그대로
    블로킹하면 그동안 다른 요청(라이브러리 목록 등)이 전부 멈추므로,
    별도 스레드로 넘긴다."""
    import asyncio
    from services.cache import get_cached_images, save_images_cache

    _require_owned_document(doc_id, current_user)
    pdf_path = get_pdf_path(doc_id)
    if not pdf_path:
        raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")

    images = get_cached_images(doc_id, pdf_path)
    if images is not None:
        return {"images": images}

    try:
        from services.pdf_parser import extract_pdf_images
        images = await asyncio.to_thread(extract_pdf_images, pdf_path)
        save_images_cache(doc_id, pdf_path, images)
        return {"images": images}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이미지 좌표 추출 실패: {str(e)}")


@router.get("/library/{doc_id}/references")
async def get_library_references(doc_id: str, current_user: str = Depends(get_current_user)):
    """참고문헌 목록(번호 -> 원문 텍스트)을 반환합니다.

    외부 API 호출 없이 PDF 텍스트만 파싱한다 - 실제 외부 링크 조회는
    사용자가 본문에서 특정 인용 표기를 클릭했을 때만
    (/library/{doc_id}/references/{ref_num}) 그때그때 수행한다. 논문 한 편에
    참고문헌이 수십~백여 개인 경우가 흔한데, 열 때마다 전부 미리 조회하면
    외부 API 레이트리밋에 바로 걸리고 대부분은 클릭되지도 않아 낭비다.
    """
    _require_owned_document(doc_id, current_user)

    from services.library import get_page_insight, save_page_insight
    from services.reference_mentions import build_reference_mentions

    cached = get_page_insight(doc_id, 0, "reference_list")
    if cached is not None:
        try:
            references = json.loads(cached)
            return {
                "references": references,
                "mentions": build_reference_mentions(references),
            }
        except Exception:
            pass

    pdf_path = get_pdf_path(doc_id)
    if not pdf_path:
        raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")

    import asyncio
    from services.cache import get_cached_pages, save_pages_cache
    from services.pdf_parser import extract_pages
    from services.reference_parser import extract_reference_list
    try:
        # ensure_session()/get_cached_pages()와 같은 디스크 캐시를 공유한다 -
        # 그렇지 않으면 세션이 아직 복원되지 않은 상태(서버 재시작 직후 첫
        # 열람)에서 텍스트를 한 번 더 처음부터 추출하게 된다.
        pages = get_cached_pages(doc_id, pdf_path)
        if pages is None:
            pages = await asyncio.to_thread(extract_pages, pdf_path)
            save_pages_cache(doc_id, pdf_path, pages)
        references = extract_reference_list(pages)
    except Exception:
        references = {}

    save_page_insight(doc_id, 0, "reference_list", json.dumps(references, ensure_ascii=False))
    return {
        "references": references,
        "mentions": build_reference_mentions(references),
    }


async def _get_resolved_reference(doc_id: str, ref_num: str, refresh: bool = False) -> dict:
    """검증된 캐시를 우선 사용하고, 실패 캐시는 일정 시간 뒤 또는 수동 요청 시 재검색한다."""
    from datetime import datetime, timedelta, timezone
    from services.reference_linker import REFERENCE_RESOLVER_VERSION
    from services.library import get_page_insight, save_page_insight

    cached = get_page_insight(doc_id, 0, "reference_url", suffix=ref_num)
    if cached is not None and not refresh:
        try:
            data = json.loads(cached)
        except Exception:
            data = {}
        # 이전 검색기는 첫 번째 OpenAlex 결과를 검증 없이 저장했으므로 버전이
        # 없는 성공 캐시도 다시 검색한다. 새 실패 캐시는 10분 동안만 유지한다.
        if data.get("_resolver_version") == REFERENCE_RESOLVER_VERSION:
            if data.get("_not_found"):
                try:
                    retry_after = datetime.fromisoformat(data.get("_retry_after") or "")
                except (TypeError, ValueError):
                    retry_after = datetime.min.replace(tzinfo=timezone.utc)
                if retry_after > datetime.now(timezone.utc):
                    raise HTTPException(status_code=404, detail="외부에서 일치하는 논문을 찾지 못했습니다.")
            elif data.get("url"):
                return data

    ref_list_cached = get_page_insight(doc_id, 0, "reference_list")
    references = {}
    if ref_list_cached:
        try:
            references = json.loads(ref_list_cached)
        except Exception:
            references = {}

    ref_text = references.get(ref_num)
    if not ref_text:
        raise HTTPException(status_code=404, detail="해당 번호의 참고문헌을 찾을 수 없습니다.")

    from services.reference_linker import resolve_reference
    result = await resolve_reference(ref_text)
    if not result:
        retry_after = datetime.now(timezone.utc) + timedelta(minutes=10)
        negative_cache = {
            "_resolver_version": REFERENCE_RESOLVER_VERSION,
            "_not_found": True,
            "_retry_after": retry_after.isoformat(),
        }
        save_page_insight(
            doc_id, 0, "reference_url",
            json.dumps(negative_cache, ensure_ascii=False), suffix=ref_num,
        )
        raise HTTPException(status_code=404, detail="외부에서 일치하는 논문을 찾지 못했습니다.")
    result.setdefault("_resolver_version", REFERENCE_RESOLVER_VERSION)
    save_page_insight(doc_id, 0, "reference_url", json.dumps(result, ensure_ascii=False), suffix=ref_num)
    return result


@router.get("/library/{doc_id}/references/{ref_num}/download")
async def download_library_reference(
    doc_id: str,
    ref_num: str,
    current_user: str = Depends(get_current_user),
):
    """오픈 액세스로 확인된 인용 논문 PDF를 내려받는다.

    외부 URL은 OpenAlex 검색 결과에서만 얻고, 리다이렉트 단계마다 공인 HTTPS
    주소인지 확인해 로컬 네트워크로의 SSRF를 막는다.
    """
    _require_owned_document(doc_id, current_user)
    result = await _get_resolved_reference(doc_id, ref_num)
    pdf_url = (result.get("pdf_url") or "").strip()
    if not pdf_url:
        raise HTTPException(status_code=404, detail="다운로드 가능한 공개 PDF를 찾지 못했습니다.")

    import asyncio
    import ipaddress
    import re
    import socket
    from urllib.parse import quote, urljoin, urlparse
    import httpx
    from fastapi.responses import Response

    async def ensure_public_https(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HTTPException(status_code=400, detail="안전하지 않은 PDF 주소입니다.")

        def resolve_addresses():
            return socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)

        try:
            infos = await asyncio.to_thread(resolve_addresses)
        except OSError:
            raise HTTPException(status_code=502, detail="PDF 서버 주소를 확인할 수 없습니다.")
        for info in infos:
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global:
                raise HTTPException(status_code=400, detail="안전하지 않은 PDF 주소입니다.")

    max_bytes = 80 * 1024 * 1024
    current_url = pdf_url
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            for _ in range(6):
                await ensure_public_https(current_url)
                response = await client.get(
                    current_url,
                    headers={"User-Agent": "EasyPaper/0.1 (research paper downloader)"},
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise HTTPException(status_code=502, detail="PDF 다운로드 주소가 올바르지 않습니다.")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code != 200:
                    raise HTTPException(status_code=502, detail="인용 논문 PDF를 내려받지 못했습니다.")
                content = response.content
                if len(content) > max_bytes:
                    raise HTTPException(status_code=413, detail="PDF 파일이 80MB를 초과합니다.")
                if not content.startswith(b"%PDF"):
                    raise HTTPException(status_code=502, detail="다운로드 결과가 PDF 파일이 아닙니다.")
                break
            else:
                raise HTTPException(status_code=502, detail="PDF 다운로드 리다이렉트가 너무 많습니다.")
    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="인용 논문 PDF 서버에 연결하지 못했습니다.")

    title = result.get("title") or f"reference-{ref_num}"
    safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._")[:80] or f"reference-{ref_num}"
    encoded = quote(f"{title[:120]}.pdf")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{safe_ascii}.pdf\"; filename*=UTF-8''{encoded}"
        },
    )


@router.post("/library/{doc_id}/references/{ref_num}/insight")
async def get_library_reference_insight(
    doc_id: str,
    ref_num: str,
    payload: CitationInsightRequest,
    current_user: str = Depends(get_current_user),
):
    """현재 본문의 인용 주변 문맥과 인용 논문 초록을 함께 분석해, 인용 이유와
    인용 논문 개요를 한국어로 생성한다."""
    doc = _require_owned_document(doc_id, current_user)
    from services.library import get_page_insight, save_page_insight
    import hashlib

    context = (payload.surrounding_context or "").strip()[:5000]
    context_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()[:12]
    cache_suffix = f"{ref_num}:{context_hash}"
    cached = get_page_insight(doc_id, 0, "reference_insight", suffix=cache_suffix)
    if cached:
        return {"content": cached}

    result = await _get_resolved_reference(doc_id, ref_num)
    ref_list_cached = get_page_insight(doc_id, 0, "reference_list") or "{}"
    try:
        reference_text = json.loads(ref_list_cached).get(ref_num, "")
    except Exception:
        reference_text = ""

    system_prompt = """당신은 학술 논문의 인용 관계를 분석하는 연구 도우미입니다.
제공된 현재 논문의 인용 주변 문맥과 인용 논문의 서지정보·초록만 근거로 답하세요.
한국어로 간결하고 구체적으로 작성하며, 근거가 부족한 부분은 추측하지 말고
'문맥만으로 단정하기 어렵습니다'라고 밝히세요."""
    user_prompt = f"""[현재 읽는 논문]
{(doc.get("metadata") or {}).get("title") or doc.get("filename", "")}

[인용 주변 문맥]
{context or "(주변 문맥 없음)"}

[참고문헌 원문]
{reference_text}

[인용 논문 정보]
제목: {result.get("title", "")}
저자: {", ".join(result.get("authors") or [])}
연도/학회: {result.get("year") or ""} / {result.get("venue") or ""}
초록: {result.get("abstract") or "(초록 정보 없음)"}

아래 두 제목을 정확히 사용해 각각 2~4문장으로 작성하세요.
## 이 논문이 인용된 이유
## 인용 논문 개요"""

    from services.llm_client import stream_chat
    chunks = []
    try:
        async for token in stream_chat(
            system_prompt,
            [{"role": "user", "content": user_prompt}],
            session_id=f"citation-insight:{doc_id}:{ref_num}:{context_hash}",
        ):
            chunks.append(token)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"인용 관계 분석 실패: {str(exc)}")

    content = "".join(chunks).strip()
    if not content:
        raise HTTPException(status_code=500, detail="인용 관계 분석 결과가 비어 있습니다.")
    save_page_insight(doc_id, 0, "reference_insight", content, suffix=cache_suffix)
    return {"content": content}


@router.get("/library/{doc_id}/references/{ref_num}")
async def resolve_library_reference(
    doc_id: str,
    ref_num: str,
    refresh: bool = False,
    current_user: str = Depends(get_current_user),
):
    """특정 참고문헌을 여러 외부 소스에서 검색하고 검증한 링크를 반환한다."""
    _require_owned_document(doc_id, current_user)
    return await _get_resolved_reference(doc_id, ref_num, refresh=refresh)


@router.put("/library/{doc_id}/metadata")
async def update_doc_metadata(
    doc_id: str,
    payload: dict,
    current_user: str = Depends(get_current_user)
):
    """문서의 메타데이터(예: 제목 등)를 업데이트합니다."""
    doc = _require_owned_document(doc_id, current_user)

    meta = doc.get("metadata") or {}
    meta.update(payload)
    
    update_document_metadata(doc_id, meta)
    
    # 활성 메모리 세션도 업데이트하여 정합성 유지
    from routers.upload import sessions
    if doc_id in sessions:
        sessions[doc_id]["metadata"] = meta
        
    return {"status": "success", "metadata": meta}
