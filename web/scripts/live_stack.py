"""Start a real ARCILOGGER API for the full-stack smoke test of the web app (local, not in CI).

From the repository root, with the throwaway PostgreSQL role of the test suite::

    export ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres
    uv run python web/scripts/live_stack.py --dir /tmp/arci-live --drop --seed
    # second terminal:
    cd web && ARCI_LIVE_DIR=/tmp/arci-live npm run e2e:live

What it does, with the same commands the Windows installer runs:

1. creates a fresh database ``arci_web_smoke_<random>`` owned by the test role;
2. writes ``<dir>/arcilogger.toml`` (data in ``<dir>/data``, ``[api] secure_cookies = false``
   because the dev server is plain HTTP on 127.0.0.1, ``[database] timescale = "off"``);
3. ``arcilogger-core secret init`` and ``secret set database.password`` (from stdin);
4. ``arcilogger-db upgrade`` (a new installation: no VLAN plan, no departments, no sources);
5. ``arcilogger-api setup-token`` — the token is written to ``<dir>/setup-token.txt`` for the
   Playwright test (``e2e-live/smoke.spec.ts``), which creates the first administrator with it;
6. with ``--seed``: a day of log data for the 5651 lookup, through the real pipeline (below);
7. ``arcilogger-api serve`` on 127.0.0.1:8000 (the Vite dev server proxies ``/api`` there) until
   Ctrl+C; ``--drop`` drops the database afterwards.

``--seed`` (for the 5651 / evidence package / sealing part of the smoke test):

* the config gets the two sources of the tests' fictional example site (FortiGate
  ``172.31.255.1``, MikroTik ``172.20.10.1``), the **mock** time-stamp authority (never legally
  valid) and ``[seal] quiet_seconds = 0``;
* the database gets that site's VLAN plan and departments
  (``arcilogger_db.testing.seed_example_network``), as an administrator would enter them — the
  product ships none;
* yesterday's (site time) raw spool files are written exactly as the collector writes them
  (``rawlog.format_line``; the FortiGate and MikroTik message formats of
  ``arcilogger_worker.testing``): DHCP leases, FortiGate sessions with source NAT to
  ``100.64.12.9`` — among them the session of the 5651 request, the example attribution chain
  (``100.64.12.9:51001`` at 11:23:41 → 172.20.42.57, lease of ``dhcp42`` at 10:42:18) — and
  ARP lines of an IP conflict around 13:30;
* ``arcilogger-ingest run --once`` loads them, ``arcilogger-worker rebuild --day`` builds the
  lease timeline and ``arcilogger-core seal --day`` seals the day with the mock TSA;
* site settings as an administrator would make them: ``nat.wan_transip = 100.64.12.9`` (the
  firewall's WAN address behind the ISP router; without it the same request correctly gives
  double-NAT candidates), the PC's device linked to a person (the device/person pages are a
  later milestone, so this one row is written directly) and the evidence disk reserve lowered to
  1 % / 1 GiB so a small development disk does not refuse packages with
  ``507 disk_space_low``;
* ``<dir>/seed.json`` tells the Playwright test the day and the expected chain.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

#: Expected attribution chain of the seeded 5651 request (``seed.json``): the example chain
#: WAN 100.64.12.9:51001 → 172.20.42.57 (VLAN 42 "Planlama", lease of dhcp42 at 10:42:18) →
#: device "Planlama-PC-14" → Selin Kaya / Planlama.
WAN, WAN_PORT, AT = "100.64.12.9", 51001, (11, 23, 41)
PC, PC_MAC, PC_HOST = "172.20.42.57", "00:00:5e:00:53:57", "PLN-PC14"
PC_SERVER, PC_LEASE = "dhcp42", (10, 42, 18)
ROGUE_MAC = "00:0c:29:00:00:99"
PERSON, DEPARTMENT, DEVICE_NAME = "Selin Kaya", "Planlama", "Planlama-PC-14"
OTHERS = (
    ("172.20.42.165", "3c:52:82:00:00:05", "PLN-PC-05", "dhcp42"),
    ("172.20.41.15", "00:15:5d:00:00:20", "FIN-PC-01", "dhcp41"),
    ("172.20.43.44", "a4:83:e7:00:00:44", "IDR-PC-04", "dhcp43"),
)

SEED_CONFIG = (
    "\n[tsa]\n"
    'provider = "mock"\n\n'
    "[seal]\n"
    "quiet_seconds = 0\n\n"
    "[[sources]]\n"
    'name = "fortigate"\n'
    'type = "fortigate"\n'
    'address = "172.31.255.1"\n\n'
    "[[sources]]\n"
    'name = "core-r1"\n'
    'type = "mikrotik"\n'
    'address = "172.20.10.1"\n'
)


def _run(*args: str, stdin: str | None = None) -> str:
    print("$", " ".join(args))
    done = subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)  # noqa: S603
    output = done.stdout + done.stderr
    print(output.rstrip())
    if done.returncode != 0:
        raise SystemExit(f"failed ({done.returncode}): {' '.join(args)}")
    return output


class _NoDatabase:
    """Stands in for the session of ``arcilogger_worker.testing.Evidence``: only the raw spool
    lines are wanted here — the real ingest service loads them into the database."""

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _write_spool(config_path: Path) -> date:
    """Yesterday's raw spool files (site time) as the collector writes them."""
    from arcilogger_core.config import load_config
    from arcilogger_core.layout import Layout
    from arcilogger_core.timeutil import site_day, utc_now
    from arcilogger_worker.testing import Evidence, tr

    class SpoolOnly(Evidence):
        def _add(self, row: Any) -> int:
            return 0

    config = load_config(config_path)
    day = site_day(utc_now()) - timedelta(days=1)
    spool = SpoolOnly(_NoDatabase(), Layout(config.data_dir))  # type: ignore[arg-type]
    for ip, mac, hostname, server in OTHERS:
        spool.dhcp(tr(day, 8, 12), "assigned", ip, mac, server=server, hostname=hostname)
    spool.dhcp(tr(day, *PC_LEASE), "assigned", PC, PC_MAC, server=PC_SERVER, hostname=PC_HOST)
    spool.traffic(tr(day, *AT), PC, WAN_PORT, "203.0.113.10", 443, duration=10)
    destinations = [("151.101.1.69", 443), ("104.18.32.7", 443), ("8.8.8.8", 53)]
    for index in range(9):
        ip = OTHERS[index % len(OTHERS)][0]
        dstip, dstport = destinations[index % len(destinations)]
        spool.traffic(
            tr(day, 11, 22, 1) + timedelta(seconds=index * 11),
            ip,
            50100 + index,
            dstip,
            dstport,
            duration=3 + index % 4,
            proto=17 if dstport == 53 else 6,
        )
    # A static device answers ARP for the PC's address: an IP conflict around 13:30.
    spool.arp(tr(day, 13), PC, ROGUE_MAC, interface="vlan42")
    spool.arp(tr(day, 14), PC, ROGUE_MAC, interface="vlan42")
    spool.traffic(tr(day, 13, 30), PC, 61001, "93.184.216.34", 443, duration=12)
    return day


