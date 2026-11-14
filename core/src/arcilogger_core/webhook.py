"""Alert webhooks: the payload, its signature and one HTTPS ``POST`` (Wave 6 contract §2.4).

Used by the worker job ``alerts.webhooks`` and by the administration agent's ``webhook`` probe
("Test gönder"), so both send exactly the same request.

* **Payloads** (:func:`payload`, §2.4.3): ``generic`` is the alert's id, rule, severity, state,
  titles, time, count and web link; ``slack`` and ``teams`` carry one line of it (both shapes are
  "sahada doğrula", V8). No payload holds a person's name or a log line: the titles name an
  address or a device, never a person (contract §0.1 rule 5), and the raw lines stay on the
  server.
* **Signature** (:func:`signature`): ``generic`` requests carry ``X-Arcilogger-Timestamp`` and
  ``X-Arcilogger-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<body>">`` with the
  webhook's secret.
* **Sending** (:func:`send`): ``https://`` only, the certificate always verified (the system
  store, or the webhook's ``ca_file``), through ``network.https_proxy`` when set, redirects not
  followed. A ``2xx`` reply is success; a 4xx reply other than 408 and 429 is permanent
  (:class:`WebhookError` ``permanent``); everything else may be tried again.
* **Error texts** (:func:`error_text`): the status code and at most :data:`ERROR_MAX` characters
  of the reply, without the URL's path or query, the secret or any header value.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import ssl
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from arcilogger_core.timeutil import isoformat_site
from arcilogger_core.tsa.rfc3161 import timestamp_opener

PRODUCT: Final = "ARCILOGGER"
WebhookFormat = Literal["generic", "teams", "slack"]
#: The severity names of the alert texts, Turkish and English.
SEVERITY_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "info": ("Bilgi", "Info"),
    "low": ("Düşük", "Low"),
    "medium": ("Orta", "Medium"),
    "high": ("Yüksek", "High"),
    "critical": ("Kritik", "Critical"),
}
#: Longest part of a reply kept in ``last_error`` (§2.4.2).
ERROR_MAX: Final = 300
#: Most bytes of a reply read for the error text.
REPLY_READ_MAX: Final = 4096
TIMESTAMP_HEADER: Final = "X-Arcilogger-Timestamp"
SIGNATURE_HEADER: Final = "X-Arcilogger-Signature"
HIDDEN: Final = "***"


@dataclass(frozen=True)
class WebhookAlert:
    """What a webhook says about one alert (§2.4.3)."""

    id: int
    rule: str
    severity: str
    state: str
    title_tr: str
    title_en: str
    time: datetime
    count: int
    #: The alert on the Security page (``<alerts.web_base_url>/security?alert=<id>``).
    url: str


def payload(
    format: WebhookFormat, alert: WebhookAlert, *, version: str, site: str
) -> dict[str, Any]:
    """The JSON document of ``alert`` for a webhook of ``format`` (§2.4.3)."""
    severity_tr, severity_en = SEVERITY_LABELS.get(alert.severity, (alert.severity,) * 2)
    if format == "slack":
        return {"text": f"[{PRODUCT}] {severity_tr} — {alert.title_tr} ({alert.url})"}
    if format == "teams":
        card = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": f"{severity_tr} — {alert.title_tr}", "wrap": True},
                {
                    "type": "TextBlock",
                    "text": f"{severity_en} — {alert.title_en}",
                    "wrap": True,
                    "isSubtle": True,
                },
            ],
            "actions": [{"type": "Action.OpenUrl", "title": "Aç / Open", "url": alert.url}],
        }
        return {
            "type": "message",
            "attachments": [
                {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
            ],
        }
    return {
        "product": PRODUCT,
        "version": version,
        "site": site,
        "alert": {
            "id": alert.id,
            "rule": alert.rule,
            "severity": alert.severity,
            "state": alert.state,
            "title_tr": alert.title_tr,
            "title_en": alert.title_en,
            "time": isoformat_site(alert.time, "seconds"),
            "count": alert.count,
            "url": alert.url,
        },
    }


def encode(document: Mapping[str, Any]) -> bytes:
    """The request body: compact UTF-8 JSON (the bytes the signature covers)."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signature(secret: str, timestamp: int, body: bytes) -> str:
    """``sha256=<hex HMAC-SHA256 of "<timestamp>.<body>">`` with ``secret``."""
    message = f"{timestamp}.".encode("ascii") + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def is_permanent(status: int) -> bool:
    """A refusal that a later attempt cannot change: 4xx except 408 and 429 (§2.4.2)."""
    return 400 <= status < 500 and status not in (408, 429)


