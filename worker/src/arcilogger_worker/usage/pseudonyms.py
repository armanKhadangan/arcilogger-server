"""Optional pseudonyms for the usage reports (owner decision 2026-10-20: off by default).

Names are shown to every role that may see usage. Only when ``usage.pseudonymize_anonymous_role``
is on, viewers with ``usage.anonymous`` but without ``usage.person`` see ``Kullanıcı-7F3A`` /
``Cihaz-1A2B`` instead of names, MAC and IP addresses — and reports with ``pseudonymize`` true.

A pseudonym is ``HMAC-SHA-256(key, subject key)``: stable (the same person gets the same
pseudonym in every report and on every day), not reversible without the key, and different on
another installation. The key is the secret ``usage.pseudonym_key`` of the encrypted secret store
(``arcilogger-core secret set usage.pseudonym_key``: any long random text); without it there are
no pseudonyms (:class:`PseudonymKeyMissingError`). The token the API accepts back (``p:<16 hex>`` /
``d:<16 hex>``) is the first 64 bits of the HMAC; the label shows its first four hex digits — two
people may share a label, never a token. Only administrators see the mapping (``GET
/usage/pseudonyms``, audited).
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Final

from arcilogger_core.secretstore import USAGE_PSEUDONYM_KEY, SecretStore

#: Name of the key in the secret store.
PSEUDONYM_SECRET: Final = USAGE_PSEUDONYM_KEY
#: Shortest key accepted (characters).
MIN_KEY_LENGTH: Final = 16


class PseudonymKeyMissingError(RuntimeError):
    """Pseudonyms are wanted but ``usage.pseudonym_key`` is not in the secret store."""


def load_key(secrets_dir: Path) -> bytes | None:
    """The pseudonym key from the secret store (``None`` when not stored or too short)."""
    store = SecretStore(secrets_dir / "secret.key", secrets_dir / "secrets.json")
    value = store.get(PSEUDONYM_SECRET)
    if value is None or len(value) < MIN_KEY_LENGTH:
        return None
    return value.encode("utf-8")


def token(key: bytes, subject: str) -> str:
    """64-bit hex token of a subject key (``person:12`` …)."""
    return hmac.new(key, subject.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def pseudonym(key: bytes, subject: str) -> tuple[str, str]:
    """``(opaque key, label)`` of a subject: ``("p:3f2a…", "Kullanıcı-3F2A")``."""
    found = token(key, subject)
    if subject.startswith("person:"):
        return f"p:{found}", f"Kullanıcı-{found[:4].upper()}"
    return f"d:{found}", f"Cihaz-{found[:4].upper()}"
