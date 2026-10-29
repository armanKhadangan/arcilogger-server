"""Test helpers for the 5651 lookup, report and raw-line endpoints (and later packages: the
evidence API, end-to-end tests, demo data).

* :func:`build_site` — two clients and FortiGate sessions around 12.10.2026 11:23:41 (fictional
  values; the example attribution chain WAN 100.64.12.9:51001 → 172.20.42.57 → "Planlama-PC-14"
  → Selin Kaya / Planlama), written as real spool lines **and** database rows by
  :class:`arcilogger_worker.testing.Evidence`, the lease timeline rebuilt by the worker job, one
  device linked to a person; with ``dhcp="fortigate"`` the leases come from FortiGate DHCP ACKs
  instead of MikroTik DHCP, with ``srcmac=True`` every session carries its client's interface and
  MAC (device identification on), with ``wan2=True`` the phone has one more session through the
  second exit (contract 2.12);
* :func:`unconverted_dhcp_line`, :func:`device_identified_traffic`,
  :func:`unknown_sender_traffic` — single evidence lines of the Wave 3 scenarios (contract 3.5): a
  FortiGate DHCP line that stayed an ``fg_event`` row, a session line with the device
  identification fields, a session from a sender that is no configured source;
* :func:`enter_exits` — the internet exits (``nat.egresses``) as entered at a given moment, with
  their change record;
* :func:`accept_privacy` — the KVKK notice accepted for a user (person-level endpoints refuse
  without it);
* :func:`seal_day` — seals a day with the core sealer and the mock time-stamp authority (never
  legally valid), so tests can read raw lines from the vault.

**Test support only**: the API service never imports it (it writes spool files and seals with
the mock TSA). It lives in the package, like :mod:`arcilogger_api.testing`, so that the tests of
other packages can build the same site; leaving the test-support modules out of the production
wheel is a build setting of ``api/pyproject.toml``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth.accounts import accept_privacy as _accept
from arcilogger_api.auth.accounts import privacy_version
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import day_window, to_site
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_db.egress import CHANGES_KEY, fingerprint, validate_egresses
from arcilogger_db.models import Department, Device, FgEvent, FgTraffic, Person, Setting, Source
from arcilogger_db.settings import SYSTEM_AUTHOR, get_setting, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.testing import FIREWALL, FORTIGATE_DHCP_TEXTS, ROUTER, Evidence, tr

DAY = date(2026, 10, 12)
#: Moment of the requests (the PC's session: srcport 51001 → transport 51001).
AT = tr(DAY, 11, 23, 41)
NOW = tr(DAY + timedelta(days=1), 9)
WAN = "100.64.12.9"
#: The example chain's PC (VLAN 42 "Planlama", lease of dhcp42 at 10:42:18) and a phone on
#: the staff Wi-Fi.
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
PC_HOSTNAME = "PLN-PC14"
PHONE, PHONE_MAC = "172.20.60.142", "02:00:5e:00:53:64"
PHONE_HOSTNAME = "Telefon-12"
PERSON_NAME = "Selin Kaya"
DEPARTMENT = "Planlama"
DEVICE_NAME = "Planlama-PC-14"
#: The FortiGate's NAT address behind the second ISP router (``dstintf`` ``wan2``).
WAN2 = "100.64.13.9"
#: The FortiGate interfaces of the two clients' networks (``srcintf`` of their sessions, the
#: ``interface`` of the FortiGate DHCP server's records).
PC_INTERFACE, PHONE_INTERFACE = "vlan42", "vlan60"
#: The lease time of the FortiGate DHCP server (``dhcp="fortigate"``); clients renew after half.
FORTIGATE_LEASE = 86400
#: The address of a sender that is no configured source (:func:`unknown_sender_traffic`).
UNKNOWN_SENDER = "192.0.2.7"
#: Device identification fields (contract F19) as :func:`device_identified_traffic` writes them
#: by default; fictional values.
DEVICE_IDENTIFICATION: Mapping[str, str] = {
    "srcname": PC_HOSTNAME,
    "osname": "Windows",
    "unauthuser": "ornek.kullanici",
}


@dataclass(frozen=True)
class Site:
    person_id: int
    #: Traffic row ids: s1 = PC → 203.0.113.10:443 at AT (10 s), s2 = phone's long session
    #: (two interim rows and the final row), s3 = PC → 203.0.113.40:443 at 11:24:36.
    s1: int
    s2: tuple[int, ...]
    s3: int
    #: The clients' first DHCP rows: ``mt_dhcp`` ids, with ``dhcp="fortigate"`` the ``fg_dhcp``
    #: ids of their first ACKs (the PC at 10:42:18, the phone at 09:37:52).
    dhcp_pc: int
    dhcp_phone: int
    #: ``wan2=True``: the phone → 203.0.113.10:443 at 11:23:50 through :data:`WAN2`.
    wan2: int | None = None


def build_site(
    factory: sessionmaker[Session],
    layout: Layout,
    day: date = DAY,
    *,
    dhcp: Literal["mikrotik", "fortigate"] = "mikrotik",
    srcmac: bool = False,
    wan2: bool = False,
) -> Site:
    """Write the sample network day (spool + rows, the two sources), rebuild its leases, link
    the PC to a person.

    ``dhcp="fortigate"``: the leases rest on FortiGate DHCP ACKs (``fg_dhcp``, lease
    :data:`FORTIGATE_LEASE`) instead of MikroTik DHCP rows: the PC at 10:42:18 and 22:42:18 on
    vlan42, the phone at 09:37:52 and 21:37:52 on vlan60 (the renewal every 12 h keeps the chain
    covered without a traffic extension, D26); no ``mt_dhcp`` row is written, the two
    ``sources`` rows are. ``srcmac=True``: every session row carries its client's interface and
    MAC (the PC vlan42 and :data:`PC_MAC`, the phone vlan60 and :data:`PHONE_MAC`).
    ``wan2=True``: one more phone session through :data:`WAN2` (``dstintf="wan2"``), written
    after the other lines (:attr:`Site.wan2`). The defaults write the same lines and rows as
    before these options existed."""
    at = tr(day, 11, 23, 41)
    pc_side: dict[str, Any] = {"srcintf": PC_INTERFACE, "srcmac": PC_MAC} if srcmac else {}
    phone_side: dict[str, Any] = {"srcintf": PHONE_INTERFACE, "srcmac": PHONE_MAC} if srcmac else {}
    with factory() as session:
        # The ingest service keeps ``sources`` up to date; the raw-line view checks names there.
        session.merge(Source(name="fortigate", type="fortigate", address=FIREWALL))
        session.merge(Source(name="core-r1", type="mikrotik", address=ROUTER))
        evidence = Evidence(session, layout)
        department = session.scalars(select(Department).where(Department.name == DEPARTMENT)).one()
        person = Person(name=PERSON_NAME, department_id=department.id, title="Planlama Uzmanı")
        session.add(person)
        session.flush()
        if dhcp == "fortigate":
            dhcp_pc = _acks(evidence, tr(day, 10, 42, 18), PC, PC_MAC, PC_INTERFACE, PC_HOSTNAME)
            dhcp_phone = _acks(
                evidence, tr(day, 9, 37, 52), PHONE, PHONE_MAC, PHONE_INTERFACE, PHONE_HOSTNAME
            )
        else:
            dhcp_pc = evidence.dhcp(
                tr(day, 10, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname=PC_HOSTNAME
            )
            dhcp_phone = evidence.dhcp(
                tr(day, 9, 37, 52),
                "assigned",
                PHONE,
                PHONE_MAC,
                server="dhcp60",
                hostname=PHONE_HOSTNAME,
            )
        s1 = evidence.traffic(at, PC, 51001, "203.0.113.10", 443, duration=10, **pc_side)
        google = "203.0.113.20"
        s2 = (
            evidence.traffic(
                tr(day, 11, 20, 56), PHONE, 51002, google, duration=100, interim=True, **phone_side
            ),
            evidence.traffic(
                tr(day, 11, 22, 56), PHONE, 51002, google, duration=220, interim=True, **phone_side
            ),
            evidence.traffic(tr(day, 11, 25, 26), PHONE, 51002, google, duration=370, **phone_side),
        )
        s3 = evidence.traffic(
            tr(day, 11, 24, 36), PC, 51005, "203.0.113.40", 443, duration=5, **pc_side
        )
        evidence.traffic(tr(day, 11, 34, 56), PC, 62099, "203.0.113.40", 443, duration=5, **pc_side)
        evidence.web(tr(day, 11, 24, 32), PC, 51005, "static.portal.example", "203.0.113.40")
        evidence.dns(tr(day, 11, 14, 56), PHONE, "www.google.com", [google])
        second = None
        if wan2:
            second = evidence.traffic(
                tr(day, 11, 23, 50),
                PHONE,
                51007,
                "203.0.113.10",
                443,
                transip=WAN2,
                dstintf="wan2",
                duration=10,
                **phone_side,
            )
        session.commit()
        rebuild_day(session, day, horizon=tr(day + timedelta(days=1), 9))
        device = session.get_one(Device, PC_MAC)
        device.person_id = person.id
        device.name = DEVICE_NAME
        device.type = "pc"
        session.commit()
        return Site(
            person_id=person.id,
            s1=s1,
            s2=s2,
            s3=s3,
            dhcp_pc=dhcp_pc,
            dhcp_phone=dhcp_phone,
            wan2=second,
        )


def _acks(
    evidence: Evidence, first: datetime, ip: str, mac: str, interface: str, hostname: str
) -> int:
    """FortiGate DHCP ACKs of a client at ``first`` and at its renewal 12 h later; the first
    ACK's row id."""
    ack: dict[str, Any] = {"interface": interface, "hostname": hostname, "lease": FORTIGATE_LEASE}
    row = evidence.fortigate_dhcp(first, ip, mac, **ack)
    evidence.fortigate_dhcp(first + timedelta(seconds=FORTIGATE_LEASE // 2), ip, mac, **ack)
    return row


def unconverted_dhcp_line(
    evidence: Evidence,
    time: datetime,
    ip: str,
    mac: str,
    *,
    hostname: str | None = None,
    lease: int | None = FORTIGATE_LEASE,
    interface: str = PC_INTERFACE,
    source: str = "fortigate",
    logid: str = "0100026001",
    dhcp_msg: str | None = None,
) -> int:
    """A FortiGate DHCP ACK (a RELEASE with ``logid="0100026002"`` and ``lease=None``) that
    stayed an ``fg_event`` row with the full DHCP shape (D8: a line loaded before the upgrade,
    or by the previous version after a rollback): its spool line and the row that version
    stored, every DHCP field in ``extra``; returns the row id. A lookup that builds no chain for
    ``ip`` while the line covers the moment cites the row in ``outcome.unconverted_dhcp``
    (2.8.3). ``dhcp_msg``: another message than the logid's own ("Ack" / "Release");
    ``source``: the spool folder (``unknown-<ip>`` sends from that address). The shape of the
    worker's FortiGate-only goldens (``unconverted_line``), which the API cannot import."""
    sender = source.removeprefix("unknown-") if source.startswith("unknown-") else FIREWALL
    local = to_site(time)
    logdesc, msg = FORTIGATE_DHCP_TEXTS[logid]
    fields = {
        "interface": interface,
        "dhcp_msg": dhcp_msg or ("Ack" if logid == "0100026001" else "Release"),
        "mac": mac.upper(),
        "ip": ip,
        "lease": None if lease is None else str(lease),
        "hostname": hostname,
    }
    tail = "".join(
        f" {key}={value}" if key in ("ip", "lease") else f' {key}="{value}"'
        for key, value in fields.items()
        if value is not None
    )
    message = (
        f'<190>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="FIREWALL-01" '
        f'devid="FGT0000000000000" eventtime={int(time.timestamp() * 1e9)} tz="+0300" '
        f'logid="{logid}" type="event" subtype="system" level="information" vd="root" '
        f'logdesc="{logdesc}"{tail} msg="{msg}"'
    )
    common = evidence._write(source, sender, time, message)
    extra = {key: value for key, value in fields.items() if value is not None}
    return evidence._add(
        FgEvent(
            **common,
            logid=logid,
            subtype="system",
            level="information",
            logdesc=logdesc,
            msg=msg,
            extra={"devid": "FGT0000000000000", "vd": "root", **extra},
        )
    )


def device_identified_traffic(
    evidence: Evidence,
    time: datetime,
    srcip: str,
    srcport: int,
    dstip: str = "203.0.113.10",
    dstport: int = 443,
    *,
    srcintf: str = PC_INTERFACE,
    srcmac: str = PC_MAC,
    identification: Mapping[str, str] = DEVICE_IDENTIFICATION,
    transip: str | None = WAN,
    dstintf: str | None = None,
) -> int:
    """A session record of an interface with FortiGate device identification on: the line
    carries ``srcmac`` as given and the device identification fields ``identification`` (F19,
    e.g. ``srcname``, ``unauthuser``) after it, the ``fg_traffic`` row the MAC normalised and
    those fields in ``extra`` (as the parser keeps fields without a column); returns the row id.
    A report prints such a line as it is, whatever ``lookup.srcmac_interfaces`` says (D21)."""
    return _session(
        evidence,
        time,
        srcip,
        srcport,
        dstip,
        dstport,
        source="fortigate",
        sender=FIREWALL,
        transip=transip,
        srcintf=srcintf,
        dstintf=dstintf,
        srcmac=srcmac,
        identification=identification,
    )


def unknown_sender_traffic(
    evidence: Evidence,
    time: datetime,
    srcip: str,
    srcport: int,
    dstip: str = "203.0.113.10",
    dstport: int = 443,
    *,
    sender: str = UNKNOWN_SENDER,
    transip: str | None = WAN,
    dstintf: str | None = None,
) -> int:
    """A session record from ``sender``, an address that is no configured source: the line in
    the collector's folder ``unknown-<sender>``, its ``fg_traffic`` row and the ``sources`` row
    ingest files for such a sender (``unknown``, type ``fortigate``); returns the row id. The
    lookup keeps the session and names the sender on its match (``untrusted_sources``, D24)."""
    name = f"unknown-{sender}"
    if evidence.session.get(Source, name) is None:
        evidence.session.add(Source(name=name, type="fortigate", address=sender, unknown=True))
    return _session(
        evidence,
        time,
        srcip,
        srcport,
        dstip,
        dstport,
        source=name,
        sender=sender,
        transip=transip,
        srcintf=None,
        dstintf=dstintf,
        srcmac=None,
        identification={},
    )


def _session(
    evidence: Evidence,
    time: datetime,
    srcip: str,
    srcport: int,
    dstip: str,
    dstport: int,
    *,
    source: str,
    sender: str,
    transip: str | None,
    srcintf: str | None,
    dstintf: str | None,
    srcmac: str | None,
    identification: Mapping[str, str],
) -> int:
    """A final 10-s session record of ``sender`` in the spool folder ``source``, in the line
    format of ``Evidence.traffic`` (``srcmac`` and the ``identification`` fields at its end),
    and its ``fg_traffic`` row."""
    local = to_site(time)
    nat = f' trandisp="snat" transip={transip} transport={srcport}' if transip else ""
    source_side = f' srcintf="{srcintf}"' if srcintf is not None else ""
    destination_side = f' dstintf="{dstintf}"' if dstintf is not None else ""
    device = f' srcmac="{srcmac}"' if srcmac is not None else ""
    device += "".join(f' {key}="{value}"' for key, value in identification.items())
    message = (
        f'<189>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="FIREWALL-01" '
        f'devid="FGT0000000000000" eventtime={int(time.timestamp() * 1e9)} tz="+0300" '
        f'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" '
        f"srcip={srcip} srcport={srcport}{source_side} dstip={dstip} dstport={dstport}"
        f"{destination_side} "
        f'sessionid={srcport} proto=6 action="accept"{nat} duration=10{device}'
    )
    common = evidence._write(source, sender, time, message)
    return evidence._add(
        FgTraffic(
            **common,
            logid="0000000013",
            subtype="forward",
            action="accept",
            sessionid=srcport,
            proto=6,
            srcip=srcip,
            srcport=srcport,
            srcintf=srcintf,
            dstip=dstip,
            dstport=dstport,
            dstintf=dstintf,
            transip=transip,
            transport=srcport if transip else None,
            trandisp="snat" if transip else None,
            duration=10,
            is_interim=False,
            srcmac=None if not srcmac else srcmac.lower().replace("-", ":"),
            extra=dict(identification),
        )
    )


def enter_exits(
    factory: sessionmaker[Session],
    value: Mapping[str, Any],
    *,
    at: datetime,
    by: str,
    changes: bool = True,
) -> dict[str, dict[str, Any]]:
    """Store ``value`` as ``nat.egresses`` (normalised, as the settings write it), entered by
    ``by`` at ``at`` (the row's ``updated_at``); returns the stored value.

    ``changes``: also the change record ``nat.egresses_changes`` the settings keep beside it
    (D32), as ``settings_admin.change_setting`` updates it: an exit that is new or whose content
    differs from its entry gets ``{fingerprint, changed_at: at, changed_by: by, deleted:
    false}``, an exit no longer listed is marked deleted, the other entries stay; so a second
    call at a later ``at`` is a change of the exits after the event (E-k4). ``changes=False``
    writes the value alone, without a change record."""
    normalised = validate_egresses(dict(value))
    with factory() as session:
        set_setting(session, "nat.egresses", normalised, updated_by=by)
        written = ["nat.egresses"]
        if changes:
            stored = get_setting(session, CHANGES_KEY)
            record = dict(stored) if isinstance(stored, dict) else {}
            entered = at.astimezone(UTC).isoformat()
            changed = False
            for name, exit_ in normalised.items():
                content = fingerprint(exit_)
                known = record.get(name)
                if known is None or known.get("deleted") or known.get("fingerprint") != content:
                    record[name] = _change(content, entered, by, deleted=False)
                    changed = True
            for name in sorted(record.keys() - normalised.keys()):
                if not record[name].get("deleted"):
                    record[name] = _change(record[name]["fingerprint"], entered, by, deleted=True)
                    changed = True
            if changed:
                set_setting(session, CHANGES_KEY, record, updated_by=SYSTEM_AUTHOR)
                written.append(CHANGES_KEY)
        session.execute(update(Setting).where(Setting.key.in_(written)).values(updated_at=at))
        session.commit()
    return normalised


def _change(content: str, at: str, by: str, *, deleted: bool) -> dict[str, Any]:
    """One exit's entry of ``nat.egresses_changes``."""
    return {"fingerprint": content, "changed_at": at, "changed_by": by, "deleted": deleted}


def accept_privacy(factory: sessionmaker[Session], user_id: int) -> None:
    """The user accepted the current KVKK notice version."""
    with factory() as session:
        _accept(session, user_id, privacy_version(session), ip=None)
        session.commit()


def mock_tsa(layout: Layout) -> MockTsa:
    """The mock TSA of ``layout`` (EC keys: created fast; the API's provider reuses them)."""
    return MockTsa(layout.state / "mock-tsa", key_type="ec")


def seal_day(config: Config, layout: Layout, day: date) -> None:
    """Seal ``day`` with the mock TSA (as the nightly task would, one hour after midnight)."""
    sealing = Config.model_validate(
        {
            **config.model_dump(),
            "seal": {"quiet_seconds": 0, "first_day": day.isoformat()},
            "tsa": {"provider": "mock"},
        }
    )
    tsa = mock_tsa(layout)
    sealer = Sealer(
        sealing,
        provider=MockTsaProvider(tsa),
        roots=[tsa.root_certificate],
        clock=lambda: day_window(day)[1] + timedelta(hours=1),
        clock_status=lambda: None,
    )
    outcome = sealer.seal_day(day)
    if outcome.status != "sealed":
        raise RuntimeError(f"sealing {day} failed: {outcome.message}")


def iso(moment: datetime) -> str:
    """ISO-8601 with offset, as a client sends it."""
    return moment.isoformat()
