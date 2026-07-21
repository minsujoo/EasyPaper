"""
구조화된 로깅 설정.

지금까지는 전부 print()로만 로그를 남겨서, 레벨별 필터링(에러만 보기 등)이나
Windows/macOS처럼 systemd 저널이 없는 환경에서의 영속 저장이 불가능했다.
표준 logging 모듈로 콘솔 + 파일(로테이션) 핸들러를 함께 등록해, 어떤
운영체제/실행 방식에서도 backend/logs/easypaper.log에 로그가 남도록 한다.

기존 print() 문들은 이번에는 건드리지 않는다 - 전체 일괄 치환은 200곳
이상을 건드리는 큰 변경이라 회귀 위험이 크므로, 우선 이 설정을 쓰는 신규/
전환 모듈부터 시작하고 나머지는 점진적으로 옮긴다. print() 출력은
Linux(systemd) 환경에서는 기존처럼 저널에 그대로 남는다.
"""

import logging
import logging.handlers
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "easypaper.log")

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """루트 로거에 콘솔 + 로테이팅 파일 핸들러를 등록한다. 여러 번 호출해도
    핸들러가 중복 등록되지 않는다."""
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 10MB x 5개 백업 - 무한정 커지지 않도록 로테이션
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    _configured = True
    return root
