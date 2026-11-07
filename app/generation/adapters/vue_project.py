import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.generation.adapters.vue_browser import BrowserValidationResult

_TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "vue_project"
_FIXED = (
    "package.json",
    "package-lock.json",
    "vite.config.js",
    "vite-source-locator.js",
    "index.html",
    "src/main.js",
    "src/router/index.js",
)
_GENERATED = (
    "src/App.vue",
    "src/style.css",
    "src/router/routes.js",
    "src/views/HomeView.vue",
)
_INSTALL_MARKER = ".ai-code-npm-install.json"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PLACEHOLDER_RE = re.compile(
    r"(?i)\b(?:TODO|FIXME)\b|等待生成内容|<!--\s*[A-Z][A-Z0-9_]*(?:_VIEW|_SECTION|_PLACEHOLDER)\s*-->"
)
_MAX_OUTPUT_CHARS = 8000


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


class VueSourceSnapshot:
    """保存 src/public 快照，失败或中断时恢复上一版可运行源码。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._snapshot_root = Path(tempfile.mkdtemp(prefix="ai-code-vue-source-"))
        for name in ("src", "public"):
            source = self._root / name
            if source.is_dir():
                shutil.copytree(source, self._snapshot_root / name)

    def restore(self) -> None:
        self._restore_directory("src")
        self._restore_directory("public")
        self._cleanup()

    def commit(self) -> None:
        self._cleanup()

    def _restore_directory(self, name: str) -> None:
        target = self._root / name
        self._remove_path(target)
        snapshot = self._snapshot_root / name
        if snapshot.is_dir():
            shutil.copytree(snapshot, target)

    def _cleanup(self) -> None:
        shutil.rmtree(self._snapshot_root, ignore_errors=True)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    output: str
    dist_size_bytes: int | None = None
    source_digest: str | None = None


class BrowserValidator(Protocol):
    def validate(self, dist_root: Path) -> BrowserValidationResult: ...


class VueProjectBuilder:
    def __init__(
        self,
        install_timeout: int = 300,
        build_timeout: int = 180,
        *,
        browser_validator: BrowserValidator | None = None,
    ) -> None:
        self._install_timeout = install_timeout
        self._build_timeout = build_timeout
        self._browser_validator = browser_validator

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
            "NO_COLOR",
            "CI",
        )
        return {key: os.environ[key] for key in allowed if key in os.environ}

    def source_digest(self, root: Path) -> str:
        digest = hashlib.sha256()
        roots = [root / "src", root / "public"]
        for directory in roots:
            if not directory.exists():
                continue
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
        for name in ("package.json", "package-lock.json", "vite.config.js", "index.html"):
            path = root / name
            if path.is_file():
                digest.update(name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
        return digest.hexdigest()

    def build(self, root: Path) -> BuildResult:
        root = root.resolve()
        digest = self.source_digest(root)
        placeholder = self._find_placeholder(root)
        if placeholder is not None:
            return BuildResult(
                False,
                f"源码校验失败：发现未完成标记 {placeholder}",
                source_digest=digest,
            )

        npm = shutil.which("npm", path=os.environ.get("PATH"))
        if npm is None:
            return BuildResult(False, "未找到 npm", source_digest=digest)

        if not self._dependencies_ready(root):
            install_command = [npm, "ci" if (root / "package-lock.json").is_file() else "install"]
            install_command.extend(["--no-audit", "--no-fund"])
            result = self._run(install_command, root, self._install_timeout, stage="依赖安装")
            if not result.ok:
                self._cleanup_path(root / "node_modules")
                (root / _INSTALL_MARKER).unlink(missing_ok=True)
                return BuildResult(False, result.output, source_digest=digest)
            self._write_install_marker(root)

        self._cleanup_stale_build_dirs(root)
        staging = root / f".ai-code-dist-staging-{uuid.uuid4().hex}"
        command = [
            npm,
            "run",
            "build",
            "--",
            "--outDir",
            staging.name,
            "--emptyOutDir",
        ]
        result = self._run(command, root, self._build_timeout, stage="Vite 构建")
        if not result.ok:
            self._cleanup_path(staging)
            return BuildResult(False, result.output, source_digest=digest)

        index = staging / "index.html"
        if not index.is_file():
            self._cleanup_path(staging)
            return BuildResult(
                False,
                "构建完成但未生成 dist/index.html",
                source_digest=digest,
            )

        validation_output = ""
        if self._browser_validator is not None:
            validation = self._browser_validator.validate(staging)
            validation_output = validation.output
            if not validation.ok:
                self._cleanup_path(staging)
                return BuildResult(
                    False,
                    self._format_output(f"{result.output}\n{validation.output}"),
                    source_digest=digest,
                )

        try:
            self._activate_staging(root, staging)
        except OSError as exc:
            self._cleanup_path(staging)
            return BuildResult(
                False,
                f"替换构建产物失败：{type(exc).__name__}: {exc}",
                source_digest=digest,
            )

        dist = root / "dist"
        size = self._tree_size(dist)
        output = self._format_output(
            "\n".join(part for part in (result.output, validation_output) if part)
        )
        return BuildResult(True, output, size, digest)

    def _run(self, command: list[str], root: Path, timeout: int, *, stage: str) -> BuildResult:
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
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.stdout:
                stdout = (
                    exc.stdout.decode("utf-8", errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else str(exc.stdout)
                )
                partial += stdout
            if exc.stderr:
                stderr = (
                    exc.stderr.decode("utf-8", errors="replace")
                    if isinstance(exc.stderr, bytes)
                    else str(exc.stderr)
                )
                partial += f"\n{stderr}"
            output = f"{stage}超时（{timeout} 秒）"
            if partial.strip():
                output += f"\n{partial.strip()}"
            return BuildResult(False, self._format_output(output))
        except OSError as exc:
            return BuildResult(False, f"{stage}进程失败：{type(exc).__name__}: {exc}")
        output = self._format_output(f"{completed.stdout}\n{completed.stderr}")
        return BuildResult(
            completed.returncode == 0,
            output or f"{stage}退出码 {completed.returncode}",
        )

    def _dependencies_ready(self, root: Path) -> bool:
        vite_bin = root / "node_modules" / ".bin"
        if not any((vite_bin / name).exists() for name in ("vite", "vite.cmd", "vite.ps1")):
            return False
        marker = root / _INSTALL_MARKER
        if not marker.is_file():
            return False
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("dependencyDigest") == self._dependency_digest(root)

    def _write_install_marker(self, root: Path) -> None:
        marker = root / _INSTALL_MARKER
        marker.write_text(
            json.dumps({"dependencyDigest": self._dependency_digest(root)}, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _dependency_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for name in ("package.json", "package-lock.json"):
            path = root / name
            if path.is_file():
                digest.update(name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _find_placeholder(root: Path) -> str | None:
        for name in ("src", "public"):
            directory = root / name
            if not directory.exists():
                continue
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                if path.suffix.lower() not in {".vue", ".js", ".ts", ".css", ".json", ".html"}:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                match = _PLACEHOLDER_RE.search(text)
                if match is not None:
                    return f"{path.relative_to(root).as_posix()}: {match.group(0)}"
        return None

    def _cleanup_stale_build_dirs(self, root: Path) -> None:
        for path in root.glob(".ai-code-dist-staging-*"):
            self._cleanup_path(path)
        dist = root / "dist"
        for backup in sorted(root.glob(".ai-code-dist-backup-*")):
            if not dist.exists():
                backup.rename(dist)
                continue
            self._cleanup_path(backup)

    @staticmethod
    def _tree_size(root: Path) -> int:
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())

    @staticmethod
    def _format_output(text: str) -> str:
        cleaned = _ANSI_RE.sub("", text).strip()
        if len(cleaned) <= _MAX_OUTPUT_CHARS:
            return cleaned
        prefix = "（已省略前部内容）\n"
        return prefix + cleaned[-(_MAX_OUTPUT_CHARS - len(prefix)) :]

    def _activate_staging(self, root: Path, staging: Path) -> None:
        target = root / "dist"
        backup = root / f".ai-code-dist-backup-{uuid.uuid4().hex}"
        moved_target = False
        try:
            if target.exists():
                target.rename(backup)
                moved_target = True
            staging.rename(target)
        except Exception:
            if moved_target and backup.exists() and not target.exists():
                backup.rename(target)
            raise
        finally:
            if backup.exists():
                self._cleanup_path(backup)

    def _cleanup_path(self, path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            return
        shutil.rmtree(path, ignore_errors=True)
