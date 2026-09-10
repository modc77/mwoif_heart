from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .auth_refresh import needs_refresh, refresh_access_token
from .client_identity import ClientIdentity
from .credential_vault import Credential
from .login_runtime import login_and_bootstrap, make_auth_record
from .runtime_store import MemoryStore
from .workflow import run_cycle

Event = Callable[[str], None]


class V2Runner:
    def __init__(self, cfg, root: Path, event_cb: Event | None = None):
        self.cfg = cfg
        self.root = root
        self.event_cb = event_cb
        self.identity = ClientIdentity.load_or_create(root)
        self.auths = MemoryStore()
        self.sessions = MemoryStore()
        self.bundles = {}

    def emit(self, text: str) -> None:
        if self.event_cb:
            self.event_cb(text)

    def login_slot(self, slot: str, email: str, password: str):
        self.emit(f"LOGIN START slot={slot}")
        bundle, auth, session = login_and_bootstrap(
            self.cfg, self.root, slot, email, password, self.identity, event_cb=self.event_cb
        )
        self.bundles[slot] = bundle
        self.auths.set(slot, auth)
        self.sessions.set(slot, session)
        self.emit(f"LOGIN READY slot={slot} auth=OK session=OK")
        return bundle, auth, session

    def refresh_slot_if_needed(self, slot: str) -> None:
        bundle = self.bundles.get(slot)
        if bundle is None:
            return
        before = int(self.cfg.raw.get("v2", {}).get("refresh_before_expiry_seconds") or 300)
        if not needs_refresh(bundle, before):
            return
        updated = refresh_access_token(self.cfg, bundle, event_cb=self.event_cb)
        self.bundles[slot] = updated
        self.auths.set(slot, make_auth_record(self.cfg, self.root, slot, updated, self.identity))
        self.emit(f"AUTH READY slot={slot} refreshed=YES")

    def run_accounts(self, receiver: Credential, senders: list[Credential], live: bool) -> dict:
        receiver_slot = "A"
        self.login_slot(receiver_slot, receiver.email, receiver.password)
        results = []
        wf = self.cfg.workflow

        for index, sender in enumerate(senders, 1):
            slot = sender.slot
            self.emit(f"SENDER {index}/{len(senders)} START slot={slot}")
            try:
                self.refresh_slot_if_needed(receiver_slot)
                self.login_slot(slot, sender.email, sender.password)
                result = run_cycle(
                    cfg=self.cfg,
                    sessions=self.sessions,
                    auths=self.auths,
                    sender=slot,
                    receiver=receiver_slot,
                    live=live,
                    source_type=int(wf.get("friend_source_type", 2)),
                    grpc_timeout=float(wf.get("grpc_timeout_seconds", 12)),
                    ds_timeout=float(wf.get("ds_timeout_seconds", 20)),
                    mailbox_retries=int(wf.get("mailbox_retries", 6)),
                    mailbox_delay=float(wf.get("mailbox_delay_seconds", 1.0)),
                    step_delay=float(wf.get("step_delay_seconds", 0.35)),
                    event_cb=lambda e: self.emit(self._event_text(e)),
                )
                results.append({"slot": slot, "ok": bool(result.get("ok")), "failed_step": result.get("failed_step")})
                self.emit(f"SENDER {slot} {'OK' if result.get('ok') else 'FAILED step='+str(result.get('failed_step'))}")
            except Exception as exc:
                results.append({"slot": slot, "ok": False, "failed_step": "login/bootstrap", "error": type(exc).__name__})
                self.emit(f"SENDER {slot} FAILED {type(exc).__name__}: {exc}")
            finally:
                self.auths.remove(slot)
                self.sessions.remove(slot)
                self.bundles.pop(slot, None)
            if index < len(senders):
                time.sleep(max(0.0, float(wf.get("batch_delay_seconds", 0.5))))

        return {
            "ok": all(x["ok"] for x in results) if results else True,
            "completed": sum(1 for x in results if x["ok"]),
            "failed": sum(1 for x in results if not x["ok"]),
            "results": results,
            "secrets_logged": False,
        }

    @staticmethod
    def _event_text(e: dict) -> str:
        event = str(e.get("event") or "EVENT")
        sender = e.get("sender")
        receiver = e.get("receiver")
        step = e.get("step") or e.get("failed_step")
        parts = [event]
        if step: parts.append(f"step={step}")
        if sender: parts.append(f"sender={sender}")
        if receiver: parts.append(f"receiver={receiver}")
        if "ok" in e: parts.append(f"ok={bool(e['ok'])}")
        if e.get("attempt"): parts.append(f"attempt={e['attempt']}")
        return " ".join(parts)
