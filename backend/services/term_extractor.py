"""
논문 원문에서 저자가 새로 만든(coined) 용어/약어 후보를 뽑아내는 순수 텍스트
처리 로직 (네트워크 호출/LLM 호출 없음).

읽기 전 브리핑의 "용어집" 섹션은 독자가 해당 분야는 알아도 이 논문이 새로
붙인 이름(예: "HNEN")은 모를 수 있다는 문제를 다룬다. 이 모듈은 정규식으로
전체 문서를 스캔해 후보를 값싸게 넓게 모으고, 실제로 이 논문 고유의 용어인지
최종 판단은 LLM 합성 단계(llm_client.generate_reading_primer)에 맡긴다 -
여기서는 재현율(누락 방지)을 우선하고 정밀도는 프롬프트 지시로 보완한다.
"""

import re
from typing import Dict, List, Optional

_MAX_CANDIDATES = 10
_CONTEXT_WINDOW = 100

# "Full Term (ACRONYM)" 형태의 최초 정의. 2~7개의 대문자로 시작하는 단어가
# 이어지고 뒤에 대문자 약어(2~8자)가 괄호로 붙는 경우만 인정한다.
_ACRONYM_DEF_RE = re.compile(
    r"\b((?:[A-Z][a-zA-Z]*\s+){1,6}[A-Z][a-zA-Z]*)\s*\(([A-Z]{2,8})\)"
)
# "we call/term/refer to/denote/define ... as X" 패턴.
_COINED_TERM_RE = re.compile(
    r"\bwe\s+(?:call|term|refer to|denote|define)\b[^.]{0,80}?\bas\s+"
    r"[\"“]?([A-Z][A-Za-z0-9\-]{1,40})[\"”]?",
    re.IGNORECASE,
)
# "we introduce/propose X" 패턴.
_INTRODUCE_TERM_RE = re.compile(
    r"\bwe\s+(?:introduce|propose)\s+(?:a\s+|an\s+|the\s+|new\s+|novel\s+)*"
    r"[\"“]?([A-Z][A-Za-z0-9\-]{1,40})[\"”]?",
)
# "... a novel method called X" 처럼 "introduce/propose"와 용어 사이에 수식어가
# 낀 경우까지 잡기 위한 보조 패턴.
_CALLED_TERM_RE = re.compile(
    r"\bcalled\s+[\"“]?([A-Z][A-Za-z0-9\-]{1,40})[\"”]?"
)

# 이미 널리 알려진 일반 배경지식 약어는 후보에서 미리 제외한다(정밀도 보완).
# 그 외 애매한 경우의 최종 판단은 LLM 합성 프롬프트가 맡는다.
_COMMON_ACRONYM_STOPLIST = {
    "CNN", "RNN", "LSTM", "GAN", "GPU", "CPU", "API", "PDF", "NLP", "SVM",
    "MLP", "VAE", "ROC", "AUC", "IID", "SGD", "RELU", "BERT", "GPT", "AI",
    "ML", "DL", "USA", "UK", "EU",
}


def extract_candidate_terms(pages: List[dict]) -> List[Dict[str, Optional[str]]]:
    """페이지 목록(각 {"text": ..., "page_num": ...} 포함)에서 논문 고유
    용어 후보를 뽑는다. 실패해도 예외를 던지지 않고 빈 리스트를 반환한다."""
    try:
        return _extract_candidate_terms_impl(pages)
    except Exception:
        return []


def _extract_candidate_terms_impl(pages: List[dict]) -> List[Dict[str, Optional[str]]]:
    candidates = []
    seen = set()

    for page in pages:
        text = page.get("text", "") or ""
        page_num = page.get("page_num")

        for pattern, is_acronym_def in (
            (_ACRONYM_DEF_RE, True),
            (_COINED_TERM_RE, False),
            (_INTRODUCE_TERM_RE, False),
            (_CALLED_TERM_RE, False),
        ):
            for m in pattern.finditer(text):
                term = (m.group(2) if is_acronym_def else m.group(1)).strip("\"“”").strip()
                if not term:
                    continue
                if is_acronym_def and term.upper() in _COMMON_ACRONYM_STOPLIST:
                    continue
                key = term.lower()
                if key in seen:
                    continue
                seen.add(key)

                start = max(0, m.start() - _CONTEXT_WINDOW)
                end = min(len(text), m.end() + _CONTEXT_WINDOW)
                context = re.sub(r"\s+", " ", text[start:end]).strip()
                candidates.append({"term": term, "context_sentence": context, "page_num": page_num})

                if len(candidates) >= _MAX_CANDIDATES:
                    return candidates
    return candidates
