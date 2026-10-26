"""The field catalogue (contract 2.4, 3.1): the fields and aliases of the contract, every column
exists, every "index-backed" claim matches an index of the models, the table choice of a query,
and the site's VLANs, departments and network sources."""

from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest
from sqlalchemy import Index
from sqlalchemy.orm import Session

from arcilogger_api.search import FIELDS, SiteNetwork, branch_tables, lookup_field, parse_query
from arcilogger_api.search.fields import (
    ALIASES,
    DepartmentValue,
    VlanValue,
    allowed_kinds,
    catalogue_fields,
    catalogue_kinds,
    dns_name,
    field_key,
    mac_text,
)
from arcilogger_api.search.messages import SearchQueryError
from arcilogger_core.config import Config
from arcilogger_db.kinds import CLIENT_IP, KINDS, TYPED_TABLES
from arcilogger_db.models import TIME_SERIES_TABLES, Base

#: Contract 2.4: every field with its aliases.
CONTRACT_FIELDS = {
    "source": ("kaynak",),
    "sender": ("gonderen",),
    "type": ("tur", "tip"),
    "subtype": ("alttur",),
    "host": ("cihaz",),
    "program": ("konu",),
    "severity": ("onem",),
    "error": ("hata",),
    "srcip": ("kaynakip", "istemci"),
    "dstip": ("hedefip",),
    "natip": ("transip",),
    "ip": ("adres",),
    "srcport": ("kaynakport",),
    "dstport": ("hedefport",),
    "natport": ("transport",),
    "port": (),
    "proto": ("protokol",),
    "action": ("eylem",),
    "domain": ("alanadi", "site"),
    "qname": (),
    "url": (),
    "app": ("uygulama",),
    "appcat": (),
    "category": ("kategori",),
    "service": ("servis",),
    "country": ("ulke",),
    "policy": ("politika",),
    "sessionid": ("oturum",),
    "user": ("kullanici",),
    "mac": (),
    "vlan": (),
    "department": ("departman",),
    "interface": ("arayuz",),
    "qtype": (),
    "attack": ("saldiri",),
    "virus": (),
    "logid": (),
    "level": (),
    "text": ("metin",),
}
_CLIENT_INDEXED = {t for t in CLIENT_IP if t not in ("fg_event", "mt_event")}
#: Contract 2.4 "Idx" (R27, R28, R29): where the field alone is index-backed.
CONTRACT_INDEXED = {
    "source": {"messages", "mt_interface", "mt_config", "mt_event"},
    "type": {"messages", "mt_event"},
    "srcip": _CLIENT_INDEXED,
    "dstip": {"fg_traffic", "mt_fw"},
    "natip": {"fg_traffic"},
    "ip": {"fg_traffic", "mt_fw", "mt_dhcp", "mt_arp", "mt_account"},
    "domain": {"fg_web"},
    "qname": {"fg_dns"},
    "sessionid": {"fg_traffic", "fg_web", "fg_app"},
    "mac": {"mt_dhcp", "mt_arp"},
    "vlan": _CLIENT_INDEXED,
    "department": _CLIENT_INDEXED,
}


def test_the_fields_and_aliases_of_the_contract() -> None:
    assert {name: entry.aliases for name, entry in FIELDS.items()} == CONTRACT_FIELDS
    names = [key for entry in FIELDS.values() for key in (entry.name, *entry.aliases)]
    assert len(names) == len(set(names))  # every name and alias is unique
    assert all(key.isascii() and key == key.lower() for key in names)
    assert set(ALIASES) == set(names)


