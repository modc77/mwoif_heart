from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mwoif.core.redact import email_hash, mask_email
from mwoif.domain.enums import Source
from mwoif.domain.errors import ConfigError
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault


@dataclass(frozen=True, slots=True)
class ReceiverRecord:
    hr_id: int
    email_mask: str | None


@dataclass(frozen=True, slots=True)
class SenderRecord:
    hs_id: int
    label: str
    email_mask: str | None


@dataclass(frozen=True, slots=True)
class JobRecord:
    hj_id: int
    hr_id: int
    requested_hearts: int


class AccountManager:
    def __init__(self, repo: HeartRepository, vault: CredentialVault | None = None) -> None:
        self.repo = repo
        self.vault = vault

    def ensure_receiver(self, email: str, *, source: Source = Source.LOCAL, u_id: int | None = None) -> ReceiverRecord:
        receiver = self.repo.upsert_receiver(
            email_hash=email_hash(email),
            email_mask=mask_email(email),
            source=source.value,
            u_id=u_id,
        )
        return ReceiverRecord(hr_id=int(receiver["hr_id"]), email_mask=receiver.get("email_mask"))

    def ensure_sender(self, label: str, email: str) -> SenderRecord:
        sender = self.repo.upsert_sender(
            label=label,
            email_hash=email_hash(email),
            email_mask=mask_email(email),
        )
        return SenderRecord(hs_id=int(sender["hs_id"]), label=str(sender["label"]), email_mask=sender.get("email_mask"))

    def store_sender_credential(self, hs_id: int, *, email: str, password: str) -> None:
        if self.vault is None:
            raise ConfigError("CredentialVault is required", stage="SENDER_VAULT")
        blob = self.vault.encrypt_json({"email": email, "password": password}, aad=f"heart_sender:{hs_id}")
        self.repo.upsert_sender_vault(hs_id=hs_id, blob=blob)

    def create_job_with_receiver_credential(
        self,
        *,
        hr_id: int,
        requested_hearts: int,
        email: str,
        password: str,
        source: Source = Source.LOCAL,
        u_id: int | None = None,
    ) -> JobRecord:
        if self.vault is None:
            raise ConfigError("CredentialVault is required", stage="RECEIVER_JOB_VAULT")
        job = self.repo.create_job(hr_id=hr_id, requested_hearts=requested_hearts, source=source.value, u_id=u_id)
        hj_id = int(job["hj_id"])
        blob = self.vault.encrypt_json({"email": email, "password": password}, aad=f"heart_receiver_job:{hj_id}:{hr_id}")
        self.repo.upsert_receiver_job_vault(hj_id=hj_id, hr_id=hr_id, blob=blob)
        return JobRecord(hj_id=hj_id, hr_id=hr_id, requested_hearts=requested_hearts)

    def create_round(self, *, hj_id: int, hs_id: int, hr_id: int, sequence_no: int) -> dict[str, Any]:
        return self.repo.create_round(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id, sequence_no=sequence_no)

    def store_shared_sender_password(self, *, credential_name: str, password: str) -> None:
        if self.vault is None:
            raise ConfigError("CredentialVault is required", stage="SENDER_SHARED_VAULT")
        name = (credential_name or "default").strip() or "default"
        blob = self.vault.encrypt_json({"credential_name": name, "password": password}, aad=f"heart_sender_shared:{name}")
        self.repo.upsert_shared_sender_credential(credential_name=name, blob=blob)

    def store_sender_identity_with_shared_password(self, hs_id: int, *, email: str, shared_credential: str = "default") -> None:
        if self.vault is None:
            raise ConfigError("CredentialVault is required", stage="SENDER_VAULT")
        name = (shared_credential or "default").strip() or "default"
        blob = self.vault.encrypt_json({"email": email, "password_mode": "shared", "shared_credential": name}, aad=f"heart_sender:{hs_id}")
        self.repo.upsert_sender_vault(hs_id=hs_id, blob=blob)

    def import_sender_pattern(
        self,
        *,
        prefix: str,
        domain: str,
        start: int,
        end: int,
        width: int = 5,
        label_prefix: str = "Sender",
        shared_credential: str = "default",
        store_shared_identity: bool = True,
    ) -> dict[str, Any]:
        if start <= 0 or end < start:
            raise ConfigError("invalid sender pattern range", stage="SENDER_IMPORT_PATTERN")
        if end - start + 1 > 20000:
            raise ConfigError("sender pattern import limit is 20000 per command", stage="SENDER_IMPORT_PATTERN")
        domain_clean = domain.strip().lstrip("@").lower()
        created: list[dict[str, Any]] = []
        updated = 0
        for n in range(start, end + 1):
            suffix = str(n).zfill(width)
            email = f"{prefix}{suffix}@{domain_clean}"
            label = f"{label_prefix}{suffix}"
            sender = self.ensure_sender(label, email)
            if store_shared_identity:
                self.store_sender_identity_with_shared_password(sender.hs_id, email=email, shared_credential=shared_credential)
            created.append({"hs_id": sender.hs_id, "label": sender.label, "email_mask": sender.email_mask})
            updated += 1
        return {
            "ok": True,
            "count": updated,
            "first": created[0] if created else None,
            "last": created[-1] if created else None,
            "shared_credential": shared_credential,
            "sender_vault_identity_stored": store_shared_identity,
            "secretOutput": "NONE",
        }
