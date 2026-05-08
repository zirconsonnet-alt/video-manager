import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


def test_resolves_app_dir_to_executable_parent_when_packaged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packaged_exe = tmp_path / "VideoManager.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(packaged_exe))

    assert app.resolve_app_dir() == tmp_path
