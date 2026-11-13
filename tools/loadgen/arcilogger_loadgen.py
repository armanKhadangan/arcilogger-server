"""ARCILOGGER load generator: realistic syslog of a simulated company network (spec 17).

**Synthetic — verify on site.** Every line is generated; the formats follow the format samples
(``tests/fixtures/format/``, modelled on FortiOS 7.6 / RouterOS 7 output) and, where there is
no sample yet, the synthetic fixtures (``tests/fixtures/synthetic/``): the MikroTik wire format,
FortiGate web filter / DNS logids (placeholders ``99000000xx``), FortiGuard categories
(placeholders ``9xx`` "Sentetik: …"), the interim-log interval and the domain names
(``*.example``, RFC 2606) are **not** real values. Use it for load tests, demo data and tests —
never as evidence of anything.

What it simulates, deterministically from ``--seed`` (the same seed and options always give
the same lines, byte for byte):

* ``--users`` people (default 120) in the three department VLANs of the fictional example
  network (41 Finans, 42 Planlama, 43 İdari İşler), each with a wired PC (DHCP in the department
  VLAN) and a phone on the staff Wi-Fi (VLAN 60, everyone's) with a **randomised MAC** (locally
  administered; some rotate it every day), a few guests on the guest Wi-Fi (dhcp61, 1 h leases;
  anonymous devices that browse through the firewall like everyone else), static network devices
  in VLAN 10 (access points, printers, a switch, the admin PC 172.20.10.37) and servers in VLAN
  20 (the users' DNS server is one of them, 172.20.20.53);
* work-hour patterns (arrival 07:45-09:30, lunch 12:15-13:45, leaving 17:15-19:30, few people at
  weekends), DHCP ``assigned``/``deassigned`` churn from the lease times (1 day, dhcp61 1 h;
  RouterOS logs no renewals, spec 9.1), hourly ``ARP-LOG`` lines for every active device,
  ``ARCI-HEARTBEAT`` every 5 minutes from every MikroTik, link up/down of the PCs on the
  switch's 48 ports (the other PCs are on switches that send no syslog);
* FortiGate: DNS query/response, web filter (passthrough or **blocked**), application control
  and traffic logs joined by ``sessionid``; long sessions get interim logs (``0000000020``) with
  ``sentdelta``/``rcvddelta``/``durationdelta`` and a final log (``0000000013``); every user
  session is source-NATed to ``--transip`` (default ``100.64.12.9``, the example network's
  firewall WAN address behind the ISP router) with ``transport`` = the source port unless another
  session still holds that port (port preservation is an assumption of the simulation: verify
  on site; ports are given out in time order over all devices once a day is generated),
  performance statistics every 5 minutes.

Wave 3 site plans (ADR-0016; every new option defaults to the behaviour above, byte for byte):

* ``--dhcp-server fortigate``: a FortiGate-only site. The FortiGate is every VLAN's gateway and
  DHCP server: FortiGate DHCP Ack (26001) lines in the shape of the synthetic fixture on
  assignment and on renewal at half the lease (a simulation assumption, sahada doğrula: F7), a
  26002 line only when a guest leaves (every other lease expires unlogged, a simulation choice),
  ``srcintf="vlan<VLAN>"`` on client traffic, and no ``core-r1``/``switch-1`` line or ARP-LOG.
  ``both``: MikroTik for VLAN 41-43, the FortiGate for the Wi-Fi VLANs 60/61.
* ``--srcmac``: traffic lines carry ``srcmac`` (lower case): the client's MAC behind the
  FortiGate, the core router's MAC for every client behind the router (the shared case).
* ``--egress NAME:TRANSIP:DSTINTF:SHARE`` (repeatable, shares sum to 100): internet exits (dual
  WAN). Each session leaves by one exit (``dstintf``, source NAT to its ``transip``), drawn per
  session; ``waninfo`` of the performance statistics lists the exits' interfaces.

Outputs: ``spool`` writes the raw spool files of the days (``spool/<source>/<day>.log``, each
line through :func:`arcilogger_core.rawlog.format_line`, exactly as the collector writes them);
``send`` sends the messages over UDP and/or TCP (octet counting, RFC 6587) at ``--rate``;
``truth`` prints the ground truth (sessions with their device, person and exit, DHCP leases with
the table that holds their evidence) as JSON lines (UTF-8, also on a Windows console); ``stats``
counts lines per source and kind. Python API: :class:`Settings`, :class:`Simulation`,
:func:`write_spool`, :func:`send_messages`.

**Never on the production server.** ``spool`` refuses a data folder that is in use (sealed
days, a ``state`` folder, spool files it did not write) unless ``--i-know-this-is-not-production``
is given, and leaves the marker file ``LOADGEN-DEMO.txt`` in every data folder it writes to.
Every simulated DHCP pool has 160 addresses (``.64`` to ``.223``), and an address stays held
until its lease ends: about 125 users fill the staff Wi-Fi (everyone's phone is on it), about 140
when the FortiGate serves it; more stop with a clear error.

Run from the repository root: ``uv run python tools/loadgen/arcilogger_loadgen.py --help``.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import random
import socket
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, Final, Literal

from arcilogger_core.chain import sealed_days
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import SITE_TZ, site_day

#: Shown in --help and in every truth record: nothing here is a real capture.
SYNTHETIC_NOTE: Final = "synthetic, verify on site"
#: The example network's firewall WAN address (source NAT; the ISP router in front of it).
DEFAULT_TRANSIP: Final = "100.64.12.9"
#: The example network's DNS server (a server in VLAN 20, not the firewall). A simplification of
#: the simulation (verify on site): the firewall's DNS filter logs every user query to it.
DNS_SERVER: Final = "172.20.20.53"
DEVNAME: Final = "FIREWALL-01"
DEVID: Final = "FGT0000000000000"  # placeholder serial, as in the format samples
#: The simulated firewall's internet policy (id and UUID as in the format samples).
POLICYID: Final = 12
POLUUID: Final = "00000000-0000-0000-0000-000000000012"
#: The FortiGate's interim-log interval is a device setting: not captured yet (verify on site).
DEFAULT_INTERIM_SECONDS: Final = 120
LEASE_SECONDS: Final = 86400  # settings lease.default_seconds
#: Settings ``lease.server_seconds``: 1 h on the guest Wi-Fi, as in the tests' example network.
LEASE_SECONDS_BY_SERVER: Final = {"dhcp61": 3600}
HEARTBEAT_MINUTES: Final = 5
EPHEMERAL: Final = (49152, 65535)
#: The simulated DHCP pools are ``.64`` to ``.223`` in every VLAN.
POOL_FIRST, POOL_SIZE = 64, 160
#: About how many users fill the staff Wi-Fi pool, by who serves it (14 simulated days, by seed:
#: 126-134 users with MikroTik DHCP, whose leases run 24 h past the last activity; 144-149 with
#: FortiGate DHCP). An address stays held until its lease ends with either server (#89).
WIFI_USERS: Final = {"mikrotik": 125, "fortigate": 140}
#: A NAT port stays taken this long after its session ended.
NAT_GRACE: Final = timedelta(seconds=120)
#: Left in every data folder ``spool`` writes to: these lines are synthetic, never evidence.
DEMO_MARKER: Final = "LOADGEN-DEMO.txt"
DEMO_TEXT: Final = (
    "ARCILOGGER yük üreteci (tools/loadgen) bu klasöre SENTETİK kayıt satırları yazdı. Bunlar "
    "delil değildir; bu klasörü üretim sunucusunda kullanmayın.\n"
    "The ARCILOGGER load generator (tools/loadgen) wrote SYNTHETIC log lines into this folder "
    f"({SYNTHETIC_NOTE}). They are not evidence; never use this folder on the production "
    "server.\n"
)


#: Attack scenarios (Wave 6 contract §2.7): name → the security rule its lines make fire. Every
#: line is synthetic, from the example world, in the formats of ``tests/fixtures/``; ``all`` is
#: every scenario (the rule engine's performance guard and the demo use it).
SCENARIOS: Final[dict[str, str]] = {
    "port-scan": "R01",
    "external-scan": "R02",
    "brute-force": "R03",
    "off-hours-admin": "R04",
    "ips": "R05",
    "botnet": "R06",
    "malware": "R07",
    "dns-bypass": "R08",
    "blocked-category": "R09",
    "tor": "R10",
    "new-device": "R11",
    "mac-flap": "R12",
    "big-upload": "R13",
    "night": "R14",
    "config-change": "R16",
    "fw-health": "R17",
    "pool-empty": "R18",
    "dga": "R19",
}
#: The example world's actors of the scenarios: addresses outside every simulated DHCP pool
#: (``.64``-``.223``), so no simulated device holds them.
SCAN_SOURCE: Final = "172.20.41.196"
SCAN_TARGET: Final = "172.20.20.10"
EXTERNAL_SCANNER: Final = "203.0.113.77"
BOTNET_HOST: Final = "172.20.41.231"
MALWARE_HOST: Final = "172.20.43.232"
#: The user PC of the person-level and DNS scenarios (R08, R09, R10, R13, R14, R19).
SCENARIO_PC: Final = "172.20.42.230"
OUTSIDE_DNS: Final = "198.51.100.53"
NEW_DEVICE_IP: Final = "172.20.43.240"
FLAP_IP: Final = "172.20.43.250"
FLAP_MACS: Final = ("00:00:5e:00:53:e1", "00:00:5e:00:53:e2")
#: R17: the performance-statistics slots (5 minutes each) with a busy CPU, 16:30-16:55.
HOT_SLOTS: Final = range(198, 204)


def parse_scenarios(value: str) -> tuple[str, ...]:
    """``name[,name…]`` or ``all`` → scenario names in :data:`SCENARIOS` order."""
    names = {part.strip() for part in value.split(",") if part.strip()}
    if "all" in names:
        return tuple(SCENARIOS)
    unknown = sorted(names - set(SCENARIOS))
    if unknown or not names:
        given = ", ".join(unknown) if unknown else repr(value)
        raise argparse.ArgumentTypeError(
            f"bilinmeyen senaryo / unknown scenario: {given}; "
            f"seçenekler / choices: {', '.join(SCENARIOS)}, all"
        )
    return tuple(name for name in SCENARIOS if name in names)


class LoadgenError(RuntimeError):
    """A request the generator cannot (or must not) carry out; the CLI shows it and exits 2."""


SourceType = Literal["fortigate", "mikrotik"]


@dataclass(frozen=True)
class Source:
    """A device that sends syslog: ``name`` as in ``arcilogger.toml``, ``address`` its IP."""

    name: str
    type: SourceType
    address: str
    identity: str


#: The senders of the fictional example network (a firewall, the core router that serves DHCP,
#: a switch): the commented-out ``[[sources]]`` of ``deploy/windows/arcilogger.example.toml``
#: (tests/test_example_config.py keeps them equal).
DEFAULT_SOURCES: Final = (
    Source("fortigate", "fortigate", "172.31.255.1", DEVNAME),
    Source("core-r1", "mikrotik", "172.20.10.1", "CORE-R1"),
    Source("switch-1", "mikrotik", "172.20.10.21", "SWITCH-1"),
)
#: The switch that sends syslog: link up/down of the PCs on its ports (the first users' PCs; the
#: others are on switches that send none).
SWITCH: Final = "switch-1"
SWITCH_ADDRESS: Final = "172.20.10.21"
SWITCH_PORTS: Final = 48
#: The core router's port to the firewall (the firewall's ARP entry is on it, not on a VLAN).
ROUTER_UPLINK: Final = "ether1"
#: The core router's MAC on the transit link (172.31.255.2; it follows the address like the
#: infrastructure's): the ``srcmac`` the firewall logs for every client behind the router.
ROUTER_MAC: Final = "00:00:5e:00:53:02"

#: The department VLANs of the example network (DHCP server ``dhcp<VLAN>``): VLAN → department.
DEPARTMENTS: Final = {
    41: "Finans",
    42: "Planlama",
    43: "İdari İşler",
}
#: Everyone's phone is on the staff Wi-Fi; guests are on the guest Wi-Fi (short leases).
STAFF_WIFI: Final = 60
GUEST_WIFI: Final = 61
#: ``--dhcp-server both``: the VLANs whose gateway and DHCP server is the FortiGate (the core
#: router keeps the department VLANs).
FORTIGATE_VLANS_BOTH: Final = (STAFF_WIFI, GUEST_WIFI)
#: Static network devices (switches, access points, printers, the admin PC) and servers.
DEVICE_VLAN: Final = 10
SERVER_VLAN: Final = 20
FIRST_NAMES: Final = (
    "Ayşe", "Mehmet", "Fatma", "Ahmet", "Zeynep", "Mustafa", "Elif", "Emre", "Merve", "Can",
    "Selin", "Burak", "Deniz", "Ece", "Hakan", "İpek", "Kerem", "Leyla", "Murat", "Özge",
    "Serkan", "Tuğba", "Umut", "Yasemin",
)  # fmt: skip
LAST_NAMES: Final = (
    "Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Yıldız", "Aydın", "Öztürk", "Arslan", "Doğan",
    "Kılıç", "Aslan", "Çetin", "Koç", "Kurt", "Özdemir",
)  # fmt: skip


@dataclass(frozen=True)
class Site:
    """A web destination. Names are RFC 2606 ``.example`` domains; addresses are from the
    documentation ranges (RFC 5737); categories are placeholders, not FortiGuard values."""

    hostname: str
    category: int
    catdesc: str
    weight: int
    blocked: bool = False
    #: (typical seconds, typical received bytes): browsing, mail, video …
    duration: float = 20.0
    received: int = 150_000

    @property
    def addresses(self) -> tuple[str, str]:
        digest = hashlib.sha256(self.hostname.encode()).digest()
        net = ("203.0.113", "198.51.100")[digest[0] % 2]
        return f"{net}.{10 + digest[1] % 240}", f"{net}.{10 + digest[2] % 240}"


SITES: Final = (
    Site("portal.example", 901, "Sentetik: İş Uygulamaları", 30, duration=30, received=80_000),
    Site("mail.example", 902, "Sentetik: E-posta", 25, duration=60, received=200_000),
    Site("arama.example", 903, "Sentetik: Arama Motorları", 25, duration=5, received=60_000),
    Site("haber.example", 904, "Sentetik: Haber", 15, duration=40, received=900_000),
    Site("video.example", 905, "Sentetik: Video", 8, duration=600, received=40_000_000),
    Site("sosyal.example", 906, "Sentetik: Sosyal Medya", 10, duration=45, received=1_500_000),
    Site("bulut.example", 907, "Sentetik: Bulut Depolama", 6, duration=120, received=5_000_000),
    Site("banka.example", 908, "Sentetik: Finans", 4, duration=30, received=300_000),
    Site("cdn.example", 909, "Sentetik: İçerik Dağıtımı", 20, duration=3, received=400_000),
    Site("oyun.example", 990, "Sentetik: Oyun", 2, blocked=True),
    Site("bahis.example", 991, "Sentetik: Kumar", 1, blocked=True),
)
#: Application-control signature: the fictional application of the format samples
#: (``tests/fixtures/format/``; a placeholder id and name, not a FortiGuard value).
TLS_APP: Final = {
    "appid": "99101",
    "app": "Example.Docs",
    "appcat": "General.Interest",
    "apprisk": "elevated",
}


DhcpServer = Literal["mikrotik", "fortigate", "both"]


@dataclass(frozen=True)
class Egress:
    """An internet exit (``--egress NAME:TRANSIP:DSTINTF:SHARE``): ``share`` per cent of the
    sessions leave by ``dstintf`` with source NAT to ``transip``."""

    name: str
    transip: str
    dstintf: str
    share: int


@dataclass(frozen=True)
class Settings:
    seed: int = 5651
    users: int = 120
    #: Browsing sessions per user and active work hour on the PC (phones: a fifth of it).
    intensity: float = 30.0
    transip: str = DEFAULT_TRANSIP
    interim_seconds: int = DEFAULT_INTERIM_SECONDS
    guests_per_day: int = 3
    #: Share of phones that use a new random MAC every day (the others keep one per network).
    rotating_phones: float = 0.2
    #: Share of the people who come in at the weekend.
    weekend_presence: float = 0.06
    sources: tuple[Source, ...] = DEFAULT_SOURCES
    #: Gateway and DHCP server of the VLANs: the core router, the FortiGate (a FortiGate-only
    #: site), or both (:data:`FORTIGATE_VLANS_BOTH` on the FortiGate).
    dhcp_server: DhcpServer = "mikrotik"
    #: Traffic lines carry ``srcmac`` (FortiGate device identification).
    srcmac: bool = False
    #: Internet exits; none: every session leaves by ``wan1`` with ``transip``.
    egresses: tuple[Egress, ...] = ()
    #: Attack scenarios (:data:`SCENARIOS`); none: the normal site only, byte for byte.
    scenarios: tuple[str, ...] = ()


def check_egresses(egresses: Sequence[Egress]) -> None:
    """Names unique, ``transip`` an IPv4 address, shares of 1-100 that sum to 100."""
    if not egresses:
        return
    names = [egress.name for egress in egresses]
    if len(set(names)) != len(names):
        raise LoadgenError(
            f"--egress adları tekrarlanıyor / --egress names repeat: {', '.join(names)}"
        )
    for egress in egresses:
        try:
            address = ipaddress.ip_address(egress.transip)
        except ValueError:
            address = None
        if address is None or address.version != 4:
            raise LoadgenError(
                f"--egress {egress.name}: TRANSIP bir IPv4 adresi olmalı / must be an IPv4 "
                f"address: {egress.transip!r}"
            )
        if not 1 <= egress.share <= 100:
            raise LoadgenError(
                f"--egress {egress.name}: SHARE 1-100 olmalı / must be 1-100: {egress.share}"
            )
    total = sum(egress.share for egress in egresses)
    if total != 100:
        raise LoadgenError(
            f"--egress paylarının toplamı 100 olmalı, {total} / the --egress shares must sum to "
            f"100, not {total}"
        )


# -- the simulated network ------------------------------------------------------------------------


@dataclass(frozen=True)
class Device:
    key: str
    kind: str  # pc, phone, ap, printer, server, admin, switch, firewall, guest
    #: VLAN id (``0``: on no VLAN, see ``interface``).
    vlan: int
    #: DHCP server name, ``None`` for static addresses.
    server: str | None
    static_ip: str | None
    hostname: str | None
    owner: int | None
    base_mac: str
    rotating: bool = False
    #: Access switch and port of a wired PC (link up/down lines).
    switch: str | None = None
    port: int | None = None
    #: The core router's interface of its ARP entry when it is not ``vlan<id>``.
    interface: str | None = None

    def mac_for(self, day: date, seed: int) -> str:
        """The MAC on ``day``: fixed, or a fresh locally administered one (rotating phones)."""
        if not self.rotating:
            return self.base_mac
        digest = hashlib.sha256(f"{seed}:{self.key}:{day.isoformat()}".encode()).digest()
        first = (digest[0] & 0xF0) | 0x02  # locally administered, unicast (x2, x6, xA, xE)
        return ":".join(f"{b:02x}" for b in (first, *digest[1:6]))


@dataclass(frozen=True)
class User:
    index: int
    name: str
    department: str
    vlan: int
    pc: Device
    phone: Device


def _mac(prefix: str, number: int) -> str:
    return f"{prefix}:{(number >> 16) & 0xFF:02x}:{(number >> 8) & 0xFF:02x}:{number & 0xFF:02x}"


def _random_mac(rng: random.Random) -> str:
    first = (rng.randrange(256) & 0xF0) | 0x02
    return ":".join(f"{b:02x}" for b in (first, *(rng.randrange(256) for _ in range(5))))


def build_users(settings: Settings) -> list[User]:
    rng = random.Random(f"{settings.seed}:users")  # noqa: S311 - a simulation, not security
    vlans = sorted(DEPARTMENTS)
    users = []
    for index in range(settings.users):
        vlan = vlans[index % len(vlans)]
        department = DEPARTMENTS[vlan]
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        on_switch = index < SWITCH_PORTS
        pc = Device(
            key=f"u{index:03d}-pc",
            kind="pc",
            vlan=vlan,
            server=f"dhcp{vlan}",
            static_ip=None,
            hostname=f"PC-{index + 1:03d}",
            owner=index,
            base_mac=_mac("3c:52:82", 0x1000 + index),  # OUI of the synthetic fixtures
            switch=SWITCH if on_switch else None,
            port=1 + index if on_switch else None,
        )
        phone = Device(
            key=f"u{index:03d}-phone",
            kind="phone",
            vlan=STAFF_WIFI,
            server=f"dhcp{STAFF_WIFI}",
            static_ip=None,
            hostname=rng.choice(("iPhone", "Galaxy-S", "Redmi-Note", None)),
            owner=index,
            base_mac=_random_mac(rng),
            rotating=rng.random() < settings.rotating_phones,
        )
        users.append(User(index, name, department, vlan, pc, phone))
    return users


def _static(
    key: str, kind: str, vlan: int, address: str, *, interface: str | None = None
) -> Device:
    """A device with a static address; its MAC (documentation range, RFC 7042) ends in the last
    byte of the address, so every infrastructure MAC follows its address."""
    host = int(address.rsplit(".", 1)[1])
    mac = f"00:00:5e:00:53:{host:02x}"
    return Device(key, kind, vlan, None, address, None, None, mac, interface=interface)


def infrastructure() -> list[Device]:
    """Always-on devices with static addresses: in VLAN 10 the admin PC, the switch, access
    points and printers, in VLAN 20 the servers, on the transit link the firewall. MAC addresses
    from the documentation range (RFC 7042), except the printers': they keep a printer vendor's
    prefix, so the OUI lookup names a vendor for them."""
    devices = [
        _static("admin-pc", "admin", DEVICE_VLAN, "172.20.10.37"),
        _static("switch-1", "switch", DEVICE_VLAN, SWITCH_ADDRESS),
        _static("arcilogger", "server", SERVER_VLAN, "172.20.20.15"),
        _static("nas", "server", SERVER_VLAN, "172.20.20.61"),
        _static("fortigate", "firewall", 0, "172.31.255.1", interface=ROUTER_UPLINK),
    ]
    for number in range(1, 5):
        devices.append(_static(f"ap-{number}", "ap", DEVICE_VLAN, f"172.20.10.{40 + number}"))
    for number in range(1, 5):
        devices.append(
            Device(
                f"printer-{number}",
                "printer",
                DEVICE_VLAN,
                None,
                f"172.20.10.{80 + number}",
                None,
                None,
                _mac("00:1b:a9", number),
            )
        )
    return devices


# -- output types ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """One syslog message: device time, source, the bytes on the wire (``render(at)``)."""

    at: datetime
    source: str
    order: int
    render: Callable[[datetime], bytes] = field(compare=False, repr=False)
    kind: str = ""

    @property
    def wire(self) -> bytes:
        return self.render(self.at)

    @property
    def received(self) -> datetime:
        """When the collector receives it in the simulation (a few milliseconds later)."""
        return self.at + timedelta(milliseconds=3 + self.order % 37)


@dataclass(frozen=True)
class SessionTruth:
    """A FortiGate session and who caused it (ground truth for lookups)."""

    sessionid: int
    start: datetime
    end: datetime
    proto: int
    srcip: str
    srcport: int
    transip: str
    transport: int
    dstip: str
    dstport: int
    hostname: str
    blocked: bool
    interim_logs: int
    device: str
    kind: str
    mac: str
    vlan: int
    user: int | None
    person: str | None
    department: str | None
    #: The exit the session left by (``--egress``); ``None`` without exits.
    egress: str | None = None


#: The table that holds a lease's DHCP evidence; ``none``: no DHCP or ARP-LOG line places the
#: device at its address (a static device behind the FortiGate; with ``--srcmac`` its traffic
#: lines carry its MAC, partial evidence only).
Evidence = Literal["mt_dhcp", "fg_dhcp", "none"]


@dataclass(frozen=True)
class LeaseTruth:
    ip: str
    mac: str
    server: str | None
    vlan: int
    hostname: str | None
    start: datetime
    end: datetime | None
    device: str
    user: int | None
    evidence: Evidence = "mt_dhcp"


@dataclass
class DayOutput:
    day: date
    messages: list[Message] = field(default_factory=list)
    sessions: list[SessionTruth] = field(default_factory=list)
    leases: list[LeaseTruth] = field(default_factory=list)

    def by_source(self) -> dict[str, list[Message]]:
        found: dict[str, list[Message]] = {}
        for message in self.messages:
            found.setdefault(message.source, []).append(message)
        return found


# -- rendering ------------------------------------------------------------------------------------


def _site_iso_ms(at: datetime) -> str:
    local = at.astimezone(SITE_TZ)
    return local.strftime("%Y-%m-%dT%H:%M:%S.") + f"{local.microsecond // 1000:03d}+03:00"


def mikrotik(identity: str, topics: str, text: str) -> Callable[[datetime], bytes]:
    """RouterOS remote syslog in the approximate format of ``tests/fixtures/synthetic/`` (no wire
    format sample yet: synthetic, verify on site): ``<30>ISO-time identity topics message``."""

    def render(at: datetime) -> bytes:
        return f"<30>{_site_iso_ms(at)} {identity} {topics} {text}".encode()

    return render


def _fg_value(value: object) -> str:
    if isinstance(value, _Nat):
        return str(value.assigned)
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text and all(c.isdigit() or c == "." for c in text) and text.count(".") == 3:
        return text  # IPv4 addresses are unquoted, as in the format samples
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fortigate(
    pri: int, logid: str, kind: str, subtype: str, level: str, fields: Sequence[tuple[str, object]]
) -> Callable[[datetime], bytes]:
    """FortiOS ``format default`` (key=value) with the header of the format samples."""

    def render(at: datetime) -> bytes:
        local = at.astimezone(SITE_TZ)
        nanos = int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1000 + 417
        head = (
            f'<{pri}>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="{DEVNAME}" '
            f'devid="{DEVID}" eventtime={nanos} tz="+0300" logid="{logid}" type="{kind}" '
            f'subtype="{subtype}" level="{level}" vd="root"'
        )
        body = " ".join(f"{key}={_fg_value(value)}" for key, value in fields)
        return f"{head} {body}".encode()

    return render


# -- the simulation -------------------------------------------------------------------------------


@dataclass
class _Nat:
    """A session's source NAT. ``transport`` is assigned once the whole day is generated, in
    time order over all devices (sessions are generated one user at a time)."""

    srcport: int
    start: datetime
    end: datetime
    sessionid: int
    transip: str
    port: int | None = None
    #: Index of the session's truth record in the day's ``sessions``.
    truth: int | None = None

    @property
    def assigned(self) -> int:
        if self.port is None:  # pragma: no cover - would be a bug in Simulation.day
            raise RuntimeError(f"session {self.sessionid}: NAT port not assigned yet")
        return self.port


@dataclass
class _Lease:
    mac: str
    ip: str
    device: Device
    start: datetime
    expires: datetime
    #: The day output whose ``leases`` holds this lease's truth record, and its index there.
    truth: tuple[DayOutput, int] | None = None
    #: A FortiGate lease: the time of its last ACK (``None``: a MikroTik lease).
    acked: datetime | None = None


@dataclass
class _DayPlan:
    """When a user is at work: PC on [arrive, leave], phone on Wi-Fi a little longer."""

    present: bool
    arrive: datetime | None = None
    leave: datetime | None = None
    lunch: tuple[datetime, datetime] | None = None


class Simulation:
    """Deterministic simulation of the network; :meth:`run` yields one :class:`DayOutput` per
    day. Days must be generated in order (leases and ports carry over midnight)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        check_egresses(self.settings.egresses)
        self.users = build_users(self.settings)
        self.infrastructure = infrastructure()
        # A FortiGate-only site has no MikroTik: no core-r1/switch-1 line at all.
        self.sources = {
            source.name: source
            for source in self.settings.sources
            if self.settings.dhcp_server != "fortigate" or source.type != "mikrotik"
        }
        self._leases: dict[str, _Lease] = {}  # by MAC
        self._sticky: dict[str, str] = {}  # device key -> last IP
        self._next_session = 3_200_000
        self._ports: dict[str, int] = {}
        self._transport_busy: dict[tuple[str, int], datetime] = {}  # (transip, port) -> until
        self._nat: list[_Nat] = []
        self._order = 0
        self._out: DayOutput | None = None
        #: Per device: (from, until, ip) of the leases seen on the current day.
        self._windows: dict[str, list[tuple[datetime, datetime, str]]] = {}
        self._statics_recorded = False
        #: Per IP: when its last lease ended. ``day`` handles one device at a time, so a lease that
        #: ended later than another device's ``begin`` is already gone from ``_leases`` then.
        self._ended: dict[str, datetime] = {}

    # -- public -------------------------------------------------------------------------------

    def run(self, first: date, last: date | None = None) -> Iterator[DayOutput]:
        day = first
        while day <= (last or first):
            yield self.day(day)
            day += timedelta(days=1)

    def day(self, day: date) -> DayOutput:
        self._out = out = DayOutput(day)
        self._windows = {}
        start = datetime.combine(day, dtime(0), tzinfo=SITE_TZ).astimezone(UTC)
        end = start + timedelta(days=1)
        activity: dict[str, list[tuple[datetime, datetime]]] = {}
        plans = {user.index: self._plan(user, day) for user in self.users}
        for user in self.users:
            plan = plans[user.index]
            if plan.present and plan.arrive is not None and plan.leave is not None:
                activity[user.pc.key] = [(plan.arrive, plan.leave)]
                activity[user.phone.key] = [
                    (plan.arrive - timedelta(minutes=12), plan.leave + timedelta(minutes=4))
                ]
        for device in self.infrastructure:
            activity[device.key] = [(start, end)]
        guests = self._guests(day)
        for guest, interval in guests:
            activity[guest.key] = [interval]
        devices = [
            *(d for user in self.users for d in (user.pc, user.phone)),
            *self.infrastructure,
            *(guest for guest, _ in guests),
        ]
        for device in devices:
            self._dhcp(device, day, activity.get(device.key, []), start, end)
        self._record_statics(start)
        self._arp(day, devices, activity, start)
        self._heartbeats(start)
        self._switch_links(plans, day)
        for user in self.users:
            self._browse(user, plans[user.index], day)
        for guest, (arrive, leave) in guests:
            self._browse_guest(guest, arrive, leave, day)
        self._admin(day)
        self._firewall_drops(day, plans)
        for name in self.settings.scenarios:
            first = len(out.messages)
            getattr(self, "_scenario_" + name.replace("-", "_"))(day)
            # The scenario's lines count as its own kind in ``stats`` (and for the tests).
            out.messages[first:] = [
                replace(m, kind=f"scenario.{name}") for m in out.messages[first:]
            ]
        self._assign_nat(out)
        out.messages.sort(key=lambda m: (m.at, m.order))
        out.sessions.sort(key=lambda s: (s.start, s.sessionid))
        self._out = None
        return out

    # -- helpers ------------------------------------------------------------------------------

    def _rng(self, *parts: object) -> random.Random:
        seed = ":".join(str(p) for p in (self.settings.seed, *parts))
        return random.Random(seed)  # noqa: S311 - a reproducible simulation, not security

    def _emit(
        self, at: datetime, source: str, render: Callable[[datetime], bytes], kind: str
    ) -> None:
        if source not in self.sources or self._out is None:
            return
        self._order += 1
        self._out.messages.append(Message(at, source, self._order, render, kind))

    def _at(self, day: date, hour: float) -> datetime:
        local = datetime.combine(day, dtime(0), tzinfo=SITE_TZ) + timedelta(hours=hour)
        return local.astimezone(UTC)

    def _fortigate_gateway(self, vlan: int) -> bool:
        """Whether the FortiGate is the gateway and DHCP server of ``vlan`` (``--dhcp-server``);
        otherwise it is the core router, as on the example network."""
        mode = self.settings.dhcp_server
        return mode == "fortigate" or (mode == "both" and vlan in FORTIGATE_VLANS_BOTH)

    def _plan(self, user: User, day: date) -> _DayPlan:
        rng = self._rng("plan", user.index, day.isoformat())
        weekend = day.weekday() >= 5
        if weekend and rng.random() >= self.settings.weekend_presence:
            return _DayPlan(False)
        if not weekend and rng.random() < 0.04:  # leave, sick day
            return _DayPlan(False)
        arrive = rng.uniform(7.75, 9.5) if not weekend else rng.uniform(9.5, 11)
        leave = rng.uniform(17.25, 19.5) if not weekend else arrive + rng.uniform(2, 4)
        lunch_start = rng.uniform(12.25, 12.75)
        return _DayPlan(
            True,
            self._at(day, arrive),
            self._at(day, leave),
            (self._at(day, lunch_start), self._at(day, lunch_start + rng.uniform(0.6, 1.0))),
        )

    def _guests(self, day: date) -> list[tuple[Device, tuple[datetime, datetime]]]:
        rng = self._rng("guests", day.isoformat())
        found = []
        count = (
            0
            if day.weekday() >= 5
            else rng.randint(min(1, self.settings.guests_per_day), self.settings.guests_per_day)
        )
        for number in range(count):
            begin = rng.uniform(9.0, 16.0)
            device = Device(
                key=f"guest-{day.isoformat()}-{number}",
                kind="guest",
                vlan=GUEST_WIFI,
                server=f"dhcp{GUEST_WIFI}",
                static_ip=None,
                hostname=rng.choice(("iPhone", "android-misafir", None)),
                owner=None,
                base_mac=_random_mac(rng),
            )
            found.append(
                (device, (self._at(day, begin), self._at(day, begin + rng.uniform(0.5, 3))))
            )
        return found

    # -- DHCP ---------------------------------------------------------------------------------

    def _lease_seconds(self, server: str | None) -> int:
        return LEASE_SECONDS_BY_SERVER.get(server or "", LEASE_SECONDS)

    def _free_ip(self, device: Device, at: datetime) -> str:
        # A DHCP server never hands out an address before its lease has ended. Devices are
        # simulated one after the other, so a lease that ends later than ``at`` may already have
        # left ``_leases``: its end still holds the address (#89; the default output changed).
        held = {lease.ip for lease in self._leases.values()}
        held |= {ip for ip, ended in self._ended.items() if ended > at}
        sticky = self._sticky.get(device.key)
        if sticky is not None and sticky not in held:
            return sticky
        digest = int(hashlib.sha256(f"{self.settings.seed}:{device.key}".encode()).hexdigest(), 16)
        for step in range(POOL_SIZE):
            candidate = f"172.20.{device.vlan}.{POOL_FIRST + (digest + step) % POOL_SIZE}"
            if candidate not in held:
                return candidate
        vlan = device.vlan
        full = WIFI_USERS["fortigate" if self._fortigate_gateway(STAFF_WIFI) else "mikrotik"]
        raise LoadgenError(
            f"VLAN {vlan} DHCP havuzu (172.20.{vlan}.{POOL_FIRST}-{POOL_FIRST + POOL_SIZE - 1}, "
            f"{POOL_SIZE} adres) {self.settings.users} kullanıcının cihazlarına yetmiyor; "
            f"--users değerini düşürün (personel Wi-Fi'ı yaklaşık {full} kullanıcıyla dolar). / "
            f"The DHCP pool of VLAN {vlan} ({POOL_SIZE} addresses) is exhausted with "
            f"{self.settings.users} users: lower --users (the staff Wi-Fi fills up at about "
            f"{full})."
        )

    def _dhcp_line(self, device: Device, action: str, ip: str, mac: str, at: datetime) -> None:
        text = f"{device.server} {action} {ip} for {mac.upper()}"
        if device.hostname:
            text += f" {device.hostname}"
        identity = self.sources["core-r1"].identity if "core-r1" in self.sources else "CORE-R1"
        self._emit(at, "core-r1", mikrotik(identity, "dhcp,info", text), f"dhcp.{action}")

    def _fortigate_dhcp_line(self, lease: _Lease, at: datetime, *, release: bool = False) -> None:
        """A FortiGate DHCP server record in the shape of the synthetic fixture lines
        (``tests/fixtures/synthetic/fortigate-synthetic.log``; FortiOS 7.6 field list, MAC upper
        case as in the community sample). The ACK is 26001; the RELEASE logid is derived and its
        ``dhcp_msg``/``msg`` are the fixture's placeholders (sahada doğrula)."""
        device = lease.device
        fields: list[tuple[str, object]] = [
            ("logdesc", "DHCP Release log" if release else "DHCP Ack log"),
            ("interface", f"vlan{device.vlan}"),
            ("dhcp_msg", "Release" if release else "Ack"),
            ("mac", lease.mac.upper()),
            ("ip", lease.ip),
        ]
        if not release:
            fields.append(("lease", self._lease_seconds(device.server)))
        if device.hostname:
            fields.append(("hostname", device.hostname))
        fields.append(
            ("msg", "sentetik: DHCP release" if release else "DHCP server sends a DHCPACK")
        )
        logid = "0100026002" if release else "0100026001"
        render = fortigate(190, logid, "event", "system", "information", fields)
        self._emit(at, "fortigate", render, "fg-dhcp.release" if release else "fg-dhcp.ack")

    def _ack(self, lease: _Lease, at: datetime) -> None:
        """The FortiGate ACKs the lease at ``at``: it now ends one lease time later."""
        lease.acked = at
        lease.expires = at + timedelta(seconds=self._lease_seconds(lease.device.server))
        self._fortigate_dhcp_line(lease, at)

    def _expire(self, lease: _Lease, at: datetime) -> None:
        """The lease ends at ``at``: RouterOS logs it; a FortiGate lease runs out unlogged (a
        simulation choice: FortiOS documents no expiry record)."""
        if lease.acked is None:
            self._dhcp_line(lease.device, "deassigned", lease.ip, lease.mac, at)
        self._end(lease, at)

    def _end(self, lease: _Lease, at: datetime) -> None:
        del self._leases[lease.mac]
        self._ended[lease.ip] = max(at, self._ended.get(lease.ip, at))
        if lease.truth is not None:
            output, index = lease.truth
            output.leases[index] = replace(output.leases[index], end=at)

    def _dhcp(
        self,
        device: Device,
        day: date,
        intervals: list[tuple[datetime, datetime]],
        start: datetime,
        end: datetime,
    ) -> None:
        if device.server is None or self._out is None:
            return
        mac = device.mac_for(day, self.settings.seed)
        lease_time = timedelta(seconds=self._lease_seconds(device.server))
        fortigate_dhcp = self._fortigate_gateway(device.vlan)
        for begin, finish in intervals:
            for other in [lease for lease in self._leases.values() if lease.device is device]:
                if other.expires <= begin:
                    self._expire(other, other.expires)
            lease = self._leases.get(mac)
            if lease is None:
                ip = self._free_ip(device, begin)
                lease = _Lease(mac, ip, device, begin, begin)
                self._leases[mac] = lease
                self._sticky[device.key] = ip
                if fortigate_dhcp:
                    self._ack(lease, begin)
                else:
                    self._dhcp_line(device, "assigned", ip, mac, begin)
                lease.truth = (self._out, len(self._out.leases))
                self._out.leases.append(
                    LeaseTruth(
                        ip,
                        mac,
                        device.server,
                        device.vlan,
                        device.hostname,
                        begin,
                        None,
                        device.key,
                        device.owner,
                        "fg_dhcp" if fortigate_dhcp else "mt_dhcp",
                    )
                )
            if lease.acked is None:
                lease.expires = max(lease.expires, finish + lease_time)
            else:
                # The client renews once half the lease has passed (sahada doğrula: F7): at that
                # moment while connected, or as soon as it is back. So a lease never runs out
                # while its device is on the network.
                half = lease_time / 2
                if begin >= lease.acked + half:
                    self._ack(lease, begin)
                while lease.acked + half <= finish:
                    self._ack(lease, lease.acked + half)
                if device.kind == "guest":  # the only 26002: a guest's phone leaves
                    self._fortigate_dhcp_line(lease, finish, release=True)
                    self._windows.setdefault(device.key, []).append((begin, finish, lease.ip))
                    self._end(lease, finish)
                    continue
            self._windows.setdefault(device.key, []).append((begin, lease.expires, lease.ip))
        for lease in [lease for lease in self._leases.values() if lease.device is device]:
            if start <= lease.expires < end:
                self._expire(lease, lease.expires)

    def _record_statics(self, start: datetime) -> None:
        """Static devices behind the FortiGate: no DHCP and no ARP-LOG places them at their
        address, so the truth lists them once, with the evidence ``none``."""
        if self._statics_recorded or self._out is None:
            return
        self._statics_recorded = True
        for device in self.infrastructure:
            if device.static_ip is None or device.kind == "firewall":
                continue
            if not self._fortigate_gateway(device.vlan):
                continue
            self._out.leases.append(
                LeaseTruth(
                    device.static_ip,
                    device.base_mac,
                    None,
                    device.vlan,
                    None,
                    start,
                    None,
                    device.key,
                    None,
                    "none",
                )
            )

    def ip_at(self, device: Device, at: datetime) -> str | None:
        """The device's address at ``at`` (static, or the lease it held then)."""
        if device.static_ip is not None:
            return device.static_ip
        for begin, until, ip in self._windows.get(device.key, []):
            if begin <= at <= until:
                return ip
        return None

    # -- RouterOS -----------------------------------------------------------------------------

    def _arp(
        self,
        day: date,
        devices: Sequence[Device],
        activity: Mapping[str, list[tuple[datetime, datetime]]],
        start: datetime,
    ) -> None:
        """Hourly ARP-LOG of every dynamic ARP entry (docs/PARSERS.md): devices active at the
        hour (the core router's, so none of a VLAN whose gateway is the FortiGate)."""
        identity = self.sources["core-r1"].identity if "core-r1" in self.sources else "CORE-R1"
        recent = timedelta(minutes=10)
        for hour in range(24):
            mark = start + timedelta(hours=hour, seconds=5)
            for offset, device in enumerate(devices):
                if self._fortigate_gateway(device.vlan):
                    continue
                spans = activity.get(device.key, [])
                if not any(begin - recent <= mark <= end + recent for begin, end in spans):
                    continue
                ip = self.ip_at(device, mark)
                if ip is None:
                    continue
                interface = device.interface or f"vlan{device.vlan}"
                text = f"ARP-LOG {ip} {device.mac_for(day, self.settings.seed).upper()} {interface}"
                at = mark + timedelta(milliseconds=7 * offset)
                self._emit(at, "core-r1", mikrotik(identity, "script,info", text), "arp")

    def _heartbeats(self, start: datetime) -> None:
        for source in self.sources.values():
            if source.type != "mikrotik":
                continue
            offset = int(hashlib.sha256(source.name.encode()).hexdigest(), 16) % 60
            for slot in range(24 * 60 // HEARTBEAT_MINUTES):
                at = start + timedelta(minutes=slot * HEARTBEAT_MINUTES, seconds=offset)
                text = f"ARCI-HEARTBEAT {source.identity}"
                self._emit(
                    at, source.name, mikrotik(source.identity, "script,info", text), "heartbeat"
                )
        if "fortigate" in self.sources:
            for slot in range(24 * 60 // HEARTBEAT_MINUTES):
                at = start + timedelta(minutes=slot * HEARTBEAT_MINUTES, seconds=1)
                self._emit(at, "fortigate", self._perf_stats(slot), "perf-stats")

    def _waninfo(self, slot: int) -> str:
        """``waninfo`` of the performance statistics (the format sample's layout): the exits'
        interfaces, with made-up counters."""
        interfaces = list(dict.fromkeys(egress.dstintf for egress in self.settings.egresses))
        parts = []
        for number, name in enumerate(interfaces, 1):
            sent, received = (slot + 1) * 7_340_032 * number, (slot + 1) * 912_680 * number
            packets_out, packets_in = (slot + 1) * 6120 * number, (slot + 1) * 2890 * number
            parts.append(f"name={name},bytes={sent}/{received},packets={packets_out}/{packets_in};")
        return "".join(parts)

    def _perf_stats(self, slot: int) -> Callable[[datetime], bytes]:
        sessions = 120 + (slot * 37) % 900
        cpu, mem = slot % 9, 38 + slot % 5
        if "fw-health" in self.settings.scenarios and slot in HOT_SLOTS:
            cpu, mem = 96, 91  # scenario fw-health (R17): busy for 30 minutes
        fields: list[tuple[str, object]] = [
            ("logdesc", "System performance statistics"),
            ("action", "perf-stats"),
            ("cpu", cpu),
            ("mem", mem),
            ("totalsession", sessions),
            ("disk", 0),
            ("bandwidth", f"{slot % 300}/{slot % 700}"),
            ("setuprate", slot % 40),
            ("disklograte", 0),
            ("fazlograte", 0),
            ("freediskstorage", 0),
            ("sysuptime", 100_000 + slot * 300),
            (
                "msg",
                f"Performance statistics: average CPU: {cpu}, concurrent sessions: {sessions}",
            ),
        ]
        if self.settings.egresses:
            fields.insert(-1, ("waninfo", self._waninfo(slot)))
        return fortigate(189, "0100040704", "event", "system", "notice", fields)

    def _switch_links(self, plans: Mapping[int, _DayPlan], day: date) -> None:
        for user in self.users:
            plan = plans[user.index]
            switch = user.pc.switch
            if not plan.present or switch is None or switch not in self.sources:
                continue
            identity = self.sources[switch].identity
            port = f"ether{user.pc.port}"
            if plan.arrive is not None:
                up = mikrotik(identity, "interface,info", f"{port} link up (speed 1G, full duplex)")
                self._emit(plan.arrive - timedelta(seconds=20), switch, up, "link")
            if plan.leave is not None:
                down = mikrotik(identity, "interface,info", f"{port} link down")
                self._emit(plan.leave + timedelta(seconds=30), switch, down, "link")

    def _firewall_drops(self, day: date, plans: Mapping[int, _DayPlan]) -> None:
        """A few inter-VLAN drops (a PC trying the switch's management port)."""
        identity = self.sources["core-r1"].identity if "core-r1" in self.sources else "CORE-R1"
        rng = self._rng("drops", day.isoformat())
        for user in self.users:
            plan = plans[user.index]
            if not plan.present or plan.arrive is None or rng.random() > 0.1:
                continue
            at = plan.arrive + timedelta(minutes=rng.uniform(5, 90))
            ip = self.ip_at(user.pc, at)
            if ip is None:
                continue
            mac = user.pc.mac_for(day, self.settings.seed).upper()
            port = rng.randint(*EPHEMERAL)
            text = (
                f"DROP-MGMT forward: in:vlan{user.vlan} out:vlan10, connection-state:new "
                f"src-mac {mac}, proto TCP (SYN), {ip}:{port}->{SWITCH_ADDRESS}:8291, len 52"
            )
            self._emit(at, "core-r1", mikrotik(identity, "firewall,info", text), "firewall")

    # -- FortiGate ----------------------------------------------------------------------------

    def _port(self, device: Device, rng: random.Random) -> int:
        current = self._ports.get(device.key)
        if current is None:
            current = rng.randint(EPHEMERAL[0], EPHEMERAL[1] - 2000)
        current = current + 1 if current < EPHEMERAL[1] else EPHEMERAL[0]
        self._ports[device.key] = current
        return current

    def _transport(self, transip: str, srcport: int, start: datetime, end: datetime) -> int:
        """``transport`` = the source port unless another session still uses it on the same
        ``transip`` (called in time order: :meth:`_assign_nat`)."""
        port = srcport
        while self._transport_busy.get((transip, port), start) > start:
            port = port + 1 if port < EPHEMERAL[1] else EPHEMERAL[0]
        self._transport_busy[(transip, port)] = end + NAT_GRACE
        return port

    def _assign_nat(self, out: DayOutput) -> None:
        """Give every session of the day its NAT port, earliest session first."""
        for nat in sorted(self._nat, key=lambda n: (n.start, n.sessionid)):
            nat.port = self._transport(nat.transip, nat.srcport, nat.start, nat.end)
            if nat.truth is not None:
                out.sessions[nat.truth] = replace(out.sessions[nat.truth], transport=nat.port)
        self._nat = []

    def _browse(self, user: User, plan: _DayPlan, day: date) -> None:
        if not plan.present or plan.arrive is None or plan.leave is None:
            return
        rng = self._rng("browse", user.index, day.isoformat())
        rate = self.settings.intensity
        for device, share in ((user.pc, 1.0), (user.phone, 0.2)):
            moment = plan.arrive + timedelta(minutes=rng.uniform(1, 6))
            while True:
                at_lunch = plan.lunch is not None and plan.lunch[0] <= moment < plan.lunch[1]
                hourly = rate * (0.15 if device is user.pc and at_lunch else share)
                if device is user.phone and at_lunch:
                    hourly = rate * 0.6
                moment += timedelta(seconds=rng.expovariate(max(hourly, 0.01) / 3600))
                if moment >= plan.leave:
                    break
                ip = self.ip_at(device, moment)
                if ip is None:
                    continue
                self._session(user, device, ip, moment, rng, day)

    def _browse_guest(self, guest: Device, arrive: datetime, leave: datetime, day: date) -> None:
        """A guest's phone browses through the firewall like a staff phone: an anonymous device
        (no person) that a lookup can only name by its MAC and lease."""
        rng = self._rng("guest-browse", guest.key)
        hourly = self.settings.intensity * 0.2
        moment = arrive + timedelta(minutes=rng.uniform(1, 6))
        while True:
            moment += timedelta(seconds=rng.expovariate(max(hourly, 0.01) / 3600))
            if moment >= leave:
                break
            ip = self.ip_at(guest, moment)
            if ip is None:
                continue
            self._session(None, guest, ip, moment, rng, day)

    def _pick_site(self, rng: random.Random) -> Site:
        return rng.choices(SITES, weights=[site.weight for site in SITES])[0]

    def _egress(self, sessionid: int) -> Egress | None:
        """The exit a session leaves by: drawn by ``rng(seed, "egress", session)`` with the
        shares as weights, so the other choices of the day stay as they are."""
        egresses = self.settings.egresses
        if not egresses:
            return None
        rng = self._rng("egress", sessionid)
        return rng.choices(egresses, weights=[egress.share for egress in egresses])[0]

    def _srcmac(self, device: Device, day: date) -> str | None:
        """``srcmac`` of the device's traffic (``--srcmac``): its own MAC when the FortiGate is
        its gateway, the core router's for every client behind the router."""
        if not self.settings.srcmac:
            return None
        if self._fortigate_gateway(device.vlan):
            return device.mac_for(day, self.settings.seed)
        return ROUTER_MAC

    def _session(
        self,
        user: User | None,
        device: Device,
        ip: str,
        at: datetime,
        rng: random.Random,
        day: date,
    ) -> None:
        site = self._pick_site(rng)
        dstip = site.addresses[rng.randrange(2)]
        # The FortiGate's port to the core router, or the client's VLAN when it is the gateway.
        srcintf = f"vlan{device.vlan}" if self._fortigate_gateway(device.vlan) else "port2"
        self._dns(ip, at, site, rng, srcintf)
        start = at + timedelta(milliseconds=rng.randint(30, 120))
        sessionid = self._next_session
        self._next_session += 1
        srcport = self._port(device, rng)
        if site.blocked:
            duration = 0
        else:
            typical = site.duration
            duration = max(1, int(rng.lognormvariate(0, 0.9) * typical))
        end = start + timedelta(seconds=max(duration, 1))
        egress = self._egress(sessionid)
        transip = self.settings.transip if egress is None else egress.transip
        transport = _Nat(srcport, start, end, sessionid, transip)
        self._nat.append(transport)
        source: list[tuple[str, object]] = [
            ("srcip", ip),
            ("srcport", srcport),
            ("srcintf", srcintf),
        ]
        destination: list[tuple[str, object]] = [
            ("srcintfrole", "undefined"),
            ("dstip", dstip),
            ("dstport", 443),
            ("dstintf", "wan1" if egress is None else egress.dstintf),
            ("dstintfrole", "wan"),
        ]
        common = [*source, *destination]
        srcmac = self._srcmac(device, day)
        # srcmac only in the traffic logs, after srcintf as in the synthetic fixture.
        traffic_common = common if srcmac is None else [*source, ("srcmac", srcmac), *destination]
        url = rng.choice(("/", "/giris", "/haberler/gundem", "/api/v1/mesajlar", "/izle?v=42"))
        web: list[tuple[str, object]] = [
            ("policyid", POLICYID),
            ("sessionid", sessionid),
            *common,
            ("proto", 6),
            ("service", "HTTPS"),
            ("hostname", site.hostname),
            ("profile", "WEB-PROFILE"),
            ("action", "blocked" if site.blocked else "passthrough"),
            ("url", url),
        ]
        if site.blocked:
            web.insert(0, ("eventtype", "ftgd_blk"))
            web.append(("msg", "URL belongs to a denied category in policy"))
        else:
            web.insert(0, ("eventtype", "ftgd_allow"))
        web += [("cat", site.category), ("catdesc", site.catdesc)]
        level = "warning" if site.blocked else "notice"
        pri = 188 if site.blocked else 189
        self._emit(
            start + timedelta(milliseconds=40),
            "fortigate",
            fortigate(
                pri, "9900000001" if site.blocked else "9900000002", "utm", "webfilter", level, web
            ),
            "webfilter",
        )
        if not site.blocked:
            app: list[tuple[str, object]] = [
                ("eventtype", "signature"),
                ("appid", int(TLS_APP["appid"])),
                *common,
                ("proto", 6),
                ("service", "SSL"),
                ("direction", "incoming"),
                ("policyid", POLICYID),
                ("sessionid", sessionid),
                ("applist", "APP-PROFILE"),
                ("action", "pass"),
                ("appcat", TLS_APP["appcat"]),
                ("app", TLS_APP["app"]),
                ("hostname", site.hostname),
                ("url", "/"),
                ("msg", f"{TLS_APP['appcat']}: {TLS_APP['app']}"),
                ("apprisk", TLS_APP["apprisk"]),
            ]
            self._emit(
                start + timedelta(milliseconds=35),
                "fortigate",
                fortigate(190, "1059028704", "utm", "app-ctrl", "information", app),
                "app-ctrl",
            )
        received = 0 if site.blocked else max(200, int(rng.lognormvariate(0, 1.0) * site.received))
        sent = max(120, received // rng.randint(8, 40))
        packets_in, packets_out = max(1, received // 1200), max(1, sent // 600)
        interim = self.settings.interim_seconds
        logged = (0, 0, 0, 0, 0)  # duration, sent, received, sentpkt, rcvdpkt at the last log
        interims = 0
        elapsed = interim
        while not site.blocked and elapsed < duration:
            share = elapsed / duration
            now = (
                elapsed,
                int(sent * share),
                int(received * share),
                int(packets_out * share),
                int(packets_in * share),
            )
            self._traffic(
                start + timedelta(seconds=elapsed),
                "0000000020",
                "accept",
                traffic_common,
                sessionid,
                transport,
                now,
                logged,
            )
            logged = now
            interims += 1
            elapsed += interim
        final = (duration, sent, received, packets_out, packets_in)
        action = (
            "client-rst"
            if site.blocked
            else rng.choice(("close", "close", "client-rst", "server-rst"))
        )
        self._traffic(
            end,
            "0000000013",
            action,
            traffic_common,
            sessionid,
            transport,
            final,
            logged if interims else None,
            blocked=site.blocked,
        )
        if self._out is not None:
            transport.truth = len(self._out.sessions)
            self._out.sessions.append(
                SessionTruth(
                    sessionid=sessionid,
                    start=start,
                    end=end,
                    proto=6,
                    srcip=ip,
                    srcport=srcport,
                    transip=transip,
                    transport=0,  # assigned with the day's other NAT ports (_assign_nat)
                    dstip=dstip,
                    dstport=443,
                    hostname=site.hostname,
                    blocked=site.blocked,
                    interim_logs=interims,
                    device=device.key,
                    kind=device.kind,
                    mac=device.mac_for(day, self.settings.seed),
                    vlan=device.vlan,
                    user=user.index if user is not None else None,
                    person=user.name if user is not None else None,
                    department=user.department if user is not None else None,
                    egress=None if egress is None else egress.name,
                )
            )

    def _traffic(
        self,
        at: datetime,
        logid: str,
        action: str,
        common: Sequence[tuple[str, object]],
        sessionid: int,
        transport: _Nat,
        totals: tuple[int, int, int, int, int],
        previous: tuple[int, int, int, int, int] | None,
        *,
        blocked: bool = False,
    ) -> None:
        duration, sent, received, sentpkt, rcvdpkt = totals
        fields: list[tuple[str, object]] = [
            *common,
            ("srccountry", "Reserved"),
            ("dstcountry", "Reserved"),
            ("sessionid", sessionid),
            ("proto", 6),
            ("action", action),
            ("policyid", POLICYID),
            ("policytype", "policy"),
            ("poluuid", POLUUID),
            ("policyname", "internet-out"),
            ("service", "HTTPS"),
            ("trandisp", "snat"),
            ("transip", transport.transip),
            ("transport", transport),
            ("appid", int(TLS_APP["appid"])),
            ("app", TLS_APP["app"]),
            ("appcat", TLS_APP["appcat"]),
            ("apprisk", TLS_APP["apprisk"]),
            ("applist", "APP-PROFILE"),
            ("appact", "detected"),
            ("duration", duration),
            ("sentbyte", sent),
            ("rcvdbyte", received),
            ("sentpkt", sentpkt),
            ("rcvdpkt", rcvdpkt),
        ]
        if previous is not None:
            fields += [
                ("sentdelta", sent - previous[1]),
                ("rcvddelta", received - previous[2]),
                ("durationdelta", duration - previous[0]),
                ("sentpktdelta", sentpkt - previous[3]),
                ("rcvdpktdelta", rcvdpkt - previous[4]),
            ]
        if logid == "0000000013":
            fields.append(("utmaction", "block" if blocked else "allow"))
        render = fortigate(189, logid, "traffic", "forward", "notice", fields)
        self._emit(at, "fortigate", render, "traffic")

    def _dns(self, ip: str, at: datetime, site: Site, rng: random.Random, srcintf: str) -> None:
        sessionid = self._next_session
        self._next_session += 1
        srcport = rng.randint(50000, 65000)
        base: list[tuple[str, object]] = [
            ("policyid", POLICYID),
            ("sessionid", sessionid),
            ("srcip", ip),
            ("srcport", srcport),
            ("srcintf", srcintf),
            ("dstip", DNS_SERVER),
            ("dstport", 53),
            ("dstintf", f"vlan{SERVER_VLAN}" if self._fortigate_gateway(SERVER_VLAN) else "port2"),
            ("proto", 17),
            ("qname", f"www.{site.hostname}" if rng.random() < 0.3 else site.hostname),
            ("qtype", "A"),
        ]
        query = fortigate(
            190,
            "9900000003",
            "utm",
            "dns",
            "information",
            [("eventtype", "dns-query"), *base, ("action", "pass")],
        )
        self._emit(at, "fortigate", query, "dns")
        answer: list[tuple[str, object]] = [("eventtype", "dns-response"), *base]
        if site.blocked:
            answer += [
                ("cat", site.category),
                ("catdesc", site.catdesc),
                ("action", "block"),
                ("msg", "Domain was blocked by DNS filter"),
            ]
            render = fortigate(188, "9900000004", "utm", "dns", "warning", answer)
        else:
            answer += [
                ("ipaddr", ", ".join(site.addresses)),
                ("cat", site.category),
                ("catdesc", site.catdesc),
                ("action", "pass"),
            ]
            render = fortigate(190, "9900000004", "utm", "dns", "information", answer)
        self._emit(at + timedelta(milliseconds=rng.randint(4, 30)), "fortigate", render, "dns")

    def _admin(self, day: date) -> None:
        """The admin PC (static 172.20.10.37, no owner in the simulation) browses on workdays."""
        if day.weekday() >= 5:
            return
        admin = next(d for d in self.infrastructure if d.key == "admin-pc")
        rng = self._rng("admin", day.isoformat())
        moment = self._at(day, 9)
        while moment < self._at(day, 18):
            moment += timedelta(seconds=rng.expovariate(10 / 3600))
            self._session(None, admin, "172.20.10.37", moment, rng, day)

    # -- attack scenarios (Wave 6 contract §2.7; synthetic, verify on site) -------------------
    #
    # Each scenario adds the lines its rule (R01-R19) fires on, at a fixed site time of every
    # generated day, from the example world's addresses outside the simulated DHCP pools. The
    # FortiGate logids, texts and field names of admin logins, configuration changes, IPS,
    # antivirus, conserve mode, DHCP pool exhaustion and DNS response codes are the rule
    # parameters' defaults, not captures (contract §0.4); a MikroTik line goes nowhere on a
    # FortiGate-only site (no ``core-r1``).

    def _core(self, at: datetime, topics: str, text: str, kind: str) -> None:
        identity = self.sources["core-r1"].identity if "core-r1" in self.sources else "CORE-R1"
        self._emit(at, "core-r1", mikrotik(identity, topics, text), kind)

    def _fg_event(
        self, at: datetime, logid: str, level: str, fields: Sequence[tuple[str, object]], kind: str
    ) -> None:
        pri = {"alert": 185, "warning": 188, "notice": 189}.get(level, 190)
        self._emit(at, "fortigate", fortigate(pri, logid, "event", "system", level, fields), kind)

    def _new_session(self) -> int:
        sessionid = self._next_session
        self._next_session += 1
        return sessionid

    def _lan(self, vlan: int) -> str:
        """The FortiGate interface a client of ``vlan`` arrives on (as :meth:`_session`)."""
        return f"vlan{vlan}" if self._fortigate_gateway(vlan) else "port2"

    def _closed_traffic(
        self,
        at: datetime,
        srcip: str,
        dstip: str,
        dstport: int,
        *,
        vlan: int,
        action: str = "accept",
        proto: int = 6,
        service: str = "HTTPS",
        duration: int = 1,
        sent: int = 0,
        received: int = 0,
        inbound: bool = False,
    ) -> None:
        """One final traffic log (``0000000013``) of a session that ends at ``at``: an accepted
        outbound session is source-NATed like :meth:`_session`; a denied one has no NAT."""
        sessionid = self._new_session()
        srcport = 40000 + sessionid % 9000
        fields: list[tuple[str, object]] = [
            ("srcip", srcip),
            ("srcport", srcport),
            ("srcintf", "wan1" if inbound else self._lan(vlan)),
            ("srcintfrole", "wan" if inbound else "undefined"),
            ("dstip", dstip),
            ("dstport", dstport),
            ("dstintf", "root" if inbound else "wan1"),
            ("dstintfrole", "undefined" if inbound else "wan"),
            ("srccountry", "Reserved"),
            ("dstcountry", "Reserved"),
            ("sessionid", sessionid),
            ("proto", proto),
            ("action", action),
            ("policyid", 0 if action == "deny" else POLICYID),
            ("policytype", "policy"),
            ("poluuid", POLUUID),
            ("policyname", "internet-in" if inbound else "internet-out"),
            ("service", service),
        ]
        if action != "deny" and not inbound:
            start = at - timedelta(seconds=duration)
            transport = _Nat(srcport, start, at, sessionid, self.settings.transip)
            self._nat.append(transport)
            fields += [
                ("trandisp", "snat"),
                ("transip", self.settings.transip),
                ("transport", transport),
            ]
        else:
            fields.append(("trandisp", "noop"))
        fields += [
            ("duration", duration),
            ("sentbyte", sent),
            ("rcvdbyte", received),
            ("sentpkt", max(1, sent // 1400)),
            ("rcvdpkt", received // 1400),
        ]
        render = fortigate(189, "0000000013", "traffic", "forward", "notice", fields)
        self._emit(at, "fortigate", render, "traffic")

    def _utm(
        self,
        at: datetime,
        logid: str,
        subtype: str,
        level: str,
        fields: Sequence[tuple[str, object]],
    ) -> None:
        pri = {"alert": 185, "warning": 188, "notice": 189}.get(level, 190)
        self._emit(at, "fortigate", fortigate(pri, logid, "utm", subtype, level, fields), subtype)

    def _scenario_port_scan(self, day: date) -> None:
        """R01: an internal PC tries 40 ports of a server in 40 seconds, every one denied."""
        start = self._at(day, 10.2)
        for k in range(40):
            self._closed_traffic(
                start + timedelta(seconds=k),
                SCAN_SOURCE,
                SCAN_TARGET,
                20 + k,
                vlan=41,
                action="deny",
                duration=0,
            )

    def _scenario_external_scan(self, day: date) -> None:
        """R02: an outside address tries 120 ports of the WAN address in four minutes."""
        start = self._at(day, 10.35)
        for k in range(120):
            self._closed_traffic(
                start + timedelta(seconds=2 * k),
                EXTERNAL_SCANNER,
                self.settings.transip,
                1000 + k,
                vlan=0,
                action="deny",
                duration=0,
                inbound=True,
            )

    def _scenario_brute_force(self, day: date) -> None:
        """R03: six failed FortiGate admin logins and three failed router logins in 90 s."""
        start = self._at(day, 10.5)
        for k in range(6):
            self._fg_event(
                start + timedelta(seconds=10 * k),
                "0100032002",
                "alert",
                [
                    ("logdesc", "Admin login failed"),
                    ("user", "admin"),
                    ("ui", f"ssh({SCAN_SOURCE})"),
                    ("srcip", SCAN_SOURCE),
                    ("dstip", "172.31.255.1"),
                    ("action", "login"),
                    ("status", "failed"),
                    ("reason", "passwd_invalid"),
                    ("msg", f"Administrator admin login failed from ssh({SCAN_SOURCE})"),
                ],
                "admin-login",
            )
        for k in range(3):
            self._core(
                start + timedelta(seconds=65 + 10 * k),
                "system,error,critical",
                f"login failure for user admin from {SCAN_SOURCE} via winbox",
                "account",
            )

    def _scenario_off_hours_admin(self, day: date) -> None:
        """R04: the router and the FortiGate are logged into at 22:40 (outside work hours)."""
        at = self._at(day, 22 + 40 / 60)
        self._core(
            at,
            "system,info,account",
            "user netops logged in from 172.20.10.37 via winbox",
            "account",
        )
        self._fg_event(
            at + timedelta(seconds=50),
            "0100032001",
            "information",
            [
                ("logdesc", "Admin login successful"),
                ("user", "admin"),
                ("ui", "https(172.20.10.37)"),
                ("srcip", "172.20.10.37"),
                ("dstip", "172.31.255.1"),
                ("action", "login"),
                ("status", "success"),
                ("msg", "Administrator admin logged in successfully from https(172.20.10.37)"),
            ],
            "admin-login",
        )

    def _scenario_ips(self, day: date) -> None:
        """R05: a critical IPS signature against the WAN address."""
        sessionid = self._new_session()
        self._utm(
            self._at(day, 11),
            "9900000006",
            "ips",
            "alert",
            [
                ("eventtype", "signature"),
                ("severity", "critical"),
                ("srcip", "198.51.100.7"),
                ("srcport", 40123),
                ("srcintf", "wan1"),
                ("dstip", self.settings.transip),
                ("dstport", 443),
                ("dstintf", "root"),
                ("sessionid", sessionid),
                ("proto", 6),
                ("action", "dropped"),
                ("policyid", 0),
                ("service", "HTTPS"),
                ("attack", "Synthetic.Signature.Remote.Code.Execution"),
                ("attackid", 99001),
                ("msg", "web_server: Synthetic.Signature.Remote.Code.Execution"),
            ],
        )

    def _scenario_botnet(self, day: date) -> None:
        """R06: a PC contacts a botnet command server (FortiGate IPS botnet detection)."""
        sessionid = self._new_session()
        self._utm(
            self._at(day, 11 + 10 / 60),
            "9900000041",
            "ips",
            "alert",
            [
                ("eventtype", "botnet"),
                ("severity", "critical"),
                ("srcip", BOTNET_HOST),
                ("srcport", 51111),
                ("srcintf", self._lan(41)),
                ("dstip", "198.51.100.66"),
                ("dstport", 8443),
                ("dstintf", "wan1"),
                ("sessionid", sessionid),
                ("proto", 6),
                ("action", "dropped"),
                ("policyid", POLICYID),
                ("attack", "Synthetic.Botnet"),
                ("attackid", 99003),
                ("msg", "Botnet C&C Communication (synthetic)"),
            ],
        )

    def _scenario_malware(self, day: date) -> None:
        """R07: the antivirus blocks a test file a PC downloads."""
        sessionid = self._new_session()
        self._utm(
            self._at(day, 11 + 20 / 60),
            "9900000007",
            "virus",
            "warning",
            [
                ("eventtype", "infected"),
                ("policyid", POLICYID),
                ("sessionid", sessionid),
                ("srcip", "203.0.113.30"),
                ("srcport", 80),
                ("srcintf", "wan1"),
                ("dstip", MALWARE_HOST),
                ("dstport", 51600),
                ("dstintf", self._lan(43)),
                ("proto", 6),
                ("service", "HTTP"),
                ("action", "blocked"),
                ("direction", "incoming"),
                ("filename", "eicar.com"),
                ("virus", "Synthetic.Test.File"),
                ("virusid", 99002),
                ("dtype", "Virus"),
                ("url", "http://downloads.example.net/eicar.com"),
                ("hostname", "downloads.example.net"),
                ("msg", "File is infected."),
            ],
        )

    def _scenario_dns_bypass(self, day: date) -> None:
        """R08: a PC asks an outside DNS server 25 times in 40 minutes."""
        start = self._at(day, 11.5)
        for k in range(25):
            self._closed_traffic(
                start + timedelta(seconds=96 * k),
                SCENARIO_PC,
                OUTSIDE_DNS,
                53,
                vlan=42,
                proto=17,
                service="DNS",
                sent=74,
                received=130,
            )

    def _scenario_blocked_category(self, day: date) -> None:
        """R09: 25 pages of a blocked category in 25 minutes from one PC."""
        start = self._at(day, 13)
        for k in range(25):
            sessionid = self._new_session()
            self._utm(
                start + timedelta(minutes=k),
                "9900000001",
                "webfilter",
                "warning",
                [
                    ("eventtype", "ftgd_blk"),
                    ("policyid", POLICYID),
                    ("sessionid", sessionid),
                    ("srcip", SCENARIO_PC),
                    ("srcport", 52000 + k),
                    ("srcintf", self._lan(42)),
                    ("dstip", "203.0.113.10"),
                    ("dstport", 443),
                    ("dstintf", "wan1"),
                    ("proto", 6),
                    ("service", "HTTPS"),
                    ("hostname", "games.example.com"),
                    ("profile", "WEB-PROFILE"),
                    ("action", "blocked"),
                    ("url", f"/play?id={k}"),
                    ("msg", "URL belongs to a denied category in policy"),
                    ("cat", 901),
                    ("catdesc", "Synthetic Category A"),
                ],
            )

    def _scenario_tor(self, day: date) -> None:
        """R10: application control sees Tor from one PC."""
        sessionid = self._new_session()
        self._utm(
            self._at(day, 13 + 40 / 60),
            "1059028704",
            "app-ctrl",
            "information",
            [
                ("eventtype", "signature"),
                ("appid", 99004),
                ("srcip", SCENARIO_PC),
                ("srcport", 53111),
                ("srcintf", self._lan(42)),
                ("srcintfrole", "undefined"),
                ("dstip", "198.51.100.90"),
                ("dstport", 9001),
                ("dstintf", "wan1"),
                ("dstintfrole", "wan"),
                ("proto", 6),
                ("service", "tcp/9001"),
                ("direction", "outgoing"),
                ("policyid", POLICYID),
                ("sessionid", sessionid),
                ("applist", "APP-PROFILE"),
                ("action", "pass"),
                ("appcat", "Proxy"),
                ("app", "Tor"),
                ("msg", "Proxy: Tor"),
                ("apprisk", "critical"),
            ],
        )

    def _scenario_dhcp(self, at: datetime, ip: str, mac: str, vlan: int, hostname: str) -> None:
        """A DHCP assignment of ``ip`` to ``mac`` by the VLAN's DHCP server."""
        if self._fortigate_gateway(vlan):
            fields: list[tuple[str, object]] = [
                ("logdesc", "DHCP Ack log"),
                ("interface", f"vlan{vlan}"),
                ("dhcp_msg", "Ack"),
                ("mac", mac.upper()),
                ("ip", ip),
                ("lease", LEASE_SECONDS),
                ("hostname", hostname),
                ("msg", "DHCP server sends a DHCPACK"),
            ]
            self._fg_event(at, "0100026001", "information", fields, "fg-dhcp.ack")
        else:
            text = f"dhcp{vlan} assigned {ip} for {mac.upper()} {hostname}"
            self._core(at, "dhcp,info", text, "dhcp.assigned")

    def _scenario_new_device(self, day: date) -> None:
        """R11: a device the network has never seen gets an address (a new MAC every day)."""
        mac = f"00:00:5e:00:53:{0xF0 + day.toordinal() % 16:02x}"
        self._scenario_dhcp(self._at(day, 14), NEW_DEVICE_IP, mac, 43, "YENI-CIHAZ")

    def _scenario_mac_flap(self, day: date) -> None:
        """R12: one address answers with two MACs in turn, four times in 30 minutes."""
        for k in range(4):
            at = self._at(day, 14 + (10 + 10 * k) / 60)
            mac = FLAP_MACS[k % 2]
            if self._fortigate_gateway(43):
                self._scenario_dhcp(at, FLAP_IP, mac, 43, "SENTETIK")
            else:
                self._core(at, "script,info", f"ARP-LOG {FLAP_IP} {mac.upper()} vlan43", "arp")

    def _scenario_big_upload(self, day: date) -> None:
        """R13: one PC sends 1.5 GiB to an outside server in 20 minutes."""
        self._closed_traffic(
            self._at(day, 15 + 20 / 60),
            SCENARIO_PC,
            "203.0.113.150",
            443,
            vlan=42,
            duration=1200,
            sent=3 * 2**29,
            received=12 * 2**20,
        )

    def _scenario_night(self, day: date) -> None:
        """R14: a PC is busy at 02:30 at night (12 MiB sent, 50 MiB received)."""
        self._closed_traffic(
            self._at(day, 2.5),
            SCENARIO_PC,
            "203.0.113.151",
            443,
            vlan=42,
            duration=600,
            sent=12 * 2**20,
            received=50 * 2**20,
        )

    def _scenario_config_change(self, day: date) -> None:
        """R16: an administrator changes a firewall policy and a router filter rule."""
        at = self._at(day, 16)
        self._fg_event(
            at,
            "0100044546",
            "information",
            [
                ("logdesc", "Attribute configured"),
                ("user", "admin"),
                ("ui", "GUI(172.20.10.37)"),
                ("action", "Edit"),
                ("cfgpath", "firewall.policy"),
                ("cfgobj", "12"),
                ("cfgattr", "action[accept->deny]"),
                ("msg", "Edit firewall.policy 12"),
            ],
            "config",
        )
        self._core(
            at + timedelta(seconds=40), "system,info", "filter rule changed by admin", "config"
        )

    def _scenario_fw_health(self, day: date) -> None:
        """R17: the busy CPU of :data:`HOT_SLOTS` (in :meth:`_perf_stats`) and conserve mode."""
        self._fg_event(
            self._at(day, 16.75),
            "9900000042",
            "critical",
            [
                ("logdesc", "Kernel enters memory conserve mode"),
                ("msg", "Kernel enters memory conserve mode (synthetic)"),
            ],
            "conserve",
        )

    def _scenario_pool_empty(self, day: date) -> None:
        """R18: the guest Wi-Fi pool is empty (router line, or the FortiGate's)."""
        at = self._at(day, 17)
        if self._fortigate_gateway(GUEST_WIFI):
            fields: list[tuple[str, object]] = [
                ("logdesc", "DHCP server ran out of IP addresses"),
                ("interface", f"vlan{GUEST_WIFI}"),
                ("msg", "DHCP server ran out of IP addresses (synthetic)"),
            ]
            self._fg_event(at, "9900000043", "warning", fields, "pool-empty")
        else:
            text = (
                f"dhcp{GUEST_WIFI} failed to give out IP address: pool <pool{GUEST_WIFI}> is empty"
            )
            self._core(at, "dhcp,warning", text, "pool-empty")

    def _scenario_dga(self, day: date) -> None:
        """R19: one PC gets 250 NXDOMAIN answers in 25 minutes (generated-looking names)."""
        rng = self._rng("dga", day.isoformat())
        start = self._at(day, 18.5)
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        for k in range(250):
            sessionid = self._new_session()
            name = "".join(rng.choice(alphabet) for _ in range(12)) + ".example"
            self._utm(
                start + timedelta(seconds=6 * k),
                "9900000004",
                "dns",
                "information",
                [
                    ("eventtype", "dns-response"),
                    ("policyid", POLICYID),
                    ("sessionid", sessionid),
                    ("srcip", SCENARIO_PC),
                    ("srcport", 54000 + k),
                    ("srcintf", self._lan(42)),
                    ("dstip", DNS_SERVER),
                    ("dstport", 53),
                    ("dstintf", self._lan(SERVER_VLAN)),
                    ("proto", 17),
                    ("qname", name),
                    ("qtype", "A"),
                    ("rcode", "NXDOMAIN"),
                    ("action", "pass"),
                ],
            )


# -- outputs --------------------------------------------------------------------------------------


def senders(settings: Settings, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Source name → sender address written into the spool (or bound when sending)."""
    found = {source.name: source.address for source in settings.sources}
    found.update(overrides or {})
    return found


def spool_lines(
    outputs: Iterable[DayOutput], *, addresses: Mapping[str, str]
) -> dict[tuple[str, date], list[bytes]]:
    """The spool lines per ``(source, receive day)``, in receive order — what the collector
    would have written (:func:`arcilogger_core.rawlog.format_line`)."""
    ordered = sorted(
        (message for output in outputs for message in output.messages),
        key=lambda m: (m.received, m.order),
    )
    lines: dict[tuple[str, date], list[bytes]] = {}
    for message in ordered:
        received = message.received.astimezone(SITE_TZ)
        line = format_line(received, addresses[message.source], message.wire)
        lines.setdefault((message.source, received.date()), []).append(line)
    return lines


def in_use(layout: Layout) -> list[str]:
    """Signs that ``layout`` is a data folder ARCILOGGER works in (not one this tool made)."""
    found = []
    if layout.vault.is_dir() and sealed_days(layout):
        found.append("vault/ (mühürlü günler / sealed days)")
    if layout.state.is_dir() and any(layout.state.iterdir()):
        found.append("state/")
    if layout.spool.is_dir() and any(path.is_file() for path in layout.spool.rglob("*")):
        found.append("spool/")
    return found


def write_spool(
    data_dir: Path,
    outputs: Iterable[DayOutput],
    *,
    addresses: Mapping[str, str],
    append: bool = False,
    not_production: bool = False,
) -> dict[Path, int]:
    """Write ``spool/<source>/<receive day>.log`` (:func:`spool_lines`); lines per file.

    Raw logs are evidence: demo data must never be mixed into a real spool by accident. So this
    refuses a data folder that is in use (:func:`in_use`) unless it carries this tool's marker
    (:data:`DEMO_MARKER`) or ``not_production`` is given, refuses to add to an existing file
    unless ``append``, and leaves the marker in the folder before it writes anything.
    """
    layout = Layout(data_dir)
    marker = layout.root / DEMO_MARKER
    if not marker.exists() and not not_production:
        used = in_use(layout)
        if used:
            raise LoadgenError(
                f"{layout.root} kullanımda olan bir veri klasörü gibi görünüyor "
                f"({', '.join(used)}): sentetik satırlar delil olarak mühürlenebilir. Boş bir "
                "klasör seçin (test kurulumuysa --i-know-this-is-not-production). / "
                f"{layout.root} looks like a data folder in use: synthetic lines could be sealed "
                "as evidence. Use an empty folder (or --i-know-this-is-not-production on a test "
                "installation)."
            )
    files = {
        layout.spool_file(source, day): data
        for (source, day), data in spool_lines(outputs, addresses=addresses).items()
    }
    for path in files:
        if path.exists() and not append:
            raise FileExistsError(f"{path} exists (use --append to add to it)")
    if not marker.exists():
        layout.root.mkdir(parents=True, exist_ok=True)
        marker.write_text(DEMO_TEXT, encoding="utf-8")
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(b"".join(data))
    return {path: len(data) for path, data in files.items()}


def frame(message: bytes) -> bytes:
    """RFC 6587 octet counting (FortiGate "reliable" mode over TCP)."""
    return str(len(message)).encode("ascii") + b" " + message


Protocol = Literal["udp", "tcp", "split"]


def protocol_of(protocol: Protocol, source: Source) -> Literal["udp", "tcp"]:
    """``split``: the FortiGate over TCP (reliable mode), the MikroTiks over UDP."""
    if protocol == "split":
        return "tcp" if source.type == "fortigate" else "udp"
    return protocol


class Sender:
    """Sends messages to a collector: one socket per source (bound to its own local address
    when ``bind`` names one — the collector tells sources apart by sender IP)."""

    def __init__(
        self,
        host: str,
        *,
        udp_port: int | None,
        tcp_port: int | None,
        protocol: Protocol = "udp",
        sources: Iterable[Source] = DEFAULT_SOURCES,
        bind: Mapping[str, str] | None = None,
    ) -> None:
        self.host = host
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.protocol = protocol
        self.sources = {source.name: source for source in sources}
        self.bind = dict(bind or {})
        self._sockets: dict[str, tuple[socket.socket, Literal["udp", "tcp"]]] = {}

    def _socket(self, name: str) -> tuple[socket.socket, Literal["udp", "tcp"]]:
        found = self._sockets.get(name)
        if found is not None:
            return found
        kind = protocol_of(self.protocol, self.sources[name])
        port = self.udp_port if kind == "udp" else self.tcp_port
        if port is None:
            raise ValueError(f"no {kind.upper()} port for source {name}")
        family = socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM
        sock = socket.socket(socket.AF_INET, family)
        try:
            local = self.bind.get(name)
            if local:
                sock.bind((local, 0))
            if kind == "tcp":
                sock.connect((self.host, port))
        except OSError:
            sock.close()
            raise
        self._sockets[name] = (sock, kind)
        return sock, kind

    def send(self, source: str, data: bytes) -> None:
        sock, kind = self._socket(source)
        if kind == "udp":
            sock.sendto(data, (self.host, self.udp_port or 0))
        else:
            sock.sendall(frame(data))

    def close(self) -> None:
        for sock, _ in self._sockets.values():
            sock.close()
        self._sockets.clear()

    def __enter__(self) -> Sender:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def send_messages(
    messages: Iterable[Message],
    sender: Sender,
    *,
    rate: float,
    live: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Send at ``rate`` messages per second (0: as fast as possible); returns the count.

    ``live`` stamps every message with the current time instead of its simulated time (a load
    test of today; the parsers ignore device times more than a day from the receive time).
    """
    count = 0
    started = time.monotonic()
    for message in messages:
        sender.send(message.source, message.render(clock()) if live else message.wire)
        count += 1
        if rate > 0:
            delay = started + count / rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    return count


def truth_records(outputs: Iterable[DayOutput]) -> Iterator[dict[str, Any]]:
    """Ground truth as JSON-ready dicts (times in UTC ISO)."""
    for output in outputs:
        for session in output.sessions:
            yield {"type": "session", "note": SYNTHETIC_NOTE, **_plain(asdict(session))}
        for lease in output.leases:
            yield {"type": "lease", "note": SYNTHETIC_NOTE, **_plain(asdict(lease))}


def _plain(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value
        for key, value in values.items()
    }


# -- command line ---------------------------------------------------------------------------------


def _pairs(values: Sequence[str]) -> dict[str, str]:
    found = {}
    for value in values:
        name, _, address = value.partition("=")
        if not name or not address:
            raise SystemExit(f"expected NAME=ADDRESS, got {value!r}")
        try:
            found[name] = str(ipaddress.ip_address(address))
        except ValueError as exc:
            raise SystemExit(f"{value!r}: {exc}") from exc
    return found


def parse_egress(value: str) -> Egress:
    """``NAME:TRANSIP:DSTINTF:SHARE`` (``--egress``); :func:`check_egresses` checks the set."""
    parts = value.split(":")
    if len(parts) != 4 or not all(parts):
        raise argparse.ArgumentTypeError(f"expected NAME:TRANSIP:DSTINTF:SHARE, got {value!r}")
    name, transip, dstintf, share = parts
    try:
        address = ipaddress.ip_address(transip)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r}: {exc}") from exc
    if not share.isdigit():
        raise argparse.ArgumentTypeError(f"{value!r}: SHARE is a whole percentage")
    return Egress(name, str(address), dstintf, int(share))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcilogger_loadgen.py",
        description=(
            "ARCILOGGER yük üreteci / load generator: FortiGate + MikroTik syslog of a simulated "
            f"network, deterministic per --seed ({SYNTHETIC_NOTE}; spec 17)."
        ),
    )
    parser.add_argument("--seed", type=int, default=Settings.seed, help="deterministic seed")
    parser.add_argument("--users", type=int, default=Settings.users, help="people (default 120)")
    parser.add_argument(
        "--intensity",
        type=float,
        default=Settings.intensity,
        help="browsing sessions per work hour on each PC (default 30; phones a fifth)",
    )
    parser.add_argument(
        "--day", type=date.fromisoformat, default=None, help="first day YYYY-MM-DD (default today)"
    )
    parser.add_argument("--days", type=int, default=1, help="number of days (default 1)")
    parser.add_argument(
        "--transip", default=None, help=f"FortiGate SNAT address (default {DEFAULT_TRANSIP})"
    )
    parser.add_argument(
        "--interim-seconds",
        type=int,
        default=DEFAULT_INTERIM_SECONDS,
        help="interim traffic log interval (a FortiGate setting: verify on site)",
    )
    parser.add_argument("--only", action="append", default=[], help="only this source (repeat)")
    parser.add_argument(
        "--dhcp-server",
        choices=("mikrotik", "fortigate", "both"),
        default=Settings.dhcp_server,
        help=(
            "gateway and DHCP server of the VLANs: the core router (default), the FortiGate (a "
            "FortiGate-only site: FortiGate DHCP lines, no MikroTik lines), or both (FortiGate "
            "for the Wi-Fi VLANs 60/61)"
        ),
    )
    parser.add_argument(
        "--srcmac",
        action="store_true",
        help="traffic lines carry srcmac (the client's MAC behind the FortiGate, the core "
        "router's behind the router)",
    )
    parser.add_argument(
        "--egress",
        action="append",
        default=[],
        type=parse_egress,
        metavar="NAME:TRANSIP:DSTINTF:SHARE",
        help="an internet exit (repeat; shares sum to 100), e.g. hat-1:100.64.12.9:wan1:60; "
        "default: every session by wan1 with --transip",
    )
    parser.add_argument(
        "--scenario",
        type=parse_scenarios,
        default=(),
        metavar="NAME[,NAME…]|all",
        help="add fictional attack traffic for the security rules (synthetic): "
        + ", ".join(f"{name} ({rule})" for name, rule in SCENARIOS.items())
        + ", all",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    spool = commands.add_parser("spool", help="write raw spool files exactly as the collector")
    spool.add_argument("--data-dir", type=Path, required=True, help="ARCILOGGER data folder")
    spool.add_argument("--append", action="store_true", help="add to existing spool files")
    spool.add_argument(
        "--i-know-this-is-not-production",
        dest="not_production",
        action="store_true",
        help="write into a data folder that already holds data (a test installation only)",
    )
    spool.add_argument(
        "--source-address",
        action="append",
        default=[],
        metavar="NAME=IP",
        help="sender address of a source in the spool (default: the example network's)",
    )
    send = commands.add_parser("send", help="send over UDP and/or TCP (octet counting)")
    send.add_argument("--host", default="127.0.0.1")
    send.add_argument("--udp-port", type=int, default=None)
    send.add_argument("--tcp-port", type=int, default=None)
    send.add_argument(
        "--protocol",
        choices=("udp", "tcp", "split"),
        default="udp",
        help="split: FortiGate over TCP, MikroTik over UDP",
    )
    send.add_argument("--rate", type=float, default=1000.0, help="messages per second (0: max)")
    send.add_argument(
        "--bind",
        action="append",
        default=[],
        metavar="NAME=IP",
        help="local address of a source, e.g. core-r1=127.0.0.2 (sources are told apart by IP)",
    )
    send.add_argument("--live", action="store_true", help="stamp messages with the current time")
    commands.add_parser("truth", help="print the ground truth (sessions, leases) as JSON lines")
    commands.add_parser("stats", help="count messages per source and kind")
    return parser


def _utf8_output() -> None:
    """Names and departments are Turkish: write UTF-8 whatever the console's code page (a
    Windows server with an English locale would use cp1252 and fail on ı, ş, İ)."""
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "replace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors=errors)


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_output()
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except LoadgenError as exc:
        print(f"HATA / ERROR: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.transip is not None and args.egress:
        raise LoadgenError(
            "--transip ve --egress birlikte kullanılamaz: her çıkış kendi TRANSIP adresini verir. "
            "/ --transip and --egress exclude each other: each exit gives its own TRANSIP."
        )
    settings = Settings(
        seed=args.seed,
        users=args.users,
        intensity=args.intensity,
        transip=str(ipaddress.ip_address(args.transip or DEFAULT_TRANSIP)),
        interim_seconds=args.interim_seconds,
        dhcp_server=args.dhcp_server,
        srcmac=args.srcmac,
        egresses=tuple(args.egress),
        scenarios=args.scenario,
    )
    if args.only:
        wanted = set(args.only)
        settings = replace(settings, sources=tuple(s for s in settings.sources if s.name in wanted))
    first = args.day or site_day(datetime.now(UTC))
    outputs = list(Simulation(settings).run(first, first + timedelta(days=args.days - 1)))
    if args.command == "spool":
        addresses = senders(settings, _pairs(args.source_address))
        try:
            written = write_spool(
                args.data_dir,
                outputs,
                addresses=addresses,
                append=args.append,
                not_production=args.not_production,
            )
        except FileExistsError as exc:
            print(f"HATA / ERROR: {exc}", file=sys.stderr)
            return 1
        for path, count in sorted(written.items()):
            print(f"{count:>9}  {path}")
        print(f"{sum(written.values())} lines ({SYNTHETIC_NOTE})")
    elif args.command == "send":
        messages = [message for output in outputs for message in output.messages]
        with Sender(
            args.host,
            udp_port=args.udp_port,
            tcp_port=args.tcp_port,
            protocol=args.protocol,
            sources=settings.sources,
            bind=_pairs(args.bind),
        ) as sender:
            count = send_messages(messages, sender, rate=args.rate, live=args.live)
        print(f"{count} messages sent ({SYNTHETIC_NOTE})")
    elif args.command == "truth":
        for record in truth_records(outputs):
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    else:
        counts: dict[tuple[str, str], int] = {}
        for output in outputs:
            for message in output.messages:
                key = (message.source, message.kind)
                counts[key] = counts.get(key, 0) + 1
        for (source, kind), count in sorted(counts.items()):
            print(f"{source:<10} {kind:<16} {count:>9}")
        sessions = sum(len(output.sessions) for output in outputs)
        print(f"total {sum(counts.values())} messages, {sessions} sessions ({SYNTHETIC_NOTE})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # e.g. "truth | head": the reader stopped early
        sys.stderr.close()
        sys.exit(0)
