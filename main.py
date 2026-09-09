#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mwoif.accounts import (
    existing_slots,
    import_pair,
    pair_status,
)
from mwoif.auth_store import AuthStore
from mwoif.config import load_config
from mwoif.endpoint_registry import public_registry
from mwoif.friend_grpc import list_friends
from mwoif.grpc_metadata import load_metadata_file
from mwoif.heart_mailbox import preview_mail_list
from mwoif.session_store import SessionStore
from mwoif.slots import normalize_slot
from mwoif.workflow import cycle_plan, run_batch, run_cycle

VERSION = "7.0-clean-multi-account"


def root_dir() -> Path:
    return Path(__file__).resolve().parent


def dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def get_context():
    cfg = load_config(root_dir())
    return cfg, SessionStore(cfg.root), AuthStore(cfg.root)


def parse_slot_selector(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for raw in values:
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue

            match = __import__("re").fullmatch(r"(\d+)-(\d+)", token)
            if match:
                start, end = map(int, match.groups())
                step = 1 if end >= start else -1
                expanded = [str(i) for i in range(start, end + step, step)]
            else:
                expanded = [token]

            for item in expanded:
                slot = normalize_slot(item)
                if slot not in seen:
                    seen.add(slot)
                    out.append(slot)
    return out


def cmd_doctor(_):
    cfg, sessions, auths = get_context()
    deps = {}
    for name in ("requests", "grpc"):
        try:
            __import__(name)
            deps[name] = True
        except Exception:
            deps[name] = False

    slots = existing_slots(cfg.root)
    dump({
        "ok": all(deps.values()),
        "version": VERSION,
        "dependencies": deps,
        "receiver_default": cfg.workflow.get("receiver_slot", "A"),
        "accounts_ready": [
            pair_status(slot, sessions, auths)
            for slot in slots
        ],
    })


def cmd_accounts(_):
    cfg, sessions, auths = get_context()
    slots = existing_slots(cfg.root)
    dump({
        "ok": True,
        "count": len(slots),
        "accounts": [
            pair_status(slot, sessions, auths)
            for slot in slots
        ],
    })


def cmd_account_import(args):
    cfg, sessions, auths = get_context()
    result = import_pair(
        slot=args.slot,
        session_file=Path(args.session),
        auth_file=Path(args.auth),
        sessions=sessions,
        auths=auths,
    )
    dump({"ok": True, "action": "account-import", **result})


def cmd_import_dir(args):
    cfg, sessions, auths = get_context()
    base = Path(args.directory)
    results = []

    if not base.is_dir():
        raise ValueError(f"not a directory: {base}")

    for child in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name):
        session_file = child / "session.json"
        auth_file = child / "auth.json"
        if not session_file.is_file() or not auth_file.is_file():
            continue
        try:
            item = import_pair(
                slot=child.name,
                session_file=session_file,
                auth_file=auth_file,
                sessions=sessions,
                auths=auths,
            )
            results.append({"slot": item["slot"], "ok": True})
        except Exception as exc:
            results.append({
                "slot": child.name,
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc),
            })

    dump({
        "ok": all(x["ok"] for x in results) if results else False,
        "action": "import-dir",
        "imported": sum(1 for x in results if x["ok"]),
        "failed": sum(1 for x in results if not x["ok"]),
        "results": results,
    })


def cmd_endpoints(_):
    dump({"ok": True, "version": VERSION, "endpoints": public_registry()})


def cmd_friend_list(args):
    cfg, sessions, auths = get_context()
    slot = normalize_slot(args.slot)
    status = pair_status(slot, sessions, auths)
    if not status["ready"]:
        raise ValueError(f"slot {slot} is not ready")

    auth = auths.load(slot)
    assert auth is not None
    result = list_friends(
        cfg=cfg,
        slot=slot,
        auth=auth,
        request_body=b"",
        extra_metadata=load_metadata_file(args.metadata_file),
        timeout=args.timeout,
    )
    result["slot"] = slot
    dump(result)