def test_every_column_exists() -> None:
    for entry in FIELDS.values():
        assert entry.columns, entry.name
        for table, columns in entry.columns.items():
            assert table in TIME_SERIES_TABLES, (entry.name, table)
            for column in columns:
                assert column in Base.metadata.tables[table].c, (entry.name, table, column)
            assert entry.column_objects(table) == tuple(
                Base.metadata.tables[table].c[column] for column in columns
            )
        assert entry.tables == tuple(t for t in TIME_SERIES_TABLES if t in entry.columns)
        if entry.scope == "messages":
            assert set(entry.columns) == {"messages"}, entry.name
        if entry.scope == "typed":
            assert "messages" not in entry.columns, entry.name
    assert set(FIELDS["source"].columns) == set(TIME_SERIES_TABLES)
    assert set(FIELDS["type"].columns) == set(TIME_SERIES_TABLES)
    for name in ("srcip", "vlan", "department"):
        assert {t: c[0] for t, c in FIELDS[name].columns.items()} == dict(CLIENT_IP), name
    assert FIELDS["ip"].columns["fg_traffic"] == ("srcip", "dstip", "transip")
    assert FIELDS["ip"].columns["mt_fw"] == ("src", "dst")
    assert FIELDS["policy"].columns == {"fg_traffic": ("policyid", "policyname")}


def _leading(index: Index) -> str:
    """The first column (or expression text) of an index."""
    first = next(iter(index.expressions))
    return str(getattr(first, "name", None) or first)


def test_every_index_claim_matches_an_index_of_the_models() -> None:
    claims = {name: set(entry.indexed) for name, entry in FIELDS.items() if entry.indexed}
    assert claims == CONTRACT_INDEXED
    for name, tables in claims.items():
        for table in tables:
            leading = {_leading(index) for index in Base.metadata.tables[table].indexes}
            for column in FIELDS[name].columns[table]:
                found = column in leading or any(
                    f"({column})" in expression for expression in leading
                )
                assert found, (name, table, column, leading)
    assert "reverse(lower(hostname))" in {
        _leading(index) for index in Base.metadata.tables["fg_web"].indexes
    }


def test_the_catalogue_entries() -> None:
    fields = catalogue_fields()
    assert [entry["name"] for entry in fields] == list(FIELDS)
    for entry in fields:
        assert set(entry) == {
            "name",
            "aliases",
            "kind",
            "label_tr",
            "label_en",
            "help_tr",
            "help_en",
            "tables",
            "indexed",
            "examples",
        }
        for key in ("label_tr", "label_en", "help_tr", "help_en"):
            assert entry[key].strip() == entry[key], (entry["name"], key)
            assert entry[key], (entry["name"], key)
        assert entry["help_tr"].endswith("."), entry["name"]
        assert entry["help_en"].endswith("."), entry["name"]
        assert entry["help_tr"] != entry["help_en"], entry["name"]
        assert entry["examples"], entry["name"]
        for example in entry["examples"]:
            parse_query(example)  # every example of the syntax help parses
    assert FIELDS["department"].label_tr == "Departman (VLAN'a göre)"
    kinds = catalogue_kinds()
    assert [kind["key"] for kind in kinds] == list(KINDS)
    assert kinds[1] == {"key": "web", "label_tr": "Web filtresi", "label_en": "Web filter"}


@pytest.mark.parametrize(
    ("typed", "name"),
    [
        ("Kaynak", "source"),
        ("KAYNAKİP", "srcip"),
        ("kaynakıp", "srcip"),
        ("Tür", "type"),
        ("TİP", "type"),
        ("gönderen", "sender"),
        ("ÖNEM", "severity"),
        ("alanadı", "domain"),
        ("Ülke", "country"),
        ("kullanıcı", "user"),
        ("ARAYÜZ", "interface"),
        ("saldırı", "attack"),
        ("SrcIP", "srcip"),
    ],
)
def test_field_names_are_folded(typed: str, name: str) -> None:
    found = lookup_field(typed)
    assert found is not None
    assert found.name == name
    assert lookup_field("nosuchfield") is None
    assert field_key("Şİ") == "si"


def test_dns_names_and_macs() -> None:
    assert dns_name(".Example.COM.") == "example.com"
    assert dns_name("bücher.example") == "xn--bcher-kva.example"
    assert dns_name("_dmarc.example.com") == "_dmarc.example.com"
    for bad in ("", "..", "a..b", "-a.example", "a b.example", "x" * 64 + ".example"):
        assert dns_name(bad) is None, bad
    assert mac_text("0000.5E00.5321") == "00:00:5e:00:53:21"
    assert mac_text("00:00:5e:00:53") is None


# -- tables ---------------------------------------------------------------------------------------


def _tables(text: str) -> tuple[str, ...] | None:
    return branch_tables(parse_query(text).clauses)


