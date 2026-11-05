"""Revocation lists (SİL / CRL) of the time-stamp certificates (Wave 5 D7-D9).

The ``CrlFetch`` task runs ``arcilogger-core crl fetch`` once a day:

* **Where from.** The URLs of ``tsa.crl_urls``; when that is empty, the http/https CRL
  distribution points of the pinned roots and of the certificates in the newest token of each
  provider that chain to one of them. No URL is written into the code: Kamu SM's CRL addresses
  are unconfirmed (K10 in docs/kamusm.md, "sahada doğrula"). A URL that carries a user name or
  password is never fetched, and is logged and recorded without them.
* **How.** ``urllib``, at most :data:`MAX_CRL_BYTES` per list, through ``network.https_proxy``
  when it is set (no credentials) and directly otherwise; ``tsa.timeout_seconds`` bounds each
  URL's whole exchange (connection, headers, redirects and body). The job only downloads, it
  never sends anything of the site's. One run at a time: a second ``crl fetch`` while one runs
  stops with an error.
* **What is kept.** A list whose signature verifies with its issuer — a pinned root, or a CA
  certificate seen in any day token that chains to one — is stored byte for byte as
  ``state/crl/<issuer-sha256 first 12 hex>-<thisUpdate as YYYYMMDDTHHMMSSZ>-<SHA-256 of the
  file, first 8 hex>.crl`` (C7: two lists with the same thisUpdate are both kept; the same bytes
  again keep their file) and described in ``state/crl/index.json`` (:data:`INDEX_SCHEMA`). A
  list that does not verify is not stored; the index records the failure. A delta list
  (DeltaCRLIndicator) is not stored either (``delta_not_supported``): neither verifier applies
  one. Nothing here is ever deleted or overwritten: a CA may drop an expired certificate from
  later lists, so older lists keep their worth (owner question Q4). A stored file that no longer
  holds the bytes its index entry (or its name) gives was changed after storing: it stays as it
  is, as evidence, and is recorded as ``stored_file_changed`` (C14). Every run hashes every
  stored file, also of the lists no URL serves any more; a file that is gone counts the same.

``issuer_sha256`` is the SHA-256 fingerprint of the issuer certificate whose key verified the
list. :func:`crls_for` picks the lists an evidence package needs (D9); :func:`load_stored` reads
them back for the verifier (D10). A stored file is parsed once per process while it stays as it
is (:func:`read_crl_file`): the lists only grow (Q4), and a large one takes a second to parse.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import json
import logging
import os
import re
import socket
import ssl
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from typing import IO, Any, Literal, cast, overload
from urllib.parse import SplitResult, urlsplit

from asn1crypto import crl as asn1_crl
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.types import CertificateIssuerPublicKeyTypes

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
#: ``<issuer-sha256 first 12 hex>-<thisUpdate>-<file SHA-256 first 8 hex>.crl``: the only names
#: the index may point at (C7; W5-B was not released with any other).
STORED_NAME = re.compile(r"^[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.crl$")
_CHUNK = 64 * 1024
#: A PEM block, as the standalone verifier finds them (``pem_blocks``).
_PEM = re.compile(rb"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.DOTALL)

#: Why a URL gave no stored list (the ``error`` of an index failure). ``delta_not_supported``: a
#: verified delta list (C7), which neither verifier applies. ``stored_file_changed``: the list is
#: stored already, but its file no longer holds its bytes or is gone (C14; the file stays as it
#: is), also for a list the URL served before.
FailureCode = Literal[
    "unreachable",
    "too_large",
    "not_a_crl",
    "bad_signature",
    "unknown_issuer",
    "delta_not_supported",
    "stored_file_changed",
]

#: Why a run stopped before it wrote a new index (the ``error`` of the index's ``run_failure``,
#: #171): ``stored_file_unreadable``, a stored list that cannot be read. Not a failure of a URL.
RunFailureCode = Literal["stored_file_unreadable"]

_CRL_REASON = "2.5.29.21"
_DELTA_CRL_INDICATOR = "2.5.29.27"
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

    #: The list's DER encoding: the file itself, or its one PEM block (``sha256`` is of the file).
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
    #: A delta list (DeltaCRLIndicator): it is neither stored nor used (C7).
    delta: bool = field(default=False, compare=False)
    #: The answers of :meth:`signed_by`, by the certificate's fingerprint: checking many days
    #: asks the same question for each, and a list can be large. A copy made with
    #: ``dataclasses.replace`` shares them (the same DER).
    _signed: dict[str, bool] = field(default_factory=dict, repr=False, hash=False, compare=False)

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
        """The stored name: ``<issuer-sha256 first 12 hex>-<thisUpdate>-<sha256 first 8
        hex>.crl`` (D8, C7)."""
        return (
            f"{self.issuer_sha256[:12]}-{self.this_update:%Y%m%dT%H%M%SZ}-{self.sha256[:8]}"
            f"{CRL_SUFFIX}"
        )


def parse_crl(data: bytes) -> Crl:
    """Read a DER or PEM revocation list (no trust decision). Raises :class:`CrlError`.

    The entries are read as the standalone verifier reads them: a CRLReason value the
    ``cryptography`` library does not name (7, or above 10) or an entry extension given twice
    leaves the list readable; a CRLReason that is not an ENUMERATED makes it unreadable. PEM
    data (any ``-----BEGIN``, as there) must hold exactly one ``X509 CRL`` block: the standalone
    verifier reads every block of a file, this module one list per file.
    """
    try:
        der = _der_of(data)
        crl = x509.load_der_x509_crl(der)
        listed = asn1_crl.CertificateList.load(der)
        tbs = listed["tbs_cert_list"]
        revoked, reasons = _entries(tbs)
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
            delta=any(
                extension["extn_id"].dotted == _DELTA_CRL_INDICATOR
                for extension in tbs["crl_extensions"]
            ),
        )
    # Bytes from the network: the parsers raise ValueError, TypeError, but also exceptions of
    # their own (x509.InvalidVersion, x509.DuplicateExtension) or RecursionError.
    except Exception as exc:
        raise CrlError(f"not a certificate revocation list: {exc}") from exc


def _der_of(data: bytes) -> bytes:
    """``data`` itself (DER), or the DER of its one ``X509 CRL`` PEM block."""
    if b"-----BEGIN " not in data:
        return data
    blocks = [match for match in _PEM.finditer(data) if match.group(1) == b"X509 CRL"]
    if len(blocks) != 1:
        raise ValueError(f"PEM data with {len(blocks)} X509 CRL blocks (one list per file)")
    try:
        return base64.b64decode(re.sub(rb"\s+", b"", blocks[0].group(2)), validate=True)
    except binascii.Error as exc:
        raise ValueError("bad PEM base64") from exc


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
    # One try for the folder and the file: Path.exists() raises too when state/crl cannot be
    # searched (EACCES), and the day checks turn only a CrlError into "revocation not checked".
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
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


@dataclass(frozen=True)
class _Read:
    """A stored file as :func:`read_crl_file` last read it."""

    stat: tuple[int, ...]
    sha256: str
    #: The parsed list (``path`` set), or why the file is not one.
    crl: Crl | str


#: What :func:`read_crl_file` read, by path: the day checks of a retention run or a report, and
#: the builder, ask for the same files again, and the stored lists only grow (Q4). Only the
#: parsed list is kept, not the file's bytes (a DER file is the list's own DER).
_READ: dict[Path, _Read] = {}


def read_crl_file(path: Path) -> Crl:
    """The list stored as ``path`` (:func:`parse_crl`, with ``path`` set), parsed once per process
    while the file stays as it is (its size, times and inode); ``sha256`` is of the file as read.
    Raises :class:`CrlError` for a file that is not a list, ``OSError`` for one that cannot be
    read."""
    read = _read(path)
    if isinstance(read.crl, str):
        raise CrlError(read.crl)
    return read.crl


def _read(path: Path) -> _Read:
    # The stat is taken before the bytes are read: a change while reading gives another stat
    # next time, and the file is read again.
    stat = _stat(path.stat())
    known = _READ.get(path)
    if known is not None and known.stat == stat:
        return known
    data = path.read_bytes()
    try:
        crl: Crl | str = replace(parse_crl(data), path=path)
    except CrlError as exc:
        crl = str(exc)
    read = _Read(stat, hashlib.sha256(data).hexdigest(), crl)
    _READ[path] = read
    return read


def _stat(stat: os.stat_result) -> tuple[int, ...]:
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


@dataclass(frozen=True)
class StoredLists(Sequence[Crl]):
    """What :func:`load_stored` read: the usable lists, oldest ``thisUpdate`` first (a sequence
    of :class:`Crl`), and the index entries it left out because their file is not the list the
    entry describes (C14)."""

    lists: tuple[Crl, ...] = ()
    #: The ``file`` of each index entry whose file is missing, holds other bytes than the
    #: entry's ``sha256`` or its name give, or holds no usable list: a person has to look.
    mismatched: tuple[str, ...] = ()

    @overload
    def __getitem__(self, index: int) -> Crl: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Crl]: ...

    def __getitem__(self, index: int | slice) -> Crl | Sequence[Crl]:
        return self.lists[index]

    def __len__(self) -> int:
        return len(self.lists)


def load_stored(layout: Layout) -> StoredLists:
    """Every list of the index whose file is there and unchanged, oldest ``thisUpdate`` first.

    An entry whose file is missing, changed (its SHA-256 is not the entry's, or not the one its
    name gives) or not a usable list is left out and named in ``mismatched``, with a warning in
    the log: the lists are evidence, and the file stays as it is for a person to look at (C14).
    An entry without a stored name (:data:`STORED_NAME`) is left out with a warning in the log.
    Raises :class:`CrlError` when the index or a stored file cannot be read (permissions): the
    revocation is then not checked, rather than checked without that list. Each file is parsed
    once per process (:func:`read_crl_file`)."""
    index = read_index(layout)
    crls: list[Crl] = []
    mismatched: list[str] = []
    for entry in index["lists"]:
        name = _file_name(entry)
        if not name:
            log.warning("CRL index entry without a valid file name: %r", entry)
            continue
        crl = _indexed(layout.crl_dir / name, entry)
        if crl is None:
            mismatched.append(name)
            continue
        issuer_sha256 = str(entry.get("issuer_sha256") or "")
        url = str(entry.get("url") or "")
        crls.append(replace(crl, issuer_sha256=issuer_sha256, url=url))
    crls.sort(key=lambda crl: (crl.this_update, crl.file_name))
    return StoredLists(tuple(crls), tuple(mismatched))


def _indexed(path: Path, entry: dict[str, Any]) -> Crl | None:
    """The list of an index entry, or None (logged) when its file is not what the entry says."""
    name = path.name
    try:
        read = _read(path)
    except FileNotFoundError:
        log.warning("stored CRL %s is missing", name)
        return None
    except OSError as exc:
        raise CrlError(f"stored CRL {name} cannot be read: {exc}") from exc
    if not _matches(name, read.sha256, entry):
        log.warning("stored CRL %s does not match its SHA-256 in the index", name)
        return None
    if isinstance(read.crl, str):
        log.warning("stored CRL %s: %s", name, read.crl)
        return None
    if read.crl.delta:
        log.warning("stored CRL %s is a delta list; it is not used", name)
        return None
    return read.crl


def _matches(name: str, sha256: str, entry: dict[str, Any]) -> bool:
    """Whether a stored file with this SHA-256 holds the bytes its index entry and its name
    give."""
    return sha256 == entry.get("sha256") and name.endswith(f"-{sha256[:8]}{CRL_SUFFIX}")


def _file_name(entry: Any) -> str:
    """An index entry's ``file`` when it is a stored name (:data:`STORED_NAME`), else ""."""
    name = str(entry.get("file") or "") if isinstance(entry, dict) else ""
    return name if STORED_NAME.match(name) else ""


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


