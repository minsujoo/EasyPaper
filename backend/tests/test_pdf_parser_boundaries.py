"""페이지/블록 경계에서 잘린 학술 논문 본문을 복원하는 회귀 테스트."""

from services.pdf_parser import (
    _merge_incomplete_paragraphs,
    _partition_translation_blocks,
    _stitch_page_boundaries,
)


def _block(text, y0=100, y1=120):
    return (50.0, float(y0), 280.0, float(y1), text, False)


def test_caption_and_bottom_footnote_are_separated_from_body():
    blocks = [
        _block("Autonomous driving requires high efficiency for", 650, 680),
        _block("Figure 1. Comparison of rasterized and vectorized scenes.", 400, 450),
        _block("real-world deployment. An autonomous vehicle must perceive scenes.", 500, 600),
        _block("⋆Equal contribution; ⊠Corresponding author", 700, 720),
    ]

    body, auxiliary = _partition_translation_blocks(blocks, page_height=792)

    assert [block[4] for block in body] == [blocks[0][4], blocks[2][4]]
    assert [block[4] for block in auxiliary] == [blocks[1][4], blocks[3][4]]


def test_incomplete_body_blocks_merge_but_frontmatter_does_not():
    text = (
        "**VAD: Vectorized Scene Representation**\n\n"
        "Bo Jiang, Shaoyu Chen, Qing Xu\n\n"
        "**Abstract**\n\n"
        "This paper presents a complete abstract.\n\n"
        "**1. Introduction**\n\n"
        "Autonomous driving requires high efficiency for\n\n"
        "real-world deployment. An autonomous vehicle must perceive scenes."
    )

    merged = _merge_incomplete_paragraphs(text)

    assert "Representation**\n\nBo Jiang" in merged
    assert "efficiency for real-world deployment." in merged


def test_page_boundary_moves_only_trailing_incomplete_sentence():
    pages = [
        {
            "text": "",
            "_body_text": (
                "Recently, one holistic model is used. Some works directly output "
                "planning results without learning scene representation, which"
            ),
        },
        {
            "text": "",
            "_body_text": (
                "lacks interpretability and is difficult to optimize. Most works "
                "instead transform sensor data."
            ),
        },
    ]

    _stitch_page_boundaries(pages)

    assert pages[0]["_body_text"] == "Recently, one holistic model is used."
    assert pages[1]["_body_text"].startswith(
        "Some works directly output planning results without learning scene "
        "representation, which lacks interpretability"
    )
    assert "Most works instead" in pages[1]["_body_text"]


def test_page_boundary_handles_uppercase_proper_name_after_connector():
    pages = [
        {"text": "", "_body_text": "We follow BEVFormer and"},
        {"text": "", "_body_text": "MapTR, and further use query features.\n\n**3. Method**"},
    ]

    _stitch_page_boundaries(pages)

    assert pages[0]["_body_text"] == ""
    assert pages[1]["_body_text"].startswith(
        "We follow BEVFormer and MapTR, and further use query features."
    )


def test_complete_sentence_and_next_page_heading_are_not_moved():
    complete = [
        {"text": "", "_body_text": "The first page ends here."},
        {"text": "", "_body_text": "A new paragraph starts here."},
    ]
    heading = [
        {"text": "", "_body_text": "A dangling but intentional label"},
        {"text": "", "_body_text": "**2. Related Work**\n\nPrior work is broad."},
    ]

    _stitch_page_boundaries(complete)
    _stitch_page_boundaries(heading)

    assert complete[0]["_body_text"] == "The first page ends here."
    assert complete[1]["_body_text"] == "A new paragraph starts here."
    assert heading[0]["_body_text"] == "A dangling but intentional label"
