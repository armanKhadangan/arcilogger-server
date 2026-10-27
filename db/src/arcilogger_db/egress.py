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

This module is a leaf (``ipaddress`` and ``re`` only), so :mod:`arcilogger_db.settings` can
validate the setting with it, the lookup engine can parse it and the migration of the legacy
NAT keys can keep a frozen copy, without an import cycle. A refusal is an
:class:`EgressValueError` with a code of :data:`arcilogger_db.settings.SETTING_ERRORS`; the
settings module wraps it into its ``SettingValueError``.

The exit rules themselves (names, fields, addresses, modes, overlaps) are not checked yet:
:func:`validate_egresses` checks only that the value is an object, and :func:`parse_egresses`
parses only the empty object.
"""

from __future__ import annotations

import ipaddress
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


def _contains(networks: tuple[IPv4Network, ...], ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return any(address in network for network in networks)


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
    every exit present); :class:`EgressValueError` otherwise.

    Only the outer type is checked for now: an object."""
    if not isinstance(value, dict):
        raise EgressValueError(
            "setting_type",
            "nat.egresses: nesne bekleniyor",
            expected_tr="nesne",
            expected_en="an object",
        )
    return {str(name): exit_ for name, exit_ in value.items()}


def parse_egresses(value: Any) -> tuple[Egress, ...]:
    """The exits of a stored ``nat.egresses`` value, sorted by name; ``ValueError`` for a value
    that cannot be parsed. ``{}`` (no exits configured) gives ``()``."""
    if not isinstance(value, Mapping):
        raise ValueError("nat.egresses must be an object")
    if not value:
        return ()
    raise NotImplementedError("the exits of nat.egresses cannot be parsed yet")


def legacy_egresses(
    public_ips: Any, wan_transip: Any, port_hint: Any
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """The exits that keep the answers of the legacy keys ``nat.public_ips``,
    ``nat.wan_transip`` and ``lookup.port_hint``, and the legacy values dropped (each with its
    reason)."""
    raise NotImplementedError("the legacy NAT keys cannot be converted yet")
