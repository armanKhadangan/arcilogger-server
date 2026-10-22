"""What the sealing page shows ("Mühürleme", spec 12.3 page 9), read from the files.

The facts about sealed days come from the vault and the job state that the scheduled
``arcilogger-core`` tasks write — never from the database, which is derived data:

* ``vault/YYYY/MM/DD/manifest-<day>.json`` and ``.json.zd`` — what was sealed, the time stamp;
* ``state/seal/<day>.json`` — attempts, last error, next attempt, serial, ``sealed_at``;
* ``state/verify/last-*.json`` — the scheduled verifications (daily, weekly, monthly);
* ``state/replica/<day>.json`` — the second copy;
* ``vault/deletions/*-imha.json`` + ``.zd`` — days deleted by retention (a record without its
  time stamp is the leftover of a retention run that failed and deleted nothing);
* ``state/credit.jsonl`` — Kamu SM credit checks.

The one database fact is the result of "Doğrula" (``seal_verifications``): the API may not
write ``state/verify`` (ADR-0007). The page shows the newer of the two verifications.

A day is **sealed** when its manifest and time stamp are in the vault *and* the Seal task
finished it (``state/seal/<day>.json`` says ``sealed``, or is missing — e.g. state restored
without it). A day whose token exists but whose task state still has an error (locking the
files or the post-stamp file check failed) is shown ``failed`` with that error: the Seal task
does not treat it as sealed either, and the following days wait for it.

Everything here only reads, and a request reads every file at most once (:class:`VaultView`).
A damaged state file never breaks the page: it is reported as a message on its day; a spool
folder the API may not list (Windows rights) is reported as a problem ("erişilemiyor"), never as
a server error.

Messages are Turkish first and never carry the English text of the core code (spec 12.4): a
failed sealing attempt is described from the error **code** the Seal task records
(:func:`arcilogger_api.issues.seal_failure`, "Mühürleme başarısız: Kamu SM sunucusuna
ulaşılamadı. 15 dk sonra tekrar denenecek."), verification errors are translated
(:func:`arcilogger_api.issues.issue_tr`); the technical text stays available apart from the
message (``error_detail``, ``seal_state.last_error``).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any, Final

from cryptography import x509
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from arcilogger_api.issues import (
    issue_tr,
    issues_tr,
    last_error_tr,
    seal_failure,
    token_issues_tr,
)
from arcilogger_core.config import Config
from arcilogger_core.fmt import format_datetime
from arcilogger_core.layout import Layout
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_core.tsa.asn1 import parse_container
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.verify import (
    confirmed_legal_grade,
    inspect_token,
    is_test_stamp,
    verify_token,
)
from arcilogger_db.models import SealVerification

log = logging.getLogger("arcilogger.api.seals")

#: A sealed day counts as "verified" when a verification passed within this many days. The
#: weekly task checks the last 90 days, the monthly task the whole chain, so every sealed day is
#: checked at least once a month; 35 days leave a margin for a late monthly run.
RECENT_VERIFY_DAYS: Final = 35
#: Kamu SM credit history returned by ``/credits`` (one check a day: about a year).
CREDIT_HISTORY: Final = 400

#: state → (Turkish label, English label, tone for the colour: ok / warning / error / neutral)
STATES: Final[dict[str, tuple[str, str, str]]] = {
    "verified": ("Mühürlendi ve doğrulandı", "Sealed and verified", "ok"),
    "sealed": (
        "Mühürlendi, yakın zamanda doğrulanmadı",
        "Sealed, not verified recently",
        "warning",
    ),
    "late": ("Geç mühürlendi", "Sealed late", "warning"),
    "pending": ("Mühürlenmeyi bekliyor", "Waiting to be sealed", "warning"),
    "failed": ("Başarısız", "Failed", "error"),
    "open": ("Gün sürüyor", "Day in progress", "neutral"),
    "future": ("Henüz gelmedi", "Not yet", "neutral"),
    "no_data": ("Veri yok", "No data", "neutral"),
    "deleted": ("Saklama süresi doldu, silindi", "Deleted after the retention period", "neutral"),
}


# -- small readers --------------------------------------------------------------------------------


def _read_json(path: Path) -> tuple[Any, str | None]:
    """``(data, None)``, ``(None, None)`` when missing, ``(None, problem)`` when unreadable."""
    try:
        return json.loads(path.read_bytes()), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        log.warning("cannot read %s: %s", path, exc)
        return None, f"{path.name}: {type(exc).__name__}"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


#: ``late_reason`` of a day stamped late without a failed attempt before (core seal.py).
LATE_WITHOUT_ERROR: Final = "sealing ran late"


def _sealed_detail(
    manifest: dict[str, Any] | None, verification: Verification | None
) -> str | None:
    """The technical (English) text behind the message of a sealed day, if there is one."""
    if verification is not None and not verification.ok and verification.errors:
        return "; ".join(verification.errors)
    reason = str((manifest or {}).get("late_reason") or "")
    return reason if reason and reason != LATE_WITHOUT_ERROR else None


def vault_relative(layout: Layout, path: Path) -> str:
    return "vault/" + path.relative_to(layout.vault).as_posix()


def _unfinished(state: dict[str, Any] | None, problem: str | None) -> bool:
    """The Seal task has not completed a day it already stamped (see the module text)."""
    return problem is not None or (
        state is not None and state.get("status") not in ("sealed", "deleted")
    )


def read_seal_state(layout: Layout, day: date) -> tuple[dict[str, Any] | None, str | None]:
    """``state/seal/<day>.json``: ``(state, None)``, ``(None, None)`` when missing,
    ``(None, problem)`` when unreadable."""
    path = layout.seal_state(day)
    data, problem = _read_json(path)
    if data is not None and not isinstance(data, dict):
        return None, f"{path.name}: not a seal state"
    return data, problem


def seal_complete(layout: Layout, day: date) -> bool:
    """Manifest and token in the vault and the Seal task finished the day."""
    if not (layout.manifest(day).is_file() and layout.token(day).is_file()):
        return False
    return not _unfinished(*read_seal_state(layout, day))


def skipped_day_cover(layout: Layout, day: date) -> date | None:
    """The sealed day whose manifest lists ``day`` as empty (``unsealed_empty_days``), when
    ``day`` was skipped as part of a long run without any log and that day's seal is complete;
    otherwise ``None`` (a provisional skip, or not a skipped day)."""
    state, problem = read_seal_state(layout, day)
    if problem is not None or state is None or state.get("status") != "skipped":
        return None
    try:
        covering = date.fromisoformat(str(state.get("covered_by")))
    except ValueError:
        return None
    return covering if covering > day and seal_complete(layout, covering) else None


@dataclass(frozen=True)
class Verification:
    """One verification of a day: scheduled (``state/verify``) or on demand (database)."""

    at: datetime
    ok: bool
    deep: bool
    source: str  # "scheduled" | "on_demand"
    by: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "ok": self.ok,
            "deep": self.deep,
            "source": self.source,
            "by": self.by,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            # Turkish first in the interface (spec 12.4); the English lists stay the technical text.
            "errors_tr": issues_tr(list(self.errors)),
            "warnings_tr": issues_tr(list(self.warnings)),
        }


def newest(*candidates: Verification | None) -> Verification | None:
    present = [c for c in candidates if c is not None]
    return max(present, key=lambda c: c.at) if present else None


def on_demand_verifications(
    session: Session, first: date, last: date, *, conclusive: bool = True
) -> dict[date, SealVerification]:
    """Newest "Doğrula" result per day (only ``ok``/``failed`` when ``conclusive``)."""
    query = (
        select(SealVerification)
        .where(SealVerification.day >= first, SealVerification.day <= last)
        .order_by(SealVerification.day, SealVerification.finished_at.desc())
        .ext(distinct_on(SealVerification.day))
    )
    if conclusive:
        query = query.where(SealVerification.outcome.in_(("ok", "failed")))
    return {row.day: row for row in session.scalars(query)}


def as_verification(row: SealVerification) -> Verification:
    return Verification(
        at=row.finished_at,
        ok=row.outcome == "ok",
        deep=row.deep,
        source="on_demand",
        by=row.username,
        errors=tuple(row.errors),
        warnings=tuple(row.warnings),
    )


def token_certificate(token: bytes, subject: str) -> x509.Certificate | None:
    """The certificate named ``subject`` (the TSA) among the certificates of a token."""
    try:
        content, _ = parse_container(token)
        for choice in content["content"]["certificates"] or []:
            if choice.name != "certificate":
                continue
            certificate = x509.load_der_x509_certificate(choice.chosen.dump())
            if certificate.subject.rfc4514_string() == subject:
                return certificate
    except (TsaError, ValueError, TypeError, KeyError):
        return None
    return None


# -- the view of one request ----------------------------------------------------------------------


@dataclass
class VaultView:
    """Reads the vault and the job state lazily, each file at most once per request."""

    layout: Layout
    config: Config
    now: datetime = field(default_factory=utc_now)
    problems: list[str] = field(default_factory=list)

    @property
    def today(self) -> date:
        return site_day(self.now)

    @cached_property
    def scheduled(self) -> dict[date, Verification]:
        """Newest scheduled verification per day from every ``state/verify/last-*.json``."""
        found: dict[date, Verification] = {}
        folder = self.layout.state / "verify"
        if not folder.is_dir():
            return found
        for path in sorted(folder.glob("last-*.json")):
            data, problem = _read_json(path)
            if problem:
                self.problems.append(problem)
            if not isinstance(data, dict):
                continue
            at = _parse_time(data.get("at"))
            if at is None:
                continue
            for check in data.get("checks") or []:
                try:
                    day = date.fromisoformat(str(check["day"]))
                except (KeyError, TypeError, ValueError):
                    continue
                current = Verification(
                    at=at,
                    ok=bool(check.get("ok")),
                    deep=bool(data.get("deep")),
                    source="scheduled",
                    by=path.stem,
                    errors=tuple(str(e) for e in check.get("errors") or []),
                    warnings=tuple(str(w) for w in check.get("warnings") or []),
                )
                if day not in found or found[day].at < at:
                    found[day] = current
        return found

    @cached_property
    def deletions(self) -> dict[date, dict[str, Any]]:
        """Day → deletion record (``vault/deletions/<date>-imha.json``) that deleted it."""
        found: dict[date, dict[str, Any]] = {}
        folder = self.layout.deletions
        if not folder.is_dir():
            return found
        for path in sorted(folder.glob("*-imha.json")):
            if not path.with_name(path.name + ".zd").is_file():
                continue  # never stamped: the retention run failed and deleted nothing
            data, problem = _read_json(path)
            if problem:
                self.problems.append(problem)
            if not isinstance(data, dict):
                continue
            for entry in data.get("days") or []:
                try:
                    day = date.fromisoformat(str(entry["day"]))
                except (KeyError, TypeError, ValueError):
                    continue
                found[day] = {
                    "record": vault_relative(self.layout, path),
                    "created_at": data.get("created_at"),
                    "manifest_sha256": entry.get("manifest_sha256"),
                    "token_sha256": entry.get("token_sha256"),
                    "serial": entry.get("serial"),
                }
        return found

    def seal_state(self, day: date) -> tuple[dict[str, Any] | None, str | None]:
        return read_seal_state(self.layout, day)

    def replica(self, day: date) -> dict[str, Any]:
        data, problem = _read_json(self.layout.replica_state(day))
        if problem:
            return {"status": "unreadable", "message": problem}
        if isinstance(data, dict):
            files = data.get("files")
            return {
                "status": str(data.get("status", "?")),
                "verified_at": data.get("verified_at"),
                "target": data.get("target"),
                "files": len(files) if isinstance(files, dict) else None,
            }
        return {"status": "missing" if self.config.replica.enabled else "disabled"}

    def is_sealed(self, day: date) -> bool:
        """A manifest **and** its time stamp in the vault (a failed attempt leaves only the
        manifest)."""
        return self.layout.manifest(day).is_file() and self.layout.token(day).is_file()

    def manifest(self, day: date) -> tuple[dict[str, Any] | None, str | None]:
        data, problem = _read_json(self.layout.manifest(day))
        if data is not None and (not isinstance(data, dict) or data.get("day") != day.isoformat()):
            return None, f"manifest-{day.isoformat()}.json: not a manifest of this day"
        return data, problem

    # -- the state of a day ---------------------------------------------------------------------

    def verification(self, day: date, on_demand: SealVerification | None) -> Verification | None:
        return newest(
            self.scheduled.get(day), as_verification(on_demand) if on_demand is not None else None
        )

    def summary(self, day: date, on_demand: SealVerification | None = None) -> dict[str, Any]:
        """The calendar cell of ``day``: state, colour tone, message and the key numbers."""
        result: dict[str, Any] = {
            "day": day.isoformat(),
            "message_tr": None,
            "message_en": None,
            "error_detail": None,
        }
        manifest, manifest_problem = self.manifest(day)
        state, state_problem = self.seal_state(day)
        verification = self.verification(day, on_demand)
        result["verification"] = (
            None
            if verification is None
            else {"at": verification.at, "ok": verification.ok, "source": verification.source}
        )
        sealed = self.is_sealed(day)
        if not sealed:
            manifest = None  # a manifest without a token is a leftover of a failed attempt
        overdue = False
        name: str
        message: tuple[str, str] | None = None
        if day > self.today:
            name = "future"
        elif not sealed and (
            day in self.deletions or (state is not None and state.get("status") == "deleted")
        ):
            name = "deleted"
        elif sealed and _unfinished(state, state_problem):
            overdue = self._overdue(day)
            name, message = self._unsealed_state(day, state, state_problem, stamped=True)
            result["error_detail"] = state.get("last_error") if state else None
        elif sealed:
            name, message = self._sealed_state(day, manifest, manifest_problem, verification)
            result["error_detail"] = _sealed_detail(manifest, verification)
        elif day == self.today:
            name = "open"
        else:
            overdue = self._overdue(day)
            name, message = self._unsealed_state(day, state, state_problem)
            result["error_detail"] = state.get("last_error") if state else None
        label_tr, label_en, tone = STATES[name]
        result.update(state=name, label_tr=label_tr, label_en=label_en, tone=tone)
        result["overdue"] = overdue
        if message is not None:
            result["message_tr"], result["message_en"] = message
        files = manifest.get("files") if isinstance(manifest, dict) else None
        result["files"] = len(files) if isinstance(files, list) else None
        result["lines"] = (
            sum(int(f.get("lines") or 0) for f in files if isinstance(f, dict))
            if isinstance(files, list)
            else None
        )
        result["stamped_late"] = bool(manifest.get("stamped_late")) if manifest else False
        result["sealed_at"] = state.get("sealed_at") if state else None
        result["serial"] = state.get("serial") if state else None
        result["gen_time"] = state.get("gen_time") if state else None
        result["replica"] = self.replica(day)["status"] if sealed else None
        return result

    def _sealed_state(
        self,
        day: date,
        manifest: dict[str, Any] | None,
        problem: str | None,
        verification: Verification | None,
    ) -> tuple[str, tuple[str, str] | None]:
        if manifest is None:
            reason = problem or "manifest unreadable"
            return "failed", (f"Manifest okunamıyor: {reason}", f"Manifest unreadable: {reason}")
        if verification is not None and not verification.ok:
            first = verification.errors[0] if verification.errors else "?"
            when = format_datetime(verification.at)
            return "failed", (
                f"Doğrulama başarısız ({when}): {issue_tr(first)}",
                f"Verification failed ({when}): {first}",
            )
        if manifest.get("stamped_late"):
            reason = str(manifest.get("late_reason") or "")
            failed_before = bool(reason) and reason != LATE_WITHOUT_ERROR
            return "late", (
                "Gün bitiminden sonraki süre aşıldıktan sonra damgalandı."
                + (" Önceki mühürleme denemeleri başarısız olmuştu." if failed_before else ""),
                "Stamped after the allowed time after the end of the day."
                + (f" Earlier attempts had failed: {reason}" if failed_before else ""),
            )
        if verification is not None and verification.at >= self.now - timedelta(
            days=RECENT_VERIFY_DAYS
        ):
            return "verified", None
        return "sealed", (
            f"Son {RECENT_VERIFY_DAYS} günde başarılı doğrulama kaydı yok.",
            f"No successful verification in the last {RECENT_VERIFY_DAYS} days.",
        )

    def _overdue(self, day: date) -> bool:
        return self.now > day_window(day)[1] + timedelta(hours=self.config.seal.late_after_hours)

    def _unsealed_state(
        self,
        day: date,
        state: dict[str, Any] | None,
        problem: str | None,
        *,
        stamped: bool = False,
    ) -> tuple[str, tuple[str, str] | None]:
        """``stamped``: the token is in the vault, but the Seal task did not finish the day."""
        if problem:
            return "failed", (
                f"Durum dosyası okunamıyor: {problem}",
                f"State unreadable: {problem}",
            )
        if stamped:
            error = str(state.get("last_error") or "") if state else ""
            retry = _parse_time(state.get("next_attempt_at")) if state else None
            when_tr = f" Sonraki deneme: {format_datetime(retry)}." if retry else ""
            when_en = f" Next attempt: {format_datetime(retry)}." if retry else ""
            if error:
                return "failed", (
                    "Zaman damgası alındı, ancak mühürleme görevi günü tamamlayamadı (teknik "
                    f"ayrıntı gün sayfasında).{when_tr}",
                    f"The time stamp was obtained, but sealing did not finish: {error}.{when_en}",
                )
            return "pending", (
                "Zaman damgası alındı; mühürleme görevi günü henüz tamamlamadı.",
                "The time stamp was obtained; the sealing task has not finished the day yet.",
            )
        if state is not None and state.get("status") == "sealed":
            return "failed", (
                "Gün mühürlendi olarak kayıtlı, ancak manifesti ya da zaman damgası kasada yok.",
                "The day is recorded as sealed, but its manifest or time stamp is missing from "
                "the vault.",
            )
        if state is not None and state.get("status") == "skipped":
            # An empty day in a long run without any log (seal.max_consecutive_empty_days): not
            # stamped on its own, listed in the next sealed day's manifest.
            covered = state.get("covered_by")
            return "no_data", (
                "Hiç log yok; uzun bir boş dönemin parçası olarak tek başına damgalanmadı"
                + (f", {covered} gününün manifestinde listeleniyor." if covered else "."),
                "No log at all; part of a long empty run, not stamped on its own"
                + (f", listed in the manifest of {covered}." if covered else "."),
            )
        if state is not None and state.get("last_error"):
            code = state.get("last_error_code")
            retry = _parse_time(state.get("next_attempt_at"))
            return "failed", seal_failure(str(code) if code else None, self.config, retry, self.now)
        if state is not None:
            return "pending", None
        try:
            spooled = bool(self.layout.spool_files_for(day))
        except OSError as exc:  # Windows: the API service may not list the spool (ACLs)
            problem = f"spool: {type(exc).__name__}"
            if problem not in self.problems:
                self.problems.append(problem)
            return "pending", (
                "Ham kayıt klasörü (spool) erişilemiyor (klasör izni); günün durumu belirlenemedi.",
                "The raw log folder (spool) is not accessible (folder rights); the state of the "
                "day is unknown.",
            )
        return ("pending", None) if spooled else ("no_data", None)

    # -- the day page ---------------------------------------------------------------------------

    def detail(
        self,
        day: date,
        roots: Sequence[x509.Certificate],
        on_demand: SealVerification | None,
        last_attempt: SealVerification | None,
    ) -> dict[str, Any]:
        """Everything the day page shows (spec 12.3 page 9), values exactly as sealed."""
        result = self.summary(day, on_demand)
        sealed = self.is_sealed(day)
        manifest = self.manifest(day)[0] if sealed else None
        state, _ = self.seal_state(day)
        result["sealed"] = sealed
        result["seal_state"] = (
            {
                key: state.get(key)
                for key in (
                    "status",
                    "attempts",
                    "last_attempt_at",
                    "last_error",
                    "last_error_code",
                    "next_attempt_at",
                    "sealed_at",
                )
            }
            if state
            else None
        )
        if result["seal_state"] is not None:
            # the cause of the last failure in Turkish; ``last_error`` stays the technical text
            error, code = (
                result["seal_state"]["last_error"],
                result["seal_state"]["last_error_code"],
            )
            result["seal_state"]["last_error_tr"] = (
                last_error_tr(str(code) if code else None, self.config) if error else None
            )
        # A day still in the vault was not deleted, whatever a record says.
        result["deleted"] = None if sealed else self.deletions.get(day)
        verification = self.verification(day, on_demand)
        result["verification"] = None if verification is None else verification.as_json()
        scheduled = self.scheduled.get(day)
        result["verifications"] = {
            "scheduled": None if scheduled is None else scheduled.as_json(),
            "on_demand": None if last_attempt is None else on_demand_json(last_attempt),
        }
        result["replica"] = self.replica(day) if sealed else None
        result.update(manifest=None, files=[], audit_log=None, gaps=[], clock=None)
        result.update(previous=None, deletions=[], chain_check=None, token=None, totals=None)
        if manifest is None:
            return result
        manifest_path = self.layout.manifest(day)
        files = [f for f in manifest.get("files") or [] if isinstance(f, dict)]
        result["manifest"] = {
            "path": vault_relative(self.layout, manifest_path),
            "sha256": sha256_file(manifest_path),
            "bytes": manifest_path.stat().st_size,
            "schema": manifest.get("schema"),
            "created_at": manifest.get("created_at"),
            "window": manifest.get("window"),
            "system": manifest.get("system"),
            "tsa": manifest.get("tsa"),
            "stamped_late": bool(manifest.get("stamped_late")),
            "late_reason": manifest.get("late_reason"),
        }
        result["files"] = [
            {
                key: entry.get(key)
                for key in (
                    "source",
                    "path",
                    "bytes",
                    "bytes_uncompressed",
                    "lines",
                    "first_received",
                    "last_received",
                    "sha256",
                    "sha256_uncompressed",
                    "sender_ips",
                )
            }
            for entry in files
        ]
        result["totals"] = {
            "files": len(files),
            "lines": sum(int(f.get("lines") or 0) for f in files),
            "bytes": sum(int(f.get("bytes") or 0) for f in files),
            "bytes_uncompressed": sum(int(f.get("bytes_uncompressed") or 0) for f in files),
        }
        result["audit_log"] = manifest.get("audit_log")
        result["gaps"] = manifest.get("gaps") or []
        result["clock"] = manifest.get("clock")
        result["deletions"] = manifest.get("deletions") or []
        result["chain_check"] = manifest.get("chain_check")
        result["previous"] = self._previous(manifest.get("previous"))
        result["token"] = self._token(day, manifest, roots)
        return result

    def _previous(self, previous: Any) -> dict[str, Any] | None:
        """The link to the day before, and whether it still matches what is in the vault."""
        if not isinstance(previous, dict):
            return None
        link = {key: previous.get(key) for key in ("day", "manifest_sha256", "token_sha256")}
        try:
            before = date.fromisoformat(str(previous.get("day")))
        except ValueError:
            return {**link, "matches": False, "target": None}
        manifest, token = self.layout.manifest(before), self.layout.token(before)
        if manifest.is_file() and token.is_file():
            actual: dict[str, Any] = {
                "manifest_sha256": sha256_file(manifest),
                "token_sha256": sha256_file(token),
            }
            target = "vault"
        else:
            anchor, _ = _read_json(self.layout.chain_anchor)
            if not isinstance(anchor, dict) or anchor.get("day") != before.isoformat():
                return {**link, "matches": None, "target": None}
            actual = {key: anchor.get(key) for key in ("manifest_sha256", "token_sha256")}
            target = "chain_anchor"
        matches = all(actual[key] == link[key] for key in actual)
        return {**link, "matches": matches, "target": target}

    def _token(
        self, day: date, manifest: dict[str, Any], roots: Sequence[x509.Certificate]
    ) -> dict[str, Any] | None:
        token_path = self.layout.token(day)
        try:
            data = token_path.read_bytes()
        except OSError:
            return None
        tsa = manifest.get("tsa")
        if not isinstance(tsa, dict):
            tsa = {}
        result: dict[str, Any] = {
            "path": vault_relative(self.layout, token_path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "provider": tsa.get("provider"),
            # Legal grade comes from the verified chain (below), never from the manifest alone;
            # the claim is shown apart (a Kamu SM production stamp whose root is not listed in
            # tsa.legal_root_fingerprints is neither "legal grade" nor a test stamp).
            "legal_grade": False,
            # Decision 5 keeps the facts apart: the manifest's claim (Kamu SM production server)
            # and a test (mock) stamp. A production day whose root fingerprint is not listed in
            # tsa.legal_root_fingerprints is neither legal grade nor a test stamp.
            "legal_grade_claimed": tsa.get("legal_grade") is True,
            "test_stamp": tsa.get("provider") == "mock",
        }
        try:
            info = inspect_token(data)
        except (TsaError, ValueError, TypeError, KeyError) as exc:
            result["check"] = _token_check(False, [f"unreadable token: {exc}"], None)
            return result
        certificate = token_certificate(data, info.tsa_name) if info.tsa_name else None
        result.update(
            container=info.container,
            gen_time=info.gen_time,
            serial=info.serial,
            policy=info.policy,
            tsa_name=info.tsa_name,
            digest_alg=info.digest_alg,
            tsa_cert_not_before=certificate.not_valid_before_utc if certificate else None,
            tsa_cert_not_after=info.tsa_cert_not_after,
        )
        if is_test_stamp(info.policy, ""):
            result["test_stamp"] = True
        if not roots:
            result["check"] = _token_check(None, ["no trusted root certificates"], None)
            return result
        check = verify_token(
            data,
            roots=roots,
            file_path=self.layout.manifest(day),
            require_critical_eku=tsa.get("require_critical_eku") is not False,
        )
        root = check.details.get("root") or None
        result["check"] = _token_check(check.ok, list(check.errors), root)
        if is_test_stamp(info.policy, root or ""):
            result["test_stamp"] = True
        result["legal_grade"] = confirmed_legal_grade(
            tsa.get("legal_grade") is True, check, self.config.tsa.legal_root_fingerprints
        )
        return result


def _token_check(ok: bool | None, errors: list[str], root: str | None) -> dict[str, Any]:
    """The day page's token check, its messages also in Turkish (spec 12.4)."""
    return {"ok": ok, "errors": errors, "errors_tr": token_issues_tr(errors), "root": root}


