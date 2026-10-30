import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_TEMPLATE = Path(__file__).parent.parent / "templates" / "vue_project"
_FIXED = ("package.json", "vite.config.js", "index.html", "src/main.js")
_GENERATED = ("src/App.vue", "src/style.css")


class VueProjectTemplate:
    def prepare(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for rel in _FIXED:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_TEMPLATE / rel, target)
        for rel in _GENERATED:
            target = root / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_TEMPLATE / rel, target)


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    output: str
    dist_size_bytes: int | None = None


class VueProjectBuilder:
    def __init__(self, install_timeout: int = 300, build_timeout: int = 180) -> None:
        self._install_timeout = install_timeout
        self._build_timeout = build_timeout

    @staticmethod
    def _env() -> dict[str, str]:
        allowed = (
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "COMSPEC",
        )
        return {key: os.environ[key] for key in allowed if key in os.environ}

    def build(self, root: Path) -> BuildResult:
        npm = shutil.which("npm", path=os.environ.get("PATH"))
        if npm is None:
            return BuildResult(False, "未找到 npm")
        if not (root / "node_modules").exists():
            result = self._run(
                [npm, "install", "--no-audit", "--no-fund"], root, self._install_timeout
            )
            if not result.ok:
                return result
        result = self._run([npm, "run", "build"], root, self._build_timeout)
        if not result.ok:
            return result
        index = root / "dist" / "index.html"
        if not index.is_file():
            return BuildResult(False, "构建完成但未生成 dist/index.html")
        return BuildResult(True, result.output, index.stat().st_size)

    def _run(self, command: list[str], root: Path, timeout: int) -> BuildResult:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=self._env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return BuildResult(False, f"构建进程失败: {type(exc).__name__}")
        output = f"{completed.stdout}\n{completed.stderr}".strip()[:8000]
        return BuildResult(completed.returncode == 0, output or f"退出码 {completed.returncode}")
