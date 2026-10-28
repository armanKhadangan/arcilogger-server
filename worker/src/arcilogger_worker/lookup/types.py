"""Types of the 5651 lookup engine (build specification 2.3, 9.1-9.5).

The API stores :meth:`LookupRequest.to_dict` in ``lookups.query`` and
:meth:`LookupOutcome.to_dict` in ``lookups.outcome``; the PDF report and the evidence package
read them back with ``from_dict``. Every ``to_dict`` returns plain JSON (str, int, bool, None,
lists, objects): times are ISO-8601 strings in UTC, there are **no floats** (scores are integers
0-100, time distances integer milliseconds), so the same data can also go into canonical JSON.

Vocabulary:

* **exact** — the request's public IP is the FortiGate's own address: a ``direct`` internet
  exit's public address, an exit's NAT address (``transip``) or an address the logs show as the
  FortiGate's NAT address near the moment (setting ``nat.egresses``, ADR-0016), so
  ``transip:transport`` identifies the session.
* **candidates** — double NAT (2.3): the ISP router's translation is unknown, so sessions in
  the time window (± tolerance) that left through the exit the public IP resolved to,
  optionally to the requested destination, are ranked.
* **internal** — the request names an internal IP or a MAC directly (no NAT step).

**NAT matching rule** (spec 9.3; ``fg_traffic.time`` is the time of the log line, i.e. the
session end or an interim update): a row's session interval is ``[time - duration s, time]``
(rows without ``duration`` are the instant ``time``). A row matches the request when that
interval overlaps ``[at - tolerance, at + tolerance]``; ``time_distance_ms`` is 0 inside the
interval, else the distance to its nearer end. Queries must bound the scan:
``time BETWEEN at - tolerance AND at + tolerance + max_session_seconds`` (setting
``lookup.max_session_seconds``), so the ``(transip, transport, time)`` index range stays small.

**IP conflicts** (spec 9.1): when two MACs held the same IP at the same moment,
:func:`attribute` returns the most likely chain with the others in
:attr:`AttributionChain.alternatives`; such a chain **and every alternative** is ``low``
confidence and carries the ``ip_conflict`` explanation, so a report can never silently name one
person nor present another device as more reliable (step confidences stay as they are).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast

from arcilogger_core.iputil import canonical_ip
from arcilogger_core.layout import Layout
from arcilogger_db.egress import Egress, EgressMode, PortPreservation, parse_egresses

Confidence = Literal["high", "medium", "low"]
LookupMode = Literal["exact", "candidates", "internal", "none"]
MatchKind = Literal["exact", "candidate"]
#: How a match met the requested destination: ``ip_port`` / ``ip`` (IP, no port requested) /
#: ``ip_other_port`` (the requested IP, another port); ``domain`` (the session's own web/app log
#: or the client's DNS answer names it) / ``domain_other_port``; ``domain_address`` (only the
#: address is known for the name, from another client's logs — possibly a shared CDN address);
#: ``domain_unverified`` (exact mode, only a domain was requested: the NAT port identifies the
#: session, but no DNS/web/app log ties its destination to the name — not shown, not refuted);
#: ``port`` (only a destination port was requested, and it matched); ``not_requested``;
#: ``none``. Outcomes stored before the detailed kinds existed use only the coarse ones.
DestinationMatch = Literal[
    "ip_port",
    "ip",
    "ip_other_port",
    "domain",
    "domain_other_port",
    "domain_address",
    "domain_unverified",
    "port",
    "none",
    "not_requested",
]
DepartmentSource = Literal["person", "vlan", "none"]
#: Where a chain's person comes from: ``history`` — the device's assignment valid at the moment
#: (``device_assignments``); ``current`` — the device's owner when the lookup ran, because no
#: assignment history exists for the device (outcomes stored before the history was kept).
PersonLink = Literal["history", "current"]
#: Where a chain's MAC comes from (ADR-0016): ``lease`` — a lease of the lease timeline;
#: ``srcmac`` — the srcmac field of the FortiGate session record on an interface listed in
#: ``lookup.srcmac_interfaces`` (low confidence, partial evidence, never a lease).
MacSource = Literal["lease", "srcmac"]
#: The explanations that make a chain with alternatives: an IP conflict of leases (spec 9.1)
#: and a srcmac that names another MAC than the lease (D10).
CONFLICT_KEYS = frozenset({"ip_conflict", "srcmac_mismatch"})
#: How the requested public IP was resolved against the internet exits (``nat.egresses``,
#: ADR-0016): ``public`` (an exit's public address), ``transip`` (an exit's NAT address),
#: ``fallback`` (a direct or NAT address whose log check failed), ``observed`` (an address the
#: logs show as the FortiGate's own NAT address near the moment), ``public_unknown`` (searched
#: in the exits whose public address is unknown or may change), ``unconfigured`` (no exits:
#: one implicit double-NAT exit), ``no_egress`` (no exit claims it: nobody is listed).
EgressResolution = Literal[
    "public", "transip", "fallback", "observed", "public_unknown", "unconfigured", "no_egress"
]

CONFIDENCE_ORDER: Final[Mapping[str, int]] = {"high": 3, "medium": 2, "low": 1}
PROTOCOLS: Final[Mapping[str, int]] = {"icmp": 1, "tcp": 6, "udp": 17}
MAX_TOLERANCE_SECONDS: Final = 3600
_MAC = re.compile(r"^[0-9a-f]{12}$")

#: Standard explanation texts (key → Turkish, English). The engine may add more.
EXPLANATIONS: Final[Mapping[str, tuple[str, str]]] = {
    "double_nat": (
        "Kesin eşleşme için ISS modeminin NAT kaydı gerekir; aşağıda zaman ve hedefe göre "
        "adaylar listelenmiştir.",
        "An exact match needs the ISP router's NAT record; candidates ranked by time and "
        "destination are listed below.",
    ),
    "exact_nat": (
        "Genel IP adresi FortiGate'in kendi adresi olduğu için oturum kesin olarak eşleşti.",
        "The public IP is the FortiGate's own address, so the session matched exactly.",
    ),
    "no_match": (
        "Bu zaman aralığında eşleşen oturum bulunamadı.",
        "No matching session was found in this time window.",
    ),
    "only_in_vault": (
        "Bu tarihin kayıtları yalnızca mühürlü arşivde; sorgu için arşivden yeniden yükleyin.",
        "Records of this date are only in the sealed vault; reload them from the vault first.",
    ),
    "ip_conflict": (
        "Bu IP adresi bu zamanda birden fazla MAC adresinde görünüyor (IP çakışması); olası "
        "tüm cihazlar listelenmiştir, tek bir cihaza kesin atama yapılamaz.",
        "This IP address was held by more than one MAC address at this time (IP conflict); all "
        "possible devices are listed and no single device can be named with certainty.",
    ),
    "guest_vlan": (
        "Bu adres misafir ağı olarak işaretli bir VLAN'da: misafir ağı FortiGate'ten değil başka "
        "bir çıkıştan (ör. ayrı bir internet hattı veya VPN tüneli) çıkar; FortiGate kaydı "
        "yoktur, yalnızca DHCP/ARP kayıtları vardır.",
        "This address is in a VLAN marked as a guest network: the guest network leaves through "
        "another exit (e.g. a separate internet line or a VPN tunnel), not the FortiGate; there "
        "are no FortiGate records, only DHCP/ARP.",
    ),
}


# -- helpers --------------------------------------------------------------------------------------


def _aware(value: datetime, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _ip(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        return canonical_ip(value)
    except ValueError as exc:
        raise ValueError(f"{name}: invalid IP address {value!r}") from exc


def _port(value: int | None, name: str) -> int | None:
    if value is not None and not 0 <= value <= 65535:
        raise ValueError(f"{name} must be 0-65535")
    return value


def normalize_mac(value: str) -> str:
    """``02-00-5E-00-53-46`` / ``0200.5e00.5346`` → ``02:00:5e:00:53:46`` (PostgreSQL style)."""
    digits = re.sub(r"[:.\-]", "", value.strip()).lower()
    if not _MAC.match(digits):
        raise ValueError(f"invalid MAC address {value!r}")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def protocol_number(value: str | int | None) -> int | None:
    """``"tcp"`` → 6, ``"17"`` → 17, ``None`` → ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("protocol must be a number or tcp/udp/icmp")
    if isinstance(value, int):
        number = value
    elif value.strip().lower() in PROTOCOLS:
        number = PROTOCOLS[value.strip().lower()]
    else:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"unknown protocol {value!r} (number, tcp, udp or icmp)") from exc
    if not 0 <= number <= 255:
        raise ValueError("protocol must be 0-255")
    return number


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_encode(item) for item in value]
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, float):
        raise TypeError("lookup data must not contain floats (use integers)")
    return value


