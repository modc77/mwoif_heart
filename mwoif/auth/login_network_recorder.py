from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from .devplay_login import (
    LOGIN_COOKIE_NAMES,
    LOGIN_QUERY_NAMES,
    _build_login_web_context,
    _candidate_from_url,
    _find_visible,
    safe_url,
)
from .models import LoginBundle, find_login_payload

Event = Callable[[str], None]

_ALLOWED_HOST_SUFFIXES = (
    "devplay.com",
    "devsisters.cloud",
)
_SECRET_HINTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "cookie",
    "authorization",
    "auth",
    "code",
    "state",
    "session",
    "credential",
    "access",
    "refresh",
    "oven",
)
_INTERESTING_HINTS = (
    "login",
    "auth",
    "email",
    "password",
    "account",
    "token",
    "session",
    "serverloginresponse",
    "loginresult",
    "devplay",
)


def _emit(cb: Event | None, text: str) -> None:
    if cb:
        cb(text)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_path(url: str) -> str:
    try:
        p = urlsplit(str(url or ""))
        return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except Exception:
        return "<invalid-url>"


def _host_allowed(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def _header_names(headers: dict[str, Any] | None) -> list[str]:
    if not isinstance(headers, dict):
        return []
    out: list[str] = []
    for name in headers.keys():
        lname = str(name).lower()
        if lname not in out:
            out.append(lname)
    return sorted(out)[:80]


def _redact_value_kind(name: str, value: str) -> str:
    lname = str(name or "").lower()
    text = "" if value is None else str(value)
    if any(h in lname for h in _SECRET_HINTS):
        return f"<REDACTED len={len(text)}>"
    if "email" in lname or "mail" in lname:
        return "<EMAIL>" if text else ""
    if text == "":
        return ""
    if len(text) <= 12:
        return "<VALUE>"
    return f"<VALUE len={len(text)}>"


def _content_type_base(value: Any) -> str:
    return str(value or "").split(";", 1)[0].lower().strip()


def _summarize_post_data(raw: str | None, content_type: str) -> dict[str, Any]:
    text = raw or ""
    ctype = _content_type_base(content_type)
    out: dict[str, Any] = {
        "present": bool(text),
        "len": len(text),
        "fp": hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:12] if text else None,
        "content_type": ctype,
        "keys": [],
        "fields_redacted": {},
    }
    if not text:
        return out

    parsed: dict[str, str] = {}
    if "json" in ctype:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    parsed[str(k)] = "" if v is None else str(v)
        except Exception:
            pass
    elif "x-www-form-urlencoded" in ctype or "form" in ctype:
        try:
            values = parse_qs(text, keep_blank_values=True)
            for k, items in values.items():
                parsed[str(k)] = "" if not items else str(items[0])
        except Exception:
            pass

    if parsed:
        keys = sorted(parsed.keys())[:80]
        out["keys"] = keys
        out["fields_redacted"] = {k: _redact_value_kind(k, parsed.get(k, "")) for k in keys[:80]}
    return out


def _json_public_keys(obj: Any, *, limit: int = 80) -> dict[str, Any]:
    if isinstance(obj, dict):
        keys = sorted([str(k) for k in obj.keys()])[:limit]
        return {"type": "dict", "keys": keys, "key_count": len(obj)}
    if isinstance(obj, list):
        return {"type": "list", "count": len(obj)}
    return {"type": type(obj).__name__}


def _scan_text_for_endpoint_hints(text: str, *, source: str, limit: int = 60) -> list[dict[str, Any]]:
    if not text:
        return []
    blob = text[:2_000_000]
    decoded: list[str] = [blob]
    try:
        decoded.append(unquote(blob))
    except Exception:
        pass

    # Collect strings that look like endpoint paths or full HTTPS URLs. Keep only
    # path-level information; never retain query/fragment values.
    pattern = re.compile(r"(?P<q>[\"'`])(?P<u>(?:https://[^\"'`\\\s]+|/[A-Za-z0-9_./%:-]{2,220}))(?P=q)")
    hints: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant in decoded:
        low = variant.lower()
        for m in pattern.finditer(variant):
            raw = m.group("u")
            path_only = _safe_path(raw) if raw.startswith("https://") else raw.split("?", 1)[0].split("#", 1)[0]
            key = f"{source}:{path_only}"
            if key in seen:
                continue
            window = low[max(0, m.start() - 160): min(len(low), m.end() + 160)]
            matched = sorted({hint for hint in _INTERESTING_HINTS if hint in window})
            if not matched:
                continue
            if raw.startswith("https://") and not _host_allowed(raw):
                continue
            seen.add(key)
            hints.append({
                "source": source,
                "path": path_only,
                "keywords_nearby": matched[:12],
            })
            if len(hints) >= limit:
                return hints
    return hints


