"""Revocation lists (SİL / CRL) of the time-stamp certificates (Wave 5 D7-D9).

The ``CrlFetch`` task runs ``arcilogger-core crl fetch`` once a day:

* **Where from.** The URLs of ``tsa.crl_urls``; when that is empty, the http/https CRL
  distribution points of the pinned roots and of the certificates in the newest token of each
  provider that chain to one of them. No URL is written into the code: Kamu SM's CRL addresses
  are unconfirmed (K10 in docs/kamusm.md, "sahada doğrula"). A URL that carries a user name or
  password is never fetched, and is logged and recorded without them.
* **How.** ``urllib`` with ``tsa.timeout_seconds`` for each whole download, at most
  :data:`MAX_CRL_BYTES` per list, through ``network.https_proxy`` when it is set (no
  credentials) and directly otherwise; the job only downloads, it never sends anything of the
  site's. One run at a time: a second ``crl fetch`` while one runs stops with an error.
* **What is kept.** A list whose signature verifies with its issuer — a pinned root, or a CA
  certificate seen in a token that chains to one — is stored byte for byte as
  ``state/crl/<issuer-sha256 first 12 hex>-<thisUpdate as YYYYMMDDTHHMMSSZ>.crl`` and described
  in ``state/crl/index.json`` (:data:`INDEX_SCHEMA`). A list that does not verify is not stored;
  the index records the failure. Nothing here is ever deleted or overwritten: a CA may drop an
  expired certificate from later lists, so older lists keep their worth (owner question Q4). A
  second list under a name already taken is not stored either (failure ``name_taken``).

``issuer_sha256`` is the SHA-256 fingerprint of the issuer certificate whose key verified the
list. :func:`crls_for` picks the lists an evidence package needs (D9); :func:`load_stored` reads
them back for the verifier (D10).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from typing import IO, Any, Literal, cast
from urllib.parse import SplitResult, urlsplit

from asn1crypto import crl as asn1_crl
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.types import CertificateIssuerPublicKeyTypes
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_text, fsync_dir
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, to_utc
from arcilogger_core.tsa.asn1 import parse_container
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.verify import fingerprint

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = logging.getLogger("arcilogger.crl")

INDEX_SCHEMA = "arcilogger.crl-index/1"
INDEX_NAME = "index.json"
#: Held (an operating-system lock, released also when the process dies) while ``crl fetch``
#: runs; the file itself stays, empty.
LOCK_NAME = ".fetch.lock"
CRL_SUFFIX = ".crl"
#: A list larger than this is not read to the end (failure ``too_large``).
MAX_CRL_BYTES = 20 * 1024 * 1024
SCHEMES = ("http", "https")
#: ``<issuer-sha256 first 12 hex>-<thisUpdate>.crl``: the only names the index may point at.
STORED_NAME = re.compile(r"^[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z\.crl$")
_CHUNK = 64 * 1024

#: Why a URL gave no stored list (the ``error`` of an index failure). ``name_taken``: a verified
#: list whose stored name (issuer and thisUpdate) another list already has; the first one stays.
FailureCode = Literal[
    "unreachable", "too_large", "not_a_crl", "bad_signature", "unknown_issuer", "name_taken"
]

_CRL_REASON = "2.5.29.21"
#: The signature algorithms the standalone verifier can check (RSA PKCS #1 v1.5, RSASSA-PSS and
#: ECDSA with SHA-1 to SHA-512): a list signed otherwise counts in neither verifier (ADR-0006).
_SIGNATURE_HASHES = frozenset({"sha1", "sha224", "sha256", "sha384", "sha512"})
_SIGNATURE_OIDS = frozenset(
    {
        "1.2.840.113549.1.1.5",  # sha1WithRSAEncryption
        "1.2.840.113549.1.1.14",  # sha224WithRSAEncryption
        "1.2.840.113549.1.1.11",  # sha256WithRSAEncryption
        "1.2.840.113549.1.1.12",  # sha384WithRSAEncryption
        "1.2.840.113549.1.1.13",  # sha512WithRSAEncryption
        "1.2.840.10045.4.1",  # ecdsa-with-SHA1
        "1.2.840.10045.4.3.1",  # ecdsa-with-SHA224
        "1.2.840.10045.4.3.2",  # ecdsa-with-SHA256
        "1.2.840.10045.4.3.3",  # ecdsa-with-SHA384
        "1.2.840.10045.4.3.4",  # ecdsa-with-SHA512
    }
)
_RSASSA_PSS = "1.2.840.113549.1.1.10"


class CrlError(ValueError):
    """Not a revocation list, or ``state/crl/index.json`` cannot be read."""


@dataclass(frozen=True)
class CrlOptions:
    """What :func:`fetch` needs from the configuration (§2.5); :func:`crl_options` reads it."""

    #: ``tsa.crl_urls``; empty: the distribution points of the certificates (D7).
    urls: tuple[str, ...] = ()
    #: ``tsa.timeout_seconds``.
    timeout_seconds: float = 60
    #: ``network.https_proxy``: ``http://host:port``, or empty for a direct connection.
    https_proxy: str = ""
    #: ``tsa.crl_max_age_days``: a newest list older than this is stale. Not used by the
    #: fetch; read here so that every CRL setting is read in one place.
    max_age_days: int = 7
    #: A list larger than this fails with ``too_large`` (reading stops there).
    max_bytes: int = MAX_CRL_BYTES

    def __post_init__(self) -> None:
        if self.https_proxy:
            parts = urlsplit(self.https_proxy)
            try:
                port = parts.port
            except ValueError:  # not a number, or out of range
                port = None
            if (
                parts.scheme != "http"
                or not parts.hostname
                or port is None
                or parts.username is not None
                or parts.password is not None
                or parts.path not in ("", "/")
                or parts.query
                or parts.fragment
            ):
                raise CrlError("network.https_proxy must be http://host:port (no credentials)")


def crl_options(config: Config) -> CrlOptions:
    """The CRL settings of ``config``: the one place that reads them.

    ``tsa.crl_urls``, ``tsa.crl_max_age_days`` and ``network.https_proxy`` are added to
    :class:`~arcilogger_core.config.Config` by W5-T's day-1 configuration pull request (issue
    #98), which has not landed on this branch yet. Until it has, they are read with
    ``getattr`` and the contract's defaults (§2.5); afterwards each becomes a plain attribute.
    ``max_age_days`` is read here for the ``crl_stale`` status of W5-C, not by the fetch.
    """
    tsa, network = config.tsa, getattr(config, "network", None)
    return CrlOptions(
        urls=tuple(str(url) for url in getattr(tsa, "crl_urls", ())),
        timeout_seconds=tsa.timeout_seconds,
        https_proxy=str(getattr(network, "https_proxy", "")),
        max_age_days=int(getattr(tsa, "crl_max_age_days", 7)),
    )


# -- a revocation list ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Crl:
    """A revocation list as the CA signed it, with what the revocation check needs.

    ``revoked`` maps a certificate serial number to its revocation date (UTC); ``reasons`` holds
    the CRLReason code of the entries that carry one (:data:`CRL_REASON_HOLD` for
    certificateHold). The same shape as the standalone verifier's ``Crl`` (ADR-0006).
    """

    #: The list's DER encoding (the stored file may be PEM; ``sha256`` is of the file).
    der: bytes
    #: The issuer's name as text (RFC 4514), as in the index.
    issuer: str
    issuer_name: x509.Name = field(compare=False)
    this_update: datetime
    next_update: datetime | None
    revoked: dict[int, datetime] = field(hash=False, compare=False)
    reasons: dict[int, int] = field(hash=False, compare=False)
    #: SHA-256 fingerprint of the issuer certificate that verified the list ("" if not known).
    issuer_sha256: str = ""
    #: SHA-256 of the bytes as fetched and stored.
    sha256: str = ""
    #: The stored file (None for a list not read from ``state/crl``).
    path: Path | None = None
    #: Where it was fetched from ("" if not known).
    url: str = ""
    #: False when the list is signed with an algorithm the standalone verifier cannot check
    #: (:data:`_SIGNATURE_OIDS`): such a list is signed by no one, for both verifiers.
    known_algorithm: bool = field(default=True, compare=False)
    #: The answers of :meth:`signed_by`, by the certificate's fingerprint: checking many days
    #: asks the same question for each, and a list can be large.
    _signed: dict[str, bool] = field(
        init=False, default_factory=dict, repr=False, hash=False, compare=False
    )

    def signed_by(self, certificate: x509.Certificate) -> bool:
        """Whether ``certificate`` names the list's issuer and its key verifies the signature
        (with an algorithm both verifiers know)."""
        if certificate.subject != self.issuer_name or not self.known_algorithm:
            return False
        key = fingerprint(certificate)
        if key not in self._signed:
            self._signed[key] = self._verifies(certificate)
        return self._signed[key]

    def _verifies(self, certificate: x509.Certificate) -> bool:
        # A key that cannot sign (X25519, X448) raises TypeError.
        key = cast(CertificateIssuerPublicKeyTypes, certificate.public_key())
        try:
            return x509.load_der_x509_crl(self.der).is_signature_valid(key)
        except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
            return False

    @property
    def file_name(self) -> str:
        """The stored name: ``<issuer-sha256 first 12 hex>-<thisUpdate>.crl`` (D8)."""
        return f"{self.issuer_sha256[:12]}-{self.this_update:%Y%m%dT%H%M%SZ}{CRL_SUFFIX}"


def parse_crl(data: bytes) -> Crl:
    """Read a DER or PEM revocation list (no trust decision). Raises :class:`CrlError`.

    The entries are read as the standalone verifier reads them: a CRLReason value the
    ``cryptography`` library does not name (7, or above 10) or an entry extension given twice
    leaves the list readable; a CRLReason that is not an ENUMERATED makes it unreadable.
    """
    try:
        if b"-----BEGIN X509 CRL-----" in data:
            crl = x509.load_pem_x509_crl(data)
        else:
            crl = x509.load_der_x509_crl(data)
        der = crl.public_bytes(Encoding.DER)
        listed = asn1_crl.CertificateList.load(der)
        revoked, reasons = _entries(listed["tbs_cert_list"])
        return Crl(
            der=der,
            issuer=crl.issuer.rfc4514_string(),
            issuer_name=crl.issuer,
            this_update=crl.last_update_utc,
            next_update=crl.next_update_utc,
            revoked=revoked,
            reasons=reasons,
            sha256=hashlib.sha256(data).hexdigest(),
            known_algorithm=_known_algorithm(listed["signature_algorithm"]),
        )
    # Bytes from the network: the parsers raise ValueError, TypeError, but also exceptions of
    # their own (x509.InvalidVersion, x509.DuplicateExtension) or RecursionError.
    except Exception as exc:
        raise CrlError(f"not a certificate revocation list: {exc}") from exc


def _entries(tbs: Any) -> tuple[dict[int, datetime], dict[int, int]]:
    """Serial number → revocation date (UTC), and → CRLReason, of an asn1crypto TBSCertList."""
    revoked: dict[int, datetime] = {}
    reasons: dict[int, int] = {}
    for entry in tbs["revoked_certificates"]:
        serial = int(entry["user_certificate"].native)
        revoked[serial] = to_utc(entry["revocation_date"].native)
        for extension in entry["crl_entry_extensions"]:
            if extension["extn_id"].dotted != _CRL_REASON:
                continue
            # The ENUMERATED's own bytes: asn1crypto checks the tag, not the value (7, 11).
            reasons[serial] = int.from_bytes(extension["extn_value"].parsed.contents, "big")
    return revoked, reasons


def _known_algorithm(algorithm: Any) -> bool:
    """Whether the list's signatureAlgorithm is one the standalone verifier checks."""
    oid = algorithm["algorithm"].dotted
    if oid in _SIGNATURE_OIDS:
        return True
    if oid != _RSASSA_PSS:
        return False
    params = algorithm["parameters"]
    if params.native is None:  # the defaults: SHA-1, MGF1 with SHA-1
        return True
    mgf = params["mask_gen_algorithm"]
    return (
        params["hash_algorithm"]["algorithm"].native in _SIGNATURE_HASHES
        and mgf["algorithm"].native == "mgf1"
        and mgf["parameters"]["algorithm"].native in _SIGNATURE_HASHES
        and params["trailer_field"].native == "trailer_field_bc"
    )


