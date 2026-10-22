"""Test and benchmark helpers for the ingest service (fixtures in ``ingest/tests/conftest.py``).

* :class:`World` — a data directory with spool files written exactly as the collector writes
  them, a movable clock, and the **real** sealer with the mock time-stamp authority, so tests
  can seal a day (which deletes its spool files) and watch ingest continue from the vault.
* :class:`TcpProxy` — a TCP forwarder in front of PostgreSQL that can go "down" (drops every
  connection and refuses new ones) and come back: a database outage without stopping the server.
* Line generators for realistic traffic (FortiGate traffic lines from the format samples,
  synthetic MikroTik DHCP lines) and checks that every spool line was loaded exactly once.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from time import perf_counter, process_time, sleep
from typing import Any, Final

from sqlalchemy import Connection, Engine, make_url, text

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.seal import Sealer
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_ingest.service import IngestService

#: FortiGate format samples, fictional values (repository checkout only).
FORMAT_FIXTURES: Final = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "format"
#: The day of the format samples: their device times are plausible for lines received on it.
DAY: Final = date(2026, 9, 23)

SOURCES: Final[list[dict[str, Any]]] = [
    {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1", "heartbeat_minutes": 10},
    {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
    {"name": "ap", "type": "generic", "address": "172.20.10.0/24", "expected": False},
]
SENDERS: Final = {"fortigate": "172.31.255.1", "core-r1": "172.20.10.1"}


@cache
def fortigate_samples() -> tuple[bytes, ...]:
    path = FORMAT_FIXTURES / "fortigate-sample.log"
    return tuple(path.read_bytes().splitlines())


def fortigate_message(index: int) -> bytes:
    """One of the FortiGate format-sample lines, in rotation."""
    samples = fortigate_samples()
    return samples[index % len(samples)]


def dhcp_message(index: int) -> bytes:
    """Synthetic MikroTik DHCP line (wire format to be verified on site, like the parser tests)."""
    host = 100 + index % 150
    action = "assigned" if index % 2 == 0 else "deassigned"
    return (
        f"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 dhcp,info dhcp60 {action} "
        f"172.20.60.{host} for 02:00:5E:00:{index % 256:02X}:{host % 256:02X} NB-{host}"
    ).encode()


def make_config(root: Path, **overrides: Any) -> Config:
    data: dict[str, Any] = {
        "data_dir": str(root / "data"),
        "secrets_dir": str(root / "secrets"),
        "sources": SOURCES,
        "seal": {"quiet_seconds": 0, "gzip_level": 6},
        "tsa": {"provider": "mock"},
        "ingest": {"batch_rows": 500, "batch_seconds": 0.05, "poll_seconds": 0.02},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return Config.model_validate(data)


@dataclass
class World:
    root: Path
    config: Config
    tsa: MockTsa
    now: datetime
    #: Every line written: (source, day) -> [(line number, offset, line bytes)]
    written: dict[tuple[str, date], list[tuple[int, int, bytes]]] = field(default_factory=dict)

    @classmethod
    def create(cls, root: Path, **overrides: Any) -> World:
        config = make_config(root, **overrides)
        tsa = MockTsa(Layout(config.data_dir).state / "mock-tsa", key_type="ec")
        world = cls(root=root, config=config, tsa=tsa, now=datetime.now(UTC))
        world.set_today(DAY, hh=18)
        return world

    @property
    def layout(self) -> Layout:
        return Layout(self.config.data_dir)

    def clock(self) -> datetime:
        return self.now

    def set_today(self, day: date, hh: int = 12) -> None:
        self.now = datetime.combine(day, time(hh, 0), tzinfo=SITE_TZ).astimezone(UTC)

    def reconfigure(self, **overrides: Any) -> None:
        self.config = make_config(self.root, **overrides)

    def append(
        self,
        source: str,
        day: date,
        messages: Iterable[bytes],
        *,
        sender: str | None = None,
        start: time = time(8, 0),
        partial: bytes = b"",
    ) -> Path:
        """Append spool lines exactly as the collector writes them (plus an optional tail).

        ``partial`` is written after the lines without ``\\n`` (a line still being written);
        complete it with a later call whose first message continues it.
        """
        path = self.layout.spool_file(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = self.written.setdefault((source, day), [])
        offset = path.stat().st_size if path.exists() else 0
        base = datetime.combine(day, start, tzinfo=SITE_TZ)
        with path.open("ab") as handle:
            for index, message in enumerate(messages):
                received = base + timedelta(milliseconds=len(lines) * 7 + index)
                line = format_line(received, sender or SENDERS.get(source, "10.9.9.9"), message)
                lines.append((len(lines) + 1, offset, line))
                handle.write(line)
                offset += len(line)
            handle.write(partial)
        return path

    def append_raw(self, source: str, day: date, data: bytes) -> None:
        """Bytes that are not (yet) a complete spool line; recorded as the lines they complete."""
        path = self.layout.spool_file(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(data)
        self._recount(source, day)

    def _recount(self, source: str, day: date) -> None:
        content = self.layout.spool_file(source, day).read_bytes()
        lines: list[tuple[int, int, bytes]] = []
        offset = 0
        for number, line in enumerate(content.splitlines(keepends=True), start=1):
            if line.endswith(b"\n"):
                lines.append((number, offset, line))
            offset += len(line)
        self.written[(source, day)] = lines

    def sealer(self) -> Sealer:
        return Sealer(
            self.config,
            provider=MockTsaProvider(self.tsa),
            roots=[self.tsa.root_certificate],
            clock=self.clock,
            clock_status=lambda: "test clock status",
        )

    def seal(self, day: date) -> None:
        """Seal ``day`` with the real sealer (compress, manifest, stamp, delete the spool)."""
        saved = self.now
        self.set_today(day + timedelta(days=1), hh=0)
        self.now += timedelta(minutes=30)
        try:
            outcome = self.sealer().seal_day(day)
        finally:
            self.now = saved
        if outcome.status != "sealed":
            raise AssertionError(f"sealing {day} failed: {outcome.message}")

    def service(self, engine: Engine, **kwargs: Any) -> IngestService:
        kwargs.setdefault("clock", self.clock)
        kwargs.setdefault("status_interval", 0.05)
        kwargs.setdefault("backoff_max", 0.2)
        return IngestService(self.config, engine, **kwargs)

    def expected(self) -> set[tuple[str, date, int, int]]:
        return {
            (source, day, number, offset)
            for (source, day), lines in self.written.items()
            for number, offset, _ in lines
        }


def _query(bind: Engine | Connection, sql: str) -> Sequence[Any]:
    """Rows of ``sql``; on a connection inside its transaction (one snapshot for several reads)."""
    if isinstance(bind, Connection):
        return bind.execute(text(sql)).all()
    with bind.connect() as connection:
        return connection.execute(text(sql)).all()


def loaded(bind: Engine | Connection) -> list[tuple[str, date, int, int]]:
    """``(source, raw_day, raw_line, raw_offset)`` of every ``messages`` row."""
    rows = _query(
        bind,
        "SELECT source, raw_day, raw_line, raw_offset FROM messages "
        "ORDER BY source, raw_day, raw_line",
    )
    return [(str(r[0]), r[1], int(r[2]), int(r[3])) for r in rows]


def offsets(bind: Engine | Connection) -> dict[tuple[str, date], tuple[int, int, bool]]:
    """``(source, day) -> (byte_offset, line_number, finished)`` from ``ingest_offsets``."""
    rows = _query(
        bind, "SELECT source, day, byte_offset, line_number, finished_at FROM ingest_offsets"
    )
    return {(str(r[0]), r[1]): (int(r[2]), int(r[3]), r[4] is not None) for r in rows}


def check_exactly_once(engine: Engine, world: World) -> list[str]:
    """Problems with "every written line is in ``messages`` exactly once" (empty = fine)."""
    rows = loaded(engine)
    problems = []
    if len(rows) != len(set(rows)):
        problems.append(f"{len(rows) - len(set(rows))} line(s) loaded more than once")
    expected = world.expected()
    missing = expected - set(rows)
    extra = set(rows) - expected
    if missing:
        problems.append(f"{len(missing)} line(s) missing, e.g. {sorted(missing)[:3]}")
    if extra:
        problems.append(f"{len(extra)} unexpected row(s), e.g. {sorted(extra)[:3]}")
    return problems


class TcpProxy:
    """Forwards ``127.0.0.1:<port>`` to the database; :meth:`down` drops and refuses connections."""

    def __init__(self, host: str, port: int) -> None:
        self._target = (host, port)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = int(self._listener.getsockname()[1])
        self._up = threading.Event()
        self._up.set()
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self._closed = False
        #: Connections refused while down.
        self.refused = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            if not self._up.is_set():
                self.refused += 1
                client.close()
                continue
            try:
                server = socket.create_connection(self._target, timeout=5)
            except OSError:
                client.close()
                continue
            server.settimeout(None)
            with self._lock:
                self._sockets += [client, server]
            for source, target in ((client, server), (server, client)):
                threading.Thread(target=_pipe, args=(source, target), daemon=True).start()

    def down(self) -> None:
        self._up.clear()
        with self._lock:
            sockets, self._sockets = self._sockets, []
        for sock in sockets:
            _close(sock)

    def up(self) -> None:
        self._up.set()

    def close(self) -> None:
        self._closed = True
        self.down()
        self._listener.close()

    def dsn(self, dsn: str) -> str:
        """``dsn`` with this proxy's port (the password stays in; never print it)."""
        return make_url(dsn).set(port=self.port).render_as_string(hide_password=False)


