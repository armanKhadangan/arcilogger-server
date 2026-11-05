"""Re-stamping (renewal) of time stamps whose TSA certificate comes due (Wave 5 §2.1, D1-D6).

A time stamp stays valid for the moment it was made, but its TSA certificate expires. Before
it does, the ``Renew`` task stamps a **renewal record** that lists the token by its SHA-256, so
the old token is protected by a newer stamp of a certificate that is still valid. Nothing
sealed is ever touched: renewals are new files under ``vault/renewals/`` (rule 2).

**The record** (:data:`RENEWAL_SCHEMA`, canonical JSON, :func:`build_record`): ``created_at``
in site time, the ``system`` and ``tsa`` blocks of a manifest (without the host), ``reason``
(``due``, or ``test`` when the provider's claim is not legal grade, D5) and ``tokens`` sorted by
``path``. Each entry carries its ``kind`` (``day``, ``deletion``, ``report`` or ``renewal``), the
token's path and SHA-256, its certificate's ``notAfter`` in UTC and the hash of what the token
stamps: the manifest of a day, the record of a deletion or a renewal, the PDF of a report. An
entry for an older renewal also repeats every hash that renewal protected (:func:`protects`,
D2), so the raw-text match of the evidence builder picks up a whole chain for one day.

**The status of a token** (:func:`compute_statuses`, §2.1.2), from facts read elsewhere:

* ``not_due`` while its certificate has more than ``tsa.renew_before_days`` left;
* ``covered`` when a renewal that counts lists its ``token_sha256`` and was stamped before the
  certificate expired; ``late`` when the only ones that count were stamped after;
* ``uncovered`` when the certificate has expired and no renewal counts; otherwise ``due``.

A renewal counts when it is *eligible* (its token verifies, the claim is legal grade, its root
is one the site listed, it is not a stamp of the test TSA and it is not revoked: rule 4, D5, C15)
and its own token is ``not_due`` or ``covered`` itself: the check follows the chain of renewals.

**The status of one day** (:func:`day_renewal`) is what ``chain.verify_day(..., renewals=True)``
reports: the day token's status with the code, level and TR/EN text the standalone verifier
gives the same day (§2.1.4, rule 5). It never adds an error or a warning to the day (D6).

**The tokens of a data directory** (:func:`renewal_status`, D3): the token of every sealed day,
of every deletion record under its final name, of every stored official report
(``exports/reports/*.pdf.zd``) and of every renewal record, except one written with
``reason: "test"``. Each certificate's ``notAfter`` is read from the token itself, never from
``state/seal``. A token that cannot be read is left out and named in the report's
``unreadable``, one whose imprint is not the file it stamps as it is today (a changed file) in
``mismatched``, so no renewal ever lists the hash of a changed file; one that disappears
during the scan (the retention job deleting its day) is skipped. Each renewal token is verified
against the pinned roots and the stored revocation lists, with the EKU rule its own record was
written under (``tsa.require_critical_eku``, K6), as a day's token is judged by its manifest's
(contract W5-A-17). :func:`renewal_status` only reads; ``state/renewal/renewal.json`` is written
by the ``Renew`` task's command line (``arcilogger-core renew``) with :func:`write_state`.
``state/renewal/`` is protected: only the tasks write it, so no service can silence the
re-stamping alerts or hold the lock (contract W5-E-4).

**A run** (:func:`renew`, D4) writes at most one record, covering every token that is ``due``
or ``uncovered``, under ``state/renewal/renewal.lock`` (never inside ``vault/renewals``). As a
deletion record is (``retention``), the record is written as ``<name>.json.part``, stamped and
verified; only then is the token renamed to its final name, then the record, and both are locked
like sealed files. A failed stamp removes both, so a record never exists under its final name
without its token; leftovers of an interrupted run are tidied by the next one (contract W5-A-3).
A run whose provider claims legal grade while no pinned root is listed takes no stamp: its
renewals would never count (W5-A-13). A stamp whose chain ends at a pinned root that is not
listed is removed with its record, as a revoked one is (C15), also when the next run finds it
left by an interrupted one.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from cryptography import x509

from arcilogger_core import PRODUCT_NAME, __version__
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import sealed_days
from arcilogger_core.config import Config
from arcilogger_core.crl import Crl, CrlError, load_stored
from arcilogger_core.fsutil import atomic_write_bytes, fsync_dir
from arcilogger_core.immutability import is_locked, lock
from arcilogger_core.layout import Layout
from arcilogger_core.retention import records as deletion_records
from arcilogger_core.timeutil import (
    isoformat_site,
    require_aware,
    site_day,
    to_site,
    to_utc,
    utc_now,
)
from arcilogger_core.tsa.asn1 import READABLE_DIGESTS
from arcilogger_core.tsa.base import (
    TimestampProvider,
    TokenInfo,
    TsaConfigurationError,
    TsaError,
    TsaResponseError,
    VerifyResult,
)
from arcilogger_core.tsa.client import token_path_for
from arcilogger_core.tsa.verify import (
    confirmed_legal_grade,
    fingerprint,
    inspect_token,
    is_test_stamp,
    verify_token,
)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = logging.getLogger("arcilogger.renewal")

RENEWAL_SCHEMA: Final = "arcilogger.renewal/1"
#: What a renewal record lists: the token of a sealed day, of a deletion record, of a stored
#: official report (``exports/reports/*.pdf.zd``, owner answer Q3) or of an earlier renewal.
Kind = Literal["day", "deletion", "report", "renewal"]
KINDS: Final[tuple[Kind, ...]] = ("day", "deletion", "report", "renewal")
#: The long-term status of a token; W5-C's reader of the state file takes exactly these.
Status = Literal["not_due", "due", "covered", "late", "uncovered"]
STATUSES: Final[tuple[Status, ...]] = ("not_due", "due", "covered", "late", "uncovered")
#: ``due``: written by a legal-grade provider; ``test``: by any other, and never counts (D5).
Reason = Literal["due", "test"]
REASONS: Final[tuple[Reason, ...]] = ("due", "test")
#: ``tsa.renew_before_days`` when the configuration has no such key (§2.5, owner question Q5).
DEFAULT_RENEW_BEFORE_DAYS: Final = 180
#: The stored official reports and their tokens, under ``exports`` (the API's report store).
REPORTS_FOLDER: Final = "reports"
REPORT_TOKEN_SUFFIX: Final = ".pdf.zd"  # noqa: S105 - a file suffix
#: ``state/renewal/``: the ``Renew`` task's state file and lock, in a folder of their own that
#: only the tasks write (protected, ``Get-ArciAclPlan``; contract W5-E-4).
STATE_FOLDER: Final = "renewal"
#: ``state/renewal/renewal.lock``: one run at a time (the ``Renew`` task and a run started by
#: hand).
LOCK_NAME: Final = "renewal.lock"
#: Suffix of a record (and its token) that is not complete yet.
PART_SUFFIX: Final = ".part"

_HEX64 = re.compile(r"[0-9a-f]{64}")
#: A Windows drive (``C:``); with an empty segment (a leading ``/``, a ``//`` UNC prefix) it
#: marks a path that is not relative.
_DRIVE = re.compile(r"[A-Za-z]:")
_SYSTEM_KEYS = frozenset({"name", "version", "site"})
_TSA_KEYS = frozenset({"provider", "legal_grade", "digest"})


class RenewalRecordError(ValueError):
    """A renewal record cannot be built from what it was given (a programming error)."""


def renew_before_days(config: Config) -> int:
    """``tsa.renew_before_days`` of ``config``: the one place in ``arcilogger_core`` that reads
    it (W5-C's ``legalstatus/options.py`` has its own reader).

    The key is added to :class:`~arcilogger_core.config.Config` by W5-T's day-1 configuration
    pull request (issue #98), which has not landed on this branch yet. Until it has, it is read
    with ``getattr`` and the contract's default (§2.5); afterwards it becomes a plain attribute
    (contract W5-A-1, as W5-B-8 for ``crl.crl_options``).
    """
    return int(getattr(config.tsa, "renew_before_days", DEFAULT_RENEW_BEFORE_DAYS))


# -- the record -----------------------------------------------------------------------------------


def utc_text(moment: datetime) -> str:
    """A certificate time as the record writes it: UTC, whole seconds, ``Z``."""
    return to_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hex(value: str, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise RenewalRecordError(f"{what} must be 64 lower-case hex digits: {value!r}")
    return value


@dataclass(frozen=True)
class RecordEntry:
    """One token a renewal record lists.

    ``path`` is vault-relative (POSIX), except for a report: data-dir relative, under
    ``exports/`` (§2.1.1). ``day`` and ``manifest_sha256`` belong to a ``day`` entry only;
    ``record_sha256`` (what the token stamps) to the other kinds; ``protects`` to a ``renewal``.
    """

    kind: Kind
    path: str
    token_sha256: str
    cert_not_after: datetime
    day: date | None = None
    manifest_sha256: str | None = None
    record_sha256: str | None = None
    protects: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        if self.kind not in KINDS:
            raise RenewalRecordError(f"unknown kind: {self.kind!r}")
        path = self.path
        parts = path.split("/")
        if (
            "\\" in path
            or _DRIVE.match(path)
            or any(part in ("", ".", "..") for part in parts)
            or not path.endswith(".zd")
        ):
            raise RenewalRecordError(f"not a relative token path: {path!r}")
        if (self.kind == "report") != path.startswith("exports/"):
            raise RenewalRecordError(f"only a report token lies under exports/: {path!r}")
        entry: dict[str, Any] = {
            "kind": self.kind,
            "path": path,
            "token_sha256": _hex(self.token_sha256, "token_sha256"),
            "cert_not_after": utc_text(self.cert_not_after),
        }
        if self.kind == "day":
            if self.day is None or self.manifest_sha256 is None or self.record_sha256 is not None:
                raise RenewalRecordError(f"a day entry needs day and manifest_sha256: {path!r}")
            entry["day"] = self.day.isoformat()
            entry["manifest_sha256"] = _hex(self.manifest_sha256, "manifest_sha256")
        else:
            if (
                self.record_sha256 is None
                or self.day is not None
                or self.manifest_sha256 is not None
            ):
                raise RenewalRecordError(f"a {self.kind} entry needs record_sha256: {path!r}")
            entry["record_sha256"] = _hex(self.record_sha256, "record_sha256")
        if self.kind == "renewal":
            entry["protects"] = sorted({_hex(value, "protects") for value in self.protects})
        elif self.protects:
            raise RenewalRecordError(f"only a renewal entry protects hashes: {path!r}")
        return entry


def system_block(config: Config) -> dict[str, str]:
    """The record's ``system``: the manifest's, without the host (§2.1.1)."""
    return {"name": PRODUCT_NAME, "version": __version__, "site": config.site_name}


def tsa_block(provider: TimestampProvider, config: Config) -> dict[str, Any]:
    """The record's ``tsa``, with the keys of the manifest's (``seal.Sealer._tsa_block``): the
    provider, its **claim** to be legal grade, the digest and, only when relaxed, the EKU rule."""
    block: dict[str, Any] = {
        "provider": provider.name,
        "legal_grade": provider.legal_grade,
        "digest": config.tsa.digest,
    }
    if not config.tsa.require_critical_eku:
        block["require_critical_eku"] = False
    return block


def _critical_eku(tsa: Mapping[str, Any]) -> bool:
    """The EKU rule (K6) of a record's (or a manifest's) ``tsa`` block: strict unless the block
    says ``require_critical_eku: false``. A renewal token is verified with its own record's rule
    (contract W5-A-17)."""
    return tsa.get("require_critical_eku") is not False


def build_record(
    entries: Iterable[RecordEntry],
    *,
    created_at: datetime,
    system: Mapping[str, str],
    tsa: Mapping[str, Any],
    reason: Reason,
) -> dict[str, Any]:
    """The renewal record (§2.1.1) for ``entries``, ready for ``canonical_json``.

    ``created_at`` is written in site time with milliseconds, like a manifest's; ``tokens`` are
    sorted by ``path``, and a path may appear once. ``reason`` follows the claim of the ``tsa``
    block: ``due`` exactly when it is legal grade, else ``test`` (§2.1.2, D5).
    """
    if reason not in REASONS:
        raise RenewalRecordError(f"unknown reason: {reason!r}")
    if set(system) != _SYSTEM_KEYS:
        raise RenewalRecordError(f"system needs exactly {sorted(_SYSTEM_KEYS)}")
    if not _TSA_KEYS <= set(tsa) <= _TSA_KEYS | {"require_critical_eku"}:
        raise RenewalRecordError(f"tsa needs {sorted(_TSA_KEYS)}")
    if tsa.get("require_critical_eku", False) is not False:
        raise RenewalRecordError("tsa carries require_critical_eku only when relaxed (false)")
    if reason != ("due" if tsa["legal_grade"] is True else "test"):
        raise RenewalRecordError(
            f"reason {reason!r} does not match the claim {tsa['legal_grade']!r}"
        )
    tokens = sorted((entry.as_json() for entry in entries), key=lambda entry: entry["path"])
    paths = [entry["path"] for entry in tokens]
    if not tokens or len(set(paths)) != len(paths):
        raise RenewalRecordError("a record lists at least one token, each path once")
    return {
        "schema": RENEWAL_SCHEMA,
        "created_at": isoformat_site(created_at, "milliseconds"),
        "system": dict(system),
        "tsa": dict(tsa),
        "reason": reason,
        "tokens": tokens,
    }


def protects(older: Mapping[str, Any]) -> list[str]:
    """What the entry for the renewal ``older`` repeats (D2): every token, manifest and record
    hash ``older`` lists, and what its own renewal entries protect, sorted, each once.

    So the hashes of a day (or a report) are repeated all the way down a chain of renewals, and
    a package for one day picks up every link by the builder's raw-text match.
    """
    hashes: set[str] = set()
    tokens = older.get("tokens")
    for entry in tokens if isinstance(tokens, list) else ():
        if not isinstance(entry, Mapping):
            continue
        values = [entry.get(key) for key in ("token_sha256", "manifest_sha256", "record_sha256")]
        listed = entry.get("protects")
        values.extend(listed if isinstance(listed, list) else ())
        hashes.update(
            value for value in values if isinstance(value, str) and _HEX64.fullmatch(value)
        )
    return sorted(hashes)


# -- the status of each token ---------------------------------------------------------------------


@dataclass(frozen=True)
class TokenFact:
    """A renewable token as it is today: where it is, what it stamps and when its TSA
    certificate expires."""

    path: str
    kind: Kind
    token_sha256: str
    cert_not_after: datetime


@dataclass(frozen=True)
class RenewalFact:
    """A renewal record as read and verified elsewhere.

    ``record_path`` is vault-relative (``renewals/<date>-<seq>.json``); ``token`` is the
    renewal's own token; ``gen_time`` its genTime. ``eligible``: the token verifies, its claim is
    legal grade, its root is listed, it is not a stamp of the test TSA and it is not revoked
    (rule 4, D5, C15); a ``reason: "test"`` record is never eligible. ``lists`` holds the
    ``token_sha256`` of each of its ``tokens`` (the ``protects`` lists do not count: they serve
    the builder's match only).
    """

    record_path: str
    token: TokenFact
    gen_time: datetime
    eligible: bool
    lists: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class TokenStatus:
    """The status of one token (the entry W5-C reads, plus ``renewed_at``).

    ``covered_by`` is the record of the renewal that counts (``covered`` and ``late``), else
    None; ``renewed_at`` its genTime.
    """

    path: str
    kind: Kind
    cert_not_after: datetime
    status: Status
    covered_by: str | None = None
    renewed_at: datetime | None = None

    def as_json(self) -> dict[str, Any]:
        """The entry of ``state/renewal/renewal.json`` (the shape W5-C's reader takes, W5-C-1)."""
        return {
            "path": self.path,
            "kind": self.kind,
            "cert_not_after": utc_text(self.cert_not_after),
            "status": self.status,
            "covered_by": self.covered_by,
            "renewed_at": utc_text(self.renewed_at) if self.renewed_at is not None else None,
        }


@dataclass(frozen=True)
class RenewalNote:
    """Something the report could not check, Turkish first with English beside it."""

    code: str
    message_tr: str
    message_en: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "message_tr": self.message_tr, "message_en": self.message_en}


#: code -> (message_tr, message_en) of :class:`RenewalNote`.
NOTES: Final[dict[str, tuple[str, str]]] = {
    "crl_unreadable": (
        "Saklanan iptal listeleri okunamadı; yenileme damgaları iptal denetimi olmadan doğrulandı.",
        "The stored revocation lists cannot be read; the renewal stamps were verified without "
        "a revocation check.",
    ),
    "crl_mismatched": (
        "Saklanan iptal listelerinden bazıları dizindeki kayıtlarıyla eşleşmiyor ve kullanılmadı; "
        "bir kişinin bakması gerekir: {files}",
        "Some stored revocation lists do not match their index entries and were not used; a "
        "person has to look: {files}",
    ),
    "reports_unreadable": (
        "exports/reports klasörü okunamadı; saklanan raporların damgaları listelenmedi.",
        "The folder exports/reports cannot be read; the stamps of the stored reports are not "
        "listed.",
    ),
}


def _note(code: str, **values: str) -> RenewalNote:
    message_tr, message_en = NOTES[code]
    return RenewalNote(code, message_tr.format(**values), message_en.format(**values))


@dataclass(frozen=True)
class RenewalReport:
    """The status of every renewable token, sorted by path, and the count of each status.

    ``unreadable`` names the tokens (and renewal records) that could not be read, which have no
    status; ``mismatched`` the tokens whose imprint is not the file they stamp as it is today (a
    changed manifest, deletion record, report or renewal record): they have no status either and
    are never renewed, so no renewal vouches for a changed file. ``notes`` says what could not
    be checked.
    """

    tokens: tuple[TokenStatus, ...]
    totals: dict[Status, int]
    unreadable: tuple[str, ...] = ()
    notes: tuple[RenewalNote, ...] = ()
    mismatched: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        tokens: Iterable[TokenStatus],
        *,
        unreadable: Iterable[str] = (),
        notes: Iterable[RenewalNote] = (),
        mismatched: Iterable[str] = (),
    ) -> RenewalReport:
        ordered = tuple(sorted(tokens, key=lambda token: token.path))
        totals: dict[Status, int] = dict.fromkeys(STATUSES, 0)
        for token in ordered:
            totals[token.status] += 1
        return cls(
            ordered,
            totals,
            tuple(sorted(set(unreadable))),
            tuple(notes),
            tuple(sorted(set(mismatched))),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "tokens": [token.as_json() for token in self.tokens],
            "totals": dict(self.totals),
            "unreadable": list(self.unreadable),
            "mismatched": list(self.mismatched),
            "notes": [note.as_json() for note in self.notes],
        }


class _Statuses:
    """The rules of §2.1.2, with each token's verdict kept (a chain is followed once)."""

    def __init__(
        self, renewals: Sequence[RenewalFact], *, now: datetime, renew_before: timedelta
    ) -> None:
        self.now = require_aware(now)
        self.renew_before = renew_before
        self.listing: dict[str, list[RenewalFact]] = {}
        for renewal in renewals:
            for token_sha256 in renewal.lists:
                self.listing.setdefault(token_sha256, []).append(renewal)
        self.known: dict[TokenFact, tuple[Status, RenewalFact | None]] = {}
        self.open: set[TokenFact] = set()

    def status(self, token: TokenFact) -> tuple[Status, RenewalFact | None]:
        known = self.known.get(token)
        if known is not None:
            return known
        self.open.add(token)
        try:
            verdict = self._decide(token)
        finally:
            self.open.discard(token)
        self.known[token] = verdict
        return verdict

    def _decide(self, token: TokenFact) -> tuple[Status, RenewalFact | None]:
        expires = require_aware(token.cert_not_after)
        if expires - self.now > self.renew_before:
            return "not_due", None
        counting = sorted(
            (
                renewal
                for renewal in self.listing.get(token.token_sha256, ())
                if self._counts(renewal)
            ),
            key=lambda renewal: (to_utc(renewal.gen_time), renewal.record_path),
        )
        in_time = [renewal for renewal in counting if renewal.gen_time < expires]
        if in_time:
            return "covered", in_time[0]
        if counting:
            return "late", counting[0]
        if expires <= self.now:
            return "uncovered", None
        return "due", None

    def _counts(self, renewal: RenewalFact) -> bool:
        # A renewal on the path being decided (a cycle, which real hashes cannot form) does not
        # count; nor does one whose own token is due, late or uncovered.
        if not renewal.eligible or renewal.token in self.open:
            return False
        return self.status(renewal.token)[0] in ("not_due", "covered")


def compute_statuses(
    tokens: Sequence[TokenFact],
    renewals: Sequence[RenewalFact],
    *,
    now: datetime,
    renew_before: timedelta,
) -> RenewalReport:
    """The status of each of ``tokens`` at ``now`` (§2.1.2), from the facts of ``renewals``.

    A token is ``not_due`` while ``cert_not_after - now > renew_before`` (exactly
    ``renew_before`` left is due). Otherwise the renewals that count for it are the eligible
    ones that list its ``token_sha256`` and whose own token is ``not_due`` or ``covered``; of
    those the earliest genTime decides (then the smallest record path): ``covered`` when it is
    before ``cert_not_after``, else ``late``. With none, ``uncovered`` once the certificate has
    expired (``cert_not_after <= now``), else ``due``.
    """
    rules = _Statuses(renewals, now=now, renew_before=renew_before)
    statuses = []
    for token in tokens:
        status, renewal = rules.status(token)
        statuses.append(
            TokenStatus(
                path=token.path,
                kind=token.kind,
                cert_not_after=token.cert_not_after,
                status=status,
                covered_by=renewal.record_path if renewal else None,
                renewed_at=renewal.gen_time if renewal else None,
            )
        )
    return RenewalReport.of(statuses)


# -- the tokens of a data directory ---------------------------------------------------------------


@dataclass(frozen=True)
class Renewable:
    """A renewable token as read from the data directory: its facts for the status rules and the
    entry a renewal record lists for it."""

    fact: TokenFact
    entry: RecordEntry


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _parse_record(raw: bytes) -> dict[str, Any] | None:
    """A renewal record (:data:`RENEWAL_SCHEMA`) with what the status needs, or None.

    It is read as the standalone verifier's ``load_json`` reads it (rule 5): UTF-8 only, and a
    key that appears twice in one object makes it unreadable (``json.loads`` keeps the last
    one, so a record could say ``require_critical_eku`` both ways). None never counts, and
    :func:`recover` judges its token strict, as the verifiers do."""
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_refuse_duplicates)
    except (ValueError, RecursionError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("schema") != RENEWAL_SCHEMA
        or record.get("reason") not in REASONS
        or not isinstance(record.get("tsa"), dict)
        or not isinstance(record["tsa"].get("legal_grade"), bool)
        or not isinstance(record.get("tokens"), list)
    ):
        return None
    return record


