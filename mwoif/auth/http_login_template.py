from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

from .devplay_login import LOGIN_COOKIE_NAMES, LOGIN_QUERY_NAMES, _build_login_web_context, _candidate_from_url, _find_visible, safe_url
from .http_login_replay import _extract_user_token
from .models import LoginBundle, find_login_payload

Event = Callable[[str], None]

_ALLOWED_HOST_SUFFIXES = ("devplay.com", "devsisters.cloud")
_SECRET_KEYS = {
    "email",
    "password",
    "user_token",
    "push_token",
    "recall_session_id",
    "oven_access_token",
}
_SECRET_HEADER_HINTS = ("authorization", "cookie", "token", "secret", "session")
_PLACEHOLDERS = {
    "email": "{{EMAIL}}",
    "password": "{{PASSWORD}}",
    "user_token": "{{USER_TOKEN}}",
    "push_token": "{{QUERY:push_token}}",
    "recall_session_id": "{{QUERY:recall_session_id}}",
    "oven_access_token": "{{COOKIE:oven_access_token}}",
}


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
    return str(value)


def _bool(value: Any, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _origin(url: str) -> str:
    p = urlsplit(str(url or ""))
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def _host_allowed(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def _safe_path(url: str) -> str:
    try:
        p = urlsplit(str(url or ""))
        return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except Exception:
        return "<invalid-url>"


def _sha12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:12]


def _cookie_names_from_header(value: str) -> list[str]:
    out: list[str] = []
    for part in str(value or "").split(";"):
        name = part.strip().split("=", 1)[0].strip()
        if name and name not in out:
            out.append(name)
    return sorted(out)[:40]


def _header_public(headers: dict[str, Any]) -> dict[str, Any]:
    names = sorted(str(k).lower() for k in (headers or {}).keys())[:80]
    return {"names": names, "count": len(headers or {})}


def _redact_header_template(headers: dict[str, Any]) -> dict[str, str]:
    """Keep replay-useful browser headers, but never persist raw cookies/tokens.

    The template is a local private file.  Even so, sensitive headers are not
    stored.  API key/bundle id are sourced from the current LAB web context at
    replay time.
    """
    allow = {
        "accept",
        "accept-language",
        "content-type",
        "origin",
        "referer",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "user-agent",
        "x-api-key",
        "x-bundle-id",
    }
    out: dict[str, str] = {}
    for raw_name, raw_value in (headers or {}).items():
        name = str(raw_name).lower()
        if name not in allow:
            continue
        if any(h in name for h in _SECRET_HEADER_HINTS):
            continue
        if name in {"x-api-key", "x-bundle-id"}:
            out[name] = "{{CONTEXT:" + name + "}}"
        elif name == "referer":
            out[name] = "{{LOGIN_URL}}"
        elif name == "origin":
            out[name] = "{{LOGIN_ORIGIN}}"
        else:
            out[name] = str(raw_value or "")
    return out


def _post_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj
    except Exception:
        return None


def _replace_secrets_in_obj(obj: Any, *, email: str, password: str, user_token: str) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            low = str(key).lower()
            if low in _PLACEHOLDERS:
                out[key] = _PLACEHOLDERS[low]
            elif low == "email":
                out[key] = "{{EMAIL}}"
            elif low == "password":
                out[key] = "{{PASSWORD}}"
            elif low == "user_token":
                out[key] = "{{USER_TOKEN}}"
            elif low == "push_token":
                out[key] = "{{QUERY:push_token}}"
            elif low == "recall_session_id":
                out[key] = "{{QUERY:recall_session_id}}"
            elif low == "oven_access_token":
                out[key] = "{{COOKIE:oven_access_token}}"
            else:
                out[key] = _replace_secrets_in_obj(value, email=email, password=password, user_token=user_token)
        return out
    if isinstance(obj, list):
        return [_replace_secrets_in_obj(v, email=email, password=password, user_token=user_token) for v in obj]
    if isinstance(obj, str):
        # Safety net for any secret value that may be embedded below a non-secret key.
        text = obj
        replacements = {
            email: "{{EMAIL}}",
            password: "{{PASSWORD}}",
            user_token: "{{USER_TOKEN}}",
        }
        for old, new in replacements.items():
            if old:
                text = text.replace(old, new)
        return text
    return obj


def _replace_secrets_in_raw(raw: str, *, email: str, password: str, user_token: str) -> str:
    text = str(raw or "")
    for old, new in ((email, "{{EMAIL}}"), (password, "{{PASSWORD}}"), (user_token, "{{USER_TOKEN}}")):
        if old:
            text = text.replace(old, new)
    return text


def _summarize_template_body(template_obj: Any, raw_template: str) -> dict[str, Any]:
    if isinstance(template_obj, dict):
        keys = sorted(str(k) for k in template_obj.keys())
        return {
            "type": "dict",
            "keys": keys[:80],
            "key_count": len(keys),
            "raw_template_len": len(raw_template or ""),
            "raw_template_fp": _sha12(raw_template or ""),
            "secret_placeholders": sorted({v for v in _PLACEHOLDERS.values()} | {"{{EMAIL}}", "{{PASSWORD}}", "{{USER_TOKEN}}"}),
        }
    return {"type": type(template_obj).__name__, "raw_template_len": len(raw_template or ""), "raw_template_fp": _sha12(raw_template or "")}


def _public_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "dict",
            "keys": sorted(str(k) for k in value.keys())[:80],
            "key_count": len(value),
            "code": value.get("code") if isinstance(value.get("code"), (str, int, float, bool)) else None,
            "result_type": type(value.get("result")).__name__ if "result" in value else None,
            "result_len": len(_text(value.get("result"))) if "result" in value and not isinstance(value.get("result"), (dict, list)) else None,
        }
    if isinstance(value, list):
        return {"type": "list", "len": len(value)}
    return {"type": type(value).__name__}


