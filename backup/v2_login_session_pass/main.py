from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from mwoif_v2.config import load_config
from mwoif_v2.credential_vault import Credential, CredentialVault, VaultError
from mwoif_v2.client_identity import ClientIdentity
from mwoif_v2.login_runtime import login_and_bootstrap
from mwoif_v2.runner import V2Runner

ROOT = Path(__file__).resolve().parent


def log(text: str) -> None:
    # Backend messages are designed to contain status only, never raw secrets.
    print(text, flush=True)


def cmd_vault_add(args) -> int:
    password = getpass.getpass("Sender password: ")
    CredentialVault(ROOT).put(args.slot, args.email, password)
    print(f"VAULT OK slot={args.slot} email={args.email} password=ENCRYPTED_DPAPI")
    return 0


def cmd_vault_import(args) -> int:
    count = CredentialVault(ROOT).import_plain_json(Path(args.path))
    print(f"VAULT IMPORT OK count={count} password=ENCRYPTED_DPAPI")
    print("Plain source JSON is not modified; remove it yourself after verifying the vault.")
    return 0


def cmd_vault_list(args) -> int:
    rows = CredentialVault(ROOT).list_public()
    if not rows:
        print("VAULT EMPTY")
    for row in rows:
        print(f"slot={row['slot']} email={row['email']} password=<ENCRYPTED>")
    return 0


def cmd_vault_remove(args) -> int:
    ok = CredentialVault(ROOT).remove(args.slot)
    print("REMOVED" if ok else "NOT_FOUND")
    return 0


def _safe_bool(value: object) -> bool:
    return value not in (None, "")


def _load_test_credential_file(path: Path) -> Credential | None:
    """Load a temporary local receiver credential for test runs only.

    Accepted private shapes:
      {"email":"...", "password":"..."}
      {"receiver":{"email":"...", "password":"..."}}
      {"accounts":[{"slot":"A", "email":"...", "password":"..."}]}

    The file is never printed and should be deleted after the login/session
    bootstrap is stable. Do not commit it or paste it into chat.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise VaultError(f"invalid test credential JSON: {type(exc).__name__}") from exc

    row = None
    if isinstance(data, dict):
        if isinstance(data.get("receiver"), dict):
            row = data["receiver"]
        elif isinstance(data.get("accounts"), list):
            for item in data["accounts"]:
                if isinstance(item, dict) and str(item.get("slot") or "A").upper() == "A":
                    row = item
                    break
        else:
            row = data
    if not isinstance(row, dict):
        raise VaultError("test credential JSON must contain receiver/email/password")

    email = str(row.get("email") or "").strip()
    password = str(row.get("password") or "")
    if not email or not password:
        raise VaultError("test credential JSON has empty email/password")
    return Credential(slot="A", email=email, password=password)


def prompt_receiver(email_arg: str | None, *, cred_file: str | None = None, allow_default_file: bool = False) -> Credential:
    candidates: list[Path] = []
    if cred_file:
        candidates.append(Path(cred_file))
    env_path = str(__import__("os").environ.get("MWOIF_V2_TEST_CREDENTIALS_FILE") or "").strip()
    if env_path:
        candidates.append(Path(env_path))
    if allow_default_file:
        candidates.append(ROOT / "state" / "receiver.test.local.json")

    for candidate in candidates:
        path = candidate if candidate.is_absolute() else ROOT / candidate
        cred = _load_test_credential_file(path)
        if cred is not None:
            print(
                "RECEIVER TEST CREDENTIALS source=LOCAL_JSON "
                f"email_present={_safe_bool(cred.email)} password_present={_safe_bool(cred.password)} secretOutput=NONE"
            )
            return cred

    email = (email_arg or input("Receiver email: ")).strip()
    password = getpass.getpass("Receiver password: ")
    return Credential(slot="A", email=email, password=password)


def cmd_login_test(args) -> int:
    cfg = load_config(ROOT)
    receiver = prompt_receiver(args.email, cred_file=args.creds_file, allow_default_file=True)
    identity = ClientIdentity.load_or_create(ROOT)
    try:
        bundle, auth, session = login_and_bootstrap(
            cfg, ROOT, "A", receiver.email, receiver.password, identity, event_cb=log
        )
    except Exception as exc:
        print(f"LOGIN TEST FAILED {type(exc).__name__}: {exc}")
        return 2
    print("LOGIN TEST PASS")
    print(json.dumps({
        "auth": bundle.public_summary(),
        "session": {
            "member_seq_present": session.member_seq > 0,
            "current_lv": session.current_lv,
            "session_key_present": bool(session.session_key),
            "secret_output": "NONE",
        },
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    cfg = load_config(ROOT)
    receiver = prompt_receiver(args.receiver_email, cred_file=args.receiver_creds_file, allow_default_file=False)
    vault = CredentialVault(ROOT)
    only = {x.strip() for x in (args.sender or []) if x.strip()} or None
    senders = vault.credentials(only_slots=only)
    if not senders:
        print("NO SENDERS: add accounts with vault-add or vault-import first")
        return 2
    runner = V2Runner(cfg, ROOT, event_cb=log)
    result = runner.run_accounts(receiver, senders, live=args.live)
    print("=" * 48)
    print(f"DONE live={args.live} completed={result['completed']} failed={result['failed']} secretOutput=NONE")
    for row in result["results"]:
        print(f"slot={row['slot']} ok={row['ok']} failed_step={row.get('failed_step') or '-'}")
    return 0 if result["ok"] else 3


def build_parser():
    p = argparse.ArgumentParser(description="M WOIF Heart V2 - Email Login + proven V1 Friend/Heart flow")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("vault-add", help="Add/update one sender account (Windows DPAPI encrypted)")
    q.add_argument("slot")
    q.add_argument("email")
    q.set_defaults(fn=cmd_vault_add)

    q = sub.add_parser("vault-import", help="One-time import [{slot,email,password}, ...] then encrypt with DPAPI")
    q.add_argument("path")
    q.set_defaults(fn=cmd_vault_import)

    q = sub.add_parser("vault-list")
    q.set_defaults(fn=cmd_vault_list)

    q = sub.add_parser("vault-remove")
    q.add_argument("slot")
    q.set_defaults(fn=cmd_vault_remove)

    q = sub.add_parser("login-test", help="Receiver email login + initMember3 only; no friend/heart writes")
    q.add_argument("--email")
    q.add_argument("--creds-file", help="Local temporary JSON with receiver email/password; never commit or paste")
    q.set_defaults(fn=cmd_login_test)

    q = sub.add_parser("run", help="Login receiver + each sender, then run the frozen V1 cycle")
    q.add_argument("--receiver-email")
    q.add_argument("--receiver-creds-file", help="Local temporary JSON with receiver email/password")
    q.add_argument("--sender", action="append", help="Run only selected sender slot; repeat option for more")
    q.add_argument("--live", action="store_true", help="Enable proven V1 friend/heart network writes")
    q.set_defaults(fn=cmd_run)
    return p


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.fn(args))
    except (VaultError, KeyboardInterrupt) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
