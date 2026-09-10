from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

from .models import LoginBundle, find_login_payload


def safe_url(url: str) -> str:
    """Return only scheme/host/path; never expose query/cookie/token values."""
    try:
        p = urlsplit(str(url or ""))
        return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except Exception:
        return "<invalid-url>"

Event = Callable[[str], None]


class LoginBootstrapError(RuntimeError):
    pass


# LAB V2 confirmed these exact names from
# AuthManager.getAuthWebViewModel -> AuthWebViewModel.getUrl(Login).
LOGIN_QUERY_NAMES: tuple[str, ...] = (
    "agree_ad_day_push",
    "agree_ad_night_push",
    "agree_dt",
    "country_code",
    "device_id",
    "device_type",
    "email_address",
    "fallback_country_code",
    "lang",
    "lc.anonymous_id",
    "lc.app_build",
    "lc.app_installed_id",
    "lc.app_version",
    "lc.device.manufacturer",
    "lc.device.model",
    "lc.device.traits",
    "lc.device.version",
    "lc.devsisters_id",
    "lc.fgs_id",
    "lc.library_name",
    "lc.library_version",
    "lc.locale_on_game",
    "lc.location_country",
    "lc.new_fgs_id",
    "lc.os_name",
    "lc.os_version",
    "lc.platform",
    "lc.semi_device_id",
    "lc.store",
    "lc.timezone",
    "push_token",
    "recall_session_id",
    "terms_updates_ids",
    "terms_updates_values",
    "timezone",
    "use_popup_v2",
    "use_terms_v2",
)

# LAB V2 confirmed the custom header contains these required Cookie names.
# After a normal in-game login the same header may also contain oven_access_token.
# The token cookie is imported and forwarded when present, but it is not required
# for opening the login surface and is never logged.
LOGIN_REQUIRED_COOKIE_NAMES: tuple[str, ...] = (
    "api_key",
    "bundle_id",
    "lang",
    "platform",
    "sdk",
    "sdk_version",
)
LOGIN_OPTIONAL_COOKIE_NAMES: tuple[str, ...] = (
    "oven_access_token",
)
LOGIN_COOKIE_NAMES: tuple[str, ...] = LOGIN_REQUIRED_COOKIE_NAMES + LOGIN_OPTIONAL_COOKIE_NAMES

# LAB captured these names as present=false in normal game-owned contexts.
# They are allowed to remain blank without classifying the context as incomplete.
LAB_CONFIRMED_EMPTY_QUERY_NAMES = frozenset({
    "email_address",
    "lc.device.traits",
    "recall_session_id",
    # LAB has shown two valid game-owned login URL shapes:
    #   - pre-login context: 36 query names, no lc.new_fgs_id
    #   - post-login/session context: 37 query names, includes lc.new_fgs_id
    # The adapter keeps the 37-name schema compatible but must not block the
    # first login bootstrap when the private export was captured from the
    # 36-name pre-login context.
    "lc.new_fgs_id",
})


def _emit(cb: Event | None, text: str) -> None:
    if cb:
        cb(text)


def _candidate_from_url(url: str) -> dict[str, Any] | None:
    try:
        p = urlsplit(url)
        values: list[str] = []
        for mapping in (parse_qs(p.query), parse_qs(p.fragment)):
            for items in mapping.values():
                values.extend(items)
        for value in values:
            for candidate in (value, unquote(value)):
                text = candidate.strip()
                if text.startswith("{") or text.startswith("["):
                    hit = find_login_payload(json.loads(text))
                    if hit:
                        return hit
    except Exception:
        pass
    return None


