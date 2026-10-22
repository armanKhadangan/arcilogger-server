"""Evidence packages are built in the background (``POST /api/v1/evidence``).

Building a package reads, hashes and zips whole sealed days and runs the standalone verifier's
checks on the result — seconds to minutes. The request therefore only validates the scope,
inserts an ``evidence_packages`` row (``queued``) and returns at once; one background thread per
API process builds the packages one after the other (``queued`` → ``building`` → ``ready`` or
``failed``), so a large request never slows the other services more than one build at a time.

* The vault is only read (:func:`arcilogger_core.evidence.build_package`); the ZIP goes to
  ``exports/`` — the only folder the API service account may write besides the audit spool.
* The **password** of a protected package is generated in the request, returned once in its
  response, handed to the builder in memory and then forgotten: it is never stored, logged or
  audited (:class:`BuildOrder` hides it from ``repr``).
* Audit: ``evidence.request`` when the request is accepted (scope, no password) and
  ``evidence.create`` when the build ends — on success with the package id, the SHA-256 of the
  ZIP, of ``paket.json`` and of ``verify.py`` (to hand to the recipient on a separate channel),
  on failure with the error. Both carry the requesting user and address.
* The builder thread is a daemon: stopping the API service does not wait for a long build.
  A build that a stop or crash interrupted leaves its row ``queued``/``building`` (and perhaps a
  ``*.zip.part``, which the purge removes after a day); the first evidence request of the next
  API process marks such rows ``failed`` (:meth:`EvidenceBuilder.recover`), and the worker's
  daily purge does the same for rows older than a day.
* A build only finishes a row that is still ``building``: if the restart recovery or the
  worker's stale-build check already marked it ``failed`` (and audited that), a build that ends
  later leaves the row as it is, deletes its ZIP and writes no second, contradicting entry.
* Just before writing, the builder checks the free space of the data disk once more
  (:func:`arcilogger_api.evidence_limits.space_refusal`); a disk that filled up while the
  package waited fails the build with a clear message instead of filling the disk.
* A package made from a 5651 lookup contains the lookup's **official report** (spec 9.6 "and
  the PDF report"): the request makes sure the report is stored (rendered once through the
  normal report path if nobody downloaded it yet), and the build reads **the stored bytes**
  (:func:`arcilogger_api.reports.read_stored`, checked against ``lookup_reports.sha256``; with
  its ``.zd`` when the report was time-stamped) into ``rapor/`` — never a second rendering. The
  report's SHA-256 is in ``paket.json``, in ``lookup_reports`` and in the audit entries
  (``lookup.report``, ``evidence.request``, ``evidence.create``); a stored report that changed
  or vanished meanwhile fails the build.
* The report's **time stamp** goes in only when it verified when the report was stored *and*
  still verifies against the roots the package carries. Otherwise (the stamp failed, did not
  verify, or the roots changed since) the report goes in without it, and ``paket.json``
  (``report_note``), the guides, the verifier (a warning) and the audit entries
  (``details.report.token_note``) say why — the stored report is never rendered again, so a
  token that does not verify must not block every package of the lookup.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, NamedTuple

from cryptography import x509
from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.audit import SystemActor, record_audit
from arcilogger_api.deps import AppState
from arcilogger_api.evidence_limits import Limits, space_refusal
from arcilogger_api.issues import bilingual_error
from arcilogger_api.reports import ReportError, read_stored, read_token
from arcilogger_api.reports.store import export_path
from arcilogger_api.sealcheck import read_roots
from arcilogger_api.sealinfo import seal_complete, skipped_day_cover
from arcilogger_core.evidence import (
    EvidenceError,
    PackageInfo,
    RawRef,
    build_package,
    requires_critical_eku,
)
from arcilogger_core.evidence.builder import REPORT_FOLDER
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_core.tsa.verify import verify_token
from arcilogger_db.models import EvidencePackage, LookupReport
from arcilogger_db.settings import get_setting

log = logging.getLogger("arcilogger.api.evidence")

#: Statuses of a package that is not finished yet.
UNFINISHED: Final = ("queued", "building")
#: Longest error text stored on a failed package.
ERROR_MAX: Final = 1000
INTERRUPTED: Final = (
    "Oluşturma yarıda kaldı (API yeniden başlatıldı); paketi yeniden oluşturun. / "
    "The build was interrupted (the API restarted); create the package again."
)
#: Turkish sentence of a builder message without a translation (``issues.bilingual_error``).
BUILD_FAILED_TR: Final = "Paket oluşturulamadı"
FILE_ERROR_TR: Final = "Paket dosyası okunamadı ya da yazılamadı (dosya sistemi hatası)"


class BilingualError(EvidenceError):
    """A build failure whose message is already ``"Türkçe / English"`` (the API's own)."""


UNEXPECTED: Final = (
    "Beklenmeyen bir hata oluştu; ayrıntılar sistem kaydında. / "
    "An unexpected error occurred; details are in the system log."
)
#: Why a lookup's report goes into a package without its time stamp (``report_note``).
NOTE_STAMP_FAILED: Final = (
    "Rapor için zaman damgası istendi, fakat alınamadı; raporun damgası yok. Raporun kendisi "
    "paket.json'daki SHA-256 özetiyle bağlıdır. / A time stamp was requested for the report but "
    "could not be obtained; the report is bound by its SHA-256 in paket.json."
)
NOTE_STAMP_UNVERIFIED: Final = (
    "Raporun zaman damgası, rapor saklandığında doğrulanamadı; damga pakete konmadı. Raporun "
    "kendisi paket.json'daki SHA-256 özetiyle bağlıdır. / The report's time stamp did not verify "
    "when the report was stored; it is not in the package. The report is bound by its SHA-256 in "
    "paket.json."
)
NOTE_STAMP_ROOTS: Final = (
    "Raporun zaman damgası, paketteki kök sertifikalarla doğrulanamıyor (kökler damgadan sonra "
    "değişmiş olabilir); damga pakete konmadı. Raporun kendisi paket.json'daki SHA-256 özetiyle "
    "bağlıdır. / The report's time stamp does not verify against the package's root "
    "certificates; it is not in the package. The report is bound by its SHA-256 in paket.json."
)
_APP_ATTRIBUTE: Final = "arci_evidence_builder"
_creation_lock = threading.Lock()


def keep_days(session: Session) -> int:
    """``evidence.keep_days`` (at least one day)."""
    try:
        return max(1, int(get_setting(session, "evidence.keep_days")))
    except (TypeError, ValueError):
        return 30


@dataclass(frozen=True)
class Requester:
    """The user who asked for the package (audit actor of the build's entries)."""

    id: int | None
    username: str


@dataclass(frozen=True)
class ReportRef:
    """The stored official report a package of a lookup contains (``lookup_reports`` row)."""

    report_id: int
    lookup_id: int
    #: File name in ``exports/reports`` and in the package (``rapor/<name>``).
    name: str
    sha256: str
    size: int
    #: ``lookup_reports.timestamp.token_sha256`` when the report's verified token goes in.
    token_sha256: str | None = None
    #: Why the report goes in without a time stamp it was meant to have (``report_note``).
    token_note: str | None = None

    @classmethod
    def of(cls, row: LookupReport) -> ReportRef:
        """The report as stored. Its token only when it verified at storage time
        (``timestamp.verified``); a failed or unverified stamp gives a note instead."""
        stamp = row.timestamp or {}
        token_sha256 = note = None
        if row.timestamp_status == "stamped" and stamp.get("token_file"):
            if stamp.get("verified") is True:
                token_sha256 = str(stamp.get("token_sha256"))
            else:
                note = NOTE_STAMP_UNVERIFIED
        elif row.timestamp_status == "failed":
            note = NOTE_STAMP_FAILED
        return cls(
            report_id=row.id,
            lookup_id=row.lookup_id,
            name=row.path.rsplit("/", 1)[-1],
            sha256=row.sha256,
            size=row.size,
            token_sha256=token_sha256,
            token_note=note,
        )

    @property
    def package_path(self) -> str:
        return f"{REPORT_FOLDER}{self.name}"

    def details(self) -> dict[str, Any]:
        """Audit details: where the report is in the package and its hashes."""
        return {
            "lookup_id": self.lookup_id,
            "file": self.package_path,
            "sha256": self.sha256,
            "size": self.size,
            "token_file": f"{self.package_path}.zd" if self.token_sha256 else None,
            "token_sha256": self.token_sha256,
            "token_note": self.token_note,
        }


class ReportFiles(NamedTuple):
    """What goes into ``rapor/``: the stored bytes and the reference as built."""

    pdf: bytes
    token: bytes | None
    ref: ReportRef


@dataclass(frozen=True)
class BuildOrder:
    """What to build. ``password`` lives only in memory and never appears in ``repr``."""

    package_id: int
    days: tuple[date, ...]
    sources: tuple[str, ...] | None
    extract: tuple[RawRef, ...]
    requester: Requester
    ip: str | None
    lookup_ref: str | None = None
    reference: str | None = None
    #: The scope as audited with the request (``evidence.request``), repeated on completion.
    scope: Mapping[str, Any] = field(default_factory=dict)
    encrypt: bool = False
    password: str | None = field(default=None, repr=False, compare=False)
    #: Size estimate of the request (``evidence_limits.estimate_bytes``) for the space check.
    estimated_bytes: int = 0
    #: The lookup's stored official report (packages made from a lookup).
    report: ReportRef | None = None
    #: Add the days' ARCILOGGER audit log (administrators only, checked by the request).
    include_audit_log: bool = False


def package_legal_grade(info: PackageInfo) -> bool | None:
    """What the package row (and the API) records as ``legal_grade`` (decision 5).

    ``True``: every day claims the Kamu SM production server and chains to a root listed in
    ``tsa.legal_root_fingerprints``. ``False``: at least one day was stamped by a test or mock
    provider. ``None``: every day claims the production server, but not every root is listed
    (the default empty list) — neither "legal grade" nor a test stamp; the package guide says
    which root fingerprint to compare.
    """
    if info.legal_grade:
        return True
    return None if info.legal_grade_claimed else False


def package_details(info: PackageInfo) -> dict[str, Any]:
    """Audit details of a finished package (never the password)."""
    return {
        "status": "ready",
        "package_id": info.package_id,
        "file": info.path.name,
        "sha256": info.sha256,
        "size": info.size,
        "file_count": info.file_count,
        "index_sha256": info.index_sha256,
        "verifier_sha256": info.verifier_sha256,
        "legal_grade": info.legal_grade,
        "legal_grade_claimed": info.legal_grade_claimed,
        "password_protected": info.password_protected,
        "days": [day.isoformat() for day in info.days],
        "sources": list(info.sources) if info.sources is not None else None,
        "extract_lines": info.extract_lines,
    }


class EvidenceBuilder:
    """The background builder of one API process: one daemon thread, one build at a time."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._orders: queue.Queue[BuildOrder] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pending = 0
        self._recovered = False
        self._last_second: int | None = None
        #: Rows created before this moment cannot belong to this builder.
        self.started_at = utc_now()

    @property
    def sessions(self) -> sessionmaker[Session]:
        return self._state.session_factory()

    @property
    def layout(self) -> Layout:
        return self._state.layout

    # -- requests ---------------------------------------------------------------------------------

    def submit(self, order: BuildOrder) -> None:
        with self._lock:
            self._pending += 1
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop, name="arci-evidence", daemon=True
                )
                self._thread.start()
        self._orders.put(order)

    def _loop(self) -> None:
        while True:
            order = self._orders.get()
            try:
                self._run(order)
            except Exception:  # pragma: no cover - _run records its own failures
                log.exception("evidence package %d: build loop error", order.package_id)
            finally:
                with self._idle:
                    self._pending -= 1
                    self._idle.notify_all()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until every submitted build ended (tests); ``False`` on timeout."""
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout=timeout)

    def recover(self) -> list[int]:
        """Once per process: rows left unfinished by an earlier API process become ``failed``."""
        with self._lock:
            if self._recovered:
                return []
            self._recovered = True
        try:
            return mark_interrupted(self.sessions, before=self.started_at)
        except Exception:  # never fail the request that happened to trigger the recovery
            log.exception("could not mark interrupted evidence packages")
            with self._lock:
                self._recovered = False
            return []

    # -- the build --------------------------------------------------------------------------------

    def _run(self, order: BuildOrder) -> None:
        try:
            with self.sessions() as session:
                started = session.execute(
                    update(EvidencePackage)
                    .where(
                        EvidencePackage.id == order.package_id, EvidencePackage.status == "queued"
                    )
                    .values(status="building")
                    .returning(EvidencePackage.id)
                ).scalar_one_or_none()
                session.commit()
        except Exception:
            log.exception("evidence package %d: cannot start the build", order.package_id)
            return
        if started is None:  # pragma: no cover - the row changed after it was queued
            log.warning("evidence package %d is no longer queued; not built", order.package_id)
            return
        try:
            self._check_space(order)
            info, report = self._build(order)
        except Exception as exc:
            self._failed(order, exc)
            return
        self._ready(order, info, report)

    def _check_space(self, order: BuildOrder) -> None:
        """Refuse to start writing when the data disk filled up while the package waited."""
        with self.sessions() as session:
            limits = Limits.load(session)
        refusal = space_refusal(self.layout, limits, estimate=order.estimated_bytes)
        if refusal is not None:
            raise EvidenceError(f"{refusal.message_tr} / {refusal.message_en}")

    def _clock(self) -> datetime:
        """The creation time of a package, never in the same second as the previous one.

        The package id is that second plus a hash of the scope, so two packages of the same
        scope must not be created within one second (the builder would refuse the second).
        """
        now = utc_now()
        while self._last_second is not None and int(now.timestamp()) <= self._last_second:
            time.sleep(0.05)
            now = utc_now()
        self._last_second = int(now.timestamp())
        return now

    def _report_bytes(
        self, ref: ReportRef, roots: Sequence[x509.Certificate], require_critical_eku: bool = True
    ) -> ReportFiles:
        """The stored report (and token) of ``ref``, checked against the recorded hashes; the
        token is left out (with a note) when it does not verify against the package's roots
        under the package's EKU rule (:func:`requires_critical_eku`)."""
        with self.sessions() as session:
            row = session.get(LookupReport, ref.report_id)
            if row is None or row.sha256 != ref.sha256:
                raise BilingualError(
                    "Sorgunun saklı raporu değişmiş ya da silinmiş; paketi yeniden oluşturun. / "
                    "The lookup's stored report changed or was removed; create the package again."
                )
            try:
                pdf = read_stored(self.layout, row)
                token = read_token(self.layout, row) if ref.token_sha256 else None
            except ReportError as exc:
                raise BilingualError(
                    f"Sorgunun saklı raporu kullanılamıyor ({exc.code}); paket oluşturulmadı. / "
                    f"The lookup's stored report cannot be used ({exc.code}): {exc}"
                ) from exc
            pdf_path = export_path(self.layout, row.path)
        if token is not None:
            check = verify_token(
                token, roots=roots, file_path=pdf_path, require_critical_eku=require_critical_eku
            )
            if not check.ok:
                log.warning(
                    "evidence: the time stamp of %s does not verify against the package roots "
                    "(%s); the report goes in without it",
                    ref.name,
                    "; ".join(check.errors),
                )
                return ReportFiles(
                    pdf, None, replace(ref, token_sha256=None, token_note=NOTE_STAMP_ROOTS)
                )
        return ReportFiles(pdf, token, ref)

    def _build(self, order: BuildOrder) -> tuple[PackageInfo, ReportRef | None]:
        try:
            return self._build_files(order)
        except BilingualError:
            raise
        except EvidenceError as exc:  # the builder's own English message: Turkish first
            raise BilingualError(bilingual_error(str(exc), BUILD_FAILED_TR)) from exc

    def _build_files(self, order: BuildOrder) -> tuple[PackageInfo, ReportRef | None]:
        config = self._state.require_config()
        layout = self.layout
        roots = read_roots(layout, config.tsa.roots_dir, provider=config.tsa.provider)
        files = (
            self._report_bytes(order.report, roots, requires_critical_eku(layout, order.days))
            if order.report is not None
            else None
        )
        info = build_package(
            layout,
            order.days,
            roots=roots,
            sources=order.sources,
            extract=order.extract,
            encrypt=order.encrypt,
            password=order.password,
            report_pdf=files.pdf if files is not None else None,
            report_name=files.ref.name if files is not None else "rapor.pdf",
            report_token=files.token if files is not None else None,
            report_note=files.ref.token_note if files is not None else None,
            include_audit_log=order.include_audit_log,
            created_by=order.requester.username,
            lookup_id=order.lookup_ref,
            reference=order.reference,
            site_name=config.site_name,
            crl_dir=layout.state / "crl",
            clock=self._clock,
            legal_root_fingerprints=config.tsa.legal_root_fingerprints,
        )
        return info, files.ref if files is not None else None

    def _ready(self, order: BuildOrder, info: PackageInfo, report: ReportRef | None) -> None:
        try:
            with self.sessions() as session:
                row = session.get_one(EvidencePackage, order.package_id, with_for_update=True)
                if row.status != "building":
                    session.rollback()
                    log.warning(
                        "evidence package %d was marked %s while it was built; %s discarded",
                        order.package_id,
                        row.status,
                        info.path.name,
                    )
                    _discard(info.path)
                    return
                finished = utc_now()
                row.status = "ready"
                row.error = None
                row.path = info.path.name
                row.sha256 = info.sha256
                row.size = info.size
                row.package_name = info.package_id
                row.index_sha256 = info.index_sha256
                row.verifier_sha256 = info.verifier_sha256
                row.legal_grade = package_legal_grade(info)
                row.finished_at = finished
                row.expires_at = finished + timedelta(days=keep_days(session))
                details = {**package_details(info), "scope": dict(order.scope)}
                if report is not None:
                    details["report"] = report.details()
                record_audit(
                    session,
                    actor=order.requester,
                    action="evidence.create",
                    target=f"evidence:{order.package_id}",
                    details=details,
                    ip=order.ip,
                )
                session.commit()
        except Exception:
            # The ZIP stays in exports/ without a ready row; the purge removes it after
            # evidence.keep_days and the next API start marks the row failed.
            log.exception("evidence package %d built but not recorded", order.package_id)
            return
        log.info("evidence package %d ready: %s", order.package_id, info.path.name)

    def _failed(self, order: BuildOrder, exc: Exception) -> None:
        if isinstance(exc, EvidenceError | OSError):
            # EvidenceError: "Türkçe / English" already; a file system error gets its sentence
            text = str(exc) if isinstance(exc, EvidenceError) else f"{FILE_ERROR_TR} / {exc}"
            message = text[:ERROR_MAX]
            log.warning("evidence package %d failed: %s", order.package_id, message)
        else:
            message = UNEXPECTED
            log.error("evidence package %d failed", order.package_id, exc_info=exc)
        try:
            with self.sessions() as session:
                changed = session.execute(
                    update(EvidencePackage)
                    .where(
                        EvidencePackage.id == order.package_id,
                        EvidencePackage.status == "building",
                    )
                    .values(status="failed", error=message, finished_at=utc_now())
                    .returning(EvidencePackage.id)
                ).scalar_one_or_none()
                if changed is None:  # already marked (and audited) by the recovery or the worker
                    session.rollback()
                    log.warning(
                        "evidence package %d was no longer building; failure not recorded",
                        order.package_id,
                    )
                    return
                record_audit(
                    session,
                    actor=order.requester,
                    action="evidence.create",
                    target=f"evidence:{order.package_id}",
                    details={"status": "failed", "error": message, "scope": dict(order.scope)},
                    ip=order.ip,
                )
                session.commit()
        except Exception:
            log.exception("evidence package %d: cannot record the failure", order.package_id)