def _prepare_context(cfg, fgs_id: str = "") -> tuple[str, dict[str, str], dict[str, str], list[str], list[str], int, int]:
    login_url, cookie_values, missing_query, missing_cookie = _build_login_web_context(cfg, fgs_id=fgs_id)
    query = {name: "" for name in LOGIN_QUERY_NAMES}
    try:
        raw = parse_qs(urlsplit(login_url).query, keep_blank_values=True)
        for name in LOGIN_QUERY_NAMES:
            query[name] = _text((raw.get(name) or [""])[0])
    except Exception:
        pass
    query_present = sum(1 for name in LOGIN_QUERY_NAMES if query.get(name))
    cookie_present = sum(1 for name in LOGIN_COOKIE_NAMES if cookie_values.get(name))
    return login_url, query, cookie_values, missing_query, missing_cookie, query_present, cookie_present


def _template_path(cfg, account_kind: str, account_id: int) -> Path:
    v2 = cfg.raw.get("v2", {})
    base = str(v2.get("http_exact_template_file") or "").strip()
    if base:
        path = Path(base)
        if not path.is_absolute():
            path = cfg.root / path
        return path
    safe_kind = re.sub(r"[^a-z0-9_-]+", "_", str(account_kind or "account").lower())
    return cfg.root / "state" / f"http_login_exact_template_{safe_kind}_{int(account_id)}.private.json"


def _apply_template_value(value: Any, *, email: str, password: str, user_token: str, query: dict[str, str], cookies: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _apply_template_value(v, email=email, password=password, user_token=user_token, query=query, cookies=cookies) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_template_value(v, email=email, password=password, user_token=user_token, query=query, cookies=cookies) for v in value]
    if isinstance(value, str):
        text = value
        replacements = {
            "{{EMAIL}}": email,
            "{{PASSWORD}}": password,
            "{{USER_TOKEN}}": user_token,
            "{{QUERY:push_token}}": query.get("push_token") or "",
            "{{QUERY:recall_session_id}}": query.get("recall_session_id") or "",
            "{{COOKIE:oven_access_token}}": cookies.get("oven_access_token") or "",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text
    return value


def _apply_template_headers(headers: dict[str, str], *, login_url: str, cookies: dict[str, str], locale: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key)
        text = str(value or "")
        text = text.replace("{{LOGIN_URL}}", login_url)
        text = text.replace("{{LOGIN_ORIGIN}}", _origin(login_url))
        text = text.replace("{{CONTEXT:x-api-key}}", cookies.get("api_key") or "")
        text = text.replace("{{CONTEXT:x-bundle-id}}", cookies.get("bundle_id") or "")
        if text:
            out[name] = text
    # Required fallback when template omitted a browser header.
    out.setdefault("accept", "application/json, text/plain, */*")
    out.setdefault("accept-language", locale.replace("_", "-") + ",en;q=0.8")
    out.setdefault("content-type", "application/json")
    out.setdefault("origin", _origin(login_url))
    out.setdefault("referer", login_url)
    if cookies.get("api_key"):
        out.setdefault("x-api-key", cookies.get("api_key") or "")
    if cookies.get("bundle_id"):
        out.setdefault("x-bundle-id", cookies.get("bundle_id") or "")
    return out


@dataclass(slots=True)
class ExactTemplateCaptureResult:
    ok: bool
    stage: str
    code: str
    message: str
    bundle: LoginBundle | None = None
    template_path: str | None = None
    elapsed_ms: float = 0.0
    query_present: int = 0
    cookie_present: int = 0
    captured_requests: list[dict[str, Any]] = field(default_factory=list)
    template_summary: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": "browser-exact-template-capture-v1",
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
            "query_present": f"{self.query_present}/{len(LOGIN_QUERY_NAMES)}",
            "cookie_present": f"{self.cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "captured_requests": self.captured_requests[-8:],
            "template_summary": self.template_summary,
            "template_path": self.template_path,
            "login_bundle": self.bundle.public_summary() if self.bundle else None,
            "secretOutput": "NONE",
        }


