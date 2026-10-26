"""How every log source is doing: Kaynaklar (spec 12.3 page 3, contract 2.16).

**Which sources** — the configured ones (``[[sources]]``: exact addresses and networks) and every
row of ``sources`` (unknown senders, members ``<name>-<ip>`` of a configured network, sources
switched off or removed from the configuration). A network entry is listed with its members
(``network_of``) and sums them.

**State** — the rule of R15 and the Panel (:mod:`arcilogger_worker.alerts.r15`): the newest
message is the later of what ingest loaded (``sources.last_seen``) and what the collector received
(``state/collector-status.json`` while fresh). ``active`` within the heartbeat, else ``silent``
(an ``expected = false`` source is shown "Sessiz (beklenmiyor)"); ``never`` without any message;
``disabled`` when switched off; ``unknown`` for an ``unknown-<ip>`` sender not in the
configuration, ``promoted`` once its address is configured (``promoted_to``: the name the
collector now writes it under; the old folder and its evidence stay where they are).

**Cost** (one overview, cached 15 s; a detail cached 30 s per source):

* the messages a minute of the last 60 whole minutes: one grouped query on
  ``ix_messages_source_time``, one descent per name (:func:`sparklines`; guard: under 1 s at
  300,000 rows an hour, ``api/tests/test_sources_api.py``);
* today's lines and the ingest lag: ``ingest_offsets`` and one ``stat`` per spool file;
* parse failures: sums of ``parse_failures``;
* ``suggested`` (unknown senders only, at most 20): the device family of what they sent in the last
  24 hours and a name from the most common ``messages.host``;
* a detail's hours from ``search_counts`` while it is complete for the range, else counted
  directly; its days from ``ingest_offsets``, ``parse_failures`` and the seals; its gaps from the
  sealed manifests (parsed once, kept by path, size and time) plus today's open gap.

Counts and states only — nothing here is person-level data, nothing is audited. The raw lines of
a source (``GET /sources/{name}/tail``) are, and are audited as ``raw.tail``
(:mod:`arcilogger_api.routers.sources`).
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
from collections import Counter, OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.rawlines import regular_file
from arcilogger_api.sealinfo import VaultView
from arcilogger_core.config import Config, SourceConfig
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, to_site
from arcilogger_db.kinds import family_of, kind_of
from arcilogger_db.models import IngestOffset, Message, ParseFailure, SearchCount, Source
from arcilogger_db.searchcounts import counts_complete
from arcilogger_worker.correlation.watermark import COLLECTOR_STATUS_FRESH

State = Literal["active", "silent", "never", "disabled", "unknown", "promoted"]

#: Minutes of the sparkline (as the Panel's).
SPARK_MINUTES: Final = 60
#: Hours of a detail's ``hourly``, days of ``daily``, ``gaps`` and ``parse_failures``.
HOURLY_HOURS: Final = 48
DAILY_DAYS: Final = 14
GAP_DAYS: Final = 30
FAILURE_DAYS: Final = 7
FAILURES_SHOWN: Final = 50
#: Unknown senders a suggestion is made for, and the history it looks at.
SUGGESTED_MAX: Final = 20
SUGGEST_WINDOW: Final = timedelta(hours=24)
UNKNOWN_PREFIX: Final = "unknown-"
#: A source name (``[[sources]] name``).
NAME: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
#: Parsed manifests kept (sealed manifests never change).
MANIFESTS_KEPT: Final = 64


def _minutes(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


def _address_of_unknown(name: str) -> str | None:
    """The sender address of an ``unknown-<ip>`` folder (the collector writes ``:`` as ``-``)."""
    rest = name.removeprefix(UNKNOWN_PREFIX)
    if rest == name:
        return None
    try:
        return str(ipaddress.ip_address(rest if "." in rest else rest.replace("-", ":")))
    except ValueError:
        return None


def slug(text: str) -> str | None:
    """A source name made of ``text`` (a device's host name), ``None`` when nothing is left."""
    name = re.sub(r"[^a-z0-9-]+", "-", text.strip().lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)[:63].rstrip("-")
    return name if NAME.fullmatch(name) and not name.startswith(UNKNOWN_PREFIX) else None


@dataclass
class Item:
    """One row of the overview while it is put together."""

    name: str
    type: str
    address: str | None
    configured: bool
    network_of: str | None
    unknown: bool
    enabled: bool
    expected: bool
    heartbeat_minutes: int
    first_seen: datetime | None = None
    loaded: datetime | None = None
    received: datetime | None = None
    members: list[Item] = field(default_factory=list)

    @property
    def last_message(self) -> datetime | None:
        seen = [moment for moment in (self.loaded, self.received) if moment is not None]
        seen += [m.last_message for m in self.members if m.last_message is not None]
        return max(seen) if seen else None


def collector_status(
    layout: Layout, now: datetime
) -> tuple[datetime | None, bool, dict[str, datetime]]:
    """When the collector last wrote its status, whether that is fresh, and per source its last
    receive time (empty unless fresh: a stopped collector says nothing about now)."""
    try:
        status = json.loads(layout.collector_status.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(status["updated_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None, False, {}
    if updated.tzinfo is None:
        return None, False, {}
    fresh = now - updated <= COLLECTOR_STATUS_FRESH
    received: dict[str, datetime] = {}
    if fresh:
        for name, stats in dict(status.get("sources") or {}).items():
            last = stats.get("last_received") if isinstance(stats, dict) else None
            try:
                moment = datetime.fromisoformat(str(last)) if last else None
            except ValueError:
                moment = None
            if moment is not None and moment.tzinfo is not None:
                received[str(name)] = moment.astimezone(UTC)
    return updated, fresh, received


def _items(db: Session, config: Config, received: Mapping[str, datetime]) -> list[Item]:
    """Configured sources and ``sources`` rows, network members under their network."""
    items: dict[str, Item] = {}
    networks: dict[str, SourceConfig] = {}
    for source in config.sources:
        items[source.name] = Item(
            name=source.name,
            type=source.type,
            address=source.address,
            configured=True,
            network_of=None,
            unknown=False,
            enabled=source.enabled,
            expected=source.expected and not source.is_network,
            heartbeat_minutes=source.heartbeat_minutes,
        )
        if source.is_network:
            networks[source.name] = source
    for row in db.scalars(select(Source).order_by(Source.name)):
        item = items.get(row.name)
        if item is None:
            network = _network_of(row.name, networks)
            parent = networks.get(network) if network else None
            item = Item(
                name=row.name,
                type=parent.type if parent else row.type,
                address=None if row.address is None else str(row.address),
                configured=parent is not None,
                network_of=network,
                unknown=row.unknown or row.name.startswith(UNKNOWN_PREFIX),
                enabled=parent.enabled if parent else row.enabled,
                expected=False if parent else row.expected,
                heartbeat_minutes=parent.heartbeat_minutes if parent else row.heartbeat_minutes,
            )
            items[row.name] = item
            if network is not None:
                items[network].members.append(item)
        item.first_seen = row.first_seen
        item.loaded = row.last_seen
    for item in items.values():
        item.received = received.get(item.name)
        if item.members:
            firsts = [m.first_seen for m in item.members if m.first_seen is not None]
            item.first_seen = min(firsts) if firsts else None
    # A network's members follow it.
    return sorted(
        items.values(),
        key=lambda item: (item.network_of or item.name, item.network_of is not None, item.name),
    )


def _network_of(name: str, networks: Mapping[str, SourceConfig]) -> str | None:
    """The configured network ``name`` (``<network>-<ip>``) belongs to."""
    for network, source in networks.items():
        prefix = f"{network}-"
        if name.startswith(prefix):
            rest = name[len(prefix) :]
            try:  # the collector writes an IPv6 address's ``:`` as ``-``
                address = ipaddress.ip_address(rest if "." in rest else rest.replace("-", ":"))
            except ValueError:
                continue
            if address in ipaddress.ip_network(source.address):
                return network
    return None


def state_of(item: Item, config: Config, now: datetime) -> tuple[State, str | None]:
    """The state and, for a promoted unknown sender, the name it is written under now."""
    if item.unknown:
        address = _address_of_unknown(item.name)
        promoted = config.source_name_for(address) if address else None
        return ("promoted", promoted) if promoted else ("unknown", None)
    if not item.enabled:
        return "disabled", None
    last = item.last_message
    if last is None:
        return "never", None
    if now - last <= timedelta(minutes=item.heartbeat_minutes):
        return "active", None
    return "silent", None


def sparklines(db: Session, names: Iterable[str], now: datetime) -> dict[str, list[int]]:
    """Messages per minute (device time) of the 60 whole minutes before ``now``'s, per source."""
    wanted = sorted(set(names))
    end = now.astimezone(UTC).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=SPARK_MINUTES)
    lines = {name: [0] * SPARK_MINUTES for name in wanted}
    if not wanted:
        return lines
    minute = func.date_trunc("minute", Message.time)
    rows = db.execute(
        select(Message.source, minute, func.count())
        .where(Message.source.in_(wanted), Message.time >= start, Message.time < end)
        .group_by(Message.source, minute)
    )
    for source, moment, count in rows:
        index = int((moment.astimezone(UTC) - start).total_seconds() // 60)
        if 0 <= index < SPARK_MINUTES:
            lines[source][index] = int(count)
    return lines


def suggestions(
    db: Session, names: Iterable[str], config: Config, now: datetime
) -> dict[str, dict[str, str]]:
    """``{name, type}`` for unknown senders: the device family of the kinds they sent in the last
    24 hours (else ``generic``) and a name from their most common host name (else
    ``kaynak-<last part of the address>``), never one that is configured already."""
    wanted = sorted(set(names))[:SUGGESTED_MAX]
    if not wanted:
        return {}
    taken = {source.name for source in config.sources}
    kinds: dict[str, set[str]] = {name: set() for name in wanted}
    hosts: dict[str, Counter[str]] = {name: Counter() for name in wanted}
    rows = db.execute(
        select(Message.source, Message.type, Message.subtype, Message.host, func.count())
        .where(
            Message.source.in_(wanted),
            Message.time >= now - SUGGEST_WINDOW,
            Message.time < now + timedelta(minutes=5),
        )
        .group_by(Message.source, Message.type, Message.subtype, Message.host)
    )
    for source, type_, subtype, host, count in rows:
        kinds[source].add(kind_of(str(type_), subtype))
        if host:
            hosts[source][str(host)] += int(count)
    found: dict[str, dict[str, str]] = {}
    for name in wanted:
        family = family_of(kinds[name]) or "generic"
        suggested = None
        for host, _ in hosts[name].most_common():
            candidate = slug(host)
            if candidate is not None and candidate not in taken:
                suggested = candidate
                break
        if suggested is None:
            address = _address_of_unknown(name) or name
            last = re.split(r"[.:]", address)[-1] or "1"
            suggested = slug(f"kaynak-{last}") or "kaynak"
        found[name] = {"name": suggested, "type": family}
    return found


def _today_offsets(db: Session, today: date) -> dict[str, tuple[int, int]]:
    """``source → (bytes loaded, lines loaded)`` of today's files."""
    return {
        source: (int(offset), int(lines))
        for source, offset, lines in db.execute(
            select(IngestOffset.source, IngestOffset.byte_offset, IngestOffset.line_number).where(
                IngestOffset.day == today
            )
        )
    }


def _failures_on(db: Session, today: date) -> dict[str, int]:
    return {
        source: int(total)
        for source, total in db.execute(
            select(ParseFailure.source, func.sum(ParseFailure.count))
            .where(ParseFailure.day == today)
            .group_by(ParseFailure.source)
        )
    }


def _pending_bytes(layout: Layout, name: str, today: date, loaded: int) -> int:
    """Bytes of today's spool file ingest has not loaded yet (0 without a file)."""
    try:
        size = layout.spool_file(name, today).stat().st_size
    except OSError:
        return 0
    return max(0, size - loaded)


def permille(failures: int, lines: int) -> float | None:
    return round(1000 * failures / lines, 1) if lines else None


def overview(db: Session, config: Config, layout: Layout, now: datetime) -> dict[str, Any]:
    """``SourcesOverview`` (see the module documentation)."""
    today = site_day(now)
    updated, fresh, received = collector_status(layout, now)
    items = _items(db, config, received)
    names = [item.name for item in items if not item.members]
    sparks = sparklines(db, names, now)
    offsets = _today_offsets(db, today)
    failures = _failures_on(db, today)
    states = {item.name: state_of(item, config, now) for item in items}
    suggested = suggestions(
        db, [item.name for item in items if states[item.name][0] == "unknown"], config, now
    )
    out: list[dict[str, Any]] = []
    for item in items:
        members = item.members
        parts = [m.name for m in members] if members else [item.name]
        spark = [
            sum(values)
            for values in zip(*(sparks.get(n, [0] * SPARK_MINUTES) for n in parts), strict=True)
        ]
        lines = sum(offsets.get(name, (0, 0))[1] for name in parts)
        failed = sum(failures.get(name, 0) for name in parts)
        pending = sum(
            _pending_bytes(layout, name, today, offsets.get(name, (0, 0))[0]) for name in parts
        )
        state, promoted = states[item.name]
        if members and item.enabled:  # a network: active when a member is
            member_states = {states[m.name][0] for m in members}
            state = "active" if "active" in member_states else "silent"
        last = item.last_message
        origin: str | None = None
        if last is not None:
            collected = [x.received for x in (members or [item]) if x.received is not None]
            origin = "collector" if collected and max(collected) >= last else "database"
        entry = {
            "name": item.name,
            "type": item.type,
            "address": item.address,
            "configured": item.configured,
            "network_of": item.network_of,
            "unknown": item.unknown,
            "enabled": item.enabled,
            "expected": item.expected,
            "heartbeat_minutes": item.heartbeat_minutes,
            "first_seen": None if item.first_seen is None else to_site(item.first_seen),
            "last_message": None if last is None else to_site(last),
            "last_message_origin": origin,
            "state": state,
            "silent_minutes": _minutes(now - last) if state == "silent" and last else None,
            "messages_per_minute": round(sum(spark) / SPARK_MINUTES, 1),
            "sparkline": spark,
            "lines_today": lines,
            "pending_bytes": pending,
            "parse_failures_today": failed,
            "parse_failure_permille": permille(failed, lines),
            "promoted_to": promoted,
            "suggested": suggested.get(item.name),
        }
        out.append(entry)
    return {
        "generated_at": to_site(now),
        "today": today,
        "collector": {"updated_at": None if updated is None else to_site(updated), "fresh": fresh},
        "items": out,
    }


# -- the detail of one source ---------------------------------------------------------------------


class ManifestCache:
    """Parsed manifests by path, size and modification time (least recently used dropped)."""

    def __init__(self, size: int = MANIFESTS_KEPT) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, int, int], dict[str, Any] | None] = OrderedDict()

    def get(self, path: Path) -> dict[str, Any] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            data = None
        parsed = data if isinstance(data, dict) else None
        with self._lock:
            self._entries[key] = parsed
            while len(self._entries) > self._size:
                self._entries.popitem(last=False)
        return parsed


MANIFESTS: Final = ManifestCache()


def known(db: Session, config: Config, name: str) -> bool:
    """A configured source (exact or network) or a ``sources`` row."""
    if any(source.name == name for source in config.sources):
        return True
    return db.scalar(select(Source.name).where(Source.name == name)) is not None


def hourly(db: Session, names: list[str], now: datetime) -> list[dict[str, Any]]:
    """Lines per hour of the last 48 hours (the current one included), oldest first: from
    ``search_counts`` while it is complete for the range, else counted from ``messages``."""
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = end - timedelta(hours=HOURLY_HOURS)
    counts: dict[datetime, int] = {}
    if counts_complete(db.connection(), start, end):
        rows = db.execute(
            select(SearchCount.hour, func.sum(SearchCount.n))
            .where(SearchCount.source.in_(names), SearchCount.hour >= start, SearchCount.hour < end)
            .group_by(SearchCount.hour)
        )
    else:
        hour = func.date_trunc("hour", Message.time)
        rows = db.execute(
            select(hour, func.count())
            .where(Message.source.in_(names), Message.time >= start, Message.time < end)
            .group_by(hour)
        )
    for moment, count in rows:
        counts[moment.astimezone(UTC)] = counts.get(moment.astimezone(UTC), 0) + int(count)
    return [
        {"hour": to_site(at), "count": counts.get(at, 0)}
        for at in (start + timedelta(hours=n) for n in range(HOURLY_HOURS))
    ]


def daily(
    db: Session, layout: Layout, config: Config, names: list[str], now: datetime
) -> list[dict[str, Any]]:
    """Lines, parse failures and seal of the last 14 days (today first)."""
    today = site_day(now)
    first = today - timedelta(days=DAILY_DAYS - 1)
    lines: Counter[date] = Counter()
    failures: Counter[date] = Counter()
    for day, count in db.execute(
        select(IngestOffset.day, func.sum(IngestOffset.line_number))
        .where(IngestOffset.source.in_(names), IngestOffset.day >= first)
        .group_by(IngestOffset.day)
    ):
        lines[day] = int(count)
    for day, count in db.execute(
        select(ParseFailure.day, func.sum(ParseFailure.count))
        .where(ParseFailure.source.in_(names), ParseFailure.day >= first)
        .group_by(ParseFailure.day)
    ):
        failures[day] = int(count)
    view = VaultView(layout, config, now=now)
    return [
        {
            "day": day,
            "lines": lines[day],
            "failures": failures[day],
            "sealed": day < today and view.is_sealed(day),
        }
        for day in (today - timedelta(days=n) for n in range(DAILY_DAYS))
    ]


def gaps(
    layout: Layout, config: Config, names: list[str], last: datetime | None, now: datetime
) -> list[dict[str, Any]]:
    """Gaps of the last 30 days: those the sealed manifests record for the source, newest first,
    and today's open one (no message for longer than ``[seal] gap_minutes``)."""
    today = site_day(now)
    found: list[dict[str, Any]] = []
    limit = timedelta(minutes=config.seal.gap_minutes)
    if (
        last is not None
        and now - last > limit
        and site_day(last) >= today - timedelta(days=GAP_DAYS)
    ):
        found.append(
            {
                "from": to_site(last),
                "to": to_site(now),
                "minutes": _minutes(now - last),
                "reason": "no messages",
                "origin": "live",
            }
        )
    wanted = set(names)
    for day in (today - timedelta(days=n) for n in range(1, GAP_DAYS + 1)):
        manifest = MANIFESTS.get(layout.manifest(day))
        if manifest is None or manifest.get("day") != day.isoformat():
            continue
        for gap in reversed(manifest.get("gaps") or []):
            if not isinstance(gap, dict) or gap.get("source") not in wanted:
                continue
            try:
                start = datetime.fromisoformat(str(gap["from"]))
                end = datetime.fromisoformat(str(gap["to"]))
            except (KeyError, ValueError):
                continue
            found.append(
                {
                    "from": to_site(start),
                    "to": to_site(end),
                    "minutes": _minutes(end - start),
                    "reason": str(gap.get("reason") or ""),
                    "origin": "manifest",
                }
            )
    return found


def failures(
    db: Session, names: list[str], now: datetime, *, with_errors: bool
) -> list[dict[str, Any]]:
    """The 50 most frequent parse-failure patterns of the last 7 days with an example line.

    ``error`` (the parser's detail, which can echo a value of the line) only ``with_errors``.
    """
    first = site_day(now) - timedelta(days=FAILURE_DAYS - 1)
    rows = db.scalars(
        select(ParseFailure)
        .where(ParseFailure.source.in_(names), ParseFailure.day >= first)
        .order_by(ParseFailure.count.desc(), ParseFailure.day.desc(), ParseFailure.pattern)
        .limit(FAILURES_SHOWN)
    )
    return [
        {
            "day": row.day,
            "pattern": row.pattern,
            "count": int(row.count),
            "first_seen": to_site(row.first_seen),
            "last_seen": to_site(row.last_seen),
            "error": row.error if with_errors else None,
            "example": {
                "source": row.source,
                "day": row.day,
                "line": row.example_line,
                "offset": row.example_offset,
            },
        }
        for row in rows
    ]


def detail(
    db: Session, config: Config, layout: Layout, name: str, now: datetime, *, with_errors: bool
) -> dict[str, Any] | None:
    """``SourceDetail`` (``None``: no such source)."""
    if not known(db, config, name):
        return None
    summary = overview(db, config, layout, now)
    entry = next((item for item in summary["items"] if item["name"] == name), None)
    if entry is None:  # pragma: no cover - known() and the overview read the same rows
        return None
    names = [item["name"] for item in summary["items"] if item["network_of"] == name] or [name]
    # Only an expected source has gaps (the sealer's rule); a live one while it is silent.
    open_since = entry["last_message"] if entry["expected"] and entry["enabled"] else None
    return {
        **entry,
        "hourly": hourly(db, names, now),
        "daily": daily(db, layout, config, names, now),
        "gaps": gaps(layout, config, [name, *names], open_since, now),
        "parse_failures": failures(db, names, now, with_errors=with_errors),
    }


def spool_day(layout: Layout, name: str, today: date) -> date | None:
    """The day of the newest spool file of ``name``: today's, else yesterday's (``None``:
    neither; the vault is never decompressed for this)."""
    for day in (today, today - timedelta(days=1)):
        if regular_file(layout.spool_file(name, day)):
            return day
    return None
