"""Daily sealing (build specification sections 6.5-6.8 and 8.2).

For every **closed** receive day D, oldest first, without gaps in the chain:

1. wait until the day's spool files are quiet (the collector closes them after midnight);
2. for each source: ``gzip -n -9`` equivalent (no name, mtime 0) into the vault, SHA-256 of the
   compressed and the uncompressed bytes, line count, first/last receive time, gaps;
3. compress the day's ARCILOGGER audit log the same way;
4. build the manifest (RFC 8785 canonical JSON) with the previous day's manifest and token hashes,
   clock samples and the time-stamp provider;
5. time-stamp the manifest, verify the token independently, re-check every vault file against
   its hashes;
6. make the day's files read-only, delete the spool copies, record the state.

Every step can be repeated: a failed day stays ``pending`` and is retried on the configured
schedule (5, 15, 60, 180 min, then every 6 h); later days wait for it. A day stamped more than
``late_after_hours`` after its end is marked ``stamped_late`` with the reason.

Raw data is never edited: a spool file whose last line was torn by a crash or a full disk is
sealed as it is (the line counts; ``unterminated_last_line`` in the manifest).

The server clock is not trusted blindly (spec 8.2 "closed day"):

* the chain start comes from the sealing records once anything was sealed and never moves back,
  so a spool file of an old day (clock jumped backwards) is never sealed; such files — and spool
  files of sealed or future days — raise one critical ``R20-clock`` alert and are left in place.
  Spool files of days before ``seal.first_day`` that were there before the first seal (test
  messages before go-live) are not sealed either; they raise one ``info`` alert;
* a day is sealed only after its end plus ``quiet_seconds`` by the site clock (before that the
  run just waits) **and** when the time-stamp authority's own time (genTime) is after the end of
  the day; otherwise the server clock is ahead, the stamp is discarded and the day is retried
  (critical alert);
* at most ``max_consecutive_empty_days`` consecutive days without any log (no raw file, no audit
  entry) are stamped one by one; further days of such a run are ``skipped`` (one critical
  ``R20-seal-skip`` alert): the next sealed day links to the last sealed day before the run and
  lists the run in its ``gaps`` (``unsealed_empty_days``), which the verifiers follow.
* a skip is decided by the server clock alone, so it is **provisional** until a later day is
  sealed (:meth:`Sealer.review_skips`): a skipped day goes back to normal sealing when data
  appears for it, when it has not ended by the current clock, or when its run of empty days is
  no longer longer than the limit (a clock that was ahead and was corrected). Just before a
  later day is stamped the skipped days are checked once more — the manifest never lists a day
  with data as empty — and that day's time stamp (genTime after its end) proves that the
  skipped days had ended. From then on the skip is final: data that still appears for such a
  day is a stray file (``R20-clock``), kept in the spool for a person to examine.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509

from arcilogger_core import PRODUCT_NAME, __version__
from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.canonical import canonical_json
from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_bytes, atomic_write_text, fsync_dir
from arcilogger_core.immutability import is_locked, lock
from arcilogger_core.layout import AUDIT_SOURCE, SPOOL_SUFFIX, Layout
from arcilogger_core.rawlog import RawLineError, received_at
from arcilogger_core.timeutil import SITE_TZ_NAME, day_window, isoformat_site, site_day, utc_now
from arcilogger_core.tsa.base import (
    TimestampProvider,
    TokenInfo,
    TsaConfigurationError,
    TsaError,
    TsaNoCreditError,
    TsaRejectedError,
    TsaResponseError,
    TsaUnavailableError,
    VerifyResult,
)
from arcilogger_core.tsa.verify import confirmed_legal_grade, verify_token

log = logging.getLogger("arcilogger.seal")

MANIFEST_SCHEMA = "arcilogger.manifest/1"
CHUNK = 1024 * 1024
Clock = Callable[[], datetime]
#: Day states (``state/seal/<day>.json``).
SEALED = "sealed"
SKIPPED = "skipped"
DELETED = "deleted"
#: ``gaps`` entry of a manifest that stands for a run of empty days that were not stamped one
#: by one (``seal.max_consecutive_empty_days``): its ``first_day`` … ``last_day`` are the days
#: between the manifest's ``previous`` day and the manifest's own day.
UNSEALED_DAYS = "unsealed_empty_days"
#: Keys of the clock/spool alerts already raised (one alert per problem, not one per run).
ALERTS_SEEN = "alerts-raised.txt"
#: Alert rules: a sign of a server clock step (stray spool files, a clock behind the seals),
#: and a run of empty days that is not stamped day by day (or such a skip withdrawn).
CLOCK_ALERT = "R20-clock"
SKIP_ALERT = "R20-seal-skip"
#: Why a provisional skip is withdrawn (:meth:`Sealer.review_skips`).
WITHDRAW_DATA = "data"
WITHDRAW_NOT_ENDED = "not-ended"
WITHDRAW_SHORT_RUN = "short-run"
#: The Seal task could not start sealing at all: credentials, roots or client missing
#: (:func:`config_alert`). Raised at most once per site day and cause.
CONFIG_ALERT = "R20-seal-config"
#: Cause → (Turkish, English) text of :data:`CONFIG_ALERT`.
CONFIG_CAUSES: dict[str, tuple[str, str]] = {
    "zamane-jar": (
        "[tsa] zamane_jar ayarlı değil (Kamu SM Zamane istemcisi)",
        "[tsa] zamane_jar is not set (the Kamu SM Zamane client)",
    ),
    "credentials": (
        "Kamu SM müşteri numarası ya da parolası kayıtlı değil (arcilogger-core secret set "
        "kamusm.customer / kamusm.password)",
        "the Kamu SM customer number or password is not stored (arcilogger-core secret set "
        "kamusm.customer / kamusm.password)",
    ),
    "roots": (
        "[tsa] roots_dir (kamusm-roots) klasöründe sabitlenmiş kök sertifika yok",
        "no pinned root certificate in [tsa] roots_dir (kamusm-roots)",
    ),
    "secret-store": (
        "gizli depo anahtarı (secret.key) yok ya da kayıtlı bilgilerle eşleşmiyor",
        "the secret store key (secret.key) is missing or does not match the stored secrets",
    ),
    "provider": (
        "zaman damgası sağlayıcısının yapılandırması geçersiz ([tsa])",
        "the time-stamp provider configuration is invalid ([tsa])",
    ),
    "location": (
        "arcilogger.toml içindeki secrets_dir ya da data_dir kurulumun klasörü değil (olası "
        "kurcalama; dosyayı sunucuda denetleyin)",
        "secrets_dir or data_dir in arcilogger.toml is not the installation's folder (possible "
        "tampering; check the file on the server)",
    ),
}
#: Site day of the last :data:`CONFIG_ALERT` per cause, one ``<cause> <day>`` line each
#: (``state/seal``; not ``.json``: that folder's ``*.json`` files are the day states).
CONFIG_ALERTS_SEEN = "config-alerts-raised.txt"
#: How far a receive time may be ahead of the server clock before it counts as a clock step.
CLOCK_TOLERANCE = timedelta(minutes=5)
#: Bytes read from the end of a spool file to find its last receive time.
TAIL_BYTES = 64 * 1024


class SealError(RuntimeError):
    """A day cannot be sealed now; ``retryable`` decides whether the schedule tries again;
    ``critical`` raises the alert as critical at once (a clock problem, not a passing error)."""

    def __init__(self, message: str, *, retryable: bool = True, critical: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.critical = critical


def unsealed_days(manifest: dict[str, Any]) -> tuple[date, date] | None:
    """The run of empty days a manifest stands for (its ``unsealed_empty_days`` gap), if any.

    Raises ``ValueError`` for an entry that names no valid days.
    """
    for gap in manifest.get("gaps") or []:
        if isinstance(gap, dict) and gap.get("kind") == UNSEALED_DAYS:
            first = date.fromisoformat(str(gap.get("first_day")))
            last = date.fromisoformat(str(gap.get("last_day")))
            if last < first:
                raise ValueError(f"{UNSEALED_DAYS}: {last} is before {first}")
            return first, last
    return None


def config_alert(
    layout: Layout, cause: str, error: BaseException, *, clock: Clock = utc_now
) -> bool:
    """``R20-seal-config``: the Seal task stopped before sealing started (no credentials, no
    pinned roots, no Zamane client, an unusable secret store). The task runs every 5 minutes,
    so the alert is raised at most once per site day and cause; returns whether it was raised.
    The error text names what is missing, never a secret value."""
    if cause not in CONFIG_CAUSES:
        cause = "provider"
    today = site_day(clock()).isoformat()
    seen_path = layout.state / "seal" / CONFIG_ALERTS_SEEN
    seen: dict[str, str] = {}
    try:
        for line in seen_path.read_text(encoding="utf-8").splitlines():
            name, _, day = line.partition(" ")
            seen[name] = day.strip()
    except OSError:
        pass
    if seen.get(cause) == today:
        return False
    text_tr, text_en = CONFIG_CAUSES[cause]
    raise_alert(
        layout.alerts,
        CONFIG_ALERT,
        "high",
        f"Mühürleme başlatılamadı: {text_tr}. Düzeltilene kadar hiçbir gün mühürlenmez; ham "
        f"kayıtlar spool'da bekler. [Sealing cannot start: {text_en}. No day is sealed until "
        "this is fixed; the raw logs wait in the spool.]",
        {"cause": cause, "day": today, "error": str(error)[:300]},
    )
    seen[cause] = today
    atomic_write_text(seen_path, "".join(f"{name} {day}\n" for name, day in sorted(seen.items())))
    return True


def failure_code(exc: Exception) -> str:
    """Stable code of a failed attempt, stored next to the (English) ``last_error``: the web
    interface shows its own Turkish sentence for it (spec 12.4)."""
    if isinstance(exc, SealError) and exc.critical:
        return "clock_ahead"  # the only critical SealError: the stamp's time is before day end
    codes: list[tuple[type[Exception], str]] = [
        (TsaUnavailableError, "tsa_unavailable"),
        (TsaNoCreditError, "tsa_no_credit"),
        (TsaConfigurationError, "tsa_configuration"),
        (TsaRejectedError, "tsa_rejected"),
        (TsaResponseError, "tsa_rejected"),
        (SealError, "seal_error"),
    ]
    return next((code for kind, code in codes if isinstance(exc, kind)), "unknown")


@dataclass
class FileSummary:
    source: str
    path: str
    bytes: int
    bytes_uncompressed: int
    lines: int
    first_received: str | None
    last_received: str | None
    sha256_uncompressed: str
    sha256: str
    sender_ips: list[str] = field(default_factory=list)
    #: (from, to) receive times between which the source was silent too long
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    #: The file ends without ``\n``: its last line was torn by a crash or a full disk. The
    #: bytes are sealed exactly as they are; the manifest says so.
    unterminated_last_line: bool = False

    def manifest_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "source": self.source,
            "sender_ips": self.sender_ips,
            "path": self.path,
            "bytes": self.bytes,
            "bytes_uncompressed": self.bytes_uncompressed,
            "lines": self.lines,
            "first_received": self.first_received,
            "last_received": self.last_received,
            "sha256_uncompressed": self.sha256_uncompressed,
            "sha256": self.sha256,
        }
        if self.unterminated_last_line:
            entry["unterminated_last_line"] = True
        return entry


@dataclass(frozen=True)
class SealOutcome:
    day: date
    status: str  # "sealed" | "pending" | "failed" | "waiting"
    message: str = ""


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def audit_summary(name: str, data: bytes) -> tuple[str | None, int]:
    """Last entry hash and number of entries of an ARCILOGGER audit spool file.

    Refuses a damaged file instead of sealing it: an incomplete last line (a write interrupted
    by a crash or a full disk) is cut off by the API at its next audit flush, so that case is
    retried; a complete last line that is not an audit entry needs a person (not retryable).
    """
    if not data:
        return None, 0
    if not data.endswith(b"\n"):
        raise SealError(
            f"{name}: the last line is incomplete (interrupted write); the API removes it at "
            "its next audit flush",
            retryable=True,
        )
    lines = data.splitlines()
    try:
        last = json.loads(lines[-1])
    except ValueError:
        last = None
    if not isinstance(last, dict) or not isinstance(last.get("hash"), str):
        raise SealError(f"{name}: the last line is not an audit entry", retryable=False)
    return str(last["hash"]), len(lines)


def gunzip_sha256(path: Path) -> tuple[str, int]:
    """SHA-256 and line count of the uncompressed content of a ``.gz`` file.

    Lines are counted by the one rule ARCILOGGER uses everywhere (sealing, ingest, extracts,
    the standalone verifier): every ``\\n`` ends a line, and a non-empty last line without
    ``\\n`` (a write torn by a crash or a full disk) counts as a line too.
    """
    hasher = hashlib.sha256()
    lines = 0
    last = b""
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            hasher.update(chunk)
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    if last not in (b"", b"\n"):
        lines += 1
    return hasher.hexdigest(), lines


def compress_deterministic(source: Path, target: Path, level: int) -> None:
    """``gzip -n``: no file name, mtime 0 — the same input gives the same output."""
    part = target.with_name(target.name + ".part")
    with source.open("rb") as raw, part.open("wb") as out:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=level, fileobj=out, mtime=0) as gz:
            shutil.copyfileobj(raw, gz, CHUNK)
        out.flush()
        os.fsync(out.fileno())
    part.replace(target)
    fsync_dir(target.parent)


def summarize_spool(
    path: Path, gap: timedelta, window: tuple[datetime, datetime]
) -> dict[str, Any]:
    """Hash and line statistics of an uncompressed spool file, plus silence gaps.

    A last line without ``\\n`` (torn by a crash or a full disk) is counted and hashed like any
    other line and reported as ``unterminated_last_line``: the sealer never edits raw data.
    """
    hasher = hashlib.sha256()
    lines = 0
    first: datetime | None = None
    previous: datetime | None = None
    senders: set[str] = set()
    gaps: list[tuple[datetime, datetime]] = []
    unterminated = False
    with path.open("rb") as handle:
        for line in handle:
            hasher.update(line)
            lines += 1
            unterminated = not line.endswith(b"\n")
            try:
                current = received_at(line)
            except RawLineError:
                continue  # stays in the evidence unchanged; counted as a parse failure later
            sender_end = line.find(b" ", line.find(b" ") + 1)
            if sender_end > 0:
                senders.add(line[line.find(b" ") + 1 : sender_end].decode("ascii", "replace"))
            if first is None:
                first = current
                if current - window[0] > gap:
                    gaps.append((window[0], current))
            elif previous is not None and current - previous > gap:
                gaps.append((previous, current))
            previous = current
    if previous is not None and window[1] - previous > gap:
        gaps.append((previous, window[1]))
    return {
        "sha256_uncompressed": hasher.hexdigest(),
        "bytes_uncompressed": path.stat().st_size,
        "lines": lines,
        "first_received": isoformat_site(first, "milliseconds") if first else None,
        "last_received": isoformat_site(previous, "milliseconds") if previous else None,
        "sender_ips": sorted(senders),
        "gaps": gaps,
        "unterminated_last_line": unterminated,
    }


def clock_status_text() -> str | None:
    """Time service status for the manifest (w32tm on Windows, chrony elsewhere)."""
    command = ["w32tm", "/query", "/status"] if sys.platform == "win32" else ["chronyc", "tracking"]
    if shutil.which(command[0]) is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed command
            command, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.decode("utf-8", "replace")[:4000] or None


class Sealer:
    def __init__(
        self,
        config: Config,
        *,
        provider: TimestampProvider,
        roots: Sequence[x509.Certificate],
        clock: Clock = utc_now,
        clock_status: Callable[[], str | None] = clock_status_text,
    ) -> None:
        self.config = config
        self.layout = Layout(config.data_dir)
        self.provider = provider
        self.roots = list(roots)
        self._clock = clock
        self._clock_status = clock_status

    # -- state ----------------------------------------------------------------------------------

    def state(self, day: date) -> dict[str, Any]:
        path = self.layout.seal_state(day)
        if not path.exists():
            return {"day": day.isoformat(), "status": "pending", "attempts": 0}
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def _save_state(self, day: date, state: dict[str, Any]) -> None:
        atomic_write_text(
            self.layout.seal_state(day), json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )

    def is_sealed(self, day: date) -> bool:
        return self.state(day).get("status") == SEALED

    # -- which days -----------------------------------------------------------------------------

    def _day_states(self) -> dict[date, dict[str, Any]]:
        """Every ``state/seal/<YYYY-MM-DD>.json`` (other files in the folder are ignored)."""
        folder = self.layout.state / "seal"
        states: dict[date, dict[str, Any]] = {}
        if not folder.is_dir():
            return states
        for file in folder.glob("*.json"):
            try:
                day = date.fromisoformat(file.stem)
                state = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(state, dict):
                states[day] = state
        return states

    def _sealed_days(self) -> list[date]:
        return [day for day, state in self._day_states().items() if state.get("status") == SEALED]

    def _vault_days(self) -> list[date]:
        """Days with a manifest in the vault (also when their state file is gone)."""
        days = []
        for manifest in self.layout.vault.glob(
            "[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/manifest-*.json"
        ):
            try:
                days.append(date.fromisoformat(manifest.stem.removeprefix("manifest-")))
            except ValueError:
                continue
        return days

    def _anchor_day(self) -> date | None:
        try:
            anchor = json.loads(self.layout.chain_anchor.read_text(encoding="utf-8"))
            return date.fromisoformat(str(anchor["day"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def established_start(self) -> date | None:
        """First day of the chain once anything was sealed, from the sealing records only.

        It is never taken from the spool again: a spool file of an earlier day (a server clock
        that jumped backwards, a folder restored by hand) can never move the start of the chain
        back and make the sealer stamp every day in between. After retention it is the day after
        the chain anchor.
        """
        anchor = self._anchor_day()
        if anchor is not None:
            return anchor + timedelta(days=1)
        known = [
            day
            for day, state in self._day_states().items()
            if state.get("status") in (SEALED, DELETED)
        ]
        known += self._vault_days()
        return min(known) if known else None

    def chain_start(self) -> date | None:
        """Where the chain starts: the established start (``seal.first_day`` can only move it
        forward); before the first seal ``seal.first_day``, else the oldest spool day."""
        first_day = self.config.seal.first_day
        established = self.established_start()
        if established is not None:
            return max(established, first_day) if first_day is not None else established
        if first_day is not None:
            return first_day
        spool = self.layout.spool_days()
        return spool[0] if spool else None

    def closed_unsealed_days(self) -> list[date]:
        """Closed days from the chain start that still need a seal (or a skip decision)."""
        start = self.chain_start()
        if start is None:
            return []
        today = site_day(self._clock())
        states = self._day_states()
        days = []
        current = start
        while current < today:
            if states.get(current, {}).get("status") not in (SEALED, DELETED, SKIPPED):
                days.append(current)
            current += timedelta(days=1)
        return days

    # -- days without any data ------------------------------------------------------------------

    def _has_data(self, day: date) -> bool:
        """Raw spool files, an audit spool or a stamped manifest (an interrupted attempt)."""
        return bool(
            self.layout.spool_files_for(day)
            or self.layout.audit_spool(day).exists()
            or self.layout.manifest(day).exists()
            or self.layout.token(day).exists()
        )

    def _was_empty(self, day: date, states: dict[date, dict[str, Any]]) -> bool:
        """A day already decided that had nothing to seal: skipped, or sealed without files
        and without audit entries."""
        state = states.get(day) or {}
        if state.get("status") == SKIPPED:
            return True
        if state.get("status") != SEALED:
            return False
        try:
            manifest = json.loads(self.layout.manifest(day).read_bytes())
        except (OSError, ValueError):
            return False
        audit = manifest.get("audit_log") or {}
        return not manifest.get("files") and not int(audit.get("entries") or 0)

    def _empty_run(
        self, day: date, today: date, states: dict[date, dict[str, Any]] | None = None
    ) -> int:
        """Length of the run of consecutive empty days that ``day`` (itself empty) belongs to:
        the empty days already decided before it, ``day`` and the closed empty days after it
        that are not sealed (undecided or provisionally skipped; counted only as far as the
        limit needs)."""
        if states is None:
            states = self._day_states()
        limit = self.config.seal.max_consecutive_empty_days
        start = self.chain_start() or day
        before = 0
        current = day - timedelta(days=1)
        while before <= limit and current >= start and self._was_empty(current, states):
            before += 1
            current -= timedelta(days=1)
        after = 0
        current = day + timedelta(days=1)
        while (
            before + after < limit
            and current < today
            and states.get(current, {}).get("status") not in (SEALED, DELETED)
            and not self._has_data(current)
        ):
            after += 1
            current += timedelta(days=1)
        return before + 1 + after

    def stamped_through(self, states: dict[date, dict[str, Any]] | None = None) -> date | None:
        """The newest day the chain has passed: sealed or deleted, or stamped in the vault (a
        token next to its manifest — an attempt that crashed before it was recorded).

        A skipped day before it is covered by that stamp: the skip is final.
        """
        if states is None:
            states = self._day_states()
        known = [day for day, state in states.items() if state.get("status") in (SEALED, DELETED)]
        known += [day for day in self._vault_days() if self.layout.token(day).exists()]
        anchor = self._anchor_day()
        if anchor is not None:
            known.append(anchor)
        return max(known) if known else None

    def _final_skip(
        self, day: date, states: dict[date, dict[str, Any]], through: date | None
    ) -> bool:
        """``day`` is skipped and a later day's stamp covers it (it can no longer change)."""
        return (
            states.get(day, {}).get("status") == SKIPPED and through is not None and day < through
        )

    def review_skips(self) -> list[date]:
        """Withdraw the provisional skips that no longer hold; returns the days withdrawn.

        A skip is decided by the server clock alone. Until a later day is stamped (its genTime
        then proves that the skipped days had ended) a skipped day goes back to normal sealing
        when

        * a raw or audit spool file appeared for it: the clock was ahead when the day was
          skipped and the real data came later (high alert);
        * it has not ended by the current clock: the clock was ahead and was corrected (high);
        * the run of empty days it belongs to is no longer longer than the limit (info).
        """
        states = self._day_states()
        through = self.stamped_through(states)
        today = site_day(self._clock())
        limit = self.config.seal.max_consecutive_empty_days
        withdrawn = []
        for day in sorted(day for day, state in states.items() if state.get("status") == SKIPPED):
            if self._final_skip(day, states, through):
                continue
            if self._has_data(day):
                why = WITHDRAW_DATA
            elif day >= today:
                why = WITHDRAW_NOT_ENDED
            elif self._empty_run(day, today, states) <= limit:
                why = WITHDRAW_SHORT_RUN
            else:
                continue
            states[day] = self._withdraw_skip(day, why)
            withdrawn.append(day)
        return withdrawn

    def _withdraw_skip(self, day: date, why: str) -> dict[str, Any]:
        """Turn a provisional skip back into an unsealed day (it is sealed normally) and say so."""
        state = self.state(day)
        skipped_at = state.pop("skipped_at", None)
        state.pop("reason", None)
        state.pop("covered_by", None)
        now = isoformat_site(self._clock(), "seconds")
        state.update(
            status="pending",
            attempts=0,
            next_attempt_at=None,
            last_error=None,
            skip_withdrawn={"skipped_at": skipped_at, "withdrawn_at": now, "why": why},
        )
        self._save_state(day, state)
        shown = day.strftime("%d.%m.%Y")
        texts: dict[str, tuple[Severity, str]] = {
            WITHDRAW_DATA: (
                "high",
                f"{shown} günü hiç log olmadığı için boş geçilmişti, ancak şimdi o güne ait veri "
                "var: gün boş geçilirken sunucu saati ileride olabilir. Gün normal biçimde "
                "mühürlenecek; sunucu saatini kontrol edin.",
            ),
            WITHDRAW_NOT_ENDED: (
                "high",
                f"{shown} günü boş geçilmişti, ancak sunucu saatine göre henüz bitmedi: gün boş "
                "geçilirken sunucu saati ileride olmalı. Gün bittiğinde normal biçimde "
                "mühürlenecek; sunucu saatini kontrol edin.",
            ),
            WITHDRAW_SHORT_RUN: (
                "info",
                f"{shown} günü boş geçilmişti; içinde bulunduğu boş günler dizisi artık sınırdan "
                "uzun değil. Gün tek başına damgalanacak.",
            ),
        }
        severity, message = texts[why]
        self._alert_once(
            f"unskip:{why}:{day.isoformat()}",
            severity,
            message,
            {"day": day.isoformat(), "why": why},
            rule=SKIP_ALERT,
        )
        log.warning("%s: the skip is withdrawn (%s); the day is sealed normally", day, why)
        return state

    def _check_skipped_still_empty(self, day: date, skipped: Sequence[date]) -> None:
        """The skipped days this day's manifest lists as empty must still have no data.

        Otherwise the skip is withdrawn and this day waits: the manifest never calls a day with
        data empty.
        """
        found = [skipped_day for skipped_day in skipped if self._has_data(skipped_day)]
        if not found:
            return
        for skipped_day in found:
            self._withdraw_skip(skipped_day, WITHDRAW_DATA)
        raise SealError(
            "skipped as empty but now with data: "
            + ", ".join(item.isoformat() for item in found)
            + f"; they are sealed before {day}",
            retryable=True,
        )

    def _skip(self, day: date, run: int) -> SealOutcome:
        """Record that an empty day is not stamped on its own (the next manifest lists it).

        The first day of such a run raises a critical alert: no log at all for that long means
        the collector stopped, or the server clock jumped forward.
        """
        limit = self.config.seal.max_consecutive_empty_days
        state = self.state(day)
        state.update(
            status=SKIPPED,
            skipped_at=isoformat_site(self._clock(), "seconds"),
            reason=(
                f"no raw log and no audit entry; part of a run of more than {limit} consecutive "
                "empty days, which are not stamped one by one (seal.max_consecutive_empty_days)"
            ),
            next_attempt_at=None,
        )
        self._save_state(day, state)
        if self.state(day - timedelta(days=1)).get("status") != SKIPPED:
            self._alert_once(
                f"empty-run:{day.isoformat()}",
                "critical",
                f"{day.strftime('%d.%m.%Y')} gününden beri hiç log yok ({run} gün ya da daha "
                f"uzun; sınır {limit} gün): toplayıcı durmuş ya da sunucu saati ileri atlamış "
                "olabilir. Bu günler tek tek zaman damgası almayacak; bir sonraki mühürlenen "
                "günün manifestinde boşluk olarak listelenecek. Sunucu saatini ve toplayıcıyı "
                "kontrol edin.",
                {"day": day.isoformat(), "run": run, "limit": limit},
                rule=SKIP_ALERT,
            )
        log.warning("%s has no data; not stamped on its own (empty run of %d+ days)", day, run)
        return SealOutcome(
            day,
            SKIPPED,
            f"{day.strftime('%d.%m.%Y')}: veri yok; {limit} günden uzun boş dönemin parçası, "
            "tek başına damgalanmadı (sonraki manifestte listelenecek).",
        )

    # -- clock steps and stray spool files ------------------------------------------------------

    def _seen_alerts(self) -> set[str]:
        """Keys of the clock/spool/skip alerts raised before (:meth:`_alert_once`)."""
        path = self.layout.state / "seal" / ALERTS_SEEN
        try:
            return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
        except OSError:
            return set()

    def _alert_once(
        self,
        key: str,
        severity: Severity,
        message: str,
        details: dict[str, Any],
        *,
        rule: str = CLOCK_ALERT,
    ) -> bool:
        """Raise an alert unless one with ``key`` was raised before (the task runs every few
        minutes; one alert per problem is enough). Returns whether it was raised.

        Never "R20-seal": that rule is one failed sealing attempt of a day (the worker imports
        it into the audit log as such). These are signs of a clock step or a collector outage.
        """
        if key in self._seen_alerts():
            return False
        path = self.layout.state / "seal" / ALERTS_SEEN
        raise_alert(self.layout.alerts, rule, severity, message, {**details, "key": key})
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(key + "\n")
        return True

    def stray_spool_files(self) -> list[tuple[str, date, str]]:
        """Spool files the sealer must never take: ``(source, day, why)``.

        ``why`` is

        * ``before-chain``: a day before the established chain start (the sealing records) — a
          server clock that jumped backwards, or files copied in by hand;
        * ``before-first-day``: a day before ``seal.first_day`` that is not before the
          established start, or whose file was already reported as such before the first seal
          (test messages received before go-live; ``first_day`` moved forward);
        * ``sealed``: a day already sealed or deleted, or skipped with the skip covered by a
          later stamp (a provisionally skipped day with data is sealed instead,
          :meth:`review_skips`);
        * ``future``: a day after today.

        The raw data stays where it is — it is evidence — and a person decides what to do
        with it.
        """
        start = self.chain_start()
        established = self.established_start()
        today = site_day(self._clock())
        states = self._day_states()
        through = self.stamped_through(states)
        seen = self._seen_alerts()
        found = []
        for source, day in self._spool_files():
            status = states.get(day, {}).get("status")
            early = f"stray:before-first-day:{source}:{day.isoformat()}" in seen
            if established is not None and day < established and not early:
                found.append((source, day, "before-chain"))
            elif start is not None and day < start:
                found.append((source, day, "before-first-day"))
            elif status in (SEALED, DELETED) or self._final_skip(day, states, through):
                found.append((source, day, "sealed"))
            elif day > today:
                found.append((source, day, "future"))
        return found

    def _spool_files(self) -> list[tuple[str, date]]:
        """``(source, day)`` of every raw and audit spool file."""
        found: list[tuple[str, date]] = []
        if not self.layout.spool.is_dir():
            return found
        for source_dir in sorted(self.layout.spool.iterdir()):
            if not source_dir.is_dir():
                continue
            suffix = ".jsonl" if source_dir.name == AUDIT_SOURCE else SPOOL_SUFFIX
            for file in sorted(source_dir.glob(f"*{suffix}")):
                try:
                    found.append((source_dir.name, date.fromisoformat(file.name[: -len(suffix)])))
                except ValueError:
                    continue
        return found

    def check_clock_and_spool(self) -> list[str]:
        """Alerts (once each) for signs of a server clock step; returns the keys raised now.

        * a spool file the sealer must not take (:meth:`stray_spool_files`);
        * the newest sealed day is today or later: the clock is behind the one that sealed it;
        * a raw line was received later than the current time: the clock went backwards.

        A clock that jumped forward is caught when a day is stamped: the time-stamp authority's
        time (genTime) must be after the end of the day (:meth:`_check_day_closed`).
        """
        raised = []
        start = self.chain_start()
        first_day = self.config.seal.first_day
        established = self.established_start()
        texts = {
            "before-chain": "zincirin başlangıcından ("
            + (start.strftime("%d.%m.%Y") if start else "-")
            + ") önceki bir güne ait",
            "sealed": "zaten mühürlenmiş (ya da imha edilmiş / boş geçilmiş) bir güne ait",
            "future": "henüz gelmemiş bir güne ait",
        }
        for source, day, why in self.stray_spool_files():
            key = f"stray:{why}:{source}:{day.isoformat()}"
            shown = day.strftime("%d.%m.%Y")
            severity: Severity = "critical"
            if why == "before-first-day":
                first = first_day.strftime("%d.%m.%Y") if first_day else "-"
                if established is None:
                    # Before the first seal: test messages before go-live, as expected.
                    severity = "info"
                    message = (
                        f"Biriktirme klasöründe zincirin ayarlanan ilk gününden (seal.first_day = "
                        f"{first}) önceki bir güne ait bir dosya var ({source}, {shown}); "
                        "örneğin canlıya geçişten önceki deneme mesajları. Bu gün "
                        "mühürlenmeyecek; dosya silinmeyecek."
                    )
                else:
                    severity = "high"
                    message = (
                        f"Biriktirme klasöründe seal.first_day ({first}) değerinden önceki, "
                        f"henüz mühürlenmemiş bir güne ait bir dosya var ({source}, {shown}): "
                        "seal.first_day ileri alınmış. Bu gün mühürlenmeyecek; dosya "
                        "silinmeyecek. Ayarı ve dosyayı kontrol edin."
                    )
            else:
                message = (
                    f"Biriktirme klasöründe {texts[why]} bir dosya var ({source}, {shown}). "
                    "Sunucu saati atlamış olabilir. Dosya mühürlenmeyecek ve silinmeyecek; "
                    "sunucu saatini kontrol edin ve dosyayı inceleyin."
                )
            if self._alert_once(
                key,
                severity,
                message,
                {"source": source, "day": day.isoformat(), "why": why},
            ):
                raised.append(key)
        now = self._clock()
        today = site_day(now)
        sealed = self._sealed_days()
        if sealed and max(sealed) >= today:
            newest = max(sealed)
            key = f"clock-behind:sealed:{newest.isoformat()}"
            if self._alert_once(
                key,
                "critical",
                f"Sunucu saati geri gitmiş görünüyor: {newest.strftime('%d.%m.%Y')} günü "
                f"mühürlenmiş, oysa saate göre bugün {today.strftime('%d.%m.%Y')}. Saat "
                "düzelene kadar yeni bir gün mühürlenmez.",
                {"newest_sealed": newest.isoformat(), "today": today.isoformat()},
            ):
                raised.append(key)
        latest = self._latest_receive_time()
        if latest is not None and latest > now + CLOCK_TOLERANCE:
            key = f"clock-behind:spool:{site_day(latest).isoformat()}"
            if self._alert_once(
                key,
                "critical",
                "Sunucu saati geri gitmiş görünüyor: biriktirme klasöründe "
                f"{isoformat_site(latest, 'seconds')} zamanında alınmış bir satır var, şimdiki "
                f"saat {isoformat_site(now, 'seconds')}.",
                {"latest_received": latest.isoformat(), "now": now.isoformat()},
            ):
                raised.append(key)
        return raised

    def _latest_receive_time(self) -> datetime | None:
        """Receive time of the last line of the newest raw spool files (a tail read only)."""
        days = self.layout.spool_days()
        if not days:
            return None
        latest: datetime | None = None
        for _, path in self.layout.spool_files_for(days[-1]):
            try:
                with path.open("rb") as handle:
                    size = handle.seek(0, os.SEEK_END)
                    handle.seek(max(0, size - TAIL_BYTES))
                    tail = handle.read()
            except OSError:
                continue
            for line in reversed(tail.splitlines()):
                try:
                    received = received_at(line)
                except RawLineError:
                    continue
                if latest is None or received > latest:
                    latest = received
                break
        return latest

    # -- entry points ---------------------------------------------------------------------------

    def seal_due(self, requested: Mapping[date, str] | None = None) -> list[SealOutcome]:
        """Seal every closed day whose retry time has come, oldest first; stop at a failure.

        Provisional skips are reviewed first (:meth:`review_skips`), then signs of a clock
        step are alerted; they do not stop the run on their own (the chain start cannot move
        back, and a day is only sealed once the time-stamp authority confirms that it ended).
        An empty day in a run of more than ``seal.max_consecutive_empty_days`` days is skipped
        instead of stamped. A day that ended less than ``seal.quiet_seconds`` ago is not due
        yet: the run stops there with ``waiting`` (no failed attempt, no alert).

        ``requested`` maps a day to the signed seal request ("Şimdi mühürle", ADR-0014) that
        asks for it: this run does not wait for that day's next retry time, and its attempt
        records the request id (``requested``). Nothing else changes: the order, the quiet
        window, the empty-run skip and the skipped and deleted days stay as they are.
        """
        requested = requested or {}
        self.review_skips()
        self.check_clock_and_spool()
        outcomes = []
        now = self._clock()
        today = site_day(now)
        limit = self.config.seal.max_consecutive_empty_days
        quiet = timedelta(seconds=self.config.seal.quiet_seconds)
        for day in self.closed_unsealed_days():
            if not self._has_data(day):
                run = self._empty_run(day, today)
                if run > limit:
                    outcomes.append(self._skip(day, run))
                    continue
            if now < day_window(day)[1] + quiet:
                # Just after midnight: a message received before it may still be on its way.
                outcomes.append(
                    SealOutcome(day, "waiting", f"{day} ended less than {quiet.seconds} s ago")
                )
                break
            next_attempt = self.state(day).get("next_attempt_at")
            waits = next_attempt and datetime.fromisoformat(next_attempt) > now
            if waits and day not in requested:
                outcomes.append(SealOutcome(day, "waiting", f"next attempt {next_attempt}"))
                break
            outcome = self.seal_day(day, requested=requested.get(day))
            outcomes.append(outcome)
            if outcome.status != SEALED:
                break
        return outcomes

    def seal_day(self, day: date, *, requested: str | None = None) -> SealOutcome:
        """One attempt to seal ``day``; ``requested``: the id of the seal request it serves
        (recorded with the attempt, otherwise an earlier attempt's id is removed)."""
        state = self.state(day)
        if state.get("status") == SEALED:
            return SealOutcome(day, SEALED, "already sealed")
        if state.get("status") in (SKIPPED, DELETED):
            status = str(state["status"])
            return SealOutcome(day, status, f"{day} is {status}; it is not sealed on its own")
        state["attempts"] = int(state.get("attempts", 0)) + 1
        state["last_attempt_at"] = isoformat_site(self._clock(), "seconds")
        state.pop("requested", None)
        if requested is not None:
            state["requested"] = requested
        try:
            result = self._seal(day, state)
        except (SealError, TsaError, OSError) as exc:
            return self._failed(day, state, exc)
        covered = [str(item) for item in result.pop("covers_skipped", [])]
        state.update(result)
        state.update(status=SEALED, next_attempt_at=None, last_error=None, last_error_code=None)
        self._save_state(day, state)
        for skipped in covered:
            skipped_day = date.fromisoformat(skipped)
            skipped_state = self.state(skipped_day)
            skipped_state["covered_by"] = day.isoformat()
            self._save_state(skipped_day, skipped_state)
        log.info("%s sealed (serial %s)", day, result.get("serial"))
        return SealOutcome(
            day, SEALED, f"{day.strftime('%d.%m.%Y')} günü mühürlendi ve doğrulandı."
        )

    # -- the work -------------------------------------------------------------------------------

    def _seal(self, day: date, state: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        window = day_window(day)
        if day >= site_day(now):
            raise SealError(f"{day} is not closed yet", retryable=True)
        previous, skipped = self._previous_link(day)
        manifest_path = self.layout.manifest(day)
        token_path = self.layout.token(day)

        if token_path.exists() and manifest_path.exists():
            # A previous attempt got this far (crash after stamping): verify and finish.
            manifest = json.loads(manifest_path.read_bytes())
            check = verify_token(
                token_path.read_bytes(),
                roots=self.roots,
                file_path=manifest_path,
                now=self._verify_now(),
                require_critical_eku=manifest["tsa"].get("require_critical_eku") is not False,
            )
            if check.ok and check.info is not None:
                self._check_day_closed(day, check.info)
                self._verify_vault_files(day, manifest)
                result = self._finish(day, manifest, check)
                # The stamped manifest decides which skipped days it covers.
                run = unsealed_days(manifest)
                if run is not None:
                    result["covers_skipped"] = [
                        (run[0] + timedelta(days=n)).isoformat()
                        for n in range((run[1] - run[0]).days + 1)
                    ]
                return result
            token_path.unlink()

        self._check_skipped_still_empty(day, skipped)
        spool = self.layout.spool_files_for(day)
        self._wait_until_quiet([path for _, path in spool])
        if now < window[1] + timedelta(seconds=self.config.seal.quiet_seconds):
            # Closed by the site clock, but a message received just before midnight may still
            # be on its way into a file that does not exist yet.
            raise SealError(f"{day} ended less than {self.config.seal.quiet_seconds} s ago")
        gap = timedelta(minutes=self.config.seal.gap_minutes)
        vault_day = self.layout.vault_day(day)
        vault_day.mkdir(parents=True, exist_ok=True)

        files: list[FileSummary] = []
        for source, path in spool:
            stats = summarize_spool(path, gap, window)
            if stats["unterminated_last_line"]:
                log.warning("%s: the last line has no line end (torn write); sealed as it is", path)
            target = vault_day / f"{source}.log.gz"
            compress_deterministic(path, target, self.config.seal.gzip_level)
            files.append(
                FileSummary(
                    source=source,
                    path=target.relative_to(self.layout.vault).as_posix(),
                    bytes=target.stat().st_size,
                    sha256=sha256_file(target),
                    **stats,
                )
            )

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "system": {
                "name": PRODUCT_NAME,
                "version": __version__,
                "host": platform.node(),
                "site": self.config.site_name,
            },
            "day": day.isoformat(),
            "tz": SITE_TZ_NAME,
            "window": {
                "from": window[0].isoformat(),
                "to": window[1].isoformat(),
                "partition": "receive_time",
            },
            "files": [summary.manifest_entry() for summary in files],
            "audit_log": self._audit(day, vault_day),
            "gaps": self._unsealed_gap(skipped) + self._gaps(files, window),
            "clock": self._clock_summary(day),
            "tsa": self._tsa_block(),
            "previous": previous,
            "deletions": self._pending_deletions(),
            "chain_check": self._pending_chain_check(),
            "stamped_late": False,
            "created_at": isoformat_site(now, "milliseconds"),
        }
        late_after = window[1] + timedelta(hours=self.config.seal.late_after_hours)
        if now > late_after:
            manifest["stamped_late"] = True
            manifest["late_reason"] = state.get("last_error") or "sealing ran late"
        atomic_write_bytes(manifest_path, canonical_json(manifest))

        stamp = self.provider.stamp(manifest_path)
        check = verify_token(
            stamp.token_path.read_bytes(),
            roots=self.roots,
            file_path=manifest_path,
            now=self._verify_now(),
            require_critical_eku=self.config.tsa.require_critical_eku,
        )
        if not check.ok or check.info is None:
            stamp.token_path.unlink(missing_ok=True)
            raise SealError("time-stamp verification failed: " + "; ".join(check.errors))
        self._check_day_closed(day, check.info)
        try:
            # Once more after the stamp: nothing may have arrived for the days listed as empty.
            self._check_skipped_still_empty(day, skipped)
        except SealError:
            stamp.token_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise
        self._verify_vault_files(day, manifest)
        result = self._finish(day, manifest, check)
        if stamp.raw_output:
            result["tsa_output"] = stamp.raw_output[:2000]
        if skipped:
            result["covers_skipped"] = [item.isoformat() for item in skipped]
        return result

    def _verify_now(self) -> datetime:
        """What "now" is for the token's "genTime in the future" check: the later of the
        sealer's clock and the system clock (the same clock in production; an injected clock
        may run ahead in tests and simulations)."""
        return max(self._clock(), utc_now())

    def _check_day_closed(self, day: date, info: TokenInfo) -> None:
        """The time-stamp authority's own time (genTime) must be after the end of the day.

        If it is not, the server clock is ahead: the day has not really ended and messages may
        still arrive for it. The stamp and the manifest are discarded (the spool stays) and the
        day is tried again later — never sealed early.
        """
        end = day_window(day)[1]
        if info.gen_time >= end:
            return
        self.layout.token(day).unlink(missing_ok=True)
        self.layout.manifest(day).unlink(missing_ok=True)
        raise SealError(
            f"the time stamp's time ({isoformat_site(info.gen_time, 'seconds')}) is before the "
            f"end of the day ({isoformat_site(end, 'seconds')}): the server clock is ahead, the "
            "day has not ended yet; stamp discarded, the day is tried again later",
            retryable=True,
            critical=True,
        )

    def _unsealed_gap(self, skipped: Sequence[date]) -> list[dict[str, str]]:
        """The ``gaps`` entry for the empty days between the previous sealed day and this one."""
        if not skipped:
            return []
        first, last = skipped[0], skipped[-1]
        limit = self.config.seal.max_consecutive_empty_days
        return [
            {
                "source": "*",
                "kind": UNSEALED_DAYS,
                "first_day": first.isoformat(),
                "last_day": last.isoformat(),
                "from": day_window(first)[0].isoformat(),
                "to": day_window(last)[1].isoformat(),
                "reason": (
                    f"no raw log and no audit entry on these {len(skipped)} days (checked again "
                    "when this manifest was stamped); they belong to a run of more than "
                    f"{limit} consecutive days without any log, whose further days are not "
                    "stamped one by one (seal.max_consecutive_empty_days)"
                ),
            }
        ]

    def _tsa_block(self) -> dict[str, Any]:
        """The manifest's ``tsa``: provider, its claim to be the Kamu SM production server, the
        digest, and — only when relaxed — the EKU rule the site accepted (K6), sealed so that a
        verifier applies exactly that rule and nobody can relax it afterwards."""
        block: dict[str, Any] = {
            "provider": self.provider.name,
            "legal_grade": self.provider.legal_grade,
            "digest": self.config.tsa.digest,
        }
        if not self.config.tsa.require_critical_eku:
            block["require_critical_eku"] = False
        return block

    def _finish(self, day: date, manifest: dict[str, Any], check: VerifyResult) -> dict[str, Any]:
        info = check.info
        vault_day = self.layout.vault_day(day)
        for path in sorted(vault_day.iterdir()):
            if path.is_file() and not path.name.endswith(".part") and not is_locked(path):
                lock(path)
        for _, spool_path in self.layout.spool_files_for(day):
            spool_path.unlink()
        self.layout.audit_spool(day).unlink(missing_ok=True)
        self._mark_deletions_sealed(manifest.get("deletions") or [])
        self._clear_chain_check(manifest.get("chain_check"))
        return {
            "status": "sealed",
            "sealed_at": isoformat_site(self._clock(), "seconds"),
            "gen_time": info.gen_time.isoformat() if info else None,
            "serial": info.serial if info else None,
            "tsa_name": info.tsa_name if info else None,
            "tsa_cert_not_after": (
                info.tsa_cert_not_after.isoformat() if info and info.tsa_cert_not_after else None
            ),
            "provider": manifest["tsa"]["provider"],
            # What the manifest claims, and whether the verified chain confirms it.
            "legal_grade_claimed": manifest["tsa"]["legal_grade"],
            "legal_grade": confirmed_legal_grade(
                manifest["tsa"]["legal_grade"] is True,
                check,
                self.config.tsa.legal_root_fingerprints,
            ),
            "root_sha256": check.details.get("root_sha256") or None,
            "manifest_sha256": sha256_file(self.layout.manifest(day)),
            "token_sha256": sha256_file(self.layout.token(day)),
            "files": len(manifest["files"]),
            "lines": sum(int(f["lines"]) for f in manifest["files"]),
            "stamped_late": manifest.get("stamped_late", False),
        }

    def _failed(self, day: date, state: dict[str, Any], exc: Exception) -> SealOutcome:
        retryable = getattr(exc, "retryable", True)
        waits = self.config.seal.retry_minutes
        wait = waits[min(int(state["attempts"]) - 1, len(waits) - 1)]
        if not retryable:
            wait = max(wait, waits[-1])
        next_attempt = self._clock() + timedelta(minutes=wait)
        state.update(
            status="pending",
            last_error=str(exc),
            last_error_code=failure_code(exc),
            next_attempt_at=isoformat_site(next_attempt, "seconds"),
        )
        self._save_state(day, state)
        overdue = self._clock() > day_window(day)[1] + timedelta(
            hours=self.config.seal.late_after_hours
        )
        critical = overdue or not retryable or bool(getattr(exc, "critical", False))
        raise_alert(
            self.layout.alerts,
            "R20-seal",
            "critical" if critical else "high",
            f"Mühürleme başarısız ({day.strftime('%d.%m.%Y')}): {exc}. "
            f"{wait} dk sonra tekrar denenecek.",
            {"day": day.isoformat(), "attempts": state["attempts"], "retryable": retryable},
        )
        log.warning("sealing %s failed: %s", day, exc)
        return SealOutcome(day, "pending" if retryable else "failed", str(exc))

    # -- helpers --------------------------------------------------------------------------------

    def _previous_link(self, day: date) -> tuple[dict[str, str] | None, list[date]]:
        """The link to the last sealed day before ``day`` and the skipped empty days between.

        Normally that is the day before. Empty days skipped by the empty-run rule are passed
        over (oldest first in the returned list) and listed in this day's manifest instead.
        """
        start = self.chain_start()
        if start is None or day <= start:
            return None, []
        previous_day = day - timedelta(days=1)
        skipped: list[date] = []
        while previous_day >= start and self.state(previous_day).get("status") == SKIPPED:
            skipped.insert(0, previous_day)
            previous_day -= timedelta(days=1)
        if previous_day < start:
            # Only empty days before it (``seal.first_day`` on a day without data): the chain
            # starts here, and those days stay recorded as skipped before its start.
            return None, []
        manifest = self.layout.manifest(previous_day)
        token = self.layout.token(previous_day)
        if self.is_sealed(previous_day) and manifest.exists() and token.exists():
            return {
                "day": previous_day.isoformat(),
                "manifest_sha256": sha256_file(manifest),
                "token_sha256": sha256_file(token),
            }, skipped
        anchor = self._anchor_for(previous_day)
        if anchor is not None:
            return anchor, skipped
        raise SealError(f"previous day {previous_day} is not sealed yet")

    def _anchor_for(self, previous_day: date) -> dict[str, str] | None:
        if not self.layout.chain_anchor.exists():
            return None
        anchor: dict[str, str] = json.loads(self.layout.chain_anchor.read_text(encoding="utf-8"))
        return anchor if anchor.get("day") == previous_day.isoformat() else None

    def _wait_until_quiet(self, paths: list[Path]) -> None:
        """Real elapsed time since the last write (not the business clock).

        A file time slightly ahead of ``time.time()`` (seen on Windows, where the two come from
        different clocks) counts as "just written", never as a negative age.
        """
        quiet = self.config.seal.quiet_seconds
        if quiet <= 0:
            return
        for path in paths:
            if max(0.0, time.time() - path.stat().st_mtime) < quiet:
                raise SealError(f"{path.name} is still being written")

    def _audit(self, day: date, vault_day: Path) -> dict[str, Any]:
        source = self.layout.audit_spool(day)
        target = vault_day / "audit.jsonl.gz"
        if source.exists():
            data = source.read_bytes()
            last_hash, entries = audit_summary(source.name, data)
            compress_deterministic(source, target, self.config.seal.gzip_level)
            # The manifest describes exactly the bytes that were checked.
            if gunzip_sha256(target)[0] != hashlib.sha256(data).hexdigest():
                raise SealError(f"{source.name} changed while it was being sealed")
        else:
            empty = vault_day / ".audit-empty"
            empty.write_bytes(b"")
            compress_deterministic(empty, target, self.config.seal.gzip_level)
            empty.unlink()
            last_hash, entries = None, 0
        return {
            "path": target.relative_to(self.layout.vault).as_posix(),
            "sha256": sha256_file(target),
            "entries": entries,
            "last_entry_hash": last_hash,
        }

    def _gaps(
        self, files: list[FileSummary], window: tuple[datetime, datetime]
    ) -> list[dict[str, str]]:
        by_source = {summary.source: summary for summary in files}
        gaps: list[dict[str, str]] = []
        minutes = self.config.seal.gap_minutes
        for source in self.config.sources:
            if not source.expected or source.is_network:
                continue
            summary = by_source.get(source.name)
            spans = [window] if summary is None or summary.lines == 0 else summary.gaps
            reason = "no messages" if summary is None else f"no messages > {minutes} min"
            gaps += [
                {
                    "source": source.name,
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "reason": reason,
                }
                for start, end in spans
            ]
        return gaps

    def _clock_summary(self, day: date) -> dict[str, Any]:
        samples_path = self.layout.clock_samples(day)
        offsets = []
        if samples_path.exists():
            for line in samples_path.read_text(encoding="utf-8").splitlines():
                sample = json.loads(line)
                if sample.get("offset_us") is not None:
                    offsets.append(abs(int(sample["offset_us"])))
        return {
            # The time source the server follows: without a local one, the fallback.
            "ntp_server": self.config.clock.ntp_server or self.config.clock.fallback_server,
            "samples": len(offsets),
            "max_offset_us": max(offsets) if offsets else None,
            "status": self._clock_status(),
        }

    def _verify_vault_files(self, day: date, manifest: dict[str, Any]) -> None:
        for entry in manifest["files"]:
            path = self.layout.vault / entry["path"]
            if sha256_file(path) != entry["sha256"]:
                raise SealError(f"{entry['path']}: compressed hash mismatch")
            digest, lines = gunzip_sha256(path)
            if digest != entry["sha256_uncompressed"] or lines != entry["lines"]:
                raise SealError(f"{entry['path']}: content does not match the spool")
        audit = manifest["audit_log"]
        if sha256_file(self.layout.vault / audit["path"]) != audit["sha256"]:
            raise SealError("audit log hash mismatch")

    def _pending_deletions(self) -> list[dict[str, str]]:
        folder = self.layout.deletions
        if not folder.is_dir():
            return []
        entries = []
        for record in sorted(folder.glob("*-imha.json")):
            marker = record.with_suffix(".sealed")
            token = record.with_name(record.name + ".zd")
            if marker.exists():
                continue
            entries.append(
                {
                    "path": record.relative_to(self.layout.vault).as_posix(),
                    "sha256": sha256_file(record),
                    "token_sha256": sha256_file(token) if token.exists() else "",
                }
            )
        return entries

    def _mark_deletions_sealed(self, entries: list[dict[str, str]]) -> None:
        for entry in entries:
            marker = (self.layout.vault / entry["path"]).with_suffix(".sealed")
            marker.write_text("covered by a later manifest\n", encoding="utf-8")

    def _pending_chain_check(self) -> dict[str, Any] | None:
        path = self.layout.state / "verify" / "pending-chain-check.json"
        if not path.exists():
            return None
        result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return result

    def _clear_chain_check(self, entry: dict[str, Any] | None) -> None:
        if entry is not None:
            (self.layout.state / "verify" / "pending-chain-check.json").unlink(missing_ok=True)
