"""What the lease job stores beside a lease for the missed-move check (issue #127), against
PostgreSQL: the moves (``leases.moves``) and the VDOM of each FortiGate ref (``"vd"``).

A lease records each kept FortiGate ACK of its MAC for another address that the build saw and
did not apply; not one on another interface, in another configured network, refused, applied
(it ends the lease) or after the lease's end. The value is bounded: a flapping MAC stays at the
cap and an old move goes, marked ``truncated``. A run with nothing new writes nothing. A seed's
VDOM comes from its stored ref, so D33 still closes it once the row of its newest ACK is gone.
That live runs, a range rebuild and single-day rebuilds store the same moves and VDOMs is checked
with every other column in ``test_leases_consistency.py``. Every stored value here is the job's
own; none is written by hand.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from arcilogger_db.models import FgDhcp, Lease
from arcilogger_worker.correlation.leases import rebuild_day, rebuild_days, run_incremental
from arcilogger_worker.correlation.watermark import set_watermark
from arcilogger_worker.lookup.refs import MOVE_TIMES_CAP, epoch_us
from arcilogger_worker.testing import Evidence, tr

D = date(2026, 10, 12)
D1 = D + timedelta(days=1)
D2 = D + timedelta(days=2)

PC = "172.20.42.57"  # PLANLAMA 172.20.42.0/24
MAC = "00:00:5e:00:53:57"
NOWHERE = "172.20.44.58"  # in no configured network
SAME = "172.20.42.60"  # PLANLAMA too
STAFF = "172.20.60.20"  # Wi-Fi Personel: another configured network
MISSING = "-"  # a ref without a "vd" key


def moves(session: Session) -> dict[tuple[str, str, datetime], Any]:
    """The stored moves by (ip, mac, start), read the only way they can be read."""
    session.expire_all()
    rows = session.execute(select(Lease.ip, Lease.mac, Lease.start, Lease.moves))
    return {(ip, mac, start): value for ip, mac, start, value in rows}


def group(ip: str, *times: datetime, vd: str | None = "root", interface: str = "vlan42") -> Any:
    return {
        "ip": ip,
        "interface": interface,
        "vd": vd,
        "source": "fortigate",
        "logid": "0100026001",
        "dhcp_msg": "Ack",
        "times": [epoch_us(t) for t in times],
    }


def value(*groups: Any, truncated: bool = False) -> dict[str, Any]:
    """``leases.moves`` with ``groups`` (none: recorded, none)."""
    return {"truncated": truncated, "groups": list(groups)}


NONE = value()


def recorded(session: Session) -> dict[int, tuple[Any, ...]]:
    """What this file is about, by lease id: the lease's end, its refs' VDOMs and its moves."""
    session.expire_all()
    stored: dict[int, Any] = dict(session.execute(select(Lease.id, Lease.moves)).all())
    return {
        r.id: (
            r.end,
            tuple((e["table"], e["row_id"], e.get("vd", MISSING)) for e in r.evidence),
            stored[r.id],
        )
        for r in session.scalars(select(Lease))
    }


# -- what is recorded -----------------------------------------------------------------------------


CASES = {
    # (the ACK of MAC at D 10:00: ip, interface, vd, dhcp_msg) -> (PC's end, PC's moves)
    "no-network": ((NOWHERE, "vlan42", "root", "Ack"), (None, value(group(NOWHERE, tr(D, 10))))),
    "other-interface": ((NOWHERE, "vlan44", "root", "Ack"), (None, NONE)),
    "other-vdom": (
        (SAME, "vlan42", "misafir", "Ack"), (None, value(group(SAME, tr(D, 10), vd="misafir")))
    ),
    "other-network": ((STAFF, "vlan42", "root", "Ack"), (None, NONE)),
    "refused": ((NOWHERE, "vlan42", "root", "Nak"), (None, NONE)),
    "applied": ((SAME, "vlan42", "root", "Ack"), (tr(D, 10), NONE)),
}  # fmt: skip


@pytest.mark.parametrize("case", list(CASES))
def test_a_lease_records_the_acks_of_its_mac_the_build_did_not_apply(
    db_session: Session, evidence: Evidence, case: str
) -> None:
    """D33's leftovers: an ACK the build applied ends the lease and is no move; one on another
    interface or in another configured network never bears on the lease, a refused row is no
    evidence. An address in no configured network, or another VDOM, is recorded as it was
    logged (the lookup tests it again with its own settings and networks)."""
    (ip, interface, vd, msg), (end, expected) = CASES[case]
    evidence.fortigate_dhcp(tr(D, 8), PC, MAC)
    evidence.fortigate_dhcp(tr(D, 10), ip, MAC, interface=interface, vd=vd, dhcp_msg=msg)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D, 18))
    db_session.commit()
    pc = moves(db_session)[(PC, MAC, tr(D, 8))]
    (row,) = db_session.scalars(select(Lease).where(Lease.ip == PC))
    assert (row.end, pc) == (end, expected)
    # Every FortiGate ref of every lease carries its row's VDOM.
    for lease in db_session.scalars(select(Lease)):
        for item in lease.evidence:
            assert item["table"] != "fg_dhcp" or item["vd"] in ("root", "misafir"), item


def test_a_mikrotik_lease_records_a_fortigate_ack_of_its_mac_elsewhere(
    db_session: Session, evidence: Evidence
) -> None:
    """A MikroTik lease's moves are any interface's; the migration case (an ACK in its own
    network) ends it instead. An ARP interval records nothing (NULL)."""
    mt_ip = "172.20.43.120"
    evidence.dhcp(tr(D, 8), "assigned", mt_ip, MAC)
    evidence.fortigate_dhcp(tr(D, 9), NOWHERE, MAC, interface="vlan44")
    evidence.fortigate_dhcp(tr(D, 9, 30), STAFF, MAC, interface="vlan60")  # another network
    evidence.fortigate_dhcp(tr(D, 10), "172.20.43.121", MAC, interface="vlan43")  # migration
    evidence.arp(tr(D, 8), "172.20.43.15", "00:00:5e:00:53:15")
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D, 18))
    db_session.commit()
    stored = moves(db_session)
    assert stored[(mt_ip, MAC, tr(D, 8))] == value(group(NOWHERE, tr(D, 9), interface="vlan44"))
    (mt,) = db_session.scalars(select(Lease).where(Lease.ip == mt_ip))
    assert (mt.end, mt.end_reason) == (tr(D, 10), "superseded")
    assert stored[("172.20.43.15", "00:00:5e:00:53:15", tr(D, 8))] is None


def test_moves_after_an_earlier_end_are_cut(db_session: Session, evidence: Evidence) -> None:
    """The day's rebuild records a move before midnight, its look-ahead one after; late
    evidence then ends the lease before midnight, after the first (D33), and the seed's rebuild
    drops both moves after its new end, the stored one before midnight included, as does the
    range rebuild that the late evidence job runs. The new lease keeps them."""
    evidence.fortigate_dhcp(tr(D, 20), PC, MAC)
    evidence.fortigate_dhcp(tr(D, 23, 55), NOWHERE, MAC)
    evidence.fortigate_dhcp(tr(D1, 3), NOWHERE, MAC)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D1, 12))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 20))] == value(
        group(NOWHERE, tr(D, 23, 55), tr(D1, 3))
    )
    evidence.fortigate_dhcp(tr(D, 23, 50), SAME, MAC)  # ingest backlog
    db_session.commit()
    report = rebuild_day(db_session, D1, horizon=tr(D1, 12))
    db_session.commit()
    assert report.stats["seeds_closed_before_midnight"] == 1
    assert moves(db_session)[(PC, MAC, tr(D, 20))] == NONE
    single = recorded(db_session)
    set_watermark(db_session, tr(D1, 12))
    db_session.commit()
    rebuild_days(db_session, D, D1)
    after = recorded(db_session)
    (pc_id,) = db_session.scalars(select(Lease.id).where(Lease.ip == PC))
    assert after[pc_id] == single[pc_id]
    stored = moves(db_session)
    assert stored[(PC, MAC, tr(D, 20))] == NONE
    assert stored[(SAME, MAC, tr(D, 23, 50))] == value(group(NOWHERE, tr(D, 23, 55), tr(D1, 3)))


# -- what a run reads and writes ------------------------------------------------------------------


def test_a_run_with_nothing_new_writes_nothing_and_reads_no_moves(
    db_session: Session, evidence: Evidence
) -> None:
    """Idempotent: a rebuild and the incremental job leave recorded rows as they are. The
    incremental job reads an open lease's moves only when it changes them (every minute, for
    every open lease, it only tests them for NULL)."""
    evidence.fortigate_dhcp(tr(D, 8), PC, MAC)
    evidence.fortigate_dhcp(tr(D, 10), NOWHERE, MAC)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D, 12))
    set_watermark(db_session, tr(D, 12))
    db_session.commit()
    reference = recorded(db_session)
    again = rebuild_day(db_session, D, horizon=tr(D, 12))
    db_session.commit()
    assert (again.inserted, again.updated, again.deleted) == (0, 0, 0)

    statements: list[str] = []

    def seen(conn: Connection, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", seen)
    try:
        # Another client's lease: a new row, nothing for PC's.
        evidence.fortigate_dhcp(tr(D, 12, 30), "172.20.42.80", "00:00:5e:00:53:80")
        db_session.commit()
        report = run_incremental(db_session, now=tr(D, 13))
        db_session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", seen)
    assert report.mode == "incremental"
    assert (report.inserted, report.updated) == (1, 0)
    assert {k: recorded(db_session)[k] for k in reference} == reference
    reads = [s for s in statements if s.startswith("SELECT") and "moves" in s]
    assert reads
    assert all("moves IS NULL" in s for s in reads), reads

    # A move in the incremental window is read, merged and written once.
    evidence.fortigate_dhcp(tr(D, 13, 30), NOWHERE, MAC)
    db_session.commit()
    report = run_incremental(db_session, now=tr(D, 14))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == value(group(NOWHERE, tr(D, 10), tr(D, 13, 30)))
    assert report.updated >= 1
    report = run_incremental(db_session, now=tr(D, 14, 30))
    db_session.commit()
    assert report.updated == 0
    # A rebuild that ends before the watermark keeps the moves after its end.
    rebuild_day(db_session, D, horizon=tr(D, 12))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == value(group(NOWHERE, tr(D, 10), tr(D, 13, 30)))


def test_a_lease_written_before_the_moves_is_recorded_once_it_is_followed(
    db_session: Session, evidence: Evidence
) -> None:
    """NULL is "not recorded" (a lease of an older program): a run that follows the open lease
    records it from then on; one that closed before the run stays NULL. Its refs gain their
    VDOM when their rows are read again."""
    evidence.fortigate_dhcp(tr(D, 8), PC, MAC)
    evidence.fortigate_dhcp(tr(D, 6), "172.20.42.70", "00:00:5e:00:53:70", lease=3600)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D, 12))
    set_watermark(db_session, tr(D, 12))
    db_session.commit()
    for lease in db_session.scalars(select(Lease)):
        legacy = [{k: v for k, v in item.items() if k != "vd"} for item in lease.evidence]
        db_session.execute(
            update(Lease).where(Lease.id == lease.id).values(moves=None, evidence=legacy)
        )
    db_session.commit()
    assert set(moves(db_session).values()) == {None}
    evidence.fortigate_dhcp(tr(D, 12, 30), NOWHERE, MAC)
    db_session.commit()
    run_incremental(db_session, now=tr(D, 13))
    db_session.commit()
    stored = moves(db_session)
    assert stored[(PC, MAC, tr(D, 8))] == value(group(NOWHERE, tr(D, 12, 30)))
    assert stored[("172.20.42.70", "00:00:5e:00:53:70", tr(D, 6))] is None
    rebuild_day(db_session, D, horizon=tr(D, 12))
    db_session.commit()
    rows = {r.ip: r for r in db_session.scalars(select(Lease))}
    assert [e.get("vd", MISSING) for e in rows[PC].evidence] == ["root"]
    assert moves(db_session)[("172.20.42.70", "00:00:5e:00:53:70", tr(D, 6))] == NONE


def test_a_closed_lease_written_before_the_moves_stays_not_recorded(
    db_session: Session, evidence: Evidence
) -> None:
    """A seed of an older program (NULL) that late evidence closes before midnight is not
    followed by the next day's rebuild: its moves stay NULL (not recorded), not "none"."""
    evidence.fortigate_dhcp(tr(D, 20), PC, MAC)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D1, 12))
    db_session.commit()
    db_session.execute(update(Lease).values(moves=None))
    db_session.commit()
    evidence.fortigate_dhcp(tr(D, 23, 50), SAME, MAC)  # ingest backlog
    db_session.commit()
    report = rebuild_day(db_session, D1, horizon=tr(D1, 12))
    db_session.commit()
    assert report.stats["seeds_closed_before_midnight"] == 1
    assert moves(db_session)[(PC, MAC, tr(D, 20))] is None