class _Reader:
    """One scan of a data directory: the renewable tokens and the renewal records (D3).

    ``legal_roots`` (``tsa.legal_root_fingerprints``) judges the renewal tokens, each verified
    with its own record's EKU rule (:func:`_critical_eku`); ``crls``, when given, are the stored
    revocation lists already read.
    """

    def __init__(
        self,
        layout: Layout,
        roots: Sequence[x509.Certificate],
        now: datetime,
        *,
        legal_roots: Collection[str],
        renew_before: timedelta,
        crls: Sequence[Crl] | None = None,
    ) -> None:
        self.layout = layout
        self.roots = roots
        self.now = require_aware(now)
        self.legal_roots = legal_roots
        self.renew_before = renew_before
        self.renewables: list[Renewable] = []
        self.renewals: list[RenewalFact] = []
        self.unreadable: list[str] = []
        self.mismatched: list[str] = []
        self.notes: list[RenewalNote] = []
        self._crls = crls
        self._crls_read = crls is not None

    @classmethod
    def of(
        cls, layout: Layout, config: Config, roots: Sequence[x509.Certificate], now: datetime
    ) -> _Reader:
        """A reader with the site's rules (``config``)."""
        return cls(
            layout,
            roots,
            now,
            legal_roots=config.tsa.legal_root_fingerprints,
            renew_before=timedelta(days=renew_before_days(config)),
        )

    def read(self) -> _Reader:
        layout = self.layout
        for day in sealed_days(layout):
            self.read_day(day)
        for record in deletion_records(layout):
            self._token(token_path_for(record), record, "deletion")
        for token in self._report_tokens():
            self._token(token, token.with_name(token.name.removesuffix(".zd")), "report")
        return self.read_renewals()

    def read_day(self, day: date) -> None:
        self._token(self.layout.token(day), self.layout.manifest(day), "day", day=day)

    def read_renewals(self) -> _Reader:
        if self.layout.renewals_dir.is_dir():
            for record in sorted(self.layout.renewals_dir.glob("*.json")):
                self._renewal(record)
        return self

    def report(self) -> RenewalReport:
        computed = compute_statuses(
            [renewable.fact for renewable in self.renewables],
            self.renewals,
            now=self.now,
            renew_before=self.renew_before,
        )
        return RenewalReport.of(
            computed.tokens,
            unreadable=self.unreadable,
            notes=self.notes,
            mismatched=self.mismatched,
        )

    def crls(self) -> Sequence[Crl] | None:
        """The stored revocation lists, read once; None (with a note) when they cannot be read:
        the renewal tokens are then verified without them (contract question Q12). A list left
        out because it does not match its index entry gets a note too (C14)."""
        if not self._crls_read:
            self._crls_read = True
            try:
                stored = load_stored(self.layout)
            except CrlError as exc:
                log.warning("renewal: revocation lists not checked: %s", exc)
                self.notes.append(_note("crl_unreadable"))
            else:
                self._crls = stored
                if stored.mismatched:  # C14: reported, not dropped silently
                    self.notes.append(_note("crl_mismatched", files=", ".join(stored.mismatched)))
        return self._crls

    # -- reading ----------------------------------------------------------------------------------

    def _relative(self, path: Path) -> str:
        """Vault-relative; a report's token data-dir relative, under ``exports/`` (§2.1.1)."""
        base = self.layout.vault if path.is_relative_to(self.layout.vault) else self.layout.root
        return path.relative_to(base).as_posix()

    def _unreadable(self, path: str, why: object) -> None:
        log.warning("renewal: %s cannot be read (%s); it has no status", path, why)
        self.unreadable.append(path)

    def _read_token(self, token: Path, path: str) -> tuple[bytes, TokenInfo, datetime] | None:
        """The token's bytes, what it says and its certificate's ``notAfter``; None when it is
        not there (the caller decides) or cannot be read (named in ``unreadable``)."""
        try:
            data = token.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            self._unreadable(path, exc)
            return None
        try:
            info = inspect_token(data)
        except (TsaError, ValueError, TypeError, KeyError) as exc:
            self._unreadable(path, exc)
            return None
        if info.tsa_cert_not_after is None:
            self._unreadable(path, "the token carries no TSA certificate")
            return None
        return data, info, info.tsa_cert_not_after

    def _matches(self, path: str, info: TokenInfo, imprint: str | None) -> bool:
        """Whether the token's imprint is the stamped file as it is today; a mismatch is named in
        ``mismatched`` (and logged): the file changed, or the token is not its own."""
        if imprint is None:
            self._unreadable(path, f"its imprint algorithm {info.digest_alg} is not supported")
            return False
        if imprint != info.message_imprint_hex:
            log.warning(
                "renewal: %s does not match the file it stamps (changed?); it is not renewed",
                path,
            )
            self.mismatched.append(path)
            return False
        return True

    def _token(self, token: Path, stamped: Path, kind: Kind, *, day: date | None = None) -> None:
        path = self._relative(token)
        read = self._read_token(token, path)
        if read is None:
            return  # not stamped (yet), or deleted during the scan by the retention job
        data, info, cert_not_after = read
        try:
            stamped_sha256, imprint = _file_hashes(stamped, info.digest_alg)
        except FileNotFoundError:
            if token.exists():
                self._unreadable(path, f"{stamped.name} is missing")
            return
        except OSError as exc:
            self._unreadable(path, exc)
            return
        if not self._matches(path, info, imprint):
            return
        token_sha256 = hashlib.sha256(data).hexdigest()
        entry = RecordEntry(
            kind,
            path,
            token_sha256,
            cert_not_after,
            day=day,
            manifest_sha256=stamped_sha256 if kind == "day" else None,
            record_sha256=None if kind == "day" else stamped_sha256,
        )
        self.renewables.append(
            Renewable(TokenFact(path, kind, token_sha256, cert_not_after), entry)
        )

    def _report_tokens(self) -> list[Path]:
        """The stored reports' tokens. A missing folder has none; one that cannot be read (the
        ``Renew`` task's account may lack the right) has none too, with a note: never a crash."""
        folder = self.layout.exports / REPORTS_FOLDER
        try:
            with os.scandir(folder) as entries:
                names = sorted(
                    entry.name for entry in entries if entry.name.endswith(REPORT_TOKEN_SUFFIX)
                )
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning(
                "renewal: %s cannot be read (%s); report tokens are not listed", folder, exc
            )
            self.notes.append(_note("reports_unreadable"))
            return []
        return [folder / name for name in names]

    def _renewal(self, record: Path) -> None:
        record_path = f"renewals/{record.name}"
        token = token_path_for(record)
        try:
            raw = record.read_bytes()
        except OSError as exc:
            self._unreadable(record_path, exc)
            return
        parsed = _parse_record(raw)
        if parsed is None:
            self._unreadable(record_path, f"not an {RENEWAL_SCHEMA} record")
            return
        token_path = f"{record_path}.zd"
        read = self._read_token(token, token_path)
        if read is None:
            if not token.exists():
                self._unreadable(record_path, "the record has no token")
            return
        data, info, cert_not_after = read
        if not self._matches(token_path, info, _imprint(raw, info.digest_alg)):
            return  # a changed record (or a token not its own) protects nothing
        result = verify_token(
            data,
            roots=self.roots,
            file_path=record,
            now=max(self.now, utc_now()),
            # The rule the site wrote into this record (K6), not today's setting.
            require_critical_eku=_critical_eku(parsed["tsa"]),
            crls=self.crls(),
        )
        # D5: a test record or a test stamp never counts; a legal claim counts only when rule 4
        # confirms it.
        eligible = (
            parsed["reason"] == "due"
            and confirmed_legal_grade(parsed["tsa"]["legal_grade"], result, self.legal_roots)
            and not is_test_stamp(info.policy, result.details.get("root") or "")
        )
        token_sha256 = hashlib.sha256(data).hexdigest()
        fact = TokenFact(token_path, "renewal", token_sha256, cert_not_after)
        lists = frozenset(
            entry["token_sha256"]
            for entry in parsed["tokens"]
            if isinstance(entry, dict) and isinstance(entry.get("token_sha256"), str)
        )
        self.renewals.append(RenewalFact(record_path, fact, info.gen_time, eligible, lists))
        if parsed["reason"] == "test":
            return  # its own token is not renewed (contract W5-A-7)
        entry = RecordEntry(
            "renewal",
            token_path,
            token_sha256,
            cert_not_after,
            record_sha256=hashlib.sha256(raw).hexdigest(),
            protects=tuple(protects(parsed)),
        )
        self.renewables.append(Renewable(fact, entry))


