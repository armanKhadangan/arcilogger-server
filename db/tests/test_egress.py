"""The internet exits of ``nat.egresses`` (ADR-0016; Wave 3 contract 2.8.1, 2.8.2, 3.2): the exit
type, the address constants and the interface rule shared with the interface settings; every
rule and code of the validator, normalisation and limits, overlaps with and without interfaces,
the catch-all, parsing, the change fingerprint (D32) and the conversion of the legacy NAT keys.

The cases live in ``tests/fixtures/egress-cases.json``, which the web mirror's test reads too, so
the browser and the server refuse the same values with the same codes."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.canonical import canonical_json
from arcilogger_db.egress import (
    EGRESS_MODES,
    ERROR_CODES,
    FINGERPRINT_FIELDS,
    IMPLICIT_CATCH_ALL,
    LEGACY_CATCH_ALL_LABEL,
    LEGACY_DIRECT_LABEL,
    LEGACY_WAN_LABEL,
    NOT_PUBLIC,
    PORT_PRESERVATION,
    PRIVATE_OR_SHARED,
    RESERVED,
    Egress,
    EgressValueError,
    fingerprint,
    interface_name_ok,
    legacy_egresses,
    parse_egresses,
    validate_egresses,
)
from arcilogger_db.settings import SETTING_ERRORS, SettingValueError, validate_setting

CASES_FILE = Path(__file__).parents[2] / "tests" / "fixtures" / "egress-cases.json"
CASES: dict[str, Any] = json.loads(CASES_FILE.read_text(encoding="utf-8"))
REFUSED = [c for c in CASES["cases"] if "code" in c]
ACCEPTED = [c for c in CASES["cases"] if "normalised" in c]


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
    assert IMPLICIT_CATCH_ALL.name == ""
    assert IMPLICIT_CATCH_ALL.catch_all
    assert IMPLICIT_CATCH_ALL.public_unknown
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


# -- the shared cases -----------------------------------------------------------------------------


def test_every_exit_code_has_a_case() -> None:
    """Every code the exit rules refuse with is produced by a case of the shared file (and only
    those, besides the two codes other settings share)."""
    codes = {c["code"] for c in REFUSED}
    assert codes == ERROR_CODES | {"setting_type", "setting_too_many"}
    assert len({c["name"] for c in CASES["cases"]}) == len(CASES["cases"])


@pytest.mark.parametrize("case", REFUSED, ids=[c["name"] for c in REFUSED])
def test_a_refused_value(case: dict[str, Any]) -> None:
    with pytest.raises(EgressValueError) as caught:
        validate_egresses(case["value"])
    assert caught.value.code == case["code"]
    # The settings module names the key and gives both texts, which never repeat a value.
    with pytest.raises(SettingValueError) as wrapped:
        validate_setting("nat.egresses", case["value"])
    refusal = wrapped.value
    assert (refusal.key, refusal.code) == ("nat.egresses", case["code"])
    assert "{" not in refusal.message_tr + refusal.message_en
    console = str(refusal)
    for exit_ in case["value"].values() if isinstance(case["value"], dict) else ():
        for field in ("public", "transips"):
            items = exit_.get(field) if isinstance(exit_, dict) else None
            for item in items if isinstance(items, list) else ():
                if isinstance(item, str) and item.strip():
                    assert item not in console  # the console names the exit, never an address
                    assert item not in refusal.message_tr + refusal.message_en
    if isinstance(case["value"], dict):  # a stored value of this shape is not parsed either
        with pytest.raises(EgressValueError) as parsed:
            parse_egresses(case["value"])
        assert parsed.value.code == case["code"]
    else:
        with pytest.raises(ValueError, match="must be an object"):
            parse_egresses(case["value"])


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_an_accepted_value_is_normalised(case: dict[str, Any]) -> None:
    normalised = validate_egresses(case["value"])
    assert normalised == case["normalised"]
    assert validate_setting("nat.egresses", case["value"]) == normalised
    assert validate_egresses(normalised) == normalised  # normalising twice changes nothing
    for exit_ in normalised.values():
        assert list(exit_) == ["label", "mode", "public", "public_dynamic", "transips",
                               "interfaces", "port_preservation"]  # fmt: skip
    canonical_json(normalised)  # storable and auditable
    parsed = parse_egresses(case["value"])
    assert [e.name for e in parsed] == sorted(normalised)
    for egress in parsed:
        stored = normalised[egress.name]
        assert egress.mode == stored["mode"]
        assert egress.label == stored["label"]
        assert [_text(n) for n in egress.public] == stored["public"]
        assert [_text(n) for n in egress.transips] == stored["transips"]
        assert list(egress.interfaces) == stored["interfaces"]
        assert (egress.public_dynamic, egress.port_preservation) == (
            stored["public_dynamic"],
            stored["port_preservation"],
        )


def _text(network: ipaddress.IPv4Network) -> str:
    return str(network.network_address) if network.prefixlen == 32 else str(network)


def test_limits_count_after_de_duplication() -> None:
    sixteen = [f"203.0.113.{n}" for n in range(16)]
    value = {"hat-1": {"mode": "direct", "public": [*sixteen, "203.0.113.0/32"]}}
    assert validate_egresses(value)["hat-1"]["public"] == sixteen
    with pytest.raises(EgressValueError) as caught:
        validate_egresses({"hat-1": {"mode": "direct", "public": [*sixteen, "203.0.113.16"]}})
    assert (caught.value.code, dict(caught.value.facts)) == ("setting_too_many", {"limit": 16})
    nine = {f"h{n}": {"mode": "direct", "public": [f"203.0.113.{n}"]} for n in range(9)}
    with pytest.raises(EgressValueError) as caught:
        validate_egresses(nine)
    assert (caught.value.code, dict(caught.value.facts)) == ("setting_too_many", {"limit": 8})


def test_the_rules_run_in_order() -> None:
    """A value that breaks several rules is refused with the first one (2.8.1's order)."""
    broken = {"Hat-1": {"mode": "x", "public": ["10.0.0.1"]}}
    assert _code(broken) == "setting_egress_name"
    assert _code({"hat-1": {"mode": "x", "public": ["10.0.0.1"]}}) == "setting_egress_mode"
    assert _code({"hat-1": {"mode": "direct", "label": 7, "public": ["x"]}}) == (
        "setting_egress_label"
    )
    assert _code({"hat-1": {"mode": "direct", "public": ["10.0.0.1"], "interfaces": [" a"]}}) == (
        "setting_egress_public_private"
    )
    assert _code({"hat-1": {"mode": "direct", "interfaces": [" a"]}}) == (
        "setting_interface_invalid"
    )
    # Per mode before across exits: the second exit's mode rule wins over an overlap.
    overlapping = {
        "hat-1": {"mode": "direct", "public": ["203.0.113.200"]},
        "hat-2": {"mode": "direct", "public": ["203.0.113.200"], "transips": ["100.64.12.9"]},
    }
    assert _code(overlapping) == "setting_egress_direct"


def _code(value: Any) -> str:
    with pytest.raises(EgressValueError) as caught:
        validate_egresses(value)
    return caught.value.code


def test_a_shared_transip_needs_separate_interfaces_on_both_exits() -> None:
    def exits(first: list[str], second: list[str]) -> dict[str, Any]:
        return {
            "hat-1": {"mode": "isp_nat", "transips": ["100.64.12.9"], "interfaces": first},
            "hat-2": {"mode": "isp_nat", "transips": ["100.64.12.9"], "interfaces": second},
        }

    assert validate_egresses(exits(["wan1"], ["wan2"]))
    assert _code(exits(["wan1"], [])) == "setting_egress_overlap"
    assert _code(exits(["wan1", "wan2"], ["wan2"])) == "setting_egress_overlap"
    # An internal exit's NAT address follows the same rule.
    internal = {
        "hat-2": {"mode": "isp_nat", "transips": ["172.20.99.1"], "interfaces": ["wan2"]},
        "dmz": {"mode": "internal", "transips": ["172.20.99.0/24"], "interfaces": ["dmz"]},
    }
    assert validate_egresses(internal)
    internal["dmz"]["interfaces"] = ["wan2"]
    assert _code(internal) == "setting_egress_overlap"


# -- the change fingerprint (D32) -----------------------------------------------------------------


def test_the_fingerprint_covers_what_decides_the_addresses() -> None:
    exit_ = validate_egresses(
        {
            "hat-2": {
                "label": "Yedek hat",
                "mode": "isp_nat",
                "public": ["198.51.100.77"],
                "transips": ["100.64.13.9"],
                "interfaces": ["wan2"],
            }
        }
    )["hat-2"]
    base = fingerprint(exit_)
    content = {field: exit_[field] for field in FINGERPRINT_FIELDS}
    assert base == hashlib.sha256(canonical_json(content)).hexdigest()
    assert FINGERPRINT_FIELDS == ("mode", "public", "transips", "public_dynamic")
    unchanged: list[tuple[str, Any]] = [
        ("label", "Hat 2"),
        ("interfaces", ["wan2", "wan3"]),
        ("port_preservation", "preserved"),
    ]
    for field, other in unchanged:
        assert fingerprint({**exit_, field: other}) == base, field
    changed: list[tuple[str, Any]] = [
        ("mode", "direct"),
        ("public", ["198.51.100.78"]),
        ("transips", ["100.64.13.10"]),
        ("public_dynamic", True),
    ]
    for field, other in changed:
        assert fingerprint({**exit_, field: other}) != base, field


# -- legacy conversion (contract 2.8.2) -----------------------------------------------------------

LEGACY = CASES["legacy"]


@pytest.mark.parametrize("row", LEGACY, ids=[r["name"] for r in LEGACY])
def test_the_legacy_conversion(row: dict[str, Any]) -> None:
    exits, dropped = legacy_egresses(row["public_ips"], row["wan_transip"], row["port_hint"])
    assert (exits, dropped) == (row["exits"], row["dropped"])
    if row["valid"]:
        # Every converter output that is kept passes the rules unchanged and parses.
        assert validate_egresses(exits) == exits
        assert [e.name for e in parse_egresses(exits)] == sorted(exits)
    else:
        with pytest.raises(EgressValueError):
            validate_egresses(exits)


def _row(name: str) -> dict[str, Any]:
    return next(r for r in LEGACY if r["name"] == name)


def test_the_legacy_table() -> None:
    """The rows of 2.8.2 as the contract names them."""
    a = "203.0.113.200"
    assert legacy_egresses([], "", False) == ({}, [])  # (a) the implicit catch-all
    b, _ = legacy_egresses([a], "", False)
    assert b == {
        "genel-1": {
            "label": LEGACY_DIRECT_LABEL,
            "mode": "direct",
            "public": [a],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": "unknown",
        },
        "tanimsiz": {
            "label": LEGACY_CATCH_ALL_LABEL,
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": "unknown",
        },
    }
    b3 = _row("b3: twenty public addresses")["exits"]
    assert [len(b3[name]["public"]) for name in ("genel-1", "genel-2")] == [16, 4]
    c, _ = legacy_egresses([], "100.64.12.9", False)
    assert c == {
        "wan": {
            "label": LEGACY_WAN_LABEL,
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": ["100.64.12.9"],
            "interfaces": [],
            "port_preservation": "unknown",
        }
    }
    d, _ = legacy_egresses([a], "100.64.12.9", False)
    assert sorted(d) == ["genel-1", "wan"]
    e, _ = legacy_egresses([], "", True)
    assert e["tanimsiz"]["port_preservation"] == "preserved"
    assert legacy_egresses([], "100.64.12.9", True)[0]["wan"]["port_preservation"] == "preserved"
    f, _ = legacy_egresses([a], a, False)  # bridge mode: no wan exit
    assert sorted(f) == ["genel-1", "tanimsiz"]
    assert legacy_egresses(["not an address"], "", False) == (
        {},
        [{"value": "not an address", "reason": "invalid"}],
    )
    g, dropped = legacy_egresses(["2001:db8::44", "127.0.0.1", a], "", False)
    assert g == b
    assert dropped == [
        {"value": "2001:db8::44", "reason": "ipv6"},
        {"value": "127.0.0.1", "reason": "reserved"},
    ]
    h, _ = legacy_egresses(["100.64.12.9"], "", False)  # a private address entered as public
    assert sorted(h) == ["wan"]
    assert h["wan"]["transips"] == ["100.64.12.9"]
    both, _ = legacy_egresses(["100.64.12.9", a], "100.64.13.9", False)
    assert both["wan"]["transips"] == ["100.64.13.9", "100.64.12.9"]
    # 112 public addresses fit (7 direct exits + tanimsiz); 113 need a ninth exit and fail.
    assert _row("112 public addresses")["valid"]
    assert len(_row("112 public addresses")["exits"]) == 8
    assert not _row("113 public addresses")["valid"]
    assert not _row("17 private addresses")["valid"]  # more than 16 NAT addresses in wan


def test_the_legacy_conversion_uses_the_explicit_address_lists() -> None:
    """``PRIVATE_OR_SHARED`` moves an address into ``wan``; ``RESERVED`` drops it; an RFC 5737
    documentation address stays public."""
    for private in ("10.20.30.40", "172.16.0.9", "192.168.1.1", "100.127.255.254"):
        assert legacy_egresses([private], "", False)[0]["wan"]["transips"] == [private]
    for reserved in ("0.0.0.1", "127.0.0.1", "169.254.1.1", "224.0.0.9", "240.0.0.1"):
        assert legacy_egresses([reserved], "", False)[1] == [
            {"value": reserved, "reason": "reserved"}
        ]
    for public in ("192.0.2.10", "198.51.100.7", "203.0.113.5"):
        assert legacy_egresses([public], "", False)[0]["genel-1"]["public"] == [public]
