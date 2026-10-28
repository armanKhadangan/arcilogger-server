"""The ten cards of Uyum Durumu (Wave 4 contract 2.3.2-2.3.3): one server function for the page
and the PDF (contract D3).

:func:`compute_status` builds every card from what the evidence tasks and services already
record — the vault and ``state/`` (through :class:`arcilogger_api.sealinfo.VaultView` and the
Panel's helpers of :mod:`arcilogger_api.routers.dashboard`), ``search_counts``, ``job_runs``, the
audit log and the active alerts. It never asks a device or Kamu SM and never writes.

* **Evaluation:** a card tries the conditions of its table in order; the first that holds gives
  the tone and the reason, none gives ``ok`` with the ``ok`` reason.
* **A card that raises** is ``neutral`` with "Bu denetim şu anda yapılamadı: {error_code}" (the
  exception's class name, never its message); the error is logged. Every card runs in its own
  savepoint, so a failed query never takes the other cards with it.
* **Alerts** are read with a plain ``SELECT`` (never :func:`arcilogger_worker.alerts.store.
  active_alerts`, which locks the rows the worker updates): ``open`` and ``acknowledged``, by
  the card's dedup keys, newest first; the card lists at most :data:`ALERTS_LISTED`.
* **No data about log subjects:** source names, counts, times, table and profile names and the
  configured time servers only; never a client address, a person, a device host name, a sender
  address or the replica target (contract 0.1 item 4).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from arcilogger_api.admin_channel import Channel
from arcilogger_api.compliance import texts
from arcilogger_api.compliance.models import (
    ComplianceAlertRef,
    ComplianceCard,
    ComplianceCardId,
    ComplianceCounts,
    ComplianceFact,
    ComplianceStatus,
    ComplianceTone,
)
from arcilogger_api.compliance.texts import Text
from arcilogger_api.routers.dashboard import CLOCK_STALE, clock_box, credit_box, ingest_box
from arcilogger_api.sealinfo import (
    RECENT_VERIFY_DAYS,
    VaultView,
    credit_history,
    on_demand_verifications,
    read_seal_state,
)
from arcilogger_core.chain import sealed_days
from arcilogger_core.config import MIN_RETENTION_DAYS, Config
from arcilogger_core.fmt import Lang, format_bytes, format_date, format_datetime, format_number
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, site_day, to_site
from arcilogger_db.models import Alert, AuditLog, JobRun, SearchCount, Source
from arcilogger_db.settings import DEFAULT_SETTINGS, get_settings
from arcilogger_worker.alerts.r15 import watched_sources
from arcilogger_worker.alerts.store import ACTIVE_STATES
from arcilogger_worker.alerts.system import backup_state_path
from arcilogger_worker.compliance_filter import FilterEvidence, filter_evidence

log = logging.getLogger("arcilogger.api.compliance")

#: Audit action of ``POST /compliance/config-backup`` (contract 2.3.5); card 10 reads the newest.
CONFIG_BACKUP_CONFIRM: Final = "compliance.config_backup_confirm"
#: Active alerts a card lists (newest first).
ALERTS_LISTED: Final = 5
#: Names a reason or fact lists before "+{k}".
NAMES_LISTED: Final = 5
#: Web filter profiles card 7 lists.
PROFILES_LISTED: Final = 10
#: Days of card 3 (ending yesterday) and of card 9.
CHAIN_DAYS: Final = 30
REPLICA_DAYS: Final = 7
#: Card 5: a credit reading older than this is "unknown"; a certificate this close to its end is
#: worth a warning (the specification's 180 days).
CREDIT_FRESH: Final = timedelta(days=2)
CERT_WARNING: Final = timedelta(days=180)
#: Card 8: the window of the lease evidence rows.
LEASE_WINDOW: Final = timedelta(hours=24)

#: The settings the cards read (a stored value of the wrong type counts as the shipped default).
SETTING_KEYS: Final = (
    "alerts.seal_overdue_hour",
    "alerts.replica_stale_hours",
    "alerts.backup_stale_hours",
    "alerts.backup_missing_hours",
    "alerts.filter_days",
    "alerts.filter_skip_holidays",
)

#: Dedup keys of a card's alerts (contract 2.3.2): ``(key, prefix)``; card 2's key is completed
#: with yesterday's date. Prefixes only for R20-filter and R20-lease (contract D7). Card 4 goes
#: by rule instead (:data:`RETENTION_RULE`).
ALERT_KEYS: Final[Mapping[ComplianceCardId, tuple[str, bool]]] = {
    "sources": ("R15:", True),
    "seal_yesterday": ("R20-seal:", False),
    "chain": ("R20-verify", False),
    "retention": ("R20-retention", True),
    "tsa": ("R20-credit", False),
    "clock": ("R20-clock", True),
    "content_filter": ("R20-filter:", True),
    "lease_evidence": ("R20-lease:", True),
    "replica": ("R20-replica", True),
    "backup": ("R20-backup", True),
}
#: Card 4's alerts: this rule, except its ``info`` deletion notes (as the evidence jobs read it).
RETENTION_RULE: Final = "R20-retention"


# -- formatting -----------------------------------------------------------------------------------


def fill(text: Text, **params: object) -> Text:
    """Both texts of ``text`` filled; a ``(tr, en)`` parameter gives each language its own."""

    def pick(language: int) -> dict[str, object]:
        return {
            key: value[language] if isinstance(value, tuple) else value
            for key, value in params.items()
        }

    return text[0].format(**pick(0)), text[1].format(**pick(1))


def same(value: str) -> Text:
    return value, value


def when(moment: datetime | None) -> str:
    return texts.NONE if moment is None else format_datetime(moment)


def number(value: float, decimals: int = 0) -> Text:
    """``1.234`` / ``1,234``; ``3,2`` / ``3.2`` with at most ``decimals`` places, trailing
    zeros dropped (as :func:`arcilogger_core.fmt.format_bytes`)."""

    def text(lang: Lang) -> str:
        shown = format_number(value, decimals, lang)
        if decimals and shown.endswith("0" * decimals):
            return shown[: -decimals - 1]
        return shown

    return text("tr"), text("en")


def names(items: Sequence[str], limit: int = NAMES_LISTED, separator: str = ", ") -> str:
    """The first ``limit`` names and "+{k}" for the rest; "—" for none."""
    if not items:
        return texts.NONE
    shown = separator.join(items[:limit])
    rest = len(items) - limit
    return f"{shown} +{rest}" if rest > 0 else shown


def size(num_bytes: int | None) -> Text:
    """``1,2 GB`` / ``1.2 GB`` (binary multiples, as Windows shows them)."""
    if num_bytes is None or num_bytes < 0:
        return same(texts.NONE)
    return format_bytes(num_bytes, "tr"), format_bytes(num_bytes, "en")


def moment(value: Any) -> datetime | None:
    """An aware ISO time of a state file; anything else is ``None``."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def server_label(config: Config) -> Text:
    """The configured time-stamp server (card 5 labels)."""
    tsa = config.tsa
    if tsa.legal_grade:
        return texts.SERVER_PRODUCTION
    if tsa.provider == "zamane":
        return texts.SERVER_TEST
    return texts.SERVER_MOCK if tsa.provider == "mock" else texts.SERVER_RFC3161


