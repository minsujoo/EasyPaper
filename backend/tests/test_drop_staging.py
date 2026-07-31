def test_staged_pdf_can_be_read_once_and_deleted(test_client, tmp_path, monkeypatch):
    import routers.upload as upload_router

    staging = tmp_path / "drop-staging"
    staging.mkdir()
    token = "abc123-0.pdf"
    content = b"%PDF-1.4\nexample"
    (staging / token).write_bytes(content)
    monkeypatch.setattr(upload_router, "DROP_STAGING_DIR", str(staging))

    response = test_client.get(f"/api/drop-staging/{token}")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("application/pdf")

    deleted = test_client.delete(f"/api/drop-staging/{token}")
    assert deleted.status_code == 200
    assert not (staging / token).exists()
    assert test_client.get(f"/api/drop-staging/{token}").status_code == 404


def test_drop_staging_rejects_non_token_names(test_client):
    response = test_client.get("/api/drop-staging/not-a-token.pdf")
    assert response.status_code == 404
