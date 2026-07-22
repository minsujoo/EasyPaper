"""GET /settings/changelog 테스트 - 저장소 루트 CHANGELOG.md를 그대로 서빙하는지 확인."""

from unittest.mock import patch


def test_get_changelog_returns_actual_file_content(test_client):
    res = test_client.get("/api/settings/changelog")
    assert res.status_code == 200
    body = res.json()
    assert "# Changelog" in body["content"]
    assert "## " in body["content"]  # 날짜별 섹션 헤더가 하나 이상 있어야 한다


def test_get_changelog_returns_empty_string_when_file_missing(test_client, tmp_path):
    with patch("routers.auth.get_project_root", return_value=str(tmp_path)):
        res = test_client.get("/api/settings/changelog")
    assert res.status_code == 200
    assert res.json()["content"] == ""
