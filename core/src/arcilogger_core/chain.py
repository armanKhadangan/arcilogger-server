"""Verification of sealed days and of the chain between them (build specification 6.7).

For each day: the manifest is canonical JSON, its time-stamp token verifies against the pinned
roots, every vault file matches its SHA-256 (``deep`` also decompresses and checks the
uncompressed hash and line count), and ``previous`` matches the hashes of the previous day's
manifest and token — or the chain anchor left by the retention job. The previous day is the day
before, or — when the manifest lists a run of empty days that were not stamped one by one
(``unsealed_empty_days`` in its ``gaps``) — the day before that run.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from cryptography import x509

from arcilogger_core.canonical import is_canonical
from arcilogger_core.immutability import is_locked
from arcilogger_core.layout import Layout
from arcilogger_core.seal import gunzip_sha256, sha256_file, unsealed_days
from arcilogger_core.tsa.verify import confirmed_legal_grade, is_test_stamp, verify_token


@dataclass
class DayCheck:
    day: date
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    serial: str | None = None
    gen_time: str | None = None
    root: str | None = None
    #: SHA-256 fingerprint of the root the verified token chains to.
    root_sha256: str | None = None
    #: The manifest says the day was stamped by the Kamu SM production server …
    legal_grade_claimed: bool = False
    #: … and the verified chain ends at a root of ``tsa.legal_root_fingerprints``.
    legal_grade: bool = False
    #: Empty days this day's manifest stands for (not stamped one by one, see
    #: ``seal.max_consecutive_empty_days``).
    skipped: list[date] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "day": self.day.isoformat(),
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "serial": self.serial,
            "gen_time": self.gen_time,
            "root": self.root,
            "root_sha256": self.root_sha256,
            "legal_grade": self.legal_grade,
        }
        if self.skipped:
            result["skipped"] = [day.isoformat() for day in self.skipped]
        return result


def sealed_days(layout: Layout) -> list[date]:
    """Days that have a manifest in the vault, oldest first."""
    days = []
    for manifest in layout.vault.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/manifest-*.json"):
        try:
            days.append(date.fromisoformat(manifest.stem.removeprefix("manifest-")))
        except ValueError:
            continue
    return sorted(days)


def _link_target(layout: Layout, previous_day: date) -> dict[str, str] | None:
    manifest, token = layout.manifest(previous_day), layout.token(previous_day)
    if manifest.exists() and token.exists():
        return {
            "day": previous_day.isoformat(),
            "manifest_sha256": sha256_file(manifest),
            "token_sha256": sha256_file(token),
        }
    if layout.chain_anchor.exists():
        anchor: dict[str, str] = json.loads(layout.chain_anchor.read_text(encoding="utf-8"))
        if anchor.get("day") == previous_day.isoformat():
            return anchor
    return None


def verify_day(
    layout: Layout,
    day: date,
    roots: Sequence[x509.Certificate],
    *,
    deep: bool = False,
    first_day: date | None = None,
    legal_roots: Collection[str] = (),
    now: datetime | None = None,
) -> DayCheck:
    """Verify one sealed day (see the module docstring).

    ``legal_roots`` (``tsa.legal_root_fingerprints``) decides ``legal_grade``: the manifest's
    own claim counts only when the verified token chains to one of those roots. A claim that
    the verified chain contradicts (a test TSA's root or policy) is an error. ``now`` is the
    time a token's genTime may not be after (default: the system clock).
    """
    check = DayCheck(day)
    manifest_path, token_path = layout.manifest(day), layout.token(day)
    if not manifest_path.exists():
        check.errors.append("manifest missing")
        return check
    data = manifest_path.read_bytes()
    if not is_canonical(data):
        check.errors.append("manifest is not canonical JSON (modified?)")
    try:
        manifest = json.loads(data)
    except json.JSONDecodeError:
        check.errors.append("manifest is not JSON")
        return check
    if manifest.get("day") != day.isoformat():
        check.errors.append(f"manifest is for {manifest.get('day')}")

    found = manifest.get("tsa")
    tsa: dict[str, Any] = found if isinstance(found, dict) else {}
    check.legal_grade_claimed = tsa.get("legal_grade") is True
    if not token_path.exists():
        check.errors.append("time-stamp token missing")
    else:
        result = verify_token(
            token_path.read_bytes(),
            roots=roots,
            file_path=manifest_path,
            now=now,
            # The rule the site sealed into the manifest (tsa.require_critical_eku, K6).
            require_critical_eku=tsa.get("require_critical_eku") is not False,
        )
        check.errors += [f"token: {error}" for error in result.errors]
        check.warnings += [f"token: {warning}" for warning in result.warnings]
        if result.info is not None:
            check.serial = result.info.serial
            check.gen_time = result.info.gen_time.isoformat()
        check.root = result.details.get("root") or None
        check.root_sha256 = result.details.get("root_sha256") or None
        check.legal_grade = confirmed_legal_grade(check.legal_grade_claimed, result, legal_roots)
        if check.legal_grade_claimed and result.ok and result.info is not None:
            if is_test_stamp(result.info.policy, check.root or ""):
                check.errors.append(
                    "legal-grade claim, but the stamp is from the test TSA (mock root/policy)"
                )
            elif legal_roots and not check.legal_grade:
                check.warnings.append(
                    "legal-grade claim not confirmed: the root is not in "
                    "tsa.legal_root_fingerprints"
                )

    entries = [*manifest.get("files", []), manifest.get("audit_log") or {}]
    for entry in entries:
        if not entry:
            continue
        path = layout.vault / entry["path"]
        if not path.exists():
            check.errors.append(f"{entry['path']}: missing")
            continue
        if sha256_file(path) != entry["sha256"]:
            check.errors.append(f"{entry['path']}: SHA-256 mismatch (file changed)")
        elif deep and "sha256_uncompressed" in entry:
            digest, lines = gunzip_sha256(path)
            if digest != entry["sha256_uncompressed"] or lines != entry["lines"]:
                check.errors.append(f"{entry['path']}: content differs from the manifest")
        if not is_locked(path):
            check.warnings.append(f"{entry['path']}: not read-only")

    previous = manifest.get("previous")
    try:
        skipped = unsealed_days(manifest)
    except ValueError as exc:
        check.errors.append(f"gaps: {exc}")
        skipped = None
    if skipped is not None:
        first, last = skipped
        check.skipped = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        check.warnings.append(
            f"{len(check.skipped)} empty day(s) {first} … {last} were not stamped one by one "
            "(no log; listed in this manifest)"
        )
        if last != day - timedelta(days=1):
            check.errors.append("chain broken: the skipped days do not end the day before")
    expected = (skipped[0] if skipped else day) - timedelta(days=1)
    if previous is None:
        if first_day is not None and day != first_day:
            check.errors.append("chain broken: no link to the previous day")
    else:
        target = _link_target(layout, date.fromisoformat(previous["day"]))
        if target is None:
            check.errors.append(f"chain broken: day {previous['day']} is missing")
        elif (
            target["manifest_sha256"] != previous["manifest_sha256"]
            or target["token_sha256"] != previous["token_sha256"]
        ):
            check.errors.append(f"chain broken: day {previous['day']} was changed")
        if previous["day"] != expected.isoformat():
            check.errors.append("chain broken: link does not point to the day before")
    return check


def verify_range(
    layout: Layout,
    days: Sequence[date],
    roots: Sequence[x509.Certificate],
    *,
    deep: bool = False,
    legal_roots: Collection[str] = (),
    now: datetime | None = None,
) -> list[DayCheck]:
    """Verify ``days`` and that no day between the first and last one is missing."""
    all_sealed = sealed_days(layout)
    first_day = all_sealed[0] if all_sealed else None
    if layout.chain_anchor.exists():
        first_day = None  # the oldest remaining day links to the anchor
    checks = [
        verify_day(
            layout,
            day,
            roots,
            deep=deep,
            first_day=first_day,
            legal_roots=legal_roots,
            now=now,
        )
        for day in days
    ]
    if days:
        expected = {days[0] + timedelta(days=n) for n in range((days[-1] - days[0]).days + 1)}
        # Empty days a later manifest stands for are accounted for, not missing.
        covered = {skipped for check in checks for skipped in check.skipped}
        for missing in sorted(expected - set(days) - covered):
            checks.append(DayCheck(missing, errors=["day missing from the chain"]))
    return sorted(checks, key=lambda c: c.day)