def _close(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    sock.close()


def _pipe(source: socket.socket, target: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except OSError:
        pass
    finally:
        _close(source)
        _close(target)


@contextlib.contextmanager
def proxy_for(dsn: str) -> Iterator[TcpProxy]:
    url = make_url(dsn)
    proxy = TcpProxy(url.host or "127.0.0.1", url.port or 5432)
    try:
        yield proxy
    finally:
        proxy.close()


def write_config_file(world: World, dsn: str, **ingest: Any) -> Path:
    """``arcilogger.toml`` for the database of ``dsn``; its password goes to the secret store."""
    url = make_url(dsn)
    secrets_dir = world.root / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key = secrets_dir / "secret.key"
    if not key.exists():
        SecretStore.create_key(key)
    SecretStore(key, secrets_dir / "secrets.json").set(DATABASE_PASSWORD, url.password or "")
    settings = {"batch_rows": 500, "batch_seconds": 0.05, "poll_seconds": 0.02, **ingest}
    lines = [
        f'data_dir = "{world.config.data_dir.as_posix()}"',
        f'secrets_dir = "{secrets_dir.as_posix()}"',
        "",
        "[database]",
        f'host = "{url.host}"',
        f"port = {url.port or 5432}",
        f'name = "{url.database}"',
        f'user = "{url.username}"',
        "",
        "[ingest]",
        *(f"{name} = {value}" for name, value in settings.items()),
    ]
    for source in SOURCES:
        lines += ["", "[[sources]]"]
        for name, value in source.items():
            if isinstance(value, bool):
                lines.append(f"{name} = {str(value).lower()}")
            elif isinstance(value, str):
                lines.append(f'{name} = "{value}"')
            else:
                lines.append(f"{name} = {value}")
    path = world.root / "arcilogger.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# -- throughput measurement (ingest/tests/bench_throughput.py, test_throughput.py) -------------


@dataclass(frozen=True)
class Throughput:
    lines: int
    rows: int
    bytes: int
    seconds: float
    #: CPU seconds of this (ingest) process.
    process_cpu_seconds: float
    #: Whole machine (ingest + PostgreSQL + everything else), percent of all cores; Linux only.
    machine_cpu_percent: float | None

    @property
    def lines_per_second(self) -> float:
        return self.lines / self.seconds if self.seconds else 0.0

    def summary(self) -> str:
        machine = (
            f"{self.machine_cpu_percent:.0f} % of all cores"
            if self.machine_cpu_percent is not None
            else "n/a"
        )
        one_core = 100 * self.process_cpu_seconds / self.seconds if self.seconds else 0.0
        return (
            f"{self.lines} lines ({self.bytes / 1e6:.1f} MB, {self.rows} rows) in "
            f"{self.seconds:.2f} s = {self.lines_per_second:,.0f} lines/s; ingest process CPU "
            f"{self.process_cpu_seconds:.2f} s ({one_core:.0f} % of one core); "
            f"machine CPU {machine}"
        )


def write_realistic_spool(
    world: World, day: date, lines: int, *, fortigate_percent: int = 70, first: int = 0
) -> int:
    """``lines`` spool lines: ``fortigate_percent`` % FortiGate traffic, the rest MikroTik DHCP.

    Returns the bytes written. Uses :func:`~arcilogger_core.rawlog.format_line` like the
    collector; receive times advance by 1 ms per line (line ``first`` continues a feed).
    """
    paths = {source: world.layout.spool_file(source, day) for source in ("fortigate", "core-r1")}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    start = datetime.combine(day, time(0, 0), tzinfo=SITE_TZ)
    with paths["fortigate"].open("ab") as fortigate, paths["core-r1"].open("ab") as mikrotik:
        for index in range(first, first + lines):
            received = start + timedelta(milliseconds=index)
            if index % 100 < fortigate_percent:
                line = format_line(received, SENDERS["fortigate"], fortigate_message(index))
                fortigate.write(line)
            else:
                line = format_line(received, SENDERS["core-r1"], dhcp_message(index))
                mikrotik.write(line)
            total += len(line)
    return total


def _machine_cpu() -> tuple[int, int] | None:
    """(busy, total) jiffies from ``/proc/stat`` (Linux), else ``None``."""
    try:
        fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]
    except OSError:
        return None
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values) - idle, sum(values)


