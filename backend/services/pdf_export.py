"""
번역/주석(하이라이트·밑줄·메모)이 포함된 PDF 내보내기.

하이라이트/밑줄/메모는 브라우저 localStorage에만 저장되고 백엔드에는 전혀
없다 - 그래서 이 기능은 프론트엔드가 내보내기 요청 시 그 데이터를 함께
보내야 하고(POST body), 서버는 PyMuPDF(fitz)로 그 정보를 원본 PDF 위에
실제 PDF 주석 객체로 구워 넣은 뒤, 번역 텍스트와 메모 요약을 별도 섹션으로
이어붙여 하나의 PDF로 합쳐 반환한다.

브라우저에서 저장하는 하이라이트/밑줄은 문자 offset(해당 페이지 textLayer
기준)이 아니라 원본 텍스트 문자열 자체도 함께 저장하므로, PDF 좌표계로
직접 변환하는 대신 PyMuPDF의 page.search_for()로 그 문자열을 페이지에서
찾아 위치(quad)를 알아내는 방식을 쓴다 - pdf.js와 PyMuPDF의 텍스트 추출
결과가 100% 동일하지는 않아 완벽하지 않지만, 대부분의 경우 잘 맞고 실패해도
그 항목만 조용히 건너뛴다(전체 내보내기가 실패하지 않음).
"""

import io
import re
from html import escape as html_escape
from typing import Optional

import fitz

_HIGHLIGHT_DEFAULT_COLOR = (1, 0.92, 0.23)  # 노랑
_UNDERLINE_DEFAULT_COLOR = (0.93, 0.26, 0.26)  # 빨강

# PyMuPDF 내장 CJK 폰트 - 기본 14종 폰트(helv 등)는 한글 글리프가 없어
# "??"로만 표시되므로, 한글이 섞인 텍스트는 반드시 이 폰트를 써야 한다.
_KOREAN_TEXTBOX_FONT = "korea"


def _hex_to_rgb01(hex_color: Optional[str], fallback: tuple) -> tuple:
    if not hex_color:
        return fallback
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return fallback
    try:
        r = int(hex_color[0:2], 16) / 255
        g = int(hex_color[2:4], 16) / 255
        b = int(hex_color[4:6], 16) / 255
        return (r, g, b)
    except ValueError:
        return fallback


def _apply_annotations_to_page(page: fitz.Page, annotations: list) -> None:
    """이 페이지에 저장된 하이라이트/밑줄을 실제 PDF 주석으로 굽는다."""
    for ann in annotations:
        text = (ann.get("text") or "").strip()
        ann_type = ann.get("type")
        if not text or ann_type not in ("highlight", "underline"):
            continue

        color = _hex_to_rgb01(
            ann.get("color"),
            _HIGHLIGHT_DEFAULT_COLOR if ann_type == "highlight" else _UNDERLINE_DEFAULT_COLOR,
        )
        try:
            # 개행이 포함된 긴 인용은 검색이 실패하기 쉬우므로 공백으로 정규화
            search_text = " ".join(text.split())
            quads = page.search_for(search_text, quads=True)
        except Exception:
            quads = []
        if not quads:
            continue

        try:
            if ann_type == "highlight":
                annot = page.add_highlight_annot(quads)
            else:
                annot = page.add_underline_annot(quads)
            annot.set_colors(stroke=color)
            annot.update()
        except Exception:
            continue


def _run_story_pages(html: str) -> fitz.Document:
    """HTML을 fitz.Story로 흘려보내 필요한 만큼 자동으로 페이지를 나눈
    새 PDF 문서를 만들어 반환한다 (한글 포함 텍스트도 자연스럽게 렌더링됨)."""
    buf = io.BytesIO()
    story = fitz.Story(html=html)
    writer = fitz.DocumentWriter(buf)
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (48, 48, -48, -48)

    more = 1
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()

    buf.seek(0)
    return fitz.open("pdf", buf.read())


def _build_translation_html(doc_title: str, translations: dict) -> str:
    parts = [
        '<div style="font-family: sans-serif;">',
        f'<h1 style="font-size: 20pt; margin-bottom: 4pt;">{html_escape(doc_title)}</h1>',
        '<h2 style="font-size: 13pt; color: #555; margin-top: 0;">번역 (Translation)</h2>',
    ]
    for page_num in sorted((int(k) for k in translations.keys())):
        text = (translations.get(str(page_num)) or "").strip()
        if not text:
            continue
        parts.append(f'<h3 style="font-size: 12pt; margin-top: 18pt; color: #333;">{page_num}페이지</h3>')
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if para:
                parts.append(f'<p style="font-size: 11pt; line-height: 1.6; text-align: justify;">{html_escape(para)}</p>')
    parts.append("</div>")
    return "".join(parts)


def _build_memo_html(memos: dict) -> Optional[str]:
    entries = []
    for page_key in sorted(memos.keys(), key=lambda k: int(k.replace("page_", "")) if k.replace("page_", "").isdigit() else 0):
        page_num = page_key.replace("page_", "")
        for memo in memos.get(page_key, []):
            content = (memo.get("content") or "").strip()
            if not content:
                continue
            anchor = (memo.get("sentenceText") or "").strip()
            entries.append((page_num, anchor, content))

    if not entries:
        return None

    parts = [
        '<div style="font-family: sans-serif;">',
        '<h2 style="font-size: 16pt;">메모 (Memos)</h2>',
    ]
    for page_num, anchor, content in entries:
        parts.append(f'<h4 style="font-size: 11pt; margin-top: 14pt; margin-bottom: 2pt; color: #333;">{page_num}페이지</h4>')
        if anchor:
            snippet = anchor[:150] + ("…" if len(anchor) > 150 else "")
            parts.append(f'<p style="font-size: 9.5pt; color: #888; margin: 0 0 3pt; font-style: italic;">"{html_escape(snippet)}"</p>')
        parts.append(f'<p style="font-size: 10.5pt; line-height: 1.5; margin-top: 0;">{html_escape(content)}</p>')
    parts.append("</div>")
    return "".join(parts)


def generate_annotated_pdf(
    pdf_path: str,
    doc_title: str,
    annotations: dict,
    translations: dict,
    memos: dict,
) -> bytes:
    """원본 PDF에 하이라이트/밑줄을 구워 넣고, 번역·메모 섹션을 이어붙인
    최종 PDF를 바이트로 반환한다.

    annotations: {"page_1": [{"type", "text", "color"}, ...], ...} (프론트 localStorage 형식)
    translations: {"1": "번역 텍스트", ...} (페이지 번호 -> 번역 전문)
    memos: {"page_1": [{"content", "sentenceText"}, ...], ...} (프론트 localStorage 형식)
    """
    doc = fitz.open(pdf_path)

    for page_key, page_annotations in (annotations or {}).items():
        m = re.match(r"page_(\d+)$", page_key)
        if not m:
            continue
        page_idx = int(m.group(1)) - 1
        if 0 <= page_idx < doc.page_count and page_annotations:
            _apply_annotations_to_page(doc[page_idx], page_annotations)

    if translations:
        translation_doc = _run_story_pages(_build_translation_html(doc_title, translations))
        doc.insert_pdf(translation_doc)
        translation_doc.close()

    memo_html = _build_memo_html(memos or {})
    if memo_html:
        memo_doc = _run_story_pages(memo_html)
        doc.insert_pdf(memo_doc)
        memo_doc.close()

    result = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return result