# -- the stored lists -----------------------------------------------------------------------------


def read_index(layout: Layout) -> dict[str, Any]:
    """``state/crl/index.json``, or an empty index when there is none yet."""
    return _index(layout.crl_dir / INDEX_NAME, _index_bytes(layout.crl_dir / INDEX_NAME))


def _index_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CrlError(f"{path} cannot be read: {exc}") from exc


def _index(path: Path, raw: bytes | None) -> dict[str, Any]:
    if raw is None:
        return {"schema": INDEX_SCHEMA, "lists": [], "failures": []}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise CrlError(f"{path} cannot be read: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema") != INDEX_SCHEMA
        or not isinstance(data.get("lists"), list)
        or not isinstance(data.get("failures"), list)
    ):
        raise CrlError(f"{path} is not an {INDEX_SCHEMA} index")
    return data


#: The last answer of :func:`load_stored` and what it was read from: the day checks of a
#: retention run or a report call it once per day, and the stored lists only grow (Q4).
_LOADED: tuple[tuple[Any, ...], list[Crl]] | None = None


def load_stored(layout: Layout) -> list[Crl]:
    """Every list of the index whose file is there and unchanged, oldest ``thisUpdate`` first.

    A missing, changed or unreadable file is left out with a warning in the log (the lists are
    evidence: one that does not match its index entry is not used). The lists are read again
    only when the index or one of its files has changed since the last call (the last answer
    is kept, nothing older)."""
    global _LOADED
    path = layout.crl_dir / INDEX_NAME
    raw = _index_bytes(path)
    index = _index(path, raw)
    names = [_file_name(entry) for entry in index["lists"]]
    key = (
        str(layout.crl_dir.resolve()),
        hashlib.sha256(raw or b"").hexdigest(),
        tuple(_stat(layout.crl_dir / name) if name else None for name in names),
    )
    loaded = _LOADED
    if loaded is not None and loaded[0] == key:
        return list(loaded[1])
    crls = _load(layout, index)
    _LOADED = (key, crls)
    return list(crls)


