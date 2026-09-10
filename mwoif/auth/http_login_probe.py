from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests

from .devplay_login import (
    LOGIN_COOKIE_NAMES,
    LOGIN_QUERY_NAMES,
    _build_login_web_context,
    _candidate_from_url,
    safe_url,
)
from .models import LoginBundle, find_login_payload

Event = Callable[[str], None]


_ALLOWED_LOGIN_HOST_SUFFIXES = (
    "devplay.com",
    "devsisters.cloud",
)
_SECRET_FIELD_HINTS = (
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
)
_EMAIL_FIELD_HINTS = ("email", "mail", "account", "id")
_PASSWORD_FIELD_HINTS = ("password", "passwd", "pwd")
_PAYLOAD_MARKERS = (
    "gameAccessToken",
    "game_access_token",
    "refreshToken",
    "refresh_token",
    "ovenAccessToken",
    "oven_access_token",
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
    return str(value)


def _redact_name_value(name: str, value: str) -> str:
    lname = str(name or "").lower()
    if any(h in lname for h in _SECRET_FIELD_HINTS):
        return f"<REDACTED len={len(str(value or ''))}>"
    if "email" in lname or "mail" in lname:
        return "<EMAIL>" if value else ""
    if value and len(value) > 64:
        return f"<VALUE len={len(value)}>"
    return "<VALUE>" if value else ""


def _is_allowed_host(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_LOGIN_HOST_SUFFIXES)


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def _field_name(attrs: dict[str, str]) -> str:
    return str(attrs.get("name") or attrs.get("id") or "").strip()


def _field_type(attrs: dict[str, str]) -> str:
    return str(attrs.get("type") or "text").strip().lower()


def _looks_email_field(attrs: dict[str, str]) -> bool:
    name = _field_name(attrs).lower()
    field_type = _field_type(attrs)
    autocomplete = str(attrs.get("autocomplete") or "").lower()
    placeholder = str(attrs.get("placeholder") or "").lower()
    return (
        field_type == "email"
        or autocomplete == "email"
        or any(h in name for h in _EMAIL_FIELD_HINTS)
        or "email" in placeholder
        or "mail" in placeholder
    )


def _looks_password_field(attrs: dict[str, str]) -> bool:
    name = _field_name(attrs).lower()
    field_type = _field_type(attrs)
    autocomplete = str(attrs.get("autocomplete") or "").lower()
    placeholder = str(attrs.get("placeholder") or "").lower()
    return (
        field_type == "password"
        or "current-password" in autocomplete
        or any(h in name for h in _PASSWORD_FIELD_HINTS)
        or "password" in placeholder
    )


@dataclass(slots=True)
class HtmlInput:
    name: str
    type: str = "text"
    value: str = ""
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def disabled(self) -> bool:
        return "disabled" in self.attrs

    @property
    def email(self) -> bool:
        return _looks_email_field(self.attrs | {"name": self.name, "type": self.type})

    @property
    def password(self) -> bool:
        return _looks_password_field(self.attrs | {"name": self.name, "type": self.type})


@dataclass(slots=True)
class HtmlForm:
    method: str = "GET"
    action: str = ""
    inputs: list[HtmlInput] = field(default_factory=list)

    @property
    def has_email(self) -> bool:
        return any(item.email for item in self.inputs)

    @property
    def has_password(self) -> bool:
        return any(item.password for item in self.inputs)

    def absolute_action(self, current_url: str) -> str:
        return urljoin(current_url, self.action or current_url)

    def public_summary(self, current_url: str, *, index: int) -> dict[str, Any]:
        names: list[str] = []
        hidden_names: list[str] = []
        for item in self.inputs:
            if not item.name:
                continue
            if item.name not in names:
                names.append(item.name)
            if item.type == "hidden" and item.name not in hidden_names:
                hidden_names.append(item.name)
        return {
            "index": index,
            "method": (self.method or "GET").upper(),
            "action": safe_url(self.absolute_action(current_url)),
            "input_count": len(self.inputs),
            "input_names": names[:40],
            "hidden_names": hidden_names[:40],
            "has_email": self.has_email,
            "has_password": self.has_password,
        }


class _LoginHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[HtmlForm] = []
        self._current: HtmlForm | None = None
        self.global_inputs: list[HtmlInput] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(k).lower(): "" if v is None else str(v) for k, v in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        data = self._attrs(attrs)
        if tag == "form":
            form = HtmlForm(method=str(data.get("method") or "GET").upper(), action=str(data.get("action") or ""))
            self.forms.append(form)
            self._current = form
            return
        if tag == "input":
            name = _field_name(data)
            item = HtmlInput(name=name, type=_field_type(data), value=str(data.get("value") or ""), attrs=data)
            if self._current is not None:
                self._current.inputs.append(item)
            else:
                self.global_inputs.append(item)
            return
        if tag in {"button", "select", "textarea"}:
            name = _field_name(data)
            if not name:
                return
            item = HtmlInput(name=name, type=tag, value=str(data.get("value") or ""), attrs=data)
            if self._current is not None:
                self._current.inputs.append(item)
            else:
                self.global_inputs.append(item)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._current = None


def _parse_forms(text: str) -> list[HtmlForm]:
    parser = _LoginHtmlParser()
    try:
        parser.feed(text or "")
    except Exception:
        pass
    forms = list(parser.forms)
    if not forms and parser.global_inputs:
        # Several modern pages render inputs outside a classic <form>.  This is
        # only a fallback candidate; it still submits to the current page and is
        # reported clearly as a synthesized form.
        forms.append(HtmlForm(method="POST", action="", inputs=parser.global_inputs))
    return forms


def _pick_next_form(forms: list[HtmlForm], *, email_done: bool, password_done: bool) -> tuple[int, HtmlForm] | None:
    if not email_done:
        for i, form in enumerate(forms):
            if form.has_email and form.has_password:
                return i, form
        for i, form in enumerate(forms):
            if form.has_email:
                return i, form
    if email_done and not password_done:
        for i, form in enumerate(forms):
            if form.has_password:
                return i, form
    return None


def _build_form_payload(form: HtmlForm, *, email: str, password: str) -> tuple[dict[str, str], bool, bool]:
    payload: dict[str, str] = {}
    filled_email = False
    filled_password = False
    for item in form.inputs:
        if not item.name or item.disabled:
            continue
        ftype = (item.type or "text").lower()
        if ftype in {"submit", "button", "image", "reset", "file"}:
            continue
        if item.email:
            payload[item.name] = email
            filled_email = True
        elif item.password:
            payload[item.name] = password
            filled_password = True
        elif ftype in {"checkbox", "radio"}:
            if "checked" in item.attrs:
                payload[item.name] = item.value or "on"
        else:
            payload[item.name] = item.value or ""
    return payload, filled_email, filled_password


def _status_record(response: requests.Response) -> dict[str, Any]:
    history = [safe_url(item.url) for item in response.history[-5:]]
    return {
        "status": response.status_code,
        "url": safe_url(response.url),
        "content_type": str(response.headers.get("content-type") or "").split(";", 1)[0].lower(),
        "redirect_count": len(response.history),
        "redirects": history,
        "set_cookie_names": sorted(response.cookies.keys()),
    }


def _extract_json_candidates_near_markers(text: str) -> list[str]:
    if not text:
        return []
    decoded_variants = []
    base = str(text)
    decoded_variants.append(base)
    try:
        decoded_variants.append(html.unescape(base))
    except Exception:
        pass
    try:
        decoded_variants.append(unquote(base))
    except Exception:
        pass
    try:
        decoded_variants.append(unquote(html.unescape(base)))
    except Exception:
        pass

    candidates: list[str] = []
    for blob in decoded_variants:
        if not any(marker in blob for marker in _PAYLOAD_MARKERS):
            continue
        # Keep the scanner bounded.  DevPlay login pages are small enough for
        # this probe; very large HTML/JS bundles are a strong signal that this
        # path needs browser/JS execution.
        if len(blob) > 750_000:
            blob = blob[:750_000]
        for marker in _PAYLOAD_MARKERS:
            start_at = 0
            while True:
                idx = blob.find(marker, start_at)
                if idx < 0:
                    break
                left = blob.rfind("{", 0, idx)
                while left >= 0:
                    depth = 0
                    in_str = False
                    esc = False
                    for pos in range(left, min(len(blob), left + 100_000)):
                        ch = blob[pos]
                        if in_str:
                            if esc:
                                esc = False
                            elif ch == "\\":
                                esc = True
                            elif ch == '"':
                                in_str = False
                            continue
                        if ch == '"':
                            in_str = True
                        elif ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                candidates.append(blob[left : pos + 1])
                                break
                    break
                start_at = idx + len(marker)
    # Deduplicate but preserve order.
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:20]


