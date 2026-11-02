"""Throwaway PostgreSQL databases for tests (used by every package's database tests).

``ARCILOGGER_TEST_PG`` is the **admin DSN** of a server the tests may create databases on, e.g.
``postgresql://arci_test:arci_test@127.0.0.1:5432/postgres`` (a role with ``CREATEDB``).

* Once per test session a **template** database is created, migrated + seeded and given the
  fictional **example site** (:func:`seed_example_network`; ``arci_tpl_<host>_<pid>_<random>``):
  the product itself ships no VLAN plan, the tests use this one.
* Every test that asks for a database gets a fresh copy (``CREATE DATABASE … TEMPLATE …``), which
  takes a few tens of milliseconds — cheaper and far simpler than undoing what a test committed.
* Everything is dropped at the end (``DROP DATABASE … WITH (FORCE)``). Names are unique per host,
  process and random suffix, so several test runs (several agents, CI jobs) can share one server.
  Databases left behind by killed runs on this host are removed at the next session start.

Without ``ARCILOGGER_TEST_PG`` the database tests are **skipped** — unless
``ARCILOGGER_REQUIRE_DB=1`` (CI), then they fail. ``ARCILOGGER_TEST_TIMESCALE`` (``auto``,
``require``, ``off``) selects the TimescaleDB mode of the template (default ``auto``).
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import re
import secrets
import socket
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, cast

from sqlalchemy import URL, Engine, make_url, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import TimescaleMode, upgrade
from arcilogger_db.models import Department, Setting, Vlan
from arcilogger_db.settings import SEED_AUTHOR, set_setting

ENV_DSN: Final = "ARCILOGGER_TEST_PG"
ENV_REQUIRE: Final = "ARCILOGGER_REQUIRE_DB"
ENV_TIMESCALE: Final = "ARCILOGGER_TEST_TIMESCALE"
SKIP_REASON: Final = (
    f"database tests need {ENV_DSN}=postgresql://user:password@host:5432/postgres "
    "(a throwaway server; the role needs CREATEDB)"
)

_HOST_TAG: Final = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:6]
_NAME = re.compile(rf"^arci_(?:tpl|t)_{_HOST_TAG}_(\d+)_[0-9a-f]+(?:_[0-9a-f]+)?$")
_counter = itertools.count(1)


class TestDatabaseError(RuntimeError):
    """The test database server cannot be used."""

    __test__ = False  # not a pytest test class


def admin_dsn_from_env() -> str | None:
    value = os.environ.get(ENV_DSN, "").strip()
    return value or None


def database_required() -> bool:
    return os.environ.get(ENV_REQUIRE, "").strip() in ("1", "true", "yes")


def timescale_mode_from_env() -> TimescaleMode:
    value = os.environ.get(ENV_TIMESCALE, "auto").strip() or "auto"
    if value not in ("auto", "require", "off"):
        raise TestDatabaseError(f"{ENV_TIMESCALE} must be auto, require or off (got {value!r})")
    return cast(TimescaleMode, value)


def pause_compression_jobs(engine: Engine, timeout: float = 30.0) -> None:
    """TimescaleDB: pause the compression policies and wait for a running one. They first run a
    few seconds after a database is created from the template and compress, against today's
    date, whichever chunks exist by then; a test that compresses chunks itself pauses them."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_compression'"
            )
        )
    deadline = time.monotonic() + timeout
    while True:
        with engine.connect() as connection:
            running = connection.scalar(
                text(
                    "SELECT count(*) FROM timescaledb_information.job_stats s "
                    "JOIN timescaledb_information.jobs j USING (job_id) "
                    "WHERE j.proc_name = 'policy_compression' AND s.job_status = 'Running'"
                )
            )
        if not running:
            return
        if time.monotonic() >= deadline:
            raise TestDatabaseError("a compression job is still running")
        time.sleep(0.1)


def _pid_alive(pid: int) -> bool:
    """Whether a process of this host is still running.

    On Windows ``os.kill(pid, 0)`` would *terminate* the process, so every process counts as
    alive there; CI runners are ephemeral, stale databases are simply not collected.
    """
    alive = True
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    return alive


