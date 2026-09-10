from __future__ import annotations

import argparse
import getpass
import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from mwoif import __app_name__, __version__
from mwoif.application.account_manager import AccountManager
from mwoif.core.config import load_config
from mwoif.core.logger import configure_logging, get_logger
from mwoif.core.redact import redact_mapping
from mwoif.domain.enums import Source
from mwoif.domain.errors import MwoifHeartError
from mwoif.storage.vault import CredentialVault, generate_master_key_b64


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))


def _prompt_secret(prompt: str) -> str:
    value = getpass.getpass(prompt)
    if not value:
        raise ValueError("secret value is empty")
    return value


def _source(value: str) -> Source:
    return Source.WEB if value == Source.WEB.value else Source.LOCAL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mwoif-heart-v3", description="M WOIF Heart V3 local core")
    parser.add_argument("--env-file", default=None, help="Path to .env file. Defaults to project .env")
    parser.add_argument("--json-log", action="store_true", help="Use JSON line logs")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="Show V3 version")
    sub.add_parser("config", help="Show redacted configuration")
    sub.add_parser("db-test", help="Test MariaDB connection only")
    sub.add_parser("schema-status", help="Check heart_* table status")
    sub.add_parser("settings-init", help="Insert default heart_settings values")
    sub.add_parser("health", help="Run DB + schema health check")
    sub.add_parser("vault-keygen", help="Generate a local AES-256-GCM master key for .env")

    p = sub.add_parser("receiver-add", help="Add or update one receiver account identity without storing password")
    p.add_argument("--email", required=True)
    p.add_argument("--source", choices=["local", "web"], default="local")
    p.add_argument("--u-id", type=int, default=None)

    p = sub.add_parser("sender-add", help="Add or update one manually provisioned sender")
    p.add_argument("--label", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--store-credential", action="store_true", help="Prompt and store encrypted sender credential")
    p.add_argument("--use-shared-password", action="store_true", help="Store sender identity but use shared sender password at login")
    p.add_argument("--shared-credential", default="default")

    p = sub.add_parser("sender-vault-set", help="Prompt and store encrypted credential for an existing sender")
    p.add_argument("--hs-id", type=int, required=True)
    p.add_argument("--email", required=True)

    p = sub.add_parser("job-create", help="Create a heart job and temporary receiver credential vault")
    p.add_argument("--receiver-email", required=True)
    p.add_argument("--requested-hearts", type=int, required=True)
    p.add_argument("--source", choices=["local", "web"], default="local")
    p.add_argument("--u-id", type=int, default=None)

    p = sub.add_parser("round-create", help="Create one storage-only round checkpoint")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)
    p.add_argument("--hr-id", type=int, required=True)
    p.add_argument("--sequence-no", type=int, default=1)

    p = sub.add_parser("accounts-list", help="List receivers and senders without secrets")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("jobs-list", help="List recent heart jobs")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("events-tail", help="Show recent structured events")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("storage-smoke", help="Create receiver, sender, job, round, event using dummy or prompted secrets")
    p.add_argument("--receiver-email", required=True)
    p.add_argument("--sender-email", required=True)
    p.add_argument("--sender-label", default="Sender001")
    p.add_argument("--requested-hearts", type=int, default=1)
    p.add_argument("--dummy-secrets", action="store_true", help="Use dummy non-real passwords for DB/vault testing only")
    p.add_argument("--purge-receiver-vault", action="store_true", help="Delete temporary receiver job credential after smoke insert")


    p = sub.add_parser("login-test-receiver", help="Login one receiver from a job vault and run initMember3")
    p.add_argument("--hj-id", type=int, required=True, help="Heart job id that owns the temporary receiver credential")

    p = sub.add_parser("login-test-sender", help="Login one sender from sender vault and run initMember3")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("login-test-pair", help="Login receiver(job) and sender, then run initMember3 for both")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-probe-receiver", help="Phase 4.5: try DevPlay login with direct HTTP only, then initMember3")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("http-login-probe-sender", help="Phase 4.5: try one sender login with direct HTTP only, then initMember3")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-probe-pair", help="Phase 4.5: try receiver+sender direct HTTP login, then initMember3 for both")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-record-receiver", help="Phase 4.5.1: record safe browser network metadata for receiver login")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("http-login-record-sender", help="Phase 4.5.1: record safe browser network metadata for one sender login")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-replay-receiver", help="Phase 4.5.2: direct HTTP replay v2 login for receiver, then initMember3")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("http-login-replay-sender", help="Phase 4.5.2: direct HTTP replay v2 login for one sender, then initMember3")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-replay-pair", help="Phase 4.5.2: direct HTTP replay v2 receiver+sender login, then initMember3")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-matrix-receiver", help="Phase 4.5.5: safe direct HTTP replay matrix for receiver, then initMember3 on hit")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("http-login-matrix-sender", help="Phase 4.5.5: safe direct HTTP replay matrix for one sender, then initMember3 on hit")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-matrix-pair", help="Phase 4.5.5: safe direct HTTP replay matrix for receiver+sender, then initMember3 on hit")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-template-capture-receiver", help="Phase 4.5.6: capture private exact browser login request template for receiver")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("http-login-template-capture-sender", help="Phase 4.5.6: capture private exact browser login request template for one sender")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-template-replay-receiver", help="Phase 4.5.6: direct HTTP replay from private exact template for receiver")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("http-login-template-replay-sender", help="Phase 4.5.6: direct HTTP replay from private exact template for one sender")
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-template-replay-pair", help="Phase 4.5.6: direct HTTP exact-template replay receiver+sender, then initMember3")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)

    p = sub.add_parser("http-login-template-reuse-sender", help="Phase 4.5.7: reuse one captured sender template against another sender")
    p.add_argument("--template-hs-id", type=int, required=True, help="Sender id whose private exact template file already exists")
    p.add_argument("--target-hs-id", type=int, required=True, help="Different sender id to login with the reused template")

    p = sub.add_parser("http-login-template-reuse-receiver", help="Phase 4.5.7: reuse one captured receiver template against another receiver job")
    p.add_argument("--template-hr-id", type=int, required=True, help="Receiver id whose private exact template file already exists")
    p.add_argument("--target-hj-id", type=int, required=True, help="Job id holding the target receiver credential")

    def _pair_action(name: str, help_text: str):
        q = sub.add_parser(name, help=help_text)
        q.add_argument("--hj-id", type=int, required=True)
        q.add_argument("--hs-id", type=int, required=True)
        q.add_argument("--live", action="store_true", help="Execute network action. Without this it only previews payload/metadata.")
        return q

    p = _pair_action("friend-list-test", "Phase 4: login pair and list sender friends through Friend gRPC")

    p = _pair_action("friend-add-test", "Phase 4: Sender sends friend request to Receiver")
    p.add_argument("--source-type", type=int, default=2)

    p = _pair_action("friend-accept-test", "Phase 4: Receiver accepts Sender friend request")

    p = _pair_action("heart-send-test", "Phase 4: Sender sends one heart to Receiver")

    p = _pair_action("mailbox-read-test", "Phase 4: Receiver reads mailbox and filters latest life mail from Sender")

    p = _pair_action("heart-receive-test", "Phase 4: Receiver claims one lifeMailBoxSeq")
    p.add_argument("--seq", type=int, required=True)

    p = _pair_action("friend-remove-test", "Phase 4: Remove Receiver/Sender friendship")
    p.add_argument("--actor", choices=["sender", "receiver"], default="sender")



    p = sub.add_parser("heart-round-run", help="Phase 5.0: run one complete Heart round with one Receiver login and one Sender login")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--hs-id", type=int, required=True)
    p.add_argument("--template-hs-id", type=int, default=None, help="Sender exact-template source id. Defaults to hs-id or MWOIF_HEART_ROUND_TEMPLATE_SENDER_HS_ID")
    p.add_argument("--template-hr-id", type=int, default=None, help="Receiver exact-template source id. Defaults to job hr-id or MWOIF_HEART_ROUND_TEMPLATE_RECEIVER_HR_ID")
    p.add_argument("--sequence-no", type=int, default=None, help="Optional fixed round sequence. Defaults to next sequence for the job")
    p.add_argument("--source-type", type=int, default=2)
    p.add_argument("--mailbox-attempts", type=int, default=None)
    p.add_argument("--mailbox-delay-seconds", type=float, default=None)
    p.add_argument("--verbose-actions", action="store_true", help="Include full redacted action details in final JSON")
    p.add_argument("--live", action="store_true", help="Execute the real network round. Without this it prints a plan only")


    sub.add_parser("prod-schema-status", help="Phase 5.1: check production-local tables")
    sub.add_parser("prod-schema-apply", help="Phase 5.1: apply production-local DB migration")

    p = sub.add_parser("prod-status", help="Phase 5.1: production dashboard counts")
    p.add_argument("--hj-id", type=int, default=None)

    p = sub.add_parser("sender-shared-password-set", help="Phase 5.1: store one encrypted shared password for sender pool")
    p.add_argument("--name", default="default")

    p = sub.add_parser("sender-import-pattern", help="Phase 5.1: import existing sender emails by pattern using shared password")
    p.add_argument("--prefix", required=True, help="Example: mwoifheart")
    p.add_argument("--start", type=int, required=True, help="Example: 1")
    p.add_argument("--end", type=int, required=True, help="Example: 1000")
    p.add_argument("--width", type=int, default=5, help="Example: 5 -> 00001")
    p.add_argument("--domain", default="gmail.com")
    p.add_argument("--label-prefix", default="Sender")
    p.add_argument("--shared-credential", default="default")

    p = sub.add_parser("sender-eligible", help="Phase 5.1: count senders eligible for one job receiver")
    p.add_argument("--hj-id", type=int, required=True)

    p = sub.add_parser("heart-job-run", help="Phase 5.1: run one job with random sender replacement and pair cooldown")
    p.add_argument("--hj-id", type=int, required=True)
    p.add_argument("--template-hs-id", type=int, default=None)
    p.add_argument("--template-hr-id", type=int, default=None)
    p.add_argument("--source-type", type=int, default=2)
    p.add_argument("--max-rounds", type=int, default=None, help="Safety cap for this local run")
    p.add_argument("--lease-seconds", type=int, default=None)
    p.add_argument("--cooldown-seconds", type=int, default=None)
    p.add_argument("--mailbox-attempts", type=int, default=None)
    p.add_argument("--mailbox-delay-seconds", type=float, default=None)
    p.add_argument("--live", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "health"

    try:
        config = load_config(args.env_file)
        configure_logging(config.log_level, json_lines=args.json_log)
        log = get_logger("cli")

        if command == "version":
            _print_json({"app": __app_name__, "version": __version__})
            return 0

        if command == "vault-keygen":
            _print_json(
                {
                    "ok": True,
                    "set_this_in_env": "MWOIF_HEART_MASTER_KEY_B64",
                    "value": generate_master_key_b64(),
                    "warning": "Do not commit .env or share this value",
                    "secretOutput": "LOCAL_ONLY",
                }
            )
            return 0

        if command == "config":
            _print_json(config.redacted())
            return 0

        from mwoif.application.bootstrap import build_database, build_repository, check_health, initialize_settings

        if command == "db-test":
            db_info = build_database(config).ping()
            _print_json({"ok": True, "database": db_info, "secretOutput": "NONE"})
            return 0

        if command == "schema-status":
            tables = build_database(config).check_heart_tables()
            missing = [item.table for item in tables if not item.exists]
            _print_json(
                {
                    "ok": not missing,
                    "tables": [asdict(item) for item in tables],
                    "missing": missing,
                    "secretOutput": "NONE",
                }
            )
            return 0 if not missing else 2

        if command == "settings-init":
            initialize_settings(config)
            log.info({"event": "SETTINGS_INIT_OK", "secretOutput": "NONE"})
            _print_json({"ok": True, "secretOutput": "NONE"})
            return 0

        if command == "health":
            report = check_health(config)
            _print_json(
                {
                    "ok": report.ok,
                    "database": report.database,
                    "tables": [asdict(item) for item in report.tables],
                    "missing": report.missing_tables,
                    "dashboard": report.dashboard,
                    "secretOutput": "NONE",
                }
            )
            return 0 if report.ok else 2

        repo = build_repository(config)

        if command == "receiver-add":
            manager = AccountManager(repo)
            receiver = manager.ensure_receiver(args.email, source=_source(args.source), u_id=args.u_id)
            repo.log_event(event_type="RECEIVER_UPSERT", hr_id=receiver.hr_id, success=True, detail="receiver identity stored")
            _print_json({"ok": True, "receiver": asdict(receiver), "secretOutput": "NONE"})
            return 0

        if command == "sender-add":
            needs_vault = bool(args.store_credential or args.use_shared_password)
            manager = AccountManager(repo, CredentialVault(config.vault) if needs_vault else None)
            sender = manager.ensure_sender(args.label, args.email)
            credential_stored = False
            uses_shared_password = False
            if args.store_credential:
                password = _prompt_secret("Sender password: ")
                manager.store_sender_credential(sender.hs_id, email=args.email, password=password)
                credential_stored = True
            elif args.use_shared_password:
                manager.store_sender_identity_with_shared_password(sender.hs_id, email=args.email, shared_credential=args.shared_credential)
                credential_stored = True
                uses_shared_password = True
            repo.log_event(event_type="SENDER_UPSERT", hs_id=sender.hs_id, success=True, detail="sender identity stored")
            _print_json(
                {
                    "ok": True,
                    "sender": asdict(sender),
                    "credential_stored": credential_stored,
                    "uses_shared_password": uses_shared_password,
                    "shared_credential": args.shared_credential if uses_shared_password else None,
                    "secretOutput": "NONE",
                }
            )
            return 0

        if command == "sender-vault-set":
            manager = AccountManager(repo, CredentialVault(config.vault))
            password = _prompt_secret("Sender password: ")
            manager.store_sender_credential(args.hs_id, email=args.email, password=password)
            repo.log_event(event_type="SENDER_VAULT_SET", hs_id=args.hs_id, success=True, detail="sender credential updated")
            _print_json({"ok": True, "hs_id": args.hs_id, "credential_stored": True, "secretOutput": "NONE"})
            return 0

        if command == "job-create":
            manager = AccountManager(repo, CredentialVault(config.vault))
            receiver = manager.ensure_receiver(args.receiver_email, source=_source(args.source), u_id=args.u_id)
            password = _prompt_secret("Receiver password: ")
            job = manager.create_job_with_receiver_credential(
                hr_id=receiver.hr_id,
                requested_hearts=args.requested_hearts,
                email=args.receiver_email,
                password=password,
                source=_source(args.source),
                u_id=args.u_id,
            )
            repo.log_event(event_type="JOB_CREATED", hj_id=job.hj_id, hr_id=receiver.hr_id, success=True, detail="job created")
            _print_json({"ok": True, "receiver": asdict(receiver), "job": asdict(job), "secretOutput": "NONE"})
            return 0

        if command == "round-create":
            round_row = repo.create_round(
                hj_id=args.hj_id,
                hs_id=args.hs_id,
                hr_id=args.hr_id,
                sequence_no=args.sequence_no,
            )
            repo.log_event(
                event_type="ROUND_CREATED",
                hj_id=args.hj_id,
                hround_id=int(round_row["hround_id"]),
                hs_id=args.hs_id,
                hr_id=args.hr_id,
                step="CREATED",
                success=True,
                detail="storage checkpoint created",
            )
            _print_json({"ok": True, "round": round_row, "secretOutput": "NONE"})
            return 0

        if command == "accounts-list":
            _print_json(
                {
                    "ok": True,
                    "receivers": repo.list_receivers(limit=args.limit),
                    "senders": repo.list_senders(limit=args.limit),
                    "secretOutput": "NONE",
                }
            )
            return 0

        if command == "jobs-list":
            _print_json({"ok": True, "jobs": repo.list_jobs(limit=args.limit), "secretOutput": "NONE"})
            return 0

        if command == "events-tail":
            _print_json({"ok": True, "events": repo.tail_events(limit=args.limit), "secretOutput": "NONE"})
            return 0

        if command == "storage-smoke":
            vault = CredentialVault(config.vault)
            manager = AccountManager(repo, vault)
            receiver = manager.ensure_receiver(args.receiver_email)
            sender = manager.ensure_sender(args.sender_label, args.sender_email)
            if args.dummy_secrets:
                receiver_password = "dummy-receiver-password-not-real"
                sender_password = "dummy-sender-password-not-real"
            else:
                receiver_password = _prompt_secret("Receiver password: ")
                sender_password = _prompt_secret("Sender password: ")
            manager.store_sender_credential(sender.hs_id, email=args.sender_email, password=sender_password)
            job = manager.create_job_with_receiver_credential(
                hr_id=receiver.hr_id,
                requested_hearts=args.requested_hearts,
                email=args.receiver_email,
                password=receiver_password,
            )
            round_row = manager.create_round(hj_id=job.hj_id, hs_id=sender.hs_id, hr_id=receiver.hr_id, sequence_no=1)
            repo.log_event(
                event_type="STORAGE_SMOKE_OK",
                hj_id=job.hj_id,
                hround_id=int(round_row["hround_id"]),
                hs_id=sender.hs_id,
                hr_id=receiver.hr_id,
                step="CREATED",
                success=True,
                detail="receiver/sender/job/round/vault storage smoke test passed",
            )
            receiver_vault_purged = False
            if args.purge_receiver_vault:
                repo.purge_receiver_job_vault(hj_id=job.hj_id)
                receiver_vault_purged = True
            _print_json(
                {
                    "ok": True,
                    "receiver": asdict(receiver),
                    "sender": asdict(sender),
                    "job": asdict(job),
                    "round": {
                        "hround_id": int(round_row["hround_id"]),
                        "status": round_row.get("status"),
                        "current_step": round_row.get("current_step"),
                    },
                    "vault": {
                        "sender_credential_stored": True,
                        "receiver_job_credential_stored": not receiver_vault_purged,
                        "receiver_job_credential_purged": receiver_vault_purged,
                    },
                    "secretOutput": "NONE",
                }
            )
            return 0


        if command in {"login-test-receiver", "login-test-sender", "login-test-pair"}:
            from mwoif.application.login_session_manager import LoginSessionManager

            vault = CredentialVault(config.vault)
            manager = LoginSessionManager(config, repo, vault)

            def event(text: str) -> None:
                print(text)

            if command == "login-test-receiver":
                result = manager.login_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": True, "receiver": result.public_dict(), "secretOutput": "NONE"})
                return 0

            if command == "login-test-sender":
                result = manager.login_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": True, "sender": result.public_dict(), "secretOutput": "NONE"})
                return 0

            receiver = manager.login_receiver_for_job(hj_id=args.hj_id, event_cb=event)
            sender = manager.login_sender(hs_id=args.hs_id, event_cb=event)
            _print_json(
                {
                    "ok": True,
                    "receiver": receiver.public_dict(),
                    "sender": sender.public_dict(),
                    "secretOutput": "NONE",
                }
            )
            return 0

        if command in {
            "http-login-probe-receiver",
            "http-login-probe-sender",
            "http-login-probe-pair",
            "http-login-record-receiver",
            "http-login-record-sender",
            "http-login-replay-receiver",
            "http-login-replay-sender",
            "http-login-replay-pair",
            "http-login-matrix-receiver",
            "http-login-matrix-sender",
            "http-login-matrix-pair",
            "http-login-template-capture-receiver",
            "http-login-template-capture-sender",
            "http-login-template-replay-receiver",
            "http-login-template-replay-sender",
            "http-login-template-replay-pair",
            "http-login-template-reuse-sender",
            "http-login-template-reuse-receiver",
        }:
            from mwoif.application.login_session_manager import LoginSessionManager

            vault = CredentialVault(config.vault)
            manager = LoginSessionManager(config, repo, vault)

            def event(text: str) -> None:
                print(text)

            if command == "http-login-record-receiver":
                result = manager.http_record_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-record-sender":
                result = manager.http_record_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-replay-receiver":
                result = manager.http_replay_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-replay-sender":
                result = manager.http_replay_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-replay-pair":
                receiver = manager.http_replay_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                sender = manager.http_replay_sender(hs_id=args.hs_id, event_cb=event)
                ok = bool(receiver.get("ok")) and bool(sender.get("ok"))
                _print_json({"ok": ok, "receiver": receiver, "sender": sender, "secretOutput": "NONE"})
                return 0 if ok else 1

            if command == "http-login-template-capture-receiver":
                result = manager.http_template_capture_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-template-capture-sender":
                result = manager.http_template_capture_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-template-replay-receiver":
                result = manager.http_template_replay_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-template-replay-sender":
                result = manager.http_template_replay_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-template-replay-pair":
                receiver = manager.http_template_replay_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                sender = manager.http_template_replay_sender(hs_id=args.hs_id, event_cb=event)
                ok = bool(receiver.get("ok")) and bool(sender.get("ok"))
                _print_json({"ok": ok, "receiver": receiver, "sender": sender, "secretOutput": "NONE"})
                return 0 if ok else 1

            if command == "http-login-template-reuse-sender":
                result = manager.http_template_reuse_sender(
                    template_hs_id=args.template_hs_id,
                    target_hs_id=args.target_hs_id,
                    event_cb=event,
                )
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-template-reuse-receiver":
                result = manager.http_template_reuse_receiver_for_job(
                    template_hr_id=args.template_hr_id,
                    target_hj_id=args.target_hj_id,
                    event_cb=event,
                )
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-matrix-receiver":
                result = manager.http_matrix_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-matrix-sender":
                result = manager.http_matrix_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-matrix-pair":
                receiver = manager.http_matrix_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                sender = manager.http_matrix_sender(hs_id=args.hs_id, event_cb=event)
                ok = bool(receiver.get("ok")) and bool(sender.get("ok"))
                _print_json({"ok": ok, "receiver": receiver, "sender": sender, "secretOutput": "NONE"})
                return 0 if ok else 1

            if command == "http-login-probe-receiver":
                result = manager.http_probe_receiver_for_job(hj_id=args.hj_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "receiver": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            if command == "http-login-probe-sender":
                result = manager.http_probe_sender(hs_id=args.hs_id, event_cb=event)
                _print_json({"ok": bool(result.get("ok")), "sender": result, "secretOutput": "NONE"})
                return 0 if result.get("ok") else 1

            receiver = manager.http_probe_receiver_for_job(hj_id=args.hj_id, event_cb=event)
            sender = manager.http_probe_sender(hs_id=args.hs_id, event_cb=event)
            ok = bool(receiver.get("ok")) and bool(sender.get("ok"))
            _print_json({"ok": ok, "receiver": receiver, "sender": sender, "secretOutput": "NONE"})
            return 0 if ok else 1


        if command == "prod-schema-status":
            status = repo.check_production_tables()
            missing = [row["table"] for row in status if not row["exists"]]
            _print_json({"ok": not missing, "tables": status, "missing": missing, "secretOutput": "NONE"})
            return 0 if not missing else 2

        if command == "prod-schema-apply":
            result = repo.apply_production_schema()
            _print_json(result)
            return 0 if result.get("ok") else 1

        if command == "prod-status":
            _print_json(repo.production_dashboard(hj_id=args.hj_id))
            return 0

        if command == "sender-shared-password-set":
            manager = AccountManager(repo, CredentialVault(config.vault))
            password = _prompt_secret("Shared sender password: ")
            manager.store_shared_sender_password(credential_name=args.name, password=password)
            repo.log_event(event_type="SENDER_SHARED_PASSWORD_SET", success=True, detail="shared sender credential updated")
            _print_json({"ok": True, "credential_name": args.name, "credential_stored": True, "secretOutput": "NONE"})
            return 0

        if command == "sender-import-pattern":
            manager = AccountManager(repo, CredentialVault(config.vault))
            result = manager.import_sender_pattern(
                prefix=args.prefix,
                domain=args.domain,
                start=args.start,
                end=args.end,
                width=args.width,
                label_prefix=args.label_prefix,
                shared_credential=args.shared_credential,
                store_shared_identity=True,
            )
            repo.log_event(event_type="SENDER_PATTERN_IMPORT", success=True, detail=f"imported sender pattern count={result.get('count')}")
            _print_json(result)
            return 0

        if command == "sender-eligible":
            job = repo.get_job(args.hj_id)
            if not job:
                raise MwoifHeartError(f"heart job not found: hj_id={args.hj_id}", stage="SENDER_ELIGIBLE")
            counts = repo.eligible_sender_counts(hj_id=args.hj_id, hr_id=int(job["hr_id"]))
            _print_json({"ok": True, "hj_id": args.hj_id, "hr_id": int(job["hr_id"]), "eligible": counts, "secretOutput": "NONE"})
            return 0

        if command == "heart-job-run":
            from mwoif.application.production_job_runner import ProductionJobRunner

            manager = ProductionJobRunner(config, repo, CredentialVault(config.vault))

            def event(text: str) -> None:
                print(text)

            result = manager.run_job(
                hj_id=args.hj_id,
                live=args.live,
                template_hs_id=args.template_hs_id,
                template_hr_id=args.template_hr_id,
                source_type=args.source_type,
                max_rounds=args.max_rounds,
                lease_seconds=args.lease_seconds,
                cooldown_seconds=args.cooldown_seconds,
                mailbox_attempts=args.mailbox_attempts,
                mailbox_delay_seconds=args.mailbox_delay_seconds,
                event_cb=event,
            )
            _print_json(result)
            return 0 if result.get("ok") else 1


        if command == "heart-round-run":
            from mwoif.application.heart_round_runner import HeartRoundRunner

            manager = HeartRoundRunner(config, repo, CredentialVault(config.vault))

            def event(text: str) -> None:
                print(text)

            result = manager.run_one(
                hj_id=args.hj_id,
                hs_id=args.hs_id,
                live=args.live,
                source_type=args.source_type,
                template_hs_id=args.template_hs_id,
                template_hr_id=args.template_hr_id,
                sequence_no=args.sequence_no,
                mailbox_attempts=args.mailbox_attempts,
                mailbox_delay_seconds=args.mailbox_delay_seconds,
                verbose_actions=args.verbose_actions,
                event_cb=event,
            )
            _print_json(result)
            return 0 if result.get("ok") else 1

        if command in {
            "friend-list-test",
            "friend-add-test",
            "friend-accept-test",
            "heart-send-test",
            "mailbox-read-test",
            "heart-receive-test",
            "friend-remove-test",
        }:
            from mwoif.application.heart_action_manager import HeartActionManager

            manager = HeartActionManager(config, repo, CredentialVault(config.vault))

            def event(text: str) -> None:
                print(text)

            if command == "friend-list-test":
                result = manager.friend_list_sender(hj_id=args.hj_id, hs_id=args.hs_id, live=args.live, event_cb=event)
            elif command == "friend-add-test":
                result = manager.friend_add(hj_id=args.hj_id, hs_id=args.hs_id, source_type=args.source_type, live=args.live, event_cb=event)
            elif command == "friend-accept-test":
                result = manager.friend_accept(hj_id=args.hj_id, hs_id=args.hs_id, live=args.live, event_cb=event)
            elif command == "heart-send-test":
                result = manager.heart_send(hj_id=args.hj_id, hs_id=args.hs_id, live=args.live, event_cb=event)
            elif command == "mailbox-read-test":
                result = manager.mailbox_read(hj_id=args.hj_id, hs_id=args.hs_id, live=args.live, event_cb=event)
            elif command == "heart-receive-test":
                result = manager.heart_receive(hj_id=args.hj_id, hs_id=args.hs_id, seq=args.seq, live=args.live, event_cb=event)
            elif command == "friend-remove-test":
                result = manager.friend_remove(hj_id=args.hj_id, hs_id=args.hs_id, actor=args.actor, live=args.live, event_cb=event)
            else:
                raise AssertionError(command)

            _print_json(result)
            return 0 if result.get("ok") else 1

        parser.error(f"Unknown command: {command}")
        return 2

    except MwoifHeartError as exc:
        _print_json({"ok": False, "error": exc.to_info(), "secretOutput": "NONE"})
        return 1
    except Exception as exc:
        _print_json({"ok": False, "error": {"code": "UNHANDLED", "message": str(exc)}, "secretOutput": "NONE"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