@dataclass(slots=True)
class ExactTemplateReplayResult:
    ok: bool
    stage: str
    code: str
    message: str
    bundle: LoginBundle | None = None
    template_path: str | None = None
    template_account_kind: str | None = None
    template_account_id: int | None = None
    elapsed_ms: float = 0.0
    query_present: int = 0
    cookie_present: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": "direct-http-exact-template-replay-v1",
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
            "query_present": f"{self.query_present}/{len(LOGIN_QUERY_NAMES)}",
            "cookie_present": f"{self.cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "template_path": self.template_path,
            "template_source": {
                "account_kind": self.template_account_kind,
                "account_id": self.template_account_id,
            },
            "steps": self.steps[-8:],
            "login_bundle": self.bundle.public_summary() if self.bundle else None,
            "secretOutput": "NONE",
        }


def capture_exact_login_template(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    account_kind: str = "account",
    account_id: int = 0,
    fgs_id: str = "",
) -> ExactTemplateCaptureResult:
    start = time.monotonic()
    email = str(email or "").strip()
    if not email or not password:
        return ExactTemplateCaptureResult(False, "PRECHECK", "EMPTY_CREDENTIAL", "email/password is empty")

    try:
        login_url, query, cookie_values, missing_query, missing_cookie, query_present, cookie_present = _prepare_context(cfg, fgs_id=fgs_id)
    except Exception as exc:
        return ExactTemplateCaptureResult(False, "CONTEXT", "WEB_CONTEXT_BUILD_FAILED", f"{type(exc).__name__}: {exc}")

    _emit(event_cb, "HTTP EXACT CAPTURE WEB CONTEXT " f"query_present={query_present}/{len(LOGIN_QUERY_NAMES)} cookie_present={cookie_present}/{len(LOGIN_COOKIE_NAMES)} secretOutput=NONE")
    if missing_query or missing_cookie:
        return ExactTemplateCaptureResult(False, "CONTEXT", "WEB_CONTEXT_INCOMPLETE", "missing web context values", query_present=query_present, cookie_present=cookie_present)

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ExactTemplateCaptureResult(False, "BROWSER", "PLAYWRIGHT_MISSING", "Run: python -m pip install -r requirements.txt && python -m playwright install chromium", query_present=query_present, cookie_present=cookie_present)

    v2 = cfg.raw.get("v2", {})
    timeout_s = int(v2.get("browser_timeout_seconds") or 120)
    auto_submit = _bool(v2.get("browser_auto_submit"), True)
    headless = _bool(v2.get("browser_headless"), False)
    channel = str(v2.get("browser_channel") or "chrome").strip()

    holder: dict[str, Any] = {
        "payload": None,
        "check_user_token": "",
        "check_request": None,
        "login_request": None,
    }
    captured_requests: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = None
        launch_errors: list[str] = []
        for launch_args in (
            {"headless": headless, "channel": channel} if channel else {"headless": headless},
            {"headless": headless},
        ):
            try:
                browser = p.chromium.launch(**launch_args)
                break
            except Exception as exc:
                launch_errors.append(type(exc).__name__)
        if browser is None:
            return ExactTemplateCaptureResult(False, "BROWSER", "BROWSER_LAUNCH_FAILED", ",".join(launch_errors), query_present=query_present, cookie_present=cookie_present)
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 12; SM-A156E Build/SP1A.210812.016; wv) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                    "Chrome/120.0.0.0 Mobile Safari/537.36"
                ),
                locale=str(cfg.devplay.get("locale") or "en-US"),
            )
            parsed_login = urlsplit(login_url)
            cookie_origin = f"{parsed_login.scheme}://{parsed_login.netloc}"
            context.add_cookies([
                {"name": name, "value": cookie_values[name], "url": cookie_origin}
                for name in LOGIN_COOKIE_NAMES
                if cookie_values.get(name)
            ])

            page = context.new_page()
            page.set_default_timeout(15_000)

            def on_request(request):
                try:
                    if not _host_allowed(request.url):
                        return
                    path = urlsplit(request.url).path
                    method = request.method.upper()
                    if method != "POST" or path not in {"/v4/checkemail", "/v3/login/devsisters"}:
                        return
                    headers = request.headers or {}
                    raw = request.post_data or ""
                    obj = _post_json(raw)
                    item = {
                        "method": method,
                        "url": _safe_path(request.url),
                        "path": path,
                        "header_names": sorted(str(k).lower() for k in headers.keys())[:80],
                        "cookie_names": _cookie_names_from_header(str(headers.get("cookie") or "")),
                        "post_len": len(raw),
                        "post_fp": _sha12(raw),
                        "post_keys": sorted(str(k) for k in obj.keys())[:80] if isinstance(obj, dict) else [],
                    }
                    captured_requests.append(item)
                    _emit(event_cb, f"HTTP EXACT CAPTURE REQUEST {method} {_safe_path(request.url)} post_keys={','.join(item['post_keys'])} secretOutput=NONE")

                    record = {
                        "url": _safe_path(request.url),
                        "method": method,
                        "headers_template": _redact_header_template(headers),
                        "content_type": str(headers.get("content-type") or "application/json").split(";", 1)[0].lower(),
                        "raw_body_template": "",
                        "body_template": None,
                        "send_mode": "raw",
                        "post_keys": item["post_keys"],
                    }
                    if isinstance(obj, dict):
                        templ_obj = _replace_secrets_in_obj(obj, email=email, password=password, user_token=holder.get("check_user_token") or "")
                        templ_raw = json.dumps(templ_obj, ensure_ascii=False, separators=(",", ":"))
                        record["body_template"] = templ_obj
                        record["raw_body_template"] = templ_raw
                    else:
                        record["raw_body_template"] = _replace_secrets_in_raw(raw, email=email, password=password, user_token=holder.get("check_user_token") or "")
                    if path == "/v4/checkemail":
                        holder["check_request"] = record
                    elif path == "/v3/login/devsisters":
                        # user_token can be learned by checkemail response after request creation.
                        # Re-sanitize once below before saving.
                        holder["login_request"] = record
                except Exception:
                    pass

            def on_response(response):
                try:
                    if not _host_allowed(response.url):
                        return
                    ctype = str(response.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        return
                    obj = response.json()
                    path = urlsplit(response.url).path
                    if path == "/v4/checkemail":
                        token = _extract_user_token(obj)
                        if token:
                            holder["check_user_token"] = token
                    hit = find_login_payload(obj)
                    if hit is not None:
                        holder["payload"] = hit
                except Exception:
                    pass

            def on_nav(frame):
                if frame == page.main_frame:
                    hit = _candidate_from_url(frame.url)
                    if hit is not None:
                        holder["payload"] = hit

            page.on("request", on_request)
            page.on("response", on_response)
            page.on("framenavigated", on_nav)

            _emit(event_cb, f"HTTP EXACT CAPTURE OPEN {safe_url(login_url)} browser=YES secretOutput=NONE")
            page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
            _emit(event_cb, f"HTTP EXACT CAPTURE PAGE {safe_url(page.url)} secretOutput=NONE")

            email_selectors = [
                'input[type="email"]',
                'input[name="email"]',
                'input[name*="email" i]',
                'input[autocomplete="email"]',
                'input[placeholder*="email" i]',
                'input[placeholder*="อีเมล" i]',
            ]
            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[name*="password" i]',
                'input[autocomplete="current-password"]',
                'input[placeholder*="password" i]',
                'input[placeholder*="รหัส" i]',
            ]
            deadline = time.monotonic() + timeout_s
            email_done = False
            password_done = False
            submit_count = 0
            while time.monotonic() < deadline and holder["payload"] is None:
                if not email_done:
                    loc = _find_visible(page, email_selectors)
                    if loc is not None:
                        loc.fill(email)
                        email_done = True
                        _emit(event_cb, "HTTP EXACT CAPTURE EMAIL FILLED secretOutput=NONE")
                if email_done and not password_done:
                    loc = _find_visible(page, password_selectors)
                    if loc is not None:
                        loc.fill(password)
                        password_done = True
                        _emit(event_cb, "HTTP EXACT CAPTURE PASSWORD FILLED secretOutput=NONE")
                    elif auto_submit and submit_count == 0:
                        btn = _find_visible(page, ['button[type="submit"]', 'input[type="submit"]', 'button'])
                        if btn is not None:
                            btn.click()
                            submit_count += 1
                            _emit(event_cb, "HTTP EXACT CAPTURE CONTINUE secretOutput=NONE")
                if password_done and auto_submit and submit_count < 2:
                    btn = _find_visible(page, ['button[type="submit"]', 'input[type="submit"]', 'button'])
                    if btn is not None:
                        btn.click()
                        submit_count += 1
                        _emit(event_cb, "HTTP EXACT CAPTURE SUBMITTED secretOutput=NONE")
                if holder["payload"] is None:
                    try:
                        storage_values = page.evaluate(
                            """() => {
                              const out=[];
                              for (const s of [localStorage, sessionStorage]) {
                                for (let i=0;i<s.length;i++) out.push(s.getItem(s.key(i)) || '');
                              }
                              return out;
                            }"""
                        )
                        for value in storage_values or []:
                            hit = find_login_payload(value)
                            if hit is not None:
                                holder["payload"] = hit
                                break
                    except Exception:
                        pass
                if holder["payload"] is None:
                    page.wait_for_timeout(250)

            if holder["payload"] is None:
                return ExactTemplateCaptureResult(False, "CAPTURE", "LOGIN_PAYLOAD_NOT_FOUND", "Browser login completed no template payload was captured", query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, captured_requests=captured_requests)

            try:
                bundle = LoginBundle.from_payload(holder["payload"])
            except Exception as exc:
                return ExactTemplateCaptureResult(False, "CAPTURE", "LOGIN_PAYLOAD_INVALID", f"{type(exc).__name__}: {exc}", query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, captured_requests=captured_requests)

            login_req = holder.get("login_request")
            check_req = holder.get("check_request")
            if not isinstance(login_req, dict):
                return ExactTemplateCaptureResult(False, "TEMPLATE", "LOGIN_REQUEST_NOT_CAPTURED", "Browser login passed but /v3/login/devsisters request was not captured", bundle=bundle, query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, captured_requests=captured_requests)

            # Re-sanitize login request now that the checkemail result token is known.
            token = holder.get("check_user_token") or ""
            if isinstance(login_req.get("body_template"), dict):
                login_req["body_template"] = _replace_secrets_in_obj(login_req["body_template"], email=email, password=password, user_token=token)
                login_req["raw_body_template"] = json.dumps(login_req["body_template"], ensure_ascii=False, separators=(",", ":"))
            else:
                login_req["raw_body_template"] = _replace_secrets_in_raw(str(login_req.get("raw_body_template") or ""), email=email, password=password, user_token=token)

            if isinstance(check_req, dict) and isinstance(check_req.get("body_template"), dict):
                check_req["body_template"] = _replace_secrets_in_obj(check_req["body_template"], email=email, password=password, user_token=token)
                check_req["raw_body_template"] = json.dumps(check_req["body_template"], ensure_ascii=False, separators=(",", ":"))

            template = {
                "schema": "MWOIF_HTTP_LOGIN_EXACT_TEMPLATE_V1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "account_kind": account_kind,
                "account_id": account_id,
                "source": "browser-network-exact",
                "safe_login_url": safe_url(login_url),
                "checkemail": check_req,
                "login": login_req,
                "notes": {
                    "private_file": True,
                    "secret_placeholders_only": True,
                    "do_not_send_to_chat": True,
                },
                "secretOutput": "NONE",
            }
            path = _template_path(cfg, account_kind, int(account_id or 0))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
            _emit(event_cb, f"HTTP EXACT TEMPLATE SAVED {path} secretOutput=NONE")

            summary = _summarize_template_body(login_req.get("body_template"), str(login_req.get("raw_body_template") or ""))
            _emit(event_cb, "HTTP EXACT CAPTURE SESSION CAPTURED template=PRIVATE_FILE_ONLY secretOutput=NONE")
            return ExactTemplateCaptureResult(True, "CAPTURE", "EXACT_TEMPLATE_CAPTURED", "Browser request template captured to private local file", bundle=bundle, template_path=str(path), elapsed_ms=(time.monotonic() - start) * 1000, query_present=query_present, cookie_present=cookie_present, captured_requests=captured_requests, template_summary=summary)
        finally:
            browser.close()


