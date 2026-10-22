"""Trusted time: the one answer to "what time is it" for licence decisions (contract 3.3).

::

    trusted.now = max(system clock,
                      newest verified TSA genTime of the sealed days,
                      end of the newest vault day by its path name,
                      start of RELEASE_DATE (00:00 site time),
                      issued_at of the verified active licence)   # added by state.evaluate

Every floor can only raise ``now``, so setting the server clock back extends nothing: the
newest sealed day and its time stamp (made by the time-stamp authority with its own clock) stay
the floor. The vault is only read: nothing here writes a file, constructs the mock TSA or calls
:func:`arcilogger_core.tsa.trusted_roots` (the API's service account may not write ``state``,
ADR-0007). ``state/seal/<day>.json`` and stored verification results are never used: they are
writable copies, not signed by the time-stamp authority.

:func:`file_lower_bounds` gives the trial's lower bounds kept in the data folder (oldest vault
day, chain anchor, oldest spool day, contract 3.4).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal

from cryptography import x509

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.license._constants import PRODUCT_EPOCH
from arcilogger_core.license._constants import release_date as _release_date
from arcilogger_core.timeutil import day_window, to_utc, utc_now
from arcilogger_core.tsa.verify import fingerprint as cert_fingerprint
from arcilogger_core.tsa.verify import load_certificates, load_roots_dir, verify_token

log = logging.getLogger("arcilogger.license")

TimeSource = Literal["system", "tsa", "vault_day", "release_date", "license_issued"]

#: The newest vault days whose time stamps are checked for the TSA floor.
TSA_SCAN_DAYS: Final = 3
#: ``clock_behind``: a floor more than this after the system clock.
CLOCK_BEHIND_AFTER: Final = timedelta(minutes=5)
#: Per-process caches: token verification results, the list of vault days.
TOKEN_CACHE_SECONDS: Final = 600.0
VAULT_DAYS_CACHE_SECONDS: Final = 60.0
#: Same glob as ``Sealer._vault_days``.
VAULT_MANIFEST_GLOB: Final = "[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/manifest-*.json"
MOCK_ROOT: Final = ("mock-tsa", "root.pem")

#: "now" for the token's "genTime in the future" check: a genTime after the system clock is
#: exactly what shows that the clock was set back, so it must not make the token fail here.
_FAR_FUTURE: Final = datetime(9000, 1, 1, tzinfo=UTC)


def site_day_start(day: date) -> datetime:
    """00:00 Europe/Istanbul of ``day`` (aware UTC)."""
    return day_window(day)[0].astimezone(UTC)


def site_day_end(day: date) -> datetime:
    """00:00 Europe/Istanbul of the next day (aware UTC): the end of ``day``."""
    return day_window(day)[1].astimezone(UTC)


@dataclass(frozen=True)
class TrustedTime:
    now: datetime  # max of the floors present (3.3)
    system: datetime
    tsa: datetime | None  # newest verified genTime
    tsa_day: date | None
    vault_day_end: datetime | None  # end of the newest vault day by path name
    release_floor: datetime  # 00:00 site time of RELEASE_DATE
    license_issued: datetime | None  # set by with_floor in evaluate

    def _floors(self) -> tuple[tuple[TimeSource, datetime | None], ...]:
        return (
            ("system", self.system),
            ("tsa", self.tsa),
            ("vault_day", self.vault_day_end),
            ("release_date", self.release_floor),
            ("license_issued", self.license_issued),
        )

    @property
    def source(self) -> TimeSource:
        """The floor that won (ties: the earlier name of :data:`TimeSource`)."""
        for name, value in self._floors():
            if value is not None and value == self.now:
                return name
        return "system"

    @property
    def clock_behind(self) -> bool:
        """Some non-system floor is more than 5 minutes after the system clock."""
        return any(
            value is not None and value - self.system > CLOCK_BEHIND_AFTER
            for name, value in self._floors()
            if name != "system"
        )

    def with_floor(self, at: datetime, source: Literal["license_issued"]) -> TrustedTime:
        """This time raised to ``at`` (the verified active licence's ``issued_at``)."""
        if source != "license_issued":
            raise ValueError(f"unknown floor {source!r}")
        moment = to_utc(at)
        issued = moment if self.license_issued is None else max(self.license_issued, moment)
        return replace(self, now=max(self.now, moment), license_issued=issued)


# -- caches -------------------------------------------------------------------------------------

_lock = threading.Lock()
_monotonic = time.monotonic  # the caches' clock (tests replace it)
_vault_days_cache: dict[str, tuple[float, tuple[date, ...]]] = {}
_token_cache: dict[tuple[object, ...], tuple[float, datetime | None]] = {}
#: Spool folders this process may not list (each warned about once).
_spool_denied_warned: set[str] = set()


def clear_cache() -> None:
    """Forget the cached vault day lists, token results and spool warnings (tests)."""
    with _lock:
        _vault_days_cache.clear()
        _token_cache.clear()
        _spool_denied_warned.clear()


def _day_end_or_none(day: date) -> datetime | None:
    """:func:`site_day_end`, or None for a name that has no end (``9999-12-31`` overflows)."""
    try:
        return site_day_end(day)
    except (OverflowError, ValueError):
        return None


def _scan_vault_days(layout: Layout) -> tuple[date, ...]:
    days: set[date] = set()
    if layout.vault.is_dir():
        for manifest in layout.vault.glob(VAULT_MANIFEST_GLOB):
            try:
                day = date.fromisoformat(manifest.stem.removeprefix("manifest-"))
            except ValueError:
                continue
            if _day_end_or_none(day) is not None:  # no site day: not a name the sealer writes
                days.add(day)
    return tuple(sorted(days))


def vault_days(layout: Layout) -> tuple[date, ...]:
    """Days with a manifest in the vault by path name, oldest first (cached 60 s)."""
    key = str(layout.vault)
    with _lock:
        hit = _vault_days_cache.get(key)
        if hit is not None and hit[0] > _monotonic():
            return hit[1]
    days = _scan_vault_days(layout)
    with _lock:
        _vault_days_cache[key] = (_monotonic() + VAULT_DAYS_CACHE_SECONDS, days)
    return days


# -- the TSA floor ------------------------------------------------------------------------------


def read_only_roots(config: Config | None, layout: Layout) -> list[x509.Certificate]:
    """The pinned roots a token may chain to, read-only (the set of ``api/sealcheck.read_roots``).

    ``tsa.roots_dir`` when it is a directory, plus the mock TSA's root file only while
    ``tsa.provider`` is ``mock`` and the file exists. Writes nothing.
    """
    roots: list[x509.Certificate] = []
    if config is None:
        return roots
    if config.tsa.roots_dir is not None and config.tsa.roots_dir.is_dir():
        roots += load_roots_dir(config.tsa.roots_dir)
    mock_root = layout.state.joinpath(*MOCK_ROOT)
    if config.tsa.provider == "mock" and mock_root.is_file():
        roots += load_certificates([mock_root])
    return roots


def _verified_gen_time(
    layout: Layout, day: date, roots: Sequence[x509.Certificate], pins: tuple[str, ...]
) -> datetime | None:
    token_path = layout.token(day)
    manifest_path = layout.manifest(day)
    token_stat = token_path.stat()
    manifest_stat = manifest_path.stat()
    key = (
        str(manifest_path),
        token_stat.st_mtime_ns,
        token_stat.st_size,
        manifest_stat.st_mtime_ns,
        manifest_stat.st_size,
        pins,
    )
    with _lock:
        hit = _token_cache.get(key)
        if hit is not None and hit[0] > _monotonic():
            return hit[1]
    manifest = json.loads(manifest_path.read_bytes())
    block = manifest.get("tsa") if isinstance(manifest, dict) else None
    relaxed = isinstance(block, dict) and block.get("require_critical_eku") is False
    result = verify_token(
        token_path.read_bytes(),
        roots=roots,
        file_path=manifest_path,
        now=_FAR_FUTURE,
        require_critical_eku=not relaxed,
    )
    gen_time = to_utc(result.info.gen_time) if result.ok and result.info is not None else None
    with _lock:
        moment = _monotonic()
        for stale in [k for k, (expires, _) in _token_cache.items() if expires <= moment]:
            del _token_cache[stale]
        _token_cache[key] = (moment + TOKEN_CACHE_SECONDS, gen_time)
    return gen_time


def _tsa_floor(
    layout: Layout, config: Config | None, days: Sequence[date]
) -> tuple[datetime | None, date | None]:
    try:
        roots = read_only_roots(config, layout)
        pins = tuple(sorted(cert_fingerprint(root) for root in roots))
    except Exception:
        return None, None
    if not roots:
        return None, None
    for day in reversed(days[-TSA_SCAN_DAYS:]):
        try:
            gen_time = _verified_gen_time(layout, day, roots, pins)
        except Exception:
            gen_time = None
        if gen_time is not None:
            return gen_time, day
    return None, None


def trusted_now(
    layout: Layout,
    config: Config | None,
    *,
    clock: Callable[[], datetime] = utc_now,
    release_date: date | None = None,
) -> TrustedTime:
    """The trusted time (3.3); never raises for vault, root or token problems.

    ``release_date``: only tests and the sample capture pass it (default: ``RELEASE_DATE``).
    """
    system = to_utc(clock())
    release_floor = site_day_start(release_date if release_date is not None else _release_date())
    try:
        days = vault_days(layout)
        vault_day_end = site_day_end(days[-1]) if days else None
    except Exception:
        days, vault_day_end = (), None
    tsa, tsa_day = _tsa_floor(layout, config, days)
    floors = [value for value in (system, tsa, vault_day_end, release_floor) if value is not None]
    return TrustedTime(
        now=max(floors),
        system=system,
        tsa=tsa,
        tsa_day=tsa_day,
        vault_day_end=vault_day_end,
        release_floor=release_floor,
        license_issued=None,
    )


# -- trial lower bounds in the data folder --------------------------------------------------------


def _anchor_day(layout: Layout) -> date | None:
    try:
        anchor = json.loads(layout.chain_anchor.read_text(encoding="utf-8"))
        return date.fromisoformat(str(anchor["day"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _oldest_day_end(days: Iterable[date]) -> datetime | None:
    """The end of the oldest of ``days`` that ends at or after ``PRODUCT_EPOCH``.

    ``evaluate`` ignores bounds before the epoch (3.4). Taking the oldest name and letting
    ``evaluate`` drop it would let one planted, correctly named file from before the product
    existed (``vault/2025/01/01/manifest-2025-01-01.json``, ``spool/<source>/2025-01-01.jsonl``)
    hide every real older day and so reopen the trial on a fresh database with the old data
    folder. Names without a site-day end (``9999-12-31``) are skipped too.
    """
    for day in sorted(days):
        end = _day_end_or_none(day)
        if end is not None and end >= PRODUCT_EPOCH:
            return end
    return None


def _spool_bound(layout: Layout) -> datetime | None:
    try:
        return _oldest_day_end(layout.spool_days())
    except PermissionError as exc:
        # Every process that evaluates the licence must be able to list the spool (ADR-0007);
        # without that right it would decide without this bound, unlike the others (5.2).
        key = str(layout.spool)
        with _lock:
            first = key not in _spool_denied_warned
            _spool_denied_warned.add(key)
        if first:
            log.warning(
                "the spool folder %s cannot be listed (%s): this process evaluates the licence "
                "without the trial's spool lower bound; check the folder rights (ADR-0007)",
                key,
                exc,
            )
    except Exception:  # noqa: S110 - a bound that cannot be read is absent
        pass
    return None


def file_lower_bounds(layout: Layout) -> dict[str, datetime]:
    """{"vault": ..., "spool": ...} as in 3.4 (missing keys when absent); never raises.

    Only days that end at or after ``PRODUCT_EPOCH`` are candidates, so a name from before the
    product cannot stand in for the oldest real day (:func:`_oldest_day_end`).
    """
    bounds: dict[str, datetime] = {}
    try:
        days = list(vault_days(layout))
        anchor = _anchor_day(layout)
        if anchor is not None:
            days.append(anchor)
        vault = _oldest_day_end(days)
        if vault is not None:
            bounds["vault"] = vault
    except Exception:  # noqa: S110 - a bound that cannot be read is absent
        pass
    spool = _spool_bound(layout)
    if spool is not None:
        bounds["spool"] = spool
    return bounds
