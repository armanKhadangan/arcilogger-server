"""The worker job ``snmp.poll`` (Wave 5 contract §6, N2 and N4).

Every :data:`SNMP_POLL_EVERY` it polls each SNMP-enabled source whose
``interval_seconds`` is due, all of them at once, writes ``state/wan-rates.json`` and opens or
resolves ``R20-snmp:<source>`` (low): a source with an interface that has had no good read for
:data:`ALERT_AFTER` is reported, and the next good read of every interface resolves it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.snmp import PysnmpClient, SnmpClient
from arcilogger_core.timeutil import isoformat_site
from arcilogger_worker.alerts.store import active_alerts, open_alert, resolve
from arcilogger_worker.alerts.texts import SNMP_RULE, SNMP_STATUSES
from arcilogger_worker.snmp.poller import fail, poll_source
from arcilogger_worker.snmp.settings import (
    SECRET_MISSING,
    SnmpSource,
    secret_store,
    snmp_sources,
    target,
)
from arcilogger_worker.snmp.state import SourceState, read_state, write_state

if TYPE_CHECKING:
    from arcilogger_worker.service import JobContext

log = logging.getLogger(__name__)

#: How often the job looks for due sources; each source is polled by its ``interval_seconds``.
SNMP_POLL_EVERY: Final = timedelta(seconds=5)
RULE: Final = SNMP_RULE
SEVERITY: Final = "low"
#: A source is reported once one of its interfaces has had no good read for this long (N4).
ALERT_AFTER: Final = timedelta(minutes=10)
#: A source is due this much before its interval has passed: the job runs every 5 s, so an
#: interval of 15 s is kept instead of drifting to 20 s.
DUE_EARLY: Final = timedelta(seconds=2.5)


def dedup_key(source: str) -> str:
    return f"{RULE}:{source}"


@dataclass
class PollReport:
    polled: list[str] = field(default_factory=list)
    failing: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    #: Source → why it is not polled, or polled without a request (N1's refusal codes).
    refused: dict[str, str] = field(default_factory=dict)

    def details(self) -> dict[str, Any]:
        return {
            "polled": self.polled,
            "failing": self.failing,
            "opened": self.opened,
            "resolved": self.resolved,
            "refused": self.refused,
        }


#: (source, code) pairs already logged by this process: a refusal is logged once, not every 5 s.
_logged: set[tuple[str, str]] = set()


def _log_refusals(refused: Mapping[str, str]) -> None:
    for pair in sorted(refused.items()):
        if pair not in _logged:
            _logged.add(pair)
            log.warning("SNMP source %s is not polled as configured: %s", *pair)


def _due(source: SnmpSource, state: SourceState | None, now: datetime) -> bool:
    if state is None or state.polled_at is None:
        return True
    return now - state.polled_at >= timedelta(seconds=source.interval_seconds) - DUE_EARLY


async def _poll_all(
    jobs: Sequence[tuple[SnmpSource, SourceState]],
    config: Config,
    client: SnmpClient,
    now: datetime,
    refused: dict[str, str],
) -> None:
    store = secret_store(config)
    targets = [target(source, store) for source, _ in jobs]
    for (source, _), found in zip(jobs, targets, strict=True):
        if found is None:
            refused[source.name] = SECRET_MISSING  # polled as auth_failed, no request sent
    results = await asyncio.gather(
        *(
            poll_source(source, found, client, state, now)
            for (source, state), found in zip(jobs, targets, strict=True)
        ),
        return_exceptions=True,  # one source's failure never stops the others
    )
    for (source, state), result in zip(jobs, results, strict=True):
        if isinstance(result, Exception):
            log.warning("SNMP poll of %s failed: %s", source.name, type(result).__name__)
            for line in state.lines.values():
                fail(line, "timeout", now)
        elif isinstance(result, BaseException):
            raise result


def failing_lines(state: SourceState, now: datetime) -> dict[str, str]:
    """``ifName`` → status of each line without a good read for :data:`ALERT_AFTER`."""
    found: dict[str, str] = {}
    for name, line in state.lines.items():
        # the later of the last good read and the start of the watch: a read from before SNMP
        # was switched off and on again does not make a new failure late at once
        since = max((at for at in (line.updated_at, line.watched_since) if at), default=None)
        if (
            line.status not in ("ok", "disabled")
            and since is not None
            and now - since >= ALERT_AFTER
        ):
            found[name] = line.status
    return found


def poll_due(
    layout: Layout,
    config: Config,
    *,
    now: datetime,
    sessions: sessionmaker[Session] | None,
    client: SnmpClient | None = None,
) -> PollReport | None:
    """One run of ``snmp.poll``; ``None`` when no source has SNMP and nothing is left to clear."""
    report = PollReport()
    sources = snmp_sources(config)
    path = layout.wan_rates
    wan = read_state(path)
    if not sources and not wan.sources and not path.exists():
        _log_refusals(report.refused)
        return None
    jobs = []
    for source in sources:
        state = wan.sources.get(source.name)
        if _due(source, state, now):
            if state is None:
                state = wan.sources[source.name] = SourceState(name=source.name)
            jobs.append((source, state))
    if jobs:
        asyncio.run(_poll_all(jobs, config, client or PysnmpClient(), now, report.refused))
        report.polled = [source.name for source, _ in jobs]
    enabled = {source.name for source in sources}
    configured = {source.name for source in config.sources}
    changed = bool(jobs)
    for name in list(wan.sources):
        if name not in configured:
            del wan.sources[name]  # the log source itself was removed
            changed = True
        elif name not in enabled:
            for line in wan.sources[name].lines.values():
                changed = changed or line.status != "disabled"
                line.status = "disabled"
            wan.sources[name].watched_since = None
    if changed:
        try:
            write_state(path, wan, now)
        except OSError as exc:  # on Windows while the API reads it: the next poll writes it
            log.warning(
                "%s cannot be written now: %s", path.name, exc.strerror or type(exc).__name__
            )
    failing = {name: failing_lines(state, now) for name, state in wan.sources.items()}
    report.failing = sorted(name for name, lines in failing.items() if lines)
    if sessions is not None:
        _alerts(sessions, failing, enabled, now, report)
    _log_refusals(report.refused)
    return report


def _alerts(
    sessions: sessionmaker[Session],
    failing: Mapping[str, Mapping[str, str]],
    polled: Set[str],
    now: datetime,
    report: PollReport,
) -> None:
    """Open ``R20-snmp:<source>`` for each failing source; resolve the others: ``read_again``
    for a source still polled, ``not_polled`` for one whose SNMP is off or gone."""
    with sessions() as session:
        for source, lines in failing.items():
            if not lines:
                continue
            parts_tr = ", ".join(f"{name}: {SNMP_STATUSES[s][0]}" for name, s in lines.items())
            parts_en = ", ".join(f"{name}: {SNMP_STATUSES[s][1]}" for name, s in lines.items())
            minutes = int(ALERT_AFTER.total_seconds() // 60)
            opened = open_alert(
                session,
                rule=RULE,
                severity=SEVERITY,
                title=(
                    f"İnternet hattı hızı okunamıyor: {source} ({parts_tr}), {minutes} dakikadan "
                    "uzun süredir"
                ),
                details={
                    "source": source,
                    "lines": dict(lines),
                    "minutes": minutes,
                    "title_en": (
                        f"Internet line speed cannot be read: {source} ({parts_en}) for more "
                        f"than {minutes} minutes"
                    ),
                },
                key=dedup_key(source),
                time=now,
                now=now,
            )
            if opened.created:
                report.opened.append(source)
        for alert in active_alerts(session):
            key = alert.dedup_key or ""
            source = key.removeprefix(f"{RULE}:")
            if not key.startswith(f"{RULE}:") or failing.get(source):
                continue
            reason = "read_again" if source in polled else "not_polled"
            resolve(alert, reason=reason, now=now, facts={"at": isoformat_site(now, "seconds")})
            report.resolved.append(source)
        session.commit()


def job_snmp_poll(ctx: JobContext) -> Mapping[str, Any]:
    """The internet line's speed: the WAN counters of the SNMP-enabled sources (W5-N)."""
    from arcilogger_worker.service import SKIPPED, job_config

    report = poll_due(ctx.layout, job_config(ctx), now=ctx.now, sessions=ctx.sessions)
    if report is None or not (report.polled or report.opened or report.resolved):
        return {SKIPPED: True}
    return report.details()