def cmd_mailbox(args):
    cfg, sessions, auths = get_context()
    receiver = normalize_slot(args.receiver)
    sender = normalize_slot(args.sender)

    receiver_session = sessions.load(receiver)
    receiver_auth = auths.load(receiver)
    sender_session = sessions.load(sender)
    if not receiver_session or not receiver_session.established:
        raise ValueError(f"receiver {receiver} Session not ready")
    if not receiver_auth or not receiver_auth.ready:
        raise ValueError(f"receiver {receiver} Auth not ready")
    if not sender_session or not sender_session.established:
        raise ValueError(f"sender {sender} Session not ready")

    result = preview_mail_list(
        cfg=cfg,
        slot=receiver,
        session=receiver_session,
        auth=receiver_auth,
        from_slot=sender,
        from_member_seq=int(sender_session.member_seq),
        live=args.live,
        timeout=args.timeout,
    )
    dump(result)


def _workflow_options(cfg, args):
    wf = cfg.workflow
    return {
        "source_type": args.source_type or int(wf.get("friend_source_type", 2)),
        "grpc_timeout": float(wf.get("grpc_timeout_seconds", 12)),
        "ds_timeout": float(wf.get("ds_timeout_seconds", 20)),
        "mailbox_retries": int(wf.get("mailbox_retries", 6)),
        "mailbox_delay": float(wf.get("mailbox_delay_seconds", 1.0)),
        "step_delay": float(wf.get("step_delay_seconds", 0.35)),
    }


def cmd_cycle(args):
    cfg, sessions, auths = get_context()
    receiver = normalize_slot(
        args.receiver or cfg.workflow.get("receiver_slot", "A")
    )
    sender = normalize_slot(args.sender)

    result = run_cycle(
        cfg=cfg,
        sessions=sessions,
        auths=auths,
        sender=sender,
        receiver=receiver,
        live=args.live,
        **_workflow_options(cfg, args),
    )
    dump(result)


def cmd_batch(args):
    cfg, sessions, auths = get_context()
    receiver = normalize_slot(
        args.receiver or cfg.workflow.get("receiver_slot", "A")
    )

    if args.all:
        senders = [
            slot
            for slot in existing_slots(cfg.root)
            if slot != receiver
            and pair_status(slot, sessions, auths)["ready"]
        ]
    else:
        senders = parse_slot_selector(args.senders)
        if not senders:
            raise ValueError("provide sender slots/ranges or use --all")

    result = run_batch(
        cfg=cfg,
        sessions=sessions,
        auths=auths,
        senders=senders,
        receiver=receiver,
        live=args.live,
        stop_on_error=args.stop_on_error,
        batch_delay=float(cfg.workflow.get("batch_delay_seconds", 0.5)),
        **_workflow_options(cfg, args),
    )
    dump(result)


def _menu_accounts(cfg, sessions, auths):
    slots = existing_slots(cfg.root)
    print("\nAccounts")
    print("-" * 62)
    print(f"{'SLOT':<12}{'READY':<8}{'MID':<18}{'MEMBER_SEQ'}")
    for slot in slots:
        st = pair_status(slot, sessions, auths)
        print(
            f"{slot:<12}{str(st['ready']):<8}"
            f"{st['mid']:<18}{st['member_seq']}"
        )
    if not slots:
        print("(none)")


