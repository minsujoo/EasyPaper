"""
논문 원문 텍스트에서 References/참고문헌 섹션을 찾아 번호별 항목으로
파싱하는 순수 텍스트 처리 로직 (네트워크 호출 없음).

번호가 매겨진 인용 스타일(`[12] ...` 또는 `12. ...`)만 지원한다 - (Author,
Year) 스타일은 패턴이 훨씬 다양하고 오탐이 잦아 이번 범위에서는 제외했다.
CS/ML 논문 대부분이 번호 인용 스타일을 쓰므로 이 앱의 실제 사용처에서는
충분히 유용하다.
"""

import re
from typing import Dict, List

_SECTION_HEADER_RE = re.compile(
    r"^\s*\**\s*(references|bibliography|참고\s*문헌)\s*\**\s*$",
    re.IGNORECASE,
)
_BRACKET_ENTRY_RE = re.compile(r"^\s*\[(\d{1,3})\]\s*(.+)")
_PLAIN_NUMBERED_ENTRY_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.+)")

_MAX_ENTRY_LENGTH = 500


def extract_reference_list(pages: List[dict]) -> Dict[str, str]:
    """페이지 목록(각 {"text": ...} 포함)에서 참고문헌 목록을 파싱합니다.

    반환값: {"12": "Vaswani et al. Attention Is All You Need. 2017.", ...}
    섹션을 찾지 못하거나 파싱에 실패하면 빈 딕셔너리를 반환합니다(호출부가
    이 실패를 전체 기능 중단으로 이어가지 않도록).
    """
    try:
        return _extract_reference_list_impl(pages)
    except Exception:
        return {}


def _extract_reference_list_impl(pages: List[dict]) -> Dict[str, str]:
    ref_start_page_idx = None
    for i in range(len(pages) - 1, -1, -1):
        text = pages[i].get("text", "") or ""
        if any(_SECTION_HEADER_RE.match(line.strip()) for line in text.split("\n")):
            ref_start_page_idx = i
            break

    if ref_start_page_idx is None:
        return {}

    combined_text = "\n".join(p.get("text", "") or "" for p in pages[ref_start_page_idx:])
    lines = combined_text.split("\n")

    start_idx = 0
    for idx, line in enumerate(lines):
        if _SECTION_HEADER_RE.match(line.strip()):
            start_idx = idx + 1
            break
    body_lines = lines[start_idx:]

    entries: Dict[str, str] = {}
    current_num = None
    current_parts: List[str] = []

    def flush():
        if current_num is not None:
            text = re.sub(r"\s+", " ", " ".join(current_parts)).strip()
            if text:
                entries[current_num] = text[:_MAX_ENTRY_LENGTH]

    for raw_line in body_lines:
        line = raw_line.strip()
        if not line:
            continue

        m = _BRACKET_ENTRY_RE.match(line) or _PLAIN_NUMBERED_ENTRY_RE.match(line)
        if m:
            flush()
            current_num = m.group(1)
            current_parts = [m.group(2)]
        elif current_num is not None:
            current_parts.append(line)

    flush()
    return entries
