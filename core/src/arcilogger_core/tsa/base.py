"""Time-stamp provider interface (build specification section 6.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol


class TsaError(RuntimeError):
    """A time stamp could not be obtained. ``retryable`` errors are retried on the schedule."""

    retryable = True


class TsaUnavailableError(TsaError):
    """Network error, timeout or HTTP error: the server could not be reached."""


class TsaNoCreditError(TsaError):
    """Kamu SM reports that the credit (kontör) is used up."""


class TsaRejectedError(TsaError):
    """The server answered with a rejection status."""


class TsaResponseError(TsaError):
    """The answer is malformed or does not match the request (wrong imprint, nonce)."""


class TsaConfigurationError(TsaError):
    """Missing client, credentials or certificates — fixing needs an administrator."""

    retryable = False


@dataclass(frozen=True)
class TokenInfo:
    """What a time-stamp token says (TSTInfo + the TSA certificate)."""

    gen_time: datetime
    serial: str
    policy: str
    tsa_name: str
    tsa_cert_not_after: datetime | None
    message_imprint_hex: str
    digest_alg: str
    nonce: int | None
    container: str  # "TimeStampResp" or "TimeStampToken"


@dataclass(frozen=True)
class StampResult:
    token_path: Path
    info: TokenInfo
    #: Client output for the audit trail, with credentials redacted.
    raw_output: str = ""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    errors: tuple[str, ...] = ()
    info: TokenInfo | None = None
    details: dict[str, str] = field(default_factory=dict)
    #: Accepted deviations (e.g. a non-critical EKU allowed by ``tsa.require_critical_eku``).
    warnings: tuple[str, ...] = ()


class TimestampProvider(Protocol):
    name: str
    #: True only for Kamu SM production stamps (Law 5070).
    legal_grade: bool

    def stamp(self, file_path: Path) -> StampResult:
        """Time-stamp ``file_path``; writes ``<file>.zd`` next to it."""
        ...

    def credits(self) -> int | None:
        """Remaining credit, or None when the provider has no credit concept."""
        ...
