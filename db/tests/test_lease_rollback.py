"""The lease step of a rollback and its undoing at the re-upgrade (ADR-0016, round 6).

``arcilogger-db leases prepare-rollback`` lowers every FortiGate DHCP lease with one
``updated_at`` and records it in ``leases.rollback_lowered``; ``arcilogger-db upgrade`` (its seed)
restores the leases the previous version left as they were. The previous version is simulated by
rewriting a lowered lease the way its timeout does (``end`` set, a later ``updated_at``).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.cli import main
from arcilogger_db.lease_rollback import (
    DEFAULT_LEASE_SECONDS,
    ROLLBACK_KEY,
    prepare_rollback,
    restore_after_rollback,
)
from arcilogger_db.models import FgDhcp, Lease, MtDhcp, Setting
from arcilogger_db.seed import seed
from arcilogger_db.settings import SYSTEM_AUTHOR

CONFIG = """
data_dir = "{data}"

[database]
host = "{host}"
port = {port}
name = "{name}"
user = "{user}"
connect_timeout_seconds = 5
"""
AT = datetime(2026, 10, 7, 9, 30, 15, 123456, tzinfo=UTC)
MAC = "00:00:5e:00:53:57"
OTHER = "00:00:5e:00:53:58"
RAW = {"source": "fortigate", "raw_day": AT.date(), "raw_line": 1, "raw_offset": 0}


@pytest.fixture
def config(pg_dsn: str, tmp_path: Path) -> Path:
    """``arcilogger.toml`` and the secret store for this test's migrated database."""
    url = make_url(pg_dsn)
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(),
            host=url.host or "127.0.0.1",
            port=url.port or 5432,
            name=url.database,
            user=url.username,
        ),
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    return path


@pytest.fixture
def session(db_engine: Engine) -> Iterator[Session]:
    with Session(db_engine) as session:
        yield session


def _lease(
    session: Session,
    ip: str,
    *,
    table: str = "fg_dhcp",
    confidence: str = "high",
    start: datetime = AT - timedelta(hours=5),
    end: datetime | None = None,
    lease_seconds: int | None = None,
    mac: str = MAC,
) -> int:
    lease = Lease(
        ip=ip,
        mac=mac,
        vlan=42,
        server="vlan42" if table == "fg_dhcp" else ("dhcp42" if table == "mt_dhcp" else None),
        start=start,
        end=end,
        end_reason="open" if end is None else "dhcp",
        confidence=confidence,
        source_table=table,
        last_evidence=start,
        lease_seconds=lease_seconds,
    )
    session.add(lease)
    session.flush()
    return lease.id


def _state(session: Session) -> dict[int, tuple[str, datetime, datetime | None]]:
    session.expire_all()
    state = {
        lease.id: (lease.confidence, lease.updated_at, lease.end)
        for lease in session.scalars(select(Lease))
    }
    session.commit()  # no read transaction stays open while a command runs
    return state


def _evidence(session: Session) -> list[Any]:
    session.expire_all()
    rows: list[Any] = [
        (row.id, row.time, row.ip, row.mac, row.lease, row.extra)
        for row in session.scalars(select(FgDhcp).order_by(FgDhcp.id))
    ]
    rows += [
        (row.id, row.time, row.ip, row.mac, row.action, row.extra)
        for row in session.scalars(select(MtDhcp).order_by(MtDhcp.id))
    ]
    session.commit()
    return rows


def _site(session: Session) -> dict[str, int]:
    """One lease of each kind, the evidence rows beside them, committed with older times."""
    ids = {
        "open": _lease(session, "172.20.42.57", lease_seconds=86400),
        "ended": _lease(session, "172.20.42.58", end=AT - timedelta(hours=1), mac=OTHER),
        "low": _lease(
            session, "172.20.42.59", confidence="low", end=AT - timedelta(hours=2), mac=OTHER
        ),
        "mikrotik": _lease(session, "172.20.41.20", table="mt_dhcp"),
        "arp": _lease(session, "172.20.10.5", table="mt_arp", confidence="medium"),
    }
    session.add(
        FgDhcp(
            time=AT - timedelta(hours=5),
            time_received=AT - timedelta(hours=5),
            sender="192.0.2.1",
            logid="0100026001",
            dhcp_msg="Ack",
            interface="vlan42",
            ip="172.20.42.57",
            mac=MAC,
            lease=86400,
            device_time=True,
            extra={"vd": "root"},
            **RAW,
        )
    )
    session.add(
        MtDhcp(
            time=AT - timedelta(hours=5),
            time_received=AT - timedelta(hours=5),
            sender="192.0.2.2",
            server="dhcp41",
            action="assigned",
            ip="172.20.41.20",
            mac="00:00:5e:00:53:20",
            **{**RAW, "source": "mikrotik"},
        )
    )
    session.commit()
    return ids