def _seed(config_path: Path, database_dsn: str, workdir: Path) -> None:
    from sqlalchemy.orm import Session

    from arcilogger_db.models import Department, Device, Person
    from arcilogger_db.settings import set_setting
    from arcilogger_db.testing import seed_example_network

    engine = create_engine(database_dsn)
    try:
        # Before the lease rebuild: it takes the VLAN of an address from this plan when the
        # DHCP record names none; the person below joins one of its departments.
        with Session(engine) as session:
            seed_example_network(session)
            session.commit()

        day = _write_spool(config_path)
        config = str(config_path)
        _run("arcilogger-ingest", "run", "--once", "--workers", "0", "--config", config)
        _run("arcilogger-worker", "rebuild", "--day", day.isoformat(), "--config", config)
        _run("arcilogger-core", "--config", config, "seal", "--day", day.isoformat())

        with Session(engine) as session:
            department = session.query(Department).filter(Department.name == DEPARTMENT).one()
            person = Person(name=PERSON, department_id=department.id, title="Planlama Uzmanı")
            session.add(person)
            session.flush()
            device = session.get(Device, PC_MAC)
            if device is None:
                raise SystemExit(f"the lease rebuild made no device for {PC_MAC}")
            device.person_id = person.id
            device.name = DEVICE_NAME
            device.type = "pc"
            # The FortiGate's WAN address behind the ISP router (Ayarlar → NAT), so the
            # request for that address is an exact match instead of double-NAT candidates.
            set_setting(session, "nat.wan_transip", WAN, updated_by="live_stack")
            for key in ("evidence.min_free_percent", "evidence.min_free_gb"):
                set_setting(session, key, 1, updated_by="live_stack")
            session.commit()
    finally:
        engine.dispose()

    seed = {
        "day": day.isoformat(),
        "wan": WAN,
        "port": WAN_PORT,
        "time": "{:02d}:{:02d}:{:02d}".format(*AT),
        "pc": PC,
        "mac": PC_MAC,
        "hostname": PC_HOST,
        "person": PERSON,
        "department": DEPARTMENT,
        "conflict_mac": ROGUE_MAC,
    }
    (workdir / "seed.json").write_text(json.dumps(seed, indent=2), encoding="utf-8")
    print(f"seeded {day}: {workdir / 'seed.json'}")


