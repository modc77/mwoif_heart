from __future__ import annotations

import time
from typing import Any, Callable

from .accounts import require_pair
from .friend_grpc import (
    handle_friend_request,
    remove_friend,
    send_friend_request,
)
from .heart_ds import preview_heart_send
from .heart_mailbox import preview_mail_list
from .heart_receive import preview_heart_receive
from .slots import normalize_slot


EventCallback = Callable[[dict[str, Any]], None]


def _emit(event_cb: EventCallback | None, event: str, **payload: Any) -> None:
    if event_cb is None:
        return
    try:
        event_cb({"event": event, **payload})
    except Exception:
        # UI/log callbacks must never break the validated network workflow.
        pass


class CycleError(RuntimeError):
    def __init__(self, step: str, result: dict[str, Any]):
        super().__init__(f"{step} failed")
        self.step = step
        self.result = result


def cycle_plan(sender: str, receiver: str) -> list[dict[str, str]]:
    sender = normalize_slot(sender)
    receiver = normalize_slot(receiver)
    return [
        {"step": "friend-add", "actor": sender, "target": receiver},
        {"step": "friend-accept", "actor": receiver, "target": sender},
        {"step": "heart-send", "actor": sender, "target": receiver},
        {"step": "heart-mail-list", "actor": receiver, "target": sender},
        {"step": "heart-receive", "actor": receiver, "target": sender},
        {"step": "friend-remove", "actor": sender, "target": receiver},
    ]


def _brief(step: str, result: dict[str, Any]) -> dict[str, Any]:
    out = {
        "step": step,
        "ok": bool(result.get("ok")),
    }
    for key in (
        "elapsed_ms",
        "grpc_code",
        "grpc_details",
        "http_status",
        "response_code",
        "response_message",
        "error",
        "message",
    ):
        if result.get(key) not in (None, ""):
            out[key] = result[key]
    return out


def _must(step: str, result: dict[str, Any], steps: list[dict[str, Any]]):
    brief = _brief(step, result)
    steps.append(brief)
    if not result.get("ok"):
        raise CycleError(step, result)