class TestServer:
    """Creates and drops uniquely named databases through an admin connection."""

    __test__ = False  # not a pytest test class

    def __init__(self, admin_dsn: str) -> None:
        self.admin_url: URL = make_url(admin_dsn)
        self._admin: Engine = engine_from_url(
            self.admin_url,
            application="tests-admin",
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
        )

    # -- names and URLs ---------------------------------------------------------------------------

    @staticmethod
    def new_name(kind: str) -> str:
        return f"arci_{kind}_{_HOST_TAG}_{os.getpid()}_{next(_counter):x}_{secrets.token_hex(3)}"

    def url_for(self, database: str) -> URL:
        return self.admin_url.set(database=database)

    def dsn_for(self, database: str) -> str:
        """DSN with the password (for drivers and subprocesses; never print it)."""
        return self.url_for(database).render_as_string(hide_password=False)

    # -- operations -------------------------------------------------------------------------------

    def check(self) -> str:
        """Server version; raises :class:`TestDatabaseError` when unreachable."""
        try:
            with self._admin.connect() as connection:
                return str(connection.execute(text("SHOW server_version")).scalar_one())
        except OperationalError as exc:
            raise TestDatabaseError(
                f"cannot connect to {self.admin_url.render_as_string()}: {exc.orig}"
            ) from exc

    def create_database(self, name: str, *, template: str = "template0") -> None:
        """``CREATE DATABASE`` (UTF-8, C collation); retries while the template is busy."""
        options = "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'" if template == "template0" else ""
        statement = f'CREATE DATABASE "{name}" TEMPLATE "{template}" {options}'
        for attempt in range(8):
            try:
                with self._admin.connect() as connection:
                    connection.execute(text(statement))
                return
            except DBAPIError as exc:
                busy = "being accessed by other users" in str(exc.orig)
                if not busy or attempt == 7:
                    raise
                self._terminate(template)
                time.sleep(0.1 * (attempt + 1))

    def drop_database(self, name: str) -> None:
        """``DROP … WITH (FORCE)``, retried: it fails while it may not terminate a backend that
        belongs to another role, e.g. an autovacuum worker that is still in that database."""
        for attempt in range(10):
            try:
                with self._admin.connect() as connection:
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                return
            except DBAPIError as exc:
                if attempt == 9 or "terminate" not in str(exc.orig):
                    raise
                time.sleep(0.2 * (attempt + 1))

    def _terminate(self, database: str) -> None:
        with self._admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database},
            )

    def drop_stale(self) -> list[str]:
        """Drop databases of earlier, no longer running test processes on this host."""
        with self._admin.connect() as connection:
            names = connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'arci\\_%'")
            ).scalars()
            stale = [
                name
                for name in names
                if (match := _NAME.match(name)) and not _pid_alive(int(match.group(1)))
            ]
        dropped = []
        for name in stale:
            with contextlib.suppress(DBAPIError):
                self.drop_database(name)
                dropped.append(name)
        return dropped

    def create_template(
        self, *, timescale: TimescaleMode = "auto", example_network: bool = True
    ) -> str:
        """A migrated and seeded database to copy from; nobody stays connected to it.

        ``example_network``: with the fictional example site of :func:`seed_example_network`
        (default; ``False`` gives the empty network of a new installation).
        """
        name = self.new_name("tpl")
        self.create_database(name)
        try:
            engine = engine_from_url(
                self.url_for(name), application="tests-migrate", poolclass=NullPool
            )
            try:
                # Only the current month's partitions (plain PostgreSQL): every partition makes
                # each per-test copy slower; rows of other months land in the default partition.
                upgrade(engine, timescale=timescale, months_ahead=0, months_back=0)
                if example_network:
                    with Session(engine) as session:
                        seed_example_network(session)
                        session.commit()
            finally:
                engine.dispose()
            # TimescaleDB's background scheduler connects to every database with the extension;
            # a template must have no sessions, so it stops accepting connections.
            with self._admin.connect() as connection:
                connection.execute(text(f'ALTER DATABASE "{name}" WITH ALLOW_CONNECTIONS false'))
            self._terminate(name)
        except BaseException:
            self.drop_database(name)
            raise
        return name

    @contextlib.contextmanager
    def database(self, template: str) -> Iterator[str]:
        """A fresh copy of ``template``, dropped afterwards; yields its name."""
        name = self.new_name("t")
        self.create_database(name, template=template)
        try:
            yield name
        finally:
            self.drop_database(name)

    def dispose(self) -> None:
        self._admin.dispose()


# -- the fictional example site -------------------------------------------------------------------

