"""Encrypted secret store (Kamu SM credentials, database and replica passwords).

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under a key kept in a separate file
(``secret.key``) whose NTFS permissions allow only the ARCILOGGER service accounts and
administrators. The store file holds only ciphertext. Values are never logged, printed or returned
by the API; callers can only ask whether a secret is set.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.timeutil import isoformat_site, utc_now

KAMUSM_CUSTOMER = "kamusm.customer"
KAMUSM_PASSWORD = "kamusm.password"  # noqa: S105 - a name, not a password
DATABASE_PASSWORD = "database.password"  # noqa: S105 - a name, not a password
#: Password of ``replica.user`` for the SMB share of the replica (:mod:`arcilogger_core.smbshare`).
REPLICA_PASSWORD = "replica.password"  # noqa: S105 - a name, not a password
#: Password of ``[smtp] username`` for the alert e-mail (``arcilogger-worker``, docs/ALERTS.md).
SMTP_PASSWORD = "smtp.password"  # noqa: S105 - a name, not a password
#: Key of the optional usage-report pseudonyms (``arcilogger_worker.usage.pseudonyms``).
USAGE_PSEUDONYM_KEY = "usage.pseudonym_key"


class SecretStoreError(RuntimeError):
    """The key is missing or does not match the stored secrets."""


def record_change(path: Path, *, name: str, action: str, result: str, operator: str) -> None:
    """Append one line to the change log of the store (build specification 6.10 and 8.5:
    credential changes are audited). Only the name, the action, its result, the operator and
    the time — never the value, nor a hash of it (a short password's hash can be guessed)."""
    entry = {
        "id": uuid.uuid4().hex,
        "time": isoformat_site(utc_now(), "seconds"),
        "name": name,
        "action": action,
        "result": result,
        "operator": operator,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


class SecretStore:
    def __init__(self, key_path: Path, store_path: Path) -> None:
        self._key_path = key_path
        self._store_path = store_path

    @staticmethod
    def create_key(key_path: Path) -> None:
        """Create a new key file; refuses to overwrite an existing key."""
        key_path.parent.mkdir(parents=True, exist_ok=True)
        with key_path.open("xb") as handle:
            handle.write(Fernet.generate_key())

    def _fernet(self) -> Fernet:
        try:
            return Fernet(self._key_path.read_bytes().strip())
        except FileNotFoundError as exc:
            raise SecretStoreError(f"secret key not found: {self._key_path}") from exc

    def _load(self) -> dict[str, str]:
        if not self._store_path.exists():
            return {}
        data = json.loads(self._store_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SecretStoreError("secret store is corrupt")
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, data: dict[str, str]) -> None:
        atomic_write_text(self._store_path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def set(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("empty secret")
        data = self._load()
        data[name] = self._fernet().encrypt(value.encode("utf-8")).decode("ascii")
        self._save(data)

    def get(self, name: str) -> str | None:
        token = self._load().get(name)
        if token is None:
            return None
        try:
            return self._fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretStoreError(f"cannot decrypt {name!r}: wrong key file?") from exc

    def delete(self, name: str) -> bool:
        data = self._load()
        if data.pop(name, None) is None:
            return False
        self._save(data)
        return True

    def names(self) -> list[str]:
        """Names of the stored secrets (never their values)."""
        return sorted(self._load())
