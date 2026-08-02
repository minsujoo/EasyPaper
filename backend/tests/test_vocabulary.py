from pathlib import Path


def _create_doc(isolated_dirs, doc_id="paper-1", username="testuser"):
    pdf_path = Path(isolated_dirs["library_dir"]) / f"{doc_id}.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    isolated_dirs["db"].db_save_document(
        doc_id, username, "paper.pdf", str(pdf_path), 3, {"title": "Forecasting Paper"}
    )


def test_vocabulary_upsert_is_unique_per_paper_and_term(isolated_dirs):
    _create_doc(isolated_dirs)
    db = isolated_dirs["db"]
    payload = {
        "doc_id": "paper-1", "page_num": 2, "term": "Occupancy",
        "meaning_ko": "점유", "context_en": "Occupancy is forecast.",
        "context_ko": "점유 상태를 예측한다.", "paper_title": "Forecasting Paper",
    }
    first = db.db_upsert_vocabulary_card("testuser", payload)
    payload["term"] = "  OCCUPANCY "
    payload["meaning_ko"] = "점유 상태"
    second = db.db_upsert_vocabulary_card("testuser", payload)

    assert first["id"] == second["id"]
    cards = db.db_list_vocabulary_cards("testuser")
    assert len(cards) == 1
    assert cards[0]["meaning_ko"] == "점유 상태"


def test_save_card_stays_pending_when_anki_is_closed(test_client, isolated_dirs, monkeypatch):
    _create_doc(isolated_dirs)
    import routers.vocabulary as router

    async def no_start(_username):
        return None

    async def unavailable(_card, _username):
        raise RuntimeError("Anki에 연결할 수 없습니다.")

    monkeypatch.setattr(router, "_ensure_anki_started", no_start)
    monkeypatch.setattr(router, "sync_card", unavailable)
    monkeypatch.setattr(router, "append_obsidian_card", lambda *_: False)
    response = test_client.post("/api/vocabulary", json={
        "doc_id": "paper-1", "page_num": 1, "term": "occupancy",
        "meaning_ko": "점유", "context_en": "We forecast occupancy.",
        "context_ko": "점유 상태를 예측한다.", "paper_title": "Forecasting Paper",
        "sync_anki": True,
    })

    assert response.status_code == 200
    assert response.json()["card"]["anki_status"] == "pending"
    assert len(test_client.get("/api/vocabulary").json()["cards"]) == 1


def test_sync_marks_pending_card_as_synced(test_client, isolated_dirs, monkeypatch):
    _create_doc(isolated_dirs)
    db = isolated_dirs["db"]
    db.db_upsert_vocabulary_card("testuser", {
        "doc_id": "paper-1", "page_num": 1, "term": "occupancy",
        "meaning_ko": "점유", "context_en": "We forecast occupancy.",
        "context_ko": "점유 상태를 예측한다.", "paper_title": "Forecasting Paper",
    })
    import routers.vocabulary as router

    async def no_start(_username):
        return None

    async def synced(_card, _username):
        return 12345

    async def no_legacy(_username):
        return {"found": 0, "imported": 0, "existing": 0, "failed": 0, "mirrored": 0}

    monkeypatch.setattr(router, "_ensure_anki_started", no_start)
    monkeypatch.setattr(router, "sync_card", synced)
    monkeypatch.setattr(router, "sync_obsidian_deck", no_legacy)
    response = test_client.post("/api/vocabulary/sync")

    assert response.status_code == 200
    assert response.json() == {"synced": 1, "failed": 0, "obsidian": {"found": 0, "imported": 0, "existing": 0, "failed": 0, "mirrored": 0}}
    assert db.db_list_vocabulary_cards("testuser")[0]["anki_note_id"] == 12345


def test_other_users_document_cannot_be_used(test_client, isolated_dirs):
    _create_doc(isolated_dirs, "private", "someone-else")
    response = test_client.post("/api/vocabulary", json={
        "doc_id": "private", "term": "secret", "meaning_ko": "비밀",
        "context_en": "secret", "context_ko": "비밀", "paper_title": "Private",
    })
    assert response.status_code == 404


def test_parse_existing_obsidian_flashcard_format(tmp_path):
    from services.anki import parse_obsidian_deck
    deck = tmp_path / "deck.md"
    deck.write_text(
        "- sparse::희소한 #flashcards/english/vocab\n"
        "<!--SR:!2026-05-17,9,250-->\n"
        "  문맥: The matrix is ==sparse==.\n"
        "  해석: 행렬은 ==희소==하다.\n\n"
        "- vital::필수적인 #flashcards/english/vocab\n"
        "  문맥: It is ==vital==.\n  해석: 그것은 ==필수적==이다.\n",
        encoding="utf-8",
    )
    cards = parse_obsidian_deck(str(deck))
    assert [card["term"] for card in cards] == ["sparse", "vital"]
    assert cards[0]["context_en"] == "The matrix is sparse."


def test_obsidian_cards_are_mirrored_into_app_wordbook(isolated_dirs):
    db = isolated_dirs["db"]
    changed = db.db_import_obsidian_vocabulary_cards("testuser", [{
        "term": "sparse", "meaning_ko": "희소한", "context_en": "The matrix is sparse.",
        "context_ko": "행렬은 희소하다.", "anki_note_id": 321,
    }])
    assert changed == 1
    card = db.db_list_vocabulary_cards("testuser")[0]
    assert card["doc_id"] == "__obsidian__"
    assert card["anki_status"] == "synced"
    assert card["anki_note_id"] == 321


def test_review_uses_anki_scheduler_actions(test_client, monkeypatch):
    import routers.vocabulary as router
    calls = []

    async def no_start(_username):
        return None

    async def fake_invoke(action, params=None, *, username):
        calls.append((action, params, username))
        if action == "guiDeckReview":
            return True
        if action == "guiCurrentCard":
            return {
                "cardId": 77,
                "fields": {"Front": {"value": "sparse"}, "Back": {"value": "희소한"}},
                "buttons": [1, 2, 3, 4], "nextReviews": ["1분", "6분", "10분", "4일"],
                "deckName": "논문 영어",
            }
        if action in ("guiShowAnswer", "guiAnswerCard"):
            return True
        raise AssertionError(action)

    monkeypatch.setattr(router, "_ensure_anki_started", no_start)
    monkeypatch.setattr(router, "invoke", fake_invoke)
    started = test_client.post("/api/vocabulary/review/start")
    assert started.status_code == 200
    assert started.json()["card"]["front"] == "sparse"

    revealed = test_client.post("/api/vocabulary/review/reveal")
    assert revealed.status_code == 200
    answered = test_client.post("/api/vocabulary/review/answer", json={"ease": 3})
    assert answered.status_code == 200
    assert any(action == "guiAnswerCard" and params == {"ease": 3} for action, params, _ in calls)