@dataclass(frozen=True)
class TokenCertificates:
    """The certificates of the vault's day tokens, each once, newest day first."""

    #: In the newest day token of each provider (the manifest's ``tsa.provider``): their
    #: distribution points are the URLs of D7.
    newest: tuple[x509.Certificate, ...] = ()
    #: In any day token: a CA certificate among them that chains to a pinned root may sign a
    #: list, also after the provider changed its intermediate CA.
    every: tuple[x509.Certificate, ...] = ()


def token_certificates(layout: Layout) -> list[x509.Certificate]:
    """The certificates in the newest day token of each provider (the manifest's
    ``tsa.provider``), newest day first (:attr:`TokenCertificates.newest`)."""
    return list(scan_tokens(layout).newest)


def scan_tokens(layout: Layout) -> TokenCertificates:
    """The certificates of every day token (:class:`TokenCertificates`). Each manifest and token
    is read once (small files); a certificate that many tokens carry is loaded once."""
    providers: set[str] = set()
    loaded: dict[bytes, x509.Certificate] = {}
    newest: dict[bytes, x509.Certificate] = {}
    manifests = layout.vault.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/manifest-*.json")
    for manifest in sorted(manifests, reverse=True):
        token = manifest.with_name(manifest.name + ".zd")
        try:
            provider = json.loads(manifest.read_text(encoding="utf-8"))["tsa"]["provider"]
            data = token.read_bytes()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        certificates = _certificates_of(data, loaded)
        if isinstance(provider, str) and provider not in providers:
            providers.add(provider)
            newest.update((der, loaded[der]) for der in certificates)
    return TokenCertificates(tuple(newest.values()), tuple(loaded.values()))


