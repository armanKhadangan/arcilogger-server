"""Capture real ARCILOGGER API answers for the web app's mocked E2E tests (run from the root).

    export ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres
    uv run python web/scripts/capture_api_samples.py           # rewrite web/e2e/samples/*.json
    uv run python web/scripts/capture_api_samples.py --check   # exit 1 when the API's answers
                                                               # no longer match the samples

The mocked API of the E2E tests (``web/e2e/mock-api.ts``) answers the 5651 lookup, evidence
package, sealing, credit and devices/persons endpoints with these samples, so the pages are
tested against what the server really sends — not against hand-written guesses that drift.

How the samples are made (everything is the production code, in this process):

1. a throwaway database (``arcilogger_db.testing.TestServer``, migrated and seeded) and a data
   directory in a temporary folder;
2. a site on **09.10.2026**: DHCP/ARP lines of the core router and FortiGate sessions written as
   real spool lines and database rows (:class:`arcilogger_worker.testing.Evidence`), the lease
   timeline rebuilt by the worker job, one device assigned to a person since 01.10.2026
   (assignment history), an IP conflict (a static device answering ARP for a DHCP client's
   address) and background sessions of other clients;
3. October 2026 sealed by the core sealer with the **mock** time-stamp authority (never legally
   valid): 04.-10.10 sealed (06.10 three days late), 11.10 failed (TSA unreachable), 12.10
   waiting; 04.10 verified through the API; replicas of 04.-07.10;
4. the API (``create_app``) called through ``TestClient`` as an administrator and an auditor:
   lookups in every mode, the official report (with and without a time stamp), raw lines,
   evidence packages (ready, password protected, failed, expired — also one whose expiry passed
   before the nightly purge marked it — and queued) and the refusals the pages must show
   (validation, KVKK notice, re-authentication, limits, disk space, time-outs, unreadable files,
   invalid settings, 501 …); then the devices and persons pages (``devices.json``: lists,
   details with assignment history, persons, departments, VLANs (one added, refused, moved and
   deleted again), (bulk) assignment, CSV exports and templates, import previews and their
   refusals);
5. the usage pages (``usage.json``) in a database of their own: the golden usage site of
   ``arcilogger_worker.usage.testing`` on **12.10.2026** (persons with Turkish names, a device
   reassigned at noon, background traffic, blocked attempts, an evening session, a session over
   midnight), rolled up by the worker's own job; company, subjects, one subject, sites,
   categories (also filtered by department), CSV and Excel exports, rebuild requests, the
   optional pseudonyms and the refusals.
6. last the Security page and the dashboard: alerts as the worker writes them (the failed
   sealing of 11.10 imported from ``alerts.jsonl``, R15 of a silent switch from the real check,
   the site's IP conflict, disk, new log source, a credit alert the check resolved) and the
   workflow on them (``alerts.json``: lists, details with events, runbook and masked e-mail
   recipients, every refusal), and ``/dashboard/summary`` per role (``dashboard.json``) over
   three watched sources, about 1,280 messages a minute, the October seal calendar, the credit
   history and the clock samples.
7. the Raporlar page
   (``reports.json``: the shipped schedules, a new and a changed one, "run now", runs made by
   the worker job ``reports.run_due`` — one e-mailed to one recipient and refused by the
   other —, their files, the preview and the refusals). The usage engine's report data is the
   fixed sample of ``arcilogger_worker.reports.testing``: the samples describe the report API,
   not usage numbers.
8. the licence (``license.json``, licensing contract 8.5) on sites of their own (a fresh
   database and data directory each, so the main site's older sealed days and audit entries —
   trial lower bounds — never decide a licence state): ``GET /license`` in every state for an
   administrator and a viewer, ``POST /license`` (installs and every refusal), ``DELETE``, the
   locked modules' 403 answers and the users limit's 409. Real evaluation throughout, on inputs
   the capture controls: the installation's seed and the worker's ``license.check`` run at
   chosen moments, licence files are signed with the deterministic test key of
   ``arcilogger_core.license.testing`` for the server id of ``fixed_identity()``
   (``XW68-7K1Z-5FGV-AGT8``), and the release day is pinned to 01.10.2026 (version 0.1.0;
   what a site did before that day ran a previous version), so the samples do not change when
   ``RELEASE_DATE`` is bumped. Times on these sites are exact moments (not the running clock),
   so the licence samples are the same on every capture.

The search, live tail and sources pages' samples (``search.json``, ``live.json``,
``sources.json``) come from modules of their own next to this script, each kept with its API
(``samples_search.py``, ``samples_live_sources.py``): their ``capture`` runs on the main site after
step 7's pages and returns ``{file name: data}``; a file whose data is empty is not written.

Steps 1-7 run with every licence module on, as the tests do by default
(``override_status(all_modules_status())``): those samples describe their own pages, not the
licence.

The clock of every ARCILOGGER module runs from :data:`CLOCK` (13.10.2026 07:00 site time), not
from the machine's date: "today", the day states of the sealing calendar, expiry times and the
"verified within 35 days" window are the same whenever the samples are rewritten, and the E2E
specs (whose browser clock is set to the same day) keep matching them. Rows whose time the
database fills in (``now()``) get the same clock.

Values that depend on the machine are replaced (the temporary folder, the host name in the
manifests); the one-time package password is replaced by a placeholder. ``--check`` captures
again and compares every answer with the committed samples: keys, value types (``null``
compatible with any type) and the values of enum-like fields (:data:`ENUM_KEYS`: states,
statuses, modes, tones, error codes …); messages, times and hashes are not compared. The Python
CI job (Linux) runs it.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest import mock
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, inspect, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from arcilogger_api import deps, evidence_limits, rawlines
from arcilogger_api.app import create_app
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.deps import AppState
from arcilogger_api.evidence_jobs import EvidenceBuilder
from arcilogger_api.lookup_testing import mock_tsa
from arcilogger_api.reports import inputs as report_inputs
from arcilogger_api.routers import credits as credits_router
from arcilogger_api.routers import lookup as lookup_router
from arcilogger_api.routers.evidence import STATUS_LABELS
from arcilogger_api.security import hash_token
from arcilogger_api.testing import LoggedIn, login, make_user
import arcilogger_core
from arcilogger_core import timeutil
from arcilogger_core.admin.testing import FakeAgent, SmtpTestServer, make_installation
from arcilogger_core.config import Config, load_config
from arcilogger_core.credit import check_credit
from arcilogger_core.layout import Layout
from arcilogger_core.license import revoked as license_revoked
from arcilogger_core.license.fingerprint import ServerIdentity, server_id_of
from arcilogger_core.license.testing import (
    TEST_INSTALLATION_ID,
    all_modules_status,
    fixed_identity,
    make_license,
    override_status,
    tamper,
    test_private_key,
    trusted_test_keys,
)
from arcilogger_core.rawlog import format_line
from arcilogger_core.secretstore import USAGE_PSEUDONYM_KEY, SecretStore
from arcilogger_core.replica import replicate_day
from arcilogger_core.timeutil import SITE_TZ, site_day, utc_now
from arcilogger_core.tsa.base import TsaUnavailableError
from arcilogger_core.tsa.mock import MockFailure, MockTsaProvider
from arcilogger_db import licensing
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.audit import append_audit
from arcilogger_db.egress import legacy_egresses
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import (
    Alert,
    AlertDelivery,
    Department,
    Device,
    EvidencePackage,
    Person,
    ReportRun,
    ReportSchedule,
    Source,
    User,
    UserSession,
)
from arcilogger_db.seed import seed
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import TestServer, admin_dsn_from_env
from arcilogger_worker.admin_jobs import process_requests as process_admin_requests
from arcilogger_worker.alerts.importer import import_alerts
from arcilogger_worker.alerts.r15 import check_silent_sources
from arcilogger_worker.alerts.store import open_alert, resolve
from arcilogger_worker.alerts.mail import SmtpSendError
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.licensing import job_license_check
from arcilogger_worker.reports import runner as report_runner
from arcilogger_worker.reports.testing import report_sample
from arcilogger_worker.service import JobContext
from arcilogger_worker.testing import FIREWALL, Evidence, tr
from arcilogger_worker.usage.jobs import process_requests
from arcilogger_worker.usage.jobs import rebuild_day as rebuild_usage_day
from arcilogger_worker.usage.testing import build_golden_site
from arcilogger_worker.usage import report_data
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

# The sample modules next to this script: on the path when it runs as a script, not when a test
# loads it by its file name.
_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import samples_live_sources  # noqa: E402
import samples_search  # noqa: E402

#: The pages whose samples a module of their own captures (module text).
SAMPLE_MODULES = (samples_search, samples_live_sources)

OUT_DIR = Path(__file__).resolve().parent.parent / "e2e" / "samples"
API = "/api/v1"

#: The capture's "now" (see the module text): the morning after 12.10.2026, before that day was
#: sealed. The E2E specs expect the day states and dates that follow from it.
CLOCK = datetime(2026, 10, 13, 7, 0, tzinfo=SITE_TZ)
SITE_DAY = date(2026, 10, 9)
#: Moment of the requests: the example chain's session from port 51001, which the firewall's
#: source NAT keeps (WAN port 51001).
AT = "2026-10-09T11:23:41"
WAN = "100.64.12.9"
#: The FortiGate's WAN address behind the ISP router as the internet exit ``wan`` (revision
#: 0018_nat_egresses converts Wave 2's nat.wan_transip to it), so the WAN request is exact.
WAN_EXIT = legacy_egresses([], WAN, False)[0]
#: A public address of the ISP router (documentation range, RFC 5737).
PUBLIC_IP = "198.51.100.23"
#: The example attribution chain: WAN 100.64.12.9:51001 → the PC on VLAN 42 "Planlama" (lease of
#: dhcp42 at 10:42:18) → device "Planlama-PC-14" → Selin Kaya / Planlama.
PC, PC_MAC, PC_HOSTNAME = "172.20.42.57", "00:00:5e:00:53:57", "PLN-PC14"
PERSON_NAME, DEPARTMENT, DEVICE_NAME = "Selin Kaya", "Planlama", "Planlama-PC-14"
#: A phone on the staff Wi-Fi (VLAN 60) with a randomised MAC address.
PHONE, PHONE_MAC, PHONE_HOSTNAME = "172.20.60.142", "02:00:5e:00:53:64", "Telefon-12"
ROGUE_MAC = "00:0c:29:00:00:99"
#: Other clients on the department VLANs (VLAN 42 Planlama, 41 Finans, 43 İdari İşler).
OTHERS = (
    ("172.20.42.165", "3c:52:82:00:00:05", "PLN-PC-05", "dhcp42"),
    ("172.20.41.15", "00:15:5d:00:00:20", "FIN-PC-01", "dhcp41"),
    ("172.20.43.44", "a4:83:e7:00:00:44", "IDR-PC-04", "dhcp43"),
)
SEALED = [date(2026, 10, day) for day in range(4, 11)]  # 04.-10.10.2026
LATE = date(2026, 10, 6)
FAILED = date(2026, 10, 11)
WAITING = date(2026, 10, 12)
VERIFIED = date(2026, 10, 4)
TAMPERED = date(2026, 10, 8)
REPLICATED = [date(2026, 10, day) for day in range(4, 8)]
#: Selin Kaya holds the PC since then (the lookups of 09.10 name her from the history).
ASSIGNED_SINCE = datetime(2026, 10, 1, 9, 0, tzinfo=SITE_TZ)
HOST = "ARCI-SRV01"
#: The usage samples' day: the day before the capture's clock (a Monday), rolled up as final.
USAGE_DAY = date(2026, 10, 12)
#: The optional pseudonyms' key in the capture's secret store (never a real one).
USAGE_PSEUDONYM_VALUE = "ornek-takma-ad-anahtari-yalnizca-test"  # gitleaks:allow — test value
REPLICA_TARGET = "//NAS-01/arcilogger-worm"
PASSWORD_PLACEHOLDER = "ornek-parola-yalnizca-test"  # noqa: S105  # gitleaks:allow — replaces the real one

#: The capture's configuration file (an installation folder: the browser administration reads
#: it, its agent writes it); ``{data_dir}`` and ``{replica}`` are filled in.
SITE_TOML = """\
# The capture's site (web/scripts/capture_api_samples.py): the repository's fictional world.
site_name = "Örnek A.Ş."
data_dir = "{data_dir}"

[api]
secure_cookies = false

[seal]
quiet_seconds = 0

[tsa]
provider = "mock"

[replica]
enabled = true
target = "{replica}"

[[sources]]
name = "fortigate"
type = "fortigate"
address = "172.31.255.1"

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "172.20.10.1"
"""

REQUEST = {
    "authority": "İzmir Cumhuriyet Başsavcılığı",
    "file_no": "2026/48213 Soruşturma",
    "request_date": "2026-10-10",
    "reason": (
        "5651 sayılı Kanun kapsamında, yazıda belirtilen genel IP adresi ve zaman için\n"
        "erişim kayıtları ve kullanıcı bilgisi talebi (müzekkere no 2026/771)."
    ),
}


# -- small helpers --------------------------------------------------------------------------------


def _local(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=SITE_TZ)


class Capture:
    """The app, its users and everything captured so far."""

    def __init__(self, root: Path, engine: Engine) -> None:
        self.root = root
        self.engine = engine
        # An installation folder as the installer leaves it: the configuration file, the
        # secret store, the administration channel's keys and policy (ADR-0014).
        toml = SITE_TOML.replace("{replica}", (root / "replica").as_posix())
        self.installed = make_installation(root, toml=toml)
        self.config = load_config(self.installed.config_path)
        if self.config.sources[0].address != FIREWALL:
            raise SystemExit("SITE_TOML must name the example firewall")
        self.layout = Layout(self.config.data_dir)
        self.app = create_app(
            self.config,
            engine,
            config_path=self.installed.config_path,
            audit_flush_seconds=None,
        )
        state: AppState = self.app.state.arci
        self.factory: sessionmaker[Session] = state.session_factory()
        self._agent: FakeAgent | None = None
        self.samples: dict[str, dict[str, Any]] = {
            "lookups": {},
            "raw": {},
            "evidence": {},
            "seals": {},
            "credits": {},
            "devices": {},
            "alerts": {},
            "dashboard": {},
            "reports": {},
            "admin": {},
            "settings": {},
        }

    def agent(self) -> FakeAgent:
        """The site's administration agent (the real one, run synchronously) with ApplySystem
        (a managed firewall), its heartbeat fresh."""
        if self._agent is None:
            self._agent = FakeAgent(self.installed, system=True)
            self._agent.start()
        self._agent.agent.heartbeat()
        return self._agent

    # -- users ----------------------------------------------------------------------------------

    def client(self, role: str, username: str, display_name: str, *, notice: bool = True) -> Any:
        user = make_user(self.factory, role, username=username, display_name=display_name)
        return self.signed_in(user, notice=notice)

    def signed_in(self, user: User, *, notice: bool = True) -> Any:
        """A new session of an existing user (``client`` creates the user first)."""
        client = TestClient(self.app, base_url="http://testserver")
        who = login(client, self.factory, user)
        if notice:
            with self.factory() as session:
                accept_privacy(session, user.id, privacy_version(session), ip=None)
                session.commit()
        client.who = who  # type: ignore[attr-defined]
        return client

    def recent(self, client: Any, *, minutes_ago: int = 0) -> None:
        """The client's session re-authenticated ``minutes_ago`` (``POST /auth/reauth``)."""
        who: LoggedIn = client.who
        with self.factory() as session:
            session.execute(
                update(UserSession)
                .where(UserSession.token_hash == hash_token(who.session_token))
                .values(reauthenticated_at=utc_now() - timedelta(minutes=minutes_ago))
            )
            session.commit()

    def setting(self, key: str, value: Any) -> None:
        with self.factory() as session:
            set_setting(session, key, value, updated_by="capture")
            session.commit()

    def builder(self) -> EvidenceBuilder:
        found = getattr(self.app.state, "arci_evidence_builder", None)
        if not isinstance(found, EvidenceBuilder):
            raise SystemExit("the app has no evidence builder")
        return found

    # -- requests -------------------------------------------------------------------------------

    @staticmethod
    def json(response: Any, status: int) -> Any:
        if response.status_code != status:
            raise SystemExit(
                f"{response.request.method} {response.request.url}: expected {status}, "
                f"got {response.status_code}: {response.text[:2000]}"
            )
        return response.json()

    @staticmethod
    def error(response: Any, status: int, code: str) -> dict[str, Any]:
        """An error answer: ``{"status", "headers" (Retry-After), "body"}``."""
        body = Capture.json(response, status)
        if body["error"]["code"] != code:
            raise SystemExit(f"expected error {code}, got {body}")
        headers = {
            name: response.headers[name] for name in ("retry-after",) if name in response.headers
        }
        return {"status": status, "headers": headers, "body": body}

    @staticmethod
    def csv(response: Any) -> dict[str, Any]:
        """A CSV answer: the file facts and its text (the pages may show a template)."""
        facts = Capture.file(response)
        return {**facts, "text": response.content.decode("utf-8")}

    @staticmethod
    def file(response: Any) -> dict[str, Any]:
        """A file answer: the headers the pages use, size and SHA-256 (no content)."""
        if response.status_code != 200:
            raise SystemExit(f"download failed: {response.status_code} {response.text[:500]}")
        wanted = (
            "content-type",
            "content-disposition",
            "x-arci-sha256",
            "repr-digest",
            "accept-ranges",
            "cache-control",
        )
        return {
            "headers": {
                name: response.headers[name] for name in wanted if name in response.headers
            },
            "size": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }


