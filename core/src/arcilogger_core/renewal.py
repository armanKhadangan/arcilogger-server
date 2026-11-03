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
is one the site listed and it is not revoked: rule 4, D5, C15) and its own token is ``not_due``
or ``covered`` itself: the check follows the chain of renewals.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from arcilogger_core import PRODUCT_NAME, __version__
from arcilogger_core.config import Config
from arcilogger_core.timeutil import isoformat_site, require_aware, to_utc
from arcilogger_core.tsa.base import TimestampProvider

RENEWAL_SCHEMA: Final = "arcilogger.renewal/1"
#: What a renewal record lists: the token of a sealed day, of a deletion record, of a stored
#: official report (``exports/reports/*.pdf.zd``, owner answer Q3) or of an earlier renewal.
Kind = Literal["day", "deletion", "report", "renewal"]
KINDS: Final[tuple[Kind, ...]] = ("day", "deletion", "report", "renewal")
#: The long-term status of a token; W5-C's reader of ``state/renewal.json`` takes exactly these.
Status = Literal["not_due", "due", "covered", "late", "uncovered"]
STATUSES: Final[tuple[Status, ...]] = ("not_due", "due", "covered", "late", "uncovered")
#: ``due``: written by a legal-grade provider; ``test``: by any other, and never counts (D5).
Reason = Literal["due", "test"]
REASONS: Final[tuple[Reason, ...]] = ("due", "test")
#: ``tsa.renew_before_days`` when the configuration has no such key (§2.5, owner question Q5).
DEFAULT_RENEW_BEFORE_DAYS: Final = 180

_HEX64 = re.compile(r"[0-9a-f]{64}")
_SYSTEM_KEYS = frozenset({"name", "version", "site"})
_TSA_KEYS = frozenset({"provider", "legal_grade", "digest"})


class RenewalRecordError(ValueError):
    """A renewal record cannot be built from what it was given (a programming error)."""


def renew_before_days(config: Config) -> int:
    """``tsa.renew_before_days`` of ``config``: the one place that reads it.

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
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
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
    sorted by ``path``, and a path may appear once.
    """
    if reason not in REASONS:
        raise RenewalRecordError(f"unknown reason: {reason!r}")
    if set(system) != _SYSTEM_KEYS:
        raise RenewalRecordError(f"system needs exactly {sorted(_SYSTEM_KEYS)}")
    if not _TSA_KEYS <= set(tsa) <= _TSA_KEYS | {"require_critical_eku"}:
        raise RenewalRecordError(f"tsa needs {sorted(_TSA_KEYS)}")
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
    legal grade, its root is listed and it is not revoked (rule 4, D5, C15); a ``reason: "test"``
    record is never eligible. ``lists`` holds the ``token_sha256`` of each of its ``tokens`` (the
    ``protects`` lists do not count: they serve the builder's match only).
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


@dataclass(frozen=True)
class RenewalReport:
    """The status of every renewable token, sorted by path, and the count of each status."""

    tokens: tuple[TokenStatus, ...]
    totals: dict[Status, int]

    @classmethod
    def of(cls, tokens: Iterable[TokenStatus]) -> RenewalReport:
        ordered = tuple(sorted(tokens, key=lambda token: token.path))
        totals: dict[Status, int] = dict.fromkeys(STATUSES, 0)
        for token in ordered:
            totals[token.status] += 1
        return cls(ordered, totals)


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