def _certificates_of(token_data: bytes, loaded: dict[bytes, x509.Certificate]) -> list[bytes]:
    """The DER of the token's certificates, each loaded into ``loaded`` once."""
    try:
        token, _ = parse_container(token_data)
        choices = token["content"]["certificates"] or []
        ders = [choice.chosen.dump() for choice in choices if choice.name == "certificate"]
        for der in ders:
            if der not in loaded:
                loaded[der] = x509.load_der_x509_certificate(der)
    except (TsaError, ValueError, TypeError, KeyError):
        return []
    return ders


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
    options: CrlOptions,
    roots: Sequence[x509.Certificate],
    seen: Sequence[x509.Certificate],
    known: Sequence[x509.Certificate] = (),
) -> list[str]:
    """The URLs of D7: ``tsa.crl_urls`` when set, else the distribution points of the pinned
    roots and of the certificates seen in tokens (``seen``, the newest of each provider) that
    chain to them (a certificate anyone can add to a token's unsigned set names no URL).
    ``known``: further certificates of tokens a chain may pass through."""
    if options.urls:
        return list(dict.fromkeys(url for url in options.urls if _http_url(url)))
    issuers = trusted_issuers(roots, [*seen, *known])
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


class _Deadline:
    """The end of one URL's exchange: once ``seconds`` have passed, every connection the
    exchange opened is cut (shut down at the socket, also while the headers or the body
    arrive), until the exchange is over. Connections join through :data:`_EXCHANGE`. A TLS
    handshake under way is not cut (its socket is the TLS module's until it ends): Python's own
    handshake limit, one socket timeout, bounds it, and the cut follows right after it."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.expired = False
        self._connections: list[http.client.HTTPConnection] = []
        self._lock = threading.Lock()
        self._over = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="crl-deadline", daemon=True)

    def __enter__(self) -> _Deadline:
        self._token = _EXCHANGE.set(self)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._over.set()
        self._thread.join()
        _EXCHANGE.reset(self._token)

    def add(self, connection: http.client.HTTPConnection) -> None:
        with self._lock:
            self._connections.append(connection)

    def _watch(self) -> None:
        if self._over.wait(self.seconds):
            return
        self.expired = True
        # Again and again: a connection may get its socket only after the time is over.
        while True:
            with self._lock:
                for connection in self._connections:
                    _cut(connection)
            if self._over.wait(0.05):
                return


#: The deadline of the exchange under way in this thread (:func:`_download`).
_EXCHANGE: ContextVar[_Deadline | None] = ContextVar("crl_exchange", default=None)


def _cut(connection: http.client.HTTPConnection) -> None:
    sock = connection.sock
    if sock is None:
        return
    # The plain socket's shutdown, also under TLS: a blocked read returns at once. OSError: not
    # connected (any more), or a TLS handshake under way (the plain socket is detached then).
    with suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


def _join_exchange(connection: http.client.HTTPConnection) -> None:
    deadline = _EXCHANGE.get()
    if deadline is not None:
        deadline.add(connection)


class _HttpConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        _join_exchange(self)
        super().connect()


class _HttpsConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        _join_exchange(self)
        super().connect()


class _HttpHandler(urllib.request.HTTPHandler):
    """urllib's handler with connections the deadline of the download can cut."""

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HttpConnection, req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    """urllib's handler with connections the deadline of the download can cut, and the system's
    certificate authorities (as urllib's)."""

    def __init__(self) -> None:
        self._tls = ssl.create_default_context()
        super().__init__(context=self._tls)

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HttpsConnection, req, context=self._tls)