def measure_throughput(world: World, engine: Engine, *, lines: int, workers: int = 0) -> Throughput:
    """Write ``lines`` realistic lines, then time ``run(once=True)`` until all are loaded.

    With ``workers`` the time includes starting the worker processes.
    """
    size = write_realistic_spool(world, DAY, lines)
    service = world.service(engine, status_interval=5.0, workers=workers)
    cpu_before, machine_before = process_time(), _machine_cpu()
    started = perf_counter()
    if service.run(once=True) != 0:
        raise AssertionError("ingest did not finish")
    seconds = perf_counter() - started
    cpu = process_time() - cpu_before
    machine_after = _machine_cpu()
    machine = None
    if machine_before is not None and machine_after is not None:
        busy = machine_after[0] - machine_before[0]
        total = machine_after[1] - machine_before[1]
        machine = 100.0 * busy / total if total else None
    return Throughput(
        lines=service.counters.lines,
        rows=service.counters.rows,
        bytes=size,
        seconds=seconds,
        process_cpu_seconds=cpu,
        machine_cpu_percent=machine,
    )


@dataclass(frozen=True)
class LiveRun:
    """A live feed at a fixed rate while the service runs normally (not ``--once``)."""

    rate: int
    seconds: float
    written: int
    loaded: int
    #: Most lines written but not yet committed at any sample (every 20 ms).
    max_backlog: int
    #: Time from the end of the feed until every line was committed.
    catch_up_seconds: float
    #: CPU seconds of this process: the service **and** the line generator thread.
    process_cpu_seconds: float
    machine_cpu_percent: float | None

    def summary(self) -> str:
        machine = (
            f"{self.machine_cpu_percent:.0f} % of all cores"
            if self.machine_cpu_percent is not None
            else "n/a"
        )
        return (
            f"offered {self.rate:,} lines/s for {self.seconds:.0f} s ({self.written} lines, "
            f"{self.loaded} loaded): largest backlog {self.max_backlog} lines "
            f"(~{self.max_backlog / self.rate:.2f} s), caught up {self.catch_up_seconds:.2f} s "
            f"after the feed stopped; process CPU incl. generator "
            f"{100 * self.process_cpu_seconds / self.seconds:.0f} % of one core; machine CPU "
            f"{machine}"
        )


