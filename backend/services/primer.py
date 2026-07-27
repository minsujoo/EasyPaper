"""
"읽기 전 브리핑(Reading Primer)" 생성 오케스트레이션.

업로드 직후 백그라운드로 실행되어 아래 네 가지를 조합한 결과를 page_insights
테이블(doc_id, page_num=0, kind="primer")에 캐싱한다:
  1. 훅 문장 / 예측 질문 3개 / 체크리스트 3개 (LLM, llm_client.generate_reading_primer)
  2. 대표 Figure 크롭 이미지 (pdf_parser.extract_pdf_images + render_image_crop)
  3. 내 라이브러리 안에서 이 논문의 참고문헌과 겹치는 논문 매칭 (외부 API 없이 텍스트 매칭)
  4. 내 라이브러리에 없는 참고문헌 중 최대 3건만 Semantic Scholar로 확장 조회
"""
import json
import re
from typing import Optional

from services.library import (
    save_page_insight,
    get_page_insight,
    get_pdf_path,
    save_primer_figure,
    list_documents,
)
from services.reference_parser import extract_reference_list
from services.llm_client import generate_reading_primer

_EXTERNAL_MATCH_LIMIT = 3
_EXTERNAL_ATTEMPT_LIMIT = 5
_LIBRARY_MATCH_THRESHOLD = 0.7
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "on", "in", "to", "with",
    "using", "via", "based", "towards", "toward", "from", "into", "at", "by",
}


def _normalize_words(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _match_library_references(reference_map: dict, doc_id: str, username: str) -> tuple[list, set]:
    """참고문헌 원문 목록을 내 라이브러리의 다른 논문 제목과 텍스트 매칭한다.
    외부 API를 쓰지 않아 업로드마다 항상 실행해도 부담이 없다."""
    candidates = []
    for doc in list_documents(username=username):
        if doc["id"] == doc_id:
            continue
        title = (doc.get("metadata") or {}).get("title") or doc.get("filename", "")
        words = _normalize_words(title)
        if len(words) < 2:
            continue
        candidates.append((doc["id"], title, words))

    matches = []
    matched_doc_ids = set()
    matched_ref_nums = set()
    for ref_num, ref_text in reference_map.items():
        ref_words = _normalize_words(ref_text)
        if not ref_words:
            continue
        for cand_doc_id, title, words in candidates:
            if cand_doc_id in matched_doc_ids:
                continue
            if len(words & ref_words) / len(words) >= _LIBRARY_MATCH_THRESHOLD:
                matches.append({"ref_num": ref_num, "doc_id": cand_doc_id, "title": title})
                matched_doc_ids.add(cand_doc_id)
                matched_ref_nums.add(ref_num)
                break
    return matches, matched_ref_nums


def _is_plausible_match(ref_text: str, resolved: dict) -> bool:
    """Semantic Scholar 공개 검색은 API 키 없이 쓰다 보니 지저분한 인용 문자열에
    엉뚱한 논문을 최상위로 반환하는 경우가 있다(실제로 EEG 논문 테스트에서 안과
    수술 논문이 매칭된 사례를 확인함). 연도가 인용 원문에 그대로 등장하거나,
    제목 단어가 인용 원문과 충분히 겹칠 때만 신뢰할 만한 매칭으로 받아들인다."""
    year = resolved.get("year")
    if year and str(year) in (ref_text or ""):
        return True
    title_words = _normalize_words(resolved.get("title", ""))
    if not title_words:
        return False
    ref_words = _normalize_words(ref_text)
    return len(title_words & ref_words) / len(title_words) >= 0.5


async def _resolve_external_references(reference_map: dict, matched_ref_nums: set) -> list:
    from services.reference_linker import resolve_reference
    results = []
    attempts = 0
    for ref_num, ref_text in reference_map.items():
        if len(results) >= _EXTERNAL_MATCH_LIMIT or attempts >= _EXTERNAL_ATTEMPT_LIMIT:
            break
        if ref_num in matched_ref_nums:
            continue
        attempts += 1
        resolved = await resolve_reference(ref_text)
        if resolved and _is_plausible_match(ref_text, resolved):
            results.append({"ref_num": ref_num, **resolved})
    return results


def _pick_primary_figure(pdf_path: str) -> Optional[dict]:
    from services.pdf_parser import extract_pdf_images
    try:
        images = extract_pdf_images(pdf_path)
    except Exception as e:
        print(f"[primer] Figure 추출 실패: {e}")
        return None

    best = None
    best_num = None
    for img in images:
        label = img.get("label") or ""
        m = re.match(r"^fig(?:ure)?\.?\s*(\d+)", label, re.IGNORECASE)
        if not m:
            continue
        num = int(m.group(1))
        if best_num is None or num < best_num:
            best_num = num
            best = img
    return best


async def generate_primer(
    doc_id: str,
    pages: list,
    metadata: dict,
    username: str,
    pdf_path: str,
    target_lang: str = "한국어",
    session_id: str = None,
) -> dict:
    """primer 콘텐츠를 생성하고 캐시에 저장한 뒤 결과 dict를 반환한다.
    실패해도 예외를 던지지 않고 부분 결과를 반환한다 - 업로드 직후 번역 착수를
    막아서는 안 되는 백그라운드 부가 기능이기 때문이다.
    """
    title = metadata.get("title") or ""
    combined_text = "\n".join(p.get("text", "") for p in pages[:2])

    try:
        llm_part = await generate_reading_primer(title, combined_text, target_lang=target_lang, session_id=session_id)
    except Exception as e:
        print(f"[primer] LLM 생성 실패 ({doc_id}): {e}")
        llm_part = {"hook": "", "questions": [], "checklist": []}

    try:
        reference_map = extract_reference_list(pages)
    except Exception as e:
        print(f"[primer] 참고문헌 파싱 실패 ({doc_id}): {e}")
        reference_map = {}

    library_matches, matched_ref_nums = [], set()
    if reference_map:
        try:
            library_matches, matched_ref_nums = _match_library_references(reference_map, doc_id, username)
        except Exception as e:
            print(f"[primer] 라이브러리 매칭 실패 ({doc_id}): {e}")

    external_matches = []
    if reference_map:
        try:
            external_matches = await _resolve_external_references(reference_map, matched_ref_nums)
        except Exception as e:
            print(f"[primer] 외부 참고문헌 조회 실패 ({doc_id}): {e}")

    figure = None
    try:
        picked = _pick_primary_figure(pdf_path)
        if picked and save_primer_figure(doc_id, pdf_path, picked["page"], picked):
            figure = {"page": picked["page"], "label": picked.get("label")}
    except Exception as e:
        print(f"[primer] Figure 크롭 실패 ({doc_id}): {e}")

    result = {
        "hook": llm_part.get("hook", ""),
        "questions": llm_part.get("questions", []),
        "checklist": llm_part.get("checklist", []),
        "figure": figure,
        "citation_graph": {
            "library": library_matches,
            "external": external_matches,
        },
    }

    try:
        save_page_insight(doc_id, 0, "primer", json.dumps(result, ensure_ascii=False), suffix=target_lang)
    except Exception as e:
        print(f"[primer] 캐시 저장 실패 ({doc_id}): {e}")

    return result


def get_cached_primer(doc_id: str, target_lang: str = "한국어") -> Optional[dict]:
    content = get_page_insight(doc_id, 0, "primer", suffix=target_lang)
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