def _file_name(entry: Any) -> str:
    """An index entry's ``file`` when it is a stored name (:data:`STORED_NAME`), else ""."""
    name = str(entry.get("file") or "") if isinstance(entry, dict) else ""
    return name if STORED_NAME.match(name) else ""


def _stat(path: Path) -> tuple[int, ...] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


def _load(layout: Layout, index: dict[str, Any]) -> list[Crl]:
    crls = []
    for entry in index["lists"]:
        name = _file_name(entry)
        if not name:
            log.warning("CRL index entry without a valid file name: %r", entry)
            continue
        path = layout.crl_dir / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            log.warning("stored CRL %s cannot be read: %s", name, exc)
            continue
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            log.warning("stored CRL %s does not match its SHA-256 in the index", name)
            continue
        try:
            crl = parse_crl(data)
        except CrlError as exc:
            log.warning("stored CRL %s: %s", name, exc)
            continue
        issuer_sha256 = str(entry.get("issuer_sha256") or "")
        url = str(entry.get("url") or "")
        crls.append(replace(crl, issuer_sha256=issuer_sha256, path=path, url=url))
    return sorted(crls, key=lambda crl: (crl.this_update, crl.file_name))


def crls_for(
    layout: Layout,
    issuer: x509.Certificate,
    moments: Iterable[datetime],
    *,
    stored: Sequence[Crl] | None = None,
) -> list[Path]:
    """The stored lists of ``issuer`` an evidence package needs (D9).

    The newest list, plus for each moment (a packaged day's stamp time) the first list with
    ``thisUpdate`` at or after it — the one that can tell whether a certificate was revoked
    before that stamp. A list belongs to ``issuer`` when it names it and its key verifies the
    signature (the standalone verifier's rule). Sorted by ``thisUpdate``, each file once.
    ``stored``: the result of :func:`load_stored`, when the caller already has it.
    """
    candidates = load_stored(layout) if stored is None else stored
    lists = [crl for crl in candidates if crl.signed_by(issuer)]
    lists.sort(key=lambda crl: (crl.this_update, crl.file_name))
    if not lists:
        return []
    chosen = {len(lists) - 1}
    for moment in moments:
        moment = to_utc(moment)
        first = next((i for i, crl in enumerate(lists) if crl.this_update >= moment), None)
        if first is not None:
            chosen.add(first)
    return [crl.path for i, crl in enumerate(lists) if i in chosen and crl.path is not None]


