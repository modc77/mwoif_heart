from __future__ import annotations

from urllib.parse import urlsplit

SECRET_WORDS = (
    "password", "passwd", "refresh_token", "refreshtoken",
    "game_access_token", "gameaccesstoken", "oven_access_token",
    "ovenaccesstoken", "device_secret", "authorization", "sessionkey",
    "session_key",
)


def safe_url(url: str) -> str:
    try:
        p = urlsplit(str(url or ""))
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return "<URL>"


def safe_error_text(text: str, limit: int = 240) -> str:
    value = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    lowered = value.lower()
    if any(word in lowered for word in SECRET_WORDS):
        return "<REDACTED_ERROR_BODY>"
    return value[:limit]
