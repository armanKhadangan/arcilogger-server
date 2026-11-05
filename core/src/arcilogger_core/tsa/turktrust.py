"""TÜRKTRUST time stamps: RFC 3161 over HTTPS with HTTP Basic authentication (contract §2.4,
D12).

The request is the standard ``application/timestamp-query`` POST, with the account of the
secret store (``turktrust.username``, ``turktrust.password``) in an ``Authorization: Basic``
header (RFC 7617, UTF-8). The server certificate is verified with the operating system's trust
store; nothing turns that off. A plain ``http://`` URL is accepted only with
``tsa.allow_plain_http`` (checked by :class:`~arcilogger_core.config.TsaConfig`), and the
legal-status page then warns that the password travels in clear text
(``credentials_in_clear``). TÜRKTRUST has no credit query: :meth:`credits` is None.

The password never appears in an exception, a log line or ``raw_output``: only the HTTP status
or the network error is reported, and the header is never forwarded (redirects are not
followed).
"""

from __future__ import annotations

import base64
import ssl
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Final

from arcilogger_core.tsa.base import TsaConfigurationError, TsaUnavailableError
from arcilogger_core.tsa.client import Rfc3161Client
from arcilogger_core.tsa.rfc3161 import MAX_RESPONSE_BYTES, REQUEST_TYPE, timestamp_opener

#: TÜRKTRUST production stamps claim legal grade: the product owner's decision of 2026-11-02
#: (contract D12, owner question Q1). A product constant, so that no site setting can make
#: another provider "legal". Whether a day is shown as legal grade is still the three-part test
#: of rule 4 (:func:`arcilogger_core.tsa.verify.confirmed_legal_grade`).
TURKTRUST_LEGAL_GRADE: Final = True

#: The statuses that mean the user name or password is wrong (not retried).
CREDENTIAL_STATUSES: Final = frozenset({401, 403})


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is reported as the HTTP error it is, never followed: the request and its
    credentials go only to the configured URL."""

    def redirect_request(self, *_: object) -> urllib.request.Request | None:
        return None


def basic_authorization(username: str, password: str) -> str:
    """The ``Authorization`` header value of RFC 7617 with the UTF-8 charset."""
    pair = f"{username}:{password}".encode()
    return "Basic " + base64.b64encode(pair).decode("ascii")


class TurktrustHttpsProvider(Rfc3161Client):
    name = "turktrust"

    def __init__(
        self,
        url: str,
        *,
        username: str,
        password: str,
        legal_grade: bool,
        digest: str = "sha256",
        timeout_seconds: float = 60,
        https_proxy: str = "",
        allow_plain_http: bool = False,
    ) -> None:
        super().__init__(digest)
        if url.startswith("http://") and not allow_plain_http:
            raise TsaConfigurationError(
                "TÜRKTRUST needs an https:// URL (tsa.allow_plain_http is not set)"
            )
        if not url.startswith(("http://", "https://")):
            raise TsaConfigurationError("time-stamp URL must be https://")
        if ":" in username:
            raise TsaConfigurationError("turktrust.username must not contain ':' (RFC 7617)")
        self.url = url
        self.legal_grade = legal_grade
        self.timeout_seconds = timeout_seconds
        self.https_proxy = https_proxy
        self._authorization = basic_authorization(username, password)

    def __repr__(self) -> str:
        return f"TurktrustHttpsProvider(url={self.url!r}, digest={self.digest!r})"

    def _exchange(self, request: bytes) -> bytes:
        http_request = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self.url,
            data=request,
            method="POST",
            headers={"Content-Type": REQUEST_TYPE, "Accept": "application/timestamp-reply"},
        )
        http_request.add_unredirected_header("Authorization", self._authorization)
        opener = timestamp_opener(
            self.https_proxy,
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            _NoRedirects(),
        )
        try:
            with opener.open(http_request, timeout=self.timeout_seconds) as reply:
                return bytes(reply.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in CREDENTIAL_STATUSES:
                raise TsaConfigurationError(
                    f"credentials: TÜRKTRUST refused the user name or password (HTTP {exc.code})"
                ) from None
            raise TsaUnavailableError(f"time-stamp server answered HTTP {exc.code}") from None
        except (urllib.error.URLError, HTTPException, TimeoutError, OSError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise TsaUnavailableError(f"time-stamp server not reachable: {reason}") from None