def measure_live(
    world: World, engine: Engine, *, rate: int, seconds: float, workers: int = 0
) -> LiveRun:
    """Append ``rate`` lines/s for ``seconds`` while the service runs; sample the backlog."""
    service = world.service(engine, status_interval=5.0, workers=workers)
    runner = threading.Thread(target=service.run)
    runner.start()
    written = max_backlog = 0
    try:
        while service.state != "running":
            sleep(0.01)
        cpu_before, machine_before = process_time(), _machine_cpu()
        started = perf_counter()
        while (elapsed := perf_counter() - started) < seconds:
            due = int(elapsed * rate)
            if due > written:
                write_realistic_spool(world, DAY, due - written, first=written)
                written = due
            max_backlog = max(max_backlog, written - service.counters.lines)
            sleep(0.02)
        fed = perf_counter()
        while service.counters.lines < written and perf_counter() - fed < 300:
            sleep(0.005)
        catch_up = perf_counter() - fed
        cpu = process_time() - cpu_before
        machine_after = _machine_cpu()
    finally:
        service.stop.set()
        runner.join(60)
    machine = None
    if machine_before is not None and machine_after is not None:
        total = machine_after[1] - machine_before[1]
        machine = 100.0 * (machine_after[0] - machine_before[0]) / total if total else None
    return LiveRun(
        rate=rate,
        seconds=seconds,
        written=written,
        loaded=service.counters.lines,
        max_backlog=max_backlog,
        catch_up_seconds=catch_up,
        process_cpu_seconds=cpu,
        machine_cpu_percent=machine,
    )