def run_cycle(
    *,
    cfg,
    sessions,
    auths,
    sender: str,
    receiver: str,
    live: bool,
    source_type: int = 2,
    grpc_timeout: float = 12.0,
    ds_timeout: float = 20.0,
    mailbox_retries: int = 6,
    mailbox_delay: float = 1.0,
    step_delay: float = 0.35,
    event_cb: EventCallback | None = None,
) -> dict[str, Any]:
    sender, sender_session, sender_auth = require_pair(
        sender, sessions, auths
    )
    receiver, receiver_session, receiver_auth = require_pair(
        receiver, sessions, auths
    )
    if sender == receiver:
        raise ValueError("sender and receiver must be different slots")

    _emit(event_cb, "cycle_start", sender=sender, receiver=receiver)
    plan = cycle_plan(sender, receiver)
    if not live:
        _emit(event_cb, "cycle_preview", sender=sender, receiver=receiver, plan=plan)
        return {
            "ok": True,
            "live": False,
            "sender": sender,
            "receiver": receiver,
            "plan": plan,
            "note": "preview only; add --live to execute",
        }

    steps: list[dict[str, Any]] = []
    life_mail_seq: int | None = None

    try:
        _emit(event_cb, "step_start", step="friend-add", sender=sender, receiver=receiver)
        add = send_friend_request(
            cfg=cfg,
            slot=sender,
            auth=sender_auth,
            target_mid=receiver_auth.mid,
            source_type=source_type,
            timeout=grpc_timeout,
            live=True,
        )
        _must("friend-add", add, steps)
        _emit(event_cb, "step_done", step="friend-add", sender=sender, receiver=receiver, ok=True, result=_brief("friend-add", add))
        time.sleep(step_delay)

        _emit(event_cb, "step_start", step="friend-accept", sender=sender, receiver=receiver)
        accept = handle_friend_request(
            cfg=cfg,
            slot=receiver,
            auth=receiver_auth,
            target_mid=sender_auth.mid,
            accept=True,
            timeout=grpc_timeout,
            live=True,
        )
        _must("friend-accept", accept, steps)
        _emit(event_cb, "step_done", step="friend-accept", sender=sender, receiver=receiver, ok=True, result=_brief("friend-accept", accept))
        time.sleep(step_delay)

        _emit(event_cb, "step_start", step="heart-send", sender=sender, receiver=receiver)
        send = preview_heart_send(
            cfg=cfg,
            actor_slot=sender,
            target_slot=receiver,
            actor_session=sender_session,
            target_session=receiver_session,
            actor_auth=sender_auth,
            live=True,
            timeout=ds_timeout,
        )
        _must("heart-send", send, steps)
        _emit(event_cb, "step_done", step="heart-send", sender=sender, receiver=receiver, ok=True, result=_brief("heart-send", send))
        time.sleep(step_delay)

        mailbox_result = None
        _emit(event_cb, "step_start", step="heart-mail-list", sender=sender, receiver=receiver)
        for attempt in range(1, max(1, mailbox_retries) + 1):
            _emit(event_cb, "mailbox_attempt", sender=sender, receiver=receiver, attempt=attempt, max_attempts=max(1, mailbox_retries))
            mailbox_result = preview_mail_list(
                cfg=cfg,
                slot=receiver,
                session=receiver_session,
                auth=receiver_auth,
                from_slot=sender,
                from_member_seq=int(sender_session.member_seq),
                live=True,
                timeout=ds_timeout,
            )
            if not mailbox_result.get("ok"):
                _must("heart-mail-list", mailbox_result, steps)

            candidates = mailbox_result.get("life_mail_candidates") or []
            if candidates:
                life_mail_seq = int(candidates[0]["seq"])
                steps.append({
                    "step": "heart-mail-list",
                    "ok": True,
                    "attempt": attempt,
                    "life_mail_seq": life_mail_seq,
                })
                _emit(event_cb, "step_done", step="heart-mail-list", sender=sender, receiver=receiver, ok=True, life_mail_seq=life_mail_seq, attempt=attempt)
                break

            if attempt < mailbox_retries:
                time.sleep(max(0.0, mailbox_delay))

        if life_mail_seq is None:
            result = dict(mailbox_result or {})
            result.update({
                "ok": False,
                "error": "HEART_MAIL_NOT_FOUND",
                "message": (
                    f"no pending heart from sender {sender} "
                    f"after {mailbox_retries} mailbox checks"
                ),
            })
            raise CycleError("heart-mail-list", result)

        _emit(event_cb, "step_start", step="heart-receive", sender=sender, receiver=receiver, life_mail_seq=life_mail_seq)
        receive = preview_heart_receive(
            cfg=cfg,
            actor_slot=receiver,
            actor_session=receiver_session,
            actor_auth=receiver_auth,
            life_mail_box_seqs=[life_mail_seq],
            live=True,
            timeout=ds_timeout,
        )
        _must("heart-receive", receive, steps)
        _emit(event_cb, "step_done", step="heart-receive", sender=sender, receiver=receiver, ok=True, result=_brief("heart-receive", receive))
        time.sleep(step_delay)

        _emit(event_cb, "step_start", step="friend-remove", sender=sender, receiver=receiver)
        remove = remove_friend(
            cfg=cfg,
            slot=sender,
            auth=sender_auth,
            target_mids=[receiver_auth.mid],
            timeout=grpc_timeout,
            live=True,
        )
        _must("friend-remove", remove, steps)
        _emit(event_cb, "step_done", step="friend-remove", sender=sender, receiver=receiver, ok=True, result=_brief("friend-remove", remove))
        _emit(event_cb, "cycle_done", sender=sender, receiver=receiver, ok=True, life_mail_seq=life_mail_seq)

        return {
            "ok": True,
            "live": True,
            "sender": sender,
            "receiver": receiver,
            "life_mail_seq": life_mail_seq,
            "steps": steps,
        }

    except CycleError as exc:
        _emit(
            event_cb,
            "cycle_failed",
            sender=sender,
            receiver=receiver,
            ok=False,
            failed_step=exc.step,
            failure=_brief(exc.step, exc.result),
        )
        return {
            "ok": False,
            "live": True,
            "sender": sender,
            "receiver": receiver,
            "failed_step": exc.step,
            "life_mail_seq": life_mail_seq,
            "steps": steps,
            "failure": _brief(exc.step, exc.result),
        }


def run_batch(
    *,
    cfg,
    sessions,
    auths,
    senders: list[str],
    receiver: str,
    live: bool,
    stop_on_error: bool = False,
    batch_delay: float = 0.5,
    event_cb: EventCallback | None = None,
    **cycle_kwargs,
) -> dict[str, Any]:
    receiver = normalize_slot(receiver)
    normalized: list[str] = []
    seen: set[str] = set()

    for raw in senders:
        slot = normalize_slot(raw)
        if slot == receiver or slot in seen:
            continue
        seen.add(slot)
        normalized.append(slot)

    _emit(event_cb, "batch_start", receiver=receiver, senders=list(normalized), total=len(normalized), live=live)
    results: list[dict[str, Any]] = []
    for index, sender in enumerate(normalized, 1):
        _emit(event_cb, "sender_start", sender=sender, receiver=receiver, index=index, total=len(normalized))
        result = run_cycle(
            cfg=cfg,
            sessions=sessions,
            auths=auths,
            sender=sender,
            receiver=receiver,
            live=live,
            event_cb=event_cb,
            **cycle_kwargs,
        )
        results.append({
            "sender": sender,
            "ok": bool(result.get("ok")),
            "failed_step": result.get("failed_step"),
            "life_mail_seq": result.get("life_mail_seq"),
            "steps": result.get("steps", []),
        })
        _emit(event_cb, "sender_done", sender=sender, receiver=receiver, index=index, total=len(normalized), ok=bool(result.get("ok")), failed_step=result.get("failed_step"))
        if not result.get("ok") and stop_on_error:
            break
        if live and index < len(normalized):
            time.sleep(max(0.0, batch_delay))

    output = {
        "ok": all(item["ok"] for item in results) if results else True,
        "live": live,
        "receiver": receiver,
        "requested_senders": normalized,
        "completed": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }
    _emit(event_cb, "batch_done", **{k: output[k] for k in ("ok", "receiver", "completed", "failed")})
    return output
