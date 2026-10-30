import re

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+[^\s,}]+", re.IGNORECASE),
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)"
    ),
    re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"),
)


def sanitize_sensitive_text(value: str | None, *, limit: int = 256) -> str | None:
    if value is None:
        return None
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized if len(sanitized) <= limit else f"{sanitized[: limit - 1]}…"
