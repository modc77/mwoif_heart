from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from mwoif.auth.config_adapter import build_devplay_runtime_config
from mwoif.auth.devplay_login import login_with_email_password
from mwoif.auth.http_login_probe import HttpLoginProbeResult, login_with_email_password_http_probe
from mwoif.auth.http_login_replay import DirectHttpMatrixResult, DirectHttpReplayResult, login_with_email_password_http_replay_matrix, login_with_email_password_http_replay_v2
from mwoif.auth.login_network_recorder import LoginNetworkRecordResult, record_login_network_flow
from mwoif.auth.http_login_template import ExactTemplateCaptureResult, ExactTemplateReplayResult, capture_exact_login_template, replay_exact_login_template
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




    def http_template_capture_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_TEMPLATE_CAPTURE_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_TEMPLATE_CAPTURE_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_template_capture(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def http_template_capture_sender(self, *, hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_TEMPLATE_CAPTURE_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_template_capture(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def _http_template_capture(self, *, kind: str, account_id: int, email: str, password: str, event_cb: Event | None) -> dict[str, Any]:
        self._emit(event_cb, f"V3 HTTP EXACT TEMPLATE CAPTURE START kind={kind} id={account_id} email={mask_email(email)} browser=YES secretOutput=NONE")
        try:
            captured: ExactTemplateCaptureResult = capture_exact_login_template(
                self.runtime_cfg,
                email,
                password,
                event_cb=event_cb,
                account_kind=kind,
                account_id=account_id,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_TEMPLATE_CAPTURE_FAILED",
                success=False,
                error_scope="http_template_capture",
                error_code="HTTP_TEMPLATE_CAPTURE_EXCEPTION",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "stage": "HTTP_TEMPLATE_CAPTURE",
                "error": {"code": "HTTP_TEMPLATE_CAPTURE_EXCEPTION", "message": msg},
                "secretOutput": "NONE",
            }

        self.repo.log_event(
            event_type=f"{kind.upper()}_HTTP_TEMPLATE_CAPTURE_{'OK' if captured.ok else 'FAILED'}",
            success=bool(captured.ok),
            error_scope=None if captured.ok else "http_template_capture",
            error_code=None if captured.ok else captured.code,
            detail=captured.message,
            hr_id=account_id if kind == "receiver" else None,
            hs_id=account_id if kind == "sender" else None,
        )
        return {
            "ok": bool(captured.ok),
            "account_kind": kind,
            "account_id": account_id,
            "email_mask": mask_email(email),
            "capture": captured.public_dict(),
            "secretOutput": "NONE",
        }

    def http_template_replay_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_TEMPLATE_REPLAY_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_TEMPLATE_REPLAY_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_template_replay(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def http_template_replay_sender(self, *, hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_TEMPLATE_REPLAY_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_template_replay(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def http_template_reuse_sender(self, *, template_hs_id: int, target_hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        """Replay a known-good sender template against another sender account.

        This is the Phase 4.5.7 gate: it proves whether one captured login
        request template can be reused for another manually owned sender without
        opening a browser again.
        """
        sender = self.repo.get_sender(target_hs_id)
        if not sender:
            raise ConfigError(f"target sender not found: hs_id={target_hs_id}", stage="HTTP_TEMPLATE_REUSE_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(target_hs_id),
            aad=f"heart_sender:{target_hs_id}",
        )
        return self._http_template_replay(
            kind="sender",
            account_id=target_hs_id,
            email=email,
            password=password,
            event_cb=event_cb,
            template_kind="sender",
            template_account_id=template_hs_id,
            reuse_mode=True,
        )

    def http_template_reuse_receiver_for_job(self, *, template_hr_id: int, target_hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(target_hj_id)
        if not job:
            raise ConfigError(f"target heart job not found: hj_id={target_hj_id}", stage="HTTP_TEMPLATE_REUSE_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"target receiver not found: hr_id={hr_id}", stage="HTTP_TEMPLATE_REUSE_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(target_hj_id, hr_id),
            aad=f"heart_receiver_job:{target_hj_id}:{hr_id}",
        )
        return self._http_template_replay(
            kind="receiver",
            account_id=hr_id,
            email=email,
            password=password,
            event_cb=event_cb,
            template_kind="receiver",
            template_account_id=template_hr_id,
            reuse_mode=True,
        )

    def _http_template_replay(
        self,
        *,
        kind: str,
        account_id: int,
        email: str,
        password: str,
        event_cb: Event | None,
        template_kind: str | None = None,
        template_account_id: int | None = None,
        reuse_mode: bool = False,
    ) -> dict[str, Any]:
        source_kind = str(template_kind or kind)
        source_id = int(template_account_id if template_account_id is not None else account_id)
        mode = "REUSE" if reuse_mode or source_kind != kind or source_id != account_id else "REPLAY"
        self._emit(event_cb, f"V3 HTTP EXACT TEMPLATE {mode} START kind={kind} id={account_id} template={source_kind}:{source_id} email={mask_email(email)} browser=NO secretOutput=NONE")
        try:
            replayed: ExactTemplateReplayResult = replay_exact_login_template(
                self.runtime_cfg,
                email,
                password,
                event_cb=event_cb,
                account_kind=kind,
                account_id=account_id,
                template_account_kind=source_kind,
                template_account_id=source_id,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_TEMPLATE_REPLAY_FAILED",
                success=False,
                error_scope="http_template_replay",
                error_code="HTTP_TEMPLATE_REPLAY_EXCEPTION",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "stage": "HTTP_TEMPLATE_REPLAY",
                "error": {"code": "HTTP_TEMPLATE_REPLAY_EXCEPTION", "message": msg},
                "secretOutput": "NONE",
            }

        if not replayed.ok or replayed.bundle is None:
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_TEMPLATE_REPLAY_FAILED",
                success=False,
                error_scope="http_template_replay",
                error_code=replayed.code,
                detail=replayed.message,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "template_source": {"account_kind": source_kind, "account_id": source_id},
                "replay": replayed.public_dict(),
                "secretOutput": "NONE",
            }

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=replayed.bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_TEMPLATE_SESSION_FAILED",
                success=False,
                error_scope="http_template_session",
                error_code="SESSION_INIT_FAILED",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "template_source": {"account_kind": source_kind, "account_id": source_id},
                "replay": replayed.public_dict(),
                "session_error": {"code": "SESSION_INIT_FAILED", "message": msg},
                "secretOutput": "NONE",
            }

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="RECEIVER_HTTP_TEMPLATE_SESSION_OK", hr_id=account_id, success=True, detail="receiver direct-http exact-template auth/session ready")
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="SENDER_HTTP_TEMPLATE_SESSION_OK", hs_id=account_id, success=True, detail="sender direct-http exact-template auth/session ready")
        self._emit(event_cb, f"V3 HTTP EXACT TEMPLATE SESSION OK kind={kind} id={account_id} memberSeq=present lv={session.current_lv} secretOutput=NONE")
        return {
            "ok": True,
            "account_kind": kind,
            "account_id": account_id,
            "email_mask": mask_email(email),
            "template_source": {"account_kind": source_kind, "account_id": source_id},
            "replay": replayed.public_dict(),
            "auth": auth.public_summary(),
            "session": session.public_summary(),
            "secretOutput": "NONE",
        }


    def login_receiver_for_job_http_template(
        self,
        *,
        hj_id: int,
        template_hr_id: int | None = None,
        event_cb: Event | None = None,
    ) -> LoginSessionResult:
        """Login a job receiver with direct HTTP exact-template replay and return runtime objects."""
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_TEMPLATE_LOGIN_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_TEMPLATE_LOGIN_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_template_login_session_result(
            kind="receiver",
            account_id=hr_id,
            email=email,
            password=password,
            event_cb=event_cb,
            template_kind="receiver",
            template_account_id=int(template_hr_id or hr_id),
        )

    def login_sender_http_template(
        self,
        *,
        hs_id: int,
        template_hs_id: int | None = None,
        event_cb: Event | None = None,
    ) -> LoginSessionResult:
        """Login a sender with direct HTTP exact-template replay and return runtime objects."""
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_TEMPLATE_LOGIN_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_template_login_session_result(
            kind="sender",
            account_id=hs_id,
            email=email,
            password=password,
            event_cb=event_cb,
            template_kind="sender",
            template_account_id=int(template_hs_id or hs_id),
        )

    def _http_template_login_session_result(
        self,
        *,
        kind: str,
        account_id: int,
        email: str,
        password: str,
        event_cb: Event | None,
        template_kind: str,
        template_account_id: int,
    ) -> LoginSessionResult:
        self._emit(
            event_cb,
            f"V3 HTTP EXACT TEMPLATE LOGIN START kind={kind} id={account_id} template={template_kind}:{template_account_id} email={mask_email(email)} browser=NO secretOutput=NONE",
        )
        try:
            replayed: ExactTemplateReplayResult = replay_exact_login_template(
                self.runtime_cfg,
                email,
                password,
                event_cb=event_cb,
                account_kind=kind,
                account_id=account_id,
                template_account_kind=template_kind,
                template_account_id=template_account_id,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if kind == "receiver":
                self.repo.update_receiver_runtime_error(hr_id=account_id, stage="HTTP_TEMPLATE_LOGIN", code="HTTP_TEMPLATE_REPLAY_EXCEPTION", message=msg)
            else:
                self.repo.update_sender_runtime_error(hs_id=account_id, stage="HTTP_TEMPLATE_LOGIN", code="HTTP_TEMPLATE_REPLAY_EXCEPTION", message=msg)
            raise AuthError(msg, stage="HTTP_TEMPLATE_LOGIN") from exc

        if not replayed.ok or replayed.bundle is None:
            msg = replayed.message or replayed.code
            if kind == "receiver":
                self.repo.update_receiver_runtime_error(hr_id=account_id, stage="HTTP_TEMPLATE_LOGIN", code=replayed.code, message=msg)
            else:
                self.repo.update_sender_runtime_error(hs_id=account_id, stage="HTTP_TEMPLATE_LOGIN", code=replayed.code, message=msg)
            raise AuthError(msg, stage="HTTP_TEMPLATE_LOGIN")

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=replayed.bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if kind == "receiver":
                self.repo.update_receiver_runtime_error(hr_id=account_id, stage="HTTP_TEMPLATE_SESSION", code="SESSION_INIT_FAILED", message=msg)
            else:
                self.repo.update_sender_runtime_error(hs_id=account_id, stage="HTTP_TEMPLATE_SESSION", code="SESSION_INIT_FAILED", message=msg)
            raise SessionError(msg, stage="HTTP_TEMPLATE_SESSION") from exc

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="RECEIVER_HTTP_TEMPLATE_SESSION_OK", hr_id=account_id, success=True, detail="receiver direct-http exact-template auth/session ready")
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="SENDER_HTTP_TEMPLATE_SESSION_OK", hs_id=account_id, success=True, detail="sender direct-http exact-template auth/session ready")
        self._emit(event_cb, f"V3 HTTP EXACT TEMPLATE SESSION OK kind={kind} id={account_id} memberSeq=present lv={session.current_lv} secretOutput=NONE")
        return LoginSessionResult(account_kind=kind, account_id=account_id, email_mask=mask_email(email), auth=auth, session=session)


    def http_matrix_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_MATRIX_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_MATRIX_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_matrix_login(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def http_matrix_sender(self, *, hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_MATRIX_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_matrix_login(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def _http_matrix_login(self, *, kind: str, account_id: int, email: str, password: str, event_cb: Event | None) -> dict[str, Any]:
        self._emit(event_cb, f"V3 HTTP MATRIX START kind={kind} id={account_id} email={mask_email(email)} browser=NO secretOutput=NONE")
        try:
            matrix: DirectHttpMatrixResult = login_with_email_password_http_replay_matrix(
                self.runtime_cfg,
                email,
                password,
                event_cb=event_cb,
                account_kind=kind,
                account_id=account_id,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_MATRIX_FAILED",
                success=False,
                error_scope="http_matrix",
                error_code="HTTP_MATRIX_EXCEPTION",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "stage": "HTTP_MATRIX",
                "error": {"code": "HTTP_MATRIX_EXCEPTION", "message": msg},
                "secretOutput": "NONE",
            }

        if not matrix.ok or matrix.bundle is None:
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_MATRIX_FAILED",
                success=False,
                error_scope="http_matrix",
                error_code=matrix.code,
                detail=matrix.message,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "matrix": matrix.public_dict(),
                "secretOutput": "NONE",
            }

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=matrix.bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_MATRIX_SESSION_FAILED",
                success=False,
                error_scope="http_matrix_session",
                error_code="SESSION_INIT_FAILED",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "matrix": matrix.public_dict(),
                "session_error": {"code": "SESSION_INIT_FAILED", "message": msg},
                "secretOutput": "NONE",
            }

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="RECEIVER_HTTP_MATRIX_SESSION_OK", hr_id=account_id, success=True, detail="receiver direct-http-matrix auth/session ready")
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="SENDER_HTTP_MATRIX_SESSION_OK", hs_id=account_id, success=True, detail="sender direct-http-matrix auth/session ready")
        self._emit(event_cb, f"V3 HTTP MATRIX SESSION OK kind={kind} id={account_id} memberSeq=present lv={session.current_lv} secretOutput=NONE")
        return {
            "ok": True,
            "account_kind": kind,
            "account_id": account_id,
            "email_mask": mask_email(email),
            "matrix": matrix.public_dict(),
            "auth": auth.public_summary(),
            "session": session.public_summary(),
            "secretOutput": "NONE",
        }

    def http_replay_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_REPLAY_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_REPLAY_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_replay_login(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def http_replay_sender(self, *, hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_REPLAY_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_replay_login(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def _http_replay_login(self, *, kind: str, account_id: int, email: str, password: str, event_cb: Event | None) -> dict[str, Any]:
        self._emit(event_cb, f"V3 HTTP REPLAY START kind={kind} id={account_id} email={mask_email(email)} browser=NO secretOutput=NONE")
        try:
            replay: DirectHttpReplayResult = login_with_email_password_http_replay_v2(self.runtime_cfg, email, password, event_cb=event_cb)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_REPLAY_FAILED",
                success=False,
                error_scope="http_replay",
                error_code="HTTP_REPLAY_EXCEPTION",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "stage": "HTTP_REPLAY",
                "error": {"code": "HTTP_REPLAY_EXCEPTION", "message": msg},
                "secretOutput": "NONE",
            }

        if not replay.ok or replay.bundle is None:
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_REPLAY_FAILED",
                success=False,
                error_scope="http_replay",
                error_code=replay.code,
                detail=replay.message,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "replay": replay.public_dict(),
                "secretOutput": "NONE",
            }

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=replay.bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_REPLAY_SESSION_FAILED",
                success=False,
                error_scope="http_replay_session",
                error_code="SESSION_INIT_FAILED",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "replay": replay.public_dict(),
                "session_error": {"code": "SESSION_INIT_FAILED", "message": msg},
                "secretOutput": "NONE",
            }

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="RECEIVER_HTTP_REPLAY_SESSION_OK", hr_id=account_id, success=True, detail="receiver direct-http-replay-v2 auth/session ready")
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="SENDER_HTTP_REPLAY_SESSION_OK", hs_id=account_id, success=True, detail="sender direct-http-replay-v2 auth/session ready")
        self._emit(event_cb, f"V3 HTTP REPLAY SESSION OK kind={kind} id={account_id} memberSeq=present lv={session.current_lv} secretOutput=NONE")
        return {
            "ok": True,
            "account_kind": kind,
            "account_id": account_id,
            "email_mask": mask_email(email),
            "replay": replay.public_dict(),
            "auth": auth.public_summary(),
            "session": session.public_summary(),
            "secretOutput": "NONE",
        }

    def http_record_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_RECORD_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_RECORD_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_record_login(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def http_record_sender(self, *, hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_RECORD_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_record_login(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def _http_record_login(self, *, kind: str, account_id: int, email: str, password: str, event_cb: Event | None) -> dict[str, Any]:
        self._emit(event_cb, f"V3 NETWORK RECORD START kind={kind} id={account_id} email={mask_email(email)} secretOutput=NONE")
        try:
            record: LoginNetworkRecordResult = record_login_network_flow(
                self.runtime_cfg,
                email,
                password,
                event_cb=event_cb,
                account_kind=kind,
                account_id=account_id,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_NETWORK_RECORD_FAILED",
                success=False,
                error_scope="network_record",
                error_code="NETWORK_RECORD_EXCEPTION",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "stage": "NETWORK_RECORD",
                "error": {"code": "NETWORK_RECORD_EXCEPTION", "message": msg},
                "secretOutput": "NONE",
            }

        if not record.ok or record.bundle is None:
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_NETWORK_RECORD_FAILED",
                success=False,
                error_scope="network_record",
                error_code=record.code,
                detail=record.message,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "record": record.public_dict(),
                "secretOutput": "NONE",
            }

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=record.bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_NETWORK_RECORD_SESSION_FAILED",
                success=False,
                error_scope="network_record_session",
                error_code="SESSION_INIT_FAILED",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "record": record.public_dict(),
                "session_error": {"code": "SESSION_INIT_FAILED", "message": msg},
                "secretOutput": "NONE",
            }

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
        self.repo.log_event(
            event_type=f"{kind.upper()}_HTTP_NETWORK_RECORD_OK",
            success=True,
            error_scope=None,
            error_code=None,
            detail=f"report={record.report_path or ''}",
            hr_id=account_id if kind == "receiver" else None,
            hs_id=account_id if kind == "sender" else None,
        )
        return {
            "ok": True,
            "account_kind": kind,
            "account_id": account_id,
            "email_mask": mask_email(email),
            "record": record.public_dict(),
            "auth": auth.public_summary(),
            "session": session.public_summary(),
            "secretOutput": "NONE",
        }

    def http_probe_receiver_for_job(self, *, hj_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HTTP_PROBE_RECEIVER")
        hr_id = int(job["hr_id"])
        receiver = self.repo.get_receiver(hr_id)
        if not receiver:
            raise ConfigError(f"receiver not found: hr_id={hr_id}", stage="HTTP_PROBE_RECEIVER")
        email, password = self._decrypt_credential(
            self.repo.get_receiver_job_vault(hj_id, hr_id),
            aad=f"heart_receiver_job:{hj_id}:{hr_id}",
        )
        return self._http_probe_login(kind="receiver", account_id=hr_id, email=email, password=password, event_cb=event_cb)

    def http_probe_sender(self, *, hs_id: int, event_cb: Event | None = None) -> dict[str, Any]:
        sender = self.repo.get_sender(hs_id)
        if not sender:
            raise ConfigError(f"sender not found: hs_id={hs_id}", stage="HTTP_PROBE_SENDER")
        email, password = self._decrypt_credential(
            self.repo.get_sender_vault(hs_id),
            aad=f"heart_sender:{hs_id}",
        )
        return self._http_probe_login(kind="sender", account_id=hs_id, email=email, password=password, event_cb=event_cb)

    def _http_probe_login(self, *, kind: str, account_id: int, email: str, password: str, event_cb: Event | None) -> dict[str, Any]:
        self._emit(event_cb, f"V3 HTTP LOGIN PROBE START kind={kind} id={account_id} email={mask_email(email)} secretOutput=NONE")
        try:
            probe: HttpLoginProbeResult = login_with_email_password_http_probe(self.runtime_cfg, email, password, event_cb=event_cb)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_LOGIN_PROBE_FAILED",
                success=False,
                error_scope="http_probe",
                error_code="HTTP_PROBE_EXCEPTION",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "stage": "HTTP_LOGIN",
                "error": {"code": "HTTP_PROBE_EXCEPTION", "message": msg},
                "secretOutput": "NONE",
            }

        if not probe.ok or probe.bundle is None:
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_LOGIN_PROBE_FAILED",
                success=False,
                error_scope="http_probe",
                error_code=probe.code,
                detail=probe.message,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "probe": probe.public_dict(),
                "secretOutput": "NONE",
            }

        auth = self._make_auth_record(kind=kind, account_id=account_id, bundle=probe.bundle)
        try:
            session = bootstrap_session(self.runtime_cfg, kind.upper()[0], auth, event_cb=event_cb)
        except SessionBootstrapError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.repo.log_event(
                event_type=f"{kind.upper()}_HTTP_PROBE_SESSION_FAILED",
                success=False,
                error_scope="http_probe_session",
                error_code="SESSION_INIT_FAILED",
                detail=msg,
                hr_id=account_id if kind == "receiver" else None,
                hs_id=account_id if kind == "sender" else None,
            )
            return {
                "ok": False,
                "account_kind": kind,
                "account_id": account_id,
                "email_mask": mask_email(email),
                "probe": probe.public_dict(),
                "session_error": {"code": "SESSION_INIT_FAILED", "message": msg},
                "secretOutput": "NONE",
            }

        if kind == "receiver":
            self.repo.update_receiver_runtime_ok(hr_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="RECEIVER_HTTP_LOGIN_SESSION_OK", hr_id=account_id, success=True, detail="receiver direct-http auth/session ready")
        else:
            self.repo.update_sender_runtime_ok(hs_id=account_id, member_seq=session.member_seq, current_lv=session.current_lv)
            self.repo.log_event(event_type="SENDER_HTTP_LOGIN_SESSION_OK", hs_id=account_id, success=True, detail="sender direct-http auth/session ready")
        self._emit(event_cb, f"V3 HTTP LOGIN SESSION OK kind={kind} id={account_id} memberSeq=present lv={session.current_lv} secretOutput=NONE")
        return {
            "ok": True,
            "account_kind": kind,
            "account_id": account_id,
            "email_mask": mask_email(email),
            "probe": probe.public_dict(),
            "auth": auth.public_summary(),
            "session": session.public_summary(),
            "secretOutput": "NONE",
        }

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
