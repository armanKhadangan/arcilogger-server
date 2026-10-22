"""TOTP second factor (RFC 6238) and single-use recovery codes (spec 13).

* **Codes:** HMAC-SHA1, 6 digits, 30-second steps — what every authenticator app (Google
  Authenticator, Microsoft Authenticator, FreeOTP …) supports. A code is accepted for the
  current step and one step either side (±30 s of clock difference).
* **Replay protection:** the step of the last accepted code is stored
  (``users.totp_last_counter``); a code for that step or an earlier one is refused, so an
  observed code cannot be used a second time, not even within its 30 seconds.
* **Secret:** 160 random bits (RFC 4226 section 4 recommends 160), stored **encrypted** in
  ``users.totp_secret_encrypted`` with Fernet under the key of the existing secret store,
  ``<secrets_dir>/secret.key`` (the same key that protects the database password, so the API's
  service account can already read it; the API never creates the key). The secret is shown once
  during enrolment (QR code and text) and never logged or audited.
* **Recovery codes:** 10 codes of 16 Crockford base32 characters (80 bits each, shown as
  ``xxxx-xxxx-xxxx-xxxx``), shown once; only ``sha256("arcilogger-recovery-v1:<user id>:<code>")``
  is stored. 80 random bits make an offline search of the hashes hopeless, so a slow hash is not
  needed (and a lookup by hash keeps the check constant-time in the database). Typing is forgiving:
  case, spaces and dashes are ignored and ``i``/``l``/``o`` are read as ``1``/``1``/``0``.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken

TOTP_DIGITS: Final = 6
TOTP_PERIOD: Final = 30
#: Steps accepted before and after the current one.
TOTP_WINDOW: Final = 1
ISSUER: Final = "ARCILOGGER"
RECOVERY_CODE_COUNT: Final = 10
RECOVERY_CODE_LENGTH: Final = 16
#: Crockford base32 (no i, l, o, u).
RECOVERY_ALPHABET: Final = "0123456789abcdefghjkmnpqrstvwxyz"
_RECOVERY_LOOKALIKES: Final = str.maketrans({"i": "1", "l": "1", "o": "0"})
#: ASCII digits only: ``\d`` would also match other scripts' digits (``١٢٣٤٥٦``), which can never
#: be a valid code and made the constant-time comparison raise (a 500 instead of a counted failure).
_CODE = re.compile(rf"[0-9]{{{TOTP_DIGITS}}}")


class SecretKeyUnavailableError(RuntimeError):
    """``secret.key`` is missing or does not decrypt the stored secret (wrong key file?)."""


def secret_key_path(secrets_dir: Path) -> Path:
    return secrets_dir / "secret.key"


def _fernet(secrets_dir: Path) -> Fernet:
    path = secret_key_path(secrets_dir)
    try:
        return Fernet(path.read_bytes().strip())
    except (OSError, ValueError) as exc:
        raise SecretKeyUnavailableError(f"secret key not readable: {path}") from exc


def new_secret() -> str:
    """A fresh base32 TOTP secret (32 characters = 160 bits)."""
    return pyotp.random_base32(length=32)


def encrypt_secret(secrets_dir: Path, secret: str) -> str:
    return _fernet(secrets_dir).encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_secret(secrets_dir: Path, token: str) -> str:
    try:
        return _fernet(secrets_dir).decrypt(token.encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise SecretKeyUnavailableError("stored TOTP secret does not match secret.key") from exc


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD)


def provisioning_uri(secret: str, account: str) -> str:
    """``otpauth://totp/ARCILOGGER:<account>?secret=…&issuer=ARCILOGGER`` for the QR code."""
    return _totp(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def qr_svg(uri: str) -> str:
    """The QR code of ``uri`` as a standalone SVG document (no scripts, no external resources)."""
    buffer = io.BytesIO()
    segno.make_qr(uri, error="m").save(
        buffer,
        kind="svg",
        scale=5,
        border=4,
        xmldecl=False,
        svgns=True,
        title="TOTP QR",
        dark="#000000",
        light="#ffffff",
    )
    return buffer.getvalue().decode("utf-8")


def counter_at(at: datetime) -> int:
    """The RFC 6238 time step of ``at``."""
    return int(at.timestamp()) // TOTP_PERIOD


def code_for(secret: str, counter: int) -> str:
    """The code of one time step (tests, enrolment checks)."""
    return _totp(secret).generate_otp(counter)


def looks_like_totp(code: str) -> bool:
    return bool(_CODE.fullmatch(code.replace(" ", "")))


@dataclass(frozen=True)
class TotpCheck:
    """``counter`` is the accepted step (store it); ``replay`` marks a code already used."""

    counter: int | None
    replay: bool = False

    @property
    def ok(self) -> bool:
        return self.counter is not None


def check_totp(secret: str, code: str, *, last_counter: int | None, at: datetime) -> TotpCheck:
    """Accept ``code`` for the current step ±:data:`TOTP_WINDOW` unless already used."""
    candidate = code.replace(" ", "")
    if not _CODE.fullmatch(candidate):
        return TotpCheck(counter=None)
    now = counter_at(at)
    replay = False
    for counter in range(now - TOTP_WINDOW, now + TOTP_WINDOW + 1):
        if not hmac.compare_digest(code_for(secret, counter), candidate):
            continue
        if last_counter is not None and counter <= last_counter:
            replay = True
            continue
        return TotpCheck(counter=counter)
    return TotpCheck(counter=None, replay=replay)


# -- recovery codes -------------------------------------------------------------------------------


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Fresh codes for display (``xxxx-xxxx-xxxx-xxxx``)."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        codes.append("-".join(raw[i : i + 4] for i in range(0, RECOVERY_CODE_LENGTH, 4)))
    return codes


def normalize_recovery_code(text: str) -> str | None:
    """The canonical form of a typed code, ``None`` when it cannot be one."""
    compact = re.sub(r"[\s-]+", "", text).lower().translate(_RECOVERY_LOOKALIKES)
    if len(compact) != RECOVERY_CODE_LENGTH or any(c not in RECOVERY_ALPHABET for c in compact):
        return None
    return compact


def hash_recovery_code(user_id: int, code: str) -> str:
    """Stored form of a code (``code`` as displayed or typed; normalised here)."""
    normalized = normalize_recovery_code(code)
    if normalized is None:
        raise ValueError("not a recovery code")
    data = f"arcilogger-recovery-v1:{user_id}:{normalized}".encode("ascii")
    return hashlib.sha256(data).hexdigest()
