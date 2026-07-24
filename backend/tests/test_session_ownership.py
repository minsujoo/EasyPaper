"""세션 기반 엔드포인트(upload/jobs/translate/insight/chat)들이 다른 사용자의
세션에 접근하는 것을 막는지 확인하는 회귀 테스트.

test_client 픽스처는 get_current_user를 "testuser"로 고정하므로, "otheruser"
소유의 세션을 만들어두고 testuser로 접근했을 때 404가 나는지 검증한다
(문서의 존재 여부 자체를 노출하지 않기 위해 403이 아니라 404로 통일).
"""

import pytest

import routers.upload as upload_module


@pytest.fixture(autouse=True)
def _cleanup_fake_sessions():
    yield
    for key in list(upload_module.sessions.keys()):
        if key.startswith("own-test-"):
            del upload_module.sessions[key]


def _inject_session(session_id, username, text="Sample paper text."):
    upload_module.sessions[session_id] = {
        "pdf_path": f"/x/{session_id}.pdf",
        "filename": f"{session_id}.pdf",
        "pages": [{"page_num": 1, "text": text}],
        "total_pages": 1,
        "metadata": {},
        "from_library": True,
        "username": username,
    }


def test_get_session_rejects_other_users_session(test_client):
    _inject_session("own-test-1", "otheruser")
    res = test_client.get("/api/session/own-test-1")
    assert res.status_code == 404


def test_get_session_allows_owner(test_client):
    _inject_session("own-test-2", "testuser")
    res = test_client.get("/api/session/own-test-2")
    assert res.status_code == 200


def test_delete_session_rejects_other_users_session(test_client):
    _inject_session("own-test-3", "otheruser")
    res = test_client.delete("/api/session/own-test-3")
    assert res.status_code == 404
    # 삭제되지 않고 그대로 남아 있어야 한다
    assert "own-test-3" in upload_module.sessions


def test_get_pdf_path_rejects_other_users_session(test_client):
    _inject_session("own-test-4", "otheruser")
    res = test_client.get("/api/pdf/own-test-4")
    assert res.status_code == 404


def test_translation_status_rejects_other_users_session(test_client):
    _inject_session("own-test-5", "otheruser")
    res = test_client.get("/api/translation-status/own-test-5")
    assert res.status_code == 404


def test_chat_history_rejects_other_users_session(test_client):
    _inject_session("own-test-6", "otheruser")
    res = test_client.get("/api/chat/own-test-6/history")
    assert res.status_code == 404


def test_job_status_rejects_other_users_session(test_client):
    _inject_session("own-test-7", "otheruser")
    res = test_client.get("/api/jobs/own-test-7/status")
    assert res.status_code == 404


def test_job_cancel_rejects_other_users_session(test_client):
    _inject_session("own-test-8", "otheruser")
    res = test_client.post("/api/jobs/own-test-8/cancel")
    assert res.status_code == 404