def on_demand_json(row: SealVerification) -> dict[str, Any]:
    return {
        "at": row.finished_at,
        "started_at": row.started_at,
        "outcome": row.outcome,
        "ok": row.outcome == "ok",
        "deep": row.deep,
        "by": row.username,
        "errors": list(row.errors),
        "warnings": list(row.warnings),
        "errors_tr": issues_tr(list(row.errors)),
        "warnings_tr": issues_tr(list(row.warnings)),
        "details": dict(row.details),
    }


def month_days(month: date) -> list[date]:
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return [first + timedelta(days=n) for n in range((following - first).days)]


# -- Kamu SM credit --------------------------------------------------------------------------------


def credit_history(layout: Layout, limit: int = CREDIT_HISTORY) -> tuple[list[dict[str, Any]], int]:
    """The last ``limit`` credit checks, oldest first, and the number of unreadable lines."""
    try:
        lines = layout.credit_log.read_bytes().splitlines()
    except FileNotFoundError:
        return [], 0
    entries: list[dict[str, Any]] = []
    bad = 0
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            bad += 1
            continue
        at = _parse_time(data.get("time")) if isinstance(data, dict) else None
        if at is None:
            bad += 1
            continue
        credits = data.get("credits")
        entries.append(
            {
                "time": at,
                "provider": data.get("provider"),
                "credits": credits if isinstance(credits, int) else None,
                "error": data.get("error"),
            }
        )
    return entries, bad
