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