def _dt(value: Any) -> datetime:
    return _aware(datetime.fromisoformat(str(value)), "time")


def _opt_dt(value: Any) -> datetime | None:
    return None if value is None else _dt(value)


def _opt_date(value: Any) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


# -- request --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LookupRequest:
    """What an official request asks (any combination; ``at`` is required)."""

    at: datetime
    public_ip: str | None = None
    public_port: int | None = None
    #: IP protocol number (6 TCP, 17 UDP, 1 ICMP). ``"tcp"`` / ``"6"`` are accepted and stored as
    #: the number (:func:`protocol_number`), so it compares with ``fg_traffic.proto``.
    protocol: int | None = None
    dest_ip: str | None = None
    dest_port: int | None = None
    dest_domain: str | None = None
    #: ± seconds around ``at``; ``None`` = setting ``lookup.tolerance_seconds``.
    tolerance_seconds: int | None = None
    internal_ip: str | None = None
    mac: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "at", _aware(self.at, "at"))
        set_(self, "public_ip", _ip(self.public_ip, "public_ip"))
        set_(self, "dest_ip", _ip(self.dest_ip, "dest_ip"))
        set_(self, "internal_ip", _ip(self.internal_ip, "internal_ip"))
        _port(self.public_port, "public_port")
        _port(self.dest_port, "dest_port")
        set_(self, "protocol", protocol_number(self.protocol))
        if self.tolerance_seconds is not None and not (
            0 <= self.tolerance_seconds <= MAX_TOLERANCE_SECONDS
        ):
            raise ValueError(f"tolerance_seconds must be 0-{MAX_TOLERANCE_SECONDS}")
        if self.mac is not None:
            set_(self, "mac", normalize_mac(self.mac))
        if self.dest_domain is not None:
            domain = self.dest_domain.strip().lower().rstrip(".")
            set_(self, "dest_domain", domain or None)
        if not (self.public_ip or self.internal_ip or self.mac or self.dest_ip or self.dest_domain):
            raise ValueError(
                "a lookup needs a public IP, an internal IP, a MAC address or a destination"
            )

    @property
    def is_internal(self) -> bool:
        return self.public_ip is None and (self.internal_ip is not None or self.mac is not None)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _encode(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LookupRequest:
        return cls(
            at=_dt(data["at"]),
            public_ip=data.get("public_ip"),
            public_port=data.get("public_port"),
            protocol=protocol_number(data.get("protocol")),
            dest_ip=data.get("dest_ip"),
            dest_port=data.get("dest_port"),
            dest_domain=data.get("dest_domain"),
            tolerance_seconds=data.get("tolerance_seconds"),
            internal_ip=data.get("internal_ip"),
            mac=data.get("mac"),
        )


# -- evidence -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawRef:
    """Line ``line`` (1-based) of ``spool/<source>/<day>.log`` = the same line of the sealed
    ``vault/<Y>/<M>/<D>/<source>.log.gz`` after decompression; ``offset`` = byte offset."""

    source: str
    day: date
    line: int
    offset: int | None = None

    def spool_path(self, layout: Layout) -> Path:
        return layout.spool_file(self.source, self.day)

    def vault_path(self, layout: Layout) -> Path:
        return layout.vault_day(self.day) / f"{self.source}.log.gz"

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _encode(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RawRef:
        return cls(
            source=str(data["source"]),
            day=date.fromisoformat(str(data["day"])),
            line=int(data["line"]),
            offset=None if data.get("offset") is None else int(data["offset"]),
        )


@dataclass(frozen=True)
class EvidenceRef:
    """A database row that supports a step, with the raw line it came from."""

    table: str
    row_id: int | None
    time: datetime
    raw: RawRef | None
    #: Short neutral description, e.g. ``dhcp42 assigned 172.20.42.57 for 00:00:5e:…``.
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", _aware(self.time, "time"))

    def to_dict(self) -> dict[str, Any]:
        """``{"table", "row_id", "time", "raw": {source, day, line, offset} | null, "summary"}``
        — also the element format of ``leases.evidence``."""
        return cast(dict[str, Any], _encode(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceRef:
        raw = data.get("raw")
        return cls(
            table=str(data["table"]),
            row_id=data.get("row_id"),
            time=_dt(data["time"]),
            raw=None if raw is None else RawRef.from_dict(raw),
            summary=str(data.get("summary", "")),
        )


def _evidence(items: Any) -> tuple[EvidenceRef, ...]:
    return tuple(EvidenceRef.from_dict(item) for item in items or ())


@dataclass(frozen=True)
class Explanation:
    """A sentence for the result page and the PDF, in Turkish and English."""

    key: str
    text_tr: str
    text_en: str
    params: Mapping[str, str | int | None] = field(default_factory=dict)

    @classmethod
    def standard(cls, key: str, **params: str | int | None) -> Explanation:
        text_tr, text_en = EXPLANATIONS[key]
        return cls(key=key, text_tr=text_tr, text_en=text_en, params=params)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Explanation:
        return cls(
            key=str(data["key"]),
            text_tr=str(data["text_tr"]),
            text_en=str(data["text_en"]),
            params=dict(data.get("params") or {}),
        )


# -- attribution chain ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaseInfo:
    lease_id: int | None
    ip: str
    mac: str
    vlan: int | None
    hostname: str | None
    start: datetime
    end: datetime | None
    #: dhcp | timeout | superseded | arp | open
    end_reason: str
    confidence: Confidence
    evidence: tuple[EvidenceRef, ...] = ()
    #: The table the lease rests on: ``mt_dhcp``, ``fg_dhcp`` or ``mt_arp`` (``None`` in
    #: outcomes stored before ADR-0016).
    source_table: str | None = None
    #: The lease time the FortiGate logged (``fg_dhcp`` leases only), seconds.
    lease_seconds: int | None = None
    #: The lease's own newest DHCP record at or before the chain's moment: never the line that
    #: ended it (another device's ACK) and never a renewal logged after the moment.
    last_dhcp_evidence: datetime | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LeaseInfo:
        return cls(
            lease_id=data.get("lease_id"),
            ip=str(data["ip"]),
            mac=str(data["mac"]),
            vlan=data.get("vlan"),
            hostname=data.get("hostname"),
            start=_dt(data["start"]),
            end=_opt_dt(data.get("end")),
            end_reason=str(data["end_reason"]),
            confidence=data["confidence"],
            evidence=_evidence(data.get("evidence")),
            source_table=data.get("source_table"),
            lease_seconds=data.get("lease_seconds"),
            last_dhcp_evidence=_opt_dt(data.get("last_dhcp_evidence")),
        )


@dataclass(frozen=True)
class DeviceInfo:
    mac: str
    name: str | None
    hostnames: tuple[str, ...]
    vendor: str | None
    randomized: bool
    type: str | None
    infrastructure: bool
    confidence: Confidence
    evidence: tuple[EvidenceRef, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceInfo:
        return cls(
            mac=str(data["mac"]),
            name=data.get("name"),
            hostnames=tuple(data.get("hostnames") or ()),
            vendor=data.get("vendor"),
            randomized=bool(data.get("randomized", False)),
            type=data.get("type"),
            infrastructure=bool(data.get("infrastructure", False)),
            confidence=data["confidence"],
            evidence=_evidence(data.get("evidence")),
        )


@dataclass(frozen=True)
class PersonInfo:
    person_id: int
    name: str
    #: The person's department **today** (no department history is kept, ADR-0008).
    department: str | None
    title: str | None
    #: The device → person link is an administrative assignment, not log evidence.
    confidence: Confidence
    evidence: tuple[EvidenceRef, ...] = ()
    #: ``[assigned_from, assigned_to)`` of the assignment (``link="history"``); ``assigned_to``
    #: ``None`` while it lasts. Both ``None`` for a ``current`` link.
    assigned_from: datetime | None = None
    assigned_to: datetime | None = None
    #: The defaults describe outcomes stored before the history existed: the current owner.
    link: PersonLink = "current"

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if self.assigned_from is not None:
            set_(self, "assigned_from", _aware(self.assigned_from, "assigned_from"))
        if self.assigned_to is not None:
            set_(self, "assigned_to", _aware(self.assigned_to, "assigned_to"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PersonInfo:
        return cls(
            person_id=int(data["person_id"]),
            name=str(data["name"]),
            department=data.get("department"),
            title=data.get("title"),
            confidence=data["confidence"],
            evidence=_evidence(data.get("evidence")),
            assigned_from=_opt_dt(data.get("assigned_from")),
            assigned_to=_opt_dt(data.get("assigned_to")),
            link=data.get("link") or "current",
        )


@dataclass(frozen=True)
class AttributionChain:
    """``internal IP + T → lease (MAC) → device → person / department`` (spec 9.4)."""

    internal_ip: str
    at: datetime
    vlan: int | None
    vlan_name: str | None
    lease: LeaseInfo | None
    device: DeviceInfo | None
    person: PersonInfo | None
    #: Person's department if linked, else the VLAN's department (spec 9.2).
    department: str | None
    department_source: DepartmentSource
    #: Weakest step of the chain (always ``low`` when there are :attr:`alternatives`).
    confidence: Confidence
    explanations: tuple[Explanation, ...] = ()
    #: IP conflict (spec 9.1): the other chains for the same IP at the same moment (another MAC
    #: held an overlapping lease, or, ADR-0016 D10, the session record's srcmac names another
    #: MAC than the lease). Requires ``confidence="low"`` and the ``ip_conflict`` (or
    #: ``srcmac_mismatch``) explanation; the engine marks every alternative the same way (not
    #: enforced here, so lookups stored earlier still load); alternatives have no alternatives
    #: of their own.
    alternatives: tuple[AttributionChain, ...] = ()
    #: Where the MAC comes from (``srcmac``: partial evidence, ``low``; ADR-0016).
    mac_source: MacSource = "lease"

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _aware(self.at, "at"))
        if self.alternatives:
            if self.confidence != "low":
                raise ValueError("a chain with alternatives (IP conflict) must be low confidence")
            if not any(e.key in CONFLICT_KEYS for e in self.explanations):
                raise ValueError(
                    "a chain with alternatives needs the ip_conflict explanation "
                    "(srcmac_mismatch for a srcmac conflict)"
                )
            for other in self.alternatives:
                if other.alternatives:
                    raise ValueError("alternatives must not have alternatives of their own")
                if other.internal_ip != self.internal_ip:
                    raise ValueError("alternatives must be chains of the same internal IP")

    @property
    def conflict(self) -> bool:
        """More than one MAC held the IP at that moment (see :attr:`alternatives`)."""
        return bool(self.alternatives)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _encode(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AttributionChain:
        lease, device, person = data.get("lease"), data.get("device"), data.get("person")
        return cls(
            internal_ip=str(data["internal_ip"]),
            at=_dt(data["at"]),
            vlan=data.get("vlan"),
            vlan_name=data.get("vlan_name"),
            lease=None if lease is None else LeaseInfo.from_dict(lease),
            device=None if device is None else DeviceInfo.from_dict(device),
            person=None if person is None else PersonInfo.from_dict(person),
            department=data.get("department"),
            department_source=data["department_source"],
            confidence=data["confidence"],
            explanations=tuple(Explanation.from_dict(e) for e in data.get("explanations") or ()),
            alternatives=tuple(
                AttributionChain.from_dict(a) for a in data.get("alternatives") or ()
            ),
            mac_source=data.get("mac_source") or "lease",
        )


def weakest(levels: Sequence[Confidence]) -> Confidence:
    """Lowest confidence of a chain (``high`` when empty)."""
    return min(levels, key=lambda level: CONFIDENCE_ORDER[level], default="high")


# -- NAT matches and outcome ----------------------------------------------------------------------


@dataclass(frozen=True)
class TrafficInfo:
    """The FortiGate session behind a match (from ``fg_traffic``)."""

    sessionid: int | None
    proto: int | None
    srcip: str
    srcport: int | None
    dstip: str | None
    dstport: int | None
    transip: str | None
    transport: int | None
    #: Session start (end minus duration, or the first interim log) and end/last log.
    start: datetime
    end: datetime
    evidence: tuple[EvidenceRef, ...] = ()
    #: The session's FortiGate interfaces (``None`` in outcomes stored before ADR-0016).
    srcintf: str | None = None
    dstintf: str | None = None
    #: The session record's srcmac, only for interfaces listed in ``lookup.srcmac_interfaces``.
    srcmac: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrafficInfo:
        return cls(
            sessionid=data.get("sessionid"),
            proto=data.get("proto"),
            srcip=str(data["srcip"]),
            srcport=data.get("srcport"),
            dstip=data.get("dstip"),
            dstport=data.get("dstport"),
            transip=data.get("transip"),
            transport=data.get("transport"),
            start=_dt(data["start"]),
            end=_dt(data["end"]),
            evidence=_evidence(data.get("evidence")),
            srcintf=data.get("srcintf"),
            dstintf=data.get("dstintf"),
            srcmac=data.get("srcmac"),
        )


@dataclass(frozen=True)
class NatMatch:
    kind: MatchKind
    #: 0-100, higher is better (exact matches 100).
    score: int
    #: Distance between the requested time and the session's [start, end] (0 = inside).
    time_distance_ms: int
    destination_match: DestinationMatch
    traffic: TrafficInfo
    chain: AttributionChain | None
    #: The internet exit of the match, from its session row nearest the requested moment.
    egress: str | None = None
    #: The sources of its rows that are not configured FortiGate senders (marked, never hidden).
    untrusted_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError("score must be 0-100")
        if self.time_distance_ms < 0:
            raise ValueError("time_distance_ms must not be negative")

    @property
    def time_distance_seconds(self) -> float:
        return self.time_distance_ms / 1000

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NatMatch:
        chain = data.get("chain")
        return cls(
            kind=data["kind"],
            score=int(data["score"]),
            time_distance_ms=int(data["time_distance_ms"]),
            destination_match=data["destination_match"],
            traffic=TrafficInfo.from_dict(data["traffic"]),
            chain=None if chain is None else AttributionChain.from_dict(chain),
            egress=data.get("egress"),
            untrusted_sources=tuple(str(s) for s in data.get("untrusted_sources") or ()),
        )


@dataclass(frozen=True)
class EgressEntry:
    """One internet exit as a lookup used it: the normalised fields of ``nat.egresses``
    (addresses in canonical text, a single address without ``/32``)."""

    name: str
    label: str
    mode: EgressMode
    public: tuple[str, ...]
    public_dynamic: bool
    transips: tuple[str, ...]
    interfaces: tuple[str, ...]
    port_preservation: PortPreservation

    @classmethod
    def from_egress(cls, egress: Egress) -> EgressEntry:
        def text(networks: tuple[Any, ...]) -> tuple[str, ...]:
            return tuple(
                str(n.network_address) if n.prefixlen == n.max_prefixlen else str(n)
                for n in networks
            )

        return cls(
            name=egress.name,
            label=egress.label,
            mode=egress.mode,
            public=text(egress.public),
            public_dynamic=egress.public_dynamic,
            transips=text(egress.transips),
            interfaces=egress.interfaces,
            port_preservation=egress.port_preservation,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EgressEntry:
        return cls(
            name=str(data["name"]),
            label=str(data.get("label") or ""),
            mode=data["mode"],
            public=tuple(str(a) for a in data.get("public") or ()),
            public_dynamic=bool(data.get("public_dynamic", False)),
            transips=tuple(str(a) for a in data.get("transips") or ()),
            interfaces=tuple(str(i) for i in data.get("interfaces") or ()),
            port_preservation=data.get("port_preservation") or "unknown",
        )


@dataclass(frozen=True)
class EgressSearched:
    """What a public-IP lookup searched: the NAT addresses, whether the unclaimed private or
    shared NAT addresses were searched, the unclaimed addresses the logs showed near the moment
    (at most 5), the private or shared addresses that made a fallback's log check fail (at most
    5) and the interface condition."""

    transips: tuple[str, ...] = ()
    unclaimed: bool = False
    unclaimed_seen: tuple[str, ...] = ()
    outside_seen: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    interface_filter: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EgressSearched:
        return cls(
            transips=tuple(str(a) for a in data.get("transips") or ()),
            unclaimed=bool(data.get("unclaimed", False)),
            unclaimed_seen=tuple(str(a) for a in data.get("unclaimed_seen") or ()),
            outside_seen=tuple(str(a) for a in data.get("outside_seen") or ()),
            interfaces=tuple(str(i) for i in data.get("interfaces") or ()),
            interface_filter=bool(data.get("interface_filter", False)),
        )


@dataclass(frozen=True)
class EgressChange:
    """The last content change of an exit (``nat.egresses_changes``): ``changed_by`` as stored
    (``:migration``, ``:seed`` and ``:system`` are rendered in words); ``after_event`` when it
    lies after the requested moment."""

    name: str
    changed_at: datetime
    changed_by: str
    deleted: bool = False
    after_event: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_at", _aware(self.changed_at, "changed_at"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EgressChange:
        return cls(
            name=str(data["name"]),
            changed_at=_dt(data["changed_at"]),
            changed_by=str(data["changed_by"]),
            deleted=bool(data.get("deleted", False)),
            after_event=bool(data.get("after_event", False)),
        )


@dataclass(frozen=True)
class LookupEgress:
    """The internet exit a public-IP lookup resolved its address to (ADR-0016)."""

    resolution: EgressResolution
    #: ``fallback``: the exit whose check failed, then the exits it fell into; ``no_egress``:
    #: every configured exit; ``observed``: the ``not_logged`` exit that claims the address, if
    #: any; ``unconfigured``: the implicit exit (name ``""``).
    egresses: tuple[EgressEntry, ...] = ()
    searched: EgressSearched = field(default_factory=EgressSearched)
    changes: tuple[EgressChange, ...] = ()
    changed_after_event: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LookupEgress:
        return cls(
            resolution=data["resolution"],
            egresses=tuple(EgressEntry.from_dict(e) for e in data.get("egresses") or ()),
            searched=EgressSearched.from_dict(data.get("searched") or {}),
            changes=tuple(EgressChange.from_dict(c) for c in data.get("changes") or ()),
            changed_after_event=bool(data.get("changed_after_event", False)),
        )


@dataclass(frozen=True)
class DataWindow:
    """Where the data of the requested time lives (DB keeps less than the vault, spec 7.3)."""

    requested_day: date
    db_first_day: date | None
    db_last_day: date | None
    in_database: bool
    in_vault: bool

    @property
    def only_in_vault(self) -> bool:
        return self.in_vault and not self.in_database

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DataWindow:
        return cls(
            requested_day=date.fromisoformat(str(data["requested_day"])),
            db_first_day=_opt_date(data.get("db_first_day")),
            db_last_day=_opt_date(data.get("db_last_day")),
            in_database=bool(data["in_database"]),
            in_vault=bool(data["in_vault"]),
        )


@dataclass(frozen=True)
class LookupOutcome:
    request: LookupRequest
    mode: LookupMode
    #: Best first (exact before candidates, then score, then time distance).
    matches: tuple[NatMatch, ...]
    #: No public IP configured for the FortiGate (spec 2.3): exact public-port lookups impossible.
    double_nat: bool
    #: For ``internal`` lookups: the chain of the requested internal IP / MAC.
    attribution: AttributionChain | None
    explanations: tuple[Explanation, ...]
    data_window: DataWindow | None
    tolerance_seconds: int
    generated_at: datetime
    #: More candidates existed than ``lookup.max_candidates``.
    truncated: bool = False
    #: How the requested public IP was resolved (``None`` without a public IP in the request).
    egress: LookupEgress | None = None
    #: FortiGate DHCP records of the moment that never entered the lease timeline (loaded
    #: before the upgrade, unreadable fields, or loaded by the previous version after a
    #: rollback): at most one per IP, cited in internal mode and for exact matches only.
    unconverted_dhcp: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _aware(self.generated_at, "generated_at"))

    @property
    def result_count(self) -> int:
        return len(self.matches) + (1 if self.attribution is not None else 0)

    def to_dict(self) -> dict[str, Any]:
        data = cast(dict[str, Any], _encode(self))
        data["result_count"] = self.result_count
        if self.data_window is not None:
            data["data_window"]["only_in_vault"] = self.data_window.only_in_vault
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LookupOutcome:
        attribution, window = data.get("attribution"), data.get("data_window")
        return cls(
            request=LookupRequest.from_dict(data["request"]),
            mode=data["mode"],
            matches=tuple(NatMatch.from_dict(m) for m in data.get("matches") or ()),
            double_nat=bool(data["double_nat"]),
            attribution=None if attribution is None else AttributionChain.from_dict(attribution),
            explanations=tuple(Explanation.from_dict(e) for e in data.get("explanations") or ()),
            data_window=None if window is None else DataWindow.from_dict(window),
            tolerance_seconds=int(data["tolerance_seconds"]),
            generated_at=_dt(data["generated_at"]),
            truncated=bool(data.get("truncated", False)),
            egress=None if data.get("egress") is None else LookupEgress.from_dict(data["egress"]),
            unconverted_dhcp=_evidence(data.get("unconverted_dhcp")),
        )


# -- settings -------------------------------------------------------------------------------------

#: The shipped FortiGate DHCP logid map and dhcp_msg lists (``arcilogger_db.settings``; a test
#: keeps them equal).
FORTIGATE_LOGIDS: Final[Mapping[str, str]] = {
    "0100026001": "assigned",
    "0100026002": "deassigned",
}
FORTIGATE_DHCP_MSGS: Final[Mapping[str, tuple[str, ...]]] = {"0100026001": ("Ack",)}


@dataclass(frozen=True)
class LookupSettings:
    """The settings the engine, the lease timeline and the usage subjects need (from
    ``arcilogger_db.settings``). Only values: what needs a settings row's author or time is read
    by the lookup itself."""

    tolerance_seconds: int = 60
    max_candidates: int = 100
    #: Longest FortiGate session the lookup considers (bounds the time-range scan).
    max_session_seconds: int = 86400
    lease_default_seconds: int = 86400
    lease_server_seconds: Mapping[str, int] = field(default_factory=dict)
    #: An open lease times out ``lease time * percent / 100`` after its last evidence (spec 9.1:
    #: "x 1.1"). An integer, because every setting must be auditable as canonical JSON.
    lease_timeout_percent: int = 110
    arp_interval_seconds: int = 3600
    #: ``arp.ignore_after_release_seconds``: an ARP sighting of a pair this soon after its
    #: release is a stale ARP table entry (the lease timeline's rule 6, whose default
    #: ``DEFAULT_RELEASE_GRACE`` this equals; the lookup reads it for a RELEASE mapped after the
    #: build, round 6).
    arp_release_grace_seconds: int = 300
    infrastructure_macs: tuple[str, ...] = ()
    #: ``nat.egresses`` as stored: parsed only where it is used (:meth:`egresses`), so a value
    #: that cannot be parsed never stops the lease timeline or the usage rollups.
    nat_egresses: Any = field(default_factory=dict)
    #: FortiGate ``trandisp`` values a candidate search takes for unclaimed NAT addresses.
    snat_trandisp: tuple[str, ...] = ("snat",)
    #: ADR-0016 srcmac: the ``srcintf`` names whose srcmac may name a chain's MAC, and the most
    #: IPv4 addresses one srcmac may show within an hour of the moment.
    srcmac_interfaces: tuple[str, ...] = ()
    srcmac_max_ips: int = 1
    #: ADR-0016 FortiGate DHCP evidence: logid → ``assigned``/``deassigned`` (empty: off), the
    #: ``dhcp_msg`` values a logid's rows may carry, the logged lease time, traffic extending a
    #: FortiGate lease, the interfaces that are no lease evidence and the site check (D29).
    fortigate_logids: Mapping[str, str] = field(default_factory=lambda: dict(FORTIGATE_LOGIDS))
    fortigate_dhcp_msgs: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(FORTIGATE_DHCP_MSGS)
    )
    use_logged_lease: bool = True
    fortigate_traffic_extends: bool = False
    fortigate_interfaces_excluded: tuple[str, ...] = ()
    fortigate_format_verified: bool = False
    _egresses: tuple[Egress, ...] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def egresses(self) -> tuple[Egress, ...]:
        """The internet exits, parsed on first use and kept; ``ValueError`` when the stored
        ``nat.egresses`` cannot be parsed (each caller decides what that means)."""
        parsed = self._egresses
        if parsed is None:
            parsed = parse_egresses(self.nat_egresses)
            object.__setattr__(self, "_egresses", parsed)
        return parsed

    def lease_timeout_seconds(self, server: str | None, lease_seconds: int | None = None) -> int:
        """Seconds after the last evidence until an open lease of ``server`` times out.

        A FortiGate lease with a usable logged lease time (``lease_seconds``) ends exactly that
        long after its last DHCP evidence, the FortiGate's own end (ADR-0016, D6); any other
        lease after its server's configured time x ``lease.timeout_percent`` / 100, which is
        only an estimate of the server's lease."""
        if lease_seconds is not None:
            return lease_seconds
        return self.server_lease_seconds(server) * self.lease_timeout_percent // 100

    def server_lease_seconds(self, server: str | None) -> int:
        """The configured lease time of ``server`` (``lease.server_seconds``, else
        ``lease.default_seconds``), without the timeout margin."""
        return self.lease_server_seconds.get(server or "", self.lease_default_seconds)

    @classmethod
    def from_settings(cls, values: Mapping[str, Any]) -> LookupSettings:
        """From ``get_settings(session)`` (keys like ``lookup.tolerance_seconds``).

        ``ValueError`` names a bad setting. The lease job and the usage rollups call this too,
        so the values only the lookup reads (the exits, ``nat.snat_trandisp``, the srcmac
        settings) never raise here: the exits are kept as stored and parsed by
        :meth:`egresses`, the others fall back to the shipped default for a value of another
        shape (written around the validator, CORRELATION §5). The FortiGate flags
        (``dhcp.fortigate_use_logged_lease``, ``…_traffic_extends``, ``…_format_verified``) read
        only a JSON ``true`` or ``false``, any other value gives their default.
        """
        return cls(
            tolerance_seconds=int(values.get("lookup.tolerance_seconds", 60)),
            max_candidates=int(values.get("lookup.max_candidates", 100)),
            max_session_seconds=int(values.get("lookup.max_session_seconds", 86400)),
            lease_default_seconds=int(values.get("lease.default_seconds", 86400)),
            lease_server_seconds={
                str(k): int(v) for k, v in (values.get("lease.server_seconds") or {}).items()
            },
            lease_timeout_percent=int(values.get("lease.timeout_percent", 110)),
            arp_interval_seconds=int(values.get("arp.interval_seconds", 3600)),
            arp_release_grace_seconds=_lookup_count(
                values, "arp.ignore_after_release_seconds", 300
            ),
            infrastructure_macs=tuple(
                normalize_mac(str(mac)) for mac in values.get("devices.infrastructure_macs") or ()
            ),
            nat_egresses=values.get("nat.egresses", {}),
            snat_trandisp=_lookup_texts(values, "nat.snat_trandisp", ("snat",)),
            srcmac_interfaces=_lookup_texts(values, "lookup.srcmac_interfaces", ()),
            srcmac_max_ips=_lookup_count(values, "lookup.srcmac_max_ips", 1),
            fortigate_logids={
                str(logid): str(meaning)
                for logid, meaning in _object(values, "dhcp.fortigate_logids", FORTIGATE_LOGIDS)
            },
            fortigate_dhcp_msgs={
                str(logid): _text_list(messages, "dhcp.fortigate_dhcp_msgs")
                for logid, messages in _object(
                    values, "dhcp.fortigate_dhcp_msgs", FORTIGATE_DHCP_MSGS
                )
            },
            use_logged_lease=_flag(values, "dhcp.fortigate_use_logged_lease", True),
            fortigate_traffic_extends=_flag(values, "dhcp.fortigate_traffic_extends", False),
            fortigate_interfaces_excluded=_texts(values, "dhcp.fortigate_interfaces_excluded", ()),
            fortigate_format_verified=_flag(values, "dhcp.fortigate_format_verified", False),
        )


def _texts(values: Mapping[str, Any], key: str, default: Sequence[str]) -> tuple[str, ...]:
    """The texts of a list setting; ``ValueError`` naming it for any other value."""
    return _text_list(values.get(key, default), key)


def _text_list(items: Any, key: str) -> tuple[str, ...]:
    if not isinstance(items, list | tuple):
        raise ValueError(f"{key} must be a list of texts")
    return tuple(str(item) for item in items)


def _object(
    values: Mapping[str, Any], key: str, default: Mapping[str, Any]
) -> list[tuple[Any, Any]]:
    """The entries of an object setting; ``ValueError`` naming it for any other value."""
    value = values.get(key, default)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return list(value.items())


def _lookup_texts(values: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """The texts of a list setting only the lookup reads; ``default`` for any other value."""
    items = values.get(key, default)
    return tuple(str(item) for item in items) if isinstance(items, list | tuple) else default


def _flag(values: Mapping[str, Any], key: str, default: bool) -> bool:
    """A JSON ``true``/``false`` setting; ``default`` for any other value (``bool("false")`` is
    true, so a text written around the validator would switch the setting on)."""
    value = values.get(key, default)
    return value if isinstance(value, bool) else default


def _lookup_count(values: Mapping[str, Any], key: str, default: int) -> int:
    """An integer setting only the lookup reads; ``default`` for any other value."""
    value = values.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default
