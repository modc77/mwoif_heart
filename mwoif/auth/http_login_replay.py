from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests

from .devplay_login import LOGIN_COOKIE_NAMES, LOGIN_QUERY_NAMES, _build_login_web_context, safe_url
from .models import LoginBundle, find_login_payload

Event = Callable[[str], None]

_ALLOWED_HOST_SUFFIXES = ("devplay.com", "devsisters.cloud")
_SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "cookie",
    "authorization",
    "session",
    "access",
    "refresh",
    "device_secret",
)


def _emit(cb: Event | None, text: str) -> None:
    if cb:
        cb(text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _bool(value: Any, default: bool = False) -> bool:
    text = _text(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _is_allowed_host(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def _safe_endpoint(base_url: str, path: str) -> str:
    base = str(base_url or "https://account.devplay.com").rstrip("/") + "/"
    url = urljoin(base, str(path or "").lstrip("/"))
    if not _is_allowed_host(url):
        raise ValueError(f"endpoint host is not allowed: {safe_url(url)}")
    return url


def _split_list(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    # URLs produced by the SDK normally carry comma/pipe separated terms values.
    return [item.strip() for item in re.split(r"[,|]", text) if item.strip()]


def _terms_updates(query: dict[str, str]) -> list[dict[str, Any]]:
    ids = _split_list(query.get("terms_updates_ids"))
    values = _split_list(query.get("terms_updates_values"))
    out: list[dict[str, Any]] = []
    for i, term_id in enumerate(ids):
        raw_value = values[i] if i < len(values) else ""
        if raw_value.lower() in {"true", "false", "1", "0", "yes", "no"}:
            value: Any = _bool(raw_value, False)
        else:
            value = raw_value
        out.append({"id": term_id, "value": value})
    return out


def _nested_lc(query: dict[str, str]) -> dict[str, Any]:
    traits = query.get("lc.device.traits") or ""
    device: dict[str, Any] = {
        "manufacturer": query.get("lc.device.manufacturer") or "",
        "model": query.get("lc.device.model") or "",
        "version": query.get("lc.device.version") or "",
    }
    if traits:
        try:
            device["traits"] = json.loads(traits)
        except Exception:
            device["traits"] = traits

    lc: dict[str, Any] = {
        "anonymous_id": query.get("lc.anonymous_id") or "",
        "app_build": query.get("lc.app_build") or "",
        "app_installed_id": query.get("lc.app_installed_id") or "",
        "app_version": query.get("lc.app_version") or "",
        "device": device,
        "devsisters_id": query.get("lc.devsisters_id") or "",
        "fgs_id": query.get("lc.fgs_id") or "",
        "library_name": query.get("lc.library_name") or "",
        "library_version": query.get("lc.library_version") or "",
        "locale_on_game": query.get("lc.locale_on_game") or "",
        "location_country": query.get("lc.location_country") or "",
        "os_name": query.get("lc.os_name") or "",
        "os_version": query.get("lc.os_version") or "",
        "platform": query.get("lc.platform") or "",
        "semi_device_id": query.get("lc.semi_device_id") or "",
        "store": query.get("lc.store") or "",
        "timezone": query.get("lc.timezone") or "",
    }
    new_fgs_id = query.get("lc.new_fgs_id") or ""
    if new_fgs_id:
        lc["new_fgs_id"] = new_fgs_id
    return lc


def _flat_lc(query: dict[str, str]) -> dict[str, str]:
    return {name[3:]: (query.get(name) or "") for name in LOGIN_QUERY_NAMES if name.startswith("lc.")}


def _prefixed_lc(query: dict[str, str]) -> dict[str, str]:
    """Browser JS evidence suggests the POST key named `lc` may preserve
    the original query parameter names such as `lc.anonymous_id`.

    This is not a secret and it avoids fabricating values: only values already
    present in the LAB-exported login URL are forwarded.
    """
    return {name: (query.get(name) or "") for name in LOGIN_QUERY_NAMES if name.startswith("lc.")}


def _build_lc(query: dict[str, str], mode: str) -> Any:
    mode = str(mode or "nested").strip().lower()
    if mode in {"flat", "unprefixed"}:
        return _flat_lc(query)
    if mode in {"prefixed", "browser", "browser-prefixed", "raw-prefixed"}:
        return _prefixed_lc(query)
    if mode == "json-string":
        return json.dumps(_nested_lc(query), ensure_ascii=False, separators=(",", ":"))
    if mode in {"json-string-prefixed", "prefixed-string"}:
        return json.dumps(_prefixed_lc(query), ensure_ascii=False, separators=(",", ":"))
    return _nested_lc(query)


def _safe_lc_shape(lc: Any) -> dict[str, Any]:
    text = _text(lc)
    out: dict[str, Any] = {
        "type": type(lc).__name__,
        "len": len(text),
    }
    if isinstance(lc, dict):
        keys = sorted(str(k) for k in lc.keys())
        out["key_count"] = len(keys)
        out["keys_head"] = keys[:8]
        out["keys_tail"] = keys[-4:]
        out["has_lc_prefix_keys"] = any(k.startswith("lc.") for k in keys)
    return out


def _header_names(headers: dict[str, str]) -> list[str]:
    return sorted([str(k).lower() for k in headers.keys()])[:40]


def _cookie_names(response: requests.Response) -> list[str]:
    try:
        return sorted(response.cookies.keys())[:30]
    except Exception:
        return []


def _json_public(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "dict",
            "keys": sorted([str(k) for k in value.keys()])[:60],
            "key_count": len(value),
            "code": value.get("code") if isinstance(value.get("code"), (int, str, float, bool)) else None,
            "blocked_seconds": value.get("blocked_seconds") if isinstance(value.get("blocked_seconds"), (int, str, float, bool)) else None,
            "result_type": type(value.get("result")).__name__ if "result" in value else None,
            "result_len": len(_text(value.get("result"))) if "result" in value and not isinstance(value.get("result"), (dict, list)) else None,
        }
    if isinstance(value, list):
        return {"type": "list", "len": len(value)}
    return {"type": type(value).__name__}


def _redacted_post_keys(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in payload.items():
        low = key.lower()
        text = _text(value)
        if any(h in low for h in _SECRET_KEY_HINTS):
            out[key] = f"<REDACTED len={len(text)}>"
        elif "email" in low:
            out[key] = "<EMAIL>" if text else ""
        elif isinstance(value, (dict, list)):
            out[key] = f"<{type(value).__name__.upper()} len={len(text)}>"
        elif len(text) > 64:
            out[key] = f"<VALUE len={len(text)}>"
        else:
            out[key] = "<VALUE>" if text else ""
    return out


def _step_from_response(name: str, method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None, response: requests.Response) -> dict[str, Any]:
    item: dict[str, Any] = {
        "step": name,
        "method": method,
        "url": safe_url(url),
        "http_status": response.status_code,
        "content_type": str(response.headers.get("content-type") or "").split(";", 1)[0].lower(),
        "set_cookie_names": _cookie_names(response),
        "request_header_names": _header_names(headers),
    }
    if payload is not None:
        item["post_keys"] = sorted(payload.keys())
        item["fields_redacted"] = _redacted_post_keys(payload)
        if "lc" in payload:
            item["lc_shape"] = _safe_lc_shape(payload.get("lc"))
        if "terms_updates" in payload:
            item["terms_updates_shape"] = {
                "type": type(payload.get("terms_updates")).__name__,
                "len": len(_text(payload.get("terms_updates"))),
            }
    try:
        obj = response.json()
        item["json"] = _json_public(obj)
        item["contains_login_payload"] = find_login_payload(obj) is not None
    except Exception:
        item["json"] = {"type": "unreadable"}
    return item


@dataclass(slots=True)
class DirectHttpReplayResult:
    ok: bool
    stage: str
    code: str
    message: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    bundle: LoginBundle | None = None
    query_present: int = 0
    cookie_present: int = 0
    elapsed_ms: float = 0.0
    replay_mode: str = "recorded-v2"

    def public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": "direct-http-replay-v2",
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "replay_mode": self.replay_mode,
            "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
            "query_present": f"{self.query_present}/{len(LOGIN_QUERY_NAMES)}",
            "cookie_present": f"{self.cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "steps": self.steps[-10:],
            "login_bundle": self.bundle.public_summary() if self.bundle else None,
            "secretOutput": "NONE",
        }


def _prepare_context(cfg, fgs_id: str = "") -> tuple[str, dict[str, str], dict[str, str], list[str], list[str], int, int]:
    login_url, cookie_values, missing_query, missing_cookie = _build_login_web_context(cfg, fgs_id=fgs_id)
    query = {name: "" for name in LOGIN_QUERY_NAMES}
    try:
        parsed = urlsplit(login_url)
        raw = parse_qs(parsed.query, keep_blank_values=True)
        for name in LOGIN_QUERY_NAMES:
            query[name] = _text((raw.get(name) or [""])[0])
    except Exception:
        pass
    query_present = sum(1 for name in LOGIN_QUERY_NAMES if query.get(name))
    cookie_present = sum(1 for name in LOGIN_COOKIE_NAMES if cookie_values.get(name))
    return login_url, query, cookie_values, missing_query, missing_cookie, query_present, cookie_present


def _extract_user_token(value: Any) -> str:
    """Extract the transient user_token discovered by /v4/checkemail.

    Recorder evidence showed the subsequent /v3/login/devsisters request carries
    a field named user_token.  In direct replay v2/v3 this stayed empty because
    /v4/checkemail exposes only public top-level keys code,result.  The browser
    flow appears to forward the result value into user_token.

    The returned token is used only in-memory for the next request and is never
    printed.
    """
    if isinstance(value, dict):
        for key in ("user_token", "userToken", "token"):
            text = _text(value.get(key))
            if text:
                return text

        # DevPlay /v4/checkemail returns {code: 20000, result: ...}.
        # The result can be the one-time user token required by the next login
        # request.  Do not treat booleans/short literals as tokens.
        raw_result = value.get("result")
        if isinstance(raw_result, (str, int, float)) and not isinstance(raw_result, bool):
            text = _text(raw_result)
            if len(text) >= 8:
                return text

        for child in value.values():
            got = _extract_user_token(child)
            if got:
                return got
    elif isinstance(value, list):
        for child in value:
            got = _extract_user_token(child)
            if got:
                return got
    return ""


def login_with_email_password_http_replay_v2(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    fgs_id: str = "",
) -> DirectHttpReplayResult:
    """Replay the real endpoints discovered by Phase 4.5.1 without opening a browser.

    Recorder evidence for DevPlay Auth V2:
      POST https://account.devplay.com/v4/checkemail       keys=email,lc
      POST https://account.devplay.com/v3/login/devsisters keys=email,password,lc,...

    This probe uses only local LAB context + stored credentials.  It does not log
    raw cookies, passwords, tokens, session keys, request bodies, or raw response bodies.
    """
    start = time.monotonic()
    email = str(email or "").strip()
    if not email or not password:
        return DirectHttpReplayResult(False, "PRECHECK", "EMPTY_CREDENTIAL", "email/password is empty")

    try:
        login_url, query, cookie_values, missing_query, missing_cookie, query_present, cookie_present = _prepare_context(cfg, fgs_id=fgs_id)
    except Exception as exc:
        return DirectHttpReplayResult(False, "CONTEXT", "WEB_CONTEXT_BUILD_FAILED", f"{type(exc).__name__}: {exc}")

    _emit(
        event_cb,
        "HTTP REPLAY WEB CONTEXT "
        f"query_schema={len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)} "
        f"query_present={query_present}/{len(LOGIN_QUERY_NAMES)} "
        f"cookie_present={cookie_present}/{len(LOGIN_COOKIE_NAMES)} "
        "secretOutput=NONE",
    )

    if missing_query or missing_cookie:
        parts: list[str] = []
        if missing_query:
            parts.append("missing_query=" + ",".join(missing_query))
        if missing_cookie:
            parts.append("missing_cookie=" + ",".join(missing_cookie))
        return DirectHttpReplayResult(
            False,
            "CONTEXT",
            "WEB_CONTEXT_INCOMPLETE",
            "; ".join(parts),
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    v2 = cfg.raw.get("v2", {})
    account_base_url = str(v2.get("devplay_account_base_url") or "https://account.devplay.com")
    timeout_s = int(v2.get("http_login_timeout_seconds") or 35)
    lc_mode = str(v2.get("http_login_lc_mode") or "nested").strip().lower()
    warmup = _bool(v2.get("http_login_warmup_get"), True)
    locale = str(cfg.devplay.get("locale") or query.get("lc.locale_on_game") or "en-US")

    try:
        checkemail_url = _safe_endpoint(account_base_url, str(v2.get("http_login_checkemail_path") or "/v4/checkemail"))
        login_endpoint_url = _safe_endpoint(account_base_url, str(v2.get("http_login_devsisters_path") or "/v3/login/devsisters"))
    except Exception as exc:
        return DirectHttpReplayResult(
            False,
            "CONTEXT",
            "ENDPOINT_NOT_ALLOWED",
            f"{type(exc).__name__}: {exc}",
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    session = requests.Session()
    user_agent = (
        "Mozilla/5.0 (Linux; Android 12; SM-A156E Build/SP1A.210812.016; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    )
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": locale.replace("_", "-") + ",en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )

    for host in ("app.devplay.com", "account.devplay.com"):
        for name in LOGIN_COOKIE_NAMES:
            value = cookie_values.get(name)
            if value:
                session.cookies.set(name, value, domain=host, path="/")

    api_key = cookie_values.get("api_key") or ""
    bundle_id = cookie_values.get("bundle_id") or ""
    common_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": locale.replace("_", "-") + ",en;q=0.8",
        "Content-Type": "application/json",
        "Origin": _origin(login_url),
        "Referer": login_url,
        "User-Agent": user_agent,
        "X-API-Key": api_key,
        "X-Bundle-Id": bundle_id,
    }

    steps: list[dict[str, Any]] = []
    lc = _build_lc(query, lc_mode)
    _emit(event_cb, "HTTP REPLAY START endpoints=checkemail,login-devsisters browser=NO secretOutput=NONE")

    try:
        if warmup:
            warm = session.get(login_url, timeout=timeout_s, allow_redirects=True)
            warm_step = _step_from_response("warmup-login-try", "GET", login_url, {"Referer": ""}, None, warm)
            warm_step["url"] = safe_url(login_url)
            steps.append(warm_step)
            _emit(event_cb, f"HTTP REPLAY WARMUP status={warm.status_code} url={safe_url(warm.url)} secretOutput=NONE")

        check_payload: dict[str, Any] = {"email": email, "lc": lc}
        _emit(event_cb, f"HTTP REPLAY POST {safe_url(checkemail_url)} post_keys=email,lc secretOutput=NONE")
        check_resp = session.post(checkemail_url, json=check_payload, headers=common_headers, timeout=timeout_s, allow_redirects=True)
        steps.append(_step_from_response("checkemail", "POST", checkemail_url, common_headers, check_payload, check_resp))
        check_json: Any = None
        try:
            check_json = check_resp.json()
        except Exception:
            check_json = None
        if check_resp.status_code >= 400:
            return DirectHttpReplayResult(
                False,
                "CHECKEMAIL",
                "CHECKEMAIL_HTTP_FAILED",
                f"http_status={check_resp.status_code}",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        user_token = _extract_user_token(check_json)
        user_token_source = "checkemail.result-or-token" if user_token else "empty"
        login_payload: dict[str, Any] = {
            "agree_ad_day_push": _bool(query.get("agree_ad_day_push"), False),
            "agree_ad_night_push": _bool(query.get("agree_ad_night_push"), False),
            "device_id": query.get("device_id") or "",
            "device_type": query.get("device_type") or "",
            "email": email,
            "lang": query.get("lang") or str(cfg.devplay.get("locale") or "en-US").split("-", 1)[0],
            "lc": lc,
            "oven_access_token": cookie_values.get("oven_access_token") or "",
            "password": password,
            "push_token": query.get("push_token") or "",
            "recall_session_id": query.get("recall_session_id") or "",
            "terms_updates": _terms_updates(query),
            "timezone": query.get("timezone") or query.get("lc.timezone") or "",
            "user_token": user_token,
        }

        _emit(
            event_cb,
            "HTTP REPLAY POST "
            f"{safe_url(login_endpoint_url)} "
            "post_keys=agree_ad_day_push,agree_ad_night_push,device_id,device_type,email,lang,lc,oven_access_token,password,push_token,recall_session_id,terms_updates,timezone,user_token "
            "secretOutput=NONE",
        )
        login_resp = session.post(login_endpoint_url, json=login_payload, headers=common_headers, timeout=timeout_s, allow_redirects=True)
        login_step = _step_from_response("login-devsisters", "POST", login_endpoint_url, common_headers, login_payload, login_resp)
        login_step["user_token_source"] = user_token_source
        login_step["user_token_present"] = bool(user_token)
        login_step["user_token_len"] = len(user_token) if user_token else 0
        steps.append(login_step)

        login_json: Any = None
        try:
            login_json = login_resp.json()
        except Exception:
            login_json = None
        hit = find_login_payload(login_json)
        if hit is None:
            return DirectHttpReplayResult(
                False,
                "LOGIN",
                "LOGIN_PAYLOAD_NOT_FOUND",
                "Direct HTTP replay finished but ServerLoginResponse was not found. Keep browser provider as fallback and inspect safe step json keys.",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
                replay_mode=f"recorded-v2/lc={lc_mode}",
            )

        try:
            bundle = LoginBundle.from_payload(hit)
        except Exception as exc:
            return DirectHttpReplayResult(
                False,
                "LOGIN",
                "LOGIN_PAYLOAD_INVALID",
                f"{type(exc).__name__}: {exc}",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
                replay_mode=f"recorded-v2/lc={lc_mode}",
            )

        _emit(event_cb, "HTTP REPLAY LOGIN SESSION CAPTURED (secrets redacted)")
        return DirectHttpReplayResult(
            True,
            "CAPTURE",
            "HTTP_REPLAY_LOGIN_CAPTURED",
            "ServerLoginResponse captured by direct HTTP replay v2",
            steps=steps,
            bundle=bundle,
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
            replay_mode=f"recorded-v2/lc={lc_mode}",
        )
    except requests.RequestException as exc:
        return DirectHttpReplayResult(
            False,
            "NETWORK",
            "HTTP_REPLAY_REQUEST_FAILED",
            f"{type(exc).__name__}: {exc}",
            steps=steps,
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
            replay_mode=f"recorded-v2/lc={lc_mode}",
        )


# ---------------------------------------------------------------------------
# Phase 4.5.5: Direct HTTP Replay Matrix / Safe Diff
# ---------------------------------------------------------------------------

def _terms_updates_value(query: dict[str, str], mode: str) -> Any:
    """Build safe terms_updates variants from LAB-exported query values only."""
    mode = str(mode or "list").strip().lower()
    terms = _terms_updates(query)
    ids = _split_list(query.get("terms_updates_ids"))
    values = _split_list(query.get("terms_updates_values"))

    if mode in {"omit", "omitted", "none"}:
        return None
    if mode in {"empty", "empty-list"}:
        return []
    if mode in {"json", "json-string", "list-string"}:
        return json.dumps(terms, ensure_ascii=False, separators=(",", ":"))
    if mode in {"map", "dict", "id-map"}:
        return {str(item.get("id") or ""): item.get("value") for item in terms if item.get("id")}
    if mode in {"raw", "raw-query", "ids-values"}:
        return {"ids": ids, "values": values}
    if mode in {"ids", "id-list"}:
        return ids
    return terms


def _coerce_bool_value(value: Any, mode: str) -> Any:
    mode = str(mode or "bool").strip().lower()
    b = _bool(value, False)
    if mode in {"string", "str"}:
        return "true" if b else "false"
    if mode in {"int", "number"}:
        return 1 if b else 0
    return b


def _build_login_payload_variant(
    *,
    query: dict[str, str],
    cookie_values: dict[str, str],
    cfg: Any,
    email: str,
    password: str,
    lc: Any,
    terms_mode: str,
    bool_mode: str,
    user_token: str,
    include_empty: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agree_ad_day_push": _coerce_bool_value(query.get("agree_ad_day_push"), bool_mode),
        "agree_ad_night_push": _coerce_bool_value(query.get("agree_ad_night_push"), bool_mode),
        "device_id": query.get("device_id") or "",
        "device_type": query.get("device_type") or "",
        "email": email,
        "lang": query.get("lang") or str(cfg.devplay.get("locale") or "en-US").split("-", 1)[0],
        "lc": lc,
        "oven_access_token": cookie_values.get("oven_access_token") or "",
        "password": password,
        "push_token": query.get("push_token") or "",
        "recall_session_id": query.get("recall_session_id") or "",
        "timezone": query.get("timezone") or query.get("lc.timezone") or "",
        "user_token": user_token,
    }
    terms_value = _terms_updates_value(query, terms_mode)
    if terms_value is not None:
        payload["terms_updates"] = terms_value
    if not include_empty:
        # Keep email/password/lc even if malformed; remove only optional blank fields.
        required = {"email", "password", "lc", "terms_updates"}
        payload = {k: v for k, v in payload.items() if k in required or _text(v) != ""}
    return payload


def _headers_variant(login_url: str, locale: str, api_key: str, bundle_id: str, user_agent: str, mode: str) -> dict[str, str]:
    mode = str(mode or "browser").strip().lower()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": locale.replace("_", "-") + ",en;q=0.8",
        "Content-Type": "application/json",
        "Referer": login_url,
        "User-Agent": user_agent,
        "X-API-Key": api_key,
        "X-Bundle-Id": bundle_id,
    }
    if mode not in {"no-origin", "browser-no-origin"}:
        headers["Origin"] = _origin(login_url)
    if mode in {"browser", "browser-like", "chromium", "browser-no-origin"}:
        headers.update(
            {
                "Sec-CH-UA": '"Not_A Brand";v="8", "Chromium";v="120", "Android WebView";v="120"',
                "Sec-CH-UA-Mobile": "?1",
                "Sec-CH-UA-Platform": '"Android"',
            }
        )
    return headers


def _post_json(session: requests.Session, url: str, payload: dict[str, Any], headers: dict[str, str], timeout_s: int, send_mode: str) -> requests.Response:
    mode = str(send_mode or "compact-json").strip().lower()
    if mode in {"compact", "compact-json", "json-compact"}:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return session.post(url, data=body, headers=headers, timeout=timeout_s, allow_redirects=True)
    return session.post(url, json=payload, headers=headers, timeout=timeout_s, allow_redirects=True)


def _safe_attempt_fp(parts: dict[str, Any]) -> str:
    text = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(slots=True)
class DirectHttpMatrixAttempt:
    n: int
    check_lc: str
    login_lc: str
    terms: str
    headers: str
    send: str
    bools: str
    include_empty: bool
    ok: bool = False
    check_http: int | None = None
    check_code: Any = None
    check_result_len: int | None = None
    login_http: int | None = None
    login_code: Any = None
    payload_hit: bool = False
    error: str | None = None
    elapsed_ms: float = 0.0
    fp: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "fp": self.fp,
            "check_lc": self.check_lc,
            "login_lc": self.login_lc,
            "terms": self.terms,
            "headers": self.headers,
            "send": self.send,
            "bools": self.bools,
            "include_empty": self.include_empty,
            "ok": self.ok,
            "check_http": self.check_http,
            "check_code": self.check_code,
            "check_result_len": self.check_result_len,
            "login_http": self.login_http,
            "login_code": self.login_code,
            "payload_hit": self.payload_hit,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass(slots=True)
class DirectHttpMatrixResult:
    ok: bool
    stage: str
    code: str
    message: str
    attempts: list[DirectHttpMatrixAttempt] = field(default_factory=list)
    bundle: LoginBundle | None = None
    query_present: int = 0
    cookie_present: int = 0
    elapsed_ms: float = 0.0
    hit_attempt: DirectHttpMatrixAttempt | None = None
    report_path: str | None = None

    def public_dict(self) -> dict[str, Any]:
        passed_check = sum(1 for a in self.attempts if str(a.check_code) == "20000")
        payload_hits = sum(1 for a in self.attempts if a.payload_hit)
        attempts_sorted = sorted(
            self.attempts,
            key=lambda a: (
                0 if a.payload_hit else 1,
                0 if str(a.login_code) == "20000" else 1,
                0 if str(a.check_code) == "20000" else 1,
                a.n,
            ),
        )
        return {
            "ok": self.ok,
            "provider": "direct-http-replay-matrix-v1",
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
            "query_present": f"{self.query_present}/{len(LOGIN_QUERY_NAMES)}",
            "cookie_present": f"{self.cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "attempt_count": len(self.attempts),
            "check_pass_count": passed_check,
            "payload_hit_count": payload_hits,
            "hit_attempt": self.hit_attempt.public_dict() if self.hit_attempt else None,
            "best_attempts": [a.public_dict() for a in attempts_sorted[:12]],
            "report_path": self.report_path,
            "login_bundle": self.bundle.public_summary() if self.bundle else None,
            "secretOutput": "NONE",
        }


def _matrix_plan(max_attempts: int) -> list[dict[str, Any]]:
    # Compact but high-signal plan. It focuses on evidence from the recorder:
    # checkemail likes prefixed LC; login still fails, so vary login LC, terms,
    # browser-like headers and body encoding. No fabricated values are introduced.
    login_lc_modes = ["prefixed", "json-string-prefixed", "nested", "json-string", "flat"]
    term_modes = ["list", "json-string", "map", "raw-query", "empty-list", "omitted"]
    header_modes = ["browser", "current", "no-origin"]
    send_modes = ["compact-json", "requests-json"]
    bool_modes = ["bool", "string"]

    plan: list[dict[str, Any]] = []

    # Known best checkemail shape first.
    for terms in term_modes:
        for login_lc in login_lc_modes:
            plan.append(
                {
                    "check_lc": "prefixed",
                    "login_lc": login_lc,
                    "terms": terms,
                    "headers": "browser",
                    "send": "compact-json",
                    "bools": "bool",
                    "include_empty": True,
                }
            )

    # Header/body/boolean variations around the two most likely body shapes.
    for headers in header_modes:
        for send in send_modes:
            for bools in bool_modes:
                for terms in ("list", "json-string", "map"):
                    plan.append(
                        {
                            "check_lc": "prefixed",
                            "login_lc": "prefixed",
                            "terms": terms,
                            "headers": headers,
                            "send": send,
                            "bools": bools,
                            "include_empty": True,
                        }
                    )

    # Check whether checkemail can pass with string LC and pair it with same login LC.
    for check_lc in ("json-string-prefixed", "prefixed-string", "nested", "json-string"):
        for terms in ("list", "json-string"):
            plan.append(
                {
                    "check_lc": check_lc,
                    "login_lc": check_lc,
                    "terms": terms,
                    "headers": "browser",
                    "send": "compact-json",
                    "bools": "bool",
                    "include_empty": True,
                }
            )

    # Try omitting optional blanks near the end.
    for login_lc in ("prefixed", "json-string-prefixed", "nested"):
        for terms in ("list", "json-string", "map"):
            plan.append(
                {
                    "check_lc": "prefixed",
                    "login_lc": login_lc,
                    "terms": terms,
                    "headers": "browser",
                    "send": "compact-json",
                    "bools": "bool",
                    "include_empty": False,
                }
            )

    # Deduplicate while preserving order.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in plan:
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= max_attempts:
            break
    return unique


def _save_matrix_report(cfg: Any, account_kind: str, account_id: int, result: DirectHttpMatrixResult) -> str | None:
    try:
        v2 = cfg.raw.get("v2", {})
        if not _bool(v2.get("http_matrix_save"), True):
            return None
        log_dir = cfg.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = log_dir / f"http_login_matrix_{account_kind}_{account_id}_{stamp}.json"
        path.write_text(json.dumps(result.public_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        return None


def login_with_email_password_http_replay_matrix(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    fgs_id: str = "",
    account_kind: str = "account",
    account_id: int = 0,
) -> DirectHttpMatrixResult:
    start_all = time.monotonic()
    email = str(email or "").strip()
    if not email or not password:
        return DirectHttpMatrixResult(False, "PRECHECK", "EMPTY_CREDENTIAL", "email/password is empty")

    try:
        login_url, query, cookie_values, missing_query, missing_cookie, query_present, cookie_present = _prepare_context(cfg, fgs_id=fgs_id)
    except Exception as exc:
        return DirectHttpMatrixResult(False, "CONTEXT", "WEB_CONTEXT_BUILD_FAILED", f"{type(exc).__name__}: {exc}")

    if missing_query or missing_cookie:
        parts: list[str] = []
        if missing_query:
            parts.append("missing_query=" + ",".join(missing_query))
        if missing_cookie:
            parts.append("missing_cookie=" + ",".join(missing_cookie))
        return DirectHttpMatrixResult(False, "CONTEXT", "WEB_CONTEXT_INCOMPLETE", "; ".join(parts), query_present=query_present, cookie_present=cookie_present)

    v2 = cfg.raw.get("v2", {})
    account_base_url = str(v2.get("devplay_account_base_url") or "https://account.devplay.com")
    timeout_s = int(v2.get("http_login_timeout_seconds") or 35)
    warmup = _bool(v2.get("http_login_warmup_get"), True)
    locale = str(cfg.devplay.get("locale") or query.get("lc.locale_on_game") or "en-US")
    max_attempts = int(v2.get("http_matrix_max_attempts") or 48)
    stop_on_hit = _bool(v2.get("http_matrix_stop_on_hit"), True)
    sleep_ms = int(v2.get("http_matrix_sleep_ms") or 150)

    try:
        checkemail_url = _safe_endpoint(account_base_url, str(v2.get("http_login_checkemail_path") or "/v4/checkemail"))
        login_endpoint_url = _safe_endpoint(account_base_url, str(v2.get("http_login_devsisters_path") or "/v3/login/devsisters"))
    except Exception as exc:
        return DirectHttpMatrixResult(False, "CONTEXT", "ENDPOINT_NOT_ALLOWED", f"{type(exc).__name__}: {exc}", query_present=query_present, cookie_present=cookie_present)

    user_agent = (
        "Mozilla/5.0 (Linux; Android 12; SM-A156E Build/SP1A.210812.016; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    )
    api_key = cookie_values.get("api_key") or ""
    bundle_id = cookie_values.get("bundle_id") or ""
    plan = _matrix_plan(max_attempts=max_attempts)

    _emit(
        event_cb,
        "HTTP MATRIX START "
        f"attempts={len(plan)} max={max_attempts} "
        f"endpoints=checkemail,login-devsisters browser=NO secretOutput=NONE",
    )

    attempts: list[DirectHttpMatrixAttempt] = []
    hit_bundle: LoginBundle | None = None
    hit_attempt: DirectHttpMatrixAttempt | None = None

    for idx, variant in enumerate(plan, start=1):
        t0 = time.monotonic()
        attempt = DirectHttpMatrixAttempt(
            n=idx,
            check_lc=str(variant["check_lc"]),
            login_lc=str(variant["login_lc"]),
            terms=str(variant["terms"]),
            headers=str(variant["headers"]),
            send=str(variant["send"]),
            bools=str(variant["bools"]),
            include_empty=bool(variant["include_empty"]),
        )
        attempt.fp = _safe_attempt_fp(variant)
        attempts.append(attempt)
        try:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": user_agent,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": locale.replace("_", "-") + ",en;q=0.8",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                }
            )
            for host in ("app.devplay.com", "account.devplay.com"):
                for name in LOGIN_COOKIE_NAMES:
                    value = cookie_values.get(name)
                    if value:
                        session.cookies.set(name, value, domain=host, path="/")

            headers = _headers_variant(login_url, locale, api_key, bundle_id, user_agent, attempt.headers)
            if warmup:
                warm = session.get(login_url, timeout=timeout_s, allow_redirects=True)
                # Warmup status is not enough to decide; record only request-level errors.
                if warm.status_code >= 500:
                    attempt.error = f"warmup_http_{warm.status_code}"

            check_lc = _build_lc(query, attempt.check_lc)
            check_payload: dict[str, Any] = {"email": email, "lc": check_lc}
            check_resp = _post_json(session, checkemail_url, check_payload, headers, timeout_s, attempt.send)
            attempt.check_http = check_resp.status_code
            check_json: Any = None
            try:
                check_json = check_resp.json()
            except Exception:
                check_json = None
            if isinstance(check_json, dict):
                attempt.check_code = check_json.get("code")
                result_value = check_json.get("result")
                attempt.check_result_len = len(_text(result_value)) if result_value is not None and not isinstance(result_value, (dict, list)) else None
            user_token = _extract_user_token(check_json)

            login_lc = _build_lc(query, attempt.login_lc)
            login_payload = _build_login_payload_variant(
                query=query,
                cookie_values=cookie_values,
                cfg=cfg,
                email=email,
                password=password,
                lc=login_lc,
                terms_mode=attempt.terms,
                bool_mode=attempt.bools,
                user_token=user_token,
                include_empty=attempt.include_empty,
            )
            login_resp = _post_json(session, login_endpoint_url, login_payload, headers, timeout_s, attempt.send)
            attempt.login_http = login_resp.status_code
            login_json: Any = None
            try:
                login_json = login_resp.json()
            except Exception:
                login_json = None
            if isinstance(login_json, dict):
                attempt.login_code = login_json.get("code")
            hit = find_login_payload(login_json)
            attempt.payload_hit = hit is not None
            attempt.ok = bool(hit)
            if hit is not None:
                hit_bundle = LoginBundle.from_payload(hit)
                hit_attempt = attempt
                attempt.elapsed_ms = (time.monotonic() - t0) * 1000
                _emit(
                    event_cb,
                    "HTTP MATRIX HIT "
                    f"n={attempt.n} fp={attempt.fp} check_lc={attempt.check_lc} login_lc={attempt.login_lc} "
                    f"terms={attempt.terms} headers={attempt.headers} send={attempt.send} secretOutput=NONE",
                )
                if stop_on_hit:
                    break
        except Exception as exc:
            attempt.error = f"{type(exc).__name__}: {exc}"
        finally:
            attempt.elapsed_ms = attempt.elapsed_ms or ((time.monotonic() - t0) * 1000)
            _emit(
                event_cb,
                "HTTP MATRIX "
                f"{attempt.n:03d}/{len(plan):03d} fp={attempt.fp} "
                f"check_lc={attempt.check_lc} login_lc={attempt.login_lc} terms={attempt.terms} "
                f"headers={attempt.headers} send={attempt.send} bools={attempt.bools} "
                f"check={attempt.check_code} login={attempt.login_code} payload={'YES' if attempt.payload_hit else 'NO'} "
                "secretOutput=NONE",
            )
            if sleep_ms > 0 and idx < len(plan) and hit_bundle is None:
                time.sleep(sleep_ms / 1000.0)

    ok = hit_bundle is not None
    result = DirectHttpMatrixResult(
        ok=ok,
        stage="CAPTURE" if ok else "LOGIN",
        code="HTTP_MATRIX_LOGIN_CAPTURED" if ok else "HTTP_MATRIX_NO_HIT",
        message="ServerLoginResponse captured by direct HTTP matrix" if ok else "Matrix finished but ServerLoginResponse was not found. Use browser/headless fallback or inspect safe report.",
        attempts=attempts,
        bundle=hit_bundle,
        query_present=query_present,
        cookie_present=cookie_present,
        elapsed_ms=(time.monotonic() - start_all) * 1000,
        hit_attempt=hit_attempt,
    )
    result.report_path = _save_matrix_report(cfg, account_kind, account_id, result)
    if result.report_path:
        _emit(event_cb, f"HTTP MATRIX REPORT {result.report_path} secretOutput=NONE")
    return result