# -- the context of one computation ---------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    tone: ComplianceTone
    reason: Text


@dataclass
class CardContext:
    """What the cards read, each at most once per computation (lazily: a card that fails to read
    something fails alone; the next card that needs it tries again)."""

    db: Session
    layout: Layout
    config: Config
    channel: Channel
    now: datetime

    @cached_property
    def view(self) -> VaultView:
        return VaultView(self.layout, self.config, now=self.now)

    @property
    def today(self) -> date:
        return site_day(self.now)

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)

    @cached_property
    def settings(self) -> dict[str, Any]:
        return get_settings(self.db, keys=SETTING_KEYS)

    def setting(self, key: str) -> Any:
        value, default = self.settings.get(key), DEFAULT_SETTINGS[key]
        return value if type(value) is type(default) else default

    @cached_property
    def watched(self) -> list[Source]:
        return watched_sources(self.db)

    @cached_property
    def cells(self) -> dict[date, dict[str, Any]]:
        """The sealing page's cells of the 30 days ending yesterday (as the Panel's seal box)."""
        days = [self.yesterday - timedelta(days=n) for n in range(CHAIN_DAYS - 1, -1, -1)]
        on_demand = on_demand_verifications(self.db, days[0], days[-1])
        return {day: self.view.summary(day, on_demand.get(day)) for day in days}

    @cached_property
    def sealed(self) -> list[date]:
        """The sealed days in the vault (manifest and time stamp), oldest first."""
        return [day for day in sealed_days(self.layout) if self.view.is_sealed(day)]

    def alerts(self, card: ComplianceCardId) -> list[Alert]:
        """The card's active alerts, newest first (a plain ``SELECT``: no lock)."""
        key, prefix = ALERT_KEYS[card]
        if card == "seal_yesterday":
            key += self.yesterday.isoformat()
        if card == "retention":
            # A retention alert is imported without a dedup key (every refused run is its own
            # alert, ``import_key``); the ``info`` one only notes days deleted as configured.
            condition: ColumnElement[bool] = and_(
                Alert.rule == RETENTION_RULE, Alert.severity != "info"
            )
        elif prefix:
            condition = Alert.dedup_key.startswith(key, autoescape=True)
        else:
            condition = Alert.dedup_key == key
        return list(
            self.db.scalars(
                select(Alert)
                .where(Alert.state.in_(ACTIVE_STATES), condition)
                .order_by(Alert.time.desc(), Alert.id.desc())
            )
        )


