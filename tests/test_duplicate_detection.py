import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import find_existing_download_by_default_name


def test_finds_same_default_name_anywhere_under_output_dir(tmp_path: Path) -> None:
    nested_dir = tmp_path / "course" / "chapter"
    nested_dir.mkdir(parents=True)
    existing_file = nested_dir / "0 课程介绍.mp4"
    existing_file.write_text("already downloaded", encoding="utf-8")

    assert find_existing_download_by_default_name(tmp_path, "0 课程介绍") == existing_file


def test_finds_same_default_name_with_different_extension(tmp_path: Path) -> None:
    existing_file = tmp_path / "1-1 计算机系统的组成.mkv"
    existing_file.write_text("already downloaded", encoding="utf-8")

    assert find_existing_download_by_default_name(tmp_path, "1-1 计算机系统的组成") == existing_file


def test_ignores_numbered_duplicate_suffix_when_comparing_default_name(tmp_path: Path) -> None:
    (tmp_path / "1-1 计算机系统的组成 (1).mp4").write_text(
        "duplicate copy",
        encoding="utf-8",
    )

    assert find_existing_download_by_default_name(tmp_path, "1-1 计算机系统的组成") is None
