"""services/reference_parser.py 테스트 - References/참고문헌 섹션 파싱."""

from services.reference_parser import extract_reference_list


def test_parses_bracket_style_numbered_references():
    pages = [
        {"page_num": 1, "text": "Title\n\nAbstract\n\nBody text..."},
        {"page_num": 9, "text": (
            "Conclusion\n\nWe presented the Transformer.\n\n"
            "References\n\n"
            "[1] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. Neural machine "
            "translation by jointly learning to align and translate. CoRR, 2014.\n\n"
            "[2] Jianpeng Cheng, Li Dong, and Mirella Lapata. Long short-term "
            "memory-networks for machine reading. In EMNLP, 2016."
        )},
    ]
    refs = extract_reference_list(pages)
    assert set(refs.keys()) == {"1", "2"}
    assert "Bahdanau" in refs["1"]
    assert "Cheng" in refs["2"]


def test_parses_plain_numbered_references_with_korean_header():
    pages = [
        {"page_num": 1, "text": "논문 제목\n\n초록\n\n본문 내용..."},
        {"page_num": 5, "text": (
            "결론\n\n참고문헌\n\n"
            "1. Smith, J. Deep Learning Basics. NeurIPS 2020.\n\n"
            "2. Lee, K. Attention Mechanisms Survey. ICML 2021."
        )},
    ]
    refs = extract_reference_list(pages)
    assert refs == {
        "1": "Smith, J. Deep Learning Basics. NeurIPS 2020.",
        "2": "Lee, K. Attention Mechanisms Survey. ICML 2021.",
    }


def test_returns_empty_dict_when_no_references_section():
    pages = [{"page_num": 1, "text": "그냥 본문 텍스트입니다. 참고문헌 섹션 없음."}]
    assert extract_reference_list(pages) == {}


def test_returns_empty_dict_for_empty_input():
    assert extract_reference_list([]) == {}


def test_merges_multiline_entries():
    pages = [{"page_num": 1, "text": (
        "References\n\n"
        "[1] A very long author list that continues\n"
        "onto a second physical line before the year\n"
        "and publisher information. 2021."
    )}]
    refs = extract_reference_list(pages)
    assert "continues" in refs["1"]
    assert "publisher information" in refs["1"]


def test_truncates_excessively_long_entries():
    long_text = "A. Author. " + ("Very long title words. " * 100)
    pages = [{"page_num": 1, "text": f"References\n\n[1] {long_text}"}]
    refs = extract_reference_list(pages)
    assert len(refs["1"]) <= 500


def test_does_not_crash_on_malformed_page_data():
    """text 키가 없거나 None이어도 예외 없이 빈 결과를 반환해야 한다."""
    assert extract_reference_list([{"page_num": 1}]) == {}
    assert extract_reference_list([{"page_num": 1, "text": None}]) == {}
