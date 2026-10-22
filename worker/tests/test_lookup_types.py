"""5651 lookup types: validation, JSON round trip (stored in lookups.outcome), settings."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.canonical import canonical_json
from arcilogger_core.layout import Layout
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import EXAMPLE_SETTINGS
from arcilogger_worker.lookup import (
    AttributionChain,
    DataWindow,
    DeviceInfo,
    EvidenceRef,
    Explanation,
    LeaseInfo,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    NatMatch,
    PersonInfo,
    RawRef,
    TrafficInfo,
    normalize_mac,
    protocol_number,
    weakest,
)
from arcilogger_worker.lookup.sessions import (
    DomainHits,
    SessionGroup,
    TrafficRow,
    destination_match,
)

TR = timezone(timedelta(hours=3))
AT = datetime(2026, 10, 12, 11, 23, 41, tzinfo=TR)
RAW = RawRef(source="core-r1", day=date(2026, 10, 12), line=4, offset=310)


def _outcome() -> LookupOutcome:
    """The example chain (WAN 100.64.12.9:51001 → 172.20.42.57 → … → Selin Kaya / Planlama)."""
    dhcp = EvidenceRef(
        table="mt_dhcp",
        row_id=41,
        time=datetime(2026, 10, 12, 10, 42, 18, tzinfo=TR),
        raw=RAW,
        summary="dhcp42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14",
    )
    traffic = EvidenceRef(
        table="fg_traffic",
        row_id=1001,
        time=AT,
        raw=RawRef("fortigate", date(2026, 10, 12), 1, 0),
        summary="172.20.42.57:51001 -> 203.0.113.10:443 snat 100.64.12.9:51001",
    )
    chain = AttributionChain(
        internal_ip="172.20.42.57",
        at=AT,
        vlan=42,
        vlan_name="Planlama",
        lease=LeaseInfo(
            lease_id=7,
            ip="172.20.42.57",
            mac="00:00:5e:00:53:57",
            vlan=42,
            hostname="PLN-PC14",
            start=datetime(2026, 10, 12, 10, 42, 18, tzinfo=TR),
            end=None,
            end_reason="open",
            confidence="high",
            evidence=(dhcp,),
        ),
        device=DeviceInfo(
            mac="00:00:5e:00:53:57",
            name="Planlama-PC-14",
            hostnames=("PLN-PC14",),
            vendor=None,
            randomized=False,
            type="pc",
            infrastructure=False,
            confidence="medium",
        ),
        person=PersonInfo(
            person_id=1, name="Selin Kaya", department="Planlama", title=None, confidence="medium"
        ),
        department="Planlama",
        department_source="person",
        confidence=weakest(["high", "medium", "medium"]),
    )
    match = NatMatch(
        kind="candidate",
        score=87,
        time_distance_ms=0,
        destination_match="ip_port",
        traffic=TrafficInfo(
            sessionid=3100001,
            proto=6,
            srcip="172.20.42.57",
            srcport=51001,
            dstip="203.0.113.10",
            dstport=443,
            transip="100.64.12.9",
            transport=51001,
            start=AT - timedelta(seconds=10),
            end=AT,
            evidence=(traffic,),
        ),
        chain=chain,
    )
    return LookupOutcome(
        request=LookupRequest(
            at=AT,
            public_ip="192.0.2.44",
            public_port=40001,
            protocol=protocol_number("tcp"),
            dest_ip="203.0.113.10",
            dest_port=443,
        ),
        mode="candidates",
        matches=(match,),
        double_nat=True,
        attribution=None,
        explanations=(Explanation.standard("double_nat"),),
        data_window=DataWindow(
            requested_day=date(2026, 10, 12),
            db_first_day=date(2026, 9, 1),
            db_last_day=date(2026, 10, 12),
            in_database=True,
            in_vault=False,
        ),
        tolerance_seconds=60,
        generated_at=datetime(2026, 10, 12, 19, 0, tzinfo=UTC),
    )


def test_outcome_round_trips_through_json() -> None:
    outcome = _outcome()
    data = outcome.to_dict()
    restored = LookupOutcome.from_dict(json.loads(json.dumps(data)))
    assert restored == outcome
    assert restored.to_dict() == data


def test_outcome_json_shape_is_stable() -> None:
    data = _outcome().to_dict()
    assert data["mode"] == "candidates"
    assert data["result_count"] == 1
    assert data["double_nat"] is True
    assert data["request"]["at"] == "2026-10-12T08:23:41+00:00"  # UTC in storage
    assert data["request"]["protocol"] == 6
    assert data["data_window"]["only_in_vault"] is False
    chain = data["matches"][0]["chain"]
    assert chain["lease"]["evidence"][0]["raw"] == {
        "source": "core-r1",
        "day": "2026-10-12",
        "line": 4,
        "offset": 310,
    }
    assert chain["confidence"] == "medium"
    assert data["explanations"][0]["text_tr"].startswith("Kesin eşleşme için ISS modeminin")
    # No floats anywhere: the outcome can also be hashed as canonical JSON.
    assert canonical_json(data)


def test_request_normalises_and_validates() -> None:
    request = LookupRequest(
        at=AT,
        internal_ip=" 172.20.42.57 ",
        mac="00-00-5E-00-53-57",
        dest_domain="Example.COM.",
    )
    assert request.at.tzinfo is UTC
    assert request.internal_ip == "172.20.42.57"
    assert request.mac == "00:00:5e:00:53:57"
    assert request.dest_domain == "example.com"
    assert request.is_internal
    assert not LookupRequest(at=AT, public_ip="192.0.2.44").is_internal
    assert LookupRequest(at=AT, dest_ip="1.1.1.1").dest_ip == "1.1.1.1"
    assert LookupRequest.from_dict(request.to_dict()) == request


@pytest.mark.parametrize(("given", "stored"), [("tcp", 6), (" UDP ", 17), ("6", 6), (1, 1)])
def test_request_stores_the_protocol_number(given: Any, stored: int) -> None:
    """``fg_traffic.proto`` is SMALLINT: the request must hold the number, never 'tcp'."""
    request = LookupRequest(at=AT, public_ip="192.0.2.44", protocol=given)
    assert request.protocol == stored
    assert request.to_dict()["protocol"] == stored
    assert LookupRequest.from_dict({**request.to_dict(), "protocol": str(given)}).protocol == stored


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"at": datetime(2026, 10, 12, 18, 0), "public_ip": "1.1.1.1"}, "timezone-aware"),  # noqa: DTZ001
        ({"at": AT, "public_ip": "1.1.1.300"}, "invalid IP"),
        ({"at": AT, "public_ip": "1.1.1.1", "public_port": 70000}, "0-65535"),
        ({"at": AT, "public_ip": "1.1.1.1", "tolerance_seconds": 7200}, "tolerance_seconds"),
        ({"at": AT, "mac": "02:00"}, "invalid MAC"),
        ({"at": AT, "public_ip": "1.1.1.1", "protocol": 300}, "0-255"),
        ({"at": AT, "public_ip": "1.1.1.1", "protocol": "gre?"}, "unknown protocol"),
        ({"at": AT, "public_ip": "1.1.1.1", "protocol": True}, "protocol"),
        ({"at": AT}, "needs a public IP"),
    ],
)
def test_request_rejects_bad_input(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        LookupRequest(**kwargs)


def test_helpers() -> None:
    assert normalize_mac("0200.5E00.5346") == "02:00:5e:00:53:46"
    assert protocol_number("TCP") == 6
    assert protocol_number("udp") == 17
    assert protocol_number("47") == 47
    assert protocol_number(None) is None
    assert weakest([]) == "high"
    assert weakest(["high", "low", "medium"]) == "low"


def test_match_validation() -> None:
    match = _outcome().matches[0]
    assert match.time_distance_seconds == 0
    with pytest.raises(ValueError, match="score"):
        NatMatch(**{**match.__dict__, "score": 101})
    with pytest.raises(ValueError, match="negative"):
        NatMatch(**{**match.__dict__, "time_distance_ms": -1})


def test_floats_are_rejected_in_serialised_data() -> None:
    explanation = Explanation("k", "tr", "en", params={"ratio": 0.5})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="floats"):
        LookupOutcome(**{**_outcome().__dict__, "explanations": (explanation,)}).to_dict()


def test_raw_ref_points_to_spool_and_vault(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    assert RAW.spool_path(layout) == tmp_path / "spool" / "core-r1" / "2026-10-12.log"
    assert RAW.vault_path(layout) == tmp_path / "vault" / "2026" / "10" / "12" / "core-r1.log.gz"


def test_settings_from_shipped_defaults_mean_double_nat() -> None:
    settings = LookupSettings.from_settings(DEFAULT_SETTINGS)
    assert settings.double_nat is True
    assert settings.exact_addresses() == frozenset()
    assert settings.wan_transip is None
    assert settings.tolerance_seconds == 60
    assert settings.lease_server_seconds == {}  # no site's DHCP servers are shipped
    assert settings.lease_timeout_percent == 110
    assert settings.max_session_seconds == 86400
    # spec 9.1: lease time x 1.1 after the last evidence (integers only).
    assert settings.lease_timeout_seconds("dhcp61") == 95040
    assert settings.lease_timeout_seconds(None) == 95040
    # The example site gives its guest DHCP server 1 h leases.
    example = LookupSettings.from_settings({**DEFAULT_SETTINGS, **EXAMPLE_SETTINGS})
    assert example.lease_server_seconds == {"dhcp61": 3600}
    assert example.lease_timeout_seconds("dhcp61") == 3960
    assert example.lease_timeout_seconds("dhcp60") == 95040


def test_settings_with_public_and_wan_addresses() -> None:
    settings = LookupSettings.from_settings(
        {
            **DEFAULT_SETTINGS,
            "nat.public_ips": ["192.0.2.44"],
            "nat.wan_transip": "100.64.12.9",
            "devices.infrastructure_macs": ["00-00-5E-00-53-32"],
        }
    )
    assert settings.double_nat is False
    assert settings.exact_addresses() == {"192.0.2.44", "100.64.12.9"}
    assert settings.infrastructure_macs == ("00:00:5e:00:53:32",)


def test_settings_addresses_are_normalised_like_postgresql_returns_them() -> None:
    settings = LookupSettings.from_settings(
        {
            "nat.public_ips": [" 2001:DB8::0001 ", "192.0.2.44"],
            "nat.wan_transip": " 100.64.12.9 ",
        }
    )
    assert settings.public_ips == ("2001:db8::1", "192.0.2.44")
    assert settings.wan_transip == "100.64.12.9"
    assert "2001:db8::1" in settings.exact_addresses()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"nat.public_ips": ["085.001.002.003"]}, "nat.public_ips"),
        ({"nat.public_ips": "192.0.2.44"}, "list"),
        ({"nat.wan_transip": "100.64.12"}, "nat.wan_transip"),
    ],
)
def test_settings_reject_invalid_addresses(values: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        LookupSettings.from_settings(values)


def _conflict_chain() -> AttributionChain:
    chain = _outcome().matches[0].chain
    assert chain is not None
    other = AttributionChain(
        **{
            **chain.__dict__,
            "lease": None,
            "device": None,
            "person": None,
            "department": "Planlama",
            "department_source": "vlan",
            "confidence": "low",
        }
    )
    return AttributionChain(
        **{
            **chain.__dict__,
            "confidence": "low",
            "explanations": (Explanation.standard("ip_conflict"),),
            "alternatives": (other,),
        }
    )


def test_ip_conflict_lists_every_chain_and_round_trips() -> None:
    chain = _conflict_chain()
    assert chain.conflict
    assert not chain.alternatives[0].conflict
    data = chain.to_dict()
    assert [a["department_source"] for a in data["alternatives"]] == ["vlan"]
    assert data["explanations"][0]["key"] == "ip_conflict"
    assert data["explanations"][0]["text_tr"].startswith("Bu IP adresi bu zamanda birden fazla")
    assert AttributionChain.from_dict(json.loads(json.dumps(data))) == chain
    assert canonical_json(data)
    outcome = LookupOutcome(**{**_outcome().__dict__, "attribution": chain, "mode": "internal"})
    assert LookupOutcome.from_dict(outcome.to_dict()) == outcome


def test_ip_conflict_rules_are_enforced() -> None:
    chain = _conflict_chain()
    with pytest.raises(ValueError, match="low confidence"):
        AttributionChain(**{**chain.__dict__, "confidence": "medium"})
    with pytest.raises(ValueError, match="ip_conflict explanation"):
        AttributionChain(**{**chain.__dict__, "explanations": ()})
    with pytest.raises(ValueError, match="of their own"):
        AttributionChain(**{**chain.__dict__, "alternatives": (chain,)})
    stranger = AttributionChain(**{**chain.alternatives[0].__dict__, "internal_ip": "10.0.0.9"})
    with pytest.raises(ValueError, match="same internal IP"):
        AttributionChain(**{**chain.__dict__, "alternatives": (stranger,)})


def test_evidence_ref_is_the_lease_evidence_format() -> None:
    ref = EvidenceRef(
        table="mt_dhcp",
        row_id=41,
        time=datetime(2026, 10, 12, 10, 42, 18, tzinfo=TR),
        raw=RAW,
        summary="dhcp42 assigned",
    )
    assert ref.to_dict() == {
        "table": "mt_dhcp",
        "row_id": 41,
        "time": "2026-10-12T07:42:18+00:00",
        "raw": {"source": "core-r1", "day": "2026-10-12", "line": 4, "offset": 310},
        "summary": "dhcp42 assigned",
    }
    assert EvidenceRef.from_dict(ref.to_dict()) == ref


def _row(**fields: Any) -> TrafficRow:
    base: dict[str, Any] = {
        "id": 1,
        "time": AT,
        "logid": "0000000013",
        "sessionid": 7,
        "proto": 6,
        "srcip": "172.20.43.120",
        "srcport": 51001,
        "dstip": "203.0.113.10",
        "dstport": 443,
        "transip": "100.64.12.9",
        "transport": 51001,
        "trandisp": "snat",
        "duration": 10,
        "is_interim": False,
        "source": "fortigate",
        "raw_day": date(2026, 10, 12),
        "raw_line": 1,
        "raw_offset": 0,
    }
    return TrafficRow(**(base | fields))


@pytest.mark.parametrize(
    ("request_fields", "row_fields", "hits", "expected"),
    [
        ({"dest_ip": "203.0.113.10", "dest_port": 443}, {}, None, ("ip_port", 50)),
        ({"dest_ip": "203.0.113.10"}, {}, None, ("ip", 45)),
        ({"dest_ip": "203.0.113.10", "dest_port": 80}, {}, None, ("ip_other_port", 30)),
        ({"dest_domain": "example.com"}, {"named": True}, set(), ("domain", 45)),
        ({"dest_domain": "example.com", "dest_port": 443}, {"named": True}, set(), ("domain", 50)),
        (
            {"dest_domain": "example.com", "dest_port": 80},
            {"named": True},
            set(),
            ("domain_other_port", 40),
        ),
        ({"dest_domain": "example.com"}, {}, {"203.0.113.10"}, ("domain_address", 35)),
        (
            {"dest_domain": "example.com", "dest_port": 443},
            {},
            {"203.0.113.10"},
            ("domain_address", 40),
        ),
        ({"dest_domain": "example.com"}, {}, {"1.1.1.1"}, ("none", 0)),
        ({"dest_port": 443}, {}, None, ("port", 20)),
        ({"dest_port": 80}, {}, None, ("none", 0)),
        ({}, {}, None, ("not_requested", 25)),
    ],
)
def test_destination_match_keeps_weak_kinds_apart(
    request_fields: dict[str, Any],
    row_fields: dict[str, Any],
    hits: set[str] | None,
    expected: tuple[str, int],
) -> None:
    """A weak destination match (other port, address known only from another client) has its
    own label, so a report never presents it as a plain match."""
    request = LookupRequest(at=AT, public_ip="192.0.2.44", **request_fields)
    domain = None if hits is None else DomainHits(addresses=hits)
    group = SessionGroup([_row(**row_fields)])
    assert destination_match(group, request, domain) == expected