def _download(opener: urllib.request.OpenerDirector, url: str, options: CrlOptions) -> bytes:
    """The body of ``url``; :class:`_TooLargeError` beyond ``max_bytes`` (reading stops there).

    ``timeout_seconds`` bounds each network wait and the whole exchange — connection, headers,
    redirects and body: a server that sends a byte now and then fails with
    :class:`TimeoutError` once the time is over. Only name resolution (the system's own limit),
    a connection attempt under way (``timeout_seconds`` for each address) and a TLS handshake
    under way (Python's handshake limit, ``timeout_seconds``) can take longer."""
    with _Deadline(options.timeout_seconds) as deadline:
        try:
            data = _exchange(opener, url, options, deadline)
        except (urllib.error.URLError, HTTPException, OSError, ValueError) as exc:
            if deadline.expired:  # the connection was cut
                raise TimeoutError(f"not complete after {options.timeout_seconds} s") from exc
            raise
    if deadline.expired:  # cut while reading the body: what came is not the whole list
        raise TimeoutError(f"not complete after {options.timeout_seconds} s")
    return data


def _exchange(
    opener: urllib.request.OpenerDirector, url: str, options: CrlOptions, deadline: _Deadline
) -> bytes:
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
            if deadline.expired:
                raise TimeoutError(f"not complete after {options.timeout_seconds} s")
            chunks.append(chunk)
    return b"".join(chunks)


