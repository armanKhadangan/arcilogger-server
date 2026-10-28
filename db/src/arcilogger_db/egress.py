"""The organisation's internet exits: the setting ``nat.egresses`` (ADR-0016).

``nat.egresses`` is a JSON object keyed by exit name; each exit says how one internet line
leaves the FortiGate:

* ``direct`` — the FortiGate holds the line's public address itself, so a public IP and port
  identify a session exactly;
* ``isp_nat`` — the ISP router translates once more ("double NAT"): the FortiGate's NAT
  addresses on that line are its ``transips``, and a lookup can only give ranked candidates;
* ``not_logged`` — the line does not pass the FortiGate, so there are no records of it;
* ``internal`` — not an internet exit: the FortiGate's source-NAT address towards a DMZ,
  another VLAN, a VPN or a branch; its addresses count as claimed and are never searched.

An ``isp_nat`` exit without ``transips`` is the catch-all: every private or shared source-NAT
address that no other exit claims. ``{}`` (the shipped default) behaves as one implicit
catch-all, the pure double NAT of earlier versions.

This module is a leaf (the standard library only), so :mod:`arcilogger_db.settings` can
validate the setting with it, the lookup engine can parse it and the migration of the legacy NAT
keys can keep a frozen copy, without an import cycle. A refusal is an :class:`EgressValueError`
with a code of :data:`arcilogger_db.settings.SETTING_ERRORS`; the settings module wraps it into
its ``SettingValueError``.

**The rules** (Wave 3 contract 2.8.1), checked in this order; the first that fails names its
code: at most 8 exits; a name of lower-case letters, digits and hyphens; only the known fields
and a ``mode``; a label of at most 64 characters; ``public`` and ``transips`` lists of at most
16 IPv4 addresses or networks of at least ``/24`` (``/32`` written as the bare address; IPv6 is
refused with its own code; a ``public`` address never in :data:`NOT_PUBLIC`); at most 8
interfaces; what each mode takes; across exits no address in two exits (one ``transip`` may be
shared by two exits with separate interfaces) and at most one catch-all. A field of the wrong
JSON type (``null`` included) is ``setting_egress_field``, except the three fields whose own rule
names a code (``label``, ``mode``, ``port_preservation``) and a list entry that is no text
(``setting_egress_address``). Lists are de-duplicated in the order given before they are counted.

:func:`legacy_egresses` converts the Wave 2 keys ``nat.public_ips``, ``nat.wan_transip`` and
``lookup.port_hint`` (contract 2.8.2); revision ``0018_nat_egresses`` keeps a frozen copy of it
and of :func:`validate_egresses`, and a test keeps the copies equal. :func:`fingerprint` is what
the change record ``nat.egresses_changes`` compares (D32).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv4Network
from typing import Any, Final, Literal

EgressMode = Literal["direct", "isp_nat", "not_logged", "internal"]
PortPreservation = Literal["unknown", "preserved", "not_preserved"]

#: The values of ``mode`` and ``port_preservation``, in the order the editor offers them.
EGRESS_MODES: Final[tuple[EgressMode, ...]] = ("direct", "isp_nat", "not_logged", "internal")
PORT_PRESERVATION: Final[tuple[PortPreservation, ...]] = ("unknown", "preserved", "not_preserved")
#: The fields of one exit (``mode`` is required, the others have defaults).
EGRESS_FIELDS: Final = (
    "label",
    "mode",
    "public",
    "public_dynamic",
    "transips",
    "interfaces",
    "port_preservation",
)

#: Private (RFC 1918) and shared (RFC 6598) address space: the FortiGate's address behind an ISP
#: router is one of these, so only they count as double-NAT evidence and as unclaimed
#: candidates.
PRIVATE_OR_SHARED: Final[tuple[IPv4Network, ...]] = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
#: Reserved ranges that are never an internet address of the organisation.
RESERVED: Final[tuple[IPv4Network, ...]] = tuple(
    ipaddress.IPv4Network(network)
    for network in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)
#: Everything a ``public`` address may not lie in or overlap: one explicit list, shared by the
#: validator and the lookup's unclaimed-address predicate. Never ``ipaddress``'s
#: ``is_private``/``is_global``, which call the RFC 5737 documentation ranges not global.
NOT_PUBLIC: Final[tuple[IPv4Network, ...]] = PRIVATE_OR_SHARED + RESERVED

#: Limits of the value (``setting_too_many`` beyond them).
MAX_EGRESSES: Final = 8
MAX_ADDRESSES: Final = 16
MAX_INTERFACES: Final = 8
MAX_LABEL: Final = 64
#: A FortiGate interface name as entered in a setting (``dstintf``/``srcintf``/``interface``).
MAX_INTERFACE_NAME: Final = 35

#: The codes of :data:`arcilogger_db.settings.SETTING_ERRORS` that the exit rules refuse with
#: (besides ``setting_type`` and ``setting_too_many``, which other settings share).
ERROR_CODES: Final = frozenset(
    {
        "setting_egress_name",
        "setting_egress_field",
        "setting_egress_dynamic",
        "setting_egress_mode",
        "setting_egress_label",
        "setting_egress_address",
        "setting_ipv6_not_supported",
        "setting_egress_overlap",
        "setting_egress_direct",
        "setting_egress_not_logged",
        "setting_egress_internal",
        "setting_egress_port_preservation",
        "setting_egress_catch_all",
        "setting_egress_public_private",
        "setting_egress_isp_nat_direct",
        "setting_interface_invalid",
    }
)

#: Unicode categories a name or label may not contain: control and format characters,
#: surrogates and line or paragraph separators.
_CONTROL_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def has_control_characters(text: str) -> bool:
    """``text`` holds a control or format character or a line break."""
    return any(unicodedata.category(char) in _CONTROL_CATEGORIES for char in text)


def interface_name_ok(name: Any) -> bool:
    """The interface rule: a text of 1-35 characters, without control characters and without
    leading or trailing spaces (``vlan42``, ``wan1``, ``port 1`` are fine)."""
    return (
        isinstance(name, str)
        and 1 <= len(name) <= MAX_INTERFACE_NAME
        and name == name.strip()
        and not has_control_characters(name)
    )


@dataclass(frozen=True)
class Egress:
    """One parsed exit; addresses as IPv4 networks (a single address is a ``/32``)."""

    name: str
    label: str
    mode: EgressMode
    public: tuple[IPv4Network, ...]
    public_dynamic: bool
    transips: tuple[IPv4Network, ...]
    interfaces: tuple[str, ...]
    port_preservation: PortPreservation

    @property
    def catch_all(self) -> bool:
        """The ``isp_nat`` exit without NAT addresses: it covers every unclaimed one."""
        return self.mode == "isp_nat" and not self.transips

    @property
    def public_unknown(self) -> bool:
        """Searched for public addresses no exit claims: an ``isp_nat`` exit whose public
        address is not entered or may change."""
        return self.mode == "isp_nat" and (not self.public or self.public_dynamic)

    def claims_public(self, ip: str) -> bool:
        """``ip`` is one of the exit's public addresses (or lies in one of its networks)."""
        return _contains(self.public, ip)

    def claims_transip(self, ip: str) -> bool:
        """``ip`` is one of the exit's FortiGate NAT addresses."""
        return _contains(self.transips, ip)