def _fingerprint_url(url: str) -> str:
    return hashlib.sha256(str(url or "").encode("utf-8", "ignore")).hexdigest()[:12]


@dataclass(slots=True)
class LoginNetworkRecordResult:
    ok: bool
    stage: str
    code: str
    message: str
    bundle: LoginBundle | None = None
    elapsed_ms: float = 0.0
    query_present: int = 0
    cookie_present: int = 0
    report_path: str | None = None
    dom_signals: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    endpoint_hints: list[dict[str, Any]] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": "browser-network-recorder",
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
            "query_present": f"{self.query_present}/{len(LOGIN_QUERY_NAMES)}",
            "cookie_present": f"{self.cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "dom_signals": self.dom_signals,
            "event_count": len(self.events),
            "events_tail": self.events[-16:],
            "endpoint_hints": self.endpoint_hints[:40],
            "report_path": self.report_path,
            "login_bundle": self.bundle.public_summary() if self.bundle else None,
            "secretOutput": "NONE",
        }


def record_login_network_flow(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    account_kind: str = "account",
    account_id: int | None = None,
    fgs_id: str = "",
) -> LoginNetworkRecordResult:
    """Run the known-working browser login once and record safe network metadata.

    This Phase 4.5.1 probe exists to discover the real JS/API flow for a later
    direct-HTTP replay. It intentionally records only methods, safe paths,
    header names, POST field names with redacted value summaries, response JSON
    key names, and endpoint hints. Raw password, cookies, tokens, query strings,
    auth codes, session keys, and response bodies are never written.
    """
    start = time.monotonic()
    email = str(email or "").strip()
    if not email or not password:
        return LoginNetworkRecordResult(False, "PRECHECK", "EMPTY_CREDENTIAL", "email/password is empty")

    login_url, cookie_values, missing_query, missing_cookie = _build_login_web_context(cfg, fgs_id=fgs_id)
    try:
        values = parse_qs(urlsplit(login_url).query, keep_blank_values=True)
        query_present = sum(1 for name in LOGIN_QUERY_NAMES if (values.get(name) or [""])[0])
    except Exception:
        query_present = len(LOGIN_QUERY_NAMES) - len(missing_query)
    cookie_present = sum(1 for name in LOGIN_COOKIE_NAMES if cookie_values.get(name))

    _emit(
        event_cb,
        "NETWORK RECORD WEB CONTEXT "
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
        return LoginNetworkRecordResult(
            False,
            "CONTEXT",
            "WEB_CONTEXT_INCOMPLETE",
            "; ".join(parts),
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return LoginNetworkRecordResult(
            False,
            "BROWSER",
            "PLAYWRIGHT_MISSING",
            "Run: python -m pip install -r requirements.txt && python -m playwright install chromium",
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    v2 = cfg.raw.get("v2", {})
    timeout_s = int(v2.get("browser_timeout_seconds") or 120)
    headless = _bool(v2.get("browser_headless"), False)
    channel = str(v2.get("browser_channel") or "chrome").strip()
    auto_submit = _bool(v2.get("browser_auto_submit"), True)
    save_report = _bool(v2.get("network_record_save"), True)
    max_events = max(20, int(v2.get("network_record_max_events") or 120))
    scan_scripts = _bool(v2.get("network_record_scan_scripts"), True)

    events: list[dict[str, Any]] = []
    endpoint_hints: list[dict[str, Any]] = []
    holder: dict[str, Any] = {"payload": None, "last_path": "", "report_path": None}
    request_meta: dict[str, dict[str, Any]] = {}

    def add_event(item: dict[str, Any]) -> None:
        item = dict(item)
        item["n"] = len(events) + 1
        events.append(item)
        if len(events) > max_events:
            del events[: len(events) - max_events]

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
            return LoginNetworkRecordResult(
                False,
                "BROWSER",
                "BROWSER_LAUNCH_FAILED",
                "cannot launch Chrome/Chromium: " + ",".join(launch_errors),
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

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
                    url = request.url
                    if not _host_allowed(url):
                        return
                    headers = request.headers or {}
                    ctype = str(headers.get("content-type") or headers.get("Content-Type") or "")
                    post_summary = _summarize_post_data(request.post_data, ctype)
                    rid = _fingerprint_url(url + "|" + request.method + "|" + str(len(events)))
                    meta = {
                        "event": "request",
                        "id": rid,
                        "method": request.method,
                        "url": _safe_path(url),
                        "resource_type": request.resource_type,
                        "header_names": _header_names(headers),
                        "post": post_summary,
                    }
                    request_meta[rid] = {"url": url, "method": request.method}
                    add_event(meta)
                    if post_summary.get("present"):
                        _emit(event_cb, f"NETWORK REQUEST {request.method} {_safe_path(url)} post_keys={','.join(post_summary.get('keys') or []) or '-'} secretOutput=NONE")
                except Exception:
                    pass

            def on_response(response):
                try:
                    url = response.url
                    if not _host_allowed(url):
                        return
                    headers = response.headers or {}
                    ctype = str(headers.get("content-type") or "")
                    item: dict[str, Any] = {
                        "event": "response",
                        "status": response.status,
                        "url": _safe_path(url),
                        "content_type": _content_type_base(ctype),
                        "set_cookie_names": sorted([
                            part.split("=", 1)[0].strip()
                            for part in str(headers.get("set-cookie") or "").split(";")
                            if part.strip() and "=" in part and not part.strip().startswith(("Path", "Domain", "Expires", "Max-Age"))
                        ])[:20],
                    }
                    if "json" in ctype:
                        try:
                            obj = response.json()
                            item["json"] = _json_public_keys(obj)
                            hit = find_login_payload(obj)
                            if hit is not None:
                                holder["payload"] = hit
                                item["contains_login_payload"] = True
                        except Exception:
                            item["json"] = {"type": "unreadable"}
                    add_event(item)
                except Exception:
                    pass

            def on_nav(frame):
                if frame == page.main_frame:
                    holder["last_path"] = safe_url(frame.url)
                    hit = _candidate_from_url(frame.url)
                    if hit is not None:
                        holder["payload"] = hit

            page.on("request", on_request)
            page.on("response", on_response)
            page.on("framenavigated", on_nav)

            _emit(event_cb, f"NETWORK RECORD OPEN {safe_url(login_url)} context=LAB_V2 secretOutput=NONE")
            page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
            _emit(event_cb, f"NETWORK RECORD PAGE {safe_url(page.url)} secretOutput=NONE")

            dom_signals: dict[str, Any] = {}
            try:
                dom_signals = page.evaluate(
                    """() => {
                      const clean = (v) => (v || '').toString().slice(0, 80);
                      const inputs = Array.from(document.querySelectorAll('input')).slice(0, 30).map((el, i) => ({
                        index: i,
                        type: clean(el.getAttribute('type') || ''),
                        name_present: !!el.getAttribute('name'),
                        id_present: !!el.getAttribute('id'),
                        autocomplete: clean(el.getAttribute('autocomplete') || ''),
                        placeholder: clean(el.getAttribute('placeholder') || ''),
                        required: !!el.required,
                        class_fp: clean(el.className || '').length ? String(clean(el.className || '').length) : ''
                      }));
                      return {
                        title: clean(document.title || ''),
                        input_count: inputs.length,
                        inputs,
                        button_count: document.querySelectorAll('button,input[type="submit"]').length,
                        form_count: document.querySelectorAll('form').length,
                        script_count: document.scripts.length,
                      };
                    }"""
                ) or {}
            except Exception:
                dom_signals = {}

            if scan_scripts:
                try:
                    scripts = page.evaluate(
                        """() => Array.from(document.scripts)
                          .map(s => s.src || '')
                          .filter(Boolean)
                          .slice(0, 80)"""
                    ) or []
                    for src in scripts[:40]:
                        if not isinstance(src, str) or not _host_allowed(src):
                            continue
                        if "/_next/" not in src and "static" not in src:
                            continue
                        try:
                            resp = context.request.get(src, timeout=15_000)
                            if resp.status != 200:
                                continue
                            text = resp.text()
                            for hint in _scan_text_for_endpoint_hints(text, source=_safe_path(src), limit=25):
                                key = hint.get("path")
                                if key and all(old.get("path") != key for old in endpoint_hints):
                                    endpoint_hints.append(hint)
                                    if len(endpoint_hints) >= 80:
                                        break
                            if len(endpoint_hints) >= 80:
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

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
                        _emit(event_cb, "NETWORK RECORD EMAIL FILLED secretOutput=NONE")

                if email_done and not password_done:
                    loc = _find_visible(page, password_selectors)
                    if loc is not None:
                        loc.fill(password)
                        password_done = True
                        _emit(event_cb, "NETWORK RECORD PASSWORD FILLED secretOutput=NONE")
                    elif auto_submit and submit_count == 0:
                        btn = _find_visible(page, ['button[type="submit"]', 'input[type="submit"]', 'button'])
                        if btn is not None:
                            btn.click()
                            submit_count += 1
                            _emit(event_cb, "NETWORK RECORD CONTINUE secretOutput=NONE")

                if password_done and auto_submit and submit_count < 2:
                    btn = _find_visible(page, ['button[type="submit"]', 'input[type="submit"]', 'button'])
                    if btn is not None:
                        btn.click()
                        submit_count += 1
                        _emit(event_cb, "NETWORK RECORD SUBMITTED secretOutput=NONE")

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

            bundle: LoginBundle | None = None
            ok = False
            code = "LOGIN_PAYLOAD_NOT_FOUND"
            message = "Browser network flow recorded, but ServerLoginResponse was not captured."
            if holder["payload"] is not None:
                try:
                    bundle = LoginBundle.from_payload(holder["payload"])
                    ok = True
                    code = "NETWORK_LOGIN_CAPTURED"
                    message = "ServerLoginResponse captured while recording safe network metadata."
                    _emit(event_cb, "NETWORK RECORD SESSION CAPTURED (secrets redacted)")
                except Exception as exc:
                    code = "LOGIN_PAYLOAD_INVALID"
                    message = f"{type(exc).__name__}: {exc}"

            elapsed_ms = (time.monotonic() - start) * 1000

            # Save safe local report for the next direct-HTTP replay patch.
            report_path_str: str | None = None
            if save_report:
                try:
                    report_dir = cfg.root / "logs"
                    report_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    suffix = f"{account_kind}_{account_id or 'x'}"
                    report_path = report_dir / f"http_login_network_record_{suffix}_{stamp}.json"
                    report = {
                        "schema": "MWOIF_HTTP_LOGIN_NETWORK_RECORD_V1",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "account_kind": account_kind,
                        "account_id": account_id,
                        "safe_login_url": safe_url(login_url),
                        "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
                        "query_present": f"{query_present}/{len(LOGIN_QUERY_NAMES)}",
                        "cookie_present": f"{cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
                        "dom_signals": dom_signals,
                        "events": events,
                        "endpoint_hints": endpoint_hints,
                        "login_bundle_summary": bundle.public_summary() if bundle else None,
                        "secretOutput": "NONE",
                    }
                    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    report_path_str = str(report_path)
                    _emit(event_cb, f"NETWORK RECORD REPORT {report_path_str} secretOutput=NONE")
                except Exception as exc:
                    add_event({"event": "report_save_failed", "error": type(exc).__name__})

            return LoginNetworkRecordResult(
                ok=ok,
                stage="CAPTURE" if ok else "RECORD",
                code=code,
                message=message,
                bundle=bundle,
                elapsed_ms=elapsed_ms,
                query_present=query_present,
                cookie_present=cookie_present,
                report_path=report_path_str,
                dom_signals=dom_signals,
                events=events,
                endpoint_hints=endpoint_hints,
            )
        finally:
            browser.close()