def _find_payload_in_response(response: requests.Response, body_text: str, forms: list[HtmlForm]) -> dict[str, Any] | None:
    # Redirect/callback URL can carry a JSON-encoded ServerLoginResponse.
    hit = _candidate_from_url(response.url)
    if hit is not None:
        return hit
    for old in response.history:
        hit = _candidate_from_url(old.url)
        if hit is not None:
            return hit

    ctype = str(response.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            hit = find_login_payload(response.json())
            if hit is not None:
                return hit
        except Exception:
            pass

    hit = find_login_payload(body_text)
    if hit is not None:
        return hit

    # Hidden input values or inline JS can contain an escaped JSON payload.
    for form in forms:
        for item in form.inputs:
            hit = find_login_payload(item.value)
            if hit is not None:
                return hit
            for candidate in _extract_json_candidates_near_markers(item.value):
                hit = find_login_payload(candidate)
                if hit is not None:
                    return hit
    for candidate in _extract_json_candidates_near_markers(body_text):
        hit = find_login_payload(candidate)
        if hit is not None:
            return hit
    return None


@dataclass(slots=True)
class HttpLoginProbeResult:
    ok: bool
    stage: str
    code: str
    message: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    bundle: LoginBundle | None = None
    query_present: int = 0
    cookie_present: int = 0
    elapsed_ms: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "provider": "direct-http",
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "query_schema": f"{len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)}",
            "query_present": f"{self.query_present}/{len(LOGIN_QUERY_NAMES)}",
            "cookie_present": f"{self.cookie_present}/{len(LOGIN_COOKIE_NAMES)}",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "steps": self.steps[-12:],
            "login_bundle": self.bundle.public_summary() if self.bundle else None,
            "secretOutput": "NONE",
        }
        return out