# -- where the lists come from ---------------------------------------------------------------------


def token_certificates(layout: Layout) -> list[x509.Certificate]:
    """The certificates in the newest day token of each provider (the manifest's
    ``tsa.provider``), newest day first."""
    seen: set[str] = set()
    certificates: list[x509.Certificate] = []
    manifests = layout.vault.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/manifest-*.json")
    for manifest in sorted(manifests, reverse=True):
        token = manifest.with_name(manifest.name + ".zd")
        try:
            provider = json.loads(manifest.read_text(encoding="utf-8"))["tsa"]["provider"]
            data = token.read_bytes()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(provider, str) or provider in seen:
            continue
        seen.add(provider)
        certificates += _certificates_of(data)
    return certificates


def _certificates_of(token_data: bytes) -> list[x509.Certificate]:
    try:
        token, _ = parse_container(token_data)
        choices = token["content"]["certificates"] or []
        return [
            x509.load_der_x509_certificate(choice.chosen.dump())
            for choice in choices
            if choice.name == "certificate"
        ]
    except (TsaError, ValueError, TypeError, KeyError):
        return []


def distribution_points(certificates: Iterable[x509.Certificate]) -> list[str]:
    """The http/https CRL distribution points of ``certificates``, each once, in order."""
    urls: list[str] = []
    for certificate in certificates:
        try:
            points = certificate.extensions.get_extension_for_class(
                x509.CRLDistributionPoints
            ).value
        except (x509.ExtensionNotFound, ValueError):
            continue
        for point in points:
            for name in point.full_name or []:
                if isinstance(name, x509.UniformResourceIdentifier) and name.value not in urls:
                    urls.append(name.value)
    return [url for url in urls if _http_url(url)]


