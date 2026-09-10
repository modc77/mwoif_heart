from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mwoif.core.config import VaultConfig
from mwoif.domain.errors import ConfigError, CredentialError


@dataclass(frozen=True, slots=True)
class VaultBlob:
    ciphertext: bytes
    iv: bytes
    auth_tag: bytes
    key_version: int


def generate_master_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


class CredentialVault:
    def __init__(self, config: VaultConfig) -> None:
        if not config.master_key_b64:
            raise ConfigError(
                "MWOIF_HEART_MASTER_KEY_B64 is required for credential vault. Generate with: .\\run.bat vault-keygen",
                stage="VAULT_CONFIG",
            )
        try:
            key = base64.b64decode(config.master_key_b64, validate=True)
        except Exception as exc:
            raise ConfigError("Invalid MWOIF_HEART_MASTER_KEY_B64 base64", stage="VAULT_CONFIG") from exc
        if len(key) != 32:
            raise ConfigError("MWOIF_HEART_MASTER_KEY_B64 must decode to 32 bytes", stage="VAULT_CONFIG")
        self._aead = AESGCM(key)
        self.key_version = config.key_version

    def encrypt_json(self, payload: dict[str, Any], *, aad: str) -> VaultBlob:
        try:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            iv = os.urandom(12)
            sealed = self._aead.encrypt(iv, raw, aad.encode("utf-8"))
            return VaultBlob(
                ciphertext=sealed[:-16],
                iv=iv,
                auth_tag=sealed[-16:],
                key_version=self.key_version,
            )
        except Exception as exc:
            raise CredentialError("credential encryption failed", stage="VAULT_ENCRYPT") from exc

    def decrypt_json(self, blob: VaultBlob, *, aad: str) -> dict[str, Any]:
        try:
            raw = self._aead.decrypt(blob.iv, blob.ciphertext + blob.auth_tag, aad.encode("utf-8"))
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise CredentialError("credential decryption failed", stage="VAULT_DECRYPT") from exc
        if not isinstance(value, dict):
            raise CredentialError("credential payload is not an object", stage="VAULT_DECRYPT")
        return value
