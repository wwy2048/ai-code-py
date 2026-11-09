import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LOGGER_NAMES = ("app", "uvicorn", "uvicorn.error", "uvicorn.access")


def _resolve_level(level: str) -> int:
    value = getattr(logging, level.upper(), logging.INFO)
    return value if isinstance(value, int) else logging.INFO


def configure_logging(level: str) -> None:
    """统一控制台日志格式，兼容 Uvicorn 自带日志器与项目日志器。"""
    log_level = _resolve_level(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler(sys.stderr))
    root_logger.setLevel(log_level)
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    for logger_name in _LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)
        for handler in logger.handlers:
            handler.setFormatter(formatter)
