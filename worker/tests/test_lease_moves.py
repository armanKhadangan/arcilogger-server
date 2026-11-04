"""What the lease job stores beside a lease for the missed-move check (issue #127), against
PostgreSQL: the moves (``leases.moves``) and the VDOM of each FortiGate ref (``"vd"``).

A lease records each kept FortiGate ACK of its MAC for another address that the build saw and
did not apply; not one on another interface, in another configured network, refused, applied
(it ends the lease) or after the lease's end. Live incremental runs, a range rebuild and
single-day rebuilds in any order store the same moves and VDOMs, and a run with nothing new
writes nothing. A seed's VDOM comes from its stored ref, so D33 still closes it once the row of
its newest ACK is gone. Every stored value here is the job's own; none is written by hand.
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
from arcilogger_worker.correlation.watermark import get_watermark, set_watermark
from arcilogger_worker.lookup.refs import epoch_us
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


def snapshot(session: Session) -> dict[int, tuple[Any, ...]]:
    """Every column a rebuild computes, the moves and the refs' VDOMs, by lease id."""
    session.expire_all()
    stored: dict[int, Any] = dict(session.execute(select(Lease.id, Lease.moves)).all())
    return {
        r.id: (
            r.ip, r.mac, r.vlan, r.server, r.hostname, r.start, r.end, r.end_reason,
            r.confidence, r.source_table, r.last_evidence, r.lease_seconds,
            tuple((e["table"], e["row_id"], e.get("vd", MISSING)) for e in r.evidence),
            stored[r.id],
        )
        for r in session.scalars(select(Lease))
    }  # fmt: skip


# -- what is recorded -----------------------------------------------------------------------------


CASES = {
    # (the ACK of MAC at D 10:00: ip, interface, vd, dhcp_msg) -> (PC's end, PC's moves)
    "no-network": ((NOWHERE, "vlan42", "root", "Ack"), (None, [group(NOWHERE, tr(D, 10))])),
    "other-interface": ((NOWHERE, "vlan44", "root", "Ack"), (None, [])),
    "other-vdom": (
        (SAME, "vlan42", "misafir", "Ack"), (None, [group(SAME, tr(D, 10), vd="misafir")])
    ),
    "other-network": ((STAFF, "vlan42", "root", "Ack"), (None, [])),
    "refused": ((NOWHERE, "vlan42", "root", "Nak"), (None, [])),
    "applied": ((SAME, "vlan42", "root", "Ack"), (tr(D, 10), [])),
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
    assert stored[(mt_ip, MAC, tr(D, 8))] == [group(NOWHERE, tr(D, 9), interface="vlan44")]
    (mt,) = db_session.scalars(select(Lease).where(Lease.ip == mt_ip))
    assert (mt.end, mt.end_reason) == (tr(D, 10), "superseded")
    assert stored[("172.20.43.15", "00:00:5e:00:53:15", tr(D, 8))] is None


def test_moves_after_an_earlier_end_are_cut(db_session: Session, evidence: Evidence) -> None:
    """The look-ahead records a move after midnight; late evidence then ends the lease before
    midnight (D33), and the seed's rebuild drops the move after its new end, as does the range
    rebuild that the late evidence job runs. The new lease keeps it."""
    evidence.fortigate_dhcp(tr(D, 20), PC, MAC)
    evidence.fortigate_dhcp(tr(D1, 3), NOWHERE, MAC)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D1, 12))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 20))] == [group(NOWHERE, tr(D1, 3))]
    evidence.fortigate_dhcp(tr(D, 23, 50), SAME, MAC)  # ingest backlog
    db_session.commit()
    report = rebuild_day(db_session, D1, horizon=tr(D1, 12))
    db_session.commit()
    assert report.stats["seeds_closed_before_midnight"] == 1
    assert moves(db_session)[(PC, MAC, tr(D, 20))] == []
    single = snapshot(db_session)
    set_watermark(db_session, tr(D1, 12))
    db_session.commit()
    rebuild_days(db_session, D, D1)
    after = snapshot(db_session)
    (pc_id,) = db_session.scalars(select(Lease.id).where(Lease.ip == PC))
    assert after[pc_id] == single[pc_id]
    stored = moves(db_session)
    assert stored[(PC, MAC, tr(D, 20))] == []
    assert stored[(SAME, MAC, tr(D, 23, 50))] == [group(NOWHERE, tr(D1, 3))]


# -- every mode stores the same -------------------------------------------------------------------