def mark_interrupted(factory: sessionmaker[Session], *, before: datetime) -> list[int]:
    """Mark ``queued``/``building`` rows created before ``before`` as failed (audited)."""
    with factory() as session:
        rows = session.scalars(
            select(EvidencePackage)
            .where(EvidencePackage.status.in_(UNFINISHED), EvidencePackage.created_at < before)
            .order_by(EvidencePackage.id)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.status, row.error, row.finished_at = "failed", INTERRUPTED, utc_now()
            record_audit(
                session,
                actor=SystemActor(),
                action="evidence.create",
                target=f"evidence:{row.id}",
                details={"status": "failed", "error": INTERRUPTED},
            )
        session.commit()
        return [row.id for row in rows]


def builder_for(request: Request) -> EvidenceBuilder:
    """The application's builder (created on first use)."""
    app = request.app
    with _creation_lock:
        builder = getattr(app.state, _APP_ATTRIBUTE, None)
        if builder is None:
            builder = EvidenceBuilder(app.state.arci)
            setattr(app.state, _APP_ATTRIBUTE, builder)
    if not isinstance(builder, EvidenceBuilder):  # pragma: no cover - defensive
        raise TypeError("unexpected evidence builder")
    return builder


def _discard(path: Path) -> None:
    """Delete a ZIP no row describes (the purge removes it later if this fails)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - e.g. Windows file lock; purged after keep_days
        log.warning("cannot delete %s now: %s", path.name, exc)


def days_not_sealed(layout: Layout, days: Sequence[date]) -> list[date]:
    """Days the sealing has not completed (they cannot be packaged), see :func:`seal_complete`.

    A day skipped as empty (``seal.max_consecutive_empty_days``) can be packaged together with
    the sealed day whose manifest lists it (``covered_by``): the builder and the verifier follow
    the chain across it.
    """
    wanted = set(days)
    return [
        day
        for day in days
        if not seal_complete(layout, day) and skipped_day_cover(layout, day) not in wanted
    ]
