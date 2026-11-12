"""Access and device rules R03, R04, R11, R12 and R16 (Wave 6 contract §2.2.2, package W6-C).

Every query is SQLAlchemy Core with bound parameters only (contract §0.1 item 3, §5): a list
parameter is one array parameter, a network list one ``cidr[]`` parameter matched with ``<<=``;
nothing an administrator types is placed in the SQL text. Every query of a parsed table bounds
the device ``time`` to the pruning bounds of the context and ``time_received`` to the window
(windows run on receive time, D8), and keeps only the keys with a row in the slice.

* **R03** counts failed logins (``fg_event`` by logid or ``logdesc``, V1; MikroTik
  ``login_failure``) per user **and** per source address: one row counts for both keys.
* **R04** reports each successful login (``fg_event`` by logid or ``logdesc``, V1; MikroTik
  ``login``) outside the work hours (``usage.work_hours`` / ``usage.holidays`` through the
  usage module's helper, D5; a break is work time) or from an address outside
  ``trusted_admin_networks`` (an empty list switches that part off).
* **R11** reports a device the inventory has just learnt: ``devices`` rows **inserted** in the
  slice (``created_at``, the server's clock; contract deviation, see the PR and the contract's
  "Deviations"), so a device the lease jobs write late is not missed; devices that existed when
  the rule started never alert (D12). The evidence is the parsed row of its first sighting.
* **R12** counts the MAC changes of an address in ``mt_arp``, ``mt_dhcp`` (``assigned``) and
  ``fg_dhcp`` (the rows the ``dhcp.fortigate_*`` settings call an assignment, as the lease jobs
  read them); infrastructure MACs are left out. The 5651 ``ip_conflict`` alert is unchanged.
* **R16** reports configuration changes (``fg_event`` by logid, V2; every ``mt_config`` row)
  per source and user; the cooldown of the engine counts the changes inside it.

The FortiGate logids and texts are parameters (``format_verified: false``): the repository has
no format sample of these lines yet (contract §0.4 V1, V2).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    CompoundSelect,
    Integer,
    String,
    Subquery,
    Text,
    and_,
    any_,
    cast,
    false,
    func,
    literal,
    not_,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, MACADDR
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from arcilogger_db.models import Device, FgDhcp, FgEvent, MtAccount, MtArp, MtConfig, MtDhcp
from arcilogger_db.settings import get_settings
from arcilogger_worker.lookup.types import LookupSettings, normalize_mac
from arcilogger_worker.rules.model import EvidenceRef, Hit, Param, RuleContext, RuleDef
from arcilogger_worker.usage.config import UsageSettings

#: The raw reference of an evidence row (contract §2.1.2), in this order in every row query.
_REFERENCE: Final = ("id", "time", "time_received", "source", "raw_day", "raw_line", "raw_offset")
#: MAC addresses listed in R12's values (the evidence holds every row).
MACS_SHOWN: Final = 10
#: Shown for a login or change without a user name (a key never ends empty).
NO_USER: Final = "-"


# -- shared query parts ---------------------------------------------------------------------------


def _param(ctx: RuleContext, key: str) -> Any:
    return ctx.settings.params[key]


def _texts(values: Iterable[object]) -> ColumnElement[Any]:
    """``ANY(:texts)``: one bound ``text[]`` parameter."""
    return any_(cast(sorted({str(v) for v in values}), ARRAY(Text)))


def _in_networks(column: Any, networks: Sequence[str]) -> ColumnElement[bool]:
    """``column <<= ANY(:networks::cidr[])``."""
    contained = column.op("<<=", is_comparison=True)
    return contained(any_(cast(list(networks), ARRAY(CIDR))))  # type: ignore[no-any-return]


def _bounded(table: Any, ctx: RuleContext, since: datetime) -> ColumnElement[bool]:
    """Device time within the pruning bounds, receive time in ``(since, ctx.end]``."""
    return and_(
        table.time >= ctx.device_from,
        table.time <= ctx.device_to,
        table.time_received > since,
        table.time_received <= ctx.end,
    )


def _reference(table: Any, name: str) -> list[ColumnElement[Any]]:
    """The table name (a constant of this module, never a user value) and the raw reference."""
    return [literal(name, String).label("tbl"), *(getattr(table, c).label(c) for c in _REFERENCE)]


def _evidence(row: Any) -> EvidenceRef:
    return EvidenceRef(
        tbl=row.tbl,
        row_id=int(row.id),
        time=row.time,
        time_received=row.time_received,
        source=row.source,
        raw_day=row.raw_day,
        raw_line=int(row.raw_line),
        raw_offset=int(row.raw_offset),
    )


def _user(name: str | None) -> str:
    return name if name else NO_USER


@dataclass
class _Group:
    """The rows of one key, oldest first (receive time, then table and id)."""

    key: str
    rows: list[Any]

    def hit(self, ctx: RuleContext, values: Mapping[str, object]) -> Hit:
        return Hit(
            key=self.key,
            time=max(row.time_received for row in self.rows),
            count=len(self.rows),
            values=dict(values),
            evidence=[_evidence(row) for row in self.rows[: ctx.evidence_max]],
        )


def _groups(rows: Iterable[Any]) -> list[_Group]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row.key].append(row)
    return [
        _Group(key, sorted(found, key=lambda r: (r.time_received, r.tbl, r.id)))
        for key, found in sorted(grouped.items())
    ]


def _counted(
    session: Session, ctx: RuleContext, keyed: Subquery, threshold: int
) -> list[tuple[Any, list[Any]]]:
    """Keys of ``keyed`` (columns ``key`` and the raw reference) with at least ``threshold``
    rows in the window and one in the slice: ``(count row, evidence rows)``, the evidence
    oldest first and at most ``ctx.evidence_max`` per key."""
    counts = session.execute(
        select(
            keyed.c.key,
            func.count().label("n"),
            func.max(keyed.c.time_received).label("last"),
        )
        .group_by(keyed.c.key)
        .having(and_(func.count() >= threshold, func.max(keyed.c.time_received) > ctx.start))
        .order_by(keyed.c.key)
    ).all()
    if not counts:
        return []
    order = func.row_number().over(
        partition_by=keyed.c.key, order_by=(keyed.c.time_received, keyed.c.tbl, keyed.c.id)
    )
    numbered = (
        select(keyed, order.label("n"))
        .where(keyed.c.key == _texts(row.key for row in counts))
        .subquery()
    )
    evidence: dict[str, list[Any]] = defaultdict(list)
    for row in session.execute(
        select(numbered)
        .where(numbered.c.n <= ctx.evidence_max)
        .order_by(numbered.c.key, numbered.c.n)
    ):
        evidence[row.key].append(row)
    return [(row, evidence[row.key]) for row in counts]


def _counted_hit(count: Any, rows: list[Any], values: dict[str, object]) -> Hit:
    return Hit(
        key=count.key,
        time=count.last,
        count=int(count.n),
        values=values,
        evidence=[_evidence(row) for row in rows],
    )


# -- R03 and R04: admin logins ---------------------------------------------------------------------


def _logins(
    ctx: RuleContext,
    since: datetime,
    *,
    fortigate: ColumnElement[bool],
    mikrotik_action: str,
    trusted: Sequence[str] = (),
) -> CompoundSelect[Any]:
    """Login rows of the FortiGate and the MikroTik devices with ``user_name``, ``ip`` (text,
    NULL when the line names no address) and ``unexpected``: the address is known and outside
    the ``trusted`` networks (always false while the list is empty)."""

    def unexpected(column: Any) -> ColumnElement[Any]:
        if not trusted:
            return false().label("unexpected")
        return and_(column.is_not(None), not_(_in_networks(column, trusted))).label("unexpected")

    return union_all(
        select(
            FgEvent.user_name.label("user_name"),
            func.host(FgEvent.srcip).label("ip"),
            unexpected(FgEvent.srcip),
            *_reference(FgEvent, "fg_event"),
        ).where(_bounded(FgEvent, ctx, since), fortigate),
        select(
            MtAccount.user_name.label("user_name"),
            func.host(MtAccount.ip).label("ip"),
            unexpected(MtAccount.ip),
            *_reference(MtAccount, "mt_account"),
        ).where(_bounded(MtAccount, ctx, since), MtAccount.action == mikrotik_action),
    )


def evaluate_r03(session: Session, ctx: RuleContext) -> list[Hit]:
    failed = or_(
        FgEvent.logid == _texts(_param(ctx, "fortigate_fail_logids")),
        FgEvent.logdesc == _texts(_param(ctx, "fortigate_fail_descs")),
    )
    logins = _logins(ctx, ctx.window_start, fortigate=failed, mikrotik_action="login_failure")
    rows = logins.subquery("logins")
    reference = [rows.c[c] for c in ("tbl", *_REFERENCE)]
    keyed = union_all(
        select(("user:" + rows.c.user_name).label("key"), *reference).where(
            rows.c.user_name.is_not(None), rows.c.user_name != ""
        ),
        select(("ip:" + rows.c.ip).label("key"), *reference).where(rows.c.ip.is_not(None)),
    ).subquery("keyed")
    window = ctx.settings.window_seconds
    hits = []
    for count, evidence in _counted(session, ctx, keyed, int(_param(ctx, "threshold"))):
        kind, _, subject = str(count.key).partition(":")
        values = {"by": kind, "subject": subject, "failures": int(count.n)}
        hits.append(_counted_hit(count, evidence, {**values, "window_seconds": window}))
    return hits


def evaluate_r04(session: Session, ctx: RuleContext) -> list[Hit]:
    succeeded = or_(
        FgEvent.logid == _texts(_param(ctx, "fortigate_ok_logids")),
        FgEvent.logdesc == _texts(_param(ctx, "fortigate_ok_descs")),
    )
    networks = list(_param(ctx, "trusted_admin_networks"))
    logins = _logins(
        ctx, ctx.start, fortigate=succeeded, mikrotik_action="login", trusted=networks
    ).subquery("logins")
    rows = session.execute(select(logins)).all()
    if not rows:
        return []
    usage = UsageSettings.load(session) if _param(ctx, "off_hours") else None
    reported = []
    for row in rows:
        # The login's own moment (device time) decides "mesai dışı"; a break is work time.
        off_hours = usage is not None and usage.is_off_hours(row.time)
        if off_hours or row.unexpected:
            reported.append(_Keyed(f"user:{_user(row.user_name)}", row, off_hours=off_hours))
    hits = []
    for group in _groups(reported):
        values = {
            "user": group.key.removeprefix("user:"),
            "ip": group.rows[-1].ip,
            "logins": len(group.rows),
            "off_hours": any(row.off_hours for row in group.rows),
            "unexpected_address": any(row.unexpected for row in group.rows),
        }
        hits.append(group.hit(ctx, values))
    return hits


class _Keyed:
    """A result row with its rule key (and R04's off-hours flag); the row's columns stay
    readable as attributes."""

    __slots__ = ("key", "off_hours", "row")

    def __init__(self, key: str, row: Any, *, off_hours: bool = False) -> None:
        self.key = key
        self.row = row
        self.off_hours = off_hours

    def __getattr__(self, name: str) -> Any:
        return getattr(self.row, name)


# -- R11: new device ------------------------------------------------------------------------------


#: The tables whose rows the device inventory learns from (``correlation/leases.py``).
_SIGHTINGS: Final = ((MtDhcp, "mt_dhcp"), (FgDhcp, "fg_dhcp"), (MtArp, "mt_arp"))


def evaluate_r11(session: Session, ctx: RuleContext) -> list[Hit]:
    conditions = [
        Device.created_at > ctx.start,
        Device.created_at <= ctx.end,
        Device.infrastructure.is_(False),
    ]
    if _param(ctx, "ignore_random"):
        conditions.append(Device.randomized.is_(False))
    vlans = sorted({int(v) for v in _param(ctx, "ignore_vlans")})
    if vlans:
        conditions.append(
            or_(
                Device.last_vlan.is_(None),
                not_(Device.last_vlan == any_(cast(vlans, ARRAY(Integer)))),
            )
        )
    devices = session.execute(
        select(
            Device.mac,
            Device.first_seen,
            Device.last_ip,
            Device.last_vlan,
            Device.created_at,
        )
        .where(*conditions)
        .order_by(Device.created_at, Device.mac)
    ).all()
    hits = []
    for device in devices:
        mac = normalize_mac(str(device.mac))
        evidence = _first_sighting(session, mac, device.first_seen)
        hits.append(
            Hit(
                key=mac,
                time=device.created_at,
                count=1,
                values={
                    "mac": mac,
                    "ip": None if device.last_ip is None else str(device.last_ip),
                    "vlan": device.last_vlan,
                    "first_seen": None
                    if device.first_seen is None
                    else device.first_seen.isoformat(),
                },
                evidence=evidence[: ctx.evidence_max],
            )
        )
    return hits


def _first_sighting(session: Session, mac: str, first_seen: datetime | None) -> list[EvidenceRef]:
    """The parsed row the inventory took ``first_seen`` from (one exact device-time match per
    table, the partition pruned by it); empty when the row is gone (retention)."""
    if first_seen is None:
        return []
    parts = [
        select(*_reference(table, name)).where(
            table.time == first_seen, table.mac == cast(mac, MACADDR)
        )
        for table, name in _SIGHTINGS
    ]
    rows = union_all(*parts).subquery("sightings")
    found = session.execute(
        select(rows).order_by(rows.c.time_received, rows.c.tbl, rows.c.id).limit(1)
    ).all()
    return [_evidence(row) for row in found]


# -- R12: MAC change ------------------------------------------------------------------------------


def evaluate_r12(session: Session, ctx: RuleContext) -> list[Hit]:
    lookup = LookupSettings.from_settings(
        get_settings(session, prefix="dhcp.") | get_settings(session, prefix="devices.")
    )
    assigned = sorted(k for k, meaning in lookup.fortigate_logids.items() if meaning == "assigned")
    parts = [
        select(
            func.host(MtArp.ip).label("ip"), MtArp.mac.label("mac"), *_reference(MtArp, "mt_arp")
        ).where(_bounded(MtArp, ctx, ctx.window_start)),
        select(
            func.host(MtDhcp.ip).label("ip"),
            MtDhcp.mac.label("mac"),
            *_reference(MtDhcp, "mt_dhcp"),
        ).where(_bounded(MtDhcp, ctx, ctx.window_start), MtDhcp.action == "assigned"),
    ]
    if assigned:
        parts.append(
            select(
                func.host(FgDhcp.ip).label("ip"),
                FgDhcp.mac.label("mac"),
                *_reference(FgDhcp, "fg_dhcp"),
            ).where(
                _bounded(FgDhcp, ctx, ctx.window_start),
                FgDhcp.device_time.is_(True),
                FgDhcp.logid == _texts(assigned),
                _fortigate_message(lookup, assigned),
                or_(
                    FgDhcp.interface.is_(None),
                    not_(FgDhcp.interface == _texts(lookup.fortigate_interfaces_excluded)),
                ),
            )
        )
    sightings = union_all(*parts).subquery("sightings")
    infrastructure = select(Device.mac).where(Device.infrastructure.is_(True))
    kept_rows = (
        select(sightings)
        .where(
            not_(sightings.c.mac == any_(cast(list(lookup.infrastructure_macs), ARRAY(MACADDR)))),
            sightings.c.mac.not_in(infrastructure),
        )
        .subquery("kept")
    )
    previous = func.lag(kept_rows.c.mac).over(
        partition_by=kept_rows.c.ip,
        order_by=(kept_rows.c.time_received, kept_rows.c.tbl, kept_rows.c.id),
    )
    ordered = select(kept_rows, previous.label("previous")).subquery("ordered")
    changes = (
        select(
            ordered.c.ip.label("key"),
            *(ordered.c[c] for c in ("tbl", *_REFERENCE)),
            ordered.c.mac,
            ordered.c.previous,
        )
        .where(ordered.c.previous.is_not(None), ordered.c.mac != ordered.c.previous)
        .subquery("changes")
    )
    window = ctx.settings.window_seconds
    hits = []
    for count, evidence in _counted(session, ctx, changes, int(_param(ctx, "threshold"))):
        macs = _macs_of(evidence)
        values = {
            "ip": count.key,
            "changes": int(count.n),
            "macs": macs[:MACS_SHOWN],
            "window_seconds": window,
        }
        hits.append(_counted_hit(count, evidence, values))
    return hits


def _fortigate_message(lookup: LookupSettings, assigned: Sequence[str]) -> ColumnElement[bool]:
    """The DHCP message an assigning logid must carry (``dhcp.fortigate_dhcp_msgs``; a logid
    without an entry takes any message), compared without case like the lease jobs."""
    allowed = []
    for logid in assigned:
        messages = lookup.fortigate_dhcp_msgs.get(logid)
        if messages is None:
            allowed.append(FgDhcp.logid == logid)
        else:
            allowed.append(
                and_(
                    FgDhcp.logid == logid,
                    func.lower(FgDhcp.dhcp_msg) == _texts(m.lower() for m in messages),
                )
            )
    return or_(*allowed)


def _macs_of(evidence: Sequence[Any]) -> list[str]:
    """The MACs of the changes, in order of first appearance (the one before the first change
    first)."""
    macs: list[str] = []
    for row in evidence:
        for mac in (row.previous, row.mac):
            text = normalize_mac(str(mac))
            if text not in macs:
                macs.append(text)
    return macs


# -- R16: configuration change --------------------------------------------------------------------


def evaluate_r16(session: Session, ctx: RuleContext) -> list[Hit]:
    changes = union_all(
        select(
            FgEvent.source.label("device"),
            FgEvent.user_name.label("user_name"),
            *_reference(FgEvent, "fg_event"),
        ).where(
            _bounded(FgEvent, ctx, ctx.start),
            FgEvent.logid == _texts(_param(ctx, "fortigate_logids")),
        ),
        select(
            MtConfig.source.label("device"),
            MtConfig.user_name.label("user_name"),
            *_reference(MtConfig, "mt_config"),
        ).where(_bounded(MtConfig, ctx, ctx.start)),
    ).subquery("changes")
    rows = session.execute(select(changes)).all()
    keyed = [_Keyed(f"{row.device}:{_user(row.user_name)}", row) for row in rows]
    hits = []
    for group in _groups(keyed):
        latest = group.rows[-1]
        values = {
            "device": latest.device,
            "user": _user(latest.user_name),
            "changes": len(group.rows),
        }
        hits.append(group.hit(ctx, values))
    return hits


# -- the rule definitions -------------------------------------------------------------------------


def _strings(key: str, default: list[str], tr: str, en: str) -> Param:
    return Param(key, "strings", default, 0, 200, tr, en)


ACCESS_RULES: Final[tuple[RuleDef, ...]] = (
    RuleDef(
        id="R03",
        kind="event",
        severity="high",
        window_seconds=300,
        cooldown_seconds=3600,
        params=(
            Param("threshold", "int", 5, 1, 10000, "Başarısız giriş sayısı", "Failed logins"),
            _strings(
                "fortigate_fail_logids",
                ["0100032002"],
                "FortiGate başarısız giriş logid'leri",
                "FortiGate failed-login logids",
            ),
            _strings(
                "fortigate_fail_descs",
                ["Admin login failed"],
                "FortiGate başarısız giriş açıklamaları (logdesc)",
                "FortiGate failed-login descriptions (logdesc)",
            ),
        ),
        inputs=("fg_event", "mt_account"),
        format_verified=False,
        evaluate=evaluate_r03,
    ),
    RuleDef(
        id="R04",
        kind="event",
        severity="medium",
        window_seconds=60,
        cooldown_seconds=0,
        params=(
            Param(
                "off_hours",
                "bool",
                True,
                label_tr="Mesai dışı girişleri bildir",
                label_en="Report logins outside work hours",
            ),
            Param(
                "trusted_admin_networks",
                "networks",
                [],
                0,
                200,
                "Yönetici girişine izin verilen ağlar (boş: bu kısım kapalı)",
                "Networks admins may log in from (empty: this part is off)",
            ),
            _strings(
                "fortigate_ok_logids",
                ["0100032001"],
                "FortiGate başarılı giriş logid'leri",
                "FortiGate successful-login logids",
            ),
            _strings(
                "fortigate_ok_descs",
                ["Admin login successful"],
                "FortiGate başarılı giriş açıklamaları (logdesc)",
                "FortiGate successful-login descriptions (logdesc)",
            ),
        ),
        inputs=("fg_event", "mt_account"),
        format_verified=False,
        evaluate=evaluate_r04,
    ),
    RuleDef(
        id="R11",
        kind="event",
        severity="low",
        window_seconds=60,
        cooldown_seconds=0,
        params=(
            Param(
                "ignore_random",
                "bool",
                True,
                label_tr="Rastgele (özel) MAC adreslerini yok say",
                label_en="Ignore random (private) MAC addresses",
            ),
            Param(
                "ignore_vlans",
                "ints",
                [],
                0,
                200,
                "Yok sayılan VLAN'lar",
                "VLANs to ignore",
            ),
        ),
        inputs=("devices",),
        evaluate=evaluate_r11,
    ),
    RuleDef(
        id="R12",
        kind="event",
        severity="medium",
        window_seconds=3600,
        cooldown_seconds=3600,
        params=(Param("threshold", "int", 3, 1, 1000, "MAC değişimi sayısı", "MAC changes"),),
        inputs=("mt_arp", "mt_dhcp", "fg_dhcp"),
        evaluate=evaluate_r12,
    ),
    RuleDef(
        id="R16",
        kind="event",
        severity="medium",
        window_seconds=60,
        cooldown_seconds=600,
        params=(
            _strings(
                "fortigate_logids",
                ["0100044546", "0100044547"],
                "FortiGate yapılandırma değişikliği logid'leri",
                "FortiGate configuration-change logids",
            ),
        ),
        inputs=("fg_event", "mt_config"),
        format_verified=False,
        evaluate=evaluate_r16,
    ),
)
