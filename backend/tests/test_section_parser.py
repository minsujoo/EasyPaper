"""services/section_parser.py의 순수 텍스트 처리 로직 테스트."""

from services.section_parser import detect_sections


def _page(page_num, text):
    return {"page_num": page_num, "text": text}


def test_detect_sections_splits_by_headers():
    pages = [
        _page(1, "Abstract\nThis paper studies X.\n\n1. Introduction\nExisting methods suffer from Y."),
        _page(2, "2. Related Work\nPrior work Z has limitations."),
        _page(3, "3. Method\nWe hypothesize A. Our approach uses B."),
        _page(4, "4. Results\nOur method improves accuracy by 10%."),
        _page(5, "5. Conclusion\nWe showed that A holds."),
        _page(6, "References\n[1] Someone. Some Paper. 2020."),
    ]
    sections = detect_sections(pages)
    assert "Existing methods suffer from Y" in sections["intro_related"]
    assert "Prior work Z has limitations" in sections["intro_related"]
    assert "We hypothesize A" in sections["method_results"]
    assert "improves accuracy by 10%" in sections["method_results"]
    assert "We showed that A holds" in sections["conclusion"]
    # References 섹션은 어느 버킷에도 섞여 들어가지 않는다
    assert "Someone" not in sections["conclusion"]
    assert "Someone" not in sections["method_results"]


def test_detect_sections_falls_back_to_positional_split_when_no_headers_found():
    pages = [_page(i, f"Page {i} body text without any recognizable section headers. " * 5) for i in range(1, 11)]
    sections = detect_sections(pages)
    assert sections["intro_related"]
    assert sections["method_results"]
    assert sections["conclusion"]


def test_detect_sections_handles_empty_pages():
    assert detect_sections([]) == {"intro_related": "", "method_results": "", "conclusion": ""}
    assert detect_sections([_page(1, "")]) == {"intro_related": "", "method_results": "", "conclusion": ""}


def test_detect_sections_truncates_to_char_limits():
    long_intro = "1. Introduction\n" + ("word " * 2000)
    pages = [_page(1, long_intro)]
    sections = detect_sections(pages)
    assert len(sections["intro_related"]) <= 4000
