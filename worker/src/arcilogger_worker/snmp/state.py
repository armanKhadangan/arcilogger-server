"""``state/wan-rates.json`` (Wave 5 contract §6, N4): the internet line's rates, no database table.

Written atomically by the worker's ``snmp.poll`` job, read by ``GET /activity/wan``. Per source
and interface it keeps the last reads (to go on after a restart), the rates of the last
:data:`SAMPLES_KEEP` at full resolution and the last :data:`MINUTES_KEEP` as 1-minute averages,
the ``status`` of the last poll and ``last_error_at``. Rates are bits per second. It holds no
secret and no person data. A file that cannot be read starts again empty: the rates are a live
view, not evidence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, get_args

from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.snmp import Status
from arcilogger_core.timeutil import isoformat_site

log = logging.getLogger(__name__)

SCHEMA: Final = "arcilogger.wan-rates/1"
SAMPLES_KEEP: Final = timedelta(minutes=60)
MINUTES_KEEP: Final = timedelta(hours=24)
STATUSES: Final = frozenset(get_args(Status))


def _time(value: datetime) -> str:
    return isoformat_site(value, "milliseconds")


def _parse(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


@dataclass
class Counters:
    """The last good read of an interface: the starting point of the next rate."""

    octets_in: int
    octets_out: int
    at: datetime
    #: ``sysUpTime`` of the same answer (the rate's Δt).
    uptime: int | None = None


@dataclass
class Line:
    """One WAN interface of a source (``ifName``)."""

    name: str
    if_index: int | None = None
    oper_status: str | None = None
    #: ``ifHighSpeed``: the interface's speed in Mbit/s as the device reports it.
    high_speed_mbps: int | None = None
    #: (down, up) in Mbit/s, as configured (``capacity_mbps``).
    capacity: tuple[float, float] | None = None
    status: Status = "ok"
    last_error_at: datetime | None = None
    #: The newest good read of the counters.
    updated_at: datetime | None = None
    #: When the line was first polled since it appeared or SNMP was switched on: the alert's
    #: clock while it has had no good read since.
    watched_since: datetime | None = None
    counters: Counters | None = None
    #: (time, download bit/s, upload bit/s), oldest first.
    samples: list[tuple[datetime, float, float]] = field(default_factory=list)
    #: (minute, download, upload, samples averaged), oldest first.
    minutes: list[tuple[datetime, float, float, int]] = field(default_factory=list)

    def add(self, at: datetime, download: float, upload: float) -> None:
        """One rate: kept as it is, and in the average of its minute."""
        download, upload = round(download, 1), round(upload, 1)
        self.samples.append((at, download, upload))
        minute = at.replace(second=0, microsecond=0)
        if self.minutes and self.minutes[-1][0] == minute:
            _, down, up, count = self.minutes[-1]
            self.minutes[-1] = (
                minute,
                round((down * count + download) / (count + 1), 1),
                round((up * count + upload) / (count + 1), 1),
                count + 1,
            )
        else:
            self.minutes.append((minute, download, upload, 1))

    def prune(self, now: datetime) -> None:
        self.samples = [sample for sample in self.samples if now - sample[0] <= SAMPLES_KEEP]
        self.minutes = [minute for minute in self.minutes if now - minute[0] <= MINUTES_KEEP]

    def to_json(self) -> dict[str, Any]:
        counters = self.counters
        return {
            "if_index": self.if_index,
            "oper_status": self.oper_status,
            "high_speed_mbps": self.high_speed_mbps,
            "capacity_mbps": (
                {"down": self.capacity[0], "up": self.capacity[1]} if self.capacity else None
            ),
            "status": self.status,
            "last_error_at": _time(self.last_error_at) if self.last_error_at else None,
            "updated_at": _time(self.updated_at) if self.updated_at else None,
            "watched_since": _time(self.watched_since) if self.watched_since else None,
            "counters": (
                {
                    "in": counters.octets_in,
                    "out": counters.octets_out,
                    "at": _time(counters.at),
                    "uptime": counters.uptime,
                }
                if counters
                else None
            ),
            "samples": [[_time(at), down, up] for at, down, up in self.samples],
            "minutes": [[_time(at), down, up, n] for at, down, up, n in self.minutes],
        }

    @classmethod
    def from_json(cls, name: str, data: dict[str, Any]) -> Line:
        counters = data.get("counters")
        capacity = data.get("capacity_mbps")
        status: Status = data["status"] if data.get("status") in STATUSES else "timeout"
        line = cls(
            name=name,
            if_index=data.get("if_index"),
            oper_status=data.get("oper_status"),
            high_speed_mbps=data.get("high_speed_mbps"),
            capacity=(float(capacity["down"]), float(capacity["up"])) if capacity else None,
            status=status,
            last_error_at=_parse(data.get("last_error_at")),
            updated_at=_parse(data.get("updated_at")),
            watched_since=_parse(data.get("watched_since")),
        )
        if counters:
            at = _parse(counters.get("at"))
            if at is not None:
                uptime = counters.get("uptime")
                line.counters = Counters(
                    int(counters["in"]),
                    int(counters["out"]),
                    at,
                    uptime if isinstance(uptime, int) else None,
                )
        for at, down, up in data.get("samples") or []:
            line.samples.append((datetime.fromisoformat(at), float(down), float(up)))
        for at, down, up, count in data.get("minutes") or []:
            line.minutes.append((datetime.fromisoformat(at), float(down), float(up), int(count)))
        return line


@dataclass
class SourceState:
    name: str
    #: ``sysUpTime`` of the last answer, in hundredths of a second.
    uptime: int | None = None
    polled_at: datetime | None = None
    #: The first poll since SNMP was switched on for the source (``None`` while it is off).
    watched_since: datetime | None = None
    lines: dict[str, Line] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "uptime": self.uptime,
            "polled_at": _time(self.polled_at) if self.polled_at else None,
            "watched_since": _time(self.watched_since) if self.watched_since else None,
            "lines": {name: line.to_json() for name, line in self.lines.items()},
        }

    @classmethod
    def from_json(cls, name: str, data: dict[str, Any]) -> SourceState:
        return cls(
            name=name,
            uptime=data.get("uptime"),
            polled_at=_parse(data.get("polled_at")),
            watched_since=_parse(data.get("watched_since")),
            lines={
                line: Line.from_json(line, value)
                for line, value in (data.get("lines") or {}).items()
            },
        )


@dataclass
class WanState:
    sources: dict[str, SourceState] = field(default_factory=dict)
    written_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "written_at": _time(self.written_at) if self.written_at else None,
            "sources": {name: source.to_json() for name, source in self.sources.items()},
        }


def read_state(path: Path) -> WanState:
    """The state file; an empty state when it is missing or cannot be read."""
    try:
        data = json.loads(path.read_bytes())
        if data.get("schema") != SCHEMA:
            raise ValueError(f"unknown schema {data.get('schema')!r}")
        return WanState(
            sources={
                name: SourceState.from_json(name, value)
                for name, value in (data.get("sources") or {}).items()
            },
            written_at=_parse(data.get("written_at")),
        )
    except FileNotFoundError:
        return WanState()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        log.warning("%s cannot be read (%s); starting again empty", path.name, exc)
        return WanState()


def write_state(path: Path, state: WanState, now: datetime) -> None:
    """Prune every line to its windows and write the file atomically."""
    for source in state.sources.values():
        for line in source.lines.values():
            line.prune(now)
    state.written_at = now
    # compact: rewritten at every poll and read whole by every GET /activity/wan
    text = json.dumps(state.to_json(), ensure_ascii=False, separators=(",", ":"))
    atomic_write_text(path, text + "\n")