def _imprint(data: bytes, alg: str) -> str | None:
    """The hex digest of ``data`` with a token's imprint algorithm; None when not supported."""
    name = READABLE_DIGESTS.get(alg)
    return hashlib.new(name, data).hexdigest() if name else None


def _file_hashes(path: Path, alg: str) -> tuple[str, str | None]:
    """The SHA-256 of ``path`` (what a record lists) and its digest with a token's imprint
    algorithm (None when not supported), read once."""
    sha256 = hashlib.sha256()
    name = READABLE_DIGESTS.get(alg)
    other = hashlib.new(name) if name and name != "sha256" else None
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            if other is not None:
                other.update(chunk)
    digest = sha256.hexdigest()
    if name is None:
        return digest, None
    return digest, other.hexdigest() if other is not None else digest


def renewal_status(
    layout: Layout,
    config: Config,
    roots: Sequence[x509.Certificate],
    now: datetime | None = None,
) -> RenewalReport:
    """The long-term status of every renewable token of ``layout`` at ``now`` (§2.1.2, D3).

    ``roots`` are the pinned roots the renewal tokens are verified against (with the stored
    revocation lists and each record's own EKU rule); ``tsa.legal_root_fingerprints`` and
    ``tsa.renew_before_days`` come from ``config``. Writes nothing.
    """
    return _Reader.of(layout, config, roots, now or utc_now()).read().report()