def _capture_now() -> datetime:
    """The body ``timeutil.utc_now`` gets inside :func:`frozen_clock`; the name is looked up in
    the globals of ``timeutil`` (where :func:`frozen_clock` puts it)."""
    return _arci_capture_now()  # type: ignore[name-defined, no-any-return]  # noqa: F821


@contextlib.contextmanager
def frozen_clock(start: datetime) -> Iterator[Callable[[], datetime]]:
    """``timeutil.utc_now()`` starts at ``start`` and runs on with the real elapsed time, so
    deadlines and the order of events still work; new ORM rows get it for the times the database
    would fill in (``server_default=now()``).

    The function's code is swapped, not the name: every reference to it — ``from … import
    utc_now`` in any module, dataclass ``default_factory=utc_now``, ``clock=utc_now`` defaults —
    is the same function object and sees the capture's clock.
    """
    function = timeutil.utc_now
    code = function.__code__
    origin = time.monotonic()
    begin = start.astimezone(UTC)

    def now() -> datetime:
        return begin + timedelta(seconds=time.monotonic() - origin)

    def database_times(session: Session, _context: Any, _instances: Any) -> None:
        for row in session.new:
            for attribute in inspect(type(row)).column_attrs:
                default = getattr(attribute.columns[0].server_default, "arg", None)
                if str(default) == "now()" and getattr(row, attribute.key, None) is None:
                    # ``utc_now()`` itself: the running clock, or a :func:`manual_clock` moment.
                    setattr(row, attribute.key, function())

    function.__globals__["_arci_capture_now"] = now
    function.__code__ = _capture_now.__code__
    event.listen(Session, "before_flush", database_times)
    try:
        yield now
    finally:
        event.remove(Session, "before_flush", database_times)
        function.__code__ = code
        function.__globals__.pop("_arci_capture_now", None)


class ManualClock:
    """What ``utc_now()`` says inside :func:`manual_clock`: exactly the moment set last."""

    def __init__(self, start: datetime) -> None:
        self.moment = start.astimezone(UTC)

    def __call__(self) -> datetime:
        return self.moment

    def at(self, moment: datetime) -> datetime:
        """Move to ``moment`` (forwards or backwards: the installation days of a site)."""
        self.moment = moment.astimezone(UTC)
        return self.moment


@contextlib.contextmanager
def manual_clock(start: datetime) -> Iterator[ManualClock]:
    """Inside :func:`frozen_clock`: ``utc_now()`` stands at the moments the capture sets, so
    every time in the licence samples (events, audit entries, ``checked_at``) is the same on
    every capture, however long the captures before took."""
    names = timeutil.utc_now.__globals__
    previous = names["_arci_capture_now"]
    clock = ManualClock(start)
    names["_arci_capture_now"] = clock
    try:
        yield clock
    finally:
        names["_arci_capture_now"] = previous


# -- the data -------------------------------------------------------------------------------------


