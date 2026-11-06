"""Generic RFC 3161 client over HTTP (development and demo only — never legally valid).

It sends ``Content-Type: application/timestamp-query`` (Kamu SM's firewall resets anything else).
Kamu SM additionally requires its non-standard ``identity`` header, which this client does not
send; production stamping uses the official Zamane client (:mod:`arcilogger_core.tsa.zamane`).
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from http.client import HTTPException
from urllib.parse import urlsplit

from arcilogger_core.tsa.base import TsaUnavailableError
from arcilogger_core.tsa.client import Rfc3161Client

REQUEST_TYPE = "application/timestamp-query"
MAX_RESPONSE_BYTES = 1024 * 1024


class _SettingProxy(urllib.request.ProxyHandler):
    """Every URL through ``network.https_proxy`` (CONNECT for https): no bypass list of the
    environment (``no_proxy``) or of the system is applied, the setting alone decides (D13)."""

    def proxy_open(
        self, req: urllib.request.Request, proxy: str, type: str
    ) -> http.client.HTTPResponse | None:
        req.set_proxy(urlsplit(proxy).netloc, "http")
        return None  # the http/https handler opens it, through the proxy


def timestamp_opener(
    https_proxy: str = "", *handlers: urllib.request.BaseHandler
) -> urllib.request.OpenerDirector:
    """An opener for the time-stamp request: through ``https_proxy`` (``network.https_proxy``,
    ``http://host:port``) when set, otherwise directly, also when the environment or the system
    names a proxy (D13). ``handlers`` are added to urllib's defaults."""
    proxies = _SettingProxy({"http": https_proxy, "https": https_proxy}) if https_proxy else None
    return urllib.request.build_opener(proxies or urllib.request.ProxyHandler({}), *handlers)


class Rfc3161HttpProvider(Rfc3161Client):
    name = "rfc3161"
    legal_grade = False

    def __init__(
        self,
        url: str,
        *,
        digest: str = "sha256",
        timeout_seconds: float = 60,
        https_proxy: str = "",
    ) -> None:
        super().__init__(digest)
        if not url.startswith(("http://", "https://")):
            raise ValueError("time-stamp URL must be http:// or https://")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.https_proxy = https_proxy

    def _exchange(self, request: bytes) -> bytes:
        http_request = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self.url,
            data=request,
            method="POST",
            headers={"Content-Type": REQUEST_TYPE, "Accept": "application/timestamp-reply"},
        )
        opener = timestamp_opener(self.https_proxy)
        try:
            with opener.open(http_request, timeout=self.timeout_seconds) as reply:
                return bytes(reply.read(MAX_RESPONSE_BYTES))
        except (urllib.error.URLError, HTTPException, TimeoutError, OSError) as exc:
            raise TsaUnavailableError(f"time-stamp server not reachable: {exc}") from exc
