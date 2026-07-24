"""services/atomic_io.py 단위 테스트."""

import os

from services.atomic_io import atomic_write_text


def test_atomic_write_text_creates_file_with_content(tmp_path):
    path = str(tmp_path / "sub" / "file.json")
    atomic_write_text(path, '{"a": 1}')
    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == '{"a": 1}'


def test_atomic_write_text_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "file.txt")
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")
    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == "second"


def test_atomic_write_text_leaves_no_temp_file_behind(tmp_path):
    path = str(tmp_path / "file.txt")
    atomic_write_text(path, "content")
    remaining = os.listdir(tmp_path)
    assert remaining == ["file.txt"], f"임시 파일이 정리되지 않고 남아있다: {remaining}"