# -- the bound ------------------------------------------------------------------------------------


#: The longest lease a FortiGate logs (100 days, ``LEASE_SECONDS_RANGE``).
LONGEST = 8_640_000


def test_a_flapping_mac_stays_at_the_cap_and_keeps_the_newest(
    db_session: Session, evidence: Evidence
) -> None:
    """Lease rows are never dropped, so the moves are bounded: a MAC that takes an address in no
    configured network every hour for seven weeks keeps the newest :data:`MOVE_TIMES_CAP` times,
    marked ``truncated``. A rebuild under the cap is not truncated; the catch-up that adds the
    rest, day by day, and a rebuild from scratch store the same value."""
    evidence.fortigate_dhcp(tr(D, 8), PC, MAC, lease=LONGEST)
    hours = [tr(D, 9) + timedelta(hours=k) for k in range(MOVE_TIMES_CAP + 200)]
    for moment in hours:
        evidence.fortigate_dhcp(moment, NOWHERE, MAC, lease=3600)
    db_session.commit()
    rebuild_day(db_session, D, horizon=hours[600])
    set_watermark(db_session, hours[600])
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == value(group(NOWHERE, *hours[:600]))
    report = run_incremental(db_session, now=hours[-1] + timedelta(hours=1))
    db_session.commit()
    assert report.mode == "catch_up"
    capped = value(group(NOWHERE, *hours[-MOVE_TIMES_CAP:]), truncated=True)
    (row,) = db_session.scalars(select(Lease).where(Lease.ip == PC))
    assert row.end is None
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == capped
    db_session.execute(delete(Lease))
    db_session.commit()
    rebuild_day(db_session, D, horizon=hours[-1] + timedelta(minutes=1))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == capped