def _find_visible(page, selectors: list[str]):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _first(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _parse_cookie_header(value: Any) -> dict[str, str]:
    text = _text(value)
    if not text:
        return {}
    out: dict[str, str] = {}
    for part in text.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, raw_value = item.split("=", 1)
        name = name.strip()
        if name in LOGIN_COOKIE_NAMES:
            out[name] = raw_value.strip()
    return out


def _context_path(cfg, value: Any) -> Path | None:
    text = _text(value)
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = cfg.root / path
    return path


def _load_private_web_context(cfg) -> tuple[dict[str, str], dict[str, str]]:
    """Load a real LAB WebView context from local private storage only.

    This intentionally never prints the raw URL, raw Cookie header, token, or
    password. Accepted shapes are deliberately loose so LAB output can be copied
    locally without changing Python source:

      {"url":"https://app.devplay.com/auth/v2/login-try?...", "headers":{"Cookie":"..."}}
      {"login_url":"...", "cookie_header":"..."}
      {"query":{...}, "cookies":{...}}

    The default file is v2/state/login_web_context.private.json. Keep that file
    local only; do not paste it into chat or commit it.
    """
    v2 = _dict(cfg.raw.get("v2"))
    web = _dict(v2.get("login_web_context"))

    payloads: list[dict[str, Any]] = []
    env_json = _env("MWOIF_DEVPLAY_WEB_CONTEXT_JSON")
    if env_json:
        try:
            obj = json.loads(env_json)
            if isinstance(obj, dict):
                payloads.append(obj)
        except Exception:
            pass

    candidate_files: list[Path] = []
    for raw_path in (
        _env("MWOIF_DEVPLAY_WEB_CONTEXT_FILE"),
        web.get("private_context_file"),
        "state/login_web_context.private.json",
    ):
        path = _context_path(cfg, raw_path)
        if path and path not in candidate_files:
            candidate_files.append(path)

    for path in candidate_files:
        if not path.exists() or not path.is_file():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                payloads.append(obj)
        except Exception:
            pass

    query: dict[str, str] = {}
    cookies: dict[str, str] = {}
    for obj in payloads:
        for key in ("url", "login_url", "raw_url"):
            url = _text(obj.get(key))
            if not url:
                continue
            try:
                parsed = urlsplit(url)
                host = (parsed.hostname or "").lower()
                if host and host != "app.devplay.com":
                    continue
                values = parse_qs(parsed.query, keep_blank_values=True)
                for name in LOGIN_QUERY_NAMES:
                    if name in values and values[name]:
                        query[name] = _text(values[name][0])
            except Exception:
                pass

        raw_query = _dict(obj.get("query"))
        for name in LOGIN_QUERY_NAMES:
            if name in raw_query:
                query[name] = _text(raw_query.get(name))

        headers = _dict(obj.get("headers")) or _dict(obj.get("customHeader")) or _dict(obj.get("custom_header"))
        cookie_header = _first(
            obj.get("cookie_header"),
            obj.get("Cookie"),
            headers.get("Cookie"),
            headers.get("cookie"),
        )
        cookies.update(_parse_cookie_header(cookie_header))

        raw_cookies = _dict(obj.get("cookies"))
        for name in LOGIN_COOKIE_NAMES:
            if name in raw_cookies:
                cookies[name] = _text(raw_cookies.get(name))

    return query, cookies


def _lang_from_locale(locale: str) -> str:
    value = str(locale or "").strip()
    if not value:
        return ""
    return value.replace("_", "-").split("-", 1)[0].lower()


def _build_login_web_context(cfg, *, fgs_id: str = "") -> tuple[str, dict[str, str], list[str], list[str]]:
    """Build only the WebView context supported by LAB/config/runtime evidence.

    No secret/fingerprint substitutes are generated here. Values absent from the
    current Python runtime remain blank. The caller refuses to open the page when
    a LAB-confirmed-present query/cookie value is still missing.
    """
    v2 = _dict(cfg.raw.get("v2"))
    web = _dict(v2.get("login_web_context"))
    query_override = _dict(web.get("query"))
    cookie_override = _dict(web.get("cookies"))
    private_query, private_cookies = _load_private_web_context(cfg)
    dev = cfg.devplay
    game = cfg.game

    login_url = _first(
        v2.get("devplay_login_url"),
        "https://app.devplay.com/auth/v2/login-try",
    )
    locale = _first(dev.get("locale"))
    lang = _first(dev.get("lang"), _lang_from_locale(locale))
    country = _first(dev.get("location_country"))
    timezone_name = _first(dev.get("timezone"))
    os_type = _first(dev.get("os_type"))
    os_name = _first(dev.get("os_name"))
    if not os_name and os_type.upper() == "A":
        # Explicit derivation from the existing project convention os_type=A.
        os_name = "Android"

    defaults: dict[str, Any] = {
        "agree_ad_day_push": dev.get("agree_ad_day_push"),
        "agree_ad_night_push": dev.get("agree_ad_night_push"),
        "agree_dt": dev.get("agree_dt"),
        "country_code": country,
        "device_id": dev.get("device_id"),
        "device_type": dev.get("device_type"),
        # LAB captured this as present=false. Email is entered into the page UI.
        "email_address": "",
        "fallback_country_code": _first(dev.get("fallback_country_code"), country),
        "lang": lang,
        "lc.anonymous_id": dev.get("anonymous_id"),
        "lc.app_build": game.get("build_version"),
        "lc.app_installed_id": dev.get("app_installed_id"),
        "lc.app_version": game.get("version"),
        "lc.device.manufacturer": dev.get("device_manufacturer"),
        "lc.device.model": _first(dev.get("device_model"), dev.get("device_name")),
        # LAB captured this as present=false.
        "lc.device.traits": "",
        "lc.device.version": dev.get("device_version"),
        "lc.devsisters_id": dev.get("devsisters_id"),
        "lc.fgs_id": _first(fgs_id, dev.get("fgs_id")),
        "lc.library_name": dev.get("library_name"),
        "lc.library_version": dev.get("library_version"),
        "lc.locale_on_game": locale,
        "lc.location_country": country,
        "lc.new_fgs_id": _first(dev.get("new_fgs_id"), dev.get("lc.new_fgs_id")),
        "lc.os_name": os_name,
        "lc.os_version": dev.get("os_version"),
        "lc.platform": _first(
            dev.get("platform"),
            dev.get("cookie_platform"),
            private_cookies.get("platform"),
        ),
        "lc.semi_device_id": dev.get("semi_device_id"),
        "lc.store": dev.get("market_type"),
        "lc.timezone": timezone_name,
        "push_token": dev.get("push_token"),
        # LAB captured this as present=false.
        "recall_session_id": "",
        "terms_updates_ids": dev.get("terms_updates_ids"),
        "terms_updates_values": dev.get("terms_updates_values"),
        "timezone": timezone_name,
        "use_popup_v2": dev.get("use_popup_v2"),
        "use_terms_v2": dev.get("use_terms_v2"),
    }

    query_pairs: list[tuple[str, str]] = []
    query_values: dict[str, str] = {}
    for name in LOGIN_QUERY_NAMES:
        # Explicit config override wins; otherwise prefer the real local LAB dump
        # before falling back to static config/runtime defaults.
        if name in query_override:
            value = _text(query_override[name])
        else:
            value = _first(private_query.get(name), defaults.get(name))
        query_values[name] = value
        query_pairs.append((name, value))

    p = urlsplit(login_url)
    # The LAB route is a generated game-owned URL; do not retain an old/ad-hoc
    # query from config. Rebuild the exact verified query schema from inputs.
    full_url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(query_pairs), p.fragment))

    package_name = _first(
        cookie_override.get("bundle_id"),
        _env("MWOIF_DEVPLAY_BUNDLE_ID"),
        private_cookies.get("bundle_id"),
        game.get("package_name"),
        dev.get("bundle_id"),
    )
    cookie_values = {
        "api_key": _first(
            cookie_override.get("api_key"),
            _env("MWOIF_DEVPLAY_API_KEY"),
            private_cookies.get("api_key"),
            dev.get("api_key"),
        ),
        "bundle_id": package_name,
        "lang": _first(
            cookie_override.get("lang"),
            _env("MWOIF_DEVPLAY_LANG"),
            private_cookies.get("lang"),
            lang,
        ),
        "platform": _first(
            cookie_override.get("platform"),
            _env("MWOIF_DEVPLAY_PLATFORM"),
            private_cookies.get("platform"),
            dev.get("cookie_platform"),
        ),
        "sdk": _first(
            cookie_override.get("sdk"),
            _env("MWOIF_DEVPLAY_SDK"),
            private_cookies.get("sdk"),
            dev.get("sdk"),
            # Ghidra 5.5: string 0x0078A930 is referenced from DevPlaySDK
            # initialize (FUN_0176be4c). This is not a secret.
            "DevPlay Cocos SDK",
        ),
        "sdk_version": _first(
            cookie_override.get("sdk_version"),
            _env("MWOIF_DEVPLAY_SDK_VERSION"),
            private_cookies.get("sdk_version"),
            dev.get("sdk_version"),
        ),
        "oven_access_token": _first(
            cookie_override.get("oven_access_token"),
            _env("MWOIF_DEVPLAY_OVEN_ACCESS_TOKEN"),
            private_cookies.get("oven_access_token"),
        ),
    }

    missing_query = [
        name for name in LOGIN_QUERY_NAMES
        if name not in LAB_CONFIRMED_EMPTY_QUERY_NAMES and not query_values.get(name)
    ]
    # Only the original six LAB Cookie values are mandatory. Any later runtime
    # cookies, such as oven_access_token, are forwarded when present but do not
    # block login bootstrap.
    missing_cookie = [name for name in LOGIN_REQUIRED_COOKIE_NAMES if not cookie_values.get(name)]
    return full_url, cookie_values, missing_query, missing_cookie


