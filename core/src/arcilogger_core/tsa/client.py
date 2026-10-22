"""Shared RFC 3161 client logic: build the request, exchange it, check and store the answer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from arcilogger_core.fsutil import atomic_write_bytes
from arcilogger_core.tsa.asn1 import build_request, file_digest, parse_container, tst_info
from arcilogger_core.tsa.base import (
    StampResult,
    TsaNoCreditError,
    TsaRejectedError,
    TsaResponseError,
)
from arcilogger_core.tsa.verify import inspect_token

CREDIT_WORDS = ("credit", "kontör", "kontor")


def token_path_for(file_path: Path) -> Path:
    return file_path.with_name(file_path.name + ".zd")


class Rfc3161Client(ABC):
    name = "rfc3161"
    legal_grade = False

    def __init__(self, digest: str = "sha256") -> None:
        self.digest = digest

    @abstractmethod
    def _exchange(self, request: bytes) -> bytes:
        """Send a DER TimeStampReq, return the DER TimeStampResp."""

    def stamp(self, file_path: Path) -> StampResult:
        digest = file_digest(file_path, self.digest)
        request, nonce = build_request(digest, self.digest)
        response = self._exchange(request)
        try:
            token, _ = parse_container(response)
        except TsaRejectedError as exc:
            if any(word in str(exc).lower() for word in CREDIT_WORDS):
                raise TsaNoCreditError(str(exc)) from exc
            raise
        tst, _ = tst_info(token)
        imprint = tst["message_imprint"]
        if imprint["hashed_message"].native != digest:
            raise TsaResponseError("response imprint does not match the request")
        if imprint["hash_algorithm"]["algorithm"].native != self.digest:
            raise TsaResponseError("response uses a different hash algorithm")
        if tst["nonce"].native != nonce:
            raise TsaResponseError("response nonce does not match the request")
        token_path = token_path_for(file_path)
        atomic_write_bytes(token_path, response)
        return StampResult(token_path=token_path, info=inspect_token(response))

    def credits(self) -> int | None:
        return None
