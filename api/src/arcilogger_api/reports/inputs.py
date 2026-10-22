"""What an official 5651 report is rendered from (spec 9.5), collected **once**.

:func:`build_inputs` gathers everything the PDF shows into one JSON object (no floats, times as
UTC ISO-8601 strings): the lookup and its request details, the stored outcome, the evidence
(each cited raw line read byte-exact from the spool or the sealed vault), the raw files with
their SHA-256 at report time, and for every cited sealed day the manifest and time-stamp facts
with a fresh verification (:func:`arcilogger_core.chain.verify_day`: token against the pinned
roots, every sealed file against the manifest, the link to the previous day). The object is
stored with the report (``lookup_reports.inputs``); :func:`arcilogger_api.reports.render.render`
turns it into the same PDF bytes every time.

Every cited raw line that could be read is **re-parsed and compared with what the report shows**
(:mod:`.consistency`): the result rests on database rows, which are derived data, while the raw
line is the evidence. A difference (``check.ok`` false on the evidence item, listed in
``consistency``) is shown as a warning in the PDF and the result is not presented as an exact
match or with its normal confidence.

Days that are not sealed yet are listed as "henüz mühürlenmedi" with the hash of the spool file
as it was when the report was made (the file keeps growing until midnight); a day whose raw files
are in the vault without manifest or token is listed as **seal missing**, never as "not sealed
yet". A cited file that exists but cannot be read now (access denied, I/O error) stops the report
(:class:`EvidenceReadError`) instead of being recorded in it for good.

**A day sealed while the report is collected** (the nightly Seal task writes the vault copy, the
manifest and the token, then deletes the spool file — it does not coordinate with the API) is
never recorded as "seal missing": the seal state of every cited day is read before and again
after its files; when it changed (or a spool file vanished mid-read) the files are collected
again with the new state. Sealing only moves forward, so a second pass settles it; when the state
keeps changing for :data:`SNAPSHOT_PASSES` passes the report is refused for now
(:class:`SealInProgressError`, nothing stored) and the next request tries again. Reading cost: each
cited file is read once (spool: hashed and scanned; vault: hashed, and decompressed up to the
last cited line), plus :func:`verify_day` hashing the sealed files of each cited day.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import reportlab  # type: ignore[import-untyped]
from cryptography import x509

from arcilogger_api import __version__
from arcilogger_api.rawlines import (
    FileScan,
    Origin,
    RawReferenceError,
    display_text,
    regular_file,
    scan_file,
    valid_source_name,
)
from arcilogger_api.reports.consistency import check_line, outcome_claims, ref_key
from arcilogger_api.reports.fonts import font_fingerprints
from arcilogger_core.chain import sealed_days, verify_day
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import site_day
from arcilogger_core.tsa import trusted_roots
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.verify import inspect_token, is_test_stamp
from arcilogger_worker.lookup import AttributionChain, EvidenceRef, LookupOutcome, RawRef

SCHEMA: Final = "arcilogger.lookup-report/1"
#: Collection passes before a report gives up on a day whose seal state keeps changing.
SNAPSHOT_PASSES: Final = 3


@dataclass(frozen=True)
class Person:
    """A user as the report names them."""

    username: str
    display_name: str

    def as_json(self) -> dict[str, str]:
        return {"username": self.username, "display_name": self.display_name}


@dataclass(frozen=True)
class TimestampPlan:
    """Whether the report's hash will be time-stamped, and by whom (known before rendering)."""

    requested: bool
    provider: str | None = None
    legal_grade: bool | None = None
    server: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "provider": self.provider,
            "legal_grade": self.legal_grade,
            "server": self.server,
        }


def evidence_key(ref: EvidenceRef) -> str:
    """Stable key of an evidence reference (the renderer finds evidence numbers with it)."""
    raw = ref.raw
    where = f"{raw.source}/{raw.day.isoformat()}/{raw.line}" if raw is not None else "-"
    return f"{ref.table}#{ref.row_id}@{where}"


def _chain_refs(chain: AttributionChain) -> Iterator[EvidenceRef]:
    for step in (chain.lease, chain.device, chain.person):
        if step is not None:
            yield from step.evidence
    for other in chain.alternatives:
        yield from _chain_refs(other)


def cited_evidence(outcome: LookupOutcome) -> list[EvidenceRef]:
    """Every evidence reference of the outcome in report order, without repetitions."""
    refs: list[EvidenceRef] = []
    for match in outcome.matches:
        refs += match.traffic.evidence
        if match.chain is not None:
            refs += _chain_refs(match.chain)
    if outcome.attribution is not None:
        refs += _chain_refs(outcome.attribution)
    seen: set[str] = set()
    unique = []
    for ref in refs:
        key = evidence_key(ref)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def _relative(layout: Layout, path: Path) -> str:
    return path.relative_to(layout.root).as_posix()