def _rewrite_as_the_previous_version(session: Session, lease_id: int) -> None:
    """The previous version's timeout closes the lease later than the step (stored ``low``)."""
    session.execute(
        update(Lease)
        .where(Lease.id == lease_id)
        .values(
            end=AT - timedelta(minutes=30), end_reason="timeout", updated_at=AT + timedelta(hours=2)
        )
    )
    session.commit()


def test_prepare_rollback_lowers_every_fortigate_lease_and_prints_the_count(
    config: Path, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = _site(session)
    before, evidence = _state(session), _evidence(session)

    assert main(["leases", "prepare-rollback", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert out.strip().endswith(": 2")
    assert "FortiGate kiraları düşük güvene indirildi" in out
    assert "[FortiGate leases lowered to low confidence]" in out

    after = _state(session)
    lowered = {after[ids["open"]][1], after[ids["ended"]][1]}
    assert len(lowered) == 1  # one updated_at for the whole statement
    (at,) = lowered
    assert after[ids["open"]][0] == after[ids["ended"]][0] == "low"
    assert after[ids["open"]][2] is None  # nothing else changes: open stays open
    assert after[ids["ended"]][2] == before[ids["ended"]][2]
    for name in ("low", "mikrotik", "arp"):  # already low, MikroTik and ARP: untouched
        assert after[ids[name]] == before[ids[name]]
    assert _evidence(session) == evidence

    row = session.get(Setting, ROLLBACK_KEY)
    assert row is not None
    assert row.updated_by == SYSTEM_AUTHOR
    assert row.value == {"at": at.isoformat(timespec="microseconds"), "count": 2}
    assert datetime.fromisoformat(row.value["at"]) == at


def test_the_step_sets_its_lock_and_statement_timeouts(session: Session) -> None:
    prepare_rollback(session, AT)
    assert session.execute(text("SHOW lock_timeout")).scalar_one() == "30s"
    assert session.execute(text("SHOW statement_timeout")).scalar_one() == "10min"
    session.rollback()
    assert session.execute(text("SHOW lock_timeout")).scalar_one() == "0"  # SET LOCAL only


def test_the_re_upgrade_restores_only_the_leases_the_previous_version_left(
    config: Path, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = _site(session)
    report = prepare_rollback(session, AT)
    session.commit()
    assert report.count == 2
    _rewrite_as_the_previous_version(session, ids["ended"])
    before = _state(session)

    assert main(["upgrade", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert (
        "Geri almadan sonra FortiGate kiraları [FortiGate leases after a rollback]: "
        "1 yüksek güvene döndü [restored to high], 2 hâlâ düşük güvende [still low]"
    ) in out
    # the rollback day minus the longest FortiGate lease (one day), to today
    assert f"Kiraları {site_day(AT - timedelta(days=1))} - {site_day(utc_now())} arasında" in out
    assert "Kiraları yeniden oluştur" in out

    after = _state(session)
    assert after[ids["open"]][0] == "high"  # untouched since the step: this version's row
    assert after[ids["open"]][2] is None
    assert after[ids["ended"]] == before[ids["ended"]]  # rewritten: low until a rebuild
    assert after[ids["low"]] == before[ids["low"]]  # low before the step: not the step's
    for name in ("mikrotik", "arp"):
        assert after[ids[name]] == before[ids[name]]
    assert session.get(Setting, ROLLBACK_KEY) is None

    # a second run changes nothing and prints no rollback line
    assert main(["upgrade", "--config", str(config)]) == 0
    assert "Geri almadan sonra" not in capsys.readouterr().out
    assert _state(session) == after


def test_a_lease_low_from_an_earlier_rollback_is_not_restored(session: Session) -> None:
    ids = _site(session)
    prepare_rollback(session, AT)
    session.commit()
    _rewrite_as_the_previous_version(session, ids["open"])
    first = restore_after_rollback(session, AT + timedelta(days=1))
    session.commit()
    assert first is not None
    assert (first.restored, first.still_low) == (1, 2)  # "ended" restored
    rewritten_at = _state(session)[ids["open"]][1]

    # the next rollback lowers the high leases only; the earlier low one keeps its updated_at
    later = AT + timedelta(days=3)
    second = prepare_rollback(session, later)
    session.commit()
    assert second.count == 1
    assert _state(session)[ids["open"]][1] == rewritten_at
    restore = restore_after_rollback(session, later + timedelta(hours=1))
    session.commit()
    assert restore is not None
    assert (restore.restored, restore.still_low) == (1, 2)
    state = _state(session)
    assert state[ids["ended"]][0] == "high"
    assert state[ids["open"]][0] == state[ids["low"]][0] == "low"


def test_a_step_run_again_before_the_re_upgrade_keeps_its_time(session: Session) -> None:
    """A rollback retried after it failed (this version ran in between and opened a lease):
    the second run lowers the new lease with the recorded time, so the re-upgrade restores
    both."""
    ids = _site(session)
    prepare_rollback(session, AT)
    session.commit()
    later = _lease(session, "172.20.42.60", start=AT + timedelta(hours=1), mac=OTHER)
    session.commit()
    again = prepare_rollback(session, AT + timedelta(hours=3))
    session.commit()
    assert (again.at, again.count) == (AT, 3)
    row = session.get(Setting, ROLLBACK_KEY)
    assert row is not None
    assert row.value == {"at": AT.isoformat(timespec="microseconds"), "count": 3}
    restore = restore_after_rollback(session, AT + timedelta(days=1))
    session.commit()
    assert restore is not None
    assert (restore.restored, restore.still_low) == (3, 1)
    state = _state(session)
    assert state[ids["open"]][0] == state[ids["ended"]][0] == state[later][0] == "high"


def test_the_rebuild_range_falls_back_to_the_fortios_default_lease(session: Session) -> None:
    _lease(session, "172.20.42.57")  # no logged lease time
    _lease(session, "172.20.41.20", table="mt_dhcp", lease_seconds=None)
    session.commit()
    prepare_rollback(session, AT)
    session.commit()
    now = AT + timedelta(days=2)
    restore = restore_after_rollback(session, now)
    assert restore is not None
    assert restore.rebuild_from == site_day(AT - timedelta(seconds=DEFAULT_LEASE_SECONDS))
    assert restore.rebuild_to == site_day(now)
    assert (restore.restored, restore.still_low) == (1, 0)


def test_without_a_rollback_step_the_seed_reports_nothing(session: Session) -> None:
    _site(session)
    before = _state(session)
    assert restore_after_rollback(session) is None
    report = seed(session)
    session.commit()
    assert report.rollback_restored is None
    assert report.rollback_still_low is None
    assert report.rebuild_from is None
    assert report.rebuild_to is None
    assert _state(session) == before


def test_a_status_row_without_a_readable_time_restores_nothing(session: Session) -> None:
    ids = _site(session)
    prepare_rollback(session, AT)
    session.commit()
    row = session.get(Setting, ROLLBACK_KEY)
    assert row is not None
    row.value = {"at": "not a time", "count": 2}
    session.commit()
    restore = restore_after_rollback(session, AT + timedelta(days=1))
    session.commit()
    assert restore is not None
    assert (restore.restored, restore.still_low) == (0, 3)
    assert _state(session)[ids["open"]][0] == "low"  # a rebuild re-derives it
    assert session.get(Setting, ROLLBACK_KEY) is None


def test_prepare_rollback_on_an_unreachable_database_exits_3(
    config: Path, pg_dsn: str, capsys: pytest.CaptureFixture[str]
) -> None:
    name = make_url(pg_dsn).database
    content = config.read_text(encoding="utf-8")
    config.write_text(
        content.replace(f'name = "{name}"', 'name = "arci_missing_database"'), encoding="utf-8"
    )
    assert main(["leases", "prepare-rollback", "--config", str(config)]) == 3
    assert "Veritabanına bağlanılamadı" in capsys.readouterr().err
