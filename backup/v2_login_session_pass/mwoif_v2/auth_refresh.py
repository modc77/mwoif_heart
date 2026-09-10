from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urljoin

from .models import LoginBundle, find_login_payload, jwt_exp
from .redact import safe_error_text

Event = Callable[[str], None]


class AuthRefreshError(RuntimeError):
    pass


def refresh_access_token(cfg, bundle: LoginBundle, event_cb: Event | None = None) -> LoginBundle:
    import requests

    base = str(cfg.raw.get("v2", {}).get("auth_base_url") or "https://account.prod.devsisters.cloud").rstrip("/") + "/"
    url = urljoin(base, "v3/refresh")
    body = {
        "device_id": str(cfg.devplay.get("device_id") or ""),
        "mid": bundle.mid,
        "refresh_token": bundle.refresh_token,
    }
    if event_cb:
        event_cb("AUTH REFRESH START")
    started = time.monotonic()
    try:
        r = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=float(cfg.server.get("timeout_seconds") or 20),
            verify=bool(cfg.server.get("verify_ssl", True)),
        )
    except requests.RequestException as exc:
        raise AuthRefreshError(f"refresh transport failed: {type(exc).__name__}") from exc

    if not r.ok:
        raise AuthRefreshError(f"refresh HTTP {r.status_code}: {safe_error_text(r.text)}")
    try:
        obj = r.json()
    except Exception as exc:
        raise AuthRefreshError("refresh response is not JSON") from exc
    payload = find_login_payload(obj)
    if payload is None:
        raise AuthRefreshError("refresh response has no ServerLoginResponse")
    updated = LoginBundle.from_payload(payload)
    if event_cb:
        event_cb(f"AUTH REFRESH OK {round((time.monotonic()-started)*1000)}ms")
    return updated


def needs_refresh(bundle: LoginBundle, before_seconds: int = 300) -> bool:
    exp = jwt_exp(bundle.game_access_token)
    return bool(exp and exp - int(time.time()) <= int(before_seconds))
