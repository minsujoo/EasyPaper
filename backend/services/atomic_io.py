"""파일을 원자적으로(all-or-nothing) 쓰기 위한 공용 헬퍼.

캐시/잡 상태 파일들을 open(path, "w")로 바로 덮어쓰면, 쓰는 도중 프로세스가
비정상 종료(정전, 강제 kill 등)될 경우 파일이 잘려나간 상태의 깨진 JSON으로
남을 수 있다. 같은 파일시스템 안에서의 os.replace()는 원자적이라는 보장을
이용해, 임시 파일에 다 쓴 뒤 원래 경로로 교체하는 방식으로 이 문제를 막는다.
"""

import os


def atomic_write_text(path: str, content: str, encoding: str = "utf-8") -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