#: The one exit assumed while ``nat.egresses`` is empty: a catch-all without a name (shown as
#: ``""``), the pure double NAT of earlier versions.
IMPLICIT_CATCH_ALL: Final = Egress(
    name="",
    label="",
    mode="isp_nat",
    public=(),
    public_dynamic=False,
    transips=(),
    interfaces=(),
    port_preservation="unknown",
)


def _contains(networks: tuple[IPv4Network, ...], ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return any(address in network for network in networks)


#: An exit name (rule 2): lower-case letters, digits and hyphens, at most 32 characters.
NAME: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
#: The fields whose values decide which addresses an exit claims or is searched for: the
#: content :func:`fingerprint` covers (D32). Interfaces only narrow a candidate search, port
#: preservation only reorders candidates, a label changes nothing.
FINGERPRINT_FIELDS: Final = ("mode", "public", "transips", "public_dynamic")
#: The labels of the exits :func:`legacy_egresses` makes; they are printed in official reports,
#: so they say nothing about "old behaviour".
LEGACY_DIRECT_LABEL: Final = "Genel IP (yükseltmede aktarıldı)"
LEGACY_WAN_LABEL: Final = "FortiGate WAN adresi (yükseltmede aktarıldı)"
LEGACY_CATCH_ALL_LABEL: Final = "Genel adresi tanımlanmamış hatlar"


class EgressValueError(ValueError):
    """A value of ``nat.egresses`` the exit rules refuse: ``code`` is a code of
    ``SETTING_ERRORS``, ``facts`` fill its placeholders; ``str(exc)`` is the console's Turkish
    text (it may name the exit, never the address submitted)."""

    def __init__(self, code: str, text: str, **facts: Any) -> None:
        super().__init__(text)
        self.code = code
        self.facts: Mapping[str, Any] = facts


def validate_egresses(value: Any) -> dict[str, dict[str, Any]]:
    """The value an administrator may store for ``nat.egresses``, normalised (every field of
    every exit present, lists de-duplicated in the order given, addresses in canonical text);
    :class:`EgressValueError` for the first rule (module docstring) the value breaks."""
    if not isinstance(value, dict):
        raise EgressValueError(
            "setting_type",
            "nat.egresses: nesne bekleniyor",
            expected_tr="nesne",
            expected_en="an object",
        )
    if len(value) > MAX_EGRESSES:
        raise EgressValueError(
            "setting_too_many", f"nat.egresses: en çok {MAX_EGRESSES} çıkış", limit=MAX_EGRESSES
        )
    exits = {name: _exit(name, item) for name, item in value.items()}
    _check_across(exits)
    return exits


def _refuse(code: str, exit_name: str, text: str, **facts: Any) -> EgressValueError:
    return EgressValueError(code, f"nat.egresses: {exit_name!r} çıkışı: {text}", **facts)


def _exit(name: Any, value: Any) -> dict[str, Any]:
    """One exit, normalised (rules 2-8)."""
    if not isinstance(name, str) or not NAME.match(name):
        raise _refuse(
            "setting_egress_name",
            str(name),
            "ad küçük harf, rakam ve tireden oluşmalı (en çok 32 karakter, ör. hat-1)",
        )
    if not isinstance(value, dict):
        raise _refuse("setting_egress_field", name, "nesne bekleniyor")
    unknown = sorted(str(field) for field in value if field not in EGRESS_FIELDS)
    if unknown or "mode" not in value:
        raise _refuse(
            "setting_egress_field",
            name,
            f"alanlar: {', '.join(EGRESS_FIELDS)} (mode zorunlu)"
            + (f"; bilinmeyen: {', '.join(unknown)}" if unknown else ""),
        )
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in EGRESS_MODES:
        raise _refuse("setting_egress_mode", name, f"mode {', '.join(EGRESS_MODES)} olmalı")
    label = value.get("label", "")
    if not isinstance(label, str) or len(label) > MAX_LABEL or has_control_characters(label):
        raise _refuse(
            "setting_egress_label",
            name,
            f"açıklama en çok {MAX_LABEL} karakter, kontrol karakteri içeremez",
        )
    public = _networks(value.get("public", []), name, "public")
    transips = _networks(value.get("transips", []), name, "transips")
    interfaces = _interfaces(value.get("interfaces", []), name)
    preservation = value.get("port_preservation", "unknown")
    if not isinstance(preservation, str) or preservation not in PORT_PRESERVATION:
        raise _refuse(
            "setting_egress_port_preservation",
            name,
            f"port_preservation {', '.join(PORT_PRESERVATION)} olmalı",
        )
    dynamic = value.get("public_dynamic", False)
    if not isinstance(dynamic, bool):
        raise _refuse("setting_egress_field", name, "public_dynamic true/false olmalı")
    _check_mode(name, mode, public, transips, interfaces, preservation, dynamic)
    return {
        "label": label,
        "mode": mode,
        "public": [_text(network) for network in public],
        "public_dynamic": dynamic,
        "transips": [_text(network) for network in transips],
        "interfaces": interfaces,
        "port_preservation": preservation,
    }


def _networks(items: Any, name: str, field: str) -> list[IPv4Network]:
    """The canonical networks of a ``public`` or ``transips`` list (rule 5), de-duplicated."""
    if not isinstance(items, list):
        raise _refuse("setting_egress_field", name, f"{field} bir liste olmalı")
    found: list[IPv4Network] = []
    for item in items:
        network = _network(item, name, field)
        if field == "public" and any(network.overlaps(space) for space in NOT_PUBLIC):
            raise _refuse(
                "setting_egress_public_private",
                name,
                "genel adres özel, paylaşılan ya da ayrılmış bir adres olamaz",
            )
        if network not in found:
            found.append(network)
    if len(found) > MAX_ADDRESSES:
        raise _refuse(
            "setting_too_many", name, f"{field}: en çok {MAX_ADDRESSES} adres", limit=MAX_ADDRESSES
        )
    return found


def _network(text: Any, name: str, field: str) -> IPv4Network:
    """An IPv4 address or a network of at least ``/24`` with zero host bits; a prefix of digits
    only (no netmask notation; ``/024`` is read as ``ipaddress`` reads it, ``/24``)."""
    invalid = _refuse(
        "setting_egress_address",
        name,
        f"{field}: IPv4 adresi ya da en az /24 bir IPv4 ağı bekleniyor (ağ adresi)",
    )
    if not isinstance(text, str):
        raise invalid
    address, slash, prefix = text.partition("/")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        raise invalid from None
    if parsed.version == 6:
        raise _refuse("setting_ipv6_not_supported", name, f"{field}: IPv6 adresleri desteklenmiyor")
    if slash and not (prefix.isascii() and prefix.isdigit()):
        raise invalid
    bits = int(prefix) if slash else 32
    if not 24 <= bits <= 32:
        raise invalid
    try:
        return IPv4Network(f"{parsed}/{bits}")
    except ValueError:  # host bits set
        raise invalid from None


def _text(network: IPv4Network) -> str:
    """Canonical text: a ``/32`` as the bare address."""
    if network.prefixlen == network.max_prefixlen:
        return str(network.network_address)
    return str(network)


def _interfaces(items: Any, name: str) -> list[str]:
    if not isinstance(items, list):
        raise _refuse("setting_egress_field", name, "interfaces bir liste olmalı")
    found: list[str] = []
    for item in items:
        if not interface_name_ok(item):
            raise _refuse(
                "setting_interface_invalid",
                name,
                f"arayüz adı 1-{MAX_INTERFACE_NAME} karakter olmalı, kontrol karakteri ve "
                "baş/son boşluk içeremez",
            )
        if item not in found:
            found.append(item)
    if len(found) > MAX_INTERFACES:
        raise _refuse(
            "setting_too_many", name, f"en çok {MAX_INTERFACES} arayüz", limit=MAX_INTERFACES
        )
    return found


def _check_mode(
    name: str,
    mode: str,
    public: list[IPv4Network],
    transips: list[IPv4Network],
    interfaces: list[str],
    preservation: str,
    dynamic: bool,
) -> None:
    """Rule 8: what each mode takes (``isp_nat`` takes every field)."""
    if mode == "direct":
        if not public or transips or preservation != "unknown":
            raise _refuse(
                "setting_egress_direct",
                name,
                "doğrudan çıkış en az bir genel adres ister, transip ve port koruma girilmez",
            )
        if dynamic:
            raise _refuse(
                "setting_egress_dynamic",
                name,
                "genel IP değişebilir yalnızca servis sağlayıcı NAT'ı olan çıkışta seçilebilir",
            )
    elif mode == "not_logged":
        if not public or transips or interfaces or preservation != "unknown" or dynamic:
            raise _refuse(
                "setting_egress_not_logged",
                name,
                "FortiGate'ten geçmeyen çıkışa yalnızca genel adres ve açıklama girilir",
            )
    elif mode == "internal" and (not transips or public or dynamic or preservation != "unknown"):
        raise _refuse(
            "setting_egress_internal",
            name,
            "internet çıkışı olmayan NAT tanımına yalnızca NAT adresleri (en az bir), arayüzler "
            "ve açıklama girilir",
        )


def _overlap(first: list[str], second: list[str]) -> bool:
    return any(IPv4Network(a).overlaps(IPv4Network(b)) for a in first for b in second)


def _check_across(exits: Mapping[str, dict[str, Any]]) -> None:
    """Rule 9: no address in two exits (a shared ``transip`` only on separate interfaces), one
    catch-all at most."""
    for name, exit_ in exits.items():
        if exit_["mode"] == "isp_nat" and _overlap(exit_["public"], exit_["transips"]):
            raise _refuse(
                "setting_egress_isp_nat_direct",
                name,
                "NAT adresi genel adresiyle aynı olan hat doğrudandır",
            )
    items = list(exits.items())
    pairs = [(a, b) for index, a in enumerate(items) for b in items[index + 1 :]]
    overlap = "bir genel adres ya da NAT adresi iki çıkışta birden olamaz"
    for (_, first), (name, second) in pairs:
        if _overlap(first["public"], second["public"]):
            raise _refuse("setting_egress_overlap", name, overlap)
    for (_, first), (name, second) in pairs:
        if _overlap(first["public"], second["transips"]) or _overlap(
            second["public"], first["transips"]
        ):
            raise _refuse("setting_egress_overlap", name, overlap)
        separate = (
            first["interfaces"]
            and second["interfaces"]
            and not set(first["interfaces"]) & set(second["interfaces"])
        )
        if not separate and _overlap(first["transips"], second["transips"]):
            raise _refuse(
                "setting_egress_overlap",
                name,
                "aynı transip yalnızca arayüzleri ayrı iki çıkışta kullanılabilir",
            )
    catch_alls = [name for name, e in items if e["mode"] == "isp_nat" and not e["transips"]]
    if len(catch_alls) > 1:
        raise _refuse(
            "setting_egress_catch_all",
            catch_alls[1],
            "NAT adresi boş en çok bir çıkış olabilir",
        )


def parse_egresses(value: Any) -> tuple[Egress, ...]:
    """The exits of a stored ``nat.egresses`` value, sorted by name; ``ValueError`` (an
    :class:`EgressValueError` for a broken rule) for a value that cannot be parsed. ``{}`` (no
    exits configured) gives ``()``."""
    if not isinstance(value, Mapping):
        raise ValueError("nat.egresses must be an object")
    exits = validate_egresses(dict(value))
    return tuple(
        Egress(
            name=name,
            label=exit_["label"],
            mode=exit_["mode"],
            public=tuple(IPv4Network(a) for a in exit_["public"]),
            public_dynamic=exit_["public_dynamic"],
            transips=tuple(IPv4Network(a) for a in exit_["transips"]),
            interfaces=tuple(exit_["interfaces"]),
            port_preservation=exit_["port_preservation"],
        )
        for name, exit_ in sorted(exits.items())
    )


def fingerprint(exit_: Mapping[str, Any]) -> str:
    """SHA-256 (hex) of the canonical JSON of a normalised exit's content
    (:data:`FINGERPRINT_FIELDS`): what ``nat.egresses_changes`` compares (D32). The values are
    ASCII texts, lists and a boolean, for which sorted, compact ``json.dumps`` is RFC 8785
    canonical JSON."""
    content = {field: exit_[field] for field in FINGERPRINT_FIELDS}
    data = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# -- legacy conversion (contract 2.8.2; frozen copy in revision 0018_nat_egresses) ----------------


def _v4(text: Any, dropped: list[dict[str, str]]) -> str | None:
    """The canonical IPv4 text of a legacy value, or ``None`` (reported in ``dropped``)."""
    try:
        address = ipaddress.ip_address(str(text).strip())
    except ValueError:
        dropped.append({"value": str(text)[:64], "reason": "invalid"})  # stored unvalidated
        return None
    if address.version != 4:
        dropped.append({"value": str(address), "reason": "ipv6"})  # storable before
        return None
    if any(address in network for network in RESERVED):
        dropped.append({"value": str(address), "reason": "reserved"})
        return None
    return str(address)


def legacy_egresses(
    public_ips: Any, wan_transip: Any, port_hint: Any
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """The exits that keep the answers of the legacy keys ``nat.public_ips``,
    ``nat.wan_transip`` and ``lookup.port_hint``, and the legacy values dropped (each with its
    reason: ``invalid``, ``ipv6`` or ``reserved``).

    Every public IPv4 address goes into one direct exit ``genel-1`` (16 at most; ``genel-2`` …
    past 16). A private or shared entry (the FortiGate's WAN address behind an ISP router, which
    the old help text invited) and ``nat.wan_transip`` become the NAT addresses of the
    ``isp_nat`` exit ``wan``; without one, the catch-all ``tanimsiz`` keeps every other public
    address a candidate search, as before. When ``nat.wan_transip`` is one of the public
    addresses (bridge mode) no ``wan`` exit is made: the direct exit already gives the exact
    answer. The result is not validated here (more than 8 exits or 16 NAT addresses fail
    :func:`validate_egresses`)."""
    dropped: list[dict[str, str]] = []
    items = public_ips if isinstance(public_ips, list) else [public_ips]
    found = list(dict.fromkeys(a for a in (_v4(x, dropped) for x in items) if a))
    private = [a for a in found if any(ipaddress.ip_address(a) in n for n in PRIVATE_OR_SHARED)]
    keep = [a for a in found if a not in private]  # real public addresses
    wan = _v4(wan_transip, dropped) if str(wan_transip or "").strip() else None
    preservation = "preserved" if port_hint is True else "unknown"
    out: dict[str, dict[str, Any]] = {}
    for index in range(0, len(keep), MAX_ADDRESSES):
        out[f"genel-{index // MAX_ADDRESSES + 1}"] = {
            "label": LEGACY_DIRECT_LABEL,
            "mode": "direct",
            "public": keep[index : index + MAX_ADDRESSES],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": "unknown",
        }
    transips = list(
        dict.fromkeys([*([wan] if wan is not None and wan not in keep else []), *private])
    )
    if transips:
        out["wan"] = {
            "label": LEGACY_WAN_LABEL,
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": transips,
            "interfaces": [],
            "port_preservation": preservation,
        }
    elif keep or port_hint is True:
        out["tanimsiz"] = {
            "label": LEGACY_CATCH_ALL_LABEL,
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": preservation,
        }
    return out, dropped