def test_moves_older_than_the_window_before_the_end_are_dropped(
    db_session: Session, evidence: Evidence
) -> None:
    """A closed lease keeps the moves of the 100 days before its end (the look-back of the
    missed-move check), an open one those before its newest move (the live job does not
    rewrite an open lease without a new move, so the run's end cannot be the anchor)."""
    renewal = tr(D, 8) + timedelta(days=60)
    evidence.fortigate_dhcp(tr(D, 8), PC, MAC, lease=LONGEST)
    evidence.fortigate_dhcp(renewal, PC, MAC, lease=LONGEST)  # ends 100 days after it
    moved = [tr(D, 12) + timedelta(days=10 * k) for k in range(16)]
    for moment in moved:
        evidence.fortigate_dhcp(moment, NOWHERE, MAC, lease=3600)
    db_session.commit()
    rebuild_day(db_session, D, horizon=renewal + timedelta(days=5))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == value(group(NOWHERE, *moved[:7]))
    rebuild_day(db_session, D, horizon=renewal + timedelta(days=101))
    db_session.commit()
    (row,) = db_session.scalars(select(Lease).where(Lease.ip == PC))
    assert (row.end, row.end_reason) == (renewal + timedelta(seconds=LONGEST), "timeout")
    # The end minus 100 days is the renewal: the moves up to it go.
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == value(
        group(NOWHERE, *moved[6:]), truncated=True
    )