# -- the status of one day, for the core verifier --------------------------------------------------

#: The finding of a day's status: (level, code, TR, EN), the texts of contract §2.6 and the
#: levels of §2.1.4, as the standalone verifier's ``RENEWAL_FINDINGS``. ``{renewal}`` is the
#: record that counts; ``{date}`` the certificate's expiry, for ``late`` the re-stamping's time.
DAY_FINDINGS: Final[dict[Status, tuple[str, str, str, str]]] = {
    "not_due": (
        "info",
        "renewal-not-due",
        "Yeniden damgalama henüz gerekmiyor (sertifika {date} tarihine kadar geçerli).",
        "Re-stamping is not due yet (certificate valid until {date}).",
    ),
    "covered": (
        "ok",
        "renewal-covered",
        "Bu damga, sertifikası sona ermeden yeniden damgalanmış: {renewal}.",
        "Re-stamped before its certificate expired: {renewal}.",
    ),
    "due": (
        "warn",
        "renewal-due",
        "Damga sertifikası {date} tarihinde sona eriyor; yeniden damgalama bekleniyor.",
        "The stamp certificate expires on {date}; re-stamping is due.",
    ),
    "late": (
        "warn",
        "renewal-late",
        "Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış ({date}).",
        "Re-stamped after the old certificate had expired ({date}).",
    ),
    "uncovered": (
        "warn",
        "renewal-uncovered",
        "Damga sertifikasının süresi {date} tarihinde doldu ve yeniden damgalama yok; damga, "
        "alındığı an için geçerlidir.",
        "The stamp certificate expired on {date} with no re-stamping; the stamp is valid for the "
        "moment it was made.",
    ),
}