def scenario(ev: Evidence) -> None:
    mt_ip, mt_mac = "172.20.43.120", "00:00:5e:00:53:20"
    # PC: moves on both sides of a midnight (one in another VDOM), a refused ACK, then an ACK
    # in its own network and VDOM ends it; the new lease records what comes after.
    ev.fortigate_dhcp(tr(D, 20), PC, MAC, hostname="PLN-PC14")
    ev.fortigate_dhcp(tr(D, 22), NOWHERE, MAC)
    ev.fortigate_dhcp(tr(D1, 1), NOWHERE, MAC, dhcp_msg="Nak")
    ev.fortigate_dhcp(tr(D1, 3), NOWHERE, MAC)
    ev.fortigate_dhcp(tr(D1, 5), SAME, MAC, vd="misafir")
    ev.fortigate_dhcp(tr(D1, 7), STAFF, MAC)
    ev.fortigate_dhcp(tr(D1, 12), "172.20.42.61", MAC)
    ev.fortigate_dhcp(tr(D1, 13), NOWHERE, MAC)
    ev.fortigate_dhcp(tr(D1, 14), "172.20.44.59", MAC, interface="vlan44")
    # A MikroTik lease across midnight with a FortiGate ACK of its MAC on each side.
    ev.dhcp(tr(D, 21), "assigned", mt_ip, mt_mac)
    ev.fortigate_dhcp(tr(D, 23), "172.20.44.70", mt_mac, interface="vlan44")
    ev.fortigate_dhcp(tr(D1, 4), "172.20.44.71", mt_mac, interface="vlan44", vd=None)
    # An ARP interval: no moves.
    for hour in range(8, 12):
        ev.arp(tr(D, hour), "172.20.43.15", "00:00:5e:00:53:15")


END = tr(D2, 18)


def live(session: Session, *, step: timedelta = timedelta(minutes=11)) -> None:
    """The service: the incremental job every few minutes (window cuts at odd times)."""
    now = tr(D, 7, 3)
    while now <= END:
        run_incremental(session, now=now)
        session.commit()
        now += step


def test_live_range_and_single_day_rebuilds_store_the_same_moves_and_vdoms(
    db_session: Session, evidence: Evidence
) -> None:
    scenario(evidence)
    db_session.commit()
    live(db_session)
    reference = snapshot(db_session)
    stored = moves(db_session)
    assert stored[(PC, MAC, tr(D, 20))] == [
        group(SAME, tr(D1, 5), vd="misafir"),
        group(NOWHERE, tr(D, 22), tr(D1, 3)),
    ]
    assert stored[("172.20.42.61", MAC, tr(D1, 12))] == [
        group(NOWHERE, tr(D1, 13)),
    ]
    assert stored[("172.20.43.120", "00:00:5e:00:53:20", tr(D, 21))] == [
        {**group("172.20.44.70", tr(D, 23), interface="vlan44")},
        {**group("172.20.44.71", tr(D1, 4), interface="vlan44", vd=None)},
    ]
    assert stored[("172.20.43.15", "00:00:5e:00:53:15", tr(D, 8))] is None
    vds = {v for row in reference.values() for t, _, v in row[12] if t == "fg_dhcp"}
    assert vds == {"root", "misafir", None}
    watermark = get_watermark(db_session)
    assert watermark is not None

    report = rebuild_days(db_session, D, D2)
    assert (report.inserted, report.deleted) == (0, 0)
    assert snapshot(db_session) == reference, "range"
    for order in ([D2, D1, D], [D1], [D], [D1, D, D2]):
        for day in order:
            single = rebuild_day(db_session, day)
            db_session.commit()
            assert (single.inserted, single.updated, single.deleted) == (0, 0, 0), day
        assert snapshot(db_session) == reference, f"single days {order}"
    assert get_watermark(db_session) == watermark

    # From scratch, in order: the same values (ids aside).
    db_session.execute(delete(Lease))
    db_session.commit()
    rebuild_days(db_session, D, D2)

    def by_pair(rows: dict[int, tuple[Any, ...]]) -> dict[tuple[Any, ...], tuple[Any, ...]]:
        return {(r[0], r[1], r[5]): r for r in rows.values()}

    assert by_pair(snapshot(db_session)) == by_pair(reference)


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
    reference = snapshot(db_session)
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
    assert {k: snapshot(db_session)[k] for k in reference} == reference
    reads = [s for s in statements if s.startswith("SELECT") and "moves" in s]
    assert reads
    assert all("moves IS NULL" in s for s in reads), reads

    # A move in the incremental window is read, merged and written once.
    evidence.fortigate_dhcp(tr(D, 13, 30), NOWHERE, MAC)
    db_session.commit()
    report = run_incremental(db_session, now=tr(D, 14))
    db_session.commit()
    assert moves(db_session)[(PC, MAC, tr(D, 8))] == [group(NOWHERE, tr(D, 10), tr(D, 13, 30))]
    assert report.updated >= 1
    report = run_incremental(db_session, now=tr(D, 14, 30))
    db_session.commit()
    assert report.updated == 0


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
    assert stored[(PC, MAC, tr(D, 8))] == [group(NOWHERE, tr(D, 12, 30))]
    assert stored[("172.20.42.70", "00:00:5e:00:53:70", tr(D, 6))] is None
    rebuild_day(db_session, D, horizon=tr(D, 12))
    db_session.commit()
    rows = {r.ip: r for r in db_session.scalars(select(Lease))}
    assert [e.get("vd", MISSING) for e in rows[PC].evidence] == ["root"]
    assert moves(db_session)[("172.20.42.70", "00:00:5e:00:53:70", tr(D, 6))] == []


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
        assert moves(db_session)[(PC, MAC, tr(D, 8))] == []
    else:
        assert (row.end, row.end_reason) == (None, "open")
        assert moves(db_session)[(PC, MAC, tr(D, 8))] == [group(SAME, tr(D1, 7))]