class EvidenceReadError(RuntimeError):
    """A cited file (raw file, manifest, token) exists but cannot be read **now** — access
    denied (Windows folder permissions) or an I/O error. The report is then not made at all: a
    stored report is never made again (:mod:`.store`), so it must not record a passing server
    problem as a property of the evidence."""

    def __init__(self, layout: Layout, path: Path, error: OSError) -> None:
        try:
            shown = _relative(layout, path)
        except ValueError:  # pragma: no cover - every cited path lies under the data directory
            shown = path.name
        reason = "access denied" if isinstance(error, PermissionError) else error.strerror
        super().__init__(f"{shown}: {reason or error.__class__.__name__}")
        self.file = shown
        self.access_denied = isinstance(error, PermissionError)


class SealInProgressError(RuntimeError):
    """A cited day was being sealed while the report collected its files and its state did not
    settle (module docstring): no report now, the next request tries again."""

    def __init__(self, layout: Layout, day: date) -> None:
        self.file = _relative(layout, layout.vault_day(day)) + "/"
        super().__init__(f"{self.file}: the day is being sealed")


class _SpoolGoneError(Exception):
    """A spool file disappeared between finding it and reading it: the sealer moved the day
    into the vault (collect again)."""


def _exists(layout: Layout, path: Path) -> bool:
    try:
        return regular_file(path)
    except OSError as exc:
        raise EvidenceReadError(layout, path, exc) from exc


def _seal_complete(layout: Layout, day: date) -> bool:
    """Manifest **and** time-stamp token of ``day`` are in the vault."""
    return _exists(layout, layout.manifest(day)) and _exists(layout, layout.token(day))


def _sha256(layout: Layout, path: Path) -> str:
    try:
        return sha256_file(path)
    except OSError as exc:
        raise EvidenceReadError(layout, path, exc) from exc


def _read_manifest(layout: Layout, day: date) -> dict[str, Any] | None:
    """The manifest of ``day``; ``None`` when there is none or it is not a JSON object (the
    verification of the day names the problem)."""
    path = layout.manifest(day)
    try:
        data = json.loads(path.read_bytes())
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return None
    except OSError as exc:
        raise EvidenceReadError(layout, path, exc) from exc
    return data if isinstance(data, dict) else None


def _manifest_entry(manifest: dict[str, Any], source: str) -> dict[str, Any] | None:
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and entry.get("source") == source:
            return {
                key: entry.get(key)
                for key in ("path", "sha256", "sha256_uncompressed", "lines", "bytes")
            }
    return None


def _file_facts(
    layout: Layout, source: str, day: date, lines: Sequence[int], sealed: bool
) -> tuple[dict[str, Any], FileScan | None]:
    """The raw file of ``source`` and ``day`` as it is now. ``status``:

    * ``sealed`` — the vault copy of a sealed day (manifest and token present);
    * ``unsealed`` — the spool file of a day that is not sealed yet (or being sealed right now);
    * ``seal_missing`` — the file is in the vault (the spool copy is gone) but the day's
      manifest or token is not: the seal was lost or removed — never shown as "not sealed yet";
    * ``missing`` / ``invalid`` / ``unreadable`` (damaged compressed data, over-long line).
    """
    facts: dict[str, Any] = {
        "source": source,
        "day": day.isoformat(),
        "status": "missing",
        "file": None,
        "sha256": None,
        "size": None,
        "lines": None,
        "manifest": None,
        "matches_manifest": None,
    }
    if not valid_source_name(source):
        facts["status"] = "invalid"
        return facts, None
    spool = layout.spool_file(source, day)
    vault = layout.vault_day(day) / f"{source}.log.gz"
    origin: Origin
    if sealed and _exists(layout, vault):
        origin, path = "vault", vault
    elif _exists(layout, spool):
        origin, path = "spool", spool
    elif _exists(layout, vault):
        origin, path = "vault", vault
    else:
        return facts, None
    try:
        scan = scan_file(origin, path, lines, hash_file=True)
    except FileNotFoundError as exc:
        if origin == "spool":
            raise _SpoolGoneError from exc
        raise EvidenceReadError(layout, path, exc) from exc
    except OSError as exc:
        raise EvidenceReadError(layout, path, exc) from exc
    except RawReferenceError as exc:
        facts.update(status="unreadable", file=_relative(layout, path), error=str(exc))
        return facts, None
    status = "unsealed" if origin == "spool" else ("sealed" if sealed else "seal_missing")
    facts.update(
        status=status,
        origin=origin,
        file=_relative(layout, path),
        sha256=scan.sha256,
        size=scan.size,
        lines=scan.lines,
    )
    if scan.error is not None:
        facts["error"] = scan.error
    if origin == "vault":
        manifest = _read_manifest(layout, day)
        if manifest is not None:  # without a manifest there is nothing to compare with
            entry = _manifest_entry(manifest, source)
            facts["manifest"] = entry
            facts["matches_manifest"] = entry is not None and entry.get("sha256") == scan.sha256
    return facts, scan