def fact(card: ComplianceCardId, key: str, value: Text | str) -> ComplianceFact:
    label_tr, label_en = texts.FACT_LABELS[card][key]
    value_tr, value_en = same(value) if isinstance(value, str) else value
    return ComplianceFact(
        key=key, label_tr=label_tr, label_en=label_en, value_tr=value_tr, value_en=value_en
    )


def build(
    ctx: CardContext,
    card: ComplianceCardId,
    verdict: Verdict,
    facts: Sequence[ComplianceFact],
    alerts: Sequence[Alert],
) -> ComplianceCard:
    number_ = CARD_ORDER.index(card) + 1
    (title_tr, title_en), page = texts.CARDS[card]
    verify_tr, verify_en = texts.VERIFY_ON_SITE.get(card, (None, None))
    return ComplianceCard(
        id=card,
        number=number_,
        tone=verdict.tone,
        title_tr=title_tr,
        title_en=title_en,
        reason_tr=verdict.reason[0],
        reason_en=verdict.reason[1],
        facts=list(facts),
        alerts=[
            ComplianceAlertRef(
                id=alert.id,
                rule=alert.rule,
                severity=alert.severity,
                opened_at=to_site(alert.time),
            )
            for alert in alerts[:ALERTS_LISTED]
        ],
        page=page,
        doc_tr=texts.DOC[0].format(number=number_),
        doc_en=texts.DOC[1].format(number=number_),
        verify_on_site_tr=verify_tr,
        verify_on_site_en=verify_en,
        checked_at=to_site(ctx.now),
    )


# -- card 1: sources ------------------------------------------------------------------------------


