"""
논문 원문 텍스트에서 References/참고문헌 섹션을 찾아 항목별로 파싱하는
순수 텍스트 처리 로직 (네트워크 호출 없음).

번호가 매겨진 인용 스타일(`[12] ...`, `12. ...`, `12) ...`)을 우선 지원하고,
번호 스타일 항목이 하나도 파싱되지 않으면 (Author, Year) 스타일(APA류)
참고문헌 목록으로 폴백해서 파싱한다. 두 스타일을 한 문서에서 섞어 쓰는
경우는 드물어서 폴백 방식으로 충분하다. (Author, Year) 항목은 번호가 없어
"첫 저자 성(소문자) + 연도"를 키로 쓴다(예: "vaswani2017") - 프론트엔드의
본문 인용 표기 매칭(main.js의 parseAuthorYearKeys)과 반드시 같은 키 형식을
써야 한다.

대괄호 스타일은 순수 숫자(`[12]`)뿐 아니라 alpha 스타일 BibTeX가 흔히 쓰는
"저자 이니셜+연도" 키워드 키(`[BCV13]`, `[LBH+15]`, `[Dev86]` 등)도 그대로
키로 인정한다 - 프론트엔드의 CITATION_MARKER_RE도 동일한 키 형식을 감지한다.
"""

import re
from typing import Dict, List, Optional

# 헤더 단어의 글자 사이에 공백을 허용한다 - 드롭캡(첫 글자만 별도 폰트/굵기)
# 렌더링 시 "**R** **EFERENCES**"처럼 글자 사이에 공백이 끼어드는 경우까지
# 대응하기 위함이다(별표는 별도로 먼저 제거).
_HEADER_PREFIX_RE = re.compile(
    r"^\s*(?:r\s*e\s*f\s*e\s*r\s*e\s*n\s*c\s*e\s*s|b\s*i\s*b\s*l\s*i\s*o\s*g\s*r\s*a\s*p\s*h\s*y|"
    r"참\s*고\s*문\s*헌)\b",
    re.IGNORECASE,
)
_BRACKET_ENTRY_RE = re.compile(r"^\s*\[([A-Za-z0-9][A-Za-z0-9+\-]{0,9})\]\s*(.+)")
_PLAIN_NUMBERED_ENTRY_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+)")

# (Author, Year) 스타일 항목의 시작 줄 판별용 - "Surname, Initial..." 형태로
# 시작하는 줄을 새 항목의 시작으로 본다. 실제로 참고문헌 항목인지는 flush 시점에
# 텍스트 전체에서 연도((\d{4}))가 발견되는지로 한 번 더 검증한다(오탐 방지).
_AUTHOR_YEAR_ENTRY_START_RE = re.compile(r"^\s*([A-ZÀ-Ö][A-Za-zÀ-ÖØ-öø-ÿ\-']+),\s")
_YEAR_RE = re.compile(r"\((\d{4})[a-z]?\)")

_MAX_ENTRY_LENGTH = 500


def _match_section_header_prefix(line: str) -> Optional[str]:
    """줄이 References/Bibliography/참고문헌 헤더로 시작하면 그 뒤에 남는
    텍스트를 반환하고, 헤더로 시작하지 않으면 None을 반환합니다.

    일부 논문(특히 Nature류)은 헤더 다음에 개행 없이 바로 첫 항목이 이어져
    "**References** 48. Haist, F. ..."처럼 한 줄에 같이 붙어 나온다. 이
    경우 남는 텍스트("48. Haist, F. ...")가 실제로 참고문헌 항목의 시작처럼
    보일 때만 헤더로 인정한다 - 그냥 "References"로 시작하는 일반 문장을
    섹션 시작으로 오인하지 않기 위함이다. 헤더만 있고 뒤에 아무것도 없는
    깔끔한 줄은 그대로 빈 문자열을 반환한다.
    """
    no_asterisks = re.sub(r"\*+", "", line)
    m = _HEADER_PREFIX_RE.match(no_asterisks)
    if not m:
        return None

    remainder = no_asterisks[m.end():].strip()
    if not remainder:
        return remainder
    if (_BRACKET_ENTRY_RE.match(remainder) or _PLAIN_NUMBERED_ENTRY_RE.match(remainder)
            or _AUTHOR_YEAR_ENTRY_START_RE.match(remainder)):
        return remainder
    return None


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
        if any(_match_section_header_prefix(line) is not None for line in text.split("\n")):
            ref_start_page_idx = i
            break

    if ref_start_page_idx is None:
        return {}

    combined_text = "\n".join(p.get("text", "") or "" for p in pages[ref_start_page_idx:])
    lines = combined_text.split("\n")

    start_idx = 0
    for idx, line in enumerate(lines):
        remainder = _match_section_header_prefix(line)
        if remainder is not None:
            # 헤더 뒤에 같은 줄로 바로 이어지는 첫 항목이 있으면(remainder)
            # 그 부분은 버리지 않고 body_lines의 첫 줄로 그대로 살린다.
            lines[idx] = remainder
            start_idx = idx
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

    if not entries:
        entries = _parse_author_year_entries(body_lines)

    return entries


def _parse_author_year_entries(body_lines: List[str]) -> Dict[str, str]:
    """번호가 없는 (Author, Year) 스타일 참고문헌 목록을 파싱합니다.

    "Surname, Initial. ..." 형태로 시작하는 줄을 새 항목의 시작으로 보고,
    그 항목(여러 줄에 걸칠 수 있음)을 전부 모은 뒤 연도가 실제로 포함돼
    있는지 확인해서만 채택한다 - 그냥 대문자로 시작하는 일반 문장이 새
    항목으로 오인되는 것을 막기 위함이다. 키는 "성(소문자)+연도"
    (예: "vaswani2017")이며, 같은 저자가 같은 해에 여러 편을 낸 경우
    (2020a/2020b 등) 먼저 나온 항목만 유지한다.
    """
    entries: Dict[str, str] = {}
    current_first_line = None
    current_parts: List[str] = []

    def flush():
        if current_first_line is None:
            return
        text = re.sub(r"\s+", " ", " ".join(current_parts)).strip()
        if not text:
            return
        year_match = _YEAR_RE.search(text)
        surname_match = _AUTHOR_YEAR_ENTRY_START_RE.match(current_first_line)
        if not (year_match and surname_match):
            return
        key = f"{surname_match.group(1).lower()}{year_match.group(1)}"
        if key not in entries:
            entries[key] = text[:_MAX_ENTRY_LENGTH]

    for raw_line in body_lines:
        line = raw_line.strip()
        if not line:
            continue

        if _AUTHOR_YEAR_ENTRY_START_RE.match(line):
            flush()
            current_first_line = line
            current_parts = [line]
        elif current_first_line is not None:
            current_parts.append(line)

    flush()
    return entries
