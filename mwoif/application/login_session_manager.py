from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from mwoif.auth.config_adapter import build_devplay_runtime_config
from mwoif.auth.devplay_login import login_with_email_password
from mwoif.core.config import AppConfig
from mwoif.core.redact import mask_email
from mwoif.domain.errors import AuthError, ConfigError, CredentialError, SessionError
from mwoif.session.game_session import SessionBootstrapError, bootstrap_session
from mwoif.session.models import AuthRecord, SessionRecord
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault, VaultBlob

Event = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class LoginSessionResult:
    account_kind: str
    account_id: int
    email_mask: str | None
    auth: AuthRecord
    session: SessionRecord

    def public_dict(self) -> dict[str, Any]:
        return {
            "account_kind": self.account_kind,
            "account_id": self.account_id,
            "email_mask": self.email_mask,
            "auth": self.auth.public_summary(),
            "session": self.session.public_summary(),
            "secretOutput": "NONE",
        }


class LoginSessionManager:
    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.runtime_cfg = build_devplay_runtime_config(config)

    def _emit(self, cb: Event | None, text: str) -> None:
        if cb:
            cb(text)

    def _decrypt_credential(self, blob: VaultBlob | None, *, aad: str) -> tuple[str, str]:
        if blob is None:
            raise CredentialError("credential vault row not found", stage="VAULT_READ")
        data = self.vault.decrypt_json(blob, aad=aad)
        email = str(data.get("email") or "").strip()
        password = str(data.get("password") or "")
        if not email or not password:
            raise CredentialError("credential payload missing email/password", stage="VAULT_READ")
        return email, password

    def _load_private_query(self) -> dict[str, str]:
        raw_path = self.runtime_cfg.raw.get("v2", {}).get("login_web_context", {}).get("private_context_file")
        path = self.runtime_cfg.root / str(raw_path or "state/login_web_context.private.json")
        out: dict[str, str] = {}
        if not path.is_file():
            return out
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return out
        if not isinstance(obj, dict):
            return out
        for key in ("url", "login_url", "raw_url"):
            url = str(obj.get(key) or "")
            if not url:
                continue
            try:
                values = parse_qs(urlsplit(url).query, keep_blank_values=True)
                for name, items in values.items():
                    if items:
                        out[str(name)] = str(items[0])
            except Exception:
                pass
        raw_query = obj.get("query")
        if isinstance(raw_query, dict):
            for k, v in raw_query.items():
                out[str(k)] = "" if v is None else str(v)
        return out

    def _make_auth_record(self, *, kind: str, account_id: int, bundle) -> AuthRecord:
        q = self._load_private_query()
        imported_at = datetime.now(timezone.utc).isoformat()
        fgs_id = q.get("lc.fgs_id") or q.get("lc.new_fgs_id") or ""
        device_id = q.get("device_id") or self.runtime_cfg.devplay.get("device_id") or ""
        metadata = {
            "login_platform": "email",
            "fgs-id": fgs_id,
            "fgs_id": fgs_id,
            "device-id": str(device_id),
            "device_id": str(device_id),
            "timezone": q.get("timezone") or q.get("lc.timezone") or str(self.runtime_cfg.devplay.get("timezone") or ""),
            "country": q.get("country_code") or q.get("lc.location_country") or str(self.runtime_cfg.devplay.get("location_country") or ""),
            "version": q.get("lc.app_version") or str(self.runtime_cfg.game.get("version") or ""),
            "version-code": q.get("lc.app_build") or str(self.runtime_cfg.game.get("build_version") or ""),
            "os_version": q.get("lc.os_version") or str(self.runtime_cfg.devplay.get("os_version") or ""),
            "market_type": q.get("lc.store") or str(self.runtime_cfg.devplay.get("market_type") or ""),
            "locale": q.get("lc.locale_on_game") or str(self.runtime_cfg.devplay.get("locale") or ""),
            "device-name": q.get("lc.device.model") or str(self.runtime_cfg.devplay.get("device_name") or ""),
            "device-model": q.get("lc.device.model") or str(self.runtime_cfg.devplay.get("device_model") or ""),
            "semiDeviceId": q.get("lc.semi_device_id") or "",
            "pushToken": q.get("push_token") or "",
            "game-process-elapsed-ms": "0",
            "time-zone-distance": str(self.runtime_cfg.devplay.get("time_zone_distance") or "25200"),
        }
        metadata = {k: v for k, v in metadata.items() if v not in (None, "")}
        return AuthRecord.from_login_bundle(
            account_kind=kind,
            account_id=account_id,
            bundle=bundle,
            fgs_id=fgs_id,
            device_id=str(device_id),
            metadata=metadata,
            imported_at=imported_at,
        )

    def login_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> LoginSessionResult:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="LOGIN_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="LOGIN_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._login(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def login_sender(self, *, hs_id: int, event_cb: Event | None = None) -> LoginSessionResult:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="LOGIN_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._login(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def _login(self, *, kind: str, account_id: int, email: str, password: str, event_cb: Event | None) -> LoginSessionResult:
        self._emit(event_cb, f"V3 LOGIN START kind={kind} id={account_id} email={mask_email(email)} secretOutput=NONE")
        try:
            bundle = login_with_email_password(self.runtime_cfg, email, password, event_cb=event_cb)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if kind == "receiver":
                self.repo.update_receiver_runtime_error(hr_id=account_id, stage="LOGIN", code="LOGIN_FAILED", message=msg)
                self.repo.log_event(event_type="RECEIVER_LOGIN_FAILED", hr_id=account_id, success=False, error_scope="receiver", error_code="LOGIN_FAILED", detail=msg)
            else:
                self.repo.update_sender_runtime_error(hs_id=account_id, stage="LOGIN", code="LOGIN_FAILED", message=msg)
                self.repo.log_event(event_type="SENDER_LOGIN_FAILED", hs_id=account_id, success=False, error_scope="sender", error_code="LOGIN_FAILED", detail=msg)
            raise AuthError(msg, stage="LOGIN") from exc

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if kind == "receiver":
                self.repo.update_receiver_runtime_error(hr_id=account_id, stage="INIT_MEMBER", code="SESSION_INIT_FAILED", message=msg)
                self.repo.log_event(event_type="RECEIVER_SESSION_FAILED", hr_id=account_id, success=False, error_scope="receiver", error_code="SESSION_INIT_FAILED", detail=msg)
            else:
                self.repo.update_sender_runtime_error(hs_id=account_id, stage="INIT_MEMBER", code="SESSION_INIT_FAILED", message=msg)
                self.repo.log_event(event_type="SENDER_SESSION_FAILED", hs_id=account_id, success=False, error_scope="sender", error_code="SESSION_INIT_FAILED", detail=msg)
            raise SessionError(msg, stage="INIT_MEMBER") from exc

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="RECEIVER_LOGIN_SESSION_OK", hr_id=account_id, success=True, detail="receiver auth/session ready")
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="SENDER_LOGIN_SESSION_OK", hs_id=account_id, success=True, detail="sender auth/session ready")
        self._emit(event_cb, f"V3 LOGIN SESSION OK kind={kind} id={account_id} memberSeq=present lv={session.current_lv} secretOutput=NONE")
        return LoginSessionResult(kind, account_id, mask_email(email), auth, session)