def card_sources(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("sources")
    box = ingest_box(ctx.db, ctx.layout, ctx.now)
    alerted = sorted({(alert.dedup_key or "").removeprefix("R15:") for alert in alerts})
    silent = [source.name for source in box.silent]
    grace = [name for name in silent if name not in alerted]
    if alerted:
        verdict = Verdict("error", fill(texts.SOURCES_ALERT, n=len(alerted), names=names(alerted)))
    elif grace:
        verdict = Verdict("warning", fill(texts.SOURCES_SILENT, names=names(grace)))
    elif not box.sources_total:
        verdict = Verdict("warning", texts.SOURCES_NONE)
    else:
        verdict = Verdict("ok", fill(texts.SOURCES_OK, n=box.sources_total))
    facts = [
        fact("sources", "watched", str(box.sources_total)),
        fact("sources", "active", str(box.sources_active)),
        fact("sources", "silent", names(silent)),
        fact("sources", "heartbeat", texts.SOURCES_HEARTBEAT),
    ]
    return build(ctx, "sources", verdict, facts, alerts)


# -- card 2: yesterday's seal ---------------------------------------------------------------------


def _verification(cell: Mapping[str, Any]) -> Text:
    verification = cell.get("verification")
    if not isinstance(verification, dict) or not isinstance(verification.get("at"), datetime):
        return same(texts.NONE)
    outcome = texts.PASSED if verification.get("ok") else texts.FAILED
    return fill(outcome, time=format_datetime(verification["at"]))


def card_seal_yesterday(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("seal_yesterday")
    yesterday = ctx.yesterday
    cell = ctx.cells[yesterday]
    state = str(cell["state"])
    day = format_date(yesterday)
    hour = ctx.setting("alerts.seal_overdue_hour")
    overdue_at = datetime.combine(ctx.today, time(min(max(hour, 0), 23)), tzinfo=SITE_TZ)
    message = (cell.get("message_tr") or texts.NONE, cell.get("message_en") or texts.NONE)
    if state == "failed":
        verdict = Verdict("error", fill(texts.SEAL_FAILED, day=day, message=message))
    elif state == "pending" and ctx.now >= overdue_at:
        verdict = Verdict("error", fill(texts.SEAL_OVERDUE, day=day, hour=f"{hour:02d}"))
    elif state == "no_data" and ctx.watched:
        verdict = Verdict("error", fill(texts.SEAL_NO_DATA, day=day))
    elif state == "pending":
        verdict = Verdict("warning", fill(texts.SEAL_PENDING, day=day))
    elif state == "sealed":
        verdict = Verdict("warning", fill(texts.SEAL_NOT_VERIFIED, day=day))
    elif state == "late":
        verdict = Verdict("warning", fill(texts.SEAL_LATE, day=day))
    elif state == "verified" and not ctx.config.tsa.legal_grade:
        verdict = Verdict("warning", texts.SEAL_NOT_PRODUCTION)
    elif state == "no_data":
        verdict = Verdict("neutral", texts.SEAL_NO_SOURCES)
    else:
        verdict = Verdict("ok", fill(texts.SEAL_OK, day=day))
    facts = [
        fact("seal_yesterday", "day", day),
        fact("seal_yesterday", "state", (str(cell["label_tr"]), str(cell["label_en"]))),
        fact("seal_yesterday", "sealed_at", when(moment(cell.get("sealed_at")))),
        fact("seal_yesterday", "verification", _verification(cell)),
        fact("seal_yesterday", "server", server_label(ctx.config)),
    ]
    return build(ctx, "seal_yesterday", verdict, facts, alerts)


# -- card 3: the chain ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FullCheck:
    """``state/verify/last-full.json`` (``verify --all``, the monthly VerifyMonthly task)."""

    at: datetime | None
    ok: bool


def full_check(layout: Layout) -> FullCheck | None:
    """The newest full chain check; ``None`` when it never ran. An unreadable file counts as a
    failed check of unknown time: the card's error row, not "could not be checked"."""
    try:
        data = json.loads((layout.state / "verify" / "last-full.json").read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return FullCheck(at=None, ok=False)
    if not isinstance(data, dict):
        return FullCheck(at=None, ok=False)
    return FullCheck(at=moment(data.get("at")), ok=data.get("ok") is True)


def _recently_verified(ctx: CardContext, cell: Mapping[str, Any]) -> bool:
    verification = cell.get("verification")
    return (
        isinstance(verification, dict)
        and verification.get("ok") is True
        and isinstance(verification.get("at"), datetime)
        and verification["at"] >= ctx.now - timedelta(days=RECENT_VERIFY_DAYS)
    )


def card_chain(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("chain")
    cells = ctx.cells
    states = {day: str(cell["state"]) for day, cell in cells.items()}
    failed = [day for day, state in states.items() if state == "failed"]
    unverified = [day for day, state in states.items() if state == "sealed"]
    waiting = [day for day, state in states.items() if state == "pending" and day < ctx.yesterday]
    sealed = sum(1 for day in cells if ctx.view.is_sealed(day))
    full = full_check(ctx.layout)
    full_at = when(full.at if full else None)
    first = ctx.sealed[0] if ctx.sealed else None
    if failed:
        listed = names([format_date(day) for day in failed])
        verdict = Verdict("error", fill(texts.CHAIN_FAILED, n=len(failed), days=listed))
    elif (full is not None and not full.ok) or alerts:
        at = full_at if full is not None and not full.ok else when(alerts[0].time)
        verdict = Verdict("error", fill(texts.CHAIN_FULL_FAILED, at=at))
    elif unverified:
        verdict = Verdict("warning", fill(texts.CHAIN_NOT_VERIFIED, n=len(unverified)))
    elif waiting:
        listed = names([format_date(day) for day in waiting])
        verdict = Verdict("warning", fill(texts.CHAIN_PENDING, n=len(waiting), days=listed))
    elif full is not None and (
        full.at is None or ctx.now - full.at > timedelta(days=RECENT_VERIFY_DAYS)
    ):
        verdict = Verdict("warning", fill(texts.CHAIN_FULL_OLD, at=full_at))
    elif full is None and first is not None and (ctx.today - first).days >= RECENT_VERIFY_DAYS:
        verdict = Verdict("warning", texts.CHAIN_FULL_NEVER)
    elif not sealed:
        verdict = Verdict("neutral", texts.CHAIN_NONE)
    elif full is not None:
        verdict = Verdict("ok", fill(texts.CHAIN_OK, n=sealed, at=full_at))
    else:
        verdict = Verdict("ok", fill(texts.CHAIN_OK_FIRST, n=sealed))
    # A late day counts as verified when a verification passed within 35 days, as "verified".
    late = [cell for cell in cells.values() if cell["state"] == "late"]
    late_verified = sum(1 for cell in late if _recently_verified(ctx, cell))
    counts = {
        "verified": sum(1 for state in states.values() if state == "verified") + late_verified,
        "not_verified": len(unverified) + len(late) - late_verified,
        "failed": len(failed),
        "no_data": sum(1 for state in states.values() if state in ("no_data", "deleted")),
    }
    facts = [fact("chain", key, str(count)) for key, count in counts.items()]
    if full is None:
        full_text: Text = same(texts.NONE)
    else:
        full_text = fill(texts.PASSED if full.ok else texts.FAILED, time=full_at)
    facts += [fact("chain", "full_check", full_text), fact("chain", "tasks", texts.CHAIN_TASKS)]
    return build(ctx, "chain", verdict, facts, alerts)


# -- card 4: retention ----------------------------------------------------------------------------


def card_retention(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("retention")
    retention = ctx.config.retention
    vault = ctx.sealed
    deleted = sorted(ctx.view.deletions)
    oldest = vault[0] if vault else None
    # "First sealed day" counts the days retention deleted as well (owner question Q7).
    first = min([*vault[:1], *deleted[:1]], default=None)
    floor = ctx.today - timedelta(days=MIN_RETENTION_DAYS)
    beyond = ctx.today - timedelta(days=retention.days)
    age = (ctx.today - oldest).days if oldest else 0
    shown = format_date(oldest) if oldest else texts.NONE
    if alerts:
        verdict = Verdict("error", texts.RETENTION_ALERT)
    elif first is not None and oldest is not None and first <= floor < oldest:
        verdict = Verdict("error", fill(texts.RETENTION_SHORT, oldest=shown, age=age))
    elif not retention.enabled and oldest is not None and oldest < beyond:
        kept = sum(1 for day in vault if day < beyond)
        verdict = Verdict(
            "warning",
            fill(texts.RETENTION_OFF, days=retention.days, n=kept, oldest=shown),
        )
    elif oldest is None:
        verdict = Verdict("neutral", texts.RETENTION_NONE)
    elif age >= MIN_RETENTION_DAYS:
        verdict = Verdict("ok", fill(texts.RETENTION_OK_FULL, oldest=shown, age=age))
    else:
        verdict = Verdict("ok", fill(texts.RETENTION_OK_YOUNG, oldest=shown, age=age))
    state = fill(texts.RETENTION_ON, days=retention.days) if retention.enabled else texts.OFF
    facts = [
        fact("retention", "oldest", shown),
        fact("retention", "retention", state),
        fact("retention", "deleted", str(len(deleted))),
    ]
    return build(ctx, "retention", verdict, facts, alerts)


# -- card 5: Kamu SM credit and the TSA certificate -----------------------------------------------


def certificate_end(ctx: CardContext) -> datetime | None:
    """``tsa_cert_not_after`` of the newest sealed day's state (``None``: none recorded)."""
    if not ctx.sealed:
        return None
    state, _ = read_seal_state(ctx.layout, ctx.sealed[-1])
    return moment(state.get("tsa_cert_not_after")) if state else None


def card_tsa(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("tsa")
    tsa = ctx.config.tsa
    credit = credit_box(ctx.layout, ctx.config)
    entries, _ = credit_history(ctx.layout)
    readings = [entry["time"] for entry in entries if entry["credits"] is not None]
    read_at = readings[-1] if readings else None
    remaining, threshold = credit.remaining, tsa.credit_alert_threshold
    cert = certificate_end(ctx)
    left = (cert - ctx.now).days if cert else None
    credits = number(remaining) if remaining is not None else same(texts.NONE)
    estimate = number(credit.estimated_days) if credit.estimated_days is not None else texts.NONE
    cert_date = format_date(cert) if cert else texts.NONE
    if not tsa.legal_grade:
        verdict = Verdict(
            "error", fill(texts.TSA_NOT_PRODUCTION, provider=server_label(ctx.config))
        )
    elif remaining is not None and remaining <= 0:
        verdict = Verdict("error", texts.TSA_NO_CREDIT)
    elif cert is not None and cert < ctx.now:
        verdict = Verdict("error", fill(texts.TSA_CERT_EXPIRED, date=cert_date))
    elif remaining is not None and remaining <= threshold:
        verdict = Verdict(
            "warning",
            fill(texts.TSA_LOW, credits=credits, threshold=number(threshold), days=estimate),
        )
    elif read_at is None or ctx.now - read_at > CREDIT_FRESH:
        verdict = Verdict("warning", fill(texts.TSA_UNKNOWN, at=when(read_at)))
    elif cert is not None and cert - ctx.now <= CERT_WARNING:
        verdict = Verdict("warning", fill(texts.TSA_CERT_SOON, date=cert_date, days=left))
    elif not ctx.sealed:
        verdict = Verdict("neutral", texts.TSA_NONE)
    else:
        verdict = Verdict(
            "ok",
            fill(
                texts.TSA_OK,
                credits=credits,
                threshold=number(threshold),
                days=texts.NONE if left is None else left,
            ),
        )
    facts = [
        fact("tsa", "server", server_label(ctx.config)),
        fact("tsa", "credits", credits),
        fact("tsa", "estimated_days", estimate),
        fact("tsa", "threshold", number(threshold)),
        fact("tsa", "credit_at", when(read_at)),
        fact("tsa", "cert_until", cert_date),
        fact("tsa", "cert_days", texts.NONE if left is None else str(left)),
    ]
    return build(ctx, "tsa", verdict, facts, alerts)


# -- card 6: the server clock ---------------------------------------------------------------------


def card_clock(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("clock")
    clock = ctx.config.clock
    box = clock_box(ctx.layout, ctx.config, ctx.now)
    fresh = box.checked_at is not None and ctx.now - box.checked_at <= CLOCK_STALE
    limit = number(clock.alert_offset_ms)
    offset = number(box.offset_ms, 1) if box.offset_ms is not None else same(texts.NONE)
    server = box.ntp_server or texts.NONE
    at = when(box.checked_at)
    # Unlike the Panel (``<=``), an offset equal to the limit is a problem (owner question Q9).
    if fresh and box.offset_ms is not None and abs(box.offset_ms) >= clock.alert_offset_ms:
        verdict = Verdict("error", fill(texts.CLOCK_OFFSET, offset=offset, limit=limit))
    elif (fresh and box.offset_ms is None) or alerts:
        verdict = Verdict("error", fill(texts.CLOCK_UNREACHABLE, server=server))
    elif box.checked_at is None:
        verdict = Verdict("warning", texts.CLOCK_NONE)
    elif not fresh:
        verdict = Verdict("warning", fill(texts.CLOCK_STALE, at=at))
    elif not clock.ntp_server:
        verdict = Verdict(
            "warning", fill(texts.CLOCK_NO_LOCAL, fallback=clock.fallback_server or texts.NONE)
        )
    else:
        verdict = Verdict(
            "ok", fill(texts.CLOCK_OK, offset=offset, server=server, at=at, limit=limit)
        )
    facts = [
        fact("clock", "checked_at", at),
        fact("clock", "server", server),
        fact(
            "clock",
            "offset",
            fill(texts.CLOCK_MS, value=offset) if box.offset_ms is not None else texts.NONE,
        ),
        fact("clock", "limit", fill(texts.CLOCK_MS, value=limit)),
        fact(
            "clock",
            "local_source",
            texts.CLOCK_LOCAL_SET if clock.ntp_server else texts.CLOCK_LOCAL_NONE,
        ),
    ]
    return build(ctx, "clock", verdict, facts, alerts)


# -- card 7: content filter -----------------------------------------------------------------------


def _weekdays(value: Sequence[int]) -> Text:
    days = [
        texts.WEEKDAYS[day - 1] for day in sorted(set(value)) if type(day) is int and 1 <= day <= 7
    ]
    if not days:
        return same(texts.NONE)
    return ", ".join(tr for tr, _ in days), ", ".join(en for _, en in days)


def card_content_filter(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("content_filter")
    evidence: FilterEvidence = filter_evidence(ctx.db, now=ctx.now)
    hours = evidence.window_hours
    last = when(evidence.last_at)
    in_window = evidence.last_at is not None and ctx.now - evidence.last_at <= timedelta(
        hours=hours
    )
    web, dns = evidence.today.get("fg_web", 0), evidence.today.get("fg_dns", 0)
    if not evidence.applies:
        verdict = Verdict("neutral", texts.FILTER_NONE)
    elif evidence.alert_due:
        verdict = Verdict("error", fill(texts.FILTER_DUE, hours=hours, last=last))
    elif not in_window:
        verdict = Verdict("warning", fill(texts.FILTER_QUIET, hours=hours, last=last))
    elif evidence.silent_kinds:
        kind = texts.FILTER_KINDS[evidence.silent_kinds[0]]
        verdict = Verdict("warning", fill(texts.FILTER_SILENT_KIND, kind=kind, hours=hours))
    else:
        verdict = Verdict("ok", fill(texts.FILTER_OK, last=last, web=web, dns=dns))
    days = " · ".join(f"{day:%d.%m}: {web_n + dns_n}" for day, web_n, dns_n in evidence.days)
    filter_days = ctx.setting("alerts.filter_days")
    skip_holidays = ctx.setting("alerts.filter_skip_holidays")
    facts = [
        fact("content_filter", "last", last),
        fact("content_filter", "today_web", str(web)),
        fact("content_filter", "today_dns", str(dns)),
        fact("content_filter", "days", days or texts.NONE),
        fact(
            "content_filter",
            "profiles",
            names(list(evidence.web_profiles), limit=PROFILES_LISTED),
        ),
        fact("content_filter", "dns_profile", texts.FILTER_DNS_PROFILE),
        fact("content_filter", "expected_days", _weekdays(filter_days)),
        fact(
            "content_filter",
            "holidays",
            texts.FILTER_HOLIDAYS_SKIPPED if skip_holidays else texts.FILTER_HOLIDAYS_EXPECTED,
        ),
    ]
    return build(ctx, "content_filter", verdict, facts, alerts)


# -- card 8: lease evidence -----------------------------------------------------------------------


def lease_rows(ctx: CardContext) -> dict[str, int]:
    """Rows of the lease tables of the last 24 hours from the watched sources
    (``search_counts``: hours ``date_trunc``-ed, so the hour that holds the start counts)."""
    rows = dict.fromkeys(texts.LEASE_TABLES, 0)
    sources = [source.name for source in ctx.watched]
    if not sources:
        return rows
    since = (ctx.now - LEASE_WINDOW).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    for table, count in ctx.db.execute(
        select(SearchCount.tbl, func.sum(SearchCount.n))
        .where(
            SearchCount.source.in_(sources),
            SearchCount.hour >= since,
            SearchCount.tbl.in_(list(texts.LEASE_TABLES)),
        )
        .group_by(SearchCount.tbl)
    ):
        rows[table] = int(count)
    return rows


def card_lease_evidence(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("lease_evidence")
    rows = lease_rows(ctx)
    arrived = [label for table, label in texts.LEASE_TABLES.items() if rows[table]]
    if not ctx.watched:
        verdict = Verdict("neutral", texts.LEASE_NONE)
    elif any(alert.severity in ("high", "critical") for alert in alerts):
        verdict = Verdict("error", fill(texts.LEASE_PROBLEMS, n=len(alerts)))
    elif alerts:
        verdict = Verdict("warning", fill(texts.LEASE_PROBLEMS, n=len(alerts)))
    elif not arrived:
        verdict = Verdict("warning", texts.LEASE_NO_ROWS)
    else:
        verdict = Verdict("ok", fill(texts.LEASE_OK, tables=", ".join(arrived)))
    # The alerts' titles name causes, counts and configured networks (never the dedup keys).
    titles_tr = [alert.title for alert in alerts]
    titles_en = [str(alert.details.get("title_en") or texts.LEASE_PROBLEM_EN) for alert in alerts]
    facts = [
        fact("lease_evidence", table, fill(texts.LEASE_ROWS, n=number(rows[table])))
        for table in texts.LEASE_TABLES
    ]
    facts.append(
        fact(
            "lease_evidence",
            "problems",
            (names(titles_tr, separator="; "), names(titles_en, separator="; ")),
        )
    )
    return build(ctx, "lease_evidence", verdict, facts, alerts)


# -- card 9: replica ------------------------------------------------------------------------------


def card_replica(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("replica")
    enabled = ctx.config.replica.enabled
    days = [ctx.yesterday - timedelta(days=n) for n in range(REPLICA_DAYS - 1, -1, -1)]
    sealed = [day for day in days if ctx.view.is_sealed(day)]
    copied: dict[date, datetime] = {}
    for day in sealed:
        # Only the status and the time: the state file also names the target, never shown.
        replica = ctx.view.replica(day)
        verified_at = moment(replica.get("verified_at"))
        if replica["status"] == "ok" and verified_at is not None:
            copied[day] = verified_at
    waiting = [day for day in sealed if day not in copied]
    if not enabled:
        verdict = Verdict("error", texts.REPLICA_OFF)
    elif alerts:
        verdict = Verdict("error", texts.REPLICA_ALERT)
    elif waiting:
        verdict = Verdict(
            "warning", fill(texts.REPLICA_PENDING, n=len(waiting), day=format_date(waiting[0]))
        )
    elif not sealed:
        verdict = Verdict("neutral", texts.REPLICA_NONE)
    else:
        verdict = Verdict("ok", fill(texts.REPLICA_OK, n=len(sealed)))
    facts = [
        fact("replica", "enabled", texts.ON if enabled else texts.OFF),
        fact("replica", "last_copy", when(max(copied.values(), default=None))),
        fact("replica", "pending", str(len(waiting))),
        fact("replica", "stale_hours", str(ctx.setting("alerts.replica_stale_hours"))),
    ]
    return build(ctx, "replica", verdict, facts, alerts)


# -- card 10: backups -----------------------------------------------------------------------------


#: An unreadable ``last.json``: a failed backup, as the worker's backup check reads it.
UNREADABLE: Final[Mapping[str, Any]] = {"ok": False}


def backup_state(layout: Layout) -> Mapping[str, Any] | None:
    """``state/backup/last.json`` (``None``: no backup ran yet); an unreadable file or one that
    is not an object is :data:`UNREADABLE` (``R20-backup`` "Yedek durum dosyası okunamıyor")."""
    try:
        data = json.loads(backup_state_path(layout).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return UNREADABLE
    return data if isinstance(data, dict) else UNREADABLE


def last_confirmation(db: Session) -> AuditLog | None:
    """The newest configuration-backup confirmation (only its time and SHA-256 are used)."""
    return db.scalars(
        select(AuditLog)
        .where(AuditLog.action == CONFIG_BACKUP_CONFIRM)
        .order_by(AuditLog.id.desc())
        .limit(1)
    ).first()


def card_backup(ctx: CardContext) -> ComplianceCard:
    alerts = ctx.alerts("backup")
    state = backup_state(ctx.layout)
    stale_hours = ctx.setting("alerts.backup_stale_hours")
    missing_hours = ctx.setting("alerts.backup_missing_hours")
    finished = moment(state.get("finished")) if state else None
    succeeded = finished if state is not None and state.get("ok") is True else None
    confirmed = last_confirmation(ctx.db)
    at = when(succeeded)
    if not ctx.config.backup.enabled:
        verdict = Verdict("error", texts.BACKUP_OFF)
    elif alerts or (state is not None and state.get("ok") is False):
        verdict = Verdict("error", texts.BACKUP_FAILED)
    elif state is None and _younger(ctx, timedelta(hours=missing_hours)):
        verdict = Verdict("neutral", texts.BACKUP_FIRST)
    elif succeeded is None or ctx.now - succeeded > timedelta(hours=stale_hours):
        verdict = Verdict("warning", fill(texts.BACKUP_STALE, at=at, hours=stale_hours))
    elif confirmed is None:
        verdict = Verdict("warning", texts.BACKUP_NEVER_CONFIRMED)
    elif confirmed.details.get("config_sha256") != ctx.channel.files.sha256:
        verdict = Verdict("warning", fill(texts.BACKUP_CHANGED, at=when(confirmed.time)))
    else:
        verdict = Verdict(
            "ok",
            fill(
                texts.BACKUP_OK,
                at=at,
                size=size(_bytes(state)),
                confirmed=when(confirmed.time),
            ),
        )
    facts = [
        fact("backup", "last_backup", when(finished)),
        fact("backup", "size", size(_bytes(state))),
        fact("backup", "enabled", texts.ON if ctx.config.backup.enabled else texts.OFF),
        fact(
            "backup",
            "config_copy",
            texts.BACKUP_NEVER if confirmed is None else when(confirmed.time),
        ),
    ]
    return build(ctx, "backup", verdict, facts, alerts)


def _bytes(state: Mapping[str, Any] | None) -> int | None:
    value = state.get("size") if state else None
    return value if type(value) is int else None


def _younger(ctx: CardContext, age: timedelta) -> bool:
    """The installation (its first ``job_runs`` row) is younger than ``age``."""
    installed = ctx.db.scalar(select(func.min(JobRun.started_at)))
    return installed is None or ctx.now - installed < age


# -- all cards ------------------------------------------------------------------------------------

CARD_FUNCTIONS: Final[Mapping[ComplianceCardId, Callable[[CardContext], ComplianceCard]]] = {
    "sources": card_sources,
    "seal_yesterday": card_seal_yesterday,
    "chain": card_chain,
    "retention": card_retention,
    "tsa": card_tsa,
    "clock": card_clock,
    "content_filter": card_content_filter,
    "lease_evidence": card_lease_evidence,
    "replica": card_replica,
    "backup": card_backup,
}
#: The order of contract 2.3.3 (``number`` 1-10).
CARD_ORDER: Final[tuple[ComplianceCardId, ...]] = tuple(CARD_FUNCTIONS)


def compute_card(ctx: CardContext, card: ComplianceCardId) -> ComplianceCard:
    """One card; a card that raises is ``neutral`` with the exception's class name."""
    try:
        with ctx.db.begin_nested():
            return CARD_FUNCTIONS[card](ctx)
    except Exception as exc:
        log.exception("compliance card %s could not be computed", card)
        reason = fill(texts.CHECK_FAILED, error_code=type(exc).__name__)
        return build(ctx, card, Verdict("neutral", reason), [], [])


def compute_status(
    db: Session, layout: Layout, config: Config, channel: Channel, now: datetime
) -> ComplianceStatus:
    """The ten cards at ``now`` (an aware time), for ``GET /compliance`` and the PDF."""
    ctx = CardContext(db=db, layout=layout, config=config, channel=channel, now=now)
    cards = [compute_card(ctx, card) for card in CARD_ORDER]
    tones = [card.tone for card in cards]
    return ComplianceStatus(
        generated_at=to_site(now),
        site_name=config.site_name if config.site_name_set else None,
        counts=ComplianceCounts(
            ok=tones.count("ok"),
            warning=tones.count("warning"),
            error=tones.count("error"),
            neutral=tones.count("neutral"),
        ),
        cards=cards,
        notice_tr=texts.NOTICE[0],
        notice_en=texts.NOTICE[1],
    )
