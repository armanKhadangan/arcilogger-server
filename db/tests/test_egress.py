"""The internet exits of ``nat.egresses`` (ADR-0016): the exit type, the address constants and
the interface rule shared with the interface settings; the value's outer type."""

from __future__ import annotations

import ipaddress

import pytest

from arcilogger_db.egress import (
    EGRESS_MODES,
    ERROR_CODES,
    NOT_PUBLIC,
    PORT_PRESERVATION,
    PRIVATE_OR_SHARED,
    RESERVED,
    Egress,
    EgressValueError,
    interface_name_ok,
    parse_egresses,
    validate_egresses,
)
from arcilogger_db.settings import SETTING_ERRORS


def _exit(
    mode: str,
    public: tuple[str, ...] = (),
    transips: tuple[str, ...] = (),
    *,
    dynamic: bool = False,
) -> Egress:
    return Egress(
        name="hat-1",
        label="",
        mode=mode,  # type: ignore[arg-type]
        public=tuple(ipaddress.IPv4Network(a) for a in public),
        public_dynamic=dynamic,
        transips=tuple(ipaddress.IPv4Network(a) for a in transips),
        interfaces=(),
        port_preservation="unknown",
    )


def test_the_modes_and_codes_are_the_contracts() -> None:
    assert EGRESS_MODES == ("direct", "isp_nat", "not_logged", "internal")
    assert PORT_PRESERVATION == ("unknown", "preserved", "not_preserved")
    assert set(SETTING_ERRORS) >= ERROR_CODES


def test_not_public_is_the_explicit_list() -> None:
    """RFC 1918, RFC 6598 and the reserved ranges; the RFC 5737 documentation ranges of the
    example world are public here although ``ipaddress`` calls them not global."""
    assert [str(n) for n in PRIVATE_OR_SHARED] == [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    ]
    assert [str(n) for n in RESERVED] == [
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
    ]
    assert NOT_PUBLIC == PRIVATE_OR_SHARED + RESERVED
    for public in ("203.0.113.200", "198.51.100.77", "192.0.2.1"):
        assert not any(ipaddress.ip_address(public) in n for n in NOT_PUBLIC), public
        assert not ipaddress.ip_address(public).is_global  # why the list is explicit
    for private in ("100.64.12.9", "100.64.13.9", "172.20.42.57", "127.0.0.1", "224.0.0.1"):
        assert any(ipaddress.ip_address(private) in n for n in NOT_PUBLIC), private


def test_catch_all_public_unknown_and_claims() -> None:
    direct = _exit("direct", public=("203.0.113.200",))
    assert not direct.catch_all
    assert not direct.public_unknown
    assert direct.claims_public("203.0.113.200")
    assert not direct.claims_public("203.0.113.201")
    assert not direct.claims_transip("203.0.113.200")
    catch_all = _exit("isp_nat")
    assert catch_all.catch_all
    assert catch_all.public_unknown
    line = _exit("isp_nat", public=("198.51.100.64/29",), transips=("100.64.13.9",))
    assert not line.catch_all
    assert not line.public_unknown
    assert line.claims_public("198.51.100.70")
    assert not line.claims_public("198.51.100.72")
    assert line.claims_transip("100.64.13.9")
    assert not line.claims_public("2001:db8::1")  # IPv6 is never claimed
    dynamic = _exit("isp_nat", public=("198.51.100.77",), transips=("100.64.13.9",), dynamic=True)
    assert dynamic.public_unknown
    internal = _exit("internal", transips=("172.20.99.1",))
    assert not internal.catch_all  # only an isp_nat exit is the catch-all
    assert not internal.public_unknown


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("wan1", True),
        ("vlan42", True),
        ("port 1", True),  # FortiGate names are log-supplied text
        ("x" * 35, True),
        ("", False),
        ("x" * 36, False),
        (" wan1", False),
        ("wan1 ", False),
        ("wan\t1", False),
        ("wan\u200b1", False),  # a format character (zero-width space)
        ("wan\u20281", False),  # a line separator
        (7, False),
        (None, False),
    ],
)
def test_the_interface_rule(name: object, ok: bool) -> None:
    assert interface_name_ok(name) is ok


def test_the_value_is_an_object() -> None:
    assert validate_egresses({}) == {}
    with pytest.raises(EgressValueError) as caught:
        validate_egresses([])
    assert isinstance(caught.value, ValueError)
    assert caught.value.code == "setting_type"
    assert dict(caught.value.facts) == {"expected_tr": "nesne", "expected_en": "an object"}


def test_no_exits_parse_to_none() -> None:
    assert parse_egresses({}) == ()
    with pytest.raises(ValueError, match="must be an object"):
        parse_egresses([])
    with pytest.raises(ValueError, match="must be an object"):
        parse_egresses("hat-1")