def _remove(workdir: Path) -> None:
    """Delete the previous run's directory; its sealed vault files are locked (ADR-0003)."""
    from arcilogger_core.immutability import is_locked, unlock

    for path in sorted(workdir.rglob("*"), reverse=True):
        if path.is_file() and is_locked(path):
            unlock(path)
    shutil.rmtree(workdir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="working directory (recreated)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--drop", action="store_true", help="drop the database when stopped")
    parser.add_argument(
        "--seed", action="store_true", help="a sealed day of log data for the 5651 smoke test"
    )
    args = parser.parse_args()

    admin_dsn = os.environ.get("ARCILOGGER_TEST_PG")
    if not admin_dsn:
        raise SystemExit("ARCILOGGER_TEST_PG is not set (admin DSN of a throwaway server)")
    admin = urlparse(admin_dsn)
    database = f"arci_web_smoke_{secrets.token_hex(4)}"
    driver_dsn = admin_dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    workdir: Path = args.dir.resolve()
    if workdir.exists():
        _remove(workdir)
    (workdir / "data").mkdir(parents=True)
    config = workdir / "arcilogger.toml"
    config.write_bytes(
        (
            'site_name = "Örnek A.Ş. (smoke test)"\n'
            f'data_dir = "{(workdir / "data").as_posix()}"\n\n'
            "[database]\n"
            f'host = "{admin.hostname or "127.0.0.1"}"\n'
            f"port = {admin.port or 5432}\n"
            f'name = "{database}"\n'
            f'user = "{admin.username}"\n'
            'timescale = "off"\n\n'
            "[api]\n"
            'host = "127.0.0.1"\n'
            f"port = {args.port}\n"
            "secure_cookies = false\n" + (SEED_CONFIG if args.seed else "")
        ).encode()
    )

    engine = create_engine(driver_dsn, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    print(f"database {database} created")
    try:
        _run("arcilogger-core", "--config", str(config), "secret", "init")
        _run(
            "arcilogger-core",
            "--config",
            str(config),
            "secret",
            "set",
            "database.password",
            stdin=f"{admin.password}\n",
        )
        _run("arcilogger-db", "upgrade", "--config", str(config))
        output = _run("arcilogger-api", "setup-token", "--config", str(config))
        tokens = re.findall(r"[A-Za-z0-9_-]{40,}", output)
        if not tokens:
            raise SystemExit("no setup token in the output of arcilogger-api setup-token")
        (workdir / "setup-token.txt").write_text(tokens[-1], encoding="utf-8")
        print(f"setup token written to {workdir / 'setup-token.txt'}")
        if args.seed:
            database_dsn = urlparse(driver_dsn)._replace(path=f"/{database}").geturl()
            _seed(config, database_dsn, workdir)
        print(f"serving on http://127.0.0.1:{args.port} (Ctrl+C stops)")
        with contextlib.suppress(KeyboardInterrupt):
            subprocess.run(  # noqa: S603
                ["arcilogger-api", "serve", "--config", str(config)],  # noqa: S607
                check=False,
            )
    finally:
        if args.drop:
            with engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            print(f"database {database} dropped")
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