# -- the VDOM of a seed ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored_vd", [True, False], ids=["stored-vd", "legacy-ref"])
def test_a_seed_keeps_its_vdom_once_the_row_of_its_ack_is_gone(
    db_session: Session, evidence: Evidence, stored_vd: bool
) -> None:
    """D33 needs the seed's VDOM (an ACK of its MAC in its own network ends it only from the
    same VDOM). Its newest ACK's row is gone (dropped with its day): the stored ref's ``"vd"``
    still gives it, so the ACK ends the lease. A ref of an older program has none: the VDOM is
    unknown, the ACK does not end the lease and is recorded as a move instead."""
    ack = evidence.fortigate_dhcp(tr(D, 8), PC, MAC)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D, 12))
    set_watermark(db_session, tr(D, 12))
    db_session.commit()
    (lease,) = db_session.scalars(select(Lease))
    assert [e["vd"] for e in lease.evidence] == ["root"]
    if not stored_vd:
        legacy = [{k: v for k, v in item.items() if k != "vd"} for item in lease.evidence]
        db_session.execute(update(Lease).where(Lease.id == lease.id).values(evidence=legacy))
    db_session.execute(delete(FgDhcp).where(FgDhcp.id == ack))
    evidence.fortigate_dhcp(tr(D1, 7), SAME, MAC)
    db_session.commit()
    run_incremental(db_session, now=tr(D1, 7, 30))
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(Lease, lease.id)
    assert row is not None
    if stored_vd:
        assert (row.end, row.end_reason) == (tr(D1, 7), "superseded")
        assert moves(db_session)[(PC, MAC, tr(D, 8))] == NONE
    else:
        assert (row.end, row.end_reason) == (None, "open")
        assert moves(db_session)[(PC, MAC, tr(D, 8))] == value(group(SAME, tr(D1, 7)))
