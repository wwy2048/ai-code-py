import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import App
from app.selenium_support import apply_chrome_binary, build_chrome_service

logger = logging.getLogger(__name__)


class ScreenshotCaptureError(RuntimeError):
    """浏览器启动或页面截图失败。"""


class CoverUploadError(RuntimeError):
    """封面对象上传失败。"""


class ScreenshotCapturer(Protocol):
    def capture(self, url: str, output_path: Path) -> None: ...


class CoverUploader(Protocol):
    def upload(self, path: Path, object_key: str) -> str: ...

    def delete(self, object_key: str) -> None: ...


class CoverScheduler(Protocol):
    def schedule(self, app_id: int, url: str, deploy_revision: int) -> None: ...

    async def shutdown(self) -> None: ...


class SeleniumScreenshotCapturer:
    """使用无头 Chrome 对部署页面截图。"""

    def __init__(self, settings: Settings) -> None:
        self._timeout_seconds = settings.screenshot_timeout_seconds
        self._wait_after_load_seconds = settings.screenshot_wait_after_load_seconds
        self._chromedriver_path = settings.chromedriver_path
        self._chrome_binary_path = settings.chrome_binary_path

    def capture(self, url: str, output_path: Path) -> None:
        driver = None
        try:
            from selenium.common.exceptions import TimeoutException
            from selenium.webdriver import Chrome, ChromeOptions
            from selenium.webdriver.support.ui import WebDriverWait

            options = ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-extensions")
            options.add_argument("--window-size=1600,900")
            apply_chrome_binary(options, self._chrome_binary_path)
            service = build_chrome_service(self._chromedriver_path)
            driver = Chrome(service=service, options=options)
            driver.set_page_load_timeout(self._timeout_seconds)
            driver.get(url)
            WebDriverWait(driver, self._timeout_seconds).until(
                lambda browser: browser.execute_script("return document.readyState") == "complete"
            )
            actual_url = str(driver.execute_script("return location.href") or "")
            if actual_url.startswith("chrome-error://"):
                raise ScreenshotCaptureError("page_not_loaded")
            WebDriverWait(driver, self._timeout_seconds).until(
                lambda browser: bool(
                    browser.execute_script(
                        "return document.body && document.body.innerHTML.trim().length > 0"
                    )
                )
            )
            if self._wait_after_load_seconds > 0:
                import time

                time.sleep(self._wait_after_load_seconds)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if not driver.save_screenshot(str(output_path)):
                raise ScreenshotCaptureError("screenshot_empty")
        except TimeoutException as exc:
            raise ScreenshotCaptureError("page_timeout") from exc
        except ScreenshotCaptureError:
            raise
        except FileNotFoundError as exc:
            raise ScreenshotCaptureError(f"chrome_not_found: {exc}") from exc
        except Exception as exc:
            raise ScreenshotCaptureError("browser_failure") from exc
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    logger.warning("关闭截图浏览器失败")


class CosCoverUploader:
    """把截图上传到腾讯云 COS 并返回访问 URL。"""

    def __init__(self, settings: Settings) -> None:
        self._region = settings.cos_region
        self._bucket = settings.cos_bucket
        self._public_base_url = settings.cos_public_base_url
        self._client = None
        if settings.cos_secret_id and settings.cos_secret_key and self._region and self._bucket:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import-untyped]

            config = CosConfig(
                Region=self._region,
                SecretId=settings.cos_secret_id,
                SecretKey=settings.cos_secret_key,
            )
            self._client = CosS3Client(config)

    def upload(self, path: Path, object_key: str) -> str:
        if self._client is None or self._bucket is None:
            raise CoverUploadError("cos_not_configured")
        try:
            with path.open("rb") as screenshot:
                self._client.put_object(
                    Bucket=self._bucket,
                    Body=screenshot,
                    Key=object_key,
                    ContentType="image/png",
                )
        except Exception as exc:
            raise CoverUploadError("cos_upload_failed") from exc
        base_url = self._public_base_url or (
            f"https://{self._bucket}.cos.{self._region}.myqcloud.com"
        )
        return f"{base_url.rstrip('/')}/{object_key}"

    def delete(self, object_key: str) -> None:
        if self._client is None or self._bucket is None:
            return
        self._client.delete_object(Bucket=self._bucket, Key=object_key)


