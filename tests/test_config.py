from pathlib import Path

import pytest

from app.config import Settings


def test_relative_runtime_paths_are_anchored_to_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        workspace_root="custom/codeOutput",
        deploy_root="custom/code_deploy",
        screenshots_root="custom/screenshots",
        _env_file=None,
    )
    project_root = Path(__file__).resolve().parents[1]

    assert Path(settings.workspace_root) == project_root / "custom/codeOutput"
    assert Path(settings.deploy_root) == project_root / "custom/code_deploy"
    assert Path(settings.screenshots_root) == project_root / "custom/screenshots"
