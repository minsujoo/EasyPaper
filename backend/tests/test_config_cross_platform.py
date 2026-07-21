"""config.py의 크로스플랫폼/보안 관련 로직 회귀 테스트.

이번 세션에서 실제로 발견·수정된 버그들(Windows CLI 실행, 하드코딩된 개발
서버 경로, PROJECT_ROOT 검증, SECRET_KEY 공유 기본값)이 다시 재발하지
않도록 각 수정의 핵심 동작을 고정한다.

windows_safe_exec_args/_resolve_cli_path는 platform/shutil을 함수 내부에서
지역으로 import하는데, 이는 sys.modules에 캐시된 동일한 모듈 객체를 가져오는
것이므로, 이 테스트 파일에서 직접 import한 platform/shutil을 monkeypatch해도
동일하게 적용된다.
"""

import os
import platform
import shutil

import config


def test_windows_safe_exec_args_wraps_with_cmd_on_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    result = config.windows_safe_exec_args(["claude", "--print", "hello"])
    assert result == ["cmd", "/c", "claude", "--print", "hello"]


def test_windows_safe_exec_args_passthrough_on_posix(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    result = config.windows_safe_exec_args(["claude", "--print", "hello"])
    assert result == ["claude", "--print", "hello"]


def test_resolve_cli_path_prefers_existing_configured_path(tmp_path):
    real_binary = tmp_path / "claude"
    real_binary.write_text("#!/bin/sh\n")
    resolved = config._resolve_cli_path(str(real_binary), "claude")
    assert resolved == str(real_binary)


def test_resolve_cli_path_falls_back_to_path_search(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/local/bin/claude")
    resolved = config._resolve_cli_path("/nonexistent/dev-server/only/claude", "claude")
    assert resolved == "/usr/local/bin/claude"


def test_resolve_cli_path_returns_bare_command_as_last_resort(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    resolved = config._resolve_cli_path("/nonexistent/claude", "claude")
    assert resolved == "claude"


def test_get_agy_env_never_hardcodes_dev_server_home(monkeypatch):
    """HOME/USER가 비어있으면 이 개발 서버 전용 경로(/home/ubuntu)를 하드코딩해
    반환하는 대신, 실제 현재 사용자의 홈 디렉터리를 동적으로 구해야 한다 - macOS
    등 다른 환경에서는 하드코딩된 값이 존재하지 않는 디렉터리라 CLI 서브프로세스가
    [Errno 45]로 실패하던 버그의 재발 방지 테스트.

    이 테스트 자체는 우연히 이 서버(사용자명 ubuntu)에서 돌아서 결과값이
    "/home/ubuntu"와 같을 수 있으므로, 고정 문자열과 비교하는 대신 매커니즘
    (os.path.expanduser("~")를 실제로 호출하는지)을 검증한다.
    """
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    env = config.get_agy_env()
    assert env["HOME"] == os.path.expanduser("~"), \
        "하드코딩된 값이 아니라 실제 현재 사용자의 홈 디렉터리를 동적으로 반환해야 한다"
    assert os.path.isdir(env["HOME"]), "생성된 HOME은 실제로 존재하는 디렉터리여야 한다"


def test_project_root_falls_back_when_configured_path_missing():
    """PROJECT_ROOT가 .env.example을 그대로 복사해 존재하지 않는 경로를 가리켜도,
    CLI 서브프로세스의 cwd로 그대로 써서 [Errno 2]가 나던 버그의 재발 방지 테스트.

    PROJECT_ROOT는 config.py가 처음 import될 때 딱 한 번만 계산되는 모듈 전역
    이라, 이미 로드된 프로세스 안에서는 재현할 수 없다 - 별도 파이썬
    프로세스를 띄워 env var를 존재하지 않는 경로로 지정한 뒤 실제로 폴백
    되는지 확인한다.
    """
    import subprocess
    import sys

    backend_dir = os.path.dirname(os.path.abspath(config.__file__))
    expected_project_root = os.path.dirname(backend_dir)
    env = os.environ.copy()
    env["PROJECT_ROOT"] = "/definitely/does/not/exist/on/this/machine"
    result = subprocess.run(
        [sys.executable, "-c", "import config; print(config.get_project_root())"],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    resolved = result.stdout.strip()
    assert os.path.isdir(resolved), f"폴백된 PROJECT_ROOT({resolved!r})가 존재해야 한다 (stderr: {result.stderr})"
    assert resolved == expected_project_root


def test_secret_key_never_equals_known_insecure_default():
    """SECRET_KEY는 모든 설치본이 공유하는 고정 문자열이면 안 된다 - 세션 토큰
    위조로 이어지는 취약점의 재발 방지 테스트."""
    assert config.SECRET_KEY not in config._INSECURE_DEFAULT_SECRET_KEYS
    assert len(config.SECRET_KEY) >= 32


def test_get_or_create_secret_key_persists_across_calls(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(
        config, "_persist_secret_key_to_env",
        lambda key: env_path.write_text(f"SECRET_KEY={key}\n")
    )
    monkeypatch.setenv("SECRET_KEY", "")

    first = config._get_or_create_secret_key()
    assert first not in config._INSECURE_DEFAULT_SECRET_KEYS

    # 두 번째 호출도 같은 (영속화된) 값을 재사용해야 한다 - 재시작마다 새로
    # 만들면 기존 로그인 세션이 계속 끊긴다
    monkeypatch.setenv("SECRET_KEY", first)
    second = config._get_or_create_secret_key()
    assert second == first