def finding_time(moment: datetime) -> str:
    """A time in a Turkish finding text, as the standalone verifier writes it:
    ``2031-10-01 00:00:00 UTC (01.10.2031 03:00:00 TSİ)``."""
    return (
        f"{to_utc(moment).strftime('%Y-%m-%d %H:%M:%S UTC')} "
        f"({to_site(moment).strftime('%d.%m.%Y %H:%M:%S')} TSİ)"
    )


@dataclass(frozen=True)
class DayRenewal:
    """The long-term status of a day's stamp as the core verifier reports it (§2.1.4): the
    status of :func:`compute_statuses` with the standalone verifier's code, level and texts."""

    status: Status
    code: str
    level: str
    cert_not_after: datetime
    covered_by: str | None
    renewed_at: datetime | None
    message_tr: str
    message_en: str

    @classmethod
    def of(cls, token: TokenStatus) -> DayRenewal:
        level, code, tr, en = DAY_FINDINGS[token.status]
        # late: when the stamp was renewed; otherwise when its certificate expires
        moment = token.cert_not_after
        if token.status == "late" and token.renewed_at is not None:
            moment = token.renewed_at
        moment = to_utc(moment)
        renewal = token.covered_by or ""
        return cls(
            status=token.status,
            code=code,
            level=level,
            cert_not_after=token.cert_not_after,
            covered_by=token.covered_by,
            renewed_at=token.renewed_at,
            message_tr=tr.format(renewal=renewal, date=finding_time(moment)),
            message_en=en.format(renewal=renewal, date=moment.isoformat()),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "level": self.level,
            "cert_not_after": utc_text(self.cert_not_after),
            "covered_by": self.covered_by,
            "renewed_at": utc_text(self.renewed_at) if self.renewed_at is not None else None,
            "message_tr": self.message_tr,
            "message_en": self.message_en,
        }


def day_renewal(
    layout: Layout,
    day: date,
    roots: Sequence[x509.Certificate],
    *,
    legal_roots: Collection[str] = (),
    now: datetime | None = None,
    crls: Sequence[Crl] | None = None,
    renew_before_days: int = DEFAULT_RENEW_BEFORE_DAYS,
) -> DayRenewal | None:
    """The long-term status of ``day``'s stamp at ``now`` (default: the system clock), from the
    renewal records of ``layout``; None when the day's token cannot be read or does not match
    its manifest (it has no status). Writes nothing.

    ``legal_roots`` (``tsa.legal_root_fingerprints``) and ``crls`` (the stored lists, read from
    ``state/crl`` when None) judge the renewals as :func:`renewal_status` does, each renewal
    token with its own record's EKU rule (:func:`_critical_eku`), as the day's token is judged
    by its manifest's: neither the day's rule nor the site's setting. The window is
    the §2.5 default, as the standalone verifier's (contract W5-A-5), unless the caller passes
    the site's ``tsa.renew_before_days``.
    """
    renew_before = timedelta(days=renew_before_days)
    reader = _Reader(
        layout,
        roots,
        now or utc_now(),
        legal_roots=legal_roots,
        renew_before=renew_before,
        crls=crls,
    )
    reader.read_day(day)
    facts = [renewable.fact for renewable in reader.renewables]
    if not facts:
        return None
    reader.read_renewals()
    (token,) = compute_statuses(
        facts, reader.renewals, now=reader.now, renew_before=renew_before
    ).tokens
    return DayRenewal.of(token)


# -- a run ----------------------------------------------------------------------------------------


class RenewalError(Exception):
    """A run that did not write a record, Turkish first with English beside it."""

    code = "renewal_error"

    def __init__(self, message_tr: str, message_en: str) -> None:
        super().__init__(f"{message_tr} [{message_en}]")
        self.message_tr = message_tr
        self.message_en = message_en


class RenewalBusyError(RenewalError):
    """Another run holds ``state/renewal/renewal.lock``."""

    code = "renewal_busy"

    def __init__(self) -> None:
        super().__init__(
            "Başka bir yeniden damgalama çalışıyor (state/renewal/renewal.lock kilitli); daha "
            "sonra yeniden deneyin.",
            "Another re-stamping run is in progress (state/renewal/renewal.lock is locked); try "
            "again later.",
        )