class CoverGenerator:
    """后台生成截图、上传对象存储并更新 App 封面。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        capturer: ScreenshotCapturer,
        uploader: CoverUploader,
        screenshots_root: Path,
        shutdown_timeout_seconds: float = 10.0,
        public_url_base: str | None = None,
        screenshot_url_base: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._capturer = capturer
        self._uploader = uploader
        self._screenshots_root = screenshots_root.resolve()
        self._public_url_base = public_url_base.rstrip("/") if public_url_base else None
        self._screenshot_url_base = (
            screenshot_url_base.rstrip("/") if screenshot_url_base else None
        )
        self._tasks: set[asyncio.Task[bool]] = set()
        self._generations: dict[int, int] = {}
        self._closing = False
        self._shutdown_timeout_seconds = shutdown_timeout_seconds

    def schedule(self, app_id: int, url: str, deploy_revision: int) -> None:
        if self._closing:
            logger.warning("服务关闭中，跳过封面任务 app_id=%s", app_id)
            return
        generation = self._generations.get(app_id, 0) + 1
        self._generations[app_id] = generation
        task = asyncio.create_task(
            self._generate(
                app_id,
                url,
                generation=generation,
                deploy_revision=deploy_revision,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        logger.info(
            "封面截图任务已调度 app_id=%s generation=%s deploy_revision=%s url=%s",
            app_id,
            generation,
            deploy_revision,
            url,
        )

    async def wait_for_idle(self) -> None:
        pending = tuple(self._tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def shutdown(self) -> None:
        self._closing = True
        try:
            while self._tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(self._tasks), return_exceptions=True),
                    timeout=self._shutdown_timeout_seconds,
                )
        except TimeoutError:
            logger.error("封面任务关闭超时 pending=%s", len(self._tasks))
            for task in tuple(self._tasks):
                task.cancel()
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def generate(self, app_id: int, url: str) -> bool:
        return await self._generate(app_id, url, generation=None, deploy_revision=None)

    async def _generate(
        self,
        app_id: int,
        url: str,
        *,
        generation: int | None,
        deploy_revision: int | None,
    ) -> bool:
        url = self._capture_url(url)
        now = datetime.now()
        object_key = f"screenshots/{now:%Y/%m/%d}/{uuid.uuid4().hex}.png"
        local_path = self._screenshots_root / f"{app_id}-{uuid.uuid4().hex}.png"
        logger.info(
            "封面截图开始 app_id=%s generation=%s deploy_revision=%s url=%s",
            app_id,
            generation,
            deploy_revision,
            url,
        )
        stage = "capture"
        uploaded = False
        try:
            await asyncio.to_thread(self._capturer.capture, url, local_path)
            logger.info("页面截图完成 app_id=%s", app_id)
            stage = "upload"
            cover_url = await asyncio.to_thread(self._uploader.upload, local_path, object_key)
            uploaded = True
            logger.info("封面截图上传完成 app_id=%s object_key=%s", app_id, object_key)
            stage = "persist"
            if generation is not None and self._generations.get(app_id) != generation:
                logger.info("丢弃过期封面任务 app_id=%s generation=%s", app_id, generation)
                return False
            async with self._session_factory() as session:
                statement = update(App).where(App.id == app_id)
                if deploy_revision is not None:
                    statement = statement.where(App.deploy_revision == deploy_revision)
                result = await session.execute(
                    statement.values(cover=cover_url, edit_time=datetime.now())
                )
                await session.commit()
                if getattr(result, "rowcount", 0) == 0:
                    logger.info("丢弃过期封面结果 app_id=%s generation=%s", app_id, generation)
                    await asyncio.to_thread(self._uploader.delete, object_key)
                    return False
            logger.info("封面生成完成 app_id=%s cover_url=%s", app_id, cover_url)
            return True
        except Exception as exc:
            logger.exception(
                "封面生成失败 app_id=%s stage=%s error_type=%s url=%s",
                app_id,
                stage,
                type(exc).__name__,
                url,
            )
            if uploaded:
                try:
                    await asyncio.to_thread(self._uploader.delete, object_key)
                except Exception:
                    logger.exception("封面对象补偿删除失败 app_id=%s", app_id)
            return False
        finally:
            try:
                await asyncio.to_thread(local_path.unlink, missing_ok=True)
            except OSError as exc:
                logger.error(
                    "封面本地文件清理失败 app_id=%s stage=cleanup error_type=%s",
                    app_id,
                    type(exc).__name__,
                )

    def _capture_url(self, url: str) -> str:
        """把对外部署地址替换成服务器内部可访问的截图地址。"""
        public_base = self._public_url_base
        screenshot_base = self._screenshot_url_base
        if not public_base or not screenshot_base:
            return url
        if url == public_base:
            return screenshot_base
        prefix = f"{public_base}/"
        if url.startswith(prefix):
            return f"{screenshot_base}/{url[len(prefix):]}"
        return url

    def _task_done(self, task: asyncio.Task[bool]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error("封面后台任务异常 error_type=%s", type(exception).__name__)