def cmd_menu(_):
    cfg, sessions, auths = get_context()
    default_receiver = normalize_slot(
        cfg.workflow.get("receiver_slot", "A")
    )

    while True:
        print("\n=== MWOIF HEART ===")
        print(f"Receiver default: {default_receiver}")
        print("1) Accounts")
        print("2) Import account")
        print("3) Run one sender -> receiver")
        print("4) Run selected senders -> receiver")
        print("5) Run ALL ready senders -> receiver")
        print("0) Exit")
        choice = input("> ").strip()

        if choice == "0":
            return
        if choice == "1":
            _menu_accounts(cfg, sessions, auths)
            continue
        if choice == "2":
            slot = input("Slot (A / 1 / 2 / S001 ...): ").strip()
            session_file = input("Session JSON file: ").strip().strip('"')
            auth_file = input("Auth JSON file: ").strip().strip('"')
            try:
                result = import_pair(
                    slot=slot,
                    session_file=Path(session_file),
                    auth_file=Path(auth_file),
                    sessions=sessions,
                    auths=auths,
                )
                print(f"PASS: {result['slot']}")
            except Exception as exc:
                print(f"FAIL: {type(exc).__name__}: {exc}")
            continue

        if choice in {"3", "4", "5"}:
            receiver = input(
                f"Receiver [{default_receiver}]: "
            ).strip() or default_receiver

            if choice == "3":
                senders = [input("Sender slot: ").strip()]
            elif choice == "4":
                raw = input("Senders (e.g. 1,2,5-10): ").strip()
                senders = parse_slot_selector([raw])
            else:
                receiver_norm = normalize_slot(receiver)
                senders = [
                    slot
                    for slot in existing_slots(cfg.root)
                    if slot != receiver_norm
                    and pair_status(slot, sessions, auths)["ready"]
                ]

            print(
                f"Will run {len(senders)} sender(s) -> "
                f"{normalize_slot(receiver)}"
            )
            confirm = input("Type LIVE to execute: ").strip().upper()
            if confirm != "LIVE":
                print("Cancelled")
                continue

            result = run_batch(
                cfg=cfg,
                sessions=sessions,
                auths=auths,
                senders=senders,
                receiver=receiver,
                live=True,
                stop_on_error=False,
                batch_delay=float(
                    cfg.workflow.get("batch_delay_seconds", 0.5)
                ),
                source_type=int(
                    cfg.workflow.get("friend_source_type", 2)
                ),
                grpc_timeout=float(
                    cfg.workflow.get("grpc_timeout_seconds", 12)
                ),
                ds_timeout=float(
                    cfg.workflow.get("ds_timeout_seconds", 20)
                ),
                mailbox_retries=int(
                    cfg.workflow.get("mailbox_retries", 6)
                ),
                mailbox_delay=float(
                    cfg.workflow.get("mailbox_delay_seconds", 1.0)
                ),
                step_delay=float(
                    cfg.workflow.get("step_delay_seconds", 0.35)
                ),
            )
            print(
                f"Done: PASS={result['completed']} "
                f"FAIL={result['failed']}"
            )
            for item in result["results"]:
                suffix = (
                    ""
                    if item["ok"]
                    else f" ({item.get('failed_step')})"
                )
                print(
                    f"  {item['sender']}: "
                    f"{'PASS' if item['ok'] else 'FAIL'}{suffix}"
                )
            continue

        print("Unknown choice")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="MWOIF Heart clean multi-account runner",
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    q = sp.add_parser("menu")
    q.set_defaults(func=cmd_menu)

    q = sp.add_parser("doctor")
    q.set_defaults(func=cmd_doctor)

    q = sp.add_parser("accounts")
    q.set_defaults(func=cmd_accounts)

    q = sp.add_parser("account-import")
    q.add_argument("slot")
    q.add_argument("--session", required=True)
    q.add_argument("--auth", required=True)
    q.set_defaults(func=cmd_account_import)

    q = sp.add_parser("import-dir")
    q.add_argument("directory", nargs="?", default="imports")
    q.set_defaults(func=cmd_import_dir)

    q = sp.add_parser("endpoints")
    q.set_defaults(func=cmd_endpoints)

    q = sp.add_parser("friend-list")
    q.add_argument("slot")
    q.add_argument("--timeout", type=float, default=12.0)
    q.add_argument("--metadata-file", default="")
    q.set_defaults(func=cmd_friend_list)

    q = sp.add_parser("mailbox")
    q.add_argument("receiver")
    q.add_argument("sender")
    q.add_argument("--timeout", type=float, default=20.0)
    q.add_argument("--live", action="store_true")
    q.set_defaults(func=cmd_mailbox)

    q = sp.add_parser("cycle")
    q.add_argument("sender")
    q.add_argument("--receiver", default="")
    q.add_argument("--source-type", type=int, choices=(1, 2, 3, 4))
    q.add_argument("--live", action="store_true")
    q.set_defaults(func=cmd_cycle)

    q = sp.add_parser("batch")
    q.add_argument("senders", nargs="*")
    q.add_argument("--receiver", default="")
    q.add_argument("--all", action="store_true")
    q.add_argument("--source-type", type=int, choices=(1, 2, 3, 4))
    q.add_argument("--stop-on-error", action="store_true")
    q.add_argument("--live", action="store_true")
    q.set_defaults(func=cmd_batch)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        dump({
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        })
        raise SystemExit(2)


if __name__ == "__main__":
    main()