def replay_exact_login_template(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    account_kind: str = "account",
    account_id: int = 0,
    template_account_kind: str | None = None,
    template_account_id: int | None = None,
    fgs_id: str = "",
) -> ExactTemplateReplayResult:
    start = time.monotonic()
    email = str(email or "").strip()
    if not email or not password:
        return ExactTemplateReplayResult(False, "PRECHECK", "EMPTY_CREDENTIAL", "email/password is empty")

    try:
        login_url, query, cookie_values, missing_query, missing_cookie, query_present, cookie_present = _prepare_context(cfg, fgs_id=fgs_id)
    except Exception as exc:
        return ExactTemplateReplayResult(False, "CONTEXT", "WEB_CONTEXT_BUILD_FAILED", f"{type(exc).__name__}: {exc}")

    source_kind = str(template_account_kind or account_kind or "account")
    source_id = int(template_account_id if template_account_id is not None else (account_id or 0))
    path = _template_path(cfg, source_kind, source_id)
    if not path.is_file():
        return ExactTemplateReplayResult(False, "TEMPLATE", "EXACT_TEMPLATE_MISSING", f"run http-login-template-capture-{source_kind} first", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present)
    if missing_query or missing_cookie:
        return ExactTemplateReplayResult(False, "CONTEXT", "WEB_CONTEXT_INCOMPLETE", "missing web context values", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present)

    try:
        template = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return ExactTemplateReplayResult(False, "TEMPLATE", "EXACT_TEMPLATE_READ_FAILED", f"{type(exc).__name__}: {exc}", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present)
    if not isinstance(template, dict) or template.get("schema") != "MWOIF_HTTP_LOGIN_EXACT_TEMPLATE_V1":
        return ExactTemplateReplayResult(False, "TEMPLATE", "EXACT_TEMPLATE_SCHEMA_INVALID", "template schema mismatch", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present)

    v2 = cfg.raw.get("v2", {})
    timeout_s = int(v2.get("http_login_timeout_seconds") or 35)
    warmup = _bool(v2.get("http_login_warmup_get"), True)
    locale = str(cfg.devplay.get("locale") or query.get("lc.locale_on_game") or "en-US")
    steps: list[dict[str, Any]] = []

    session = requests.Session()
    for host in ("app.devplay.com", "account.devplay.com"):
        for name in LOGIN_COOKIE_NAMES:
            value = cookie_values.get(name)
            if value:
                session.cookies.set(name, value, domain=host, path="/")

    try:
        if warmup:
            resp = session.get(login_url, headers={"referer": login_url}, timeout=timeout_s, allow_redirects=True)
            steps.append({"step": "warmup-login-try", "method": "GET", "url": safe_url(resp.url), "http_status": resp.status_code, "content_type": str(resp.headers.get("content-type") or "").split(";", 1)[0].lower()})
            _emit(event_cb, f"HTTP EXACT REPLAY WARMUP status={resp.status_code} url={safe_url(resp.url)} secretOutput=NONE")

        check_req = template.get("checkemail") if isinstance(template.get("checkemail"), dict) else {}
        login_req = template.get("login") if isinstance(template.get("login"), dict) else {}
        if not isinstance(login_req, dict):
            return ExactTemplateReplayResult(False, "TEMPLATE", "EXACT_TEMPLATE_LOGIN_MISSING", "template has no login request", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, steps=steps)

        check_url = str((check_req or {}).get("url") or "https://account.devplay.com/v4/checkemail")
        login_endpoint_url = str(login_req.get("url") or "https://account.devplay.com/v3/login/devsisters")
        if not _host_allowed(check_url) or not _host_allowed(login_endpoint_url):
            return ExactTemplateReplayResult(False, "TEMPLATE", "EXACT_TEMPLATE_HOST_BLOCKED", "template endpoint host is not allowed", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, steps=steps)

        check_headers = _apply_template_headers((check_req or {}).get("headers_template") or {}, login_url=login_url, cookies=cookie_values, locale=locale)
        login_headers = _apply_template_headers(login_req.get("headers_template") or {}, login_url=login_url, cookies=cookie_values, locale=locale)

        check_body_template = (check_req or {}).get("body_template")
        if check_body_template is None:
            check_body_template = {"email": "{{EMAIL}}", "lc": {name: query.get(name) or "" for name in LOGIN_QUERY_NAMES if name.startswith("lc.")}}
        check_body = _apply_template_value(check_body_template, email=email, password=password, user_token="", query=query, cookies=cookie_values)
        _emit(event_cb, f"HTTP EXACT REPLAY POST {safe_url(check_url)} step=checkemail secretOutput=NONE")
        check_resp = session.post(check_url, data=json.dumps(check_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), headers=check_headers, timeout=timeout_s, allow_redirects=True)
        check_json: Any = None
        try:
            check_json = check_resp.json()
        except Exception:
            check_json = None
        user_token = _extract_user_token(check_json)
        steps.append({"step": "checkemail", "method": "POST", "url": safe_url(check_url), "http_status": check_resp.status_code, "json": _public_json(check_json), "user_token_present": bool(user_token), "user_token_len": len(user_token) if user_token else 0})

        if check_resp.status_code >= 400:
            return ExactTemplateReplayResult(False, "CHECKEMAIL", "CHECKEMAIL_HTTP_FAILED", f"http_status={check_resp.status_code}", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, steps=steps)

        login_body_template = login_req.get("body_template")
        raw_template = str(login_req.get("raw_body_template") or "")
        _emit(event_cb, f"HTTP EXACT REPLAY POST {safe_url(login_endpoint_url)} step=login-devsisters user_token={'Y' if user_token else 'N'} secretOutput=NONE")
        if isinstance(login_body_template, dict):
            login_body = _apply_template_value(login_body_template, email=email, password=password, user_token=user_token, query=query, cookies=cookie_values)
            login_raw = json.dumps(login_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        elif raw_template:
            text = raw_template
            text = text.replace("{{EMAIL}}", email).replace("{{PASSWORD}}", password).replace("{{USER_TOKEN}}", user_token)
            text = text.replace("{{QUERY:push_token}}", query.get("push_token") or "")
            text = text.replace("{{QUERY:recall_session_id}}", query.get("recall_session_id") or "")
            text = text.replace("{{COOKIE:oven_access_token}}", cookie_values.get("oven_access_token") or "")
            login_raw = text.encode("utf-8")
        else:
            return ExactTemplateReplayResult(False, "TEMPLATE", "EXACT_TEMPLATE_BODY_MISSING", "template has no login body", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, steps=steps)

        login_resp = session.post(login_endpoint_url, data=login_raw, headers=login_headers, timeout=timeout_s, allow_redirects=True)
        login_json: Any = None
        try:
            login_json = login_resp.json()
        except Exception:
            login_json = None
        hit = find_login_payload(login_json)
        steps.append({"step": "login-devsisters", "method": "POST", "url": safe_url(login_endpoint_url), "http_status": login_resp.status_code, "json": _public_json(login_json), "contains_login_payload": hit is not None})
        if hit is None:
            return ExactTemplateReplayResult(False, "LOGIN", "LOGIN_PAYLOAD_NOT_FOUND", "Exact template replay finished but ServerLoginResponse was not found", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, steps=steps)

        bundle = LoginBundle.from_payload(hit)
        _emit(event_cb, "HTTP EXACT REPLAY LOGIN SESSION CAPTURED (secrets redacted)")
        return ExactTemplateReplayResult(True, "CAPTURE", "EXACT_REPLAY_LOGIN_CAPTURED", "ServerLoginResponse captured by exact-template HTTP replay", bundle=bundle, template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, steps=steps)
    except requests.RequestException as exc:
        return ExactTemplateReplayResult(False, "NETWORK", "EXACT_REPLAY_REQUEST_FAILED", f"{type(exc).__name__}: {exc}", template_path=str(path), template_account_kind=source_kind, template_account_id=source_id, query_present=query_present, cookie_present=cookie_present, elapsed_ms=(time.monotonic() - start) * 1000, steps=steps)