def login_with_email_password_http_probe(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    fgs_id: str = "",
) -> HttpLoginProbeResult:
    """Attempt DevPlay Auth V2 login with plain HTTP requests only.

    This is an isolated Phase 4.5 probe.  It intentionally does not try to
    bypass JavaScript, captcha, OTP, or device challenges.  If the web surface
    requires browser execution, the probe fails with NEEDS_BROWSER and leaves
    the existing BrowserLoginProvider as the working path.
    """
    start = time.monotonic()
    email = str(email or "").strip()
    if not email or not password:
        return HttpLoginProbeResult(False, "PRECHECK", "EMPTY_CREDENTIAL", "email/password is empty")

    login_url, cookie_values, missing_query, missing_cookie = _build_login_web_context(cfg, fgs_id=fgs_id)
    try:
        parsed_for_count = urlsplit(login_url)
        query_for_count = parse_qs(parsed_for_count.query, keep_blank_values=True)
        query_present = sum(1 for name in LOGIN_QUERY_NAMES if (query_for_count.get(name) or [""])[0])
    except Exception:
        query_present = len(LOGIN_QUERY_NAMES) - len(missing_query)
    cookie_present = sum(1 for name in LOGIN_COOKIE_NAMES if cookie_values.get(name))

    _emit(
        event_cb,
        "HTTP LOGIN WEB CONTEXT "
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
        return HttpLoginProbeResult(
            False,
            "CONTEXT",
            "WEB_CONTEXT_INCOMPLETE",
            "; ".join(parts),
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    if not _is_allowed_host(login_url):
        return HttpLoginProbeResult(
            False,
            "CONTEXT",
            "LOGIN_HOST_NOT_ALLOWED",
            f"login_url={safe_url(login_url)}",
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    v2 = cfg.raw.get("v2", {})
    timeout_s = int(v2.get("http_login_timeout_seconds") or 35)
    max_steps = int(v2.get("http_login_max_steps") or 8)
    auto_submit = bool(v2.get("http_login_auto_submit", True))
    locale = str(cfg.devplay.get("locale") or "en-US")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; SM-A156E Build/SP1A.210812.016; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": locale.replace("_", "-") + ",en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )

    parsed_login = urlsplit(login_url)
    cookie_host = parsed_login.hostname or "app.devplay.com"
    for name in LOGIN_COOKIE_NAMES:
        value = cookie_values.get(name)
        if value:
            session.cookies.set(name, value, domain=cookie_host, path="/")

    steps: list[dict[str, Any]] = []
    email_done = False
    password_done = False
    current_url = login_url
    response: requests.Response | None = None

    _emit(event_cb, f"HTTP LOGIN OPEN {safe_url(login_url)} context=LAB_V2 secretOutput=NONE")

    try:
        response = session.get(current_url, timeout=timeout_s, allow_redirects=True)
    except Exception as exc:
        return HttpLoginProbeResult(
            False,
            "OPEN",
            "HTTP_OPEN_FAILED",
            f"{type(exc).__name__}: {exc}",
            steps=steps,
            query_present=query_present,
            cookie_present=cookie_present,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    for step_index in range(max_steps):
        current_url = response.url
        try:
            body = response.text or ""
        except Exception:
            body = ""
        forms = _parse_forms(body)
        status = _status_record(response)
        status["step"] = step_index
        status["form_count"] = len(forms)
        status["forms"] = [form.public_summary(current_url, index=i) for i, form in enumerate(forms[:8])]
        steps.append(status)
        _emit(
            event_cb,
            "HTTP LOGIN STEP "
            f"{step_index} status={response.status_code} url={safe_url(response.url)} "
            f"forms={len(forms)} secretOutput=NONE",
        )

        hit = _find_payload_in_response(response, body, forms)
        if hit is not None:
            try:
                bundle = LoginBundle.from_payload(hit)
            except Exception as exc:
                return HttpLoginProbeResult(
                    False,
                    "CAPTURE",
                    "LOGIN_PAYLOAD_INVALID",
                    f"{type(exc).__name__}: {exc}",
                    steps=steps,
                    query_present=query_present,
                    cookie_present=cookie_present,
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )
            _emit(event_cb, "HTTP LOGIN SESSION CAPTURED (secrets redacted)")
            return HttpLoginProbeResult(
                True,
                "CAPTURE",
                "HTTP_LOGIN_CAPTURED",
                "ServerLoginResponse captured through direct HTTP",
                steps=steps,
                bundle=bundle,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        if not auto_submit:
            return HttpLoginProbeResult(
                False,
                "FORM",
                "HTTP_LOGIN_AUTOSUBMIT_DISABLED",
                "response inspected without submitting forms",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        pick = _pick_next_form(forms, email_done=email_done, password_done=password_done)
        if pick is None:
            return HttpLoginProbeResult(
                False,
                "FORM",
                "NEEDS_BROWSER",
                "No classic HTML email/password form was available to submit. The page likely requires JavaScript/WebView execution.",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        form_index, form = pick
        action_url = form.absolute_action(current_url)
        if not _is_allowed_host(action_url):
            return HttpLoginProbeResult(
                False,
                "FORM",
                "FORM_HOST_NOT_ALLOWED",
                f"form_action={safe_url(action_url)}",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        payload, filled_email, filled_password = _build_form_payload(form, email=email, password=password)
        if not filled_email and not email_done:
            return HttpLoginProbeResult(
                False,
                "FORM",
                "EMAIL_FIELD_NOT_FOUND",
                "selected form did not expose a usable email field",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        if email_done and not filled_password and not password_done:
            return HttpLoginProbeResult(
                False,
                "FORM",
                "PASSWORD_FIELD_NOT_FOUND",
                "selected form did not expose a usable password field",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        fill_flags: list[str] = []
        if filled_email:
            fill_flags.append("email")
            email_done = True
        if filled_password:
            fill_flags.append("password")
            password_done = True

        public_payload = {name: _redact_name_value(name, value) for name, value in payload.items()}
        steps[-1]["submit"] = {
            "form_index": form_index,
            "method": (form.method or "GET").upper(),
            "action": safe_url(action_url),
            "fill": fill_flags,
            "field_count": len(payload),
            "fields_redacted": public_payload,
        }
        _emit(
            event_cb,
            "HTTP LOGIN SUBMIT "
            f"step={step_index} form={form_index} method={(form.method or 'GET').upper()} "
            f"action={safe_url(action_url)} fill={'+'.join(fill_flags) or 'none'} secretOutput=NONE",
        )

        headers = {"Referer": current_url, "Origin": _origin(current_url)}
        try:
            if (form.method or "GET").upper() == "GET":
                response = session.get(action_url, params=payload, headers=headers, timeout=timeout_s, allow_redirects=True)
            else:
                response = session.post(action_url, data=payload, headers=headers, timeout=timeout_s, allow_redirects=True)
        except Exception as exc:
            return HttpLoginProbeResult(
                False,
                "SUBMIT",
                "HTTP_SUBMIT_FAILED",
                f"{type(exc).__name__}: {exc}",
                steps=steps,
                query_present=query_present,
                cookie_present=cookie_present,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

    return HttpLoginProbeResult(
        False,
        "CAPTURE",
        "LOGIN_PAYLOAD_NOT_FOUND",
        "Direct HTTP flow completed but ServerLoginResponse was not found. Keep browser provider as fallback.",
        steps=steps,
        query_present=query_present,
        cookie_present=cookie_present,
        elapsed_ms=(time.monotonic() - start) * 1000,
    )
