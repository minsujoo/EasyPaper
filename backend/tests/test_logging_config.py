"""logging_config.py 테스트 - 파일/콘솔 핸들러가 정상적으로 로그를 남기는지."""

import logging
import subprocess
import sys


def test_setup_logging_writes_to_rotating_file(tmp_path):
    """logging_config는 모듈 로드 시점에 로그 디렉터리를 결정하므로, 별도
    프로세스에서 실행해 임시 디렉터리를 대상으로 격리 검증한다."""
    import os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = (
        "import logging_config, os\n"
        f"logging_config.LOG_DIR = {str(tmp_path)!r}\n"
        f"logging_config.LOG_FILE = os.path.join({str(tmp_path)!r}, 'test.log')\n"
        "logging_config.setup_logging()\n"
        "import logging\n"
        "logging.getLogger('test').error('테스트 에러 메시지')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr

    log_file = tmp_path / "test.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "[ERROR]" in content
    assert "테스트 에러 메시지" in content


def test_setup_logging_is_idempotent():
    """여러 번 호출해도 핸들러가 중복 등록되지 않아야 한다 (로그 중복 방지)."""
    from logging_config import setup_logging
    root1 = setup_logging()
    handler_count_1 = len(root1.handlers)
    root2 = setup_logging()
    handler_count_2 = len(root2.handlers)
    assert handler_count_1 == handler_count_2
    assert root1 is root2
