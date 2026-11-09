import logging
import os
from pathlib import Path

from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from app.config import Settings

logger = logging.getLogger(__name__)


def configure_selenium(settings: Settings) -> None:
    """锁定 Selenium 使用本地浏览器和驱动，禁止运行时联网下载。"""
    os.environ["SE_OFFLINE"] = "true" if settings.selenium_offline else "false"
    if settings.selenium_offline:
        os.environ["SE_AVOID_BROWSER_DOWNLOAD"] = "true"
    else:
        os.environ.pop("SE_AVOID_BROWSER_DOWNLOAD", None)

    if settings.chromedriver_path:
        os.environ["SE_CHROMEDRIVER"] = settings.chromedriver_path
        _warn_if_missing(settings.chromedriver_path, "ChromeDriver")
    if settings.chrome_binary_path:
        os.environ["SE_BROWSER_PATH"] = settings.chrome_binary_path
        _warn_if_missing(settings.chrome_binary_path, "Chrome")

    logger.info(
        "Selenium 本地驱动配置 offline=%s chromedriver_path=%s chrome_binary_path=%s",
        settings.selenium_offline,
        settings.chromedriver_path or "<auto-local>",
        settings.chrome_binary_path or "<auto-local>",
    )


def build_chrome_service(chromedriver_path: str | None) -> Service | None:
    """配置了本地驱动路径时显式创建 Service，跳过 Selenium Manager。"""
    if not chromedriver_path:
        return None
    return Service(executable_path=str(_require_file(chromedriver_path, "ChromeDriver")))


def apply_chrome_binary(options: Options, chrome_binary_path: str | None) -> None:
    """配置了 Chrome 路径时显式指定浏览器可执行文件。"""
    if chrome_binary_path:
        options.binary_location = str(_require_file(chrome_binary_path, "Chrome"))


def _warn_if_missing(path: str, label: str) -> None:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        logger.warning("%s 路径不存在，浏览器功能将不可用: %s", label, resolved)


def _require_file(path: str, label: str) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} 不存在: {resolved}")
    return resolved