def crl_urls(
    options: CrlOptions, roots: Sequence[x509.Certificate], seen: Sequence[x509.Certificate]
) -> list[str]:
    """The URLs of D7: ``tsa.crl_urls`` when set, else the distribution points of the pinned
    roots and of the certificates seen in tokens that chain to them (a certificate anyone can
    add to a token's unsigned set names no URL)."""
    if options.urls:
        return list(dict.fromkeys(url for url in options.urls if _http_url(url)))
    issuers = trusted_issuers(roots, seen)
    chained = [
        certificate
        for certificate in seen
        if certificate in issuers or any(_issued_by(certificate, issuer) for issuer in issuers)
    ]
    return distribution_points([*roots, *chained])


def _http_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() in SCHEMES


def _with_credentials(url: str) -> bool:
    return "@" in urlsplit(url).netloc


def _redacted(url: str) -> str:
    """``url`` without its user name and password (they never reach a log or the index)."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return SplitResult(parts.scheme, f"***@{host}", parts.path, parts.query, "").geturl()


def _issued_by(certificate: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        certificate.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


def trusted_issuers(
    roots: Sequence[x509.Certificate], seen: Sequence[x509.Certificate]
) -> list[x509.Certificate]:
    """The pinned roots, then each CA certificate seen in a token that chains to one of them
    (a TSA certificate signs no list)."""
    trusted = list(roots)
    pending = [certificate for certificate in seen if _is_ca(certificate)]
    while True:
        pending = [certificate for certificate in pending if certificate not in trusted]
        chained = [
            certificate
            for certificate in pending
            if any(_issued_by(certificate, issuer) for issuer in trusted)
        ]
        if not chained:
            return trusted
        trusted += chained


def _is_ca(certificate: x509.Certificate) -> bool:
    try:
        return certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except (x509.ExtensionNotFound, ValueError):
        return False


def _signs_crls(certificate: x509.Certificate) -> bool:
    """keyUsage, when the certificate has it, allows cRLSign (RFC 5280 4.2.1.3)."""
    try:
        return certificate.extensions.get_extension_for_class(x509.KeyUsage).value.crl_sign
    except x509.ExtensionNotFound:
        return True
    except ValueError:
        return False


# -- fetching -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CrlFailure:
    """A URL that gave no stored list: when (site time) and why (:data:`FailureCode`)."""

    url: str
    at: str
    error: FailureCode

    def as_json(self) -> dict[str, Any]:
        return {"url": self.url, "at": self.at, "error": self.error}


@dataclass(frozen=True)
class CrlRun:
    """What one ``crl fetch`` did: the URLs it tried, the index entries of the lists it got
    (stored now, or already stored byte for byte), and the URLs that failed."""

    urls: tuple[str, ...]
    lists: tuple[dict[str, Any], ...] = ()
    failures: tuple[CrlFailure, ...] = ()

    @property
    def exit_code(self) -> int:
        """0 every URL fetched and verified, 1 some failed (recorded), 2 no URL known."""
        if not self.urls:
            return 2
        return 1 if self.failures else 0

    def as_json(self) -> dict[str, Any]:
        return {
            "ok": self.exit_code == 0,
            "urls": list(self.urls),
            "lists": list(self.lists),
            "failures": [failure.as_json() for failure in self.failures],
        }


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects to http/https only (urllib would also follow one to ftp)."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _http_url(newurl):
            raise urllib.error.URLError(f"redirect to a non-HTTP URL ({code})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SettingProxy(urllib.request.ProxyHandler):
    """Every URL through ``network.https_proxy`` (CONNECT for https): no bypass list of the
    environment (``no_proxy``) or of the system is applied, the setting alone decides."""

    def proxy_open(
        self, req: urllib.request.Request, proxy: str, type: str
    ) -> http.client.HTTPResponse | None:
        req.set_proxy(urlsplit(proxy).netloc, "http")
        return None  # the http/https handler opens it, through the proxy


class _TooLargeError(Exception):
    pass


def _download(opener: urllib.request.OpenerDirector, url: str, options: CrlOptions) -> bytes:
    """The body of ``url``; :class:`_TooLargeError` beyond ``max_bytes`` (reading stops there).

    ``timeout_seconds`` bounds each network wait and the whole download: a server that sends a
    byte now and then fails with :class:`TimeoutError` once the time is over (the read under
    way can take one more ``timeout_seconds``; name resolution has the system's own limit)."""
    deadline = time.monotonic() + options.timeout_seconds
    # S310: only http/https URLs get here (crl_urls), and redirects stay on them.
    request = urllib.request.Request(url, headers={"Accept": "application/pkix-crl, */*"})  # noqa: S310
    with opener.open(request, timeout=options.timeout_seconds) as reply:
        length = reply.headers.get("Content-Length") or ""
        if length.isdigit() and int(length) > options.max_bytes:
            raise _TooLargeError
        chunks: list[bytes] = []
        size = 0
        # read1: what one receive gives, so the deadline is looked at between small reads.
        while chunk := reply.read1(_CHUNK):
            size += len(chunk)
            if size > options.max_bytes:
                raise _TooLargeError
            if time.monotonic() > deadline:
                raise TimeoutError(f"not complete after {options.timeout_seconds} s")
            chunks.append(chunk)
    return b"".join(chunks)


def _opener(options: CrlOptions) -> urllib.request.OpenerDirector:
    """Through ``network.https_proxy`` when set; otherwise directly, also when the environment
    or the system names a proxy (the setting is the one place that decides, D13)."""
    proxy = options.https_proxy
    proxies = _SettingProxy({"http": proxy, "https": proxy}) if proxy else None
    return urllib.request.build_opener(
        proxies or urllib.request.ProxyHandler({}), _HttpOnlyRedirects
    )


def fetch_all(
    config: Config, layout: Layout, roots: Sequence[x509.Certificate], now: datetime
) -> CrlRun:
    """``crl fetch`` with the settings of ``config`` (:func:`crl_options`), see :func:`fetch`."""
    return fetch(crl_options(config), layout, roots, now)


def fetch(
    options: CrlOptions, layout: Layout, roots: Sequence[x509.Certificate], now: datetime
) -> CrlRun:
    """Download, verify and store the lists of D7/D8 and update ``state/crl/index.json``.

    ``roots``: the pinned roots (:func:`arcilogger_core.tsa.trusted_roots`). Writes only under
    ``state/crl``, never overwrites or deletes a file there. With no URL nothing is written.
    Raises :class:`CrlError` when another run holds the lock, or when the existing index cannot
    be read (it is then left as it is).
    """
    seen = token_certificates(layout)
    urls = crl_urls(options, roots, seen)
    if not urls:
        return CrlRun(urls=())
    issuers = trusted_issuers(roots, seen)
    opener = _opener(options)
    at = isoformat_site(now, "seconds")
    with _one_run(layout.crl_dir):
        index = read_index(layout)
        lists: dict[str, dict[str, Any]] = {
            str(entry.get("file")): entry for entry in index["lists"] if isinstance(entry, dict)
        }
        tried: list[str] = []
        got: list[dict[str, Any]] = []
        failures: list[CrlFailure] = []
        for url in urls:
            if _with_credentials(url):
                url = _redacted(url)
                log.warning("CRL %s: a URL with a user name or password is not fetched", url)
                outcome: dict[str, Any] | FailureCode = "unreachable"
            else:
                outcome = _fetch_one(opener, url, options, issuers, layout, lists)
            tried.append(url)
            if isinstance(outcome, str):
                failures.append(CrlFailure(url=url, at=at, error=outcome))
                continue
            entry = {**outcome, "url": url, "fetched_at": at}
            lists[entry["file"]] = entry
            got.append(entry)
        document = {
            "schema": INDEX_SCHEMA,
            "lists": [lists[name] for name in sorted(lists)],
            # The failures of the latest run: a URL that works again drops out.
            "failures": [failure.as_json() for failure in failures],
        }
        atomic_write_text(
            layout.crl_dir / INDEX_NAME, json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        )
    return CrlRun(urls=tuple(tried), lists=tuple(got), failures=tuple(failures))


@contextmanager
def _one_run(folder: Path) -> Iterator[None]:
    """Hold the lock of ``state/crl`` (:data:`LOCK_NAME`): the ``CrlFetch`` task and a run
    started by hand never read and write the index at the same time."""
    folder.mkdir(parents=True, exist_ok=True)
    handle = os.open(folder / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            _lock(handle)
        except OSError as exc:
            raise CrlError(
                "another 'crl fetch' is running (state/crl is locked); try again later"
            ) from exc
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        os.close(handle)


if sys.platform == "win32":

    def _lock(handle: int) -> None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)

    def _unlock(handle: int) -> None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)