class RenewalNotNeeded(RenewalError):  # noqa: N818 - the contract's name (§2.1.2)
    """No token is due or uncovered; ``report`` is the status that says so."""

    code = "renewal_not_needed"

    def __init__(self, report: RenewalReport) -> None:
        super().__init__(
            "Yeniden damgalanması gereken damga yok.", "No time stamp is due for re-stamping."
        )
        self.report = report


class RenewalStampInvalidError(TsaResponseError):
    """The renewal record's new token does not verify against the pinned roots, or its
    certificate was revoked at or before the stamp (the stored lists)."""

    code = "renewal_stamp_invalid"

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        detail = "; ".join(self.errors)
        self.message_tr = (
            f"Yenileme kaydının zaman damgası doğrulanamadı; kayıt yazılmadı: {detail}"
        )
        self.message_en = (
            f"The renewal record's time stamp did not verify; no record was written: {detail}"
        )
        super().__init__(f"{self.message_tr} [{self.message_en}]")


class RenewalLegalRootsError(TsaConfigurationError):
    """The provider claims legal grade, but its renewals would never count (D5), and each
    stamp spends credit (D4): no pinned root is listed in ``tsa.legal_root_fingerprints``, so
    the run refuses before it stamps; or, given the root the stamp's chain ended at (``root``,
    its subject, and ``root_sha256``), that pinned root is not listed, so the record and its
    token were removed after the stamp. The command exits 2 with the cause :attr:`cause`
    (W5-A-13)."""

    cause = "legal-roots"

    def __init__(self, *, root: str = "", root_sha256: str = "") -> None:
        self.root = root
        self.root_sha256 = root_sha256
        if root_sha256:  # after the stamp: its chain ended at this pinned, unlisted root
            self.message_tr = (
                "Sağlayıcı yasal geçerli damga beyan ediyor, ancak damganın zinciri parmak izi "
                "tsa.legal_root_fingerprints listesinde olmayan sabitlenmiş bir kök sertifikada "
                f"bitiyor: {root} (SHA-256 {root_sha256}). Bu yenileme hiçbir zaman sayılmaz; "
                "kayıt ve damgası silindi. Bir damga kredisi harcandı; kök "
                "tsa.legal_root_fingerprints listesine eklenene ya da sağlayıcı düzeltilene "
                "kadar sonraki her çalıştırma bir kredi daha harcar. Kökün parmak izini "
                "yayımlanan değerle karşılaştırın."
            )
            self.message_en = (
                "The provider claims legal grade, but the time stamp's chain ends at a pinned "
                "root certificate whose fingerprint is not in tsa.legal_root_fingerprints: "
                f"{root} (SHA-256 {root_sha256}). Such a renewal never counts, so the record "
                "and its token were removed. One stamp credit was spent; every later run "
                "spends another until the root is listed in tsa.legal_root_fingerprints or the "
                "provider is corrected. Compare the root's fingerprint with the published value."
            )
        else:
            self.message_tr = (
                "Sağlayıcı yasal geçerli damga beyan ediyor, ancak sabitlenmiş kök "
                "sertifikaların hiçbirinin parmak izi tsa.legal_root_fingerprints listesinde "
                "değil: bu yenilemeler hiçbir zaman sayılmaz, damga alınmadı. Kökün parmak "
                "izini yayımlanan değerle karşılaştırıp tsa.legal_root_fingerprints listesine "
                "ekleyin."
            )
            self.message_en = (
                "The provider claims legal grade, but no pinned root certificate's fingerprint "
                "is in tsa.legal_root_fingerprints: such renewals never count, so no stamp was "
                "taken. Compare the root's fingerprint with the published value and add it to "
                "tsa.legal_root_fingerprints."
            )
        super().__init__(f"{self.message_tr} [{self.message_en}]")


def _listed_root_pinned(roots: Sequence[x509.Certificate], legal_roots: Collection[str]) -> bool:
    """Whether a pinned root is listed in ``legal_roots`` (``tsa.legal_root_fingerprints``):
    a token verifies only by a chain that ends at a pinned root, so without one listed no stamp
    ever counts as legal grade (rule 4)."""
    listed = set(legal_roots)
    return any(fingerprint(root) in listed for root in roots)


class RenewalLockError(RenewalError):
    """``state/renewal/renewal.lock`` cannot be opened or locked for another reason than a run
    holding it (no write right on ``state/renewal``, no lock support)."""

    code = "renewal_lock_failed"

    def __init__(self, exc: OSError) -> None:
        super().__init__(
            f"state/renewal/renewal.lock açılamadı veya kilitlenemedi: {exc}",
            f"state/renewal/renewal.lock cannot be opened or locked: {exc}",
        )


#: What a lock held by another process raises (``flock``: EWOULDBLOCK/EAGAIN; ``msvcrt``:
#: EACCES).
_BUSY = frozenset({errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES})


@dataclass(frozen=True)
class RenewalRun:
    """What one run wrote: the record and its token (vault-relative), the paths of the tokens
    it lists, the status before and after, and the leftovers of an interrupted run it removed."""

    record_path: str
    token_path: str
    reason: Reason
    renewed: tuple[str, ...]
    report_before: RenewalReport
    report_after: RenewalReport
    discarded: tuple[str, ...] = ()