class WebhookError(Exception):
    """A request that did not get a ``2xx`` reply; the text is :func:`error_text`'s."""

    def __init__(self, text: str, *, status: int | None = None, permanent: bool = False) -> None:
        super().__init__(text)
        self.status = status
        self.permanent = permanent


@dataclass(frozen=True)
class Target:
    """Where and how one webhook sends: from its ``[[webhooks]]`` entry and the secret store."""

    url: str
    format: WebhookFormat
    #: The HMAC secret of a ``generic`` webhook (``None`` for ``teams`` and ``slack``).
    secret: str | None = None
    timeout_seconds: float = 10
    ca_file: Path | None = None
    #: ``network.https_proxy`` (``http://host:port``), or empty for a direct connection.
    https_proxy: str = ""

    def hidden(self) -> tuple[str, ...]:
        """Texts that never appear in an error: the URL beyond its host (a Teams or Slack URL
        is a secret as a whole), its query and the HMAC secret."""
        parts = urlsplit(self.url)
        texts = [self.url, parts.path, parts.query, self.secret or ""]
        if self.format != "generic":
            texts.append(parts.netloc)
        return tuple(text for text in texts if len(text) > 1)


def error_text(reason: str, hidden: Iterable[str] = (), *, limit: int = ERROR_MAX) -> str:
    """``reason`` on one line, at most ``limit`` characters, with every text of ``hidden``
    (the URL's path and query, the secret, header values) replaced by ``***``."""
    text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in reason)
    for secret in sorted(set(hidden), key=len, reverse=True):
        if secret:
            text = text.replace(secret, HIDDEN)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not a new target: the signed request is never sent elsewhere."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def send(target: Target, body: bytes, *, version: str, now: datetime) -> int:
    """``POST`` ``body`` to the target; the ``2xx`` status code, or :class:`WebhookError`."""
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": f"{PRODUCT}/{version}",
    }
    hidden = list(target.hidden())
    if target.format == "generic":
        if not target.secret:
            raise WebhookError("the webhook's HMAC secret is not set")
        timestamp = int(now.timestamp())
        headers[TIMESTAMP_HEADER] = str(timestamp)
        headers[SIGNATURE_HEADER] = signature(target.secret, timestamp, body)
        hidden += [headers[SIGNATURE_HEADER], headers[SIGNATURE_HEADER].removeprefix("sha256=")]
    if urlsplit(target.url).scheme != "https":
        raise WebhookError("only https:// is allowed")
    cafile = str(target.ca_file) if target.ca_file is not None else None
    try:
        context = ssl.create_default_context(cafile=cafile)
    except (OSError, ssl.SSLError) as exc:
        raise WebhookError(error_text(f"ca_file: {exc}", hidden)) from None
    opener = timestamp_opener(
        target.https_proxy, urllib.request.HTTPSHandler(context=context), _NoRedirect()
    )
    # S310: the scheme is checked above (https only), and redirects are not followed.
    request = urllib.request.Request(target.url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with opener.open(request, timeout=target.timeout_seconds) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        try:
            reply = exc.read(REPLY_READ_MAX).decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            reply = ""
        text = error_text(f"HTTP {exc.code}: {reply}" if reply else f"HTTP {exc.code}", hidden)
        raise WebhookError(text, status=exc.code, permanent=is_permanent(exc.code)) from None
    except urllib.error.URLError as exc:
        raise WebhookError(error_text(str(exc.reason), hidden)) from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise WebhookError(error_text(f"{type(exc).__name__}: {exc}", hidden)) from None
    if not 200 <= status < 300:
        raise WebhookError(f"HTTP {status}", status=status, permanent=is_permanent(status))
    return status
