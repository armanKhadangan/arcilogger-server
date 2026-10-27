"""The planner (contract 2.5.2, 3.2): driver, branches, selectivity, joins, binding, summary table.

A pure module: no database. The SQL it leads to is checked in ``test_search_sql_safety.py``
(bound values) and ``test_search_oracle.py`` (the same rows as the matcher).
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from arcilogger_api.search import SearchQueryError, parse_query, sql
from arcilogger_api.search.cursor import bounds
from arcilogger_api.search.fields import DepartmentEntry, SiteNetwork
from arcilogger_api.search.planner import Plan, describe_query, plan_query, rank_of
from arcilogger_db.kinds import CLIENT_IP, KINDS, TYPED_TABLES
from arcilogger_db.models import TIME_SERIES_TABLES

SITE = SiteNetwork(
    vlans={
        41: ipaddress.ip_network("172.20.41.0/24"),
        42: ipaddress.ip_network("172.20.42.0/24"),
        43: ipaddress.ip_network("172.20.43.0/24"),
        60: ipaddress.ip_network("172.20.60.0/22"),
        70: ipaddress.ip_network("172.20.70.0/25"),
        80: ipaddress.ip_network("203.0.113.0/24"),
    },
    departments=(
        DepartmentEntry(1, "Finans", (41,)),
        DepartmentEntry(2, "İdari İşler", (43, 60)),
        DepartmentEntry(3, "Arşiv", ()),
    ),
    network_sources=frozenset({"ap"}),
)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
DIALECT = postgresql.psycopg.dialect()  # type: ignore[no-untyped-call]


def plan(text_: str) -> Plan:
    return plan_query(parse_query(text_), SITE)


def refused(text_: str) -> SearchQueryError:
    with pytest.raises(SearchQueryError) as caught:
        plan(text_)
    return caught.value


# -- driver and branches --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text_", "driver", "tables"),
    [
        ("", "messages", ("messages",)),
        ("type:web", "messages", ("messages",)),
        ('"login failure" host:core-r1 severity:<=err', "messages", ("messages",)),
        ("source:core-r1 sender:172.20.10.1", "messages", ("messages",)),
        ("srcip:172.20.41.105", "typed", tuple(CLIENT_IP)),
        ("srcip:172.20.41.105 type:web action:blocked", "typed", ("fg_web",)),
        ("-type:web srcip:172.20.41.5", "typed", tuple(t for t in CLIENT_IP if t != "fg_web")),
        ("domain:example.com", "typed", ("fg_web", "fg_dns", "fg_app", "fg_virus")),
        ("mac:00:00:5e:00:53:21", "typed", ("mt_dhcp", "mt_arp", "mt_fw", "mt_event", "fg_dhcp")),
        ("type:event mac:00:00:5e:00:53:57", "typed", ("fg_dhcp",)),
        ("srcmac:00:00:5e:00:53:57", "typed", ("fg_traffic",)),
        ("type:dhcp mac:00:00:5e:*", "typed", ("mt_dhcp", "mt_event")),
        ("user:admin", "typed", ("fg_event", "mt_account", "mt_config")),
        (
            "-action:accept",
            "typed",
            (
                "fg_traffic",
                "fg_web",
                "fg_dns",
                "fg_app",
                "fg_ips",
                "fg_virus",
                "fg_event",
                "mt_dhcp",
                "mt_account",
                "mt_config",
            ),
        ),
        (
            "dstport:3389 action:deny",
            "typed",
            ("fg_traffic", "fg_web", "fg_dns", "fg_app", "fg_ips", "fg_virus"),
        ),
        ("interface:ether1 type:interface", "typed", ("mt_interface",)),
        ("qname:www.example.com", "typed", ("fg_dns",)),
    ],
)
def test_the_driver_and_its_tables(text_: str, driver: str, tables: tuple[str, ...]) -> None:
    found = plan(text_)
    assert found.driver == driver
    assert found.tables == tables
    assert [branch.rank for branch in found.branches] == [rank_of(t) for t in tables]
    assert [b.rank for b in found.branches] == sorted(b.rank for b in found.branches)


def test_ranks_are_the_time_series_order() -> None:
    assert rank_of("messages") == 0
    assert [rank_of(table) for table in TYPED_TABLES] == list(range(1, len(TIME_SERIES_TABLES)))
    assert rank_of("mt_event") == 14
    assert rank_of("fg_dhcp") == 15  # appended (0017_fortigate_dhcp): nothing existing moved


@pytest.mark.parametrize(
    ("text_", "fields"),
    [
        ("domain:example.com mac:00:00:5e:00:53:21", "domain, mac"),
        ("type:dhcp domain:example.com", "type, domain"),
        ("qname:www.example.com natip:198.51.100.7", "qname, natip"),
        ("type:syslog srcip:172.20.41.5", "type, srcip"),
    ],
)
def test_fields_no_table_has_together(text_: str, fields: str) -> None:
    error = refused(text_)
    assert error.code == "search_fields_incompatible"
    assert error.params == {"fields": fields}


def test_kinds_follow_type_clauses() -> None:
    assert plan("").kinds == tuple(KINDS)
    assert plan("type:web,dns").kinds == ("web", "dns")
    assert "heartbeat" not in plan("-type:heartbeat").kinds
    assert plan("type:web -type:web").kinds == ()
    # mt_event carries the events of the kinds allowed, in MT_EVENT_KINDS order
    dhcp = plan("type:dhcp mac:00:00:5e:*")
    assert dhcp.branches[-1].events == ("dhcp_offering", "dhcp_pool_empty", "dhcp_failed")
    every = plan("mac:00:00:5e:*")
    (mt_event,) = [branch for branch in every.branches if branch.table == "mt_event"]
    assert mt_event.events == (
        "dhcp_offering",
        "dhcp_pool_empty",
        "dhcp_failed",
        "wifi_connect",
        "wifi_disconnect",
        "heartbeat",
        "log",
    )
    assert all(branch.events is None for branch in every.branches if branch is not mt_event)


def test_messages_only_conditions_join_messages_in_a_typed_branch() -> None:
    found = plan('srcip:172.20.41.105 "youtube" -host:fw source:fortigate sender:172.31.255.1')
    for branch in found.branches:
        assert [c.field for c in branch.own] == ["srcip", "source", "sender"]
        assert [c.field for c in branch.joined] == ["text", "host"]
    compiled = str(
        sql.page_statement(
            found, NOW - timedelta(hours=1), bounds(None, NOW, found.ranks), 10
        ).compile(dialect=DIALECT)
    )
    assert "JOIN messages AS m ON m.time = fg_web.time AND m.source = fg_web.source" in compiled
    plain = plan("srcip:172.20.41.105")
    assert all(not branch.joined for branch in plain.branches)
    assert "JOIN messages" not in str(
        sql.page_statement(
            plain, NOW - timedelta(hours=1), bounds(None, NOW, plain.ranks), 10
        ).compile(dialect=DIALECT)
    )


# -- selectivity ----------------------------------------------------------------------------------


def _selective(text_: str) -> dict[str, bool]:
    return {branch.table: branch.selective for branch in plan(text_).branches}


def test_srcip_is_indexed_except_in_the_small_event_tables() -> None:
    """R28: the client address of each table; fg_event.srcip and mt_event.ip have no index."""
    found = _selective("srcip:172.20.41.105")
    assert {t for t, s in found.items() if not s} == {"fg_event", "mt_event"}
    assert plan("srcip:172.20.41.105").selective is True
    assert "query_not_selective" not in {n["code"] for n in plan("srcip:172.20.41.105").notices}


def test_ip_is_indexed_only_where_every_address_column_is() -> None:
    """R27: fg_traffic, mt_fw, mt_dhcp, mt_arp, mt_account, fg_dhcp; a scan in the UTM
    tables."""
    found = _selective("ip:203.0.113.10")
    assert {t for t, s in found.items() if s} == {
        "fg_traffic",
        "mt_fw",
        "mt_dhcp",
        "mt_arp",
        "mt_account",
        "fg_dhcp",
    }


@pytest.mark.parametrize(
    "text_",
    [
        "dstport:3389 action:deny",
        "-srcip:172.20.41.5",
        "srcip:*",
        "youtube",
        "-type:heartbeat",
        "url:login",
        "srcmac:00:00:5e:00:53:57",  # not indexed (ADR-0016)
    ],
)
def test_queries_no_index_serves_get_the_notice(text_: str) -> None:
    found = plan(text_)
    assert found.selective is False
    assert "query_not_selective" in {n["code"] for n in found.notices}


@pytest.mark.parametrize(
    "text_",
    [
        "",
        "type:web",
        "source:core-r1",
        "domain:example.com",
        "qname:www.example.com",
        "type:dhcp mac:00:00:5e:00:53:21",
        "vlan:41",
    ],
)
def test_index_backed_queries(text_: str) -> None:
    assert plan(text_).selective is True
    assert "query_not_selective" not in {n["code"] for n in plan(text_).notices}


def test_mt_event_is_index_backed_by_its_kind() -> None:
    found = _selective("type:dhcp mac:00:00:5e:00:53:21")
    assert found == {"mt_dhcp": True, "mt_event": True}
    assert _selective("mac:00:00:5e:00:53:21")["mt_event"] is False


# -- binding --------------------------------------------------------------------------------------


def test_vlan_and_department_are_bound_to_subnets() -> None:
    found = plan("vlan:41,60 department:Finans")
    vlan, department = found.branches[0].own
    assert vlan.networks == ((SITE.vlans[41],), (SITE.vlans[60],))
    with pytest.raises(SearchQueryError):
        plan("department:idari*")
    named = plan('department:"İDARİ İŞLER"').branches[0].own[0]
    assert named.networks == ((SITE.vlans[43], SITE.vlans[60]),)
    assert plan("department:1").branches[0].own[0].networks == ((SITE.vlans[41],),)
    assert plan("department:Arşiv").branches[0].own[0].networks == ((),)
    assert "department_by_vlan" in {n["code"] for n in plan("department:Finans").notices}
    assert department.field == "department"


def test_unknown_vlans_and_departments_are_refused() -> None:
    error = refused("srcip:172.20.41.5 vlan:99")
    assert (error.code, error.params) == ("search_vlan_unknown", {"vlan": 99})
    assert error.start == len("srcip:172.20.41.5 ")
    assert refused("department:Yok").code == "search_department_unknown"
    # POST /search/parse binds nothing (no database): no refusal there
    assert plan_query(parse_query("vlan:99 department:Yok"), None).driver == "typed"


def test_a_network_source_also_finds_its_members() -> None:
    found = plan("source:ap,core-r1,ap*").branches[0].own[0]
    assert found.members == (True, False, False)
    compiled = str(
        sql.page_statement(
            plan("source:ap"), NOW - timedelta(hours=1), bounds(None, NOW, {0}), 5
        ).compile(dialect=DIALECT)
    )
    assert "messages.source = %(" in compiled
    assert "messages.source LIKE %(" in compiled


# -- the summary table ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text_", "tables"),
    [
        ("", None),
        ("type:web,dns -source:core-*", None),
        (
            "action:blocked",
            (
                "fg_traffic",
                "fg_web",
                "fg_dns",
                "fg_app",
                "fg_ips",
                "fg_virus",
                "fg_event",
                "mt_dhcp",
                "mt_account",
                "mt_config",
            ),
        ),
        ("type:web action:blocked vlan:41", ("fg_web",)),
        ("department:Finans", tuple(CLIENT_IP)),
        ("vlan:60", tuple(CLIENT_IP)),
        ("department:Arşiv", tuple(CLIENT_IP)),
    ],
)
def test_queries_the_summary_table_answers(text_: str, tables: tuple[str, ...] | None) -> None:
    found = plan(text_)
    assert found.rollup is not None
    assert found.rollup.tables == tables
    assert found.rollup.kinds == found.kinds
    assert all(condition.field != "type" for condition in found.rollup.conditions)
    assert found.rollup_eligible(NOW - timedelta(hours=13), NOW)
    assert not found.rollup_eligible(NOW - timedelta(hours=12), NOW)


@pytest.mark.parametrize(
    "text_",
    [
        "srcip:172.20.41.5",
        "youtube",
        "type:web sender:172.31.255.1",
        "vlan:70",  # a /25: its /24 holds more than the VLAN
        "vlan:80",  # not a private network: no /24 is counted for it
        "department:Finans host:fw",
    ],
)
def test_queries_it_does_not(text_: str) -> None:
    assert plan(text_).rollup is None


# -- notices and descriptions ---------------------------------------------------------------------


def test_free_text_notices() -> None:
    codes = [n["code"] for n in plan('"İşlem başarısız"').notices]
    assert codes[:2] == ["text_case", "text_truncated"]
    assert [n["code"] for n in plan("failure").notices] == ["text_truncated", "query_not_selective"]
    assert all(
        n["message_tr"] and n["message_en"] for n in plan('"İşlem" department:Finans').notices
    )


def test_descriptions() -> None:
    found = plan('srcip:172.20.41.0/24 -action:accept "youtube"')
    described = found.describe(NOW - timedelta(days=1), NOW)
    assert described == {
        "driver": "typed",
        "tables": list(found.tables),
        "kinds": list(found.kinds),
        "selective": True,
        "rollup_eligible": False,
    }
    query = describe_query(found.query)
    assert query["normalized"] == "srcip:172.20.41.0/24 -action:accept youtube"
    assert query["text"] == ["youtube"]
    assert query["clauses"][1] == {
        "field": "action",
        "op": "match",
        "values": ["accept"],
        "negated": True,
        "start": 21,
        "end": 35,
    }
    assert query["clauses"][2]["op"] == "text"
