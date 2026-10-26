"""The collector service: syslog in, raw spool lines out.

Receive path for every message (UDP datagram or TCP frame):

1. take the receive time from the ARCILOGGER clock (UTC, written as Europe/Istanbul);
2. map the sender IP to a configured source name — unknown senders go to ``unknown-<ip>`` and
   raise a "new log source" alert once, or are only counted when ``unknown_senders = "drop"``;
   a message that arrives over a transport its source does not allow (``[[sources]] transport
   = "tcp"``: a UDP datagram claiming that address, which any host in the LAN can forge) is
   dropped, counted per address (``dropped_transport``) and alerted once (``wrong-transport``);
3. append the line to the spool file. Nothing else (parsing, database) happens here.

A failed write (disk full, I/O error) never escapes the receive path — a TCP connection stays
open — and never glues two messages into one line (:mod:`.spool`). Write failures are tracked
**per source** (the files differ; on a nearly full disk short lines may still fit while long
ones do not, and a sharing or permission error hits one file): the lost messages are counted
(``write_failures``), the source's first failure raises one ``R20-spool`` alert and its recovery
another, and its repeated errors are logged at most once a minute. A source counts as recovered
only after a good write that comes :data:`RECOVERED_AFTER` after its last failure, so a source
whose writes alternate between failing and succeeding stays in one incident (two alerts, not two
per message). Alerts are best effort: the alert and status files live on the same disk, so
during the outage the service log may be the only record. ``spool_write_error`` in the status
file stays set while any source is failing (``spool_write_errors`` lists them). A spool file
whose last line was left unterminated (power cut, crash, full disk) gets a ``\\n`` appended
before the next message (``R20-spool-torn`` alert, ``torn_lines`` in the status file); on start
the collector does this for every spool file of the current day.

A maintenance task fsyncs the spool every ``fsync_interval_seconds``, closes the files of
finished days after midnight and writes ``state/collector-status.json`` for the dashboard and
the "log source silent" rule.

The collector is never restarted for a configuration change (ADR-0014): the same task watches
``arcilogger.toml`` (:class:`~arcilogger_core.configwatch.ConfigWatcher`) and takes a changed
``[[sources]]`` within a few seconds, between two messages (one event loop, no lock). A file
that does not load keeps the running sources; a change of ``[collector]`` or ``data_dir`` (a
hand edit) is only reported. The status file's ``config`` block names the SHA-256 of the file
the sources came from: the browser administration widens the firewall for a new sender only
once that SHA is reported, so its first datagram already finds its source.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from arcilogger_collector import __version__
from arcilogger_collector.framing import FrameDecoder, FramingError
from arcilogger_collector.spool import SpoolWriter, TornLine
from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.config import Config
from arcilogger_core.configwatch import (
    COLLECTOR_HOT,
    COLLECTOR_RESTART,
    ConfigWatch,
    ConfigWatcher,
)
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day, utc_now

log = logging.getLogger("arcilogger.collector")

Clock = Callable[[], datetime]
Transport = Literal["udp", "tcp"]

#: Repeated write or maintenance errors are logged at most this often.
LOG_EVERY: Final = timedelta(seconds=60)
#: Torn-line events listed in the status file (the newest; ``torn_lines_total`` counts all).
MAX_TORN_IN_STATUS: Final = 50
#: A source whose spool writes failed counts as recovered at its first good write this long
#: after its last failure (until then good writes and failures belong to the same incident).
RECOVERED_AFTER: Final = timedelta(seconds=60)
#: ``arcilogger.toml`` is looked at at most this often (a stat; it is read when it changed), so
#: a new source is taken within this plus ``fsync_interval_seconds`` (contract: 5 s).
RELOAD_SECONDS: Final = 2.0


def unknown_source_name(address: str) -> str:
    """File-system safe name for a sender that is not configured (IPv6 ':' is not allowed)."""
    return "unknown-" + address.replace(":", "-").replace("%", "-")


@dataclass
class SourceStats:
    messages: int = 0
    bytes: int = 0
    last_received: datetime | None = None


@dataclass
class WriteOutage:
    """Messages of one source that could not be written since ``first`` (cleared by a good
    write :data:`RECOVERED_AFTER` after ``last``)."""

    first: datetime
    last: datetime
    error: str
    messages: int = 0
    #: Messages of the source that were written meanwhile (a partly failing disk or file).
    written: int = 0
    #: The newest of those good writes.
    last_written: datetime | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "since": isoformat_site(self.first),
            "last": isoformat_site(self.last),
            "messages": self.messages,
            "written_meanwhile": self.written,
            "error": self.error,
        }


@dataclass
class CollectorStats:
    started_at: datetime
    sources: dict[str, SourceStats] = field(default_factory=dict)
    dropped_unknown: dict[str, int] = field(default_factory=dict)
    #: Messages over a transport the source does not allow, per sender address.
    dropped_transport: dict[str, int] = field(default_factory=dict)
    #: Messages that could not be written to the spool (disk full, I/O error), per source.
    write_failures: dict[str, int] = field(default_factory=dict)
    #: The write failures going on now, per source (empty while the spool is written normally).
    outages: dict[str, WriteOutage] = field(default_factory=dict)
    #: Unterminated last lines that were terminated with ``\\n`` (newest last).
    torn_lines: list[dict[str, Any]] = field(default_factory=list)
    torn_lines_total: int = 0
    tcp_connections: int = 0
    tcp_framing_errors: int = 0

    def write_error(self) -> dict[str, Any] | None:
        """All sources' ongoing write failures in one (``None`` when none is failing)."""
        if not self.outages:
            return None
        outages = sorted(self.outages.items(), key=lambda item: (item[1].last, item[0]))
        return {
            "since": isoformat_site(min(o.first for _, o in outages)),
            "last": isoformat_site(outages[-1][1].last),
            "messages": sum(o.messages for _, o in outages),
            "error": outages[-1][1].error,
            "sources": sorted(name for name, _ in outages),
        }

    def as_json(self) -> dict[str, Any]:
        return {
            "started_at": isoformat_site(self.started_at, "seconds"),
            "updated_at": isoformat_site(utc_now(), "seconds"),
            "version": __version__,
            "sources": {
                name: {
                    "messages": stats.messages,
                    "bytes": stats.bytes,
                    "last_received": (
                        isoformat_site(stats.last_received) if stats.last_received else None
                    ),
                }
                for name, stats in sorted(self.sources.items())
            },
            "dropped_unknown": dict(sorted(self.dropped_unknown.items())),
            "dropped_transport": dict(sorted(self.dropped_transport.items())),
            "write_failures": dict(sorted(self.write_failures.items())),
            "spool_write_error": self.write_error(),
            "spool_write_errors": {
                name: outage.as_json() for name, outage in sorted(self.outages.items())
            },
            "torn_lines_total": self.torn_lines_total,
            "torn_lines": list(self.torn_lines),
            "tcp_connections": self.tcp_connections,
            "tcp_framing_errors": self.tcp_framing_errors,
        }


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, collector: Collector) -> None:
        self._collector = collector

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        self._collector.handle_message(str(addr[0]), data, "udp")

    def error_received(self, exc: Exception) -> None:
        log.warning("UDP receive error: %s", exc)


