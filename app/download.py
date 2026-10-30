import io
import os
import zipfile
from pathlib import Path

from app.models import App
from app.response import ErrorCode

_IGNORED_NAMES = {
    "node_modules",
    ".git",
    "dist",
    "build",
    ".DS_Store",
    ".env",
    "target",
    ".mvn",
    ".idea",
    ".vscode",
}
_IGNORED_EXTENSIONS = (".log", ".tmp", ".cache")
_MAX_FILES = 5000
_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


class WorkspaceArchiveError(RuntimeError):
    """Workspace 不满足安全打包条件。"""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.PARAMS_ERROR) -> None:
        super().__init__(message)
        self.code = code


def build_workspace_zip(app: App, workspace_root: Path) -> bytes:
    """把 App Workspace 打包为 ZIP，不跟随链接并保留相对项目结构。"""
    root = workspace_root.resolve()
    raw_workspace = root / f"{app.code_gen_type}_{app.id}"
    if _is_link(raw_workspace):
        raise WorkspaceArchiveError("应用代码路径异常")
    if not raw_workspace.exists():
        raise WorkspaceArchiveError(
            "应用代码不存在，请先生成代码",
            code=ErrorCode.NOT_FOUND,
        )
    workspace = raw_workspace.resolve()
    if not workspace.is_dir() or not workspace.is_relative_to(root) or workspace == root:
        raise WorkspaceArchiveError(
            "应用代码不存在，请先生成代码",
            code=ErrorCode.NOT_FOUND,
        )

    output = io.BytesIO()
    file_count = 0
    total_bytes = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:

        def on_scan_error(exc: OSError) -> None:
            raise WorkspaceArchiveError(
                "应用代码暂时无法读取，请稍后重试",
                code=ErrorCode.SYSTEM_ERROR,
            ) from exc

        for directory, directories, files in os.walk(
            workspace,
            followlinks=False,
            onerror=on_scan_error,
        ):
            current = Path(directory)
            allowed_directories: list[str] = []
            for name in sorted(directories):
                path = current / name
                if _is_link(path):
                    raise WorkspaceArchiveError("应用代码包含不允许的符号链接")
                if _ignored(path):
                    continue
                allowed_directories.append(name)
                archive.writestr(f"{path.relative_to(workspace).as_posix()}/", b"")
            directories[:] = allowed_directories
            for name in sorted(files):
                path = current / name
                if _is_link(path) or not path.is_file():
                    raise WorkspaceArchiveError("应用代码包含不允许的文件类型")
                if _ignored(path):
                    continue
                file_count += 1
                if file_count > _MAX_FILES:
                    raise WorkspaceArchiveError("应用代码文件数量超过下载限制")
                try:
                    file_size = path.stat().st_size
                    if total_bytes + file_size > _MAX_UNCOMPRESSED_BYTES:
                        raise WorkspaceArchiveError("应用代码体积超过下载限制")
                    data = path.read_bytes()
                except OSError as exc:
                    raise WorkspaceArchiveError(
                        "应用代码正在更新，请稍后重试",
                        code=ErrorCode.SYSTEM_ERROR,
                    ) from exc
                total_bytes += len(data)
                if total_bytes > _MAX_UNCOMPRESSED_BYTES:
                    raise WorkspaceArchiveError("应用代码体积超过下载限制")
                archive.writestr(path.relative_to(workspace).as_posix(), data)
    if file_count == 0:
        raise WorkspaceArchiveError(
            "应用代码不存在，请先生成代码",
            code=ErrorCode.NOT_FOUND,
        )
    return output.getvalue()


def _ignored(path: Path) -> bool:
    name = path.name
    return name in _IGNORED_NAMES or name.endswith(_IGNORED_EXTENSIONS)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()
