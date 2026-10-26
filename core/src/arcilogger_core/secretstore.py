"""Encrypted secret store (Kamu SM credentials, database and replica passwords).

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under a key kept in a separate file
(``secret.key``) whose NTFS permissions allow only the ARCILOGGER service accounts and
administrators. The store file holds only ciphertext. Values are never logged, printed or returned
by the API; callers can only ask whether a secret is set.

The browser administration (ADR-0014) never moves a plaintext through a file: the API encrypts
a value it was given (:meth:`SecretStore.encrypt_value`) and hands the token to the
administration agent, which checks it once (:meth:`SecretStore.set_encrypted`) and writes the
new store as one atomic file (:meth:`SecretStore.render_with`).
"""

from __future__ import annotations

import json
import os
import unicodedata
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from arcilogger_core.fsutil import atomic_write_bytes
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


#: Longest value of a stored secret given through the browser, and whether it may contain
#: whitespace (the Kamu SM customer number may not; its exact format is confirmed on site).
SECRET_LIMITS: Final[Mapping[str, tuple[int, bool]]] = MappingProxyType(
    {
        KAMUSM_CUSTOMER: (64, False),
        KAMUSM_PASSWORD: (128, True),
        SMTP_PASSWORD: (256, True),
        REPLICA_PASSWORD: (256, True),
    }
)
#: The limit of every other name.
DEFAULT_SECRET_LIMIT: Final = 1024
#: Unicode categories a secret never contains: control and format characters, line and
#: paragraph separators, unpaired surrogates (they could not be stored as UTF-8).
_REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class SecretStoreError(RuntimeError):
    """The key is missing or does not match the stored secrets."""


class SecretValueError(ValueError):
    """A value is not acceptable as a stored secret (``secret_invalid`` of the browser
    administration). The message names the rule, never the value."""

    code: Final = "secret_invalid"


def check_secret_value(name: str, value: str) -> None:
    """Refuse an empty, too long or multi-line value, or one with control characters
    (:class:`SecretValueError`)."""
    limit, spaces = SECRET_LIMITS.get(name, (DEFAULT_SECRET_LIMIT, True))
    if not value:
        raise SecretValueError(f"{name}: empty")
    if len(value) > limit:
        raise SecretValueError(f"{name}: longer than {limit} characters")
    if any(unicodedata.category(char) in _REFUSED_CATEGORIES for char in value):
        raise SecretValueError(f"{name}: contains control characters")
    if not spaces and any(char.isspace() for char in value):
        raise SecretValueError(f"{name}: contains whitespace")


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

    @staticmethod
    def _render(data: Mapping[str, str]) -> bytes:
        return (json.dumps(dict(data), indent=2, sort_keys=True) + "\n").encode("utf-8")

    def _save(self, data: dict[str, str]) -> None:
        atomic_write_bytes(self._store_path, self._render(data))

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

    def readable(self, name: str) -> bool:
        """Whether ``name`` is stored and decrypts with the key (the value is not kept)."""
        try:
            return self.get(name) is not None
        except (OSError, ValueError, SecretStoreError):
            return False

    # -- tokens (browser administration, ADR-0014) ------------------------------------------------

    def encrypt_value(self, value: str) -> str:
        """``value`` as a token in the format of a value of the store file (Fernet under the
        key). The API encrypts what a user typed; only the token travels to the agent."""
        if not value:
            raise SecretValueError("empty secret")
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_value(self, token: str) -> str:
        """The plaintext of a token (:class:`SecretStoreError` when it is not one of this key)."""
        try:
            return self._fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise SecretStoreError("a token does not decrypt with this key") from exc

    def check_token(self, name: str, token: str) -> None:
        """Decrypt ``token`` once and check its value for ``name`` (:func:`check_secret_value`);
        the plaintext is dropped at once."""
        check_secret_value(name, self.decrypt_value(token))

    def set_encrypted(self, name: str, token: str) -> None:
        """Store a token made by :meth:`encrypt_value` as the value of ``name`` (checked once)."""
        self.check_token(name, token)
        data = self._load()
        data[name] = token
        self._save(data)

    def render_with(self, changes: Mapping[str, str | None]) -> bytes:
        """The store file with ``changes`` applied, without writing it: a token stores it under
        its name (checked like :meth:`set_encrypted`), None deletes the name. Every other entry
        is kept as it is (its ciphertext is not re-encrypted)."""
        data = self._load()
        for name, token in changes.items():
            if token is None:
                data.pop(name, None)
            else:
                self.check_token(name, token)
                data[name] = token
        return self._render(data)
