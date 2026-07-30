"""업로드된 논문의 자동 요약/정리 노트 생성과 시각 자료 연결."""

import asyncio
import os
import re
import shutil
from typing import Optional

from config import LIBRARY_DIR
from services.db import (
    db_get_paper_note,
    db_list_paper_notes,
    db_save_paper_note,
    db_set_paper_note_status,
)
from services.llm_client import generate_reading_primer
from services.section_parser import detect_sections
from services.term_extractor import extract_candidate_terms

_MAX_FIGURES = 3
_MAX_TABLES = 3


def _asset_dir(doc_id: str) -> str:
    return os.path.join(LIBRARY_DIR, doc_id, "note_assets")


def clear_note_assets(doc_id: str) -> None:
    path = _asset_dir(doc_id)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _visual_sort_key(item: dict) -> tuple:
    label = item.get("label") or ""
    match = re.search(r"(\d+)", label)
    return (int(match.group(1)) if match else 10_000, item.get("page", 10_000))


def _select_visuals(pdf_path: str) -> list:
    """번호가 붙은 Figure/Table을 각각 최대 3개 선택한다.

    수식과 캡션 없는 장식 이미지는 노트에서 맥락을 설명할 수 없으므로 제외한다.
    """
    from services.pdf_parser import extract_pdf_images

    detected = extract_pdf_images(pdf_path)
    figures = []
    tables = []
    for item in detected:
        label = (item.get("label") or "").strip()
        number = r"(?:\d+|[IVXLCDM]+)"
        if re.match(rf"^Fig(?:ure)?\.?\s*{number}\b", label, re.IGNORECASE):
            figures.append(item)
        elif re.match(rf"^Table\s*{number}\b", label, re.IGNORECASE):
            tables.append(item)

    figures.sort(key=_visual_sort_key)
    tables.sort(key=_visual_sort_key)
    selected = figures[:_MAX_FIGURES] + tables[:_MAX_TABLES]
    visuals = []
    for index, item in enumerate(selected):
        visuals.append({
            "index": index,
            "kind": "table" if (item.get("label") or "").lower().startswith("table") else "figure",
            "page": item.get("page"),
            "label": item.get("label"),
            "caption": item.get("caption") or "",
            "left": item.get("left"),
            "top": item.get("top"),
            "width": item.get("width"),
            "height": item.get("height"),
        })
    return visuals


def _fallback_summary(analysis: dict) -> str:
    return analysis.get("summary") or analysis.get("lineage") or analysis.get("feynman") or analysis.get("hook", "")


async def save_note_from_analysis(
    doc_id: str,
    metadata: dict,
    analysis: dict,
    pdf_path: str,
    target_lang: str = "한국어",
) -> dict:
    """브리핑 생성에 사용한 동일 LLM 분석을 재사용해 영구 노트를 저장한다."""
    visuals = []
    try:
        visuals = await asyncio.to_thread(_select_visuals, pdf_path)
    except Exception as exc:
        print(f"[paper-note] 시각 자료 추출 실패 ({doc_id}): {exc}")

    note = {
        "title": metadata.get("title") or "",
        "one_line_summary": analysis.get("hook", ""),
        "summary": _fallback_summary(analysis),
        "contributions": analysis.get("contributions", []),
        "method_summary": analysis.get("method_summary", ""),
        "results_summary": analysis.get("results_summary", ""),
        "limitations": analysis.get("limitations", ""),
        "takeaways": analysis.get("takeaways", []),
        "keywords": analysis.get("keywords", []),
        "experiment_flow": analysis.get("experiment_flow", []),
        "glossary": analysis.get("glossary", []),
        "visuals": visuals,
    }
    clear_note_assets(doc_id)
    db_save_paper_note(doc_id, target_lang, note)
    return note


async def generate_paper_note(
    doc_id: str,
    pages: list,
    metadata: dict,
    pdf_path: str,
    target_lang: str = "한국어",
    session_id: Optional[str] = None,
) -> dict:
    """기존 문서에서 노트만 새로 만들 때 사용하는 전체 생성 경로."""
    db_set_paper_note_status(doc_id, target_lang, "generating")
    try:
        analysis = await generate_reading_primer(
            metadata.get("title") or "",
            detect_sections(pages),
            extract_candidate_terms(pages),
            target_lang=target_lang,
            session_id=session_id,
        )
        return await save_note_from_analysis(
            doc_id, metadata, analysis, pdf_path, target_lang=target_lang
        )
    except Exception as exc:
        db_set_paper_note_status(doc_id, target_lang, "error", str(exc)[:500])
        raise


def get_paper_note(doc_id: str) -> Optional[dict]:
    return db_get_paper_note(doc_id)


def list_paper_notes(username: str) -> list:
    return db_list_paper_notes(username)


def get_note_asset_path(doc_id: str, asset_index: int) -> Optional[str]:
    """저장된 노트가 가리키는 bbox만 렌더링한다. 클라이언트가 임의 bbox를
    전달하지 못하게 해 PDF의 허용된 시각 자료 범위로 요청을 제한한다."""
    note_row = db_get_paper_note(doc_id)
    content = (note_row or {}).get("content") or {}
    visuals = content.get("visuals") or []
    visual = next((item for item in visuals if item.get("index") == asset_index), None)
    if not visual:
        return None

    output_dir = _asset_dir(doc_id)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"asset_{asset_index}.png")
    if os.path.exists(output_path):
        return output_path

    pdf_path = os.path.join(LIBRARY_DIR, doc_id, "document.pdf")
    if not os.path.exists(pdf_path):
        return None

    from services.pdf_parser import render_image_crop

    try:
        if render_image_crop(pdf_path, int(visual["page"]), visual, output_path):
            return output_path
    except Exception as exc:
        print(f"[paper-note] 자산 렌더링 실패 ({doc_id}/{asset_index}): {exc}")
    return None
