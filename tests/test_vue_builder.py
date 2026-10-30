import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.runtime.vue_project import VueProjectBuilder


def test_vue_builder_reports_success_failure_timeout_and_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "node_modules").mkdir()
    builder = VueProjectBuilder()

    def success(
        command: list[str], cwd: str | Path, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if "build" in command:
            dist = Path(cwd) / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("ok", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "x" * 10000, "")

    monkeypatch.setattr(subprocess, "run", success)
    result = builder.build(tmp_path)
    assert result.ok is True
    assert result.dist_size_bytes == 2
    assert len(result.output) <= 8000

    def failure(
        command: list[str], cwd: str | Path, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "bad build")

    monkeypatch.setattr(subprocess, "run", failure)
    assert builder.build(tmp_path).ok is False

    def timeout(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("npm", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    timeout_result = builder.build(tmp_path)
    assert timeout_result.ok is False
    assert "TimeoutExpired" in timeout_result.output