def _opener(options: CrlOptions) -> urllib.request.OpenerDirector:
    """Through ``network.https_proxy`` when set; otherwise directly, also when the environment
    or the system names a proxy (the setting is the one place that decides, D13)."""
    proxy = options.https_proxy
    proxies = _SettingProxy({"http": proxy, "https": proxy}) if proxy else None
    return urllib.request.build_opener(
        proxies or urllib.request.ProxyHandler({}),
        _HttpOnlyRedirects,
        _HttpHandler,
        _HttpsHandler,
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
    ``state/crl``, never overwrites or deletes a file there. With no URL the index is still
    written, with the lists stored before and no fetch failure, so the run is seen (#151). A
    stored list whose file no longer holds its bytes (or is gone) is the failure
    ``stored_file_changed`` of its URL (C14), also when the URL serves a newer list by now: the
    file and its index entry stay as they are. Raises :class:`CrlError` when another run holds
    the lock or the existing index cannot be read (the index is then left as it is), and when a
    stored file cannot be read: the index is then written as it was read, with ``run_failure``
    (the run's ``at`` and ``stored_file_unreadable``, #171), which the next completed run drops.
    """
    tokens = scan_tokens(layout)
    urls = crl_urls(options, roots, tokens.newest, tokens.every)
    # Every CA certificate of any token: a list of an intermediate the provider no longer
    # signs with still verifies.
    issuers = trusted_issuers(roots, tokens.every)
    opener = _opener(options)
    at = isoformat_site(now, "seconds")
    with _one_run(layout.crl_dir):
        # A run stopped between _store_new's link and the tempfile module's cleanup (a reboot,
        # the task's time limit) leaves the temporary name, a hard link to a stored list when
        # the link was made, at which the installer's ACL step stops (W5-E-9). Only that name
        # goes (tempfile's ``.`` + 8 random characters + ``.tmp``), never a list (#170, Q15).
        with os.scandir(layout.crl_dir) as entries:
            leftovers = [
                entry.name
                for entry in entries
                if re.fullmatch(r"\.[a-z0-9_]{8}\.tmp", entry.name)
                and entry.is_file(follow_symlinks=False)
            ]
        for name in sorted(leftovers):
            try:
                (layout.crl_dir / name).unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.warning("state/crl: leftover temporary file %s not removed: %s", name, exc)
                continue
            log.warning("state/crl: removed the leftover temporary file %s of a stopped run", name)
        index = read_index(layout)
        lists: dict[str, dict[str, Any]] = {
            str(entry.get("file")): entry for entry in index["lists"] if isinstance(entry, dict)
        }
        try:
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
            failures.extend(_changed_files(layout, lists.values(), failures, at))
            document = {
                "schema": INDEX_SCHEMA,
                "lists": [lists[name] for name in sorted(lists)],
                # The failures of the latest run: a URL that works again drops out.
                "failures": [failure.as_json() for failure in failures],
            }
            atomic_write_text(
                layout.crl_dir / INDEX_NAME,
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            )
        except CrlError:
            # A stored list that cannot be read (_file_sha256): the run stops, and the index as it
            # was read records that, not this run's lists (they are half done).
            _record_run_failure(layout, index, at, "stored_file_unreadable")
            raise
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


def _record_run_failure(
    layout: Layout, index: dict[str, Any], at: str, error: RunFailureCode
) -> None:
    """Write ``index`` as it was read, with ``run_failure``: the Sistem page sees the run that
    stopped. Only its time and code, never the error's text (it names the file). A write that
    fails is logged: the run's own error is the one raised."""
    document = {**index, "run_failure": {"at": at, "error": error}}
    try:
        atomic_write_text(
            layout.crl_dir / INDEX_NAME, json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        )
    except OSError as exc:
        log.error("CRL index: the stopped run cannot be recorded: %s", exc)


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
    if crl.delta:
        log.warning("CRL %s: a delta list is not stored (neither verifier applies one)", url)
        return "delta_not_supported"
    known = next(
        (
            entry
            for entry in lists.values()
            if entry.get("sha256") == crl.sha256 and _file_name(entry)
        ),
        None,
    )
    if known is not None:
        stored = layout.crl_dir / _file_name(known)
        digest = _file_sha256(stored)
        if digest == crl.sha256:
            return {**known}  # the same bytes are stored already
        if digest is not None:
            return _changed(url, stored)
        # C20: the file of a known list is gone. It is stored again, and the run says so: a
        # deletion is something a person must see.
        _store_new(stored, data)
        log.error("CRL %s: the stored file %s was gone; stored again", url, stored)
        return "stored_file_changed"
    crl = replace(crl, issuer_sha256=fingerprint(signer))
    path = layout.crl_dir / crl.file_name
    if _store_new(path, data):
        log.info("CRL %s stored as %s", url, path.name)
    elif _file_sha256(path) != crl.sha256:
        # The name carries the bytes' hash: other bytes under it were changed after storing.
        return _changed(url, path)
    return _entry(crl)


def _changed(url: str, path: Path) -> FailureCode:
    """A stored file that no longer holds the bytes of its list (C14). It stays as it is: it is
    evidence of the change, for a person to look at (D8)."""
    log.error("CRL %s: the stored file %s no longer holds this list; it was changed", url, path)
    return "stored_file_changed"


def _changed_files(
    layout: Layout,
    entries: Iterable[dict[str, Any]],
    failures: Sequence[CrlFailure],
    at: str,
) -> list[CrlFailure]:
    """``stored_file_changed`` (C14) for each index entry whose file is gone or no longer holds
    the entry's bytes, with the entry's URL: the list a URL served before is usually not the
    one it serves now, and the old one is the evidence for the stamps it covers. Only hashed,
    not parsed (:func:`_file_sha256`); a URL with this failure already is not named twice."""
    named = {failure.url for failure in failures if failure.error == "stored_file_changed"}
    changed: list[CrlFailure] = []
    for entry in entries:
        name = _file_name(entry)
        url = str(entry.get("url") or "")
        if not name or url in named:
            continue
        digest = _file_sha256(layout.crl_dir / name)
        if digest is not None and _matches(name, digest, entry):
            continue
        if digest is None:
            log.error("CRL %s: the stored file %s is gone", url, name)
        else:
            _changed(url, layout.crl_dir / name)
        named.add(url)
        changed.append(CrlFailure(url=url, at=at, error="stored_file_changed"))
    return changed


def _file_sha256(path: Path) -> str | None:
    """The SHA-256 of a stored file, None when it is not there: the one :func:`read_crl_file`
    read while the file stays as it is, else hashed now (not parsed). Raises :class:`CrlError`
    when it cannot be read."""
    try:
        known = _READ.get(path)
        if known is not None and known.stat == _stat(path.stat()):
            return known.sha256
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CrlError(f"stored CRL {path.name} cannot be read: {exc}") from exc


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
