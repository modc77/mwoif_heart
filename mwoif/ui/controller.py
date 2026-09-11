from __future__ import annotations

from dataclasses import asdict
import threading
from typing import Any, Callable

from mwoif.application.account_manager import AccountManager
from mwoif.application.bootstrap import build_repository, check_health
from mwoif.application.production_job_runner import ProductionJobRunner
from mwoif.application.fast_batch_runner import FastBatchJobRunner
from mwoif.application.warm_sender_pool import WarmSenderPool
from mwoif.application.local_update_guard import LocalUpdateGuard
from mwoif.application.realtime import emit_rt
from mwoif.core.config import AppConfig, load_config
from mwoif.domain.enums import Source
from mwoif.friend.service import list_friends, remove_friend
from mwoif.storage.vault import CredentialVault

EventCb = Callable[[str], None] | None


class UiController:
    """Thin adapter between the desktop UI and production Core.

    No business rules live here. The same application/core/storage layers remain
    usable by the future web worker.
    """

    def __init__(self) -> None:
        self._prepared_lock = threading.RLock()
        self._prepared_jobs: dict[int, dict[str, Any]] = {}
        self._warm_lock = threading.RLock()
        self._warm_pool: WarmSenderPool | None = None

    def close(self) -> None:
        with self._warm_lock:
            pool = self._warm_pool
            self._warm_pool = None
        if pool is not None:
            pool.close(wait=False)

    def load(self) -> tuple[AppConfig, Any, CredentialVault]:
        cfg = load_config()
        repo = build_repository(cfg)
        vault = CredentialVault(cfg.vault)
        return cfg, repo, vault

    def _get_warm_pool(self, *, max_workers: int = 20) -> WarmSenderPool:
        with self._warm_lock:
            if self._warm_pool is None:
                cfg, repo, vault = self.load()
                self._warm_pool = WarmSenderPool(cfg, repo, vault, max_workers=max_workers)
            return self._warm_pool

    def warm_pool_status(self) -> dict[str, Any]:
        pool = self._get_warm_pool()
        pool.repo.restore_local_auto_disabled_senders()
        return pool.status()

    def warm_sender_pool(
        self, *, target: int, workers: int, template_hs_id: int, event_cb: EventCb = None
    ) -> dict[str, Any]:
        pool = self._get_warm_pool(max_workers=workers)
        restored = pool.repo.restore_local_auto_disabled_senders()
        if restored and event_cb:
            event_cb(f"LOCAL OPEN POOL normalized={restored} sender(s) enabled=YES quarantine=OFF secretOutput=NONE")
        return pool.warm(
            target=max(1, int(target)), workers=max(1, int(workers)),
            template_hs_id=int(template_hs_id), event_cb=event_cb,
        )

    def local_update_check(
        self, *, template_hs_id: int, sender_hs_id: int | None = None, event_cb: EventCb = None
    ) -> dict[str, Any]:
        """Read-only post-update smoke test for the Local/Lab tool."""
        cfg, repo, vault = self.load()
        return LocalUpdateGuard(cfg, repo, vault).run(
            sender_hs_id=sender_hs_id,
            template_hs_id=int(template_hs_id),
            event_cb=event_cb,
        )

    def dashboard(self) -> dict[str, Any]:
        cfg, repo, _vault = self.load()
        repo.restore_local_auto_disabled_senders()
        health = check_health(cfg, include_dashboard=False)
        prod = repo.production_dashboard()
        return {
            "ok": bool(health.ok and prod.get("ok")),
            "health": {"ok": health.ok, "missing": health.missing_tables},
            "prod": prod,
            "warm_pool": self._get_warm_pool().status(),
            "jobs": repo.list_jobs(limit=8),
            "events": repo.tail_events(limit=8),
            "secretOutput": "NONE",
        }

    def list_senders(self, limit: int = 20000) -> list[dict[str, Any]]:
        _cfg, repo, vault = self.load()
        repo.restore_local_auto_disabled_senders()
        rows = repo.list_senders(limit=limit)
        blobs = repo.list_sender_identity_vaults(limit=limit)
        for row in rows:
            hs_id = int(row.get("hs_id") or 0)
            row["email_local"] = row.get("email_mask")
            blob = blobs.get(hs_id)
            if blob is None:
                continue
            try:
                payload = vault.decrypt_json(blob, aad=f"heart_sender:{hs_id}")
                email = str(payload.get("email") or "").strip()
                if email:
                    row["email_local"] = email
            except Exception:
                # Keep the masked fallback. A vault read/display failure never
                # changes Local sender eligibility.
                pass
        return rows

    def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        _cfg, repo, _vault = self.load()
        return repo.list_jobs(limit=limit)

    def list_events(self, limit: int = 500) -> list[dict[str, Any]]:
        _cfg, repo, _vault = self.load()
        return repo.tail_events(limit=limit)

    def production_status(self) -> dict[str, Any]:
        _cfg, repo, _vault = self.load()
        repo.restore_local_auto_disabled_senders()
        return repo.production_dashboard()

    def set_shared_password(self, password: str, credential_name: str = "default") -> dict[str, Any]:
        _cfg, repo, vault = self.load()
        AccountManager(repo, vault).store_shared_sender_password(
            credential_name=credential_name,
            password=password,
        )
        repo.log_event(
            event_type="UI_SENDER_SHARED_PASSWORD_SET",
            success=True,
            detail="shared sender credential updated from local UI",
        )
        return {"ok": True, "credential_name": credential_name, "credential_stored": True, "secretOutput": "NONE"}

    def add_sender(self, *, label: str, email: str, shared_credential: str = "default") -> dict[str, Any]:
        _cfg, repo, vault = self.load()
        manager = AccountManager(repo, vault)
        sender = manager.ensure_sender(label, email)
        manager.store_sender_identity_with_shared_password(
            sender.hs_id,
            email=email,
            shared_credential=shared_credential,
        )
        repo.log_event(
            event_type="UI_SENDER_UPSERT",
            hs_id=sender.hs_id,
            success=True,
            detail="local UI sender stored with shared credential",
        )
        return {"ok": True, "sender": asdict(sender), "uses_shared_password": True, "secretOutput": "NONE"}

    def import_sender_pattern(
        self,
        *,
        prefix: str,
        domain: str,
        start: int,
        end: int,
        width: int,
        label_prefix: str,
        shared_credential: str = "default",
    ) -> dict[str, Any]:
        _cfg, repo, vault = self.load()
        result = AccountManager(repo, vault).import_sender_pattern(
            prefix=prefix,
            domain=domain,
            start=start,
            end=end,
            width=width,
            label_prefix=label_prefix,
            shared_credential=shared_credential,
            store_shared_identity=True,
        )
        repo.log_event(
            event_type="UI_SENDER_PATTERN_IMPORT",
            success=True,
            detail=f"local UI import pattern count={result.get('count', 0)}",
        )
        return result

    def prepare_new_job_preflight(
        self,
        *,
        email: str,
        password: str,
        amount: int,
        template_hr_id: int | None,
        batch_slots: int = 50,
        friend_capacity: int = 300,
        event_cb: EventCb = None,
    ) -> dict[str, Any]:
        """Create a queued job, login Receiver once, then check friend capacity.

        The LoginSessionResult is held only in local process memory so a user
        confirmation can continue with the same Receiver session. Nothing new
        is stored in the database schema.
        """
        cfg, repo, vault = self.load()
        account = AccountManager(repo, vault)
        receiver = account.ensure_receiver(email, source=Source.LOCAL)
        job = account.create_job_with_receiver_credential(
            hr_id=receiver.hr_id,
            requested_hearts=amount,
            email=email,
            password=password,
            source=Source.LOCAL,
        )
        repo.log_event(
            event_type="P53_PREFLIGHT_JOB_CREATED",
            hj_id=job.hj_id,
            hr_id=receiver.hr_id,
            success=True,
            detail="friend-capacity preflight job created",
        )
        runner = ProductionJobRunner(cfg, repo, vault)
        login = runner.login_manager.login_receiver_for_job_http_template(
            hj_id=job.hj_id,
            template_hr_id=template_hr_id or receiver.hr_id,
            event_cb=event_cb,
        )
        if event_cb:
            event_cb("P53 PREFLIGHT FRIEND LIST START receiver_login_reuse=YES secretOutput=NONE")
        result = list_friends(
            cfg=runner.runtime_cfg,
            slot="R",
            auth=login.auth,
            timeout=float(runner.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
            live=True,
            capacity=friend_capacity,
            include_player_ids=True,
        )
        if not bool(result.get("ok")):
            repo.mark_job_paused(
                hj_id=job.hj_id,
                error_scope="receiver",
                error_code=str(result.get("grpc_code") or result.get("error") or "FRIEND_LIST_FAILED"),
                message=str(result.get("grpc_details") or result.get("message") or "receiver friend list failed"),
            )
            raise RuntimeError("ตรวจรายชื่อเพื่อนของไอดีรับไม่สำเร็จ")

        friend_ids = [str(x) for x in (result.get("friend_player_ids") or []) if str(x).strip()]
        friend_count = int(result.get("friend_count") or 0)
        confidence = str(result.get("parser_confidence") or "none")
        response_bytes = int(result.get("response_bytes") or 0)
        # An empty gRPC body is a valid empty friend list. A non-empty response
        # that we cannot parse must never be treated as 0 friends; doing so
        # could start a batch against a full receiver.
        parser_valid = confidence in {"high", "medium"} or response_bytes == 0
        required_slots = max(1, min(int(amount), max(1, int(batch_slots))))
        free_slots = max(0, int(friend_capacity) - friend_count) if parser_valid else 0
        need_remove = max(0, required_slots - free_slots) if parser_valid else required_slots
        cleanup_supported = parser_valid and confidence in {"high", "medium"} and len(friend_ids) >= need_remove
        emit_rt(
            event_cb,
            "FRIEND_PREFLIGHT",
            hj_id=job.hj_id,
            friend_count=friend_count,
            capacity=int(friend_capacity),
            free_slots=free_slots,
            required_slots=required_slots,
            need_remove=need_remove,
            cleanup_supported=cleanup_supported,
            parser_confidence=confidence,
            parser_valid=parser_valid,
        )

        prepared = {
            "cfg": cfg,
            "repo": repo,
            "vault": vault,
            "login": login,
            "friend_ids": friend_ids,
            "friend_capacity": int(friend_capacity),
            "batch_slots": int(batch_slots),
            "required_slots": required_slots,
            "need_remove": need_remove,
            "template_hr_id": template_hr_id or receiver.hr_id,
            "created_job": job,
        }
        with self._prepared_lock:
            self._prepared_jobs[int(job.hj_id)] = prepared

        if event_cb:
            event_cb(
                f"P53 PREFLIGHT FRIENDS current={friend_count}/{friend_capacity} free={free_slots} "
                f"required={required_slots} need_remove={need_remove} confidence={confidence} secretOutput=NONE"
            )
        return {
            "ok": True,
            "prepared": True,
            "created_job": asdict(job),
            "preflight": {
                "friend_count": friend_count,
                "friend_capacity": int(friend_capacity),
                "free_slots": free_slots,
                "batch_slots": int(batch_slots),
                "required_slots": required_slots,
                "need_remove": need_remove,
                "needs_cleanup": need_remove > 0,
                "cleanup_supported": cleanup_supported,
                "parser_confidence": confidence,
                "parser_valid": parser_valid,
                "response_bytes": response_bytes,
                "player_ids_available": len(friend_ids),
                "secretOutput": "NONE",
            },
            "secretOutput": "NONE",
        }

    def prepare_existing_job_preflight(
        self,
        *,
        hj_id: int,
        template_hr_id: int | None,
        batch_slots: int = 50,
        friend_capacity: int = 300,
        event_cb: EventCb = None,
    ) -> dict[str, Any]:
        cfg, repo, vault = self.load()
        job = repo.get_job(int(hj_id))
        if not job:
            raise RuntimeError(f"ไม่พบ JOB #{hj_id}")
        requested = int(job.get("requested_hearts") or 0)
        completed = int(job.get("completed_hearts") or 0)
        remaining = max(0, requested-completed)
        if remaining <= 0:
            raise RuntimeError(f"JOB #{hj_id} เสร็จแล้ว")
        hr_id = int(job["hr_id"])
        runner = ProductionJobRunner(cfg, repo, vault)
        login = runner.login_manager.login_receiver_for_job_http_template(
            hj_id=int(hj_id),
            template_hr_id=template_hr_id or hr_id,
            event_cb=event_cb,
        )
        result = list_friends(
            cfg=runner.runtime_cfg, slot="R", auth=login.auth,
            timeout=float(runner.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
            live=True, capacity=friend_capacity, include_player_ids=True,
        )
        if not bool(result.get("ok")):
            raise RuntimeError("ตรวจรายชื่อเพื่อนของไอดีรับไม่สำเร็จ")
        friend_ids = [str(x) for x in (result.get("friend_player_ids") or []) if str(x).strip()]
        friend_count = int(result.get("friend_count") or 0)
        confidence = str(result.get("parser_confidence") or "none")
        response_bytes = int(result.get("response_bytes") or 0)
        parser_valid = confidence in {"high", "medium"} or response_bytes == 0
        required_slots = max(1, min(remaining, max(1, int(batch_slots))))
        free_slots = max(0, int(friend_capacity)-friend_count) if parser_valid else 0
        need_remove = max(0, required_slots-free_slots) if parser_valid else required_slots
        cleanup_supported = parser_valid and confidence in {"high","medium"} and len(friend_ids) >= need_remove
        prepared = {
            "cfg": cfg, "repo": repo, "vault": vault, "login": login,
            "friend_ids": friend_ids, "friend_capacity": int(friend_capacity),
            "batch_slots": int(batch_slots), "required_slots": required_slots,
            "need_remove": need_remove, "template_hr_id": template_hr_id or hr_id,
            "created_job": dict(job),
        }
        with self._prepared_lock:
            self._prepared_jobs[int(hj_id)] = prepared
        emit_rt(event_cb, "FRIEND_PREFLIGHT", hj_id=int(hj_id), friend_count=friend_count, capacity=int(friend_capacity), free_slots=free_slots, required_slots=required_slots, need_remove=need_remove, cleanup_supported=cleanup_supported, parser_confidence=confidence, parser_valid=parser_valid)
        return {
            "ok": True, "created_job": dict(job),
            "preflight": {
                "friend_count": friend_count, "friend_capacity": int(friend_capacity),
                "free_slots": free_slots, "batch_slots": int(batch_slots),
                "required_slots": required_slots, "need_remove": need_remove,
                "needs_cleanup": need_remove > 0, "cleanup_supported": cleanup_supported,
                "parser_confidence": confidence, "parser_valid": parser_valid,
                "response_bytes": response_bytes, "player_ids_available": len(friend_ids),
                "secretOutput": "NONE",
            },
            "secretOutput": "NONE",
        }

    def cancel_prepared_job(self, *, hj_id: int, reason: str = "ผู้ใช้ยกเลิกก่อนเริ่มงาน") -> dict[str, Any]:
        with self._prepared_lock:
            prepared = self._prepared_jobs.pop(int(hj_id), None)
        if prepared:
            repo = prepared["repo"]
        else:
            _cfg, repo, _vault = self.load()
        repo.mark_job_cancelled(hj_id=int(hj_id), message=reason)
        repo.purge_receiver_job_vault(hj_id=int(hj_id))
        repo.log_event(
            event_type="P53_PREFLIGHT_CANCELLED",
            hj_id=int(hj_id),
            success=True,
            detail="preflight cancelled before friend cleanup/job run",
        )
        return {"ok": True, "hj_id": int(hj_id), "status": "cancelled", "secretOutput": "NONE"}

    def run_prepared_job(
        self,
        *,
        hj_id: int,
        template_hs_id: int,
        allow_cleanup: bool,
        sender_workers: int = 10,
        network_workers: int = 20,
        batch_size: int = 50,
        warm_target: int = 100,
        warm_workers: int = 20,
        event_cb: EventCb = None,
    ) -> dict[str, Any]:
        with self._prepared_lock:
            prepared = self._prepared_jobs.get(int(hj_id))
        if not prepared:
            raise RuntimeError(f"prepared job context not found: hj_id={hj_id}")

        cfg = prepared["cfg"]
        repo = prepared["repo"]
        vault = prepared["vault"]
        login = prepared["login"]
        need_remove = int(prepared.get("need_remove") or 0)
        friend_ids = list(prepared.get("friend_ids") or [])
        capacity = int(prepared.get("friend_capacity") or 300)
        required_slots = int(prepared.get("required_slots") or 1)
        runner = FastBatchJobRunner(cfg, repo, vault, warm_pool=self._get_warm_pool(max_workers=warm_workers))

        cleanup: dict[str, Any] = {
            "performed": False,
            "requested_remove": need_remove,
            "removed": 0,
            "secretOutput": "NONE",
        }
        if need_remove > 0:
            if not allow_cleanup:
                return {
                    "ok": False,
                    "status": "waiting-cleanup-confirmation",
                    "hj_id": int(hj_id),
                    "need_remove": need_remove,
                    "secretOutput": "NONE",
                }
            if len(friend_ids) < need_remove:
                repo.mark_job_paused(
                    hj_id=int(hj_id),
                    error_scope="receiver",
                    error_code="FRIEND_LIST_PARSE_UNSAFE",
                    message="Not enough confidently parsed friend player ids for requested cleanup",
                )
                raise RuntimeError("ระบบอ่าน Player ID ของรายชื่อเพื่อนไม่ครบ จึงไม่ลบเพื่อนอัตโนมัติ")
            targets = friend_ids[:need_remove]
            if event_cb:
                event_cb(f"P53 FRIEND CLEANUP START remove={len(targets)} secretOutput=NONE")
            emit_rt(event_cb, "FRIEND_CLEANUP", hj_id=int(hj_id), stage="remove", status="running", count=len(targets))
            removed = remove_friend(
                cfg=runner.runtime_cfg,
                slot="R",
                auth=login.auth,
                target_mids=targets,
                timeout=float(runner.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
                live=True,
            )
            if not bool(removed.get("ok")):
                repo.mark_job_paused(
                    hj_id=int(hj_id),
                    error_scope="receiver",
                    error_code=str(removed.get("grpc_code") or removed.get("error") or "FRIEND_CLEANUP_FAILED"),
                    message=str(removed.get("grpc_details") or removed.get("message") or "friend cleanup failed"),
                )
                raise RuntimeError("ลบเพื่อนเพื่อเปิดพื้นที่ไม่สำเร็จ")
            cleanup.update({"performed": True, "removed": len(targets)})
            if event_cb:
                event_cb(f"P53 FRIEND CLEANUP REMOVE OK count={len(targets)} secretOutput=NONE")
            emit_rt(event_cb, "FRIEND_CLEANUP", hj_id=int(hj_id), stage="remove", status="ok", count=len(targets))

            # Re-check before any Sender work. If the server state is not what
            # we expect, stop rather than letting the job fail mid-batch.
            recheck = list_friends(
                cfg=runner.runtime_cfg,
                slot="R",
                auth=login.auth,
                timeout=float(runner.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
                live=True,
                capacity=capacity,
            )
            if not bool(recheck.get("ok")):
                raise RuntimeError("ตรวจพื้นที่เพื่อนหลังลบไม่สำเร็จ")
            new_count = int(recheck.get("friend_count") or 0)
            new_free = max(0, capacity - new_count)
            cleanup["after_friend_count"] = new_count
            cleanup["after_free_slots"] = new_free
            if event_cb:
                event_cb(f"P53 FRIEND CLEANUP RECHECK current={new_count}/{capacity} free={new_free} secretOutput=NONE")
            emit_rt(event_cb, "FRIEND_PREFLIGHT", hj_id=int(hj_id), friend_count=new_count, capacity=capacity, free_slots=new_free, required_slots=required_slots, need_remove=max(0, required_slots-new_free), cleanup_supported=True, parser_confidence=str(recheck.get("parser_confidence") or "unknown"))
            if new_free < required_slots:
                repo.mark_job_paused(
                    hj_id=int(hj_id),
                    error_scope="receiver",
                    error_code="FRIEND_CAPACITY_STILL_INSUFFICIENT",
                    message=f"free friend slots {new_free} < required {required_slots}",
                )
                raise RuntimeError(f"พื้นที่เพื่อนยังไม่พอหลังลบ: ว่าง {new_free} / ต้องการ {required_slots}")

        try:
            result = runner.run_job(
                hj_id=int(hj_id),
                template_hr_id=int(prepared.get("template_hr_id") or 0) or None,
                template_hs_id=int(template_hs_id),
                receiver_login_override=login,
                sender_workers=int(sender_workers),
                network_workers=int(network_workers),
                batch_size=int(batch_size),
                warm_target=int(warm_target),
                warm_workers=int(warm_workers),
                event_cb=event_cb,
            )
        finally:
            runner.close(wait=False)
            # Never keep auth/session objects alive in the UI controller after
            # the prepared run ends (success or error). A retry performs a new
            # preflight and obtains a fresh receiver session.
            with self._prepared_lock:
                self._prepared_jobs.pop(int(hj_id), None)
        created = prepared["created_job"]
        try:
            created_public = asdict(created)
        except TypeError:
            created_public = dict(created) if isinstance(created, dict) else {"hj_id": int(hj_id)}
        return {
            "ok": bool(result.get("ok")),
            "created_job": created_public,
            "preflight_cleanup": cleanup,
            "runner": result,
            "secretOutput": "NONE",
        }

    def request_stop_job(self, *, hj_id: int) -> dict[str, Any]:
        _cfg, repo, _vault = self.load()
        repo.request_job_stop(hj_id=int(hj_id))
        repo.log_event(
            event_type="P53_UI_STOP_REQUESTED",
            hj_id=int(hj_id),
            success=True,
            detail="stop requested; fast runner will stop before next batch",
        )
        return {"ok": True, "hj_id": int(hj_id), "status": "stopping", "secretOutput": "NONE"}

    def create_and_run_job(
        self,
        *,
        email: str,
        password: str,
        amount: int,
        template_hs_id: int,
        template_hr_id: int | None,
        event_cb: EventCb = None,
    ) -> dict[str, Any]:
        cfg, repo, vault = self.load()
        account = AccountManager(repo, vault)
        receiver = account.ensure_receiver(email, source=Source.LOCAL)
        job = account.create_job_with_receiver_credential(
            hr_id=receiver.hr_id,
            requested_hearts=amount,
            email=email,
            password=password,
            source=Source.LOCAL,
        )
        repo.log_event(
            event_type="UI_JOB_CREATED",
            hj_id=job.hj_id,
            hr_id=receiver.hr_id,
            success=True,
            detail="professional local UI created job",
        )
        runner = FastBatchJobRunner(cfg, repo, vault, warm_pool=self._get_warm_pool())
        result = runner.run_job(
            hj_id=job.hj_id,
            template_hr_id=template_hr_id or receiver.hr_id,
            template_hs_id=template_hs_id,
            event_cb=event_cb,
        )
        return {
            "ok": bool(result.get("ok")),
            "created_job": asdict(job),
            "runner": result,
            "secretOutput": "NONE",
        }

    def run_existing_job(
        self,
        *,
        hj_id: int,
        template_hs_id: int,
        template_hr_id: int | None,
        sender_workers: int = 10,
        network_workers: int = 20,
        batch_size: int = 50,
        event_cb: EventCb = None,
    ) -> dict[str, Any]:
        cfg, repo, vault = self.load()
        runner = FastBatchJobRunner(cfg, repo, vault, warm_pool=self._get_warm_pool())
        return runner.run_job(
            hj_id=hj_id,
            template_hr_id=template_hr_id,
            template_hs_id=template_hs_id,
            sender_workers=int(sender_workers),
            network_workers=int(network_workers),
            batch_size=int(batch_size),
            event_cb=event_cb,
        )
