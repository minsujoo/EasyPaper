import pytest

import services.paper_note as paper_note


def _create_doc(isolated_dirs, doc_id="doc-note", username="testuser"):
    db = isolated_dirs["db"]
    library_dir = isolated_dirs["library_dir"]
    doc_dir = library_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = doc_dir / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    db.db_save_document(
        doc_id,
        username,
        "paper.pdf",
        str(pdf_path),
        8,
        {"title": "Structured Paper"},
    )
    return pdf_path


def test_note_list_includes_existing_documents_without_note(isolated_dirs):
    _create_doc(isolated_dirs)
    notes = isolated_dirs["db"].db_list_paper_notes("testuser")

    assert len(notes) == 1
    assert notes[0]["doc_id"] == "doc-note"
    assert notes[0]["status"] == "not_started"
    assert notes[0]["content"] is None


def test_note_status_and_content_are_persisted(isolated_dirs):
    _create_doc(isolated_dirs)
    db = isolated_dirs["db"]

    db.db_set_paper_note_status("doc-note", "한국어", "generating")
    assert db.db_get_paper_note("doc-note")["status"] == "generating"

    db.db_save_paper_note("doc-note", "한국어", {"summary": "핵심 요약"})
    saved = db.db_get_paper_note("doc-note")
    assert saved["status"] == "ready"
    assert saved["content"] == {"summary": "핵심 요약"}
    assert saved["error"] is None


@pytest.mark.asyncio
async def test_save_note_reuses_analysis_and_selects_figures_and_tables(
    isolated_dirs, monkeypatch
):
    pdf_path = _create_doc(isolated_dirs)
    monkeypatch.setattr(paper_note, "LIBRARY_DIR", str(isolated_dirs["library_dir"]))

    import services.pdf_parser as pdf_parser

    detected = [
        {
            "page": 2, "left": 5, "top": 10, "width": 80, "height": 30,
            "label": "Figure 2", "caption": "Second figure",
        },
        {
            "page": 1, "left": 5, "top": 10, "width": 80, "height": 30,
            "label": "Figure 1", "caption": "First figure",
        },
        {
            "page": 4, "left": 5, "top": 10, "width": 80, "height": 30,
            "label": "Table 1", "caption": "Main results",
        },
        {
            "page": 3, "left": 5, "top": 10, "width": 80, "height": 30,
            "label": "(3)", "caption": None,
        },
    ]
    monkeypatch.setattr(pdf_parser, "extract_pdf_images", lambda _: detected)

    analysis = {
        "hook": "한 줄 핵심",
        "summary": "전체 요약",
        "contributions": ["기여 1"],
        "method_summary": "방법",
        "results_summary": "결과",
        "limitations": "한계",
        "takeaways": ["교훈"],
        "keywords": ["키워드"],
        "experiment_flow": [],
        "glossary": [],
    }
    note = await paper_note.save_note_from_analysis(
        "doc-note",
        {"title": "Structured Paper"},
        analysis,
        str(pdf_path),
    )

    assert note["summary"] == "전체 요약"
    assert [item["label"] for item in note["visuals"]] == [
        "Figure 1", "Figure 2", "Table 1"
    ]
    saved = isolated_dirs["db"].db_get_paper_note("doc-note")
    assert saved["status"] == "ready"
    assert saved["content"]["one_line_summary"] == "한 줄 핵심"


def test_notes_api_lists_only_current_users_documents(test_client, isolated_dirs):
    _create_doc(isolated_dirs, "mine", "testuser")
    _create_doc(isolated_dirs, "other", "someone-else")

    response = test_client.get("/api/notes")
    assert response.status_code == 200
    assert [item["doc_id"] for item in response.json()["notes"]] == ["mine"]