class _TcpProtocol(asyncio.Protocol):
    def __init__(self, collector: Collector) -> None:
        self._collector = collector
        self._decoder = FrameDecoder(collector.config.collector.max_message_bytes)
        self._peer = "?"
        self._transport: asyncio.Transport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if not isinstance(transport, asyncio.Transport):  # pragma: no cover - asyncio contract
            raise TypeError("stream transport expected")
        self._transport = transport
        peer = transport.get_extra_info("peername")
        self._peer = str(peer[0]) if peer else "?"
        self._collector.stats.tcp_connections += 1

    def data_received(self, data: bytes) -> None:
        try:
            messages = self._decoder.feed(data)
        except FramingError as exc:
            self._collector.stats.tcp_framing_errors += 1
            log.warning("closing TCP connection from %s: %s", self._peer, exc)
            if self._transport is not None:
                self._transport.close()
            return
        for message in messages:
            self._collector.handle_message(self._peer, message, "tcp")

    def connection_lost(self, exc: Exception | None) -> None:
        rest = self._decoder.pending()
        if rest.strip():
            self._collector.handle_message(self._peer, rest, "tcp")


class Collector:
    """``config_path``: the file ``config`` was loaded from, watched for new ``[[sources]]``
    (None: no live reload, e.g. a test with a configuration of its own)."""

    def __init__(
        self,
        config: Config,
        clock: Clock = utc_now,
        *,
        config_path: Path | None = None,
        reload_seconds: float = RELOAD_SECONDS,
    ) -> None:
        self.config = config
        self.layout = Layout(config.data_dir)
        self._clock = clock
        self._writer = SpoolWriter(self.layout, on_torn=self._torn_line)
        self._alerted_unknown: set[str] = set()
        self._alerted_transport: set[str] = set()
        self._last_log: dict[str, datetime] = {}
        self.stats = CollectorStats(started_at=clock())
        self._udp: asyncio.DatagramTransport | None = None
        self._tcp: asyncio.Server | None = None
        self._maintenance: asyncio.Task[None] | None = None
        self._watcher = (
            None
            if config_path is None
            else ConfigWatcher(
                config_path,
                config,
                hot=COLLECTOR_HOT,
                restart=COLLECTOR_RESTART,
                min_interval=reload_seconds,
            )
        )
        self.config_watch: ConfigWatch | None = None

    # -- receive path -------------------------------------------------------------------------

    def resolve(self, address: str) -> str | None:
        name = self.config.source_name_for(address)
        if name is not None:
            return name
        if self.config.collector.unknown_senders == "drop":
            return None
        return unknown_source_name(address)

    def handle_message(self, address: str, message: bytes, transport: Transport = "udp") -> None:
        """Write one received message to the spool (never raises for a spool write error)."""
        received = self._clock()
        source = self.config.source_config_for(address)
        if source is not None and source.transport not in ("any", transport):
            self.stats.dropped_transport[address] = self.stats.dropped_transport.get(address, 0) + 1
            self._alert_transport(address, source.name, source.transport, transport)
            return
        name = self.resolve(address)
        if name is None:
            self.stats.dropped_unknown[address] = self.stats.dropped_unknown.get(address, 0) + 1
            self._alert_unknown(address, written=False)
            return
        try:
            self._writer.write(name, received, address, message)
        except OSError as exc:
            self._write_failed(name, received, exc)
            return
        if name in self.stats.outages:
            self._written_during_outage(name, received)
        stats = self.stats.sources.setdefault(name, SourceStats())
        stats.messages += 1
        stats.bytes += len(message)
        stats.last_received = received
        if name.startswith("unknown-"):
            self._alert_unknown(address, written=True)

    def _alert_unknown(self, address: str, *, written: bool) -> None:
        if address in self._alerted_unknown:
            return
        self._alerted_unknown.add(address)
        action = "kaydediliyor" if written else "kaydedilmiyor (drop)"
        self._alert(
            "new-log-source",
            "medium",
            f"Yeni log kaynağı: {address} ({action})",
            {"address": address, "written": written},
        )

    def _alert_transport(self, address: str, name: str, allowed: str, transport: str) -> None:
        if address in self._alerted_transport:
            return
        self._alerted_transport.add(address)
        log.warning(
            "dropping %s messages from %s: source %s accepts only %s",
            transport,
            address,
            name,
            allowed,
        )
        self._alert(
            "wrong-transport",
            "high",
            f"{address} adresinden {transport.upper()} ile mesaj geldi, ancak {name} kaynağı "
            f"yalnızca {allowed.upper()} kabul ediyor; mesaj kaydedilmedi (sonrakiler sayılır). "
            "Adresi taklit edilmiş (sahte) bir gönderim veya yanlış cihaz ayarı olabilir.",
            {"address": address, "source": name, "allowed": allowed, "transport": transport},
        )

    # -- spool write failures -------------------------------------------------------------------

    def _write_failed(self, name: str, at: datetime, exc: OSError) -> None:
        self.stats.write_failures[name] = self.stats.write_failures.get(name, 0) + 1
        outage = self.stats.outages.get(name)
        key = f"write:{name}"
        if outage is None:
            outage = self.stats.outages[name] = WriteOutage(first=at, last=at, error=str(exc))
            self._last_log[key] = at
            log.error(
                "raw spool of %s cannot be written (%s); its received messages are being lost",
                name,
                exc,
            )
            self._alert(
                "R20-spool",
                "critical",
                f"Ham log yazılamıyor: {name} ({exc}); bu kaynaktan gelen mesajlar "
                "kaydedilemiyor. Veri diskini (boş alan, donanım) ve dosya erişimini hemen "
                "kontrol edin.",
                {"source": name, "since": isoformat_site(at), "error": str(exc)},
            )
        outage.last = at
        outage.error = str(exc)
        outage.messages += 1
        if self._due(key, at):
            log.error(
                "raw spool of %s still cannot be written (%s): %d messages lost since %s",
                name,
                exc,
                outage.messages,
                isoformat_site(outage.first),
            )

    def _written_during_outage(self, name: str, at: datetime) -> None:
        """A good write of a failing source: recovered once its last failure is old enough."""
        outage = self.stats.outages[name]
        outage.written += 1
        outage.last_written = at
        self._recover_if_quiet(name, at)

    def check_recoveries(self, now: datetime) -> None:
        """Close the incidents of sources that were written after their last failure and have
        not failed for :data:`RECOVERED_AFTER` (a quiet source is not left "failing")."""
        for name, outage in list(self.stats.outages.items()):
            if outage.last_written is not None and outage.last_written > outage.last:
                self._recover_if_quiet(name, now)

    def _recover_if_quiet(self, name: str, at: datetime) -> None:
        outage = self.stats.outages[name]
        if at - outage.last < RECOVERED_AFTER:
            return
        del self.stats.outages[name]
        self._last_log.pop(f"write:{name}", None)
        first, last = isoformat_site(outage.first), isoformat_site(outage.last)
        log.warning(
            "raw spool of %s is written again: %d messages between %s and %s could not be written",
            name,
            outage.messages,
            first,
            last,
        )
        self._alert(
            "R20-spool",
            "critical",
            f"Ham log yazılamadı: {name} kaynağından {outage.messages} mesaj kaydedilemedi "
            f"({isoformat_site(outage.first, 'seconds')} ile "
            f"{isoformat_site(outage.last, 'seconds')} arası). Yazma yeniden çalışıyor.",
            {
                "source": name,
                "messages": outage.messages,
                "written_meanwhile": outage.written,
                "first": first,
                "last": last,
                "recovered": isoformat_site(at),
                "error": outage.error,
            },
        )

    def _torn_line(self, torn: TornLine) -> None:
        day = torn.day.isoformat()
        self.stats.torn_lines_total += 1
        self.stats.torn_lines.append(
            {
                "source": torn.source,
                "day": day,
                "offset": torn.offset,
                "terminated_at": isoformat_site(utc_now()),
            }
        )
        del self.stats.torn_lines[:-MAX_TORN_IN_STATUS]
        self._alert(
            "R20-spool-torn",
            "medium",
            f"Ham log dosyasında yarım kalmış son satır kapatıldı: {torn.source} {day}, bayt "
            f"{torn.offset} (elektrik kesintisi, çökme veya dolu disk). Mevcut baytlar "
            "değiştirilmedi; yarım satır tek bir bozuk satır olarak kalır.",
            {"source": torn.source, "day": day, "offset": torn.offset},
        )

    def _due(self, what: str, at: datetime) -> bool:
        """Rate limit of repeated error logs: at most once per :data:`LOG_EVERY`."""
        last = self._last_log.get(what)
        if last is not None and abs(at - last) < LOG_EVERY:
            return False
        self._last_log[what] = at
        return True

    def _alert(self, rule: str, severity: Severity, message: str, details: dict[str, Any]) -> None:
        """Raise an alert; the alert file shares the data disk with the spool, so this is best
        effort (a failure is logged, never raised into the receive path)."""
        try:
            raise_alert(self.layout.alerts, rule, severity, message, details)
        except OSError as exc:
            log.error("could not write alert %s (%s): %s", rule, exc, message)

    # -- lifecycle ------------------------------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        settings = self.config.collector
        # A power cut or crash may have left a partial last line in today's files.
        self._writer.terminate_torn_lines(site_day(self.stats.started_at))
        if settings.udp_port is not None:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self), local_addr=(settings.bind, settings.udp_port)
            )
            self._udp = transport
            sock = transport.get_extra_info("socket")
            if sock is not None:
                try:
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_RCVBUF, settings.udp_receive_buffer_bytes
                    )
                except OSError as exc:  # pragma: no cover - platform limits
                    log.warning("could not enlarge the UDP receive buffer: %s", exc)
        if settings.tcp_port is not None:
            self._tcp = await loop.create_server(
                lambda: _TcpProtocol(self), host=settings.bind, port=settings.tcp_port
            )
        self._maintenance = asyncio.create_task(self._maintain())
        log.info(
            "collector %s listening (udp=%s tcp=%s)",
            __version__,
            self.udp_address,
            self.tcp_address,
        )

    async def stop(self) -> None:
        if self._maintenance is not None:
            self._maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance
        if self._udp is not None:
            self._udp.close()
        if self._tcp is not None:
            self._tcp.close()
            await self._tcp.wait_closed()
        try:
            self._writer.close()
        except OSError:
            log.exception("could not fsync the spool files on stop")
        try:
            self.write_status()
        except OSError:
            log.exception("could not write the collector status on stop")

    @property
    def udp_address(self) -> tuple[str, int] | None:
        if self._udp is None:
            return None
        host, port = self._udp.get_extra_info("sockname")[:2]
        return str(host), int(port)

    @property
    def tcp_address(self) -> tuple[str, int] | None:
        if self._tcp is None or not self._tcp.sockets:
            return None
        host, port = self._tcp.sockets[0].getsockname()[:2]
        return str(host), int(port)

    # -- maintenance ----------------------------------------------------------------------------

    def maintain_once(self) -> None:
        """fsync, close finished days, close write incidents that are over. Called every fsync
        interval (and directly by tests)."""
        now = self._clock()
        self.check_recoveries(now)
        self._writer.flush()
        self._writer.close_stale(site_day(now))

    def reload_config(self) -> bool:
        """Take a changed ``[[sources]]`` of the configuration file (never raises). True when
        the status file should be written now: what the collector reports about its
        configuration changed (a new file, an error, a key that waits for a restart)."""
        if self._watcher is None:
            return False
        watch = self._watcher.poll()
        before, self.config_watch = self.config_watch, watch
        if watch.changed:
            self.config = watch.config
            log.info(
                "sources reloaded (%d configured, configuration %s)",
                len(watch.config.sources),
                (watch.sha256 or "?")[:12],
            )
        return before is None or before.status_block() != watch.status_block()

    def write_status(self) -> None:
        status = self.stats.as_json()
        if self.config_watch is not None:
            status["config"] = self.config_watch.status_block()
        atomic_write_text(
            self.layout.collector_status,
            json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        )

    async def _maintain(self) -> None:
        settings = self.config.collector
        loop = asyncio.get_running_loop()
        next_status = loop.time()
        while True:
            await asyncio.sleep(settings.fsync_interval_seconds)
            try:
                self.maintain_once()
                if self.reload_config() or loop.time() >= next_status:
                    self.write_status()
                    next_status = loop.time() + settings.status_interval_seconds
            except OSError:
                # Every interval while the disk is full: the first error, then once a minute.
                if self._due("maintenance", utc_now()):
                    log.exception("spool maintenance failed")


async def run(
    config: Config, stop: asyncio.Event | None = None, *, config_path: Path | None = None
) -> None:
    """Run until ``stop`` is set (or the task is cancelled, e.g. Ctrl+C / service stop),
    reloading ``[[sources]]`` from ``config_path`` when it changes."""
    collector = Collector(config, config_path=config_path)
    await collector.start()
    try:
        await (stop or asyncio.Event()).wait()
    finally:
        await collector.stop()
