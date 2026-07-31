"""영역 설명 팝업의 독립 채팅 세션과 숨김 프롬프트 회귀 테스트."""

from unittest.mock import patch

import pytest

import routers.chat as chat_module


DOC_ID = "explanation-doc"


@pytest.fixture(autouse=True)
def _cleanup_session():
    yield
    chat_module.sessions.pop(DOC_ID, None)


def _create_doc(isolated_dirs):
    isolated_dirs["db"].db_save_document(
        DOC_ID, "testuser", "paper.pdf", "/x/paper.pdf", 1, {"title": "Paper"}
    )
    chat_module.sessions[DOC_ID] = {
        "pdf_path": "/x/paper.pdf",
        "filename": "paper.pdf",
        "pages": [{"page_num": 1, "text": "Paper context."}],
        "total_pages": 1,
        "metadata": {"title": "Paper"},
        "from_library": True,
        "username": "testuser",
    }


async def _fake_stream_chat(
    system_prompt, history_messages, session_id=None, page_image_b64=None
):
    yield f"answer:{session_id}"


def test_hidden_explanation_prompt_uses_separate_session_and_is_not_saved(
    test_client, isolated_dirs
):
    _create_doc(isolated_dirs)
    explanation_id = f"explain:{DOC_ID}:first"

    with patch.object(chat_module, "stream_chat", new=_fake_stream_chat):
        response = test_client.post(
            "/api/chat/stream",
            json={
                "session_id": DOC_ID,
                "chat_session_id": explanation_id,
                "hidden_user_message": True,
                "messages": [{"role": "user", "content": "internal explanation prompt"}],
            },
        )

    assert response.status_code == 200
    assert explanation_id in response.text
    assert isolated_dirs["db"].db_get_chat_history(DOC_ID) == []
    assert isolated_dirs["db"].db_get_chat_history(explanation_id) == [
        {"role": "assistant", "content": f"answer:{explanation_id}"}
    ]


def test_each_explanation_id_keeps_an_independent_history(test_client, isolated_dirs):
    _create_doc(isolated_dirs)
    ids = [f"explain:{DOC_ID}:one", f"explain:{DOC_ID}:two"]

    with patch.object(chat_module, "stream_chat", new=_fake_stream_chat):
        for explanation_id in ids:
            response = test_client.post(
                "/api/chat/stream",
                json={
                    "session_id": DOC_ID,
                    "chat_session_id": explanation_id,
                    "messages": [{"role": "user", "content": explanation_id}],
                },
            )
            assert response.status_code == 200

    first = isolated_dirs["db"].db_get_chat_history(ids[0])
    second = isolated_dirs["db"].db_get_chat_history(ids[1])
    assert first[0]["content"] == ids[0]
    assert second[0]["content"] == ids[1]
    assert len(first) == len(second) == 2


def test_explanation_session_must_belong_to_requested_document(
    test_client, isolated_dirs
):
    _create_doc(isolated_dirs)
    response = test_client.post(
        "/api/chat/stream",
        json={
            "session_id": DOC_ID,
            "chat_session_id": "explain:another-document:forged",
            "messages": [{"role": "user", "content": "question"}],
        },
    )
    assert response.status_code == 400