#: ``updated_by`` of the example site's settings: an administrator's change, so the seed never
#: puts the shipped default back over it.
EXAMPLE_AUTHOR: Final = "example"


@dataclass(frozen=True)
class ExampleVlan:
    vlan_id: int | None
    subnet: str
    name_tr: str
    purpose: str
    dhcp_server: str | None
    #: Users' devices live here: the VLAN name becomes a department linked to it.
    department: bool = False
    guest: bool = False
    notes: str | None = None


#: VLAN plan of the tests' fictional organisation (Örnek A.Ş.): a FortiGate in front of a MikroTik
#: core router that serves DHCP (one server per VLAN, ``dhcp<VLAN id>``). Network devices and
#: servers sit on static VLANs, three departments on their own VLANs, staff laptops and phones on
#: one staff Wi-Fi, guests on a Wi-Fi with short leases, and a /30 without a VLAN id links the
#: router and the firewall. An example, not a recommendation; the product never writes it
#: (:mod:`arcilogger_db.seed`).
EXAMPLE_VLANS: Final[tuple[ExampleVlan, ...]] = (
    ExampleVlan(
        10,
        "172.20.10.0/24",
        "Ağ Cihazları",
        "Network devices (switches, access points, printers)",
        None,
        notes="static",
    ),
    ExampleVlan(20, "172.20.20.0/24", "Sunucular", "Servers", None, notes="static"),
    ExampleVlan(41, "172.20.41.0/24", "Finans", "Department PCs", "dhcp41", department=True),
    ExampleVlan(42, "172.20.42.0/24", "Planlama", "Department PCs", "dhcp42", department=True),
    ExampleVlan(43, "172.20.43.0/24", "İdari İşler", "Department PCs", "dhcp43", department=True),
    ExampleVlan(
        60, "172.20.60.0/24", "Wi-Fi Personel", "Staff Wi-Fi (laptops and phones)", "dhcp60"
    ),
    ExampleVlan(
        61,
        "172.20.61.0/24",
        "Wi-Fi Misafir",
        "Guest Wi-Fi",
        "dhcp61",
        guest=True,
        notes="1 h leases",
    ),
    ExampleVlan(None, "172.31.255.0/30", "Transit", "Router to firewall link", None),
)

#: The example site's settings that belong to its plan: 1 h leases on the guest DHCP server (the
#: spec 9.1 rule then ends a guest lease 1.1 x 3600 = 3960 s after its last evidence).
EXAMPLE_SETTINGS: Final[Mapping[str, Any]] = {"lease.server_seconds": {"dhcp61": 3600}}


@dataclass
class ExampleNetworkReport:
    vlans_inserted: int = 0
    departments_inserted: int = 0
    settings_written: list[str] = field(default_factory=list)


def seed_example_network(session: Session) -> ExampleNetworkReport:
    """Write the example site (the caller commits): :data:`EXAMPLE_VLANS`, a department per
    user VLAN (named like it, linked to it) and :data:`EXAMPLE_SETTINGS`.

    Idempotent: existing VLANs and departments stay as they are; a setting is written while it
    is missing or still holds the shipped default (``updated_by = ':seed'``).
    """
    report = ExampleNetworkReport()
    departments = {d.name: d for d in session.scalars(select(Department))}
    for vlan in EXAMPLE_VLANS:
        department_id = None
        if vlan.department:
            department = departments.get(vlan.name_tr)
            if department is None:
                department = Department(name=vlan.name_tr)
                session.add(department)
                session.flush()
                departments[vlan.name_tr] = department
                report.departments_inserted += 1
            department_id = department.id
        if session.get(Vlan, vlan.subnet) is not None:
            continue
        session.add(
            Vlan(
                subnet=vlan.subnet,
                vlan_id=vlan.vlan_id,
                name_tr=vlan.name_tr,
                purpose=vlan.purpose,
                dhcp_server=vlan.dhcp_server,
                department_id=department_id,
                guest=vlan.guest,
                notes=vlan.notes,
            )
        )
        report.vlans_inserted += 1
    for key, value in EXAMPLE_SETTINGS.items():
        author = session.scalar(select(Setting.updated_by).where(Setting.key == key))
        if author is None or author == SEED_AUTHOR:
            set_setting(session, key, value, updated_by=EXAMPLE_AUTHOR)
            report.settings_written.append(key)
    session.flush()
    return report