def test_the_tables_a_query_reads() -> None:
    assert _tables("") is None
    assert _tables('type:web source:fortigate host:fw1 "login failure" severity:<=err') is None
    assert _tables("srcip:172.20.41.105 type:web action:blocked") == ("fg_web",)
    assert _tables("type:dhcp mac:00:00:5e:00:53:21") == ("mt_dhcp", "mt_event")
    assert _tables("mac:00:00:5e:00:53:21") == ("mt_dhcp", "mt_arp", "mt_fw", "mt_event")
    assert _tables("-type:wifi,dhcp,heartbeat mac:*") == ("mt_arp", "mt_fw", "mt_event")
    assert _tables("-type:log -type:wifi,dhcp,heartbeat mac:*") == ("mt_arp", "mt_fw")
    assert _tables("domain:example.com") == ("fg_web", "fg_dns", "fg_app", "fg_virus")
    assert _tables("dstport:3389 action:deny") == (
        "fg_traffic",
        "fg_web",
        "fg_dns",
        "fg_app",
        "fg_ips",
        "fg_virus",
    )
    assert _tables("vlan:41") == tuple(t for t in TYPED_TABLES if t in CLIENT_IP)
    assert _tables("natip:198.51.100.7 natport:40123") == ("fg_traffic",)
    assert _tables("-action:accept") == (
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
    )


@pytest.mark.parametrize(
    ("text", "fields"),
    [
        ("srcip:172.20.41.5 type:syslog", "srcip, type"),
        ("qname:www.example.com url:login", "qname, url"),
        ("type:traffic mac:*", "type, mac"),
        ("type:web -type:web srcip:*", "type, srcip"),
    ],
)
def test_fields_no_table_has_together(text: str, fields: str) -> None:
    with pytest.raises(SearchQueryError) as caught:
        _tables(text)
    assert caught.value.code == "search_fields_incompatible"
    assert caught.value.params == {"fields": fields}
    assert (caught.value.start, caught.value.end) == (0, len(text))


def test_allowed_kinds() -> None:
    assert allowed_kinds(parse_query("").clauses) == frozenset(KINDS)
    assert allowed_kinds(parse_query("type:web,dns type:dns,app").clauses) == {"dns"}
    assert allowed_kinds(parse_query("-type:web").clauses) == frozenset(KINDS) - {"web"}


# -- the site's data -------------------------------------------------------------------------------


def test_the_example_site_network(db_session: Session, tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path / "secrets"),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "ap", "type": "generic", "address": "172.20.10.0/24"},
            ],
        }
    )
    site = SiteNetwork.load(db_session, config)
    assert set(site.vlans) == {10, 20, 41, 42, 43, 60, 61}  # the transit /30 has no VLAN id
    assert site.vlans[41] == ipaddress.ip_network("172.20.41.0/24")
    assert site.network_sources == {"ap"}
    assert site.is_network_source("ap")
    assert not site.is_network_source("fortigate")
    finans = ipaddress.ip_network("172.20.41.0/24")
    assert site.vlan_networks(VlanValue(41), 0, 7) == (finans,)
    assert site.department_networks(DepartmentValue("FINANS"), 0, 1) == (finans,)
    idari = site.department_networks(DepartmentValue("İDARİ İŞLER"), 0, 1)
    assert idari == (ipaddress.ip_network("172.20.43.0/24"),)
    by_id = next(d.id for d in site.departments if d.name == "Planlama")
    assert site.department_networks(DepartmentValue(str(by_id)), 0, 1) == (
        ipaddress.ip_network("172.20.42.0/24"),
    )
    with pytest.raises(SearchQueryError) as caught:
        site.vlan_networks(VlanValue(99), 3, 10)
    assert (caught.value.code, caught.value.params) == ("search_vlan_unknown", {"vlan": 99})
    assert (caught.value.start, caught.value.end) == (3, 10)
    with pytest.raises(SearchQueryError) as caught:
        site.department_networks(DepartmentValue("Muhasebe"), 0, 1)
    assert caught.value.code == "search_department_unknown"
    assert caught.value.params == {"department": "Muhasebe"}
    assert SiteNetwork.load(db_session).network_sources == frozenset()
