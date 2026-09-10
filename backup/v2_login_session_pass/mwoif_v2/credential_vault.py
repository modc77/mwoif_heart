from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .slots import normalize_slot


class VaultError(RuntimeError):
    pass


@dataclass(slots=True)
class Credential:
    slot: str
    email: str
    password: str


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _dpapi_encrypt(text: str) -> str:
    if os.name != "nt":
        raise VaultError("sender password vault requires Windows DPAPI")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, keep = _blob(text.encode("utf-8"))
    out = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise VaultError(f"CryptProtectData failed: {ctypes.GetLastError()}")
    try:
        raw = ctypes.string_at(out.pbData, out.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        kernel32.LocalFree(out.pbData)


def _dpapi_decrypt(value: str) -> str:
    if os.name != "nt":
        raise VaultError("sender password vault requires Windows DPAPI")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, keep = _blob(base64.b64decode(value))
    out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise VaultError(f"CryptUnprotectData failed: {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(out.pbData)


class CredentialVault:
    def __init__(self, root: Path):
        self.path = root / "state" / "senders.vault.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": "mwoif-v2-senders-vault", "accounts": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
            raise VaultError("invalid sender vault")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def put(self, slot: str, email: str, password: str) -> None:
        slot = normalize_slot(slot)
        email = str(email or "").strip()
        if not email or not password:
            raise VaultError("email/password is empty")
        data = self._read()
        item = {"slot": slot, "email": email, "password_dpapi": _dpapi_encrypt(password)}
        accounts = [x for x in data["accounts"] if str(x.get("slot")) != slot]
        accounts.append(item)
        accounts.sort(key=lambda x: (not str(x.get("slot", "")).isdigit(), int(x["slot"]) if str(x.get("slot", "")).isdigit() else str(x.get("slot", ""))))
        data["accounts"] = accounts
        self._write(data)

    def remove(self, slot: str) -> bool:
        slot = normalize_slot(slot)
        data = self._read()
        old = len(data["accounts"])
        data["accounts"] = [x for x in data["accounts"] if str(x.get("slot")) != slot]
        if len(data["accounts"]) != old:
            self._write(data)
            return True
        return False

    def list_public(self) -> list[dict[str, str]]:
        return [{"slot": str(x.get("slot") or ""), "email": str(x.get("email") or "")} for x in self._read()["accounts"]]

    def credentials(self, only_slots: set[str] | None = None) -> list[Credential]:
        out: list[Credential] = []
        for x in self._read()["accounts"]:
            slot = normalize_slot(str(x.get("slot") or ""))
            if only_slots is not None and slot not in only_slots:
                continue
            out.append(Credential(slot=slot, email=str(x.get("email") or ""), password=_dpapi_decrypt(str(x.get("password_dpapi") or ""))))
        return out

    def import_plain_json(self, path: Path) -> int:
        """One-time import. Plain passwords are encrypted immediately and never logged."""
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("accounts") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise VaultError("JSON must be a list or {accounts:[...]}")
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            slot = str(row.get("slot") or row.get("id") or count + 1)
            email = str(row.get("email") or "").strip()
            password = str(row.get("password") or "")
            if email and password:
                self.put(slot, email, password)
                count += 1
        return count
