"""Generic RFC 3161 client over HTTP (development and demo only — never legally valid).

It sends ``Content-Type: application/timestamp-query`` (Kamu SM's firewall resets anything else).
Kamu SM additionally requires its non-standard ``identity`` header, which this client does not
send; production stamping uses the official Zamane client (:mod:`arcilogger_core.tsa.zamane`).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from http.client import HTTPException

from arcilogger_core.tsa.base import TsaUnavailableError
from arcilogger_core.tsa.client import Rfc3161Client

REQUEST_TYPE = "application/timestamp-query"
MAX_RESPONSE_BYTES = 1024 * 1024


class Rfc3161HttpProvider(Rfc3161Client):
    name = "rfc3161"
    legal_grade = False

    def __init__(self, url: str, *, digest: str = "sha256", timeout_seconds: float = 60) -> None:
        super().__init__(digest)
        if not url.startswith(("http://", "https://")):
            raise ValueError("time-stamp URL must be http:// or https://")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def _exchange(self, request: bytes) -> bytes:
        http_request = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self.url,
            data=request,
            method="POST",
            headers={"Content-Type": REQUEST_TYPE, "Accept": "application/timestamp-reply"},
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as reply:  # noqa: S310
                return bytes(reply.read(MAX_RESPONSE_BYTES))
        except (urllib.error.URLError, HTTPException, TimeoutError, OSError) as exc:
            raise TsaUnavailableError(f"time-stamp server not reachable: {exc}") from exc