def build_site(capture: Capture) -> None:
    """The lookup day: leases, sessions, an IP conflict, background traffic (one Evidence writer
    per day, so the raw line numbers are right)."""
    with capture.factory() as session:
        session.merge(Source(name="fortigate", type="fortigate", address=FIREWALL))
        session.merge(Source(name="core-r1", type="mikrotik", address="172.20.10.1"))
        evidence = Evidence(session, capture.layout)
        department = session.query(Department).filter(Department.name == DEPARTMENT).one()
        person = Person(name=PERSON_NAME, department_id=department.id, title="Planlama Uzmanı")
        session.add(person)
        session.flush()
        day = SITE_DAY
        for ip, mac, hostname, server in OTHERS:
            evidence.dhcp(tr(day, 8, 12), "assigned", ip, mac, server=server, hostname=hostname)
        evidence.dhcp(
            tr(day, 9, 37, 52),
            "assigned",
            PHONE,
            PHONE_MAC,
            server="dhcp60",
            hostname=PHONE_HOSTNAME,
        )
        evidence.dhcp(
            tr(day, 10, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname=PC_HOSTNAME
        )
        google = "203.0.113.20"
        evidence.dns(tr(day, 11, 14, 56), PHONE, "www.google.com", [google])
        evidence.traffic(tr(day, 11, 23, 41), PC, 51001, "203.0.113.10", 443, duration=10)
        evidence.traffic(tr(day, 11, 20, 56), PHONE, 51002, google, duration=100, interim=True)
        evidence.traffic(tr(day, 11, 22, 56), PHONE, 51002, google, duration=220, interim=True)
        evidence.traffic(tr(day, 11, 25, 26), PHONE, 51002, google, duration=370)
        evidence.web(tr(day, 11, 24, 32), PC, 51005, "static.portal.example", "203.0.113.40")
        evidence.traffic(tr(day, 11, 24, 36), PC, 51005, "203.0.113.40", 443, duration=5)
        # Background sessions of other clients around the requested moment.
        destinations = [
            ("203.0.113.10", 443),
            ("151.101.1.69", 443),
            ("104.18.32.7", 443),
            ("8.8.8.8", 53),
            ("20.190.160.20", 443),
        ]
        for index in range(18):
            ip = OTHERS[index % len(OTHERS)][0]
            dstip, dstport = destinations[index % len(destinations)]
            evidence.traffic(
                tr(day, 11, 22, 1) + timedelta(seconds=index * 7),
                ip,
                50100 + index,
                dstip,
                dstport,
                duration=3 + index % 4,
                proto=17 if dstport == 53 else 6,
            )
        evidence.traffic(tr(day, 11, 34, 56), PC, 62099, "203.0.113.40", 443, duration=5)
        # A static device answers ARP for the PC's address: an IP conflict around 13:30.
        evidence.arp(tr(day, 13), PC, ROGUE_MAC, interface="vlan42")
        evidence.arp(tr(day, 14), PC, ROGUE_MAC, interface="vlan42")
        evidence.traffic(tr(day, 13, 30), PC, 61001, "93.184.216.34", 443, duration=12)
        session.commit()
        rebuild_day(session, day, horizon=tr(day + timedelta(days=1), 9))
        device = session.get_one(Device, PC_MAC)
        device.name = DEVICE_NAME
        device.type = "pc"
        session.flush()
        # Through the assignment history, as POST /devices/assign does (ADR-0008).
        owners = lock_devices(session, [PC_MAC])
        planner = Planner(load_history(session, [PC_MAC]), now=utc_now(), owners=owners)
        planner.assign(PC_MAC, person.id, ASSIGNED_SINCE, note="Zimmet formu 2026/15")
        apply(session, planner.changes, assigned_by="tolga")
        session.commit()


def seal_october(capture: Capture) -> SealedVault:
    """04.-10.10 sealed (06.10 late), 11.10 failed, 12.10 waiting; replicas of 04.-07.10."""
    vault = make_vault(
        capture.config.data_dir, root=capture.root, tsa_now=at_site(date(2026, 9, 1))
    )
    vault.config = vault.config.model_copy(
        update={"seal": vault.config.seal.model_copy(update={"first_day": SEALED[0]})}
    )
    with mock.patch("arcilogger_core.seal.platform.node", return_value=HOST):
        for day in SEALED:
            if day != SITE_DAY:
                vault.write_day(day)
            now = at_site(day + timedelta(days=1))
            if day == LATE:
                now = at_site(day + timedelta(days=3), 1, 0)
            elif LATE < day < SITE_DAY:
                now = at_site(LATE + timedelta(days=3), 1, 10 + (day - LATE).days * 10)
            outcome = vault.seal(day, now=now)
            if outcome.status != "sealed":
                raise SystemExit(f"sealing {day} failed: {outcome.message}")
        vault.write_day(FAILED)
        if vault.fail_seal(FAILED, MockFailure.UNREACHABLE).status != "pending":
            raise SystemExit("the failed sealing attempt did not fail")
        vault.write_day(WAITING)
    target = capture.root / "replica"
    target.mkdir()
    for day in REPLICATED:
        replicate_day(capture.layout, day, target)
    return vault


def write_odd_line(capture: Capture) -> tuple[str, date, int]:
    """A spool line (not sealed yet) with a control character, a bidi override and a byte that
    is not UTF-8 — the raw-line view must show them as visible escapes."""
    path = capture.layout.spool_file("fortigate", WAITING)
    lines = path.read_bytes().count(b"\n")
    message = (
        '<190>logid="0316013056" type="utm" subtype="webfilter" srcip=172.20.42.165 '
        'dstip=104.18.32.7 hostname="fatura\u202excod.exe" msg="tab\there"'
    ).encode() + b" \x01 \xff"
    with path.open("ab") as handle:
        handle.write(format_line(_local(WAITING, 14, 2, 3), FIREWALL, message))
    return "fortigate", WAITING, lines + 1


# -- the captures ---------------------------------------------------------------------------------


def lookup_body(**query: Any) -> dict[str, Any]:
    return {"request": dict(REQUEST), "query": {"at": AT, "tolerance_seconds": 60, **query}}


def capture_lookups(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, int]:
    out = capture.samples["lookups"]
    capture.setting("nat.egresses", WAN_EXIT)
    runs = {
        "exact": lookup_body(public_ip=WAN, public_port=51001, protocol="tcp"),
        "candidates": lookup_body(
            public_ip=PUBLIC_IP, public_port=41234, dest_ip="203.0.113.10", dest_port=443
        ),
        "conflict": {
            **lookup_body(public_ip=PUBLIC_IP, dest_ip="93.184.216.34", dest_port=443),
            "query": {
                "at": "2026-10-09T13:30:05",
                "public_ip": PUBLIC_IP,
                "dest_ip": "93.184.216.34",
                "dest_port": 443,
            },
        },
        "internal": lookup_body(internal_ip=PHONE),
        "none": lookup_body(public_ip=PUBLIC_IP, dest_ip="203.0.113.50", dest_port=443),
        "vault_only": {
            **lookup_body(),
            "query": {
                "at": "2026-10-05T12:00:00+03:00",
                "public_ip": PUBLIC_IP,
                "dest_ip": "203.0.113.10",
            },
        },
    }
    ids: dict[str, int] = {}
    for name, body in runs.items():
        out[name] = capture.json(admin.post(f"{API}/lookup/5651", json=body), 201)
        ids[name] = out[name]["id"]
    capture.setting("lookup.max_candidates", 5)
    out["truncated"] = capture.json(
        admin.post(
            f"{API}/lookup/5651",
            json=lookup_body(public_ip=PUBLIC_IP, tolerance_seconds=120),
        ),
        201,
    )
    ids["truncated"] = out["truncated"]["id"]
    capture.setting("lookup.max_candidates", 100)
    auditor_body = lookup_body(public_ip=PUBLIC_IP, dest_ip="203.0.113.10", dest_port=443)
    auditor_body["request"] = {
        **REQUEST,
        "authority": "Ankara Cumhuriyet Başsavcılığı",
        "file_no": "2026/9917",
    }
    out["auditor_run"] = capture.json(auditor.post(f"{API}/lookup/5651", json=auditor_body), 201)

    # Official report: first without, then with a time stamp (mock TSA: not legally valid).
    out["report_pdf"] = capture.file(admin.get(f"{API}/lookup/{ids['exact']}/report.pdf"))
    out["exact_with_report"] = capture.json(admin.get(f"{API}/lookup/{ids['exact']}"), 200)
    capture.setting("lookup.report_timestamp", True)
    out["report_pdf_stamped"] = capture.file(
        admin.get(f"{API}/lookup/{ids['candidates']}/report.pdf")
    )
    out["candidates_with_report"] = capture.json(
        admin.get(f"{API}/lookup/{ids['candidates']}"), 200
    )
    out["report_token"] = capture.file(admin.get(f"{API}/lookup/{ids['candidates']}/report.pdf.zd"))
    capture.setting("lookup.report_timestamp", False)

    out["list_admin"] = capture.json(admin.get(f"{API}/lookups"), 200)
    out["list_admin_mine"] = capture.json(admin.get(f"{API}/lookups", params={"mine": True}), 200)
    out["list_auditor"] = capture.json(auditor.get(f"{API}/lookups"), 200)

    errors = out.setdefault("errors", {})
    future = lookup_body(public_ip=WAN, public_port=51001)
    future["request"]["request_date"] = (site_day(utc_now()) + timedelta(days=2)).isoformat()
    errors["future_request_date"] = capture.error(
        admin.post(f"{API}/lookup/5651", json=future), 422, "validation_error"
    )
    blank = lookup_body(public_ip=WAN)
    blank["request"]["authority"] = "   "
    errors["blank_authority"] = capture.error(
        admin.post(f"{API}/lookup/5651", json=blank), 422, "validation_error"
    )
    errors["nothing_to_look_for"] = capture.error(
        admin.post(f"{API}/lookup/5651", json=lookup_body()), 422, "validation_error"
    )
    errors["bad_address"] = capture.error(
        admin.post(f"{API}/lookup/5651", json=lookup_body(public_ip="100.64.12")),
        422,
        "validation_error",
    )
    # No IPv6 identity evidence (NDP, DHCPv6): refused, audited as lookup.run_failed.
    errors["ipv6_not_supported"] = capture.error(
        admin.post(
            f"{API}/lookup/5651", json=lookup_body(public_ip="2001:db8::44", public_port=41234)
        ),
        422,
        "ipv6_not_supported",
    )
    errors["privacy_notice_required"] = capture.error(
        newcomer.get(f"{API}/lookups"), 403, "privacy_notice_required"
    )
    errors["other_users_lookup"] = capture.error(
        auditor.get(f"{API}/lookup/{ids['exact']}"), 404, "not_found"
    )

    # The time budget runs out (a slow database): 504, audited as lookup.run_failed.
    capture.setting(lookup_router.TIMEOUT_SETTING, 1)

    def slow_lookup(db: Session, *_args: Any, **_kwargs: Any) -> Any:
        time.sleep(1.2)
        db.execute(text("SELECT 1"))  # refused: the time budget is used up
        raise SystemExit("the lookup's deadline did not stop a slow lookup")

    with mock.patch.object(lookup_router, "run_lookup", slow_lookup):
        errors["lookup_timeout"] = capture.error(
            admin.post(f"{API}/lookup/5651", json=lookup_body(public_ip=WAN, public_port=51001)),
            504,
            "lookup_timeout",
        )
    capture.setting(lookup_router.TIMEOUT_SETTING, lookup_router.DEFAULT_TIMEOUT_SECONDS)
    # Internet exits the lookup cannot use were saved around the validator (an address that is
    # no address); a public-IP lookup is refused, as the router parses the exits first.
    capture.setting("nat.egresses", {"wan": {"mode": "isp_nat", "transips": ["100.64.12"]}})
    errors["lookup_settings_invalid"] = capture.error(
        admin.post(f"{API}/lookup/5651", json=lookup_body(public_ip=WAN, public_port=51001)),
        503,
        "lookup_settings_invalid",
    )
    capture.setting("nat.egresses", WAN_EXIT)
    # A cited file cannot be read right now (folder rights, disk error): no report is made.
    failures = {
        "report_access_denied": PermissionError(13, "Permission denied"),
        "report_evidence_unreadable": OSError(5, "Input/output error"),
    }
    for code, failure in failures.items():
        with mock.patch.object(report_inputs, "regular_file", side_effect=failure):
            errors[code] = capture.error(
                admin.get(f"{API}/lookup/{ids['conflict']}/report.pdf"), 503, code
            )
    return ids


def raw_refs(outcome: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every raw reference in a stored outcome (matches, chains, alternatives)."""

    def chain(item: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
        if not item:
            return
        for step in ("lease", "device", "person"):
            yield from (item.get(step) or {}).get("evidence") or []
        for other in item.get("alternatives") or []:
            yield from chain(other)

    for match in outcome.get("matches") or []:
        yield from match["traffic"].get("evidence") or []
        yield from chain(match.get("chain"))
    yield from chain(outcome.get("attribution"))


def capture_raw(capture: Capture, admin: Any, odd: tuple[str, date, int]) -> None:
    out = capture.samples["raw"]
    seen: set[tuple[str, str, int]] = set()
    lines = out.setdefault("lines", {})
    for lookup in capture.samples["lookups"].values():
        if not isinstance(lookup, dict) or "outcome" not in lookup or lookup["outcome"] is None:
            continue
        for ref in raw_refs(lookup["outcome"]):
            raw = ref.get("raw")
            if raw is None:
                continue
            key = (raw["source"], raw["day"], raw["line"])
            if key in seen:
                continue
            seen.add(key)
            params = {"source": raw["source"], "day": raw["day"], "line": raw["line"]}
            if raw.get("offset") is not None:
                params["offset"] = raw["offset"]
            lines[f"{raw['source']}|{raw['day']}|{raw['line']}"] = capture.json(
                admin.get(f"{API}/raw", params=params), 200
            )
    # The day's first FortiGate line (the phone's DNS query, cited by no lookup): the E2E tests
    # check that the exact match's line 2 starts right after it.
    first = {"source": "fortigate", "day": SITE_DAY.isoformat(), "line": 1}
    if f"fortigate|{SITE_DAY.isoformat()}|1" not in lines:
        lines[f"fortigate|{SITE_DAY.isoformat()}|1"] = capture.json(
            admin.get(f"{API}/raw", params=first), 200
        )
    source, day, line = odd
    lines[f"{source}|{day.isoformat()}|{line}"] = capture.json(
        admin.get(f"{API}/raw", params={"source": source, "day": day.isoformat(), "line": line}),
        200,
    )
    out["odd_line"] = f"{source}|{day.isoformat()}|{line}"
    errors = out.setdefault("errors", {})
    errors["raw_not_found"] = capture.error(
        admin.get(
            f"{API}/raw", params={"source": "fortigate", "day": "2026-10-09", "line": 999999}
        ),
        404,
        "raw_not_found",
    )
    # The service may not read the file (folder rights) / a disk error.
    failures = {
        "raw_access_denied": PermissionError(13, "Permission denied"),
        "raw_io_error": OSError(5, "Input/output error"),
    }
    for code, failure in failures.items():
        with mock.patch.object(rawlines, "regular_file", side_effect=failure):
            errors[code] = capture.error(
                admin.get(
                    f"{API}/raw", params={"source": "fortigate", "day": "2026-10-09", "line": 2}
                ),
                503,
                code,
            )


def wait_builds(capture: Capture) -> None:
    if not capture.builder().wait(timeout=300):
        raise SystemExit("evidence builds did not finish in time")


@contextlib.contextmanager
def disk_free(free_gib: float, total_gib: float = 1000) -> Iterator[None]:
    """What ``shutil.disk_usage`` reports to the evidence limits (the capture machine may be
    nearly full)."""
    gib = 1024**3
    usage = type("Usage", (), {"total": int(total_gib * gib), "free": int(free_gib * gib)})
    usage.used = usage.total - usage.free  # type: ignore[attr-defined]
    with mock.patch.object(evidence_limits, "disk_usage", return_value=usage):
        yield


def capture_evidence(
    capture: Capture, admin: Any, auditor: Any, vault: SealedVault, lookup_ids: dict[str, int]
) -> None:
    out = capture.samples["evidence"]
    out["status_labels"] = {
        status: {"tr": tr_label, "en": en_label}
        for status, (tr_label, en_label) in STATUS_LABELS.items()
    }
    errors = out.setdefault("errors", {})
    # Without a recent re-authentication: refused, nothing queued.
    capture.recent(admin, minutes_ago=30)
    errors["create_reauth_required"] = capture.error(
        admin.post(f"{API}/evidence", json={"from": "2026-10-04", "to": "2026-10-05"}),
        403,
        "reauth_required",
    )
    errors["create_forbidden_auditor"] = capture.error(
        auditor.post(f"{API}/evidence", json={"from": "2026-10-04", "to": "2026-10-05"}),
        403,
        "forbidden",
    )
    capture.recent(admin)
    with disk_free(900):
        out["created"] = capture.json(
            admin.post(f"{API}/evidence", json={"from": "2026-10-04", "to": "2026-10-05"}), 202
        )
        wait_builds(capture)
        created = capture.json(
            admin.post(
                f"{API}/evidence",
                json={"lookup_id": lookup_ids["exact"], "content": "extract", "password": True},
            ),
            202,
        )
        created["password"] = PASSWORD_PLACEHOLDER
        out["created_password"] = created
        wait_builds(capture)
        expired = capture.json(
            admin.post(
                f"{API}/evidence",
                json={
                    "from": "2026-10-07",
                    "to": "2026-10-07",
                    "sources": ["fortigate"],
                    "reference": "Savcılık 2026/301",
                },
            ),
            202,
        )
        wait_builds(capture)
        # A vault file changed after sealing: the package is refused by its own verifier.
        path = capture.layout.vault_day(TAMPERED) / "fortigate.log.gz"
        original = path.read_bytes()
        vault.tamper(path)
        failed = capture.json(
            admin.post(f"{API}/evidence", json={"from": "2026-10-08", "to": "2026-10-08"}), 202
        )
        wait_builds(capture)
        out["verify_tampered"] = capture.json(
            admin.post(f"{API}/seals/{TAMPERED.isoformat()}/verify"), 200
        )
        path.write_bytes(original)
        out["verify_restored"] = capture.json(
            admin.post(f"{API}/seals/{TAMPERED.isoformat()}/verify"), 200
        )

        # The package's retention ran out; until the nightly purge marks it, it stays "ready"
        # (not downloadable any more) ...
        expired_id = expired["package"]["id"]
        with capture.factory() as session:
            session.execute(
                update(EvidencePackage)
                .where(EvidencePackage.id == expired_id)
                .values(expires_at=utc_now() - timedelta(hours=1))
            )
            session.commit()
        out["ready_expired"] = capture.json(admin.get(f"{API}/evidence/{expired_id}"), 200)
        if out["ready_expired"]["status"] != "ready" or out["ready_expired"]["downloadable"]:
            raise SystemExit(f"unexpected expired package: {out['ready_expired']}")
        # ... then the purge marks it.
        with capture.factory() as session:
            session.execute(
                update(EvidencePackage)
                .where(EvidencePackage.id == expired_id)
                .values(status="expired")
            )
            session.commit()

        # A request that waits (the builder is busy): duplicates and limits are refused.
        with mock.patch.object(EvidenceBuilder, "submit", return_value=None):
            out["created_queued"] = capture.json(
                admin.post(
                    f"{API}/evidence",
                    json={"from": "2026-10-10", "to": "2026-10-10", "password": True},
                ),
                202,
            )
            out["created_queued"]["password"] = PASSWORD_PLACEHOLDER
            errors["duplicate"] = capture.error(
                admin.post(
                    f"{API}/evidence",
                    json={"from": "2026-10-10", "to": "2026-10-10", "password": True},
                ),
                409,
                "evidence_duplicate",
            )
            capture.setting("evidence.max_unfinished_per_user", 1)
            errors["rate_limited"] = capture.error(
                admin.post(f"{API}/evidence", json={"from": "2026-10-09", "to": "2026-10-09"}),
                429,
                "rate_limited",
            )
            capture.setting("evidence.max_unfinished_per_user", 2)
        errors["days_not_sealed"] = capture.error(
            admin.post(f"{API}/evidence", json={"from": "2026-10-10", "to": "2026-10-12"}),
            409,
            "days_not_sealed",
        )
        errors["scope_invalid"] = capture.error(
            admin.post(f"{API}/evidence", json={"from": "2026-10-09", "to": "2026-10-01"}),
            422,
            "scope_invalid",
        )
    with disk_free(30):
        errors["disk_space_low"] = capture.error(
            admin.post(f"{API}/evidence", json={"from": "2026-10-04", "to": "2026-10-04"}),
            507,
            "disk_space_low",
        )

    ready_id = out["created"]["package"]["id"]
    capture.recent(admin)
    out["download"] = capture.file(admin.get(f"{API}/evidence/{ready_id}/download"))
    capture.recent(auditor)
    capture.file(auditor.get(f"{API}/evidence/{ready_id}/download"))
    capture.recent(auditor, minutes_ago=30)
    errors["download_reauth_required"] = capture.error(
        auditor.get(f"{API}/evidence/{ready_id}/download"), 403, "reauth_required"
    )
    errors["download_gone"] = capture.error(
        admin.get(f"{API}/evidence/{expired['package']['id']}/download"), 410, "evidence_gone"
    )
    errors["download_failed_package"] = capture.error(
        admin.get(f"{API}/evidence/{failed['package']['id']}/download"), 409, "evidence_failed"
    )
    out["list"] = capture.json(admin.get(f"{API}/evidence"), 200)
    out["list_auditor"] = capture.json(auditor.get(f"{API}/evidence"), 200)
    out["details"] = {
        str(item["id"]): capture.json(admin.get(f"{API}/evidence/{item['id']}"), 200)
        for item in out["list"]["items"]
    }


def capture_seals(capture: Capture, admin: Any, auditor: Any) -> None:
    out = capture.samples["seals"]
    out["verify"] = capture.json(admin.post(f"{API}/seals/{VERIFIED.isoformat()}/verify"), 200)
    out["verify_quick"] = capture.json(
        auditor.post(f"{API}/seals/{date(2026, 10, 5).isoformat()}/verify", params={"deep": False}),
        200,
    )
    out["calendars"] = {
        month: capture.json(admin.get(f"{API}/seals/calendar", params={"month": month}), 200)
        for month in ("2026-09", "2026-10")
    }
    out["calendar_current"] = capture.json(admin.get(f"{API}/seals/calendar"), 200)
    today = site_day(utc_now())
    first = date(2026, 10, 1)
    out["days"] = {
        day.isoformat(): capture.json(admin.get(f"{API}/seals/{day.isoformat()}"), 200)
        for day in (first + timedelta(days=n) for n in range(31))
        if day <= today
    }
    manifest = admin.get(f"{API}/seals/{VERIFIED.isoformat()}/manifest")
    token = admin.get(f"{API}/seals/{VERIFIED.isoformat()}/token")
    out["manifest_file"] = {**capture.file(manifest), "text": manifest.text}
    out["token_file"] = {
        **capture.file(token),
        "base64": base64.b64encode(token.content).decode("ascii"),
    }
    errors = out.setdefault("errors", {})
    errors["seal_now_forbidden"] = capture.error(
        auditor.post(f"{API}/seals/{WAITING.isoformat()}/seal"), 403, "forbidden"
    )
    # "Şimdi mühürle" (ADR-0014): the oldest due day through the agent and the Seal task (the
    # task's answer is the test helper's; the vault stays as it is).
    agent = capture.agent()
    errors["seal_day_sealed"] = capture.error(
        admin.post(f"{API}/seals/{VERIFIED.isoformat()}/seal"), 409, "seal_day_sealed"
    )
    errors["seal_not_due"] = capture.error(
        admin.post(f"{API}/seals/{WAITING.isoformat()}/seal"), 409, "seal_not_due"
    )
    accepted = capture.json(admin.post(f"{API}/seals/{FAILED.isoformat()}/seal"), 202)
    out["seal_now"] = accepted
    errors["seal_request_pending"] = capture.error(
        admin.post(f"{API}/seals/{FAILED.isoformat()}/seal"), 409, "seal_request_pending"
    )
    out["seal_now_queued"] = capture.json(admin.get(accepted["status_url"]), 200)
    agent.process()
    out["seal_now_done"] = capture.json(admin.get(accepted["status_url"]), 200)
    errors["verify_not_sealed"] = capture.error(
        admin.post(f"{API}/seals/{WAITING.isoformat()}/verify"), 404, "not_found"
    )


class _CreditProvider:
    """Stands in for the Zamane client's credit query in the credit log (the real task writes
    the same lines; only the numbers are made up)."""

    def __init__(self, name: str, values: list[int | None]) -> None:
        self.name = name
        self._values = iter(values)

    def credits(self) -> int | None:
        value = next(self._values)
        if value is None:
            raise TsaUnavailableError("Kamu SM sunucusuna ulaşılamadı (zaman aşımı)")
        return value


def _fixed(moment: datetime) -> Callable[[], datetime]:
    """A clock that always says ``moment`` (one credit query per day)."""
    return lambda: moment


def capture_credits(capture: Capture, admin: Any) -> None:
    out = capture.samples["credits"]
    # "Kontör sorgula" (ADR-0014): through the agent and the Requests task, once a minute.
    agent = capture.agent()
    out["query"] = capture.json(admin.post(f"{API}/credits/query"), 202)
    out["query_limited"] = capture.error(
        admin.post(f"{API}/credits/query"), 429, "admin_rate_limited"
    )
    agent.process()
    out["query_done"] = capture.json(admin.get(out["query"]["status_url"]), 200)
    layout = capture.layout
    first = _local(date(2026, 9, 14), 6, 30)

    def history(provider: Any, days: int) -> None:
        layout.credit_log.unlink(missing_ok=True)
        for n in range(days):
            moment = first + timedelta(days=n)
            check_credit(
                provider,
                layout,
                capture.config.tsa.credit_alert_threshold,
                clock=_fixed(moment),
            )

    history(MockTsaProvider(mock_tsa(layout)), 3)
    out["mock"] = credits_router.credits(None, layout, capture.config).model_dump(mode="json")  # type: ignore[arg-type]
    values: list[int | None] = []
    remaining = 9_731
    for n in range(28):
        remaining -= 3 + (n % 3) + (2 if n % 7 == 0 else 0)
        values.append(None if n == 19 else remaining)
    history(_CreditProvider("zamane", values), len(values))
    production = capture.config.model_copy(
        update={
            "tsa": capture.config.tsa.model_copy(
                update={"provider": "zamane", "url": "http://zd.kamusm.gov.tr"}
            )
        }
    )
    out["kamusm"] = credits_router.credits(None, layout, production).model_dump(mode="json")  # type: ignore[arg-type]
    low = production.model_copy(
        update={"tsa": production.tsa.model_copy(update={"credit_alert_threshold": 10_000})}
    )
    out["kamusm_low"] = credits_router.credits(None, layout, low).model_dump(mode="json")  # type: ignore[arg-type]


# -- devices and persons --------------------------------------------------------------------------


def _upload(client: Any, kind: str, text: str | bytes, *, dry_run: bool = True) -> Any:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return client.post(
        f"{API}/imports/{kind}",
        params={"dry_run": "true" if dry_run else "false"},
        files={"file": ("import.csv", data, "text/csv")},
    )


def capture_devices(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> None:
    """The "Cihazlar ve Kişiler" page: after the other captures, so their lookups keep the site
    as it was (an assignment changed here never changes a stored outcome anyway)."""
    out = capture.samples["devices"]
    errors = out.setdefault("errors", {})
    with capture.factory() as session:
        selin_id = session.query(Person.id).filter(Person.name == PERSON_NAME).scalar()
        finance = session.query(Department.id).filter(Department.name == "Finans").scalar()
        office = session.query(Department.id).filter(Department.name == "İdari İşler").scalar()
    rogue, others = ROGUE_MAC, [mac for _, mac, _, _ in OTHERS]

    out["departments"] = capture.json(admin.get(f"{API}/departments"), 200)
    out["department_created"] = capture.json(
        admin.post(f"{API}/departments", json={"name": "Ar-Ge", "notes": "Kuruluş 2026"}), 201
    )
    research = out["department_created"]["id"]
    # Unique regardless of case, with the Turkish letters (İ/i, Ş/ş).
    errors["department_exists"] = capture.error(
        admin.post(f"{API}/departments", json={"name": "İDARİ İŞLER"}), 409, "department_exists"
    )
    out["department_updated"] = capture.json(
        admin.patch(
            f"{API}/departments/{out['department_created']['id']}",
            json={"name": "Araştırma ve Geliştirme"},
        ),
        200,
    )
    out["vlans"] = capture.json(admin.get(f"{API}/vlans"), 200)
    # The staff Wi-Fi is linked to a department: its devices without an owner count there.
    out["vlan_updated"] = capture.json(
        admin.patch(f"{API}/vlans", json={"subnet": "172.20.60.0/24", "department_id": office}),
        200,
    )
    # A VLAN entered on the page, its refusals, an edit that moves it and its deletion (the
    # plan is as before afterwards: the later captures see the same network).
    lab = {
        "subnet": "172.20.70.0/24",
        "vlan_id": 70,
        "name_tr": "Laboratuvar",
        "purpose": "Test bench PCs",
        "dhcp_server": "dhcp70",
        "department_id": research,
        "guest": False,
        "notes": "Kat 2",
    }
    out["vlan_created"] = capture.json(admin.post(f"{API}/vlans", json=lab), 201)
    errors["vlan_exists"] = capture.error(
        admin.post(f"{API}/vlans", json={**lab, "vlan_id": None, "dhcp_server": None}),
        409,
        "vlan_exists",
    )
    errors["vlan_id_taken"] = capture.error(
        admin.post(
            f"{API}/vlans",
            json={"subnet": "172.20.71.0/24", "vlan_id": 43, "name_tr": "Depo"},
        ),
        409,
        "vlan_id_taken",
    )
    errors["dhcp_server_taken"] = capture.error(
        admin.post(
            f"{API}/vlans",
            json={"subnet": "172.20.71.0/24", "dhcp_server": "dhcp43", "name_tr": "Depo"},
        ),
        409,
        "dhcp_server_taken",
    )
    out["vlan_edited"] = capture.json(
        admin.patch(
            f"{API}/vlans",
            json={
                "subnet": "172.20.70.0/24",
                "new_subnet": "172.20.71.0/24",
                "name_tr": "Ar-Ge Laboratuvarı",
                "guest": True,
            },
        ),
        200,
    )
    deleted = admin.delete(f"{API}/vlans", params={"subnet": "172.20.71.0/24"})
    if deleted.status_code != 204:
        raise SystemExit(f"DELETE vlan: expected 204, got {deleted.status_code}")

    can = capture.json(
        admin.post(
            f"{API}/persons",
            json={
                "name": "Can Öztürk",
                "department_id": finance,
                "title": "Mali Analist",
                "email": "can.ozturk@example.com",
                "employee_no": "1002",
            },
        ),
        201,
    )
    out["person_created"] = can
    capture.json(admin.patch(f"{API}/persons/{selin_id}", json={"employee_no": "1001"}), 200)
    gone = capture.json(
        admin.post(
            f"{API}/persons",
            json={
                "name": "Deniz Işık",
                "employee_no": "0950",
                "active": False,
                "usage_excluded": True,
            },
        ),
        201,
    )
    errors["employee_no_taken"] = capture.error(
        admin.post(f"{API}/persons", json={"name": "Başka Biri", "employee_no": "1002"}),
        409,
        "employee_no_taken",
    )
    out["person_updated"] = capture.json(
        admin.patch(f"{API}/persons/{can['id']}", json={"title": "Finans Müdürü"}), 200
    )

    # Bulk assignment, then the phone moves to Selin: one person with two MACs, history kept.
    out["assigned"] = capture.json(
        admin.post(
            f"{API}/devices/assign",
            json={
                "macs": [PHONE_MAC, others[0]],
                "person_id": can["id"],
                "valid_from": _local(date(2026, 10, 10), 10).isoformat(),
                "note": "Zimmet formu 2026/21",
            },
        ),
        200,
    )
    out["reassigned"] = capture.json(
        admin.post(
            f"{API}/devices/assign",
            json={
                "macs": [PHONE_MAC],
                "person_id": selin_id,
                "valid_from": _local(date(2026, 10, 12), 9).isoformat(),
            },
        ),
        200,
    )
    out["unassigned"] = capture.json(
        admin.post(
            f"{API}/devices/assign",
            json={
                "macs": [others[0]],
                "person_id": None,
                "valid_from": _local(date(2026, 10, 12), 18).isoformat(),
            },
        ),
        200,
    )
    errors["assignment_overlap"] = capture.error(
        admin.post(
            f"{API}/devices/assign",
            json={
                "macs": [PC_MAC, others[1]],
                "person_id": can["id"],
                "valid_from": _local(date(2026, 9, 20), 9).isoformat(),
            },
        ),
        409,
        "assignment_overlap",
    )
    errors["assignment_in_future"] = capture.error(
        admin.post(
            f"{API}/devices/assign",
            json={
                "macs": [others[1]],
                "person_id": can["id"],
                "valid_from": (utc_now() + timedelta(days=3)).isoformat(),
            },
        ),
        409,
        "assignment_in_future",
    )
    errors["device_not_found"] = capture.error(
        admin.post(
            f"{API}/devices/assign",
            json={"macs": ["02:00:00:00:00:01"], "person_id": can["id"]},
        ),
        404,
        "device_not_found",
    )
    errors["person_inactive"] = capture.error(
        admin.post(f"{API}/devices/assign", json={"macs": [others[1]], "person_id": gone["id"]}),
        409,
        "person_inactive",
    )
    errors["validation_error"] = capture.error(
        admin.post(f"{API}/devices/assign", json={"macs": ["zz:zz"], "person_id": can["id"]}),
        422,
        "validation_error",
    )
    out["device_updated"] = capture.json(
        admin.patch(
            f"{API}/devices/{rogue}",
            json={
                "name": "Eski yazıcı (statik IP)",
                "type": "printer",
                "tags": ["statik", "kat-1"],
                "notes": f"ARP çakışması: {PC} adresini de yanıtlıyor.",
            },
        ),
        200,
    )

    out["list"] = capture.json(admin.get(f"{API}/devices"), 200)
    out["list_unknown"] = capture.json(admin.get(f"{API}/devices", params={"unknown": True}), 200)
    out["list_search"] = capture.json(
        admin.get(f"{API}/devices", params={"q": "pln-pc14", "sort": "mac", "order": "asc"}), 200
    )
    out["list_page"] = capture.json(
        admin.get(f"{API}/devices", params={"limit": 2, "offset": 2}), 200
    )
    out["detail"] = capture.json(admin.get(f"{API}/devices/{PC_MAC}"), 200)
    out["detail_phone"] = capture.json(admin.get(f"{API}/devices/{PHONE_MAC}"), 200)
    out["persons"] = capture.json(admin.get(f"{API}/persons"), 200)
    out["persons_search"] = capture.json(admin.get(f"{API}/persons", params={"q": "ozturk"}), 200)
    out["person_detail"] = capture.json(admin.get(f"{API}/persons/{selin_id}"), 200)

    out["devices_csv"] = capture.csv(admin.get(f"{API}/devices/export.csv"))
    out["persons_csv"] = capture.csv(admin.get(f"{API}/persons/export.csv"))
    out["template_persons"] = capture.csv(admin.get(f"{API}/imports/persons/template.csv"))
    out["template_assignments"] = capture.csv(admin.get(f"{API}/imports/assignments/template.csv"))

    persons_file = (
        "name;department;title;email;employee_no;active;notes\r\n"
        "Can Öztürk;Finans;Finans Direktörü;can.ozturk@example.com;1002;evet;\r\n"
        "Merve Aydın;İdari İşler;İdari İşler Uzmanı;merve.aydin@example.com;1003;evet;\r\n"
    )
    out["import_persons_preview"] = capture.json(_upload(admin, "persons", persons_file), 200)
    bad_persons = (
        "name;department;email;employee_no\r\n"
        "Ali Veli;Hayali Departman;;2001\r\n"
        ";Planlama;;2002\r\n"
        "Ayşe Kaya;Planlama;ayse@;2003\r\n"
    )
    out["import_persons_errors"] = capture.json(_upload(admin, "persons", bad_persons), 200)
    assignments_file = (
        "mac;employee_no;person_name;valid_from;note\r\n"
        f"{others[1]};1002;;11.10.2026 08:00;Devir teslim\r\n"
        f"{others[2]};;{PERSON_NAME};;\r\n"
    )
    out["import_assignments_preview"] = capture.json(
        _upload(admin, "assignments", assignments_file), 200
    )
    out["import_assignments_applied"] = capture.json(
        _upload(admin, "assignments", assignments_file, dry_run=False), 200
    )
    bad_assignments = (
        "mac;employee_no;person_name;valid_from;note\r\n"
        "02:00:00:00:00:01;1002;;;\r\n"
        f"{PC_MAC};1002;;20.09.2026 09:00;\r\n"
        f"{rogue};0950;;;\r\n"
    )
    out["import_assignments_errors"] = capture.json(
        _upload(admin, "assignments", bad_assignments, dry_run=False), 200
    )
    errors["csv_encoding"] = capture.error(
        _upload(admin, "persons", "name\r\nIşık\r\n".encode("cp1254")), 422, "csv_encoding"
    )
    errors["csv_header"] = capture.error(
        _upload(admin, "persons", "Ad;Soyad\r\nA;B\r\n"), 422, "csv_header"
    )
    errors["upload_invalid"] = capture.error(
        admin.post(f"{API}/imports/persons", content=b"name", headers={"content-type": "text/csv"}),
        422,
        "upload_invalid",
    )
    errors["privacy_notice_required"] = capture.error(
        newcomer.get(f"{API}/devices"), 403, "privacy_notice_required"
    )
    errors["forbidden"] = capture.error(auditor.get(f"{API}/devices"), 403, "forbidden")
    errors["detail_not_found"] = capture.error(
        admin.get(f"{API}/devices/02:00:00:00:00:01"), 404, "device_not_found"
    )


# -- usage ----------------------------------------------------------------------------------------


def capture_usage(server: TestServer, template: str, root: Path) -> dict[str, Any]:
    """The usage pages, on the golden usage site in a database of their own."""
    out: dict[str, Any] = {}
    errors: dict[str, Any] = out.setdefault("errors", {})
    day = USAGE_DAY.isoformat()
    period = {"from": day, "to": day}
    usage = f"{API}/usage"
    with server.database(template) as name:
        engine = engine_from_url(server.url_for(name), application="capture", poolclass=NullPool)
        try:
            capture = Capture(root / "usage", engine)
            with capture.factory() as session:
                golden = build_golden_site(session, capture.layout, day=USAGE_DAY)
                rebuild_usage_day(session, USAGE_DAY, now=utc_now())
                rebuild_usage_day(session, USAGE_DAY + timedelta(days=1), now=utc_now())
            admin = capture.client("admin", "tolga", "Tolga Yılmaz")
            hr = capture.client("hr", "selin.celik", "Selin Çelik")
            viewer = capture.client("viewer", "rapor.izleyici", "Rapor İzleyici")
            newcomer = capture.client("hr", "yeni.ik", "Yeni İK Uzmanı", notice=False)

            def get(client: Any, path: str, **params: Any) -> Any:
                return capture.json(client.get(f"{usage}{path}", params={**period, **params}), 200)

            ayse, burak, can = (f"person:{i}" for i in (golden.ayse, golden.burak, golden.can))
            out["company"] = get(hr, "/company")
            out["company_work_hours"] = get(hr, "/company", hours="work")
            out["company_off_hours"] = get(hr, "/company", hours="off")
            out["company_week"] = get(hr, "/company", **{"from": "2026-10-06"})
            out["company_today"] = capture.json(hr.get(f"{usage}/company"), 200)
            out["subjects_persons"] = get(hr, "/subjects", kind="person")
            out["subjects_devices"] = get(hr, "/subjects", kind="device")
            out["subjects_departments"] = get(hr, "/subjects", kind="department")
            out["subjects_department_filter"] = get(
                hr, "/subjects", kind="person", department="Finans"
            )
            out["subjects_search"] = get(hr, "/subjects", kind="person", q="ayşe")
            out["subjects_page"] = get(
                hr, "/subjects", kind="person", sort="label", order="asc", limit=1, offset=1
            )
            out["subject_person"] = get(hr, f"/subjects/{ayse}")
            out["subject_reassigned_before"] = get(hr, f"/subjects/{burak}")
            out["subject_reassigned_after"] = get(hr, f"/subjects/{can}")
            out["subject_device"] = get(hr, "/subjects/device:3c:52:82:00:10:02")
            out["subject_ip"] = get(hr, "/subjects/ip:172.20.41.77")
            out["subject_week"] = get(hr, f"/subjects/{ayse}", **{"from": "2026-10-06"})
            out["sites"] = get(hr, "/sites")
            out["sites_department_filter"] = get(hr, "/sites", department="Finans")
            out["sites_category_filter"] = get(hr, "/sites", category="Video")
            out["sites_search"] = get(hr, "/sites", q="portal", sort="users")
            out["site"] = get(hr, "/sites/portal.example")
            out["site_department_filter"] = get(hr, "/sites/portal.example", department="Finans")
            out["categories"] = get(hr, "/categories")
            out["categories_department_filter"] = get(hr, "/categories", department="Finans")
            out["category"] = get(hr, "/categories/Video")
            out["category_department_filter"] = get(
                hr, "/categories/İş Uygulamaları", department="Planlama"
            )
            out["category_unknown"] = get(hr, "/categories/Bilinmiyor")
            exports = {
                "export_company_csv": ("csv", {"view": "company"}),
                "export_subjects_csv": ("csv", {"view": "subjects", "kind": "person"}),
                "export_subjects_department_csv": (
                    "csv",
                    {"view": "subjects", "kind": "person", "department": "Finans"},
                ),
                "export_departments_csv": ("csv", {"view": "subjects", "kind": "department"}),
                "export_subject_csv": ("csv", {"view": "subject", "key": ayse}),
                "export_site_csv": ("csv", {"view": "site", "domain": "portal.example"}),
                "export_category_csv": ("csv", {"view": "category", "category": "Video"}),
                "export_subject_xlsx": ("xlsx", {"view": "subject", "key": ayse}),
                "export_sites_xlsx": ("xlsx", {"view": "sites", "department": "Finans"}),
                "export_categories_xlsx": ("xlsx", {"view": "categories"}),
                "export_company_xlsx": ("xlsx", {"view": "company"}),
            }
            for sample, (kind, params) in exports.items():
                response = hr.get(f"{usage}/export.{kind}", params={**period, **params})
                out[sample] = capture.csv(response) if kind == "csv" else capture.file(response)

            # Rebuilds: queued, then run by the worker's job.
            requested = capture.json(admin.post(f"{usage}/rebuild", json=period), 202)
            out["rebuild_requested"] = requested
            out["rebuild_queued"] = capture.json(
                admin.get(f"{usage}/rebuild/{requested['job_id']}"), 200
            )
            process_requests(capture.factory, now=utc_now())
            out["rebuild_done"] = capture.json(
                admin.get(f"{usage}/rebuild/{requested['job_id']}"), 200
            )

            # Names by default (owner decision 2026-10-20), pseudonyms only on request.
            out["viewer_subjects"] = get(viewer, "/subjects", kind="person")
            out["viewer_company"] = get(viewer, "/company")
            errors["pseudonyms_without_key"] = capture.error(
                admin.get(f"{usage}/pseudonyms"), 409, "pseudonym_key_missing"
            )
            capture.setting("usage.pseudonymize_anonymous_role", True)
            errors["pseudonym_key_missing"] = capture.error(
                viewer.get(f"{usage}/subjects", params=period), 409, "pseudonym_key_missing"
            )
            key_path = capture.config.secrets_dir / "secret.key"
            if not key_path.exists():
                SecretStore.create_key(key_path)
            SecretStore(key_path, capture.config.secrets_dir / "secrets.json").set(
                USAGE_PSEUDONYM_KEY, USAGE_PSEUDONYM_VALUE
            )
            pseudonymous = get(viewer, "/subjects", kind="person")
            out["viewer_subjects_pseudonymous"] = pseudonymous
            token = pseudonymous["items"][0]["subject"]["key"]
            out["viewer_subject_pseudonymous"] = get(viewer, f"/subjects/{token}")
            out["viewer_sites_pseudonymous"] = get(viewer, "/sites/portal.example")
            out["hr_subjects_with_pseudonyms_on"] = get(hr, "/subjects", kind="person")
            out["pseudonyms"] = capture.json(admin.get(f"{usage}/pseudonyms"), 200)
            out["pseudonyms_search"] = capture.json(
                admin.get(f"{usage}/pseudonyms", params={"q": "can"}), 200
            )
            capture.setting("usage.pseudonymize_anonymous_role", False)

            errors["usage_period"] = capture.error(
                hr.get(f"{usage}/company", params={"from": "2026-10-12", "to": "2026-10-01"}),
                422,
                "usage_period",
            )
            errors["usage_subject_not_found"] = capture.error(
                hr.get(f"{usage}/subjects/person:999999", params=period),
                404,
                "usage_subject_not_found",
            )
            errors["usage_site_not_found"] = capture.error(
                hr.get(f"{usage}/sites/yok.example", params=period), 404, "usage_site_not_found"
            )
            errors["usage_category_not_found"] = capture.error(
                hr.get(f"{usage}/categories/Kumar", params=period),
                404,
                "usage_category_not_found",
            )
            errors["usage_export_view"] = capture.error(
                hr.get(f"{usage}/export.csv", params={**period, "view": "site"}),
                422,
                "usage_export_view",
            )
            errors["usage_rebuild_range"] = capture.error(
                admin.post(f"{usage}/rebuild", json={"from": day, "to": "2099-01-01"}),
                422,
                "usage_rebuild_range",
            )
            errors["usage_rebuild_no_logs"] = capture.error(
                admin.post(f"{usage}/rebuild", json={"from": "2026-10-01", "to": day}),
                422,
                "usage_rebuild_no_logs",
            )
            errors["usage_rebuild_not_found"] = capture.error(
                admin.get(f"{usage}/rebuild/999999"), 404, "usage_rebuild_not_found"
            )
            errors["privacy_notice_required"] = capture.error(
                newcomer.get(f"{usage}/subjects", params=period), 403, "privacy_notice_required"
            )
            errors["forbidden_rebuild"] = capture.error(
                hr.post(f"{usage}/rebuild", json=period), 403, "forbidden"
            )
            errors["forbidden_pseudonyms"] = capture.error(
                viewer.get(f"{usage}/pseudonyms"), 403, "forbidden"
            )
        finally:
            engine.dispose()
    return out


# -- alerts (Güvenlik) and the dashboard (Panel) --------------------------------------------------

#: A switch (VLAN 10, network devices) that went silent 18 minutes ago: "R15 switch-3 sessiz
#: (18 dk)" on the dashboard.
SILENT_SWITCH = "switch-3"


def build_operations(capture: Capture) -> None:
    """What the dashboard reads besides the vault: three watched sources (one silent), the
    collector's status, about 1,280 messages a minute (by device time) around the capture's
    "now", and the hourly clock samples."""
    now = utc_now()
    with capture.factory() as session:
        for name, minutes in (("fortigate", 0.2), ("core-r1", 2.0)):
            source = session.get_one(Source, name)
            source.last_seen = now - timedelta(minutes=minutes)
            source.first_seen = now - timedelta(days=40)
            source.created_at = now - timedelta(days=40)
            source.heartbeat_minutes = 10 if name == "fortigate" else 15
        session.add(
            Source(
                name=SILENT_SWITCH,
                type="mikrotik",
                address="172.20.10.24",
                heartbeat_minutes=15,
                first_seen=now - timedelta(days=40),
                last_seen=now - timedelta(minutes=18),
                created_at=now - timedelta(days=40),
            )
        )
        # 80 minutes around "now" (the capture's clock runs on while it works): 1,150 to
        # 1,409 messages a minute, a ninth of them from the core router.
        session.execute(
            text(
                "INSERT INTO messages (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, type, text) "
                "SELECT t, t, CASE WHEN n % 9 = 0 THEN 'core-r1' ELSE 'fortigate' END, "
                "CASE WHEN n % 9 = 0 THEN '172.20.10.1'::inet ELSE '172.31.255.1'::inet END, "
                ":day, m * 2000 + n, 0, 'traffic', '' "
                "FROM generate_series(0, 79) AS m, "
                "LATERAL generate_series(1, 1150 + (m * 37) % 260) AS n, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + m * interval '1 minute' "
                "+ n * interval '60 seconds' / (1151 + (m * 37) % 260) AS t) AS x"
            ),
            {"day": site_day(now), "start": now.replace(second=0) - timedelta(minutes=70)},
        )
        session.commit()
    layout = capture.layout
    layout.collector_status.parent.mkdir(parents=True, exist_ok=True)
    layout.collector_status.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "sources": {
                    "fortigate": {"last_received": (now - timedelta(seconds=4)).isoformat()},
                    "core-r1": {"last_received": (now - timedelta(seconds=50)).isoformat()},
                },
            }
        ),
        encoding="utf-8",
    )
    samples = layout.clock_samples(site_day(now))
    samples.parent.mkdir(parents=True, exist_ok=True)
    samples.write_text(
        "".join(
            json.dumps(
                {
                    "time": (now - timedelta(minutes=minutes)).isoformat(timespec="seconds"),
                    "server": "172.20.10.1",
                    "offset_us": offset,
                    "delay_us": 900,
                    "stratum": 2,
                    "ok": True,
                }
            )
            + "\n"
            for minutes, offset in ((110, 2_800), (50, 3_200))
        ),
        encoding="utf-8",
    )


def build_alerts(capture: Capture) -> dict[str, int]:
    """Alerts as the worker writes them: the Seal task's failure of 11.10 imported from
    ``alerts.jsonl``, R15 for the silent switch from the real check (e-mailed to two
    recipients), the IP conflict of the site, a disk alert, a new log source and a Kamu SM
    credit alert the check resolved itself."""
    now = utc_now()
    import_alerts(capture.factory, capture.layout, now=now)
    with capture.factory() as session:
        check_silent_sources(session, capture.layout, now=now, started_at=now - timedelta(hours=2))
        silent = session.scalars(select(Alert).where(Alert.rule == "R15")).one()
        for recipient, status in (
            ("tolga@example.com", "sent"),
            ("bilgi.islem@example.com", "failed"),
        ):
            session.add(
                AlertDelivery(
                    alert_id=silent.id,
                    kind="new",
                    recipient=recipient,
                    status=status,
                    attempts=1 if status == "sent" else 6,
                    last_error=None if status == "sent" else "550 5.1.1 mailbox unavailable",
                    sent_at=now if status == "sent" else None,
                    message_id="<capture-1@arcilogger>" if status == "sent" else None,
                )
            )
        disk = {"drive": "D:", "used_percent": 84, "threshold_percent": 80}
        first_disk = open_alert(
            session,
            rule="R20-disk",
            severity="high",
            title="Disk dolmak üzere: D: %84 dolu (eşik %80)",
            details={**disk, "title_en": "Disk almost full: D: 84% used"},
            key="R20-disk:D:",
            time=now - timedelta(hours=26),
            now=now - timedelta(hours=26),
        ).alert
        sender = open_alert(
            session,
            rule="new-log-source",
            severity="medium",
            title="Yeni log kaynağı: 172.20.10.77 (kaydediliyor)",
            details={"address": "172.20.10.77", "written": True},
            key="new-log-source:172.20.10.77",
            time=now - timedelta(hours=5),
            now=now - timedelta(hours=5),
        ).alert
        credit = open_alert(
            session,
            rule="R20-credit",
            severity="high",
            title="Kamu SM kontörü azaldı: 420 kaldı (eşik 500)",
            details={"credits": 420, "threshold": 500, "title_en": "Kamu SM credit low"},
            key="R20-credit",
            time=now - timedelta(days=3),
            now=now - timedelta(days=3),
        ).alert
        resolve(credit, reason="credit_ok", now=now - timedelta(days=2, hours=20))
        conflict = session.scalars(select(Alert).where(Alert.rule == "ip_conflict")).first()
        seal = session.scalars(select(Alert).where(Alert.rule == "R20-seal")).first()
        session.commit()
        if conflict is None or seal is None:
            raise SystemExit("the site has no IP conflict or no failed sealing alert")
        return {
            "silent": silent.id,
            "first_disk": first_disk.id,
            "sender": sender.id,
            "credit": credit.id,
            "conflict": conflict.id,
            "seal": seal.id,
        }


def capture_alerts(capture: Capture, admin: Any, auditor: Any) -> None:
    """The Security page: the workflow on real alerts (a person closes the disk alert while the
    disk is still full, the check opens a new one, re-opening the first is refused), notes,
    a false alarm, the list, details and the summary."""
    out = capture.samples["alerts"]
    errors = out.setdefault("errors", {})
    ids = build_alerts(capture)

    def state(alert_id: int, value: str, note: str | None = None) -> Any:
        body: dict[str, Any] = {"state": value}
        if note is not None:
            body["note"] = note
        return admin.post(f"{API}/alerts/{alert_id}/state", json=body)

    out["state_resolved"] = capture.json(
        state(ids["first_disk"], "resolved", "Eski kurulum dosyaları silindi."), 200
    )
    with capture.factory() as session:  # the disk is still 84 % full: the check alerts again
        second_disk = open_alert(
            session,
            rule="R20-disk",
            severity="high",
            title="Disk dolmak üzere: D: %84 dolu (eşik %80)",
            details={"drive": "D:", "used_percent": 84, "threshold_percent": 80},
            key="R20-disk:D:",
            time=utc_now(),
        ).alert
        session.commit()
        second_id = second_disk.id
    errors["alert_key_active"] = capture.error(
        state(ids["first_disk"], "open"), 409, "alert_key_active"
    )
    out["state_acknowledged"] = capture.json(
        state(second_id, "acknowledged", "Disk büyütülecek; BT'ye iş kaydı açıldı (#4711)."), 200
    )
    out["state_dismissed"] = capture.json(
        state(
            ids["conflict"],
            "dismissed",
            "Yazıcının sabit IP'si DHCP havuzundaydı; yazıcı Ağ Cihazları VLAN'ına (10) taşındı.",
        ),
        200,
    )
    capture.json(state(ids["sender"], "resolved", "Yeni erişim noktası; kaynaklara eklendi."), 200)
    out["state_reopened"] = capture.json(state(ids["sender"], "open"), 200)
    out["note_created"] = capture.json(
        admin.post(
            f"{API}/alerts/{ids['sender']}/notes",
            json={"text": "DEVICE-SETUP bölüm 4'e göre yapılandırma bekleniyor."},
        ),
        201,
    )
    errors["alert_state_unchanged"] = capture.error(
        state(ids["sender"], "open"), 409, "alert_state_unchanged"
    )
    errors["alert_transition_not_allowed"] = capture.error(
        state(ids["conflict"], "acknowledged"), 409, "alert_transition_not_allowed"
    )
    errors["alert_not_found"] = capture.error(
        admin.get(f"{API}/alerts/999999"), 404, "alert_not_found"
    )
    errors["note_too_long"] = capture.error(
        admin.post(f"{API}/alerts/{ids['sender']}/notes", json={"text": "x" * 2001}),
        422,
        "validation_error",
    )
    errors["forbidden"] = capture.error(auditor.get(f"{API}/alerts"), 403, "forbidden")

    out["list"] = capture.json(admin.get(f"{API}/alerts"), 200)
    out["list_active"] = capture.json(
        admin.get(f"{API}/alerts", params={"state": "open,acknowledged", "sort": "severity"}), 200
    )
    out["list_search"] = capture.json(admin.get(f"{API}/alerts", params={"q": "switch-3"}), 200)
    out["list_page"] = capture.json(admin.get(f"{API}/alerts", params={"limit": 3}), 200)
    out["detail"] = capture.json(admin.get(f"{API}/alerts/{ids['silent']}"), 200)
    out["detail_dismissed"] = capture.json(admin.get(f"{API}/alerts/{ids['conflict']}"), 200)
    out["detail_auto_resolved"] = capture.json(admin.get(f"{API}/alerts/{ids['credit']}"), 200)
    out["detail_seal"] = capture.json(admin.get(f"{API}/alerts/{ids['seal']}"), 200)
    out["summary"] = capture.json(admin.get(f"{API}/alerts/summary"), 200)


def capture_dashboard(capture: Capture, admin: Any, auditor: Any) -> None:
    """``/dashboard/summary`` for every role: the administrator sees every box, the auditor no
    alerts, the report viewer only the log intake; HR has no dashboard (usage widgets only)."""
    out = capture.samples["dashboard"]
    viewer = capture.client("viewer", "rapor.izleyici", "Rapor İzleyici", notice=False)
    hr = capture.client("hr", "ik.uzmani", "İK Uzmanı")
    out["admin"] = capture.json(admin.get(f"{API}/dashboard/summary"), 200)
    out["auditor"] = capture.json(auditor.get(f"{API}/dashboard/summary"), 200)
    out["viewer"] = capture.json(viewer.get(f"{API}/dashboard/summary"), 200)
    out.setdefault("errors", {})["forbidden"] = capture.error(
        hr.get(f"{API}/dashboard/summary"), 403, "forbidden"
    )


# -- reports (group 2, area R) --------------------------------------------------------------------


def _report_engine(connection: Any, **kwargs: Any) -> dict[str, Any]:
    """The usage engine's report data: the fixed sample, for the requested period."""
    data = report_sample()
    data["kind"] = kwargs["kind"]
    data["period"] = {"from": kwargs["start"].isoformat(), "to": kwargs["end"].isoformat()}
    if not kwargs["person_level"]:
        data["person_level"] = False
        for key in ("persons", "person_sites", "person_hours", "blocked"):
            data[key] = []
    return data


class _ReportSmtp:
    """The capture's SMTP server: accepts every report e-mail except to one address."""

    def __init__(self, refused: str) -> None:
        self.refused = refused

    def send(self, message: Any, recipient: str) -> None:
        if recipient == self.refused:
            raise SmtpSendError("recipient refused: 550 5.1.1 mailbox unavailable", permanent=True)


def capture_reports(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> None:
    """The "Raporlar" page: schedules, "run now", runs made by the worker job, files, preview."""
    out = capture.samples["reports"]
    errors = out.setdefault("errors", {})
    base = f"{API}/reports"
    shipped = capture.json(admin.get(f"{base}/schedules"), 200)
    out["schedules_shipped"] = shipped
    by_kind = {item["kind"]: item["id"] for item in shipped}
    recipients = ["yonetim@ornek.example", "ik@ornek.example"]
    out["schedule_updated"] = capture.json(
        admin.patch(
            f"{base}/schedules/{by_kind['full_day']}",
            json={"enabled": True, "recipients": recipients},
        ),
        200,
    )
    out["schedule_created"] = capture.json(
        admin.post(
            f"{base}/schedules",
            json={
                "name": "Yönetim haftalık özeti",
                "kind": "weekly",
                "enabled": True,
                "time": "08:10",
                "weekday": 5,
                "recipients": ["mudur@ornek.example"],
                "formats": {"email_html": True, "pdf": True, "xlsx": False},
                "person_level": True,
                "pseudonymize": False,
            },
        ),
        201,
    )
    errors["validation_error"] = capture.error(
        admin.post(
            f"{base}/schedules",
            json={
                "name": "Eksik",
                "kind": "weekly",
                "time": "08:10",
                "recipients": [],
                "formats": {"email_html": True, "pdf": False, "xlsx": False},
            },
        ),
        422,
        "validation_error",
    )
    errors["report_recipients_required"] = capture.error(
        admin.patch(f"{base}/schedules/{by_kind['monthly']}", json={"enabled": True}),
        422,
        "report_recipients_required",
    )
    errors["report_smtp_disabled"] = capture.error(
        admin.post(f"{base}/schedules/{by_kind['full_day']}/run"), 409, "report_smtp_disabled"
    )
    out["run_queued"] = capture.json(
        admin.post(f"{base}/schedules/{by_kind['monthly']}/run", json={"send": False}), 202
    )
    custom = capture.json(
        admin.post(
            f"{base}/schedules/{by_kind['full_day']}/run",
            json={
                "from": "2026-10-05T00:00:00+03:00",
                "to": "2026-10-12T00:00:00+03:00",
                "send": False,
            },
        ),
        202,
    )
    doomed = capture.json(
        admin.post(
            f"{base}/schedules",
            json={
                "name": "Silinecek",
                "kind": "end_of_day",
                "time": "17:45",
                "recipients": [],
                "formats": {"email_html": False, "pdf": True, "xlsx": False},
            },
        ),
        201,
    )
    response = admin.delete(f"{base}/schedules/{doomed['id']}")
    if response.status_code != 204:
        raise SystemExit(f"DELETE schedule: expected 204, got {response.status_code}")
    out["schedule_deleted"] = {"status": response.status_code}
    # This morning's scheduled run of "Tam gün raporu", made and e-mailed by the worker job:
    # one recipient gets it, the other address is refused (a partial run).
    with capture.factory() as session:
        schedule = session.get(ReportSchedule, by_kind["full_day"])
        if schedule is None:
            raise SystemExit("no full_day schedule")
        schedule.next_run_at = CLOCK
        session.commit()
    smtp = capture.config.smtp.model_copy(
        update={"enabled": True, "host": "mail.ornek.example", "from_address": "a@b.example"}
    )
    worker_config = capture.config.model_copy(update={"smtp": smtp})
    for _ in range(3):
        report_runner.run_due(
            capture.factory,
            capture.layout,
            worker_config,
            now=utc_now(),
            sender_factory=lambda _config: _ReportSmtp(recipients[1]),
            builder=_report_engine,
        )
    out["runs"] = capture.json(admin.get(f"{base}/runs"), 200)
    scheduled = next(run for run in out["runs"]["items"] if run["triggered_by"] == "schedule")
    out["run_partial"] = capture.json(admin.get(f"{base}/runs/{scheduled['id']}"), 200)
    out["run_files_only"] = capture.json(admin.get(f"{base}/runs/{out['run_queued']['id']}"), 200)
    out["runs_filtered"] = capture.json(
        admin.get(f"{base}/runs", params={"schedule_id": by_kind["full_day"], "limit": 1}), 200
    )
    out["schedules"] = capture.json(admin.get(f"{base}/schedules"), 200)
    for entry in out["run_partial"]["files"]:
        out[f"file_{entry['format']}"] = capture.file(
            admin.get(f"{base}/runs/{scheduled['id']}/files/{entry['name']}")
        )
    errors["file_not_found"] = capture.error(
        admin.get(f"{base}/runs/{scheduled['id']}/files/yok.pdf"), 404, "not_found"
    )
    with capture.factory() as session:
        run = session.get(ReportRun, custom["id"])
        if run is None:
            raise SystemExit("no custom run")
        run.expired_at = utc_now()
        session.commit()
        expired_name = run.files[0]["name"]
    errors["report_expired"] = capture.error(
        admin.get(f"{base}/runs/{custom['id']}/files/{expired_name}"), 410, "report_expired"
    )
    with mock.patch.object(report_data, "build_report_data", _report_engine):
        out["preview_html"] = capture.file(
            admin.get(f"{base}/preview", params={"kind": "full_day"})
        )
        out["preview_pdf"] = capture.file(
            admin.get(f"{base}/preview", params={"kind": "weekly", "format": "pdf"})
        )
    errors["preview_invalid"] = capture.error(
        admin.get(f"{base}/preview", params={"kind": "custom"}), 422, "validation_error"
    )
    errors["privacy_notice_required"] = capture.error(
        newcomer.get(f"{base}/preview", params={"kind": "full_day"}),
        403,
        "privacy_notice_required",
    )
    errors["forbidden"] = capture.error(auditor.get(f"{base}/schedules"), 403, "forbidden")
    errors["run_not_found"] = capture.error(admin.get(f"{base}/runs/999999"), 404, "not_found")


# -- the licence (contract 8.5) -------------------------------------------------------------------

#: The release day and version of the captured licence samples: before the capture's clock (the
#: release floor of the trusted time stays behind it) and fixed, so the samples do not change
#: when ``RELEASE_DATE`` or ``__version__`` is bumped (contract 8.5). The API reads the status
#: without such parameters, so the capture pins the module values for the licence sites.
LICENSE_RELEASE = "2026-10-01"
LICENSE_VERSION = "0.1.0"
#: The version an upgraded site ran before (``version_not_covered``).
PREVIOUS_RELEASE, PREVIOUS_VERSION = "2026-08-17", "0.0.9"
#: The modules of the sold licences in the samples: usage analytics and scheduled reports, no
#: CSV import.
SOLD_MODULES = ("core_5651", "usage_analytics", "usage_reports")
#: When the licensor signed the licences of the capture's day (``make_license``'s default).
ISSUED = datetime(2026, 10, 1, tzinfo=UTC)
#: Another (fictional) Windows server: a licence issued for it, a site moved from it.
OLD_SERVER = ServerIdentity(
    platform="windows",
    machine_id="3b241101-e2bb-4255-8caf-4136c566a962",
    smbios_uuid="4C4C4544-0042-3510-8048-B4C04F4E3732",
)
#: The template's seed created the licence installation row and its ``license.trial_start``
#: entry with the machine's clock (the tables are append-only, hence the triggers): each licence
#: site gets its installation from its own seed, at the moment of its story.
LICENSE_CLEANUP = (
    "ALTER TABLE audit_log DISABLE TRIGGER USER",
    "ALTER TABLE license_installation DISABLE TRIGGER USER",
    "DELETE FROM audit_log WHERE action = 'license.trial_start'",
    "DELETE FROM license_installation",
    "ALTER TABLE license_installation ENABLE TRIGGER USER",
    "ALTER TABLE audit_log ENABLE TRIGGER USER",
)


def site_time(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=SITE_TZ)


def today_at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A moment of the capture's day (13.10.2026, site time)."""
    return site_time(2026, 10, 13, hour, minute, second)


@contextlib.contextmanager
def pinned_release(release: str, version: str) -> Iterator[None]:
    """The running version's release day and number (``arcilogger_core``, read at call time)."""
    with (
        mock.patch.object(arcilogger_core, "RELEASE_DATE", release),
        mock.patch.object(arcilogger_core, "__version__", version),
    ):
        yield


@contextlib.contextmanager
def quiet(logger: str) -> Iterator[None]:
    """No log line of ``logger`` (an error the capture causes on purpose)."""
    target = logging.getLogger(logger)
    previous = target.disabled
    target.disabled = True
    try:
        yield
    finally:
        target.disabled = previous


class LicenseSite:
    """One installation of the licence samples: a database and a data directory of its own."""

    def __init__(self, capture: Capture, clock: ManualClock) -> None:
        self.capture = capture
        self.clock = clock

    def installed(self, moment: datetime) -> None:
        """``arcilogger-db upgrade`` at the installation: the seed creates the installation row
        (its id fixed for the samples: the request code is made from it)."""
        self.clock.at(moment)
        installation_id = UUID(TEST_INSTALLATION_ID)
        with (
            self.capture.factory() as session,
            mock.patch.object(licensing, "uuid4", return_value=installation_id),
        ):
            if not seed(session).license_installation_created:
                raise SystemExit("the seed did not create the licence installation row")
            session.commit()

    def check(self, moment: datetime) -> Mapping[str, Any]:
        """The worker's ``license.check`` job at ``moment`` (state and limit events, audit)."""
        self.clock.at(moment)
        self.capture.layout.state.mkdir(parents=True, exist_ok=True)
        context = JobContext(
            engine=self.capture.engine,
            sessions=self.capture.factory,
            now=utc_now(),
            layout=self.capture.layout,
            config=self.capture.config,
        )
        return job_license_check(context)

    def user(self, role: str, username: str, display_name: str) -> User:
        return make_user(self.capture.factory, role, username=username, display_name=display_name)

    def install(self, user: User, data: bytes) -> None:
        """A licence file installed at the clock's moment by ``user`` on the web page
        (``licensing.install_license``, what ``POST /license`` runs), for a story that began
        before the capture's day."""
        with self.capture.factory() as session:

            def audit(action: str, target: str | None, details: Mapping[str, Any]) -> None:
                append_audit(
                    session,
                    action=action,
                    actor=user.username,
                    actor_id=user.id,
                    target=target,
                    details=details,
                )

            licensing.install_license(
                session,
                data,
                config=self.capture.config,
                layout=self.capture.layout,
                actor=user.username,
                source="web",
                audit=audit,
            )
            session.commit()

    def get(self, client: Any) -> dict[str, Any]:
        """``GET /license`` computed now (the API keeps a status 30 s)."""
        self.capture.app.state.arci.license_cache.invalidate()
        return cast(dict[str, Any], self.capture.json(client.get(f"{API}/license"), 200))

    def upload(self, client: Any, data: bytes, name: str = "ornek.lic") -> Any:
        """``POST /license`` after a recent re-authentication."""
        self.capture.recent(client)
        return client.post(
            f"{API}/license", files={"file": (name, data, "application/octet-stream")}
        )

    def sources(self, *extra: str) -> None:
        """The configured devices and unconfigured senders, all sending (they count, 3.1)."""
        now = utc_now()
        rows = [
            Source(name="fortigate", type="fortigate", address=FIREWALL),
            Source(name="core-r1", type="mikrotik", address="172.20.10.1"),
        ]
        rows += [
            Source(name=f"unknown-{ip}", type="generic", address=ip, unknown=True)
            for ip in ("172.20.10.77", *extra)
        ]
        with self.capture.factory() as session:
            for row in rows:
                row.first_seen = now - timedelta(days=6)
                row.last_seen = now - timedelta(minutes=1)
                session.merge(row)
            session.commit()


@contextlib.contextmanager
def license_site(
    server: TestServer, template: str, root: Path, name: str, clock: ManualClock
) -> Iterator[LicenseSite]:
    """A licence site: a fresh copy of the template without its licence installation."""
    with server.database(template) as database:
        engine = engine_from_url(
            server.url_for(database), application="capture", poolclass=NullPool
        )
        try:
            with engine.begin() as connection:
                for statement in LICENSE_CLEANUP:
                    connection.execute(text(statement))
            yield LicenseSite(Capture(root / f"license-{name}", engine), clock)
        finally:
            engine.dispose()


def expect_state(sample: dict[str, Any], state: str, problem: str | None = None) -> dict[str, Any]:
    """The captured document is in ``state`` (the story went as planned)."""
    if sample.get("status") != state or sample.get("problem") != problem:
        raise SystemExit(
            f"expected licence state {state} ({problem}), got "
            f"{sample.get('status')} ({sample.get('problem')})"
        )
    return sample


def capture_license(server: TestServer, template: str, root: Path) -> dict[str, Any]:
    """``license.json``: every licence state, the uploads and refusals, the locked modules and
    the users limit, each on a site whose story the capture controls."""
    out: dict[str, Any] = {}
    with (
        manual_clock(CLOCK) as clock,
        pinned_release(LICENSE_RELEASE, LICENSE_VERSION),
        trusted_test_keys(),
        fixed_identity() as server_id,
    ):
        old_server_id = server_id_of(OLD_SERVER)

        def sold(suffix: str, *, issued: datetime = ISSUED, **fields: Any) -> bytes:
            """A licence as sold in the samples: analytics and reports, 10 sources, 25 users,
            maintenance until 20.10.2027, for this server, signed with the test key on
            ``issued`` (``fields`` change it)."""
            values: dict[str, Any] = {
                "server_ids": [server_id],
                "modules": SOLD_MODULES,
                "log_sources": 10,
                "users": 25,
                "maintenance_until": date(2027, 10, 20),
                "issued_at": issued,
                "license_id": f"ARCI-L-{issued.astimezone(UTC):%Y%m%d}-{suffix}",
            }
            values.update(fields)
            return make_license(**values)

        with license_site(server, template, root, "main", clock) as site:
            capture_license_main(site, out, sold, old_server_id)
        with license_site(server, template, root, "last-day", clock) as site:
            # Installed (with the previous version) 13 days and 15.5 hours before the capture:
            # the trial's last day.
            with pinned_release(PREVIOUS_RELEASE, PREVIOUS_VERSION):
                site.installed(site_time(2026, 9, 29, 15, 30))
                site.check(site_time(2026, 9, 29, 15, 33))
            clock.at(today_at(6, 55))
            admin = site.capture.client("admin", "tolga", "Tolga Yılmaz")
            clock.at(CLOCK)
            out["trial_last_day"] = expect_state(site.get(admin), "trial")
            if out["trial_last_day"]["trial"]["days_left"] != 1:
                raise SystemExit(f"not the trial's last day: {out['trial_last_day']['trial']}")
        with license_site(server, template, root, "unlicensed", clock) as site:
            capture_license_unlicensed(site, out)
        with license_site(server, template, root, "clock", clock) as site:
            # The installation ran with the clock three years ahead, the clock was set right
            # later: the trial is unavailable (the clock-forward trick of contract 3.4).
            site.installed(site_time(2029, 10, 13, 7, 0))
            site.check(site_time(2029, 10, 13, 7, 3))
            clock.at(today_at(6, 55))
            admin = site.capture.client("admin", "tolga", "Tolga Yılmaz")
            site.check(today_at(6, 58))
            clock.at(CLOCK)
            out["trial_clock_inconsistent"] = expect_state(
                site.get(admin), "trial_expired", "trial_clock_inconsistent"
            )
        with license_site(server, template, root, "demo", clock) as site:
            # A demo licence valid until 10.10.2026, installed on 02.10.
            site.installed(site_time(2026, 10, 1, 9, 0))
            site.check(site_time(2026, 10, 1, 9, 3))
            tolga = site.user("admin", "tolga", "Tolga Yılmaz")
            clock.at(site_time(2026, 10, 2, 11, 0))
            demo = sold(
                "TEST0008",
                issued=datetime(2026, 10, 2, 6, 0, tzinfo=UTC),
                log_sources=None,
                users=None,
                maintenance_until=date(2026, 12, 31),
                valid_until=date(2026, 10, 10),
            )
            site.install(tolga, demo)
            site.check(site_time(2026, 10, 2, 11, 5))
            site.check(site_time(2026, 10, 11, 0, 5))
            clock.at(CLOCK)
            out["expired"] = expect_state(
                site.get(site.capture.signed_in(tolga)), "expired", "license_expired"
            )
        for name, modules, suffix in (
            ("version_not_covered", SOLD_MODULES, "TEST0005"),
            ("version_not_covered_base_only", ("core_5651",), "TEST0006"),
        ):
            with license_site(server, template, root, name.replace("_", "-"), clock) as site:
                # Licensed under the previous version, maintenance until 30.09.2026; then the
                # upgrade to the captured version, released on 01.10.2026.
                with pinned_release(PREVIOUS_RELEASE, PREVIOUS_VERSION):
                    site.installed(site_time(2026, 9, 1, 9, 0))
                    site.check(site_time(2026, 9, 1, 9, 3))
                    tolga = site.user("admin", "tolga", "Tolga Yılmaz")
                    clock.at(site_time(2026, 9, 2, 14, 0))
                    licence = sold(
                        suffix,
                        issued=datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
                        modules=modules,
                        maintenance_until=date(2026, 9, 30),
                    )
                    site.install(tolga, licence)
                    site.check(site_time(2026, 9, 2, 14, 5))
                site.check(today_at(6, 45))
                clock.at(CLOCK)
                out[name] = expect_state(
                    site.get(site.capture.signed_in(tolga)),
                    "version_not_covered",
                    "license_version_not_covered",
                )
        with license_site(server, template, root, "moved", clock) as site:
            # Installed and licensed on the old server (the previous version); the database and
            # the data folder moved to this server, with the captured version, on the capture's
            # morning.
            with (
                pinned_release(PREVIOUS_RELEASE, PREVIOUS_VERSION),
                fixed_identity(OLD_SERVER.machine_id, OLD_SERVER.smbios_uuid),
            ):
                site.installed(site_time(2026, 9, 15, 8, 0))
                site.check(site_time(2026, 9, 15, 8, 3))
                tolga = site.user("admin", "tolga", "Tolga Yılmaz")
                clock.at(site_time(2026, 9, 16, 10, 0))
                issued = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)
                site.install(tolga, sold("TEST0007", issued=issued, server_ids=[old_server_id]))
                site.check(site_time(2026, 9, 16, 10, 5))
            site.check(today_at(6, 35))
            clock.at(CLOCK)
            out["wrong_server"] = expect_state(
                site.get(site.capture.signed_in(tolga)), "wrong_server", "license_other_server"
            )
    return out


def capture_license_main(
    site: LicenseSite, out: dict[str, Any], sold: Callable[..., bytes], old_server_id: str
) -> None:
    """The main licence site: installed on 05.10.2026, in its trial on the capture's day; then
    every refusal, the licence installed, its states and limits."""
    capture, clock = site.capture, site.clock
    site.installed(site_time(2026, 10, 5, 3, 12))
    site.check(site_time(2026, 10, 5, 3, 15))
    clock.at(today_at(6, 55))
    admin = capture.client("admin", "tolga", "Tolga Yılmaz")
    capture.client("auditor", "ayse.yilmaz", "Ayşe Yılmaz")
    capture.client("hr", "selin.celik", "Selin Çelik")
    viewer = capture.client("viewer", "rapor.izleyici", "Rapor İzleyici")
    site.sources()

    clock.at(CLOCK)
    out["trial"] = expect_state(site.get(admin), "trial")
    out["trial_viewer"] = expect_state(site.get(viewer), "trial")
    if out["trial"]["trial"]["days_left"] != 6 or out["trial_viewer"]["details"] is not None:
        raise SystemExit(f"unexpected trial: {out['trial']['trial']}")

    licence = sold("TEST0001")
    clock.at(today_at(7, 1))
    capture.recent(admin, minutes_ago=30)
    out["reauth_required"] = capture.error(
        admin.post(f"{API}/license", files={"file": ("ornek.lic", licence, "application/json")}),
        403,
        "reauth_required",
    )
    clock.at(today_at(7, 1, 30))
    capture.recent(admin)
    out["remove_not_installed"] = capture.error(
        admin.delete(f"{API}/license"), 404, "license_not_installed"
    )

    later_key = test_private_key(b"arcilogger later key")
    revoked_id = "ARCI-L-20261001-TEST0009"
    refusals: list[tuple[str, str, bytes, int, str]] = [
        (
            "refuse_malformed",
            "bozuk.lic",
            b'{"payload": {}, "signature": {}}\n',
            422,
            "license_malformed",
        ),
        ("refuse_too_large", "buyuk.lic", b" " * 70_000, 413, "license_too_large"),
        (
            "refuse_unknown_key",
            "baska-anahtar.lic",
            # Signed with a key of a later version (a key id this version does not know).
            sold("TEST000A", key_id="arci-2027a", private_key=later_key),
            422,
            "license_unknown_key",
        ),
        (
            "refuse_signature_invalid",
            "degistirilmis.lic",
            tamper(sold("TEST000B"), "limits.users", 250),
            422,
            "license_signature_invalid",
        ),
        ("refuse_revoked", "iptal.lic", sold("TEST0009"), 422, "license_revoked"),
        (
            "refuse_other_server",
            "baska-sunucu.lic",
            sold("TEST000C", server_ids=[old_server_id]),
            409,
            "license_other_server",
        ),
        (
            "refuse_expired",
            "suresi-dolmus.lic",
            sold("TEST000D", valid_until=date(2026, 10, 10)),
            409,
            "license_expired",
        ),
        (
            "refuse_version_not_covered",
            "bakim-disi.lic",
            sold("TEST000E", maintenance_until=date(2026, 9, 30)),
            409,
            "license_version_not_covered",
        ),
    ]
    moment = today_at(7, 2)
    for key, name, data, status, code in refusals:
        clock.at(moment)
        moment += timedelta(seconds=15)
        with mock.patch.object(license_revoked, "REVOKED_LICENSE_IDS", frozenset({revoked_id})):
            out[key] = capture.error(site.upload(admin, data, name), status, code)

    clock.at(today_at(7, 4))
    out["upload_ok"] = expect_state(capture.json(site.upload(admin, licence), 200), "licensed")
    clock.at(today_at(7, 4, 30))
    # The same licence again, reformatted (another file, the same signed payload).
    reformatted = json.dumps(json.loads(licence), ensure_ascii=True, sort_keys=True).encode()
    out["upload_unchanged"] = expect_state(
        capture.json(site.upload(admin, reformatted, "ornek-kopya.lic"), 200), "licensed"
    )
    if out["upload_unchanged"]["details"]["history"][0]["details"].get("unchanged") is not True:
        raise SystemExit("the re-uploaded licence was not recorded as unchanged")
    site.check(today_at(7, 5))
    clock.at(today_at(7, 5, 30))
    out["licensed"] = expect_state(site.get(admin), "licensed")
    out["licensed_viewer"] = expect_state(site.get(viewer), "licensed")

    # The licence has no CSV import: its routes refuse before reading the request.
    clock.at(today_at(7, 6))
    out["module_locked_import"] = capture.error(
        _upload(admin, "persons", "name\r\nCan Öztürk\r\n"), 403, "module_not_licensed"
    )
    # The licence subsystem fails (a broken installation): 503; the 5651 routes work on.
    clock.at(today_at(7, 6, 30))
    failure = RuntimeError("capture: the licence subsystem is broken")
    with (
        mock.patch.object(licensing, "load_status", side_effect=failure),
        quiet("arcilogger.license"),
    ):
        capture.app.state.arci.license_cache.invalidate()
        out["unavailable"] = capture.error(admin.get(f"{API}/license"), 503, "license_unavailable")
    # A later version no longer trusts the licence's key (removed after a compromise) ...
    clock.at(today_at(7, 7))
    with trusted_test_keys(("arci-2027a", later_key)):
        out["invalid"] = expect_state(site.get(admin), "invalid", "license_unknown_key")
    # ... or revoked the licence (its id in revoked.py).
    clock.at(today_at(7, 7, 30))
    main_id = frozenset({"ARCI-L-20261001-TEST0001"})
    with mock.patch.object(license_revoked, "REVOKED_LICENSE_IDS", main_id):
        out["revoked"] = expect_state(site.get(admin), "invalid", "license_revoked")

    # A second unconfigured sender, then a licence for 3 sources and 3 users (4 of each in use).
    clock.at(today_at(7, 9))
    site.sources("172.20.10.78")
    clock.at(today_at(7, 10))
    capture.json(site.upload(admin, sold("TEST0002", log_sources=3, users=3)), 200)
    site.check(today_at(7, 10, 30))
    clock.at(today_at(7, 11))
    out["licensed_limits_exceeded"] = expect_state(site.get(admin), "licensed")
    limits = out["licensed_limits_exceeded"]["limits"]
    if not (limits["users"]["exceeded"] and limits["log_sources"]["exceeded"]):
        raise SystemExit(f"the limits are not exceeded: {limits}")

    clock.at(today_at(7, 15))
    capture.json(site.upload(admin, sold("TEST0003", maintenance_until=date(2026, 10, 25))), 200)
    site.check(today_at(7, 15, 30))
    clock.at(today_at(7, 16))
    out["maintenance_ends_soon"] = expect_state(site.get(admin), "licensed")
    clock.at(today_at(7, 20))
    capture.json(site.upload(admin, sold("TEST0004", maintenance_until=date(2026, 10, 5))), 200)
    clock.at(today_at(7, 21))
    out["maintenance_expired"] = expect_state(site.get(admin), "licensed")
    if not out["maintenance_expired"]["maintenance"]["expired"]:
        raise SystemExit("maintenance did not end")


def capture_license_unlicensed(site: LicenseSite, out: dict[str, Any]) -> None:
    """A site whose trial ended on 04.10.2026: 5651 Temel only, at most 3 active users."""
    capture, clock = site.capture, site.clock
    with pinned_release(PREVIOUS_RELEASE, PREVIOUS_VERSION):
        site.installed(site_time(2026, 9, 20, 9, 0))
        site.check(site_time(2026, 9, 20, 9, 3))
    site.check(site_time(2026, 10, 4, 9, 5))
    clock.at(today_at(6, 55))
    admin = capture.client("admin", "tolga", "Tolga Yılmaz")
    capture.client("hr", "selin.celik", "Selin Çelik")
    capture.client("viewer", "rapor.izleyici", "Rapor İzleyici")
    clock.at(CLOCK)
    out["trial_expired"] = expect_state(site.get(admin), "trial_expired")
    clock.at(today_at(7, 1))
    out["module_locked_usage"] = capture.error(
        admin.get(f"{API}/usage/company"), 403, "module_not_licensed"
    )
    out["module_locked_reports"] = capture.error(
        admin.get(f"{API}/reports/schedules"), 403, "module_not_licensed"
    )
    clock.at(today_at(7, 2))
    capture.recent(admin)
    out["user_limit"] = capture.error(
        admin.post(
            f"{API}/users",
            json={"username": "yeni.kullanici", "display_name": "Yeni Kullanıcı", "role": "viewer"},
        ),
        409,
        "license_limit",
    )


# -- orchestration --------------------------------------------------------------------------------


def sanitize(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: sanitize(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    return value


def placeholder_token_hashes(value: Any, seen: dict[str, str]) -> Any:
    """``token_sha256`` values (SHA-256 of a mock time-stamp token) → ``000…001``, ``000…002``.

    Not a secret, but a 64-digit random-looking value under a key with "token" in its name is
    what the secret scanner's ``generic-api-key`` rule reports (CI runs gitleaks over the
    repository). The same token keeps the same placeholder everywhere (a day's token and the
    next day's link to it); the manifest file text keeps the real bytes.
    """
    if isinstance(value, dict):
        return {
            key: (
                seen.setdefault(item, f"{len(seen) + 1:064x}")
                if key == "token_sha256" and isinstance(item, str)
                else placeholder_token_hashes(item, seen)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [placeholder_token_hashes(item, seen) for item in value]
    return value


# -- the browser administration (ADR-0014) --------------------------------------------------------


def capture_settings(capture: Capture, admin: Any, auditor: Any) -> None:
    """``settings.json``: "Uygulama ayarları" (contract 2.13), changed and put back."""
    out = capture.samples["settings"]
    url = f"{API}/settings"
    out["list"] = capture.json(admin.get(url), 200)
    change = {"key": "lookup.max_candidates", "old": 100, "value": 150}
    out["patch"] = capture.json(admin.patch(url, json={"changes": [change]}), 200)
    out["list_changed"] = capture.json(admin.get(url), 200)
    out["reset"] = capture.json(admin.post(f"{url}/lookup.max_candidates/reset"), 200)
    errors = out.setdefault("errors", {})
    bad = {"changes": [{"key": "lookup.max_candidates", "old": 100, "value": 0}]}
    errors["setting_invalid"] = capture.error(admin.patch(url, json=bad), 422, "setting_invalid")
    stale = {"changes": [{"key": "lookup.max_candidates", "old": 99, "value": 150}]}
    errors["setting_conflict"] = capture.error(
        admin.patch(url, json=stale), 409, "setting_conflict"
    )
    unknown = {"changes": [{"key": "yok.boyle", "old": 1, "value": 2}]}
    errors["setting_unknown"] = capture.error(
        admin.patch(url, json=unknown), 422, "setting_unknown"
    )
    locked = {"changes": [{"key": "devices.oui_file", "old": "", "value": "C:/oui.csv"}]}
    errors["setting_locked"] = capture.error(admin.patch(url, json=locked), 422, "setting_locked")
    idle = {"changes": [{"key": "auth.idle_minutes", "old": 15, "value": 20}]}
    capture.recent(admin, minutes_ago=10)
    errors["reauth_required"] = capture.error(admin.patch(url, json=idle), 403, "reauth_required")
    errors["forbidden"] = capture.error(auditor.get(url), 403, "forbidden")


def capture_admin(capture: Capture, admin: Any, auditor: Any) -> None:
    """``admin.json``: the machine configuration, the administration service's status and a
    change of the site name from the 202 to ``active`` (last: it changes the site's name), with
    the refusals the settings pages show."""
    out = capture.samples["admin"]
    agent = capture.agent()
    config = capture.json(admin.get(f"{API}/admin/config"), 200)
    out["config"] = config
    out["status"] = capture.json(admin.get(f"{API}/admin/status"), 200)
    errors = out.setdefault("errors", {})
    general = f"{API}/admin/config/general"
    tag = config["etags"]["general"]
    body = {"etag": tag, "site_name": "Örnek Lojistik A.Ş.", "probe": True}
    out["check_general"] = capture.json(admin.post(f"{general}/check", json=body), 200)
    clock = {"etag": tag, "clock": {"ntp_server": "172.20.10.254"}, "probe": False}
    out["check_clock"] = capture.json(admin.post(f"{general}/check", json=clock), 200)
    errors["reauth_required"] = capture.error(
        admin.put(general, json=clock), 403, "reauth_required"
    )
    errors["config_conflict"] = capture.error(
        admin.put(general, json={**body, "etag": "general:0"}), 409, "config_conflict"
    )
    errors["site_name_required"] = capture.error(
        admin.put(general, json={**body, "site_name": " "}), 422, "site_name_required"
    )
    errors["host_invalid"] = capture.error(
        admin.post(f"{general}/check", json={"etag": tag, "clock": {"ntp_server": "a b"}}),
        422,
        "host_invalid",
    )
    errors["config_unchanged"] = capture.error(
        admin.put(general, json={**body, "site_name": "Örnek A.Ş."}), 422, "config_unchanged"
    )
    errors["forbidden"] = capture.error(auditor.get(f"{API}/admin/config"), 403, "forbidden")
    errors["admin_request_not_found"] = capture.error(
        admin.get(f"{API}/admin/requests/{'0' * 32}"), 404, "admin_request_not_found"
    )
    accepted = capture.json(admin.put(general, json=body), 202)
    out["accepted"] = accepted
    errors["admin_section_busy"] = capture.error(
        admin.put(general, json={**body, "site_name": "Örnek Holding A.Ş."}),
        409,
        "admin_section_busy",
    )
    out["request_queued"] = capture.json(admin.get(accepted["status_url"]), 200)
    agent.process()
    out["request_active"] = capture.json(admin.get(accepted["status_url"]), 200)
    out["requests"] = capture.json(admin.get(f"{API}/admin/requests"), 200)
    out["status_after"] = capture.json(admin.get(f"{API}/admin/status"), 200)
    # Without the administration service, and with browser administration switched off.
    status = capture.installed.layout.agent_status
    kept = status.read_bytes()
    status.unlink()
    out["status_agent_missing"] = capture.json(admin.get(f"{API}/admin/status"), 200)
    tag = capture.json(admin.get(f"{API}/admin/config"), 200)["etags"]["general"]
    errors["admin_agent_unavailable"] = capture.error(
        admin.put(general, json={**body, "etag": tag, "site_name": "Örnek Holding A.Ş."}),
        503,
        "admin_agent_unavailable",
    )
    status.write_bytes(kept)
    capture.installed.write_policy(enabled=False)
    out["status_disabled"] = capture.json(admin.get(f"{API}/admin/status"), 200)
    errors["admin_disabled"] = capture.error(
        admin.put(general, json={**body, "etag": tag, "site_name": "Örnek Holding A.Ş."}),
        503,
        "admin_disabled",
    )
    capture.installed.write_policy()


def example_root() -> bytes:
    """A self-signed CA certificate standing in for a Kamu SM root (never a real one)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Örnek Test Kök Sertifikası")])
    start = utc_now() - timedelta(days=1)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
        .public_bytes(Encoding.PEM)
    )


def example_client() -> bytes:
    """A .jar standing in for a Zamane client that is not a known release."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        archive.writestr("ornek/Client.class", b"ornek istemci")
    return buffer.getvalue()


def upload_file(client: Any, kind: str, name: str, data: bytes) -> Any:
    return client.post(
        f"{API}/admin/uploads",
        params={"kind": kind},
        files={"file": (name, data, "application/octet-stream")},
    )


def capture_kamusm(capture: Capture, admin: Any) -> None:
    """``admin.json`` ``kamusm``: the Kamu SM wizard's answers (credentials, a root certificate,
    a Zamane client held for its approval, the stamp test) and its refusals."""
    out = capture.samples["admin"].setdefault("kamusm", {})
    errors = capture.samples["admin"].setdefault("errors", {})
    agent = capture.agent()
    tsa = f"{API}/admin/config/tsa"

    def tag() -> str:
        etags = capture.json(admin.get(f"{API}/admin/config"), 200)["etags"]
        found: str = etags["tsa"]
        return found

    errors["tsa_mock_refused"] = capture.error(
        admin.put(tsa, json={"etag": tag(), "server": "mock"}), 422, "tsa_mock_refused"
    )
    errors["tsa_would_break"] = capture.error(
        admin.post(f"{tsa}/check", json={"etag": tag(), "server": "test"}), 409, "tsa_would_break"
    )
    credentials = {"etag": tag(), "customer": "123456", "password": "Örnek-Parola-2026"}
    out["check_credentials"] = capture.json(admin.post(f"{tsa}/check", json=credentials), 200)
    capture.recent(admin)
    accepted = capture.json(admin.put(tsa, json=credentials), 202)
    out["credentials_accepted"] = accepted
    agent.process()
    out["credentials_active"] = capture.json(admin.get(accepted["status_url"]), 200)

    errors["upload_kind_invalid"] = capture.error(
        upload_file(admin, "program", "kurulum.exe", b"MZ"), 422, "upload_kind_invalid"
    )
    errors["root_certificate_invalid"] = capture.error(
        upload_file(admin, "kamusm_root", "kok.pem", b"bir sertifika degil"),
        422,
        "root_certificate_invalid",
    )
    root = capture.json(upload_file(admin, "kamusm_root", "kok.pem", example_root()), 201)
    out["upload_root"] = root
    body = {"etag": tag(), "upload_id": root["upload_id"], "legal": True, "confirmed": False}
    errors["legal_root_confirm_required"] = capture.error(
        admin.post(f"{tsa}/roots", json=body), 422, "legal_root_confirm_required"
    )
    added = capture.json(admin.post(f"{tsa}/roots", json={**body, "confirmed": True}), 202)
    out["root_accepted"] = added
    agent.process()
    out["root_active"] = capture.json(admin.get(added["status_url"]), 200)

    client = capture.json(
        upload_file(admin, "zamane_client", "tss-client-console.jar", example_client()), 201
    )
    out["upload_zamane"] = client
    body = {"etag": tag(), "upload_id": client["upload_id"]}
    errors["zamane_sha256_required"] = capture.error(
        admin.post(f"{tsa}/zamane", json=body), 422, "zamane_sha256_required"
    )
    body["expected_sha256"] = client["zamane"]["jar_sha256"]
    staged = capture.json(admin.post(f"{tsa}/zamane", json=body), 202)
    out["zamane_accepted"] = staged
    agent.process()
    out["zamane_pending"] = capture.json(admin.get(staged["status_url"]), 200)

    test = f"{API}/admin/actions/tsa-test"
    errors["tsa_test_confirm_required"] = capture.error(
        admin.post(test, json={}), 422, "tsa_test_confirm_required"
    )
    capture.installed.write_policy(tsa_test_per_day=0)
    errors["tsa_test_limit"] = capture.error(
        admin.post(test, json={"confirm_credit": True}), 429, "tsa_test_limit"
    )
    capture.installed.write_policy()
    tested = capture.json(admin.post(test, json={"confirm_credit": True}), 202)
    out["tsa_test_accepted"] = tested
    errors["admin_rate_limited"] = capture.error(
        admin.post(test, json={"confirm_credit": True}), 429, "admin_rate_limited"
    )
    agent.process()
    out["tsa_test_done"] = capture.json(admin.get(tested["status_url"]), 200)
    out["config"] = capture.json(admin.get(f"{API}/admin/config"), 200)


def collector_loads(capture: Capture) -> None:
    """The collector takes the configuration file now in force (as it does within 5 seconds):
    its status file names its SHA-256 (contract 2.4.12)."""
    path = capture.layout.collector_status
    status = json.loads(path.read_text(encoding="utf-8"))
    status["config"] = {
        "sha256": hashlib.sha256(capture.installed.config_path.read_bytes()).hexdigest(),
        "loaded_at": utc_now().isoformat(timespec="seconds"),
        "error": None,
        "restart_keys": [],
    }
    path.write_text(json.dumps(status), encoding="utf-8")


def capture_admin_sources(capture: Capture, admin: Any) -> None:
    """``admin.json`` ``sources``: a log source checked, added and removed, and the addresses
    the browser may not add."""
    out = capture.samples["admin"].setdefault("sources", {})
    errors = capture.samples["admin"].setdefault("errors", {})
    agent = capture.agent()
    url = f"{API}/admin/config/sources"

    def tag() -> str:
        etags = capture.json(admin.get(f"{API}/admin/config"), 200)["etags"]
        found: str = etags["sources"]
        return found

    source = {
        "name": "erisim-noktalari",
        "type": "generic",
        "address": "172.20.20.0/24",
        "heartbeat_minutes": 60,
        "expected": False,
        "transport": "udp",
        "enabled": True,
    }
    body = {"etag": tag(), "source": source}
    out["check_add"] = capture.json(
        admin.post(f"{url}/check", json={**body, "operation": "add"}), 200
    )
    errors["source_network_not_allowed"] = capture.error(
        admin.post(url, json={**body, "source": {**source, "address": "203.0.113.7"}}),
        422,
        "source_network_not_allowed",
    )
    errors["source_address_taken"] = capture.error(
        admin.post(url, json={**body, "source": {**source, "address": FIREWALL}}),
        409,
        "source_address_taken",
    )
    added = capture.json(admin.post(url, json=body), 202)
    out["add_accepted"] = added
    agent.process()
    out["add_applied"] = capture.json(admin.get(added["status_url"]), 200)
    collector_loads(capture)
    agent.process()  # ApplySystem opens the syslog rule once the collector runs the new file
    out["add_active"] = capture.json(admin.get(added["status_url"]), 200)
    changed = {"etag": tag(), "source": {**source, "heartbeat_minutes": 30}}
    out["check_update"] = capture.json(
        admin.post(f"{url}/check", json={**changed, "operation": "update"}), 200
    )
    name = source["name"]
    errors["source_confirm_mismatch"] = capture.error(
        admin.delete(f"{url}/{name}", params={"etag": tag(), "confirm": "erisim"}),
        422,
        "source_confirm_mismatch",
    )
    capture.recent(admin)
    removed = capture.json(
        admin.delete(f"{url}/{name}", params={"etag": tag(), "confirm": name}), 202
    )
    out["remove_accepted"] = removed
    agent.process()
    collector_loads(capture)
    agent.process()
    out["remove_active"] = capture.json(admin.get(removed["status_url"]), 200)


def capture_admin_sections(capture: Capture, admin: Any) -> None:
    """``admin.json`` ``sections``: the e-mail server (probed with a test e-mail), its CA file,
    the replica, retention with the days it deletes, the backups, and the worker's test e-mail
    and lease rebuild."""
    out = capture.samples["admin"].setdefault("sections", {})
    errors = capture.samples["admin"].setdefault("errors", {})
    agent = capture.agent()
    config = f"{API}/admin/config"
    actions = f"{API}/admin/actions"
    recipient = "bilgi-islem@ornek.example"
    capture.setting("alerts.email_recipients", [recipient])

    def tag(section: str) -> str:
        found: str = capture.json(admin.get(config), 200)["etags"][section]
        return found

    def follow(response: Any, name: str) -> None:
        accepted = capture.json(response, 202)
        out[f"{name}_accepted"] = accepted
        agent.process()
        out[f"{name}_done"] = capture.json(admin.get(accepted["status_url"]), 200)

    errors["smtp_not_configured"] = capture.error(
        admin.post(f"{actions}/test-email", json={"to": recipient}), 409, "smtp_not_configured"
    )
    errors["test_email_recipient_not_allowed"] = capture.error(
        admin.post(f"{actions}/test-email", json={"to": "yabanci@baska.example"}),
        422,
        "test_email_recipient_not_allowed",
    )
    capture.recent(admin)
    with SmtpTestServer() as mail:
        smtp = {
            "etag": tag("smtp"),
            "enabled": True,
            "host": "127.0.0.1",
            "port": mail.port,
            "security": "none",
            "username": None,
            "from_address": "arcilogger@ornek.example",
            "timeout_seconds": 10,
            "test_to": recipient,
        }
        errors["smtp_password_required"] = capture.error(
            admin.put(f"{config}/smtp", json={**smtp, "username": "arcilogger"}),
            422,
            "smtp_password_required",
        )
        out["check_smtp"] = capture.json(admin.post(f"{config}/smtp/check", json=smtp), 200)
        follow(admin.put(f"{config}/smtp", json=smtp), "smtp")
        sent = capture.json(admin.post(f"{actions}/test-email", json={"to": recipient}), 202)
        out["test_email_accepted"] = sent
        process_admin_requests(
            capture.factory, load_config(capture.installed.config_path), now=utc_now()
        )
        out["test_email_done"] = capture.json(admin.get(sent["status_url"]), 200)

    ca = capture.json(upload_file(admin, "smtp_ca", "posta-ca.pem", example_root()), 201)
    out["upload_smtp_ca"] = ca
    follow(
        admin.post(
            f"{config}/smtp/ca",
            json={"etag": tag("smtp"), "upload_id": ca["upload_id"], "test_to": recipient},
        ),
        "smtp_ca",
    )

    replica = {
        "etag": tag("replica"),
        "enabled": True,
        "target": "\\\\NAS-01\\arcilogger-worm",
        "user": "NAS-01\\arcireplica",
        "password": "Örnek-Paylaşım-2026",
    }
    errors["replica_target_invalid"] = capture.error(
        admin.put(f"{config}/replica", json={**replica, "target": "NAS-01/paylasim"}),
        422,
        "replica_target_invalid",
    )
    out["check_replica"] = capture.json(admin.post(f"{config}/replica/check", json=replica), 200)
    follow(admin.put(f"{config}/replica", json=replica), "replica")

    retention = {"etag": tag("retention"), "enabled": True, "days": 760}
    out["check_retention"] = capture.json(
        admin.post(f"{config}/retention/check", json=retention), 200
    )
    errors["retention_confirm_mismatch"] = capture.error(
        admin.put(f"{config}/retention", json=retention), 422, "retention_confirm_mismatch"
    )
    errors["retention_below_minimum"] = capture.error(
        admin.put(f"{config}/retention", json={**retention, "days": 365}),
        422,
        "retention_below_minimum",
    )
    confirm = out["check_retention"]["retention_preview"]["days_to_delete"]
    follow(
        admin.put(f"{config}/retention", json={**retention, "confirm_delete_days": confirm}),
        "retention",
    )

    backup = {"etag": tag("backup"), "enabled": True, "keep_daily": 21, "min_free_gb": 20}
    out["check_backup"] = capture.json(admin.post(f"{config}/backup/check", json=backup), 200)
    follow(admin.put(f"{config}/backup", json=backup), "backup")

    follow(admin.post(f"{actions}/replica-test", json={}), "replica_test")
    errors["lease_rebuild_range"] = capture.error(
        admin.post(f"{actions}/lease-rebuild", json={"from": "2026-01-01", "to": "2026-03-01"}),
        422,
        "lease_rebuild_range",
    )
    span = {"from": SITE_DAY.isoformat(), "to": (SITE_DAY + timedelta(days=1)).isoformat()}
    rebuild = capture.json(admin.post(f"{actions}/lease-rebuild", json=span), 202)
    out["lease_rebuild_accepted"] = rebuild
    process_admin_requests(
        capture.factory, load_config(capture.installed.config_path), now=utc_now()
    )
    out["lease_rebuild_done"] = capture.json(admin.get(rebuild["status_url"]), 200)
    out["config"] = capture.json(admin.get(config), 200)


def capture_admin_history(capture: Capture, admin: Any) -> None:
    """``admin.json`` ``history``: the versions, a revert, a queued request cancelled by the API,
    a held change cancelled by the agent, and the refusals of both."""
    out = capture.samples["admin"].setdefault("history", {})
    errors = capture.samples["admin"].setdefault("errors", {})
    agent = capture.agent()
    config = f"{API}/admin/config"
    requests = f"{API}/admin/requests"

    def tag(section: str) -> str:
        found: str = capture.json(admin.get(config), 200)["etags"][section]
        return found

    out["versions"] = capture.json(admin.get(f"{config}/versions"), 200)
    applied = out["versions"][0]["request_id"]
    errors["request_not_cancellable"] = capture.error(
        admin.delete(f"{requests}/{applied}"), 409, "request_not_cancellable"
    )
    capture.recent(admin)
    revert = capture.json(admin.post(f"{requests}/{applied}/revert"), 202)
    out["revert_accepted"] = revert
    agent.process()
    out["revert_done"] = capture.json(admin.get(revert["status_url"]), 200)

    backup = {"etag": tag("backup"), "keep_weekly": 10}
    queued = capture.json(admin.put(f"{config}/backup", json=backup), 202)
    out["cancel_queued"] = capture.json(admin.delete(f"{requests}/{queued['request_id']}"), 202)
    out["cancelled_queued"] = capture.json(admin.get(queued["status_url"]), 200)
    errors["request_not_revertible"] = capture.error(
        admin.post(f"{requests}/{queued['request_id']}/revert"), 409, "request_not_revertible"
    )

    capture.installed.write_policy(hold_hours=12)
    held = capture.json(
        admin.put(f"{config}/replica", json={"etag": tag("replica"), "enabled": False}), 202
    )
    out["held_accepted"] = held
    agent.process()
    out["held_pending"] = capture.json(admin.get(held["status_url"]), 200)
    capture.recent(admin)
    cancel = capture.json(admin.delete(f"{requests}/{held['request_id']}"), 202)
    out["cancel_held_accepted"] = cancel
    agent.process()
    out["cancel_held_done"] = capture.json(admin.get(cancel["status_url"]), 200)
    out["held_cancelled"] = capture.json(admin.get(held["status_url"]), 200)
    capture.installed.write_policy()
    out["requests"] = capture.json(admin.get(requests, params={"limit": 10}), 200)


def capture_site(capture: Capture, server: TestServer, template: str, root: Path) -> None:
    """Steps 1-7 of the module text: the lookup site and its pages (with the sample modules'
    pages), then the usage site."""
    build_site(capture)
    vault = seal_october(capture)
    odd = write_odd_line(capture)
    admin = capture.client("admin", "tolga", "Tolga Yılmaz")
    auditor = capture.client("auditor", "ayse.yilmaz", "Ayşe Yılmaz")
    newcomer = capture.client("admin", "yeni.yonetici", "Yeni Yönetici", notice=False)
    lookup_ids = capture_lookups(capture, admin, auditor, newcomer)
    capture_raw(capture, admin, odd)
    capture_evidence(capture, admin, auditor, vault, lookup_ids)
    capture_seals(capture, admin, auditor)
    capture_credits(capture, admin)
    capture_devices(capture, admin, auditor, newcomer)
    build_operations(capture)
    capture_alerts(capture, admin, auditor)
    capture_dashboard(capture, admin, auditor)
    capture_reports(capture, admin, auditor, newcomer)
    capture_settings(capture, admin, auditor)
    capture_admin(capture, admin, auditor)
    capture_kamusm(capture, admin)
    capture_admin_sources(capture, admin)
    capture_admin_sections(capture, admin)
    capture_admin_history(capture, admin)
    for module in SAMPLE_MODULES:
        for name, data in module.capture(capture, admin, auditor, newcomer).items():
            if name in capture.samples:
                raise SystemExit(f"{module.__name__}: {name}.json is captured here already")
            if data:
                capture.samples[name] = data
    vault.unlock_all()
    capture.samples["usage"] = capture_usage(server, template, root)


def capture_all(server: TestServer) -> dict[str, dict[str, Any]]:
    template = server.create_template(timescale="off")
    try:
        with (
            server.database(template) as name,
            _workdir() as root,
            frozen_clock(CLOCK),
            # The API takes the agent's changes at once (not after its 2 seconds).
            mock.patch.object(deps, "RELOAD_SECONDS", 0.0),
        ):
            engine = engine_from_url(
                server.url_for(name), application="capture", poolclass=NullPool
            )
            try:
                capture = Capture(root, engine)
                # Every licence module on, as the tests' default (steps 1-7 of the module text).
                with override_status(all_modules_status(now=CLOCK)):
                    capture_site(capture, server, template, root)
                capture.samples["license"] = capture_license(server, template, root)
                meta = {
                    "info": "captured by web/scripts/capture_api_samples.py; do not edit",
                    "captured_at": utc_now().isoformat(),
                    "clock": CLOCK.isoformat(),
                    "site_today": site_day(utc_now()).isoformat(),
                    "site_day": SITE_DAY.isoformat(),
                    "tsa": "mock (never legally valid)",
                }
                replacements = {
                    str(root / "replica"): REPLICA_TARGET,
                    root.as_posix() + "/": "",
                    str(root): "",
                }
                tokens: dict[str, str] = {}
                return {
                    name: {
                        "meta": meta,
                        **placeholder_token_hashes(sanitize(data, replacements), tokens),
                    }
                    for name, data in capture.samples.items()
                }
            finally:
                engine.dispose()
    finally:
        server.drop_database(template)


@contextlib.contextmanager
def _workdir() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix="arci-capture-"))
    try:
        yield root
    finally:
        # Sealed files are read-only (immutable on Linux as root): unlocked before removal.
        shutil.rmtree(root, ignore_errors=True)


# -- shape comparison -----------------------------------------------------------------------------

#: Objects whose keys are data (months, days, ids, raw references, state counts): only their
#: values' shapes are compared, merged.
MAP_KEYS = frozenset({"calendars", "days", "details", "lines", "counts", "errors", "params"})

#: Enum-like string fields: the pages map each value to a label, colour or branch (an unknown seal
#: state shows as "Veri yok"), so their values are compared too, not only their type. Messages,
#: times and hashes are not.
ENUM_KEYS = frozenset(
    {
        "code", "confidence", "department_source", "destination_match", "kind", "mode",
        "outcome", "state", "status", "timestamp_status", "tone",
    }
)  # fmt: skip

#: Sample files compared with other keys than the defaults: ``(MAP_KEYS, ENUM_KEYS)``.
FILE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # The licence document's ``details`` (the administrator's form) has fixed keys, so do the
    # licence events' and the refusals' details; the licence page maps the problem codes, the
    # time and event sources and the states of a state change to labels.
    "license": (MAP_KEYS - {"details"}, ENUM_KEYS | {"problem", "source", "from", "to"}),
    # A request's ``changes`` are keyed by the paths it changes, a root's by its fingerprint.
    "admin": (MAP_KEYS | {"changes"}, ENUM_KEYS),
    # The sample modules' files: their own keys added to the defaults (``SHAPE_KEYS``).
    **{
        name: (MAP_KEYS | maps, ENUM_KEYS | enums)
        for module in SAMPLE_MODULES
        for name, (maps, enums) in module.SHAPE_KEYS.items()
    },
}


def shape(
    value: Any,
    key: str | None = None,
    *,
    maps: frozenset[str] = MAP_KEYS,
    enums: frozenset[str] = ENUM_KEYS,
) -> Any:
    """Keys and value types (values of ``enums`` as ``=value``); lists and data maps merged."""
    if isinstance(value, dict):
        if key in maps:
            items = [shape(item, maps=maps, enums=enums) for item in value.values()]
            return {"<map>": merge(items)}
        return {name: shape(item, name, maps=maps, enums=enums) for name, item in value.items()}
    if isinstance(value, list):
        return ["<list>", merge([shape(item, maps=maps, enums=enums) for item in value])]
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if key in enums:
        return f"={value}"
    return "str"


def file_shape(name: str, value: Any) -> Any:
    """The shape of the sample file ``name``.json (its own keys of :data:`FILE_KEYS`)."""
    maps, enums = FILE_KEYS.get(name, (MAP_KEYS, ENUM_KEYS))
    return shape(value, maps=maps, enums=enums)


def merge(shapes: list[Any]) -> Any:
    """One shape for several (``None`` = unknown/null is compatible with anything)."""
    result: Any = None
    for item in shapes:
        result = _merge2(result, item)
    return result


def _merge2(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    if isinstance(left, dict) and isinstance(right, dict):
        return {key: _merge2(left.get(key), right.get(key)) for key in left.keys() | right.keys()}
    if isinstance(left, list) and isinstance(right, list):
        return ["<list>", _merge2(left[1], right[1])]
    if left == right:
        return left
    if isinstance(left, str) and isinstance(right, str):
        return "|".join(sorted(set(left.split("|")) | set(right.split("|"))))
    return "<mixed>"


def differences(old: Any, new: Any, path: str = "$") -> list[str]:
    if old is None or new is None:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        found = [f"{path}.{key}: missing in the API's answer" for key in old.keys() - new.keys()]
        found += [f"{path}.{key}: new in the API's answer" for key in new.keys() - old.keys()]
        for key in old.keys() & new.keys():
            found += differences(old[key], new[key], f"{path}.{key}")
        return found
    if isinstance(old, list) and isinstance(new, list):
        return differences(old[1], new[1], f"{path}[]")
    if isinstance(old, str) and isinstance(new, str):
        old_names, new_names = set(old.split("|")), set(new.split("|"))
        old_values = {name for name in old_names if name.startswith("=")}
        new_values = {name for name in new_names if name.startswith("=")}
        if old_values or new_values:
            # Enum-like values: a renamed, new or vanished value changes what the pages show.
            return [] if old_values == new_values else [f"{path}: {old} → {new}"]
        # A field may be null in one capture: types are compared as sets of names.
        return [] if old_names & new_names else [f"{path}: {old} → {new}"]
    return [f"{path}: {old!r} → {new!r}"]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare shapes, write nothing")
    args = parser.parse_args(argv)
    dsn = admin_dsn_from_env()
    if dsn is None:
        print("ARCILOGGER_TEST_PG is not set (admin DSN of a throwaway PostgreSQL server)")
        return 2
    server = TestServer(dsn)
    try:
        server.check()
        samples = capture_all(server)
    finally:
        server.dispose()
    if args.check:
        problems: list[str] = []
        for name, data in samples.items():
            path = OUT_DIR / f"{name}.json"
            if not path.exists():
                problems.append(f"{path.name}: missing")
                continue
            committed = json.loads(path.read_text(encoding="utf-8"))
            problems += [
                f"{path.name}: {item}"
                for item in differences(file_shape(name, committed), file_shape(name, data))
            ]
        if problems:
            print("the API's answers changed shape; run web/scripts/capture_api_samples.py:")
            print("\n".join(f"  {item}" for item in sorted(problems)))
            return 1
        print("samples match the API's answers")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in samples.items():
        (OUT_DIR / f"{name}.json").write_bytes(_dump(data).encode("utf-8"))
    print(f"written: {', '.join(f'{name}.json' for name in samples)} in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
