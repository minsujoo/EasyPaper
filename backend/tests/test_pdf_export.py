"""services/pdf_export.py 테스트 - 번역/하이라이트/밑줄/메모가 포함된 PDF 생성."""

import fitz
import pytest

from services.pdf_export import generate_annotated_pdf


@pytest.fixture()
def sample_pdf(tmp_path):
    """2페이지짜리 간단한 영문 PDF를 만들어 경로를 반환한다."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_textbox(
        fitz.Rect(50, 50, 545, 700),
        "Transformer Architecture\n\nThe transformer relies on self-attention mechanisms.",
        fontsize=12, fontname="helv",
    )
    p2 = doc.new_page()
    p2.insert_textbox(
        fitz.Rect(50, 50, 545, 700),
        "Results\n\nOur experiments show strong performance.",
        fontsize=12, fontname="helv",
    )
    path = tmp_path / "source.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_generate_pdf_preserves_original_pages(sample_pdf):
    result = generate_annotated_pdf(sample_pdf, "Test Paper", {}, {}, {})
    doc = fitz.open("pdf", result)
    assert doc.page_count == 2  # 주석/번역/메모가 전부 없으면 원본 페이지 수 그대로
    doc.close()


def test_generate_pdf_bakes_highlight_annotation(sample_pdf):
    annotations = {"page_1": [{"type": "highlight", "text": "self-attention mechanisms", "color": "#eab308"}]}
    result = generate_annotated_pdf(sample_pdf, "Test Paper", annotations, {}, {})
    doc = fitz.open("pdf", result)
    page1_annots = list(doc[0].annots())
    assert len(page1_annots) == 1
    assert page1_annots[0].type[1] == "Highlight"
    doc.close()


def test_generate_pdf_bakes_underline_annotation(sample_pdf):
    annotations = {"page_1": [{"type": "underline", "text": "self-attention mechanisms", "color": "#ef4444"}]}
    result = generate_annotated_pdf(sample_pdf, "Test Paper", annotations, {}, {})
    doc = fitz.open("pdf", result)
    page1_annots = list(doc[0].annots())
    assert len(page1_annots) == 1
    assert page1_annots[0].type[1] == "Underline"
    doc.close()


def test_generate_pdf_skips_annotation_when_text_not_found(sample_pdf):
    """페이지에 없는 문구를 하이라이트하려 해도 예외 없이 조용히 건너뛰어야 한다."""
    annotations = {"page_1": [{"type": "highlight", "text": "this text does not exist anywhere", "color": "#eab308"}]}
    result = generate_annotated_pdf(sample_pdf, "Test Paper", annotations, {}, {})
    doc = fitz.open("pdf", result)
    assert len(list(doc[0].annots())) == 0
    doc.close()


def test_generate_pdf_appends_translation_pages(sample_pdf):
    translations = {
        "1": "트랜스포머 아키텍처는 셀프 어텐션 메커니즘에 의존합니다.",
        "2": "우리의 실험은 강력한 성능을 보여줍니다.",
    }
    result = generate_annotated_pdf(sample_pdf, "테스트 논문", {}, translations, {})
    doc = fitz.open("pdf", result)
    assert doc.page_count > 2, "번역 섹션이 추가되어 원본 페이지 수보다 많아야 한다"

    full_text = "".join(page.get_text() for page in doc)
    assert "테스트 논문" in full_text
    assert "셀프 어텐션" in full_text
    doc.close()


def test_generate_pdf_appends_memo_page(sample_pdf):
    memos = {"page_1": [{"content": "이 부분이 핵심입니다.", "sentenceText": "self-attention mechanisms"}]}
    result = generate_annotated_pdf(sample_pdf, "Test Paper", {}, {}, memos)
    doc = fitz.open("pdf", result)
    assert doc.page_count > 2

    full_text = "".join(page.get_text() for page in doc)
    assert "이 부분이 핵심입니다" in full_text
    doc.close()


def test_generate_pdf_with_no_memo_content_adds_no_memo_page(sample_pdf):
    """memo content가 비어있으면 메모 섹션 자체를 추가하지 않아야 한다."""
    memos = {"page_1": [{"content": "  ", "sentenceText": "something"}]}
    result = generate_annotated_pdf(sample_pdf, "Test Paper", {}, {}, memos)
    doc = fitz.open("pdf", result)
    assert doc.page_count == 2
    doc.close()


def test_generate_pdf_handles_invalid_page_key_gracefully(sample_pdf):
    """존재하지 않는 페이지 번호나 잘못된 키 형식이 들어와도 에러 없이 무시해야 한다."""
    annotations = {
        "page_999": [{"type": "highlight", "text": "anything", "color": "#eab308"}],
        "not-a-page-key": [{"type": "highlight", "text": "anything", "color": "#eab308"}],
    }
    result = generate_annotated_pdf(sample_pdf, "Test Paper", annotations, {}, {})
    doc = fitz.open("pdf", result)
    assert doc.page_count == 2
    doc.close()