else:

    def _lock(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


def _fetch_one(
    opener: urllib.request.OpenerDirector,
    url: str,
    options: CrlOptions,
    issuers: Sequence[x509.Certificate],
    layout: Layout,
    lists: dict[str, dict[str, Any]],
) -> dict[str, Any] | FailureCode:
    """The index entry of the list at ``url`` (stored now or before), or why there is none."""
    try:
        data = _download(opener, url, options)
    except _TooLargeError:
        log.warning("CRL %s: larger than %d bytes", url, options.max_bytes)
        return "too_large"
    except (urllib.error.URLError, HTTPException, OSError, ValueError) as exc:
        log.warning("CRL %s: not reachable: %s", url, exc)
        return "unreachable"
    try:
        crl = parse_crl(data)
    except CrlError as exc:
        log.warning("CRL %s: %s", url, exc)
        return "not_a_crl"
    # The issuer's name comes from bytes not verified yet: logged with repr (no line breaks).
    named = [
        issuer for issuer in issuers if issuer.subject == crl.issuer_name and _signs_crls(issuer)
    ]
    if not named:
        log.warning("CRL %s: issuer %r is not a pinned or seen CRL issuer", url, crl.issuer)
        return "unknown_issuer"
    signer = next((issuer for issuer in named if crl.signed_by(issuer)), None)
    if signer is None:
        log.warning("CRL %s: signature does not verify with %r", url, crl.issuer)
        return "bad_signature"
    known = next(
        (
            entry
            for entry in lists.values()
            if entry.get("sha256") == crl.sha256 and _file_name(entry)
        ),
        None,
    )
    if known is not None and (layout.crl_dir / _file_name(known)).is_file():
        return {**known}  # the same bytes are stored already
    crl = replace(crl, issuer_sha256=fingerprint(signer))
    path = layout.crl_dir / crl.file_name
    if _store_new(path, data):
        log.info("CRL %s stored as %s", url, path.name)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != crl.sha256:
        # Another list with the same issuer and thisUpdate: the one stored first stays and the
        # new one is not stored (D8: a file is never overwritten); the run reports it.
        log.error("CRL %s: %s is taken by another list; this one is not stored", url, path.name)
        return "name_taken"
    return _entry(crl)


def _store_new(path: Path, data: bytes) -> bool:
    """Write ``data`` as ``path`` unless that name exists (then False). The bytes are written
    and flushed under a private temporary name first and then linked to ``path``: a link never
    replaces a file, and ``path`` never holds part of a list. The tempfile module removes the
    temporary name; the list stays under ``path``."""
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".", suffix=".tmp", delete_on_close=False
    ) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            return False
    fsync_dir(path.parent)
    return True


def _entry(crl: Crl) -> dict[str, Any]:
    """An index entry without ``url`` and ``fetched_at`` (the caller adds them)."""
    return {
        "file": crl.file_name,
        "issuer": crl.issuer,
        "issuer_sha256": crl.issuer_sha256,
        "this_update": crl.this_update.isoformat(),
        "next_update": crl.next_update.isoformat() if crl.next_update else None,
        "sha256": crl.sha256,
    }