def _collect(
    layout: Layout, by_file: dict[tuple[str, date], list[int]], days: list[date]
) -> tuple[dict[date, bool], dict[tuple[str, date], tuple[dict[str, Any], FileScan | None]]]:
    """The seal state of every cited day and its files, read as one consistent snapshot
    (module docstring: a day sealed during the collection is read again)."""
    for _ in range(SNAPSHOT_PASSES):
        sealed = {day: _seal_complete(layout, day) for day in days}
        try:
            files = {
                key: _file_facts(layout, key[0], key[1], lines, sealed[key[1]])
                for key, lines in sorted(by_file.items())
            }
        except _SpoolGoneError:
            continue
        if all(_seal_complete(layout, day) == sealed[day] for day in days):
            return sealed, files
    changing = [day for day in days if _seal_complete(layout, day) != sealed[day]] or days
    raise SealInProgressError(layout, changing[0])


def _line_facts(raw: RawRef, facts: dict[str, Any], scan: FileScan | None) -> dict[str, Any]:
    """The cited line as read now (``status`` ``ok``, else why it could not be shown)."""
    if scan is None:
        return {"status": str(facts["status"]), "file": facts["file"]}
    found = scan.found.get(raw.line)
    if found is None:
        return {"status": "unreadable" if scan.error else "missing", "file": facts["file"]}
    offset, data = found
    result: dict[str, Any] = {
        "status": "ok",
        "file": facts["file"],
        "origin": facts.get("origin"),
        "offset": offset,
        "text": display_text(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "length": len(data),
    }
    if raw.offset is not None and raw.offset != offset:
        result["status"] = "offset_mismatch"
        result["expected_offset"] = raw.offset
    return result


def _roots(config: Config, layout: Layout) -> tuple[list[x509.Certificate], str | None]:
    try:
        return trusted_roots(config, layout), None
    except (TsaError, OSError, ValueError) as exc:
        return [], str(exc)


def chain_first_day(layout: Layout) -> date | None:
    """The first day of the chain, by the rule of ``python -m arcilogger_core verify``
    (:func:`arcilogger_core.chain.verify_range`): the oldest sealed day in the vault — or none
    once retention has left a chain anchor (the oldest remaining day links to the anchor)."""
    if layout.chain_anchor.exists():
        return None
    days = sealed_days(layout)
    return days[0] if days else None


def _day_facts(
    layout: Layout,
    day: date,
    status: str,
    *,
    roots: list[x509.Certificate],
    roots_error: str | None,
    first_day: date | None,
    today: date,
    legal_roots: Collection[str] = (),
) -> dict[str, Any]:
    """Seal facts of ``day``. ``status``: ``sealed``; ``unsealed`` (the raw files are still in
    the spool — ``late`` when the day ended more than a day before the report); ``seal_missing``
    (raw files in the vault without manifest or token: whatever is there is shown and checked)."""
    facts: dict[str, Any] = {"day": day.isoformat(), "sealed": status == "sealed", "status": status}
    if status == "unsealed":
        facts["late"] = (today - day).days >= 2
        return facts
    manifest_path, token_path = layout.manifest(day), layout.token(day)
    manifest = _read_manifest(layout, day) or {}
    found = manifest.get("tsa")
    tsa: dict[str, Any] = found if isinstance(found, dict) else {}
    facts.update(
        manifest=(
            {"file": _relative(layout, manifest_path), "sha256": _sha256(layout, manifest_path)}
            if _exists(layout, manifest_path)
            else None
        ),
        token=(
            {"file": _relative(layout, token_path), "sha256": _sha256(layout, token_path)}
            if _exists(layout, token_path)
            else None
        ),
        # "Legal grade" only from the verified chain (a root of tsa.legal_root_fingerprints),
        # never from the manifest's own claim: false until the day verifies (below). The claim
        # and a test (mock) stamp are kept apart so the report never calls a Kamu SM production
        # stamp a test stamp just because its root is not (yet) listed (render.provider_line).
        tsa={
            "provider": tsa.get("provider"),
            "legal_grade": False,
            "legal_grade_claimed": tsa.get("legal_grade") is True,
            "test_stamp": tsa.get("provider") == "mock",
        },
        stamped_late=manifest.get("stamped_late") is True,
        previous=manifest.get("previous"),
        chain_first_day=None if first_day is None else first_day.isoformat(),
    )
    info: dict[str, Any] = {"tsa_name": None, "policy": None}
    if facts["token"] is not None:
        try:
            token = inspect_token(token_path.read_bytes())
            info = {"tsa_name": token.tsa_name or None, "policy": token.policy}
            if is_test_stamp(token.policy, ""):
                facts["tsa"]["test_stamp"] = True
        except (TsaError, ValueError):
            pass
        except OSError as exc:
            raise EvidenceReadError(layout, token_path, exc) from exc
    if roots_error is not None:
        facts["verification"] = {
            "ok": False,
            "errors": [f"no trusted root certificates: {roots_error}"],
            "warnings": [],
            "serial": None,
            "gen_time": None,
            "root": None,
            "root_sha256": None,
            **info,
        }
        return facts
    try:
        check = verify_day(layout, day, roots, first_day=first_day, legal_roots=legal_roots)
    except OSError as exc:
        raise EvidenceReadError(layout, Path(exc.filename or layout.vault_day(day)), exc) from exc
    facts["tsa"]["legal_grade"] = check.legal_grade
    if is_test_stamp(info.get("policy") or "", check.root or ""):
        facts["tsa"]["test_stamp"] = True
    facts["verification"] = {
        "ok": check.ok,
        "errors": list(check.errors),
        "warnings": list(check.warnings),
        "serial": check.serial,
        "gen_time": check.gen_time,
        "root": check.root,
        "root_sha256": check.root_sha256,
        **info,
    }
    return facts


def build_inputs(
    *,
    lookup: dict[str, Any],
    outcome: LookupOutcome,
    operator: Person,
    generated_by: Person,
    generated_at: datetime,
    layout: Layout,
    config: Config,
    timestamp: TimestampPlan,
    report_file: str,
) -> dict[str, Any]:
    """Collect the facts of one report.

    ``lookup``: ``{"id", "created_at", "finished_at", "authority", "file_no", "request_date",
    "reason", "time_input"}`` (times as ISO strings).
    """
    refs = cited_evidence(outcome)
    by_file: dict[tuple[str, date], list[int]] = defaultdict(list)
    for ref in refs:
        if ref.raw is not None:
            by_file[(ref.raw.source, ref.raw.day)].append(ref.raw.line)
    days = sorted({day for _, day in by_file})
    sealed, files = _collect(layout, by_file, days)
    claims = outcome_claims(outcome)
    evidence = []
    contradicted: list[int] = []
    checked = 0
    for number, ref in enumerate(refs, start=1):
        raw = ref.raw
        check: dict[str, Any] | None = None
        if raw is None:
            line: dict[str, Any] = {"status": "no_reference"}
        else:
            facts, scan = files[(raw.source, raw.day)]
            line = _line_facts(raw, facts, scan)
            if scan is not None and line["status"] in ("ok", "offset_mismatch"):
                offset, data = scan.found[raw.line]
                check = check_line(ref, data, offset, claims.get(ref_key(ref), ()))
                if check["error"] != "not_checked":
                    checked += 1
                    if not check["ok"]:
                        contradicted.append(number)
        evidence.append(
            {
                "no": number,
                "key": evidence_key(ref),
                "table": ref.table,
                "row_id": ref.row_id,
                "time": ref.time.isoformat(),
                "summary": ref.summary,
                "raw": None if raw is None else raw.to_dict(),
                "line": line,
                "check": check,
            }
        )
    roots, roots_error = _roots(config, layout)
    first_day = chain_first_day(layout)
    today = site_day(generated_at)
    lost = {day for (_, day), (facts, _) in files.items() if facts["status"] == "seal_missing"}

    def day_status(day: date) -> str:
        if sealed[day]:
            return "sealed"
        return "seal_missing" if day in lost else "unsealed"

    return {
        "schema": SCHEMA,
        "lookup": {**lookup, "operator": operator.as_json()},
        "outcome": outcome.to_dict(),
        "evidence": evidence,
        # Cited lines re-parsed and compared with the report (:mod:`.consistency`).
        "consistency": {
            "checked": checked,
            "ok": not contradicted,
            "contradicted": contradicted,
        },
        "files": [facts for facts, _ in files.values()],
        "days": [
            _day_facts(
                layout,
                day,
                day_status(day),
                roots=roots,
                roots_error=roots_error,
                first_day=first_day,
                today=today,
                legal_roots=config.tsa.legal_root_fingerprints,
            )
            for day in days
        ],
        "generated": {
            "at": generated_at.isoformat(),
            "by": generated_by.as_json(),
            "version": __version__,
            "site_name": config.site_name,
            "report_file": report_file,
            "renderer": {"reportlab": str(reportlab.Version), "fonts": font_fingerprints()},
        },
        "timestamp": timestamp.as_json(),
    }
