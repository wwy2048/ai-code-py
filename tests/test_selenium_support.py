import os
from pathlib import Path

import pytest
from selenium.webdriver import ChromeOptions

from app.config import Settings
from app.selenium_support import apply_chrome_binary, build_chrome_service, configure_selenium


def _settings(
    tmp_path: Path,
    *,
    chromedriver_path: Path | None = None,
    chrome_binary_path: Path | None = None,
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        chromedriver_path=str(chromedriver_path) if chromedriver_path else None,
        chrome_binary_path=str(chrome_binary_path) if chrome_binary_path else None,
        _env_file=None,
    )


def test_configure_selenium_forces_offline_and_exports_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = tmp_path / "chromedriver"
    chrome = tmp_path / "chrome"
    driver.write_text("driver", encoding="utf-8")
    chrome.write_text("chrome", encoding="utf-8")
    for key in (
        "SE_OFFLINE",
        "SE_AVOID_BROWSER_DOWNLOAD",
        "SE_CHROMEDRIVER",
        "SE_BROWSER_PATH",
    ):
        monkeypatch.setenv(key, "before")

    configure_selenium(
        _settings(
            tmp_path,
            chromedriver_path=driver,
            chrome_binary_path=chrome,
        )
    )

    assert os.environ["SE_OFFLINE"] == "true"
    assert os.environ["SE_AVOID_BROWSER_DOWNLOAD"] == "true"
    assert os.environ["SE_CHROMEDRIVER"] == str(driver)
    assert os.environ["SE_BROWSER_PATH"] == str(chrome)


def test_local_chrome_service_and_binary_bypass_selenium_manager(tmp_path: Path) -> None:
    driver = tmp_path / "chromedriver"
    chrome = tmp_path / "chrome"
    driver.write_text("driver", encoding="utf-8")
    chrome.write_text("chrome", encoding="utf-8")

    service = build_chrome_service(str(driver))
    options = ChromeOptions()
    apply_chrome_binary(options, str(chrome))

    assert service is not None
    assert Path(service.path) == driver
    assert options.binary_location == str(chrome)


def test_missing_local_driver_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ChromeDriver 不存在"):
        build_chrome_service(str(tmp_path / "missing-chromedriver"))