@contextmanager
def one_run(layout: Layout) -> Iterator[None]:
    """Hold ``state/renewal/renewal.lock`` (:data:`LOCK_NAME`); raise :class:`RenewalBusyError`
    while another run holds it, :class:`RenewalLockError` when it cannot be opened or locked.

    The ``state/renewal.lock`` of an earlier release is no longer used: it is left as it is (an
    empty file that holds nothing), and a process holding it blocks no run."""
    try:
        state_dir(layout).mkdir(parents=True, exist_ok=True)
        handle = os.open(state_dir(layout) / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise RenewalLockError(exc) from exc
    try:
        try:
            _lock(handle)
        except OSError as exc:
            if exc.errno in _BUSY:
                raise RenewalBusyError from exc
            raise RenewalLockError(exc) from exc
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


def _part(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


def _remove(path: Path, removed: list[str]) -> None:
    """Remove a leftover of a run; a locked file is never unlocked or removed here (only the
    retention job deletes evidence): it is left for a person, with a warning in the log."""
    if not path.exists():
        return
    if is_locked(path):
        log.warning("renewal: %s is locked; it is left as it is for a person to look at", path.name)
        return
    path.unlink()
    removed.append(path.name)


def _replace(source: Path, target: Path) -> None:
    source.replace(target)
    fsync_dir(target.parent)


def _promote(part: Path, token: Path, final: Path) -> None:
    """Token first, then the record, then both locked: a record under its final name always
    has its token."""
    final_token = token_path_for(final)
    if token != final_token:
        _replace(token, final_token)
    _replace(part, final)
    for path in (final, final_token):
        if not is_locked(path):
            lock(path)


def _lock_unlocked(paths: Iterable[Path]) -> None:
    """Lock each of ``paths`` that is not locked yet; a failure is logged and left for the next
    run, which tries again (the record is a valid renewal either way)."""
    for path in paths:
        if is_locked(path):
            continue
        try:
            lock(path)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("renewal: %s cannot be locked (%s); the next run tries again", path, exc)
        else:
            log.warning("renewal: locked %s, left unlocked by an interrupted run", path.name)


def _stored_lists(layout: Layout) -> Sequence[Crl] | None:
    """The stored revocation lists, or None (logged) when they cannot be read: the token is
    then verified without them (contract question Q12)."""
    try:
        return load_stored(layout)
    except CrlError as exc:
        log.warning("renewal: revocation lists not checked: %s", exc)
        return None


def _refusal(check: VerifyResult) -> tuple[str, ...]:
    """Why a new renewal token is not kept: its errors, or its certificate revoked at or before
    the stamp in the stored lists (such a renewal would never count, C15); empty when kept."""
    if not check.ok:
        return tuple(check.errors)
    if check.details.get("crl") == "crl-revoked":
        return (
            "the TSA certificate was revoked at or before the stamp "
            f"({check.details.get('crl_subject', '')}, {check.details.get('crl_at', '')})",
        )
    return ()


def _unlisted(check: VerifyResult, legal_roots: Collection[str]) -> bool:
    """Whether a verified token's chain ends at a pinned root not listed in ``legal_roots``
    (``tsa.legal_root_fingerprints``): a legal-grade renewal under it never counts (D5)."""
    return (check.details.get("root_sha256") or "") not in set(legal_roots)


def recover(
    layout: Layout,
    roots: Sequence[x509.Certificate],
    *,
    legal_roots: Collection[str],
    crls: Sequence[Crl] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Tidy up what an interrupted run left in ``vault/renewals``; returns the removed names.

    * ``X.json.part`` with a token (``X.json.part.zd``, or ``X.json.zd`` when the run stopped
      between the two renames) that verifies against it, with the part's own EKU rule
      (:func:`_critical_eku`), and whose certificate ``crls`` (the stored lists, read from
      ``state/crl`` when None) do not show revoked at or before the stamp: promoted (W5-A-3).
      A revoked one is removed, as the run would have removed it (C15: a revoked new stamp
      leaves no record), and so is one whose record claims legal grade while its chain ends at
      a pinned root not in ``legal_roots`` (``tsa.legal_root_fingerprints``, W5-A-13: required,
      so that no caller skips the check by leaving it out);
    * any other ``….part`` and ``….part.zd``, a final token without its record and a temporary
      file of an interrupted write (``.….tmp``): removed. None of them is evidence: a record
      without a verified token protects nothing. A locked file is never removed: a locked token
      without its record means a completed record was removed, and the token is left for a
      person (with a warning in the log);
    * a record and its token under their final names that are not both locked (a run stopped,
      or ``lock`` failed, after the renames): locked, as the seal locks every file of a day.
    """
    folder = layout.renewals_dir
    removed: list[str] = []
    if not folder.is_dir():
        return removed
    moment = max(now or utc_now(), utc_now())
    lists_read = crls is not None
    for part in sorted(folder.glob(f"*.json{PART_SUFFIX}")):
        final = part.with_name(part.name.removesuffix(PART_SUFFIX))
        tokens = [
            token for token in (token_path_for(part), token_path_for(final)) if token.is_file()
        ]
        if not final.exists() and len(tokens) == 1:
            if not lists_read:  # read only when a part is to be checked
                lists_read, crls = True, _stored_lists(layout)
            parsed = _parse_record(part.read_bytes())
            check = verify_token(
                tokens[0].read_bytes(),
                roots=roots,
                file_path=part,
                now=moment,
                require_critical_eku=_critical_eku(parsed["tsa"]) if parsed else True,
                crls=crls,
            )
            refusal = _refusal(check)
            if (
                not refusal
                and parsed is not None
                and parsed["tsa"]["legal_grade"] is True
                and _unlisted(check, legal_roots)
            ):
                refusal = (
                    "it claims legal grade, but its chain ends at a root not in "
                    f"tsa.legal_root_fingerprints ({check.details.get('root') or ''}, SHA-256 "
                    f"{check.details.get('root_sha256') or ''})",
                )
            if not refusal:
                _promote(part, tokens[0], final)
                log.warning("renewal: completed %s of an interrupted run", final.name)
                continue
            log.warning(
                "renewal: %s of an interrupted run is not kept: %s", final.name, "; ".join(refusal)
            )
            _remove(tokens[0], removed)
        _remove(part, removed)
    for token in sorted(folder.glob(f"*.json{PART_SUFFIX}.zd")):
        _remove(token, removed)
    for token in sorted(folder.glob("*.json.zd")):
        if not token.with_name(token.name.removesuffix(".zd")).exists():
            _remove(token, removed)
    for temporary in sorted(folder.glob(".*.tmp")):
        _remove(temporary, removed)
    for record in sorted(folder.glob("*.json")):
        token = token_path_for(record)
        if token.is_file():
            _lock_unlocked((record, token))
    if removed:
        log.warning("renewal: removed leftovers of an interrupted run: %s", ", ".join(removed))
    return removed


def _record_path(layout: Layout, today: date) -> Path:
    """``renewals/<today>-<seq>.json``: the first number from 1 that no file of a record (or
    of one in progress) uses yet."""
    number = 1
    while True:
        path = layout.renewals_dir / f"{today.isoformat()}-{number}.json"
        names = (path, token_path_for(path), _part(path), token_path_for(_part(path)))
        if not any(name.exists() for name in names):
            return path
        number += 1


def _write(
    final: Path,
    record: dict[str, Any],
    provider: TimestampProvider,
    roots: Sequence[x509.Certificate],
    *,
    legal_roots: Collection[str],
    crls: Sequence[Crl] | None,
    now: datetime,
) -> None:
    """Write, stamp and verify ``record`` as ``<final>.part``, then promote it; on any failure
    remove both files and raise (§2.1.1: a record without its token is not evidence). The token
    is verified with the record's own EKU rule (:func:`_critical_eku`). A token that verifies
    but whose certificate the stored lists show revoked at or before its genTime is refused
    too: such a renewal would never count (C15). So is a token of a provider that claims legal
    grade whose chain ends at a pinned root not listed in ``legal_roots``
    (``tsa.legal_root_fingerprints``): :class:`RenewalLegalRootsError` names that root
    (W5-A-13)."""
    part = _part(final)
    atomic_write_bytes(part, canonical_json(record))
    removed: list[str] = []
    try:
        stamp = provider.stamp(part)
        check = verify_token(
            stamp.token_path.read_bytes(),
            roots=roots,
            file_path=part,
            now=max(now, utc_now()),
            require_critical_eku=_critical_eku(record["tsa"]),
            crls=crls,
        )
        refusal = _refusal(check)
        if refusal:
            raise RenewalStampInvalidError(refusal)
        if provider.legal_grade is True and _unlisted(check, legal_roots):
            raise RenewalLegalRootsError(
                root=check.details.get("root") or "",
                root_sha256=check.details.get("root_sha256") or "",
            )
    except BaseException:  # a provider's own error or an interrupt too: never a part left
        _remove(token_path_for(part), removed)
        _remove(part, removed)
        raise
    _promote(part, stamp.token_path, final)


def renew(
    layout: Layout,
    config: Config,
    provider: TimestampProvider,
    roots: Sequence[x509.Certificate],
    now: datetime | None = None,
) -> RenewalRun:
    """Re-stamp every token that is ``due`` or ``uncovered`` at ``now`` with one record (D4).

    An expired token is renewed too; it then becomes ``late``. The record's ``reason`` follows
    the provider's claim: ``due`` for a legal-grade provider, else ``test``, and a test record
    never counts (D5). Raises :class:`RenewalBusyError` while another run holds the lock,
    :class:`RenewalLockError` when the lock file cannot be used, :class:`RenewalNotNeeded` when
    nothing is due, :class:`RenewalLegalRootsError` before any stamp when the provider claims
    legal grade but no pinned root is listed in ``tsa.legal_root_fingerprints`` (its renewals
    would never count), or after it when the stamp's chain ends at a pinned root that is not
    listed, leaving no record, and the provider's :class:`TsaError` (or
    :class:`RenewalStampInvalidError`) when the stamp fails, leaving no record.
    """
    with one_run(layout):
        return renew_held(layout, config, provider, roots, now)


def renew_held(
    layout: Layout,
    config: Config,
    provider: TimestampProvider,
    roots: Sequence[x509.Certificate],
    now: datetime | None = None,
) -> RenewalRun:
    """:func:`renew` for a caller that already holds :func:`one_run` (the ``renew`` command,
    which writes ``state/renewal/renewal.json`` under the same lock).

    Once the record is written the run is a renewal: when the status after it cannot be read
    (an :class:`OSError`), ``report_after`` is the status before the run, with a warning in the
    log."""
    moment = require_aware(now or utc_now())
    discarded = recover(layout, roots, now=moment, legal_roots=config.tsa.legal_root_fingerprints)
    reader = _Reader.of(layout, config, roots, moment).read()
    before = reader.report()
    due = {token.path for token in before.tokens if token.status in ("due", "uncovered")}
    if not due:
        raise RenewalNotNeeded(before)
    if provider.legal_grade is True and not _listed_root_pinned(
        roots, config.tsa.legal_root_fingerprints
    ):
        raise RenewalLegalRootsError
    entries = [renewable.entry for renewable in reader.renewables if renewable.fact.path in due]
    reason: Reason = "due" if provider.legal_grade is True else "test"
    record = build_record(
        entries,
        created_at=moment,
        system=system_block(config),
        tsa=tsa_block(provider, config),
        reason=reason,
    )
    final = _record_path(layout, site_day(moment))
    final.parent.mkdir(parents=True, exist_ok=True)
    _write(
        final,
        record,
        provider,
        roots,
        legal_roots=config.tsa.legal_root_fingerprints,
        crls=reader.crls(),
        now=moment,
    )
    record_path = f"renewals/{final.name}"
    log.info(
        "renewal: %s stamped by %s (reason %s), %d token(s)",
        record_path,
        provider.name,
        reason,
        len(entries),
    )
    try:
        after = renewal_status(layout, config, roots, moment)
    except OSError as exc:  # the record is written: the run stays a renewal
        log.warning(
            "renewal: the status after %s cannot be read (%s); the status before the run is "
            "reported, the next run reads it again",
            record_path,
            exc,
        )
        after = before
    return RenewalRun(
        record_path=record_path,
        token_path=f"{record_path}.zd",
        reason=reason,
        renewed=tuple(entry["path"] for entry in record["tokens"]),
        report_before=before,
        report_after=after,
        discarded=tuple(discarded),
    )


# -- state/renewal/renewal.json -------------------------------------------------------------------

STATE_SCHEMA: Final = "arcilogger.renewal-status/1"
#: ``state/renewal/renewal.json``, the path of W5-C's reader (``alerts.system.stamp_renewal_path``,
#: which reads :func:`state_to_read`). An earlier release wrote ``state/renewal.json``.
STATE_NAME: Final = "renewal.json"
#: How a run of ``arcilogger-core renew`` ended (``last_run.outcome``): a record written,
#: nothing due, the time-stamp server or the run failed (exit 3), the configuration refused
#: (exit 2).
Outcome = Literal["renewed", "not_needed", "failed", "refused"]
#: The outcomes that count towards ``failures_in_a_row`` (W5-E: "Renew failed twice in a row").
FAILED_OUTCOMES: Final[frozenset[str]] = frozenset({"failed", "refused"})


@dataclass(frozen=True)
class LastRun:
    """A run of the ``Renew`` task as ``state/renewal/renewal.json`` keeps it: when it started,
    how it ended, its exit code, the record it wrote (vault-relative) and a code for its error."""

    at: datetime
    outcome: Outcome
    exit_code: int
    record: str | None = None
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "at": utc_text(self.at),
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "record": self.record,
            "error": self.error,
        }


def state_dir(layout: Layout) -> Path:
    return layout.state / STATE_FOLDER


def state_path(layout: Layout) -> Path:
    """``state/renewal/renewal.json``, the only path :func:`write_state` writes."""
    return state_dir(layout) / STATE_NAME


def earlier_state_path(layout: Layout) -> Path:
    """``state/renewal.json``, where an earlier release kept the report. Every service could
    write it there (``state``'s rights), so it is only read, and only while the new file is
    missing (:func:`state_to_read`); nothing writes or removes it."""
    return layout.state / STATE_NAME


#: What the first counted run after an upgrade takes from :func:`earlier_state_path`, which
#: every service could write: the count of failed runs only, never the token list, its time or
#: the last run (contract W5-E-8).
EARLIER_KEPT: Final = ("failures_in_a_row",)


def state_to_read(layout: Layout) -> Path:
    """The file the report is read from: :func:`state_path`, or :func:`earlier_state_path` while
    the new file is missing. The first counted run after an upgrade (a dry run writes no state file
    while the earlier file is the only one) reads the earlier file once and carries only
    :data:`EARLIER_KEPT` over; from then on the new file exists and the earlier one is never
    read again. Until then the readers (the alerts, the Sistem page, the legal status) read it
    too, so an upgrade does not resolve an open alert before the first run."""
    path = state_path(layout)
    try:
        path.stat()
    except FileNotFoundError:
        return earlier_state_path(layout)
    except OSError:
        pass  # the read fails alike and says why
    return path


def read_state(layout: Layout) -> dict[str, Any] | None:
    """The last report (:func:`state_to_read`); None when there is none or it cannot be read."""
    return _read_report(layout, state_to_read(layout))


def _read_report(layout: Layout, path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        log.warning("renewal: %s cannot be read (%s); it is written anew", path, exc)
        return None
    if path != state_path(layout):
        log.info("renewal: %s of an earlier release read; %s replaces it", path, state_path(layout))
    return data if isinstance(data, dict) else None


def write_state(
    layout: Layout,
    report: RenewalReport | None,
    *,
    at: datetime,
    renew_before: int,
    last_run: LastRun | None,
) -> dict[str, Any] | None:
    """Write ``state/renewal/renewal.json`` atomically and return what was written (W5-A-2);
    None when nothing was written.

    Only the ``Renew`` task's command line calls this, holding :func:`one_run` around the read
    of the previous file and the write; the API and the alerts only read the file (§2.1.2).
    ``renew_before`` is the window the statuses were computed with (days). ``report`` is None
    when the statuses could not be computed (no pinned root): the last known ``tokens`` are
    kept with their ``at``, and without them the file has no token list, which W5-C's reader
    takes as unknown (never as "nothing due"). ``last_run`` is None for a dry run, which keeps
    the last run and ``failures_in_a_row``; a failed or refused run adds one to
    ``failures_in_a_row``, any other run sets it to 0. From an earlier release's file only
    :data:`EARLIER_KEPT` is taken: a refused run then writes no token list. While that file is
    the only one, a dry run writes no state file, so the readers keep reading it until the first
    counted run (contract W5-E-8): a dry run never makes the state look worse.
    """
    path = state_to_read(layout)
    earlier = path != state_path(layout)
    if earlier and last_run is None and path.exists():
        return None  # a dry run: the readers keep the earlier file until a counted run
    previous = _read_report(layout, path) or {}
    if earlier:
        previous = {key: previous[key] for key in EARLIER_KEPT if key in previous}
    state: dict[str, Any] = {"schema": STATE_SCHEMA}
    if report is not None:
        state.update(at=utc_text(at), renew_before_days=renew_before, **report.as_json())
    else:
        kept = ("at", "renew_before_days", "tokens", "totals", "unreadable", "mismatched", "notes")
        state.update({key: previous[key] for key in kept if key in previous})
    failures = previous.get("failures_in_a_row")
    failures = failures if isinstance(failures, int) and failures >= 0 else 0
    if last_run is None:
        if "last_run" in previous:
            state["last_run"] = previous["last_run"]
    else:
        state["last_run"] = last_run.as_json()
        failures = failures + 1 if last_run.outcome in FAILED_OUTCOMES else 0
    state["failures_in_a_row"] = failures
    atomic_write_bytes(
        state_path(layout), (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
    )
    return state
