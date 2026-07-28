"""services/term_extractor.py의 순수 텍스트 처리 로직 테스트."""

from services.term_extractor import extract_candidate_terms


def _page(page_num, text):
    return {"page_num": page_num, "text": text}


def test_extracts_acronym_definition():
    pages = [_page(1, "We propose the Hierarchical Neural Encoding Neglect (HNEN) framework.")]
    terms = [t["term"] for t in extract_candidate_terms(pages)]
    assert "HNEN" in terms


def test_extracts_coined_term_phrase():
    pages = [_page(1, "We call this phenomenon as EncodingDrift throughout the paper.")]
    terms = [t["term"] for t in extract_candidate_terms(pages)]
    assert "EncodingDrift" in terms


def test_extracts_introduced_term_phrase():
    pages = [_page(1, "We introduce a novel method called ViEEG for cross-modal encoding.")]
    terms = [t["term"] for t in extract_candidate_terms(pages)]
    assert "ViEEG" in terms


def test_filters_common_acronyms():
    pages = [_page(1, "We use a Convolutional Neural Network (CNN) as a baseline encoder.")]
    terms = [t["term"] for t in extract_candidate_terms(pages)]
    assert "CNN" not in terms


def test_deduplicates_repeated_terms_across_pages():
    pages = [
        _page(1, "We propose the Hierarchical Neural Encoding Neglect (HNEN) framework."),
        _page(2, "As discussed, the Hierarchical Neural Encoding Neglect (HNEN) framework generalizes well."),
    ]
    terms = [t["term"] for t in extract_candidate_terms(pages)]
    assert terms.count("HNEN") == 1


def test_returns_empty_list_when_no_candidates():
    pages = [_page(1, "This is a plain sentence with no defined terms at all.")]
    assert extract_candidate_terms(pages) == []
