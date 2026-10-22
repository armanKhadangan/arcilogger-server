"""Limits on evidence package requests (spec 13 "rate limits on exports", spec 14 disk sizing).

Packages are written to ``exports/`` on the data disk — the same volume as the raw spool, the
vault and the job state. A full data disk stops the collector (UDP syslog is then lost) and the
sealing, so ``POST /api/v1/evidence`` refuses a request **before anything is queued** when:

* the same user already waits for a package of exactly the same scope (a double click) —
  ``409 evidence_duplicate``;
* the user already has ``evidence.max_unfinished_per_user`` (2) unfinished builds, or all users
  together ``evidence.max_unfinished_total`` (5) — ``429 rate_limited``;
* the user asked for ``evidence.max_requests_per_hour`` (10) packages within the last hour —
  ``429 rate_limited``;
* the free space of the data disk after this build **and** every unfinished one would fall
  below the reserve: ``evidence.min_free_percent`` (10) % of the disk, at least
  ``evidence.min_free_gb`` (20) GiB — ``507 disk_space_low``;
* ``exports/`` would hold more than ``evidence.exports_max_gb`` (100) GiB —
  ``507 exports_quota_exceeded``.

The defaults are starting values, not measured facts: adjust them on site (settings page) to
the disk size and the expected requests. The checks and the insert of the new row run under one
transaction-level advisory lock, so two requests at the same moment cannot both pass a limit.

The size of a package is **estimated** from the sealed manifests (the bytes of every included
file, the manifests and tokens — and the days' audit logs when the package includes them
(``include_audit_log``; their manifest entry has no size, so the sealed file is measured) — plus
a fixed allowance for the verifier, guides and certificates); the sealed files are stored in the
ZIP as they are (already gzip), so the estimate is close to — and normally above — the real size.
The background builder checks the free space once more just before it starts writing
(:func:`space_refusal`).
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.errors import ApiError
from arcilogger_core.evidence.builder import PACKAGE_PREFIX
from arcilogger_core.fmt import format_bytes
from arcilogger_core.layout import Layout
from arcilogger_db.models import EvidencePackage
from arcilogger_db.settings import get_setting

#: pg_advisory_xact_lock key serialising package requests ("ARCIEVPK").
EVIDENCE_LOCK_KEY: Final = int.from_bytes(b"ARCIEVPK", "big") - 2**63
UNFINISHED: Final = ("queued", "building")
GIB: Final = 1024**3
#: Allowance for what a package holds besides the sealed files (verifier, guides, certificates,
#: CRLs, paket.json, ZIP headers): measured packages need well under 1 MiB of it.
PACKAGE_OVERHEAD: Final = 4 * 1024**2
#: Allowance per cited raw line in ``extract/`` (a raw line is at most 64 KiB, usually < 1 KiB).
EXTRACT_LINE_BYTES: Final = 4096
#: Key in ``evidence_packages.scope`` holding the size estimate of the request.
ESTIMATE_KEY: Final = "estimated_bytes"
HOUR: Final = timedelta(hours=1)

#: setting → (default, smallest allowed value)
DEFAULTS: Final[dict[str, tuple[float, float]]] = {
    "evidence.max_unfinished_per_user": (2, 1),
    "evidence.max_unfinished_total": (5, 1),
    "evidence.max_requests_per_hour": (10, 1),
    "evidence.min_free_percent": (10, 0),
    "evidence.min_free_gb": (20, 0),
    "evidence.exports_max_gb": (100, 0),
}

#: Replaceable in tests (``shutil.disk_usage``: total, used, free bytes).
disk_usage = shutil.disk_usage


def _number(session: Session, key: str) -> float:
    default, smallest = DEFAULTS[key]
    try:
        value = float(get_setting(session, key, default))
    except (TypeError, ValueError):
        return default
    if value != value or value < smallest:  # NaN or below the floor: the shipped default
        return default
    return value


@dataclass(frozen=True)
class Limits:
    max_unfinished_per_user: int
    max_unfinished_total: int
    max_requests_per_hour: int
    min_free_percent: float
    min_free_bytes: int
    exports_max_bytes: int

    @classmethod
    def load(cls, session: Session) -> Limits:
        return cls(
            max_unfinished_per_user=int(_number(session, "evidence.max_unfinished_per_user")),
            max_unfinished_total=int(_number(session, "evidence.max_unfinished_total")),
            max_requests_per_hour=int(_number(session, "evidence.max_requests_per_hour")),
            min_free_percent=_number(session, "evidence.min_free_percent"),
            min_free_bytes=int(_number(session, "evidence.min_free_gb") * GIB),
            exports_max_bytes=int(_number(session, "evidence.exports_max_gb") * GIB),
        )

    def reserve(self, total: int) -> int:
        """Bytes that must stay free on a disk of ``total`` bytes."""
        return max(int(total * self.min_free_percent / 100), self.min_free_bytes)


# -- sizes ----------------------------------------------------------------------------------------


def estimate_bytes(
    layout: Layout,
    days: Sequence[date],
    sources: Sequence[str] | None,
    extract_lines: int,
    *,
    audit_log: bool = False,
) -> int:
    """Expected size of a package (see the module text); never below the real size in practice.
    ``audit_log``: the days' ARCILOGGER audit log goes in too (``include_audit_log``)."""
    wanted = None if sources is None else set(sources)
    total = PACKAGE_OVERHEAD + extract_lines * EXTRACT_LINE_BYTES
    for day in days:
        manifest_path, token_path = layout.manifest(day), layout.token(day)
        try:
            total += manifest_path.stat().st_size + token_path.stat().st_size
            manifest = json.loads(manifest_path.read_bytes())
        except (OSError, ValueError):
            continue  # the build refuses such a day with its own message
        if not isinstance(manifest, dict):
            continue
        entries = [
            entry
            for entry in manifest.get("files") or []
            if isinstance(entry, dict) and (wanted is None or entry.get("source") in wanted)
        ]
        for entry in entries:
            size = entry.get("bytes")
            if isinstance(size, int) and size > 0:
                total += size
        audit = manifest.get("audit_log")
        if audit_log and isinstance(audit, dict):
            total += _sealed_size(layout, audit.get("path"))
    return total


