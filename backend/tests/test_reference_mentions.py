from services.reference_mentions import build_reference_mentions


def test_builds_title_and_author_hints_for_ieee_reference():
    mentions = build_reference_mentions({
        "1": (
            'K. He, X. Zhang, S. Ren, and J. Sun, '
            '"Deep residual learning for image recognition," in Proc. CVPR, 2016.'
        )
    })

    assert mentions["1"]["titles"] == ["Deep residual learning for image recognition"]
    assert mentions["1"]["authors"][:2] == ["He", "Zhang"]


def test_builds_apa_title_and_first_author_hint():
    mentions = build_reference_mentions({
        "smith2021": (
            "Smith, J., Doe, A., & Kim, S. (2021). "
            "Learning occupancy from monocular cameras. Journal of Vision, 8(2)."
        )
    })

    assert "Learning occupancy from monocular cameras" in mentions["smith2021"]["titles"]
    assert mentions["smith2021"]["authors"][0] == "Smith"


def test_ignores_short_or_venue_like_title_candidates():
    mentions = build_reference_mentions({
        "2": "A. Li. A Study. Proceedings of CVPR. 2020."
    })

    assert mentions["2"]["titles"] == []
