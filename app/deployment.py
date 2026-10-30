import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.models import App

_DEPLOY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{6}$")


def is_valid_deploy_key(value: str) -> bool:
    return _DEPLOY_KEY_PATTERN.fullmatch(value) is not None


class DeploymentSourceError(ValueError):
    """源 Workspace 或构建产物不满足部署条件。"""


class DeploymentWriteError(RuntimeError):
    """部署目录写入失败。"""


class AppDeployer(Protocol):
    def deploy(self, app: App, deploy_key: str) -> str: ...

    def remove(self, deploy_key: str) -> None: ...


class LocalAppDeployer:
    """把受控 Workspace 内容替换到本地部署目录。"""

    def __init__(self, settings: Settings) -> None:
        self._workspace_root = Path(settings.workspace_root).resolve()
        self._deploy_root = Path(settings.deploy_root).resolve()
        self._deploy_url_base = settings.deploy_url_base.rstrip("/")

    def deploy(self, app: App, deploy_key: str) -> str:
        if not is_valid_deploy_key(deploy_key):
            raise DeploymentSourceError("部署键格式不正确")
        source = self._source_directory(app)
        self._reject_symlinks(source)
        self._deploy_root.mkdir(parents=True, exist_ok=True)
        target = self._deploy_root / deploy_key
        staging = self._deploy_root / f".{deploy_key}.{uuid.uuid4().hex}.tmp"
        backup = self._deploy_root / f".{deploy_key}.{uuid.uuid4().hex}.bak"
        try:
            staging.mkdir()
            self._copy_tree(source, staging)
            if target.exists():
                target.rename(backup)
            try:
                staging.rename(target)
            except Exception:
                if backup.exists() and not target.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                self._remove_path(backup, ignore_errors=True)
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if isinstance(exc, DeploymentSourceError):
                raise
            raise DeploymentWriteError("部署失败") from exc
        return f"{self._deploy_url_base}/{deploy_key}/"

    def remove(self, deploy_key: str) -> None:
        if not is_valid_deploy_key(deploy_key):
            return
        target = self._deploy_root / deploy_key
        self._remove_path(target, ignore_errors=True)

    def _source_directory(self, app: App) -> Path:
        workspace = self._workspace_root / f"{app.code_gen_type}_{app.id}"
        if not workspace.is_dir() or self._is_link(workspace):
            raise DeploymentSourceError("源文件不存在")
        source = workspace / "dist" if app.code_gen_type == "vue_project" else workspace
        if app.code_gen_type == "vue_project":
            if not source.is_dir() or self._is_link(source):
                raise DeploymentSourceError("Vue 构建产物 dist 不存在")
        if not (source / "index.html").is_file():
            raise DeploymentSourceError("部署产物缺少 index.html")
        resolved = source.resolve()
        if resolved != self._workspace_root and not resolved.is_relative_to(self._workspace_root):
            raise DeploymentSourceError("源文件路径越界")
        return resolved

    @staticmethod
    def _reject_symlinks(source: Path) -> None:
        if LocalAppDeployer._is_link(source):
            raise DeploymentSourceError("部署源不允许包含符号链接")
        for path in source.rglob("*"):
            if LocalAppDeployer._is_link(path):
                raise DeploymentSourceError("部署源不允许包含符号链接")

    @staticmethod
    def _remove_path(target: Path, *, ignore_errors: bool = False) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=ignore_errors)
        elif target.is_dir():
            shutil.rmtree(target, ignore_errors=ignore_errors)

    @classmethod
    def _copy_tree(cls, source: Path, destination: Path) -> None:
        with os.scandir(source) as entries:
            for entry in entries:
                path = Path(entry.path)
                if cls._is_link(path):
                    raise DeploymentSourceError("部署源不允许包含符号链接")
                target = destination / entry.name
                if entry.is_dir(follow_symlinks=False):
                    target.mkdir()
                    cls._copy_tree(path, target)
                elif entry.is_file(follow_symlinks=False):
                    shutil.copy2(path, target)
                else:
                    raise DeploymentSourceError("部署源包含不支持的条目")

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or path.is_junction()
