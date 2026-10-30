import logging
import shutil
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from app.config import Settings
from app.models import App

logger = logging.getLogger(__name__)

_CODEGEN_TYPES = ('html', 'multi_file', 'vue_project')


class CoverCleaner(Protocol):
    def delete_cover(self, cover_url: str) -> None: ...


class AppAssetCleaner(Protocol):
    def delete_app_artifacts(self, app: App) -> None: ...


class CosCoverCleaner:
    """使用 COS 适配器尽力删除 App 封面；未配置时只记录。"""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.cos_bucket
        self._client = None
        configured = (
            settings.cos_secret_id
            and settings.cos_secret_key
            and settings.cos_region
            and settings.cos_bucket
        )
        if configured:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import-untyped]

            config = CosConfig(
                Region=settings.cos_region,
                SecretId=settings.cos_secret_id,
                SecretKey=settings.cos_secret_key,
            )
            self._client = CosS3Client(config)

    def delete_cover(self, cover_url: str) -> None:
        if not self._client or not self._bucket:
            logger.warning('COS 未配置，跳过封面清理: %s', cover_url)
            return
        key = urlparse(cover_url).path.lstrip('/')
        if not key:
            logger.warning('无法从封面 URL 提取对象键: %s', cover_url)
            return
        self._client.delete_object(Bucket=self._bucket, Key=key)


class LocalAppAssetCleaner:
    """清理受控根目录内的本地产物，并把外部封面交给对象存储适配器。"""

    def __init__(self, settings: Settings, cover_cleaner: CoverCleaner | None = None) -> None:
        self._workspace_root = Path(settings.workspace_root).resolve()
        self._deploy_root = Path(settings.deploy_root).resolve()
        self._screenshots_root = Path(settings.screenshots_root).resolve()
        self._cover_cleaner = cover_cleaner or CosCoverCleaner(settings)

    @staticmethod
    def _within(root: Path, target: Path) -> bool:
        try:
            resolved = target.resolve()
        except OSError:
            return False
        return resolved != root and resolved.is_relative_to(root)

    @staticmethod
    def _remove(target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)

    def delete_app_artifacts(self, app: App) -> None:
        for code_gen_type in _CODEGEN_TYPES:
            workspace = self._workspace_root / f'{code_gen_type}_{app.id}'
            if self._within(self._workspace_root, workspace):
                self._remove(workspace)

        if app.deploy_key:
            deployment = self._deploy_root / app.deploy_key
            if self._within(self._deploy_root, deployment):
                self._remove(deployment)
            else:
                logger.warning('拒绝清理部署根目录之外的路径: %s', app.deploy_key)

        cover = app.cover
        if not cover:
            return
        if '://' not in cover:
            cover_path = Path(cover)
            if not cover_path.is_absolute():
                cover_path = self._screenshots_root / cover_path
            if self._within(self._screenshots_root, cover_path):
                self._remove(cover_path.resolve())
                return
            logger.warning('拒绝清理截图根目录之外的封面: %s', cover)
            return
        self._cover_cleaner.delete_cover(cover)