def login_with_email_password(
    cfg,
    email: str,
    password: str,
    event_cb: Event | None = None,
    *,
    fgs_id: str = "",
) -> LoginBundle:
    """Login through the same DevPlay web surface used by the Android SDK.

    LAB confirms that the game opens /auth/v2/login-try with a 36-name query
    schema plus a Cookie custom header. This adapter rebuilds that context only
    from verified config/runtime values, injects the cookies before navigation,
    captures ServerLoginResponse in memory, and never logs raw URL/cookie/token.
    """
    email = str(email or "").strip()
    if not email or not password:
        raise LoginBootstrapError("email/password is empty")

    login_url, cookie_values, missing_query, missing_cookie = _build_login_web_context(
        cfg, fgs_id=fgs_id
    )
    query_present = 0
    cookie_present = 0
    try:
        parsed_for_count = urlsplit(login_url)
        query_for_count = parse_qs(parsed_for_count.query, keep_blank_values=True)
        query_present = sum(
            1 for name in LOGIN_QUERY_NAMES
            if (query_for_count.get(name) or [""])[0]
        )
    except Exception:
        query_present = len(LOGIN_QUERY_NAMES) - len(missing_query)
    cookie_present = sum(
        1 for name in LOGIN_COOKIE_NAMES
        if cookie_values.get(name)
    )
    _emit(
        event_cb,
        "LOGIN WEB CONTEXT "
        f"query_schema={len(LOGIN_QUERY_NAMES)}/{len(LOGIN_QUERY_NAMES)} "
        f"query_present={query_present}/{len(LOGIN_QUERY_NAMES)} "
        f"cookie_present={cookie_present}/{len(LOGIN_COOKIE_NAMES)} "
        "secretOutput=NONE",
    )

    # Do not send a knowingly incomplete/fabricated game context. This converts
    # the old opaque UNKNOWN(40001) into a deterministic local status and keeps
    # the missing names visible without exposing any values.
    if missing_query or missing_cookie:
        parts: list[str] = []
        if missing_query:
            parts.append("missing_query=" + ",".join(missing_query))
        if missing_cookie:
            parts.append("missing_cookie=" + ",".join(missing_cookie))
        raise LoginBootstrapError(
            "DevPlay LAB web context is incomplete; " + "; ".join(parts) + "; secretOutput=NONE"
        )

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise LoginBootstrapError(
            "Playwright is missing. Run: python -m pip install -r requirements.txt && python -m playwright install chromium"
        ) from exc

    v2 = cfg.raw.get("v2", {})
    timeout_s = int(v2.get("browser_timeout_seconds") or 120)
    auto_submit = bool(v2.get("browser_auto_submit", True))
    headless = bool(v2.get("browser_headless", False))
    channel = str(v2.get("browser_channel") or "chrome").strip()

    holder: dict[str, Any] = {"payload": None, "last_path": ""}

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
            raise LoginBootstrapError("cannot launch Chrome/Chromium: " + ",".join(launch_errors))

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

            def on_response(response):
                try:
                    host = (urlsplit(response.url).hostname or "").lower()
                    if not (host.endswith("devplay.com") or host.endswith("devsisters.cloud")):
                        return
                    ctype = str(response.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        return
                    obj = response.json()
                    hit = find_login_payload(obj)
                    if hit is not None:
                        holder["payload"] = hit
                except Exception:
                    pass

            def on_nav(frame):
                if frame == page.main_frame:
                    holder["last_path"] = safe_url(frame.url)
                    hit = _candidate_from_url(frame.url)
                    if hit is not None:
                        holder["payload"] = hit

            page.on("response", on_response)
            page.on("framenavigated", on_nav)

            _emit(event_cb, f"LOGIN OPEN {safe_url(login_url)} context=LAB_V2")
            page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
            _emit(event_cb, f"LOGIN PAGE {safe_url(page.url)}")

            email_selectors = [
                'input[type="email"]', 'input[name="email"]',
                'input[name*="email" i]', 'input[autocomplete="email"]',
                'input[placeholder*="email" i]',
            ]
            password_selectors = [
                'input[type="password"]', 'input[name="password"]',
                'input[name*="password" i]', 'input[autocomplete="current-password"]',
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
                        _emit(event_cb, "LOGIN EMAIL FILLED")

                if email_done and not password_done:
                    loc = _find_visible(page, password_selectors)
                    if loc is not None:
                        loc.fill(password)
                        password_done = True
                        _emit(event_cb, "LOGIN PASSWORD FILLED")
                    elif auto_submit and submit_count == 0:
                        btn = _find_visible(page, ['button[type="submit"]', 'input[type="submit"]'])
                        if btn is not None:
                            btn.click()
                            submit_count += 1
                            _emit(event_cb, "LOGIN CONTINUE")

                if password_done and auto_submit and submit_count < 2:
                    btn = _find_visible(page, ['button[type="submit"]', 'input[type="submit"]'])
                    if btn is not None:
                        btn.click()
                        submit_count += 1
                        _emit(event_cb, "LOGIN SUBMITTED")

                # Some DevPlay responses are persisted to web storage before the JS bridge call.
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
                path = holder.get("last_path") or safe_url(page.url)
                raise LoginBootstrapError(
                    "DevPlay login did not expose ServerLoginResponse within timeout; "
                    f"last_page={path}. If the page asks for terms/OTP, complete it in the visible browser."
                )

            bundle = LoginBundle.from_payload(holder["payload"])
            _emit(event_cb, "LOGIN SESSION CAPTURED (secrets redacted)")
            return bundle
        finally:
            browser.close()
