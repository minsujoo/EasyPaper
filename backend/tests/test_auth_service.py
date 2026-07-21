"""services/auth.py 단위 테스트 (비밀번호 해시, 세션 토큰 발급/검증)."""

import time

from services.auth import (
    hash_password,
    verify_password,
    create_session_token,
    verify_session_token,
)


def test_hash_password_produces_verifiable_hash():
    h = hash_password("my-secret-pw")
    assert ":" in h
    assert verify_password(h, "my-secret-pw") is True


def test_verify_password_rejects_wrong_password():
    h = hash_password("correct-password")
    assert verify_password(h, "wrong-password") is False


def test_verify_password_rejects_malformed_hash():
    assert verify_password("not-a-valid-hash", "anything") is False


def test_hash_password_uses_random_salt():
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2, "매번 다른 salt를 써야 같은 비밀번호도 다른 해시가 나온다"


def test_session_token_round_trip():
    token = create_session_token("admin")
    assert verify_session_token(token) is True
    assert token.split(":")[0] == "admin"


def test_session_token_rejects_tampered_username():
    token = create_session_token("admin")
    _, expires, sig = token.split(":")
    forged = f"attacker:{expires}:{sig}"
    assert verify_session_token(forged) is False, \
        "서명은 원래 username에 대해 계산된 것이므로 username을 바꾸면 검증에 실패해야 한다"


def test_session_token_rejects_expired_token():
    payload = "admin:" + str(int(time.time()) - 10)  # 이미 만료된 시각
    import hmac, hashlib
    from config import SECRET_KEY
    sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    expired_token = f"{payload}:{sig}"
    assert verify_session_token(expired_token) is False


def test_session_token_rejects_malformed_token():
    assert verify_session_token("not-enough-parts") is False
    assert verify_session_token("") is False
