from services.chunker import parse_tagged_translation, sanitize_sentence_alignments


def test_trailing_missing_tags_remain_empty_instead_of_repeating_last_translation():
    sources = [
        "The result is effective.",
        "Table 3: Performance.",
        "Method Score",
        "OccProphet 15.38",
    ]

    cleaned, aligned = parse_tagged_translation("[S0] 결과는 효과적입니다.", sources)

    assert cleaned == "결과는 효과적입니다."
    assert [item["trans"] for item in aligned] == ["결과는 효과적입니다.", "", "", ""]


def test_internal_missing_tags_do_not_duplicate_a_donor_translation():
    sources = ["First.", "Missing one.", "Missing two.", "Last."]

    _, aligned = parse_tagged_translation("[S0] 첫째입니다. [S3] 마지막입니다.", sources)

    assert [item["trans"] for item in aligned] == ["첫째입니다.", "", "", "마지막입니다."]


def test_sanitize_sentence_alignments_collapses_only_long_duplicate_runs():
    repeated = [
        {"src": "Body.", "trans": "같은 번역"},
        {"src": "Table row 1", "trans": "같은 번역"},
        {"src": "Table row 2", "trans": "같은 번역"},
        {"src": "Table row 3", "trans": "같은 번역"},
    ]
    legitimate_pair = [
        {"src": "Yes.", "trans": "예."},
        {"src": "Yes again.", "trans": "예."},
    ]

    assert [item["trans"] for item in sanitize_sentence_alignments(repeated)] == [
        "같은 번역", "", "", "",
    ]
    assert sanitize_sentence_alignments(legitimate_pair) == legitimate_pair
