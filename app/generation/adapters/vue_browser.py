import logging
import threading
import time
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import Chrome, ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait

from app.selenium_support import apply_chrome_binary, build_chrome_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserValidationResult:
    ok: bool
    output: str


class _QuietRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return None


class SeleniumVueBrowserValidator:
    """用无头 Chrome 检查构建产物是否真正挂载、渲染且无严重运行时错误。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        wait_after_load_seconds: float = 1.0,
        min_css_bytes: int = 200,
        chromedriver_path: str | None = None,
        chrome_binary_path: str | None = None,
    ) -> None:
        if timeout_seconds <= 0 or wait_after_load_seconds < 0 or min_css_bytes < 0:
            raise ValueError("浏览器校验参数不合法")
        self._timeout_seconds = timeout_seconds
        self._wait_after_load_seconds = wait_after_load_seconds
        self._min_css_bytes = min_css_bytes
        self._chromedriver_path = chromedriver_path
        self._chrome_binary_path = chrome_binary_path

    def validate(self, dist_root: Path) -> BrowserValidationResult:
        index = dist_root / "index.html"
        if not index.is_file():
            return BrowserValidationResult(False, "浏览器校验失败：缺少 dist/index.html")

        css_bytes = self._css_bytes(dist_root)
        if css_bytes < self._min_css_bytes:
            return BrowserValidationResult(
                False,
                f"浏览器校验失败：有效 CSS 仅 {css_bytes} 字节，页面可能缺少样式",
            )

        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(_QuietRequestHandler, directory=str(dist_root)),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        driver = None
        try:
            options = ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-background-networking")
            options.add_argument("--no-first-run")
            options.add_argument("--mute-audio")
            options.add_argument("--window-size=1440,900")
            options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
            apply_chrome_binary(options, self._chrome_binary_path)
            service = build_chrome_service(self._chromedriver_path)

            driver = Chrome(service=service, options=options)
            driver.set_page_load_timeout(self._timeout_seconds)
            port = server.server_address[1]
            page_url = f"http://127.0.0.1:{port}/index.html"
            driver.get(page_url)
            WebDriverWait(driver, self._timeout_seconds).until(
                lambda browser: browser.execute_script("return document.readyState") == "complete"
            )
            WebDriverWait(driver, self._timeout_seconds).until(
                lambda browser: browser.execute_script(
                    "return !!document.querySelector('#app') && "
                    "document.querySelector('#app').children.length > 0"
                )
            )
            if self._wait_after_load_seconds:
                time.sleep(self._wait_after_load_seconds)

            snapshot = driver.execute_script(
                """
                const root = document.querySelector('#app');
                const body = document.body;
                const visible = root
                  ? root.querySelectorAll('h1,h2,h3,p,button,input,a,article,section,canvas,svg')
                  : [];
                return {
                  rootChildren: root ? root.children.length : 0,
                  contentElements: visible.length,
                  bodyWidth: body ? body.scrollWidth : 0,
                  bodyHeight: body ? body.scrollHeight : 0,
                  title: document.title || '',
                };
                """
            )
            if not isinstance(snapshot, dict):
                return BrowserValidationResult(False, "浏览器校验失败：无法读取页面状态")
            if int(snapshot.get("rootChildren") or 0) <= 0:
                return BrowserValidationResult(False, "浏览器校验失败：#app 未挂载内容")
            if int(snapshot.get("contentElements") or 0) <= 0:
                return BrowserValidationResult(False, "浏览器校验失败：页面没有可见内容元素")
            body_width = int(snapshot.get("bodyWidth") or 0)
            body_height = int(snapshot.get("bodyHeight") or 0)
            if body_width <= 0 or body_height <= 0:
                return BrowserValidationResult(False, "浏览器校验失败：页面尺寸异常")

            routes = self._internal_routes(driver)
            for route in routes[:5]:
                driver.get(f"{page_url}{route}")
                WebDriverWait(driver, self._timeout_seconds).until(
                    lambda browser: browser.execute_script(
                        "return !!document.querySelector('#app') && "
                        "document.querySelector('#app').children.length > 0"
                    )
                )
                time.sleep(0.2)
                route_snapshot = driver.execute_script(
                    """
                    const root = document.querySelector('#app');
                    return root
                      ? root.querySelectorAll(
                          'h1,h2,h3,p,button,input,a,article,section,canvas,svg'
                        ).length
                      : 0;
                    """
                )
                if int(route_snapshot or 0) <= 0:
                    return BrowserValidationResult(
                        False,
                        f"浏览器校验失败：路由 {route} 没有可见内容",
                    )

            severe_logs = self._severe_logs(driver)
            if severe_logs:
                details = "\n".join(severe_logs[:5])
                return BrowserValidationResult(False, f"浏览器控制台存在严重错误：\n{details}")
            return BrowserValidationResult(
                True,
                f"浏览器校验通过：rootChildren={snapshot['rootChildren']}，"
                f"contentElements={snapshot['contentElements']}，routes={len(routes[:5])}，"
                f"cssBytes={css_bytes}",
            )
        except TimeoutException:
            return BrowserValidationResult(False, "浏览器校验超时：页面未完成挂载")
        except WebDriverException as exc:
            return BrowserValidationResult(
                False,
                f"浏览器启动或访问失败：{type(exc).__name__}: {str(exc)[:1000]}",
            )
        except Exception as exc:
            logger.exception("Vue 浏览器校验异常")
            return BrowserValidationResult(
                False,
                f"浏览器校验异常：{type(exc).__name__}: {str(exc)[:1000]}",
            )
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    logger.warning("关闭 Vue 校验浏览器失败")
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @staticmethod
    def _internal_routes(driver: object) -> list[str]:
        try:
            routes = driver.execute_script(  # type: ignore[attr-defined]
                """
                return [...new Set(
                  [...document.querySelectorAll('a[href]')]
                    .map(link => link.getAttribute('href'))
                    .filter(href => href && href.startsWith('#/') && href !== '#/')
                )];
                """
            )
        except Exception:
            return []
        if not isinstance(routes, list):
            return []
        return [str(route) for route in routes]

    @staticmethod
    def _css_bytes(root: Path) -> int:
        total = 0
        for path in root.rglob("*.css"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _severe_logs(driver: object) -> list[str]:
        try:
            entries = driver.get_log("browser")  # type: ignore[attr-defined]
        except Exception:
            return []
        result: list[str] = []
        for entry in entries:
            if str(entry.get("level", "")).upper() != "SEVERE":
                continue
            message = str(entry.get("message", "")).strip()
            lowered = message.lower()
            if "favicon.ico" in lowered or "source map" in lowered:
                continue
            if message:
                result.append(message)
        return result