def _sealed_size(layout: Layout, relative: Any) -> int:
    """Size of a sealed vault file named by a manifest entry (0 when it cannot be read: the build
    then refuses the day with its own message)."""
    if not isinstance(relative, str) or not relative or ".." in relative.split("/"):
        return 0
    try:
        return (layout.vault / relative).stat().st_size
    except OSError:
        return 0


def _existing(path: Path) -> Path:
    """``path`` or its nearest existing parent (``exports/`` is created by the first build)."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path  # pragma: no cover - the file system root always exists


def exports_bytes(layout: Layout) -> int:
    """Bytes of the packages (and unfinished ``.part`` files) in ``exports/``."""
    folder = layout.exports
    if not folder.is_dir():
        return 0
    total = 0
    for path in folder.glob(f"{PACKAGE_PREFIX}*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:  # pragma: no cover - deleted meanwhile
            continue
    return total


def _size(value: int) -> tuple[str, str]:
    """``value`` bytes as the page shows them, in Turkish and in English."""
    return format_bytes(max(value, 0), "tr"), format_bytes(max(value, 0), "en")


def space_refusal(
    layout: Layout, limits: Limits, *, estimate: int, pending: int = 0
) -> ApiError | None:
    """``507`` when the data disk or ``exports/`` cannot take ``estimate`` more bytes.

    ``pending``: the estimates of the builds already waiting (their files do not exist yet).
    """
    usage = disk_usage(_existing(layout.exports))
    reserve = limits.reserve(usage.total)
    after = usage.free - pending - estimate
    if after < reserve:
        need_tr, need_en = _size(estimate)
        free_tr, free_en = _size(max(usage.free - pending, 0))
        reserve_tr, reserve_en = _size(reserve)
        return ApiError(
            507,
            "disk_space_low",
            f"Veri diskinde yer yetersiz: paket yaklaşık {need_tr} tutacak, kullanılabilir "
            f"{free_tr}; ham kayıtlar ve mühürleme için en az {reserve_tr} boş kalmalıdır. Daha "
            "az gün ya da kaynak seçin veya eski paketlerin silinmesini bekleyin.",
            f"Not enough space on the data disk: the package needs about {need_en}, "
            f"{free_en} is available, and at least {reserve_en} must stay free for the raw "
            "logs and the sealing. Choose fewer days or sources, or wait until old packages "
            "are deleted.",
            details={
                "estimated_bytes": estimate,
                "free_bytes": usage.free,
                "pending_bytes": pending,
                "reserve_bytes": reserve,
            },
        )
    used = exports_bytes(layout)
    if used + pending + estimate > limits.exports_max_bytes:
        limit_tr, limit_en = _size(limits.exports_max_bytes)
        return ApiError(
            507,
            "exports_quota_exceeded",
            f"Kanıt paketleri klasörünün sınırı ({limit_tr}) aşılacak; eski paketlerin süresinin "
            "dolmasını bekleyin ya da daha küçük bir kapsam seçin.",
            f"The evidence package folder would exceed its limit ({limit_en}); wait until old "
            "packages expire or choose a smaller scope.",
            details={
                "estimated_bytes": estimate,
                "exports_bytes": used,
                "pending_bytes": pending,
                "limit_bytes": limits.exports_max_bytes,
            },
        )
    return None


# -- the request checks ---------------------------------------------------------------------------


def _comparable(scope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in scope.items() if key != ESTIMATE_KEY}


def _estimate_of(scope: Any) -> int:
    value = scope.get(ESTIMATE_KEY) if isinstance(scope, dict) else None
    return value if isinstance(value, int) and value > 0 else 0


def _retry_after(seconds: float) -> dict[str, str]:
    return {"Retry-After": str(max(1, math.ceil(seconds)))}


def check_request(
    session: Session,
    layout: Layout,
    *,
    user_id: int,
    scope: dict[str, Any],
    password_protected: bool,
    estimate: int,
    now: datetime,
) -> None:
    """Refuse a package request over a limit (see the module text); call under :func:`lock`."""
    limits = Limits.load(session)
    unfinished = session.execute(
        select(
            EvidencePackage.id,
            EvidencePackage.user_id,
            EvidencePackage.scope,
            EvidencePackage.password_protected,
        ).where(EvidencePackage.status.in_(UNFINISHED))
    ).all()
    wanted = _comparable(scope)
    for package_id, owner, other_scope, protected in unfinished:
        if (
            owner == user_id
            and protected == password_protected
            and _comparable(dict(other_scope or {})) == wanted
        ):
            raise ApiError(
                409,
                "evidence_duplicate",
                f"Aynı kapsamdaki paket (#{package_id}) zaten hazırlanıyor; onu bekleyin.",
                f"A package of the same scope (#{package_id}) is already being built; wait for it.",
                details={"package_id": package_id},
            )
    mine = sum(1 for _, owner, _, _ in unfinished if owner == user_id)
    if mine >= limits.max_unfinished_per_user or len(unfinished) >= limits.max_unfinished_total:
        per_user = mine >= limits.max_unfinished_per_user
        raise ApiError(
            429,
            "rate_limited",
            (
                f"Hazırlanmakta olan {mine} paketiniz var (en fazla "
                f"{limits.max_unfinished_per_user}); biri bitince tekrar deneyin."
                if per_user
                else f"Şu anda {len(unfinished)} paket hazırlanıyor (en fazla "
                f"{limits.max_unfinished_total}); biraz sonra tekrar deneyin."
            ),
            (
                f"You already have {mine} packages being built (at most "
                f"{limits.max_unfinished_per_user}); try again when one has finished."
                if per_user
                else f"{len(unfinished)} packages are being built right now (at most "
                f"{limits.max_unfinished_total}); try again shortly."
            ),
            headers=_retry_after(60),
            details={
                "reason": "unfinished_per_user" if per_user else "unfinished_total",
                "unfinished": mine if per_user else len(unfinished),
                "limit": (
                    limits.max_unfinished_per_user if per_user else limits.max_unfinished_total
                ),
            },
        )
    count, oldest = session.execute(
        select(func.count(), func.min(EvidencePackage.created_at)).where(
            EvidencePackage.user_id == user_id, EvidencePackage.created_at > now - HOUR
        )
    ).one()
    if count >= limits.max_requests_per_hour:
        raise ApiError(
            429,
            "rate_limited",
            f"Son bir saatte {count} paket istediniz (en fazla "
            f"{limits.max_requests_per_hour}); biraz sonra tekrar deneyin.",
            f"You asked for {count} packages within the last hour (at most "
            f"{limits.max_requests_per_hour}); try again later.",
            headers=_retry_after((oldest + HOUR - now).total_seconds()),
            details={
                "reason": "requests_per_hour",
                "requests": count,
                "limit": limits.max_requests_per_hour,
            },
        )
    pending = sum(_estimate_of(other) for _, _, other, _ in unfinished)
    refusal = space_refusal(layout, limits, estimate=estimate, pending=pending)
    if refusal is not None:
        raise refusal


def lock(session: Session) -> None:
    """Serialise package requests until the end of the transaction."""
    session.execute(select(func.pg_advisory_xact_lock(EVIDENCE_LOCK_KEY)))
