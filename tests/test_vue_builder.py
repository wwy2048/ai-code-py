import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.generation.adapters.vue_browser import BrowserValidationResult
from app.generation.adapters.vue_project import VueProjectBuilder


class AcceptingBrowserValidator:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, dist_root: Path) -> BrowserValidationResult:
        self.calls += 1
        assert (dist_root / "index.html").is_file()
        assert (dist_root / "assets" / "index.css").is_file()
        return BrowserValidationResult(True, "browser ok")


def _write_build_output(command: list[str], cwd: str | Path) -> None:
    output_index = command.index("--outDir") + 1
    dist = Path(cwd) / command[output_index]
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("ok", encoding="utf-8")
    (dist / "assets" / "index.css").write_text("body { color: red; }" * 30, encoding="utf-8")


def test_vue_builder_uses_staging_output_browser_validation_and_tail_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = VueProjectBuilder(browser_validator=AcceptingBrowserValidator())
    monkeypatch.setattr(builder, "_dependencies_ready", lambda root: True)

    def success(
        command: list[str], cwd: str | Path, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if "build" in command:
            _write_build_output(command, cwd)
        return subprocess.CompletedProcess(command, 0, "x" * 10000, "")

    monkeypatch.setattr(subprocess, "run", success)
    result = builder.build(tmp_path)

    assert result.ok is True
    assert result.source_digest is not None
    assert result.dist_size_bytes and result.dist_size_bytes > 2
    assert len(result.output) <= 8000
    assert result.output.startswith("（已省略前部内容）")
    assert "browser ok" in result.output
    assert (tmp_path / "dist" / "index.html").is_file()
    assert not list(tmp_path.glob(".ai-code-dist-*"))
    assert builder._browser_validator is not None
    assert builder._browser_validator.calls == 1  # type: ignore[attr-defined]


def test_vue_builder_failure_keeps_previous_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_dist = tmp_path / "dist"
    old_dist.mkdir()
    (old_dist / "index.html").write_text("old", encoding="utf-8")
    builder = VueProjectBuilder()
    monkeypatch.setattr(builder, "_dependencies_ready", lambda root: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, cwd, **kwargs: subprocess.CompletedProcess(command, 1, "", "bad build"),
    )

    result = builder.build(tmp_path)

    assert result.ok is False
    assert "bad build" in result.output
    assert (tmp_path / "dist" / "index.html").read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".ai-code-dist-*"))


def test_vue_builder_reports_timeout_and_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = VueProjectBuilder()
    monkeypatch.setattr(builder, "_dependencies_ready", lambda root: True)

    def timeout(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("npm", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    timeout_result = builder.build(tmp_path)
    assert timeout_result.ok is False
    assert "超时" in timeout_result.output
    assert "TimeoutExpired" not in timeout_result.output

    source = tmp_path / "src"
    source.mkdir()
    (source / "App.vue").write_text("<template><!--FEATURE_VIEW--></template>", encoding="utf-8")
    placeholder_result = builder.build(tmp_path)
    assert placeholder_result.ok is False
    assert "未完成标记" in placeholder_result.output


def test_vue_template_prepares_router_and_preserves_generated_files(tmp_path: Path) -> None:
    from app.generation.adapters.vue_project import VueProjectTemplate

    template = VueProjectTemplate()
    template.prepare(tmp_path)
    assert (tmp_path / "package-lock.json").is_file()
    assert (tmp_path / "vite-source-locator.js").is_file()
    assert "vueSourceLocator" in (tmp_path / "vite.config.js").read_text(encoding="utf-8")
    assert (tmp_path / "src" / "router" / "index.js").is_file()
    assert (tmp_path / "src" / "views" / "HomeView.vue").is_file()

    (tmp_path / "src" / "App.vue").write_text(
        "<template><main>kept</main></template>", encoding="utf-8"
    )
    template.prepare(tmp_path)
    assert "kept" in (tmp_path / "src" / "App.vue").read_text(encoding="utf-8")


def test_vue_source_snapshot_restores_previous_tree(tmp_path: Path) -> None:
    from app.generation.adapters.vue_project import VueSourceSnapshot

    src = tmp_path / "src"
    src.mkdir()
    (src / "App.vue").write_text("old", encoding="utf-8")
    snapshot = VueSourceSnapshot(tmp_path)

    (src / "App.vue").write_text("new", encoding="utf-8")
    (src / "Broken.vue").write_text("broken", encoding="utf-8")
    snapshot.restore()

    assert (tmp_path / "src" / "App.vue").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "src" / "Broken.vue").exists()


async def test_vue_build_coordinator_reuses_same_source_digest(tmp_path: Path) -> None:
    from app.generation.adapters.codegen import VueBuildCoordinator
    from app.generation.adapters.vue_project import BuildResult
    from app.generation.adapters.workspace import Workspace

    class CountingBuilder:
        def __init__(self) -> None:
            self.calls = 0

        def source_digest(self, root: Path) -> str:
            return "same-digest"

        def build(self, root: Path) -> BuildResult:
            self.calls += 1
            return BuildResult(True, "ok", 10, "same-digest")

    builder = CountingBuilder()
    coordinator = VueBuildCoordinator(builder)  # type: ignore[arg-type]
    workspace = Workspace(tmp_path)
    first = await coordinator.build(workspace)
    second = await coordinator.build(workspace)

    assert first == second
    assert builder.calls == 1


def test_vue_builder_retries_after_partial_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    builder = VueProjectBuilder()
    install_attempts = 0

    def run(command: list[str], cwd: str | Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal install_attempts
        root = Path(cwd)
        if "ci" in command:
            install_attempts += 1
            (root / "node_modules").mkdir(exist_ok=True)
            if install_attempts == 1:
                return subprocess.CompletedProcess(command, 1, "", "network failed")
            (root / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
            (root / "node_modules" / ".bin" / "vite.cmd").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "installed", "")
        _write_build_output(command, cwd)
        return subprocess.CompletedProcess(command, 0, "built", "")

    monkeypatch.setattr(subprocess, "run", run)

    first = builder.build(tmp_path)
    assert first.ok is False
    assert not (tmp_path / "node_modules").exists()
    assert not (tmp_path / ".ai-code-npm-install.json").exists()

    second = builder.build(tmp_path)
    assert second.ok is True
    assert install_attempts == 2
    assert (tmp_path / ".ai-code-npm-install.json").is_file()


def test_vue_browser_validator_rejects_unstyled_output(tmp_path: Path) -> None:
    from app.generation.adapters.vue_browser import SeleniumVueBrowserValidator

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id='app'></div>", encoding="utf-8")
    (dist / "assets" / "index.css").write_text("body{}", encoding="utf-8")

    result = SeleniumVueBrowserValidator().validate(dist)

    assert result.ok is False
    assert "CSS" in result.output


def test_vue_builder_recovers_previous_dist_after_interrupted_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / ".ai-code-dist-backup-crashed"
    backup.mkdir()
    (backup / "index.html").write_text("previous", encoding="utf-8")
    builder = VueProjectBuilder()
    monkeypatch.setattr(builder, "_dependencies_ready", lambda root: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, cwd, **kwargs: subprocess.CompletedProcess(command, 1, "", "build failed"),
    )

    result = builder.build(tmp_path)

    assert result.ok is False
    assert (tmp_path / "dist" / "index.html").read_text(encoding="utf-8") == "previous"
    assert not backup.exists()
