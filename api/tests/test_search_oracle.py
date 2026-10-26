"""The oracle (contract 3.2): the search API answers exactly what the matcher says.

Real ingest loads one load-generator day (``tools/loadgen``, synthetic) and every format fixture
(``tests/fixtures``, received the same day; their device times are a month earlier, so ingest
takes the receive time), plus the lines of a network-source member and of an unknown sender.
Every row is read back and evaluated by :mod:`arcilogger_api.search.matcher` — the Python twin
of the SQL, which Canlı Akış uses — for queries hypothesis draws from the field catalogue and
from values that are really in the data. The API's answer must equal it: the rows of every page
concatenated (references and order), the total, the histogram and the five facets; the
statistics from the summary table (``rollup``, ``hybrid``) must equal direct counting. So the
SQL of the planner and the live tail's matcher agree.

The summary table is filled with ``searchcounts.rebuild_stream`` per file (the loader's own
``counts`` step is WP-A2's; :func:`test_the_loader_counts_equal_direct_counting` compares its
counts without the rebuild once the step exists).
"""

from __future__ import annotations

import importlib.util
import ipaddress
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.app import create_app
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.deps import AppState
from arcilogger_api.search import FIELDS, SearchQueryError, compile_matcher, parse_query
from arcilogger_api.search.cursor import utc_text
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.matcher import Record
from arcilogger_api.search.planner import plan_query
from arcilogger_api.search.stats import MULTIPLE, SiteLabels, bucket_seconds, bucket_starts
from arcilogger_api.testing import login, make_user
from arcilogger_core.config import Config
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import SITE_TZ, day_window
from arcilogger_db import kinds
from arcilogger_db.engine import session_factory
from arcilogger_db.models import TIME_SERIES_TABLES, Base, IngestOffset
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_ingest import loader
from arcilogger_ingest.service import IngestService

ROOT = Path(__file__).resolve().parents[2]
LOADGEN = ROOT / "tools" / "loadgen" / "arcilogger_loadgen.py"
FIXTURES = ROOT / "tests" / "fixtures"
DAY = date(2026, 10, 20)
DAY_START, DAY_END = day_window(DAY)
API = "/api/v1/search"
#: Network source ``ap`` (generic) and one of its members; an unknown FortiGate.
AP_MEMBER = "ap-172.20.10.50"
UNKNOWN = "unknown-198.51.100.77"
PAGE = 500


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen_oracle", LOADGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines(path: Path) -> list[bytes]:
    return [
        line
        for line in path.read_bytes().splitlines()
        if line.strip() and not line.startswith(b"#")
    ]


def _mikrotik_print_sample() -> list[bytes]:
    lines = []
    for row in (
        (FIXTURES / "format" / "mikrotik-log-print-sample.txt").read_text("utf-8").splitlines()
    ):
        topics, _, message = row.partition("|")
        if not message:
            continue
        topics_text = ",".join(topics.strip().split(";"))
        lines.append(
            f"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 {topics_text} {message.strip()}".encode()
        )
    return lines


#: Fixture lines per spool folder (sender, messages).
def _fixture_lines() -> dict[str, tuple[str, list[bytes]]]:
    fortigate = [
        *_lines(FIXTURES / "format" / "fortigate-sample.log"),
        *_lines(FIXTURES / "synthetic" / "fortigate-synthetic.log"),
    ]
    mikrotik = [
        *_mikrotik_print_sample(),
        *_lines(FIXTURES / "synthetic" / "mikrotik-wire-synthetic.log"),
    ]
    return {
        "fortigate": ("172.31.255.1", fortigate),
        "core-r1": ("172.20.10.1", mikrotik),
        AP_MEMBER: (
            "172.20.10.50",
            [
                b"<14>Oct 20 10:15:00 ap-kat2 hostapd: wlan0: STA 00:00:5e:00:53:77 associated",
                b"<11>Oct 20 10:16:00 ap-kat2 kernel: Interface eth0 link down \xc4\xb0\xc5\x9f",
            ],
        ),
        UNKNOWN: ("198.51.100.77", fortigate[:3]),
    }


def _config(root: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": "Örnek A.Ş. (arama kâhini)",
            "data_dir": str(root / "data"),
            "secrets_dir": str(root / "secrets"),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
                {"name": "switch-1", "type": "mikrotik", "address": "172.20.10.21"},
                {"name": "ap", "type": "generic", "address": "172.20.10.48/28", "expected": False},
            ],
            "ingest": {"batch_rows": 2000, "batch_seconds": 0.05, "poll_seconds": 0.02},
        }
    )


def _write_spool(config: Config) -> None:
    lg = loadgen()
    settings_ = lg.Settings(seed=5651, users=8, intensity=8.0, guests_per_day=1)
    (output,) = list(lg.Simulation(settings_).run(DAY, DAY))
    lg.write_spool(config.data_dir, [output], addresses=lg.senders(settings_), not_production=True)
    late = datetime.combine(DAY, time(23, 30), tzinfo=SITE_TZ)
    for folder, (sender, messages) in _fixture_lines().items():
        path = config.data_dir / "spool" / folder / f"{DAY.isoformat()}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for index, message in enumerate(messages):
                handle.write(format_line(late + timedelta(seconds=7 * index), sender, message))


def _rows(session: Session, table: str) -> list[dict[str, Any]]:
    found = Base.metadata.tables[table]
    return [dict(row) for row in session.execute(select(found)).mappings()]


@dataclass
class Oracle:
    """The loaded day: every line as a record, and the API to ask."""

    records: list[Record]
    site: SiteNetwork
    labels: SiteLabels
    client: TestClient
    engine: Engine
    factory: sessionmaker[Session]
    values: dict[str, list[str]]


def _records(
    session: Session,
) -> tuple[list[dict[str, Any]], dict[tuple[Any, ...], tuple[str, dict[str, Any]]]]:
    messages = _rows(session, "messages")
    typed: dict[tuple[Any, ...], tuple[str, dict[str, Any]]] = {}
    for table in kinds.TYPED_TABLES:
        for row in _rows(session, table):
            key = (row["time"], row["source"], row["raw_day"], row["raw_line"])
            assert key not in typed, "at most one typed row per line"
            typed[key] = (table, row)
    return messages, typed


def _values(
    messages: list[dict[str, Any]], typed: dict[Any, tuple[str, dict[str, Any]]]
) -> dict[str, list[str]]:
    """Values that are in the data, per value form (the query strategy draws from them)."""
    found: dict[str, set[str]] = {}

    def add(form: str, value: Any) -> None:
        if value is not None and str(value).strip():
            found.setdefault(form, set()).add(str(value))

    for table, row in typed.values():
        for name, entry in FIELDS.items():
            for column in entry.columns.get(table, ()):
                value = row.get(column)
                if value is None or name in ("type", "vlan", "department"):
                    continue
                add(name, value)
    for row in messages:
        add("source", row["source"])
        add("sender", row["sender"])
        add("host", row["host"])
        add("program", row["program"])
        add("subtype", row["subtype"])
        add("error", row["parse_error"])
        for word in str(row["text"]).replace('"', " ").split():
            if 3 <= len(word) <= 20 and ":" not in word and "\\" not in word:
                add("text", word)
    return {name: sorted(values) for name, values in found.items()}


@pytest.fixture
def oracle(tmp_path: Path, db_engine: Engine) -> Iterator[Oracle]:
    config = _config(tmp_path)
    _write_spool(config)
    assert IngestService(config, db_engine).run(once=True) == 0
    factory = session_factory(db_engine)
    with factory() as session:
        files = session.execute(select(IngestOffset.source, IngestOffset.day)).all()
        for source, day in files:
            rebuild_stream(session.connection(), source, day)
        session.commit()
        messages, typed = _records(session)
        site = SiteNetwork.load(session, config)
        names = {
            vlan_id: name
            for vlan_id, name in session.execute(text("SELECT vlan_id, name_tr FROM vlans"))
            if vlan_id is not None
        }
    assert len(messages) > 4000
    records = []
    for message in messages:
        key = (message["time"], message["source"], message["raw_day"], message["raw_line"])
        table, row = typed.get(key, (None, None))
        records.append(Record(message, table, row))
    app = create_app(config, db_engine, audit_flush_seconds=None)
    state: AppState = app.state.arci
    app_factory = state.session_factory()
    with TestClient(app, base_url="http://testserver") as client:
        user = make_user(app_factory, "admin")
        login(client, app_factory, user)
        with app_factory() as session:
            accept_privacy(session, user.id, privacy_version(session), ip=None)
            session.execute(
                text("UPDATE settings SET value = '600' WHERE key = 'search.runs_per_minute'")
            )
            session.commit()
        yield Oracle(
            records,
            site,
            SiteLabels(site, names),
            client,
            db_engine,
            app_factory,
            _values(messages, typed),
        )


# -- what the oracle expects ----------------------------------------------------------------------


def _key(record: Record, typed: bool) -> tuple[str, datetime, int, int]:
    """``(table, time, rank, id)`` of the row that represents a record."""
    if typed:
        assert record.table is not None
        assert record.row is not None
        return (
            record.table,
            record.row["time"],
            TIME_SERIES_TABLES.index(record.table),
            record.row["id"],
        )
    return "messages", record.message["time"], 0, record.message["id"]


def expected_rows(oracle: Oracle, text_: str, start: datetime, end: datetime) -> list[Any]:
    query = parse_query(text_)
    matcher = compile_matcher(query, oracle.site)
    found = [
        _key(record, matcher.typed)
        for record in oracle.records
        if start <= record.message["time"] < end and matcher(record)
    ]
    found.sort(key=lambda key: (-key[1].timestamp(), key[2], -key[3]))
    return found


def _facet(counts: Counter[Any]) -> tuple[list[tuple[str, int]], int]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
    return [(str(value), count) for value, count in ordered[:10]], sum(c for _, c in ordered[10:])


def expected_stats(oracle: Oracle, text_: str, start: datetime, end: datetime) -> dict[str, Any]:
    query = parse_query(text_)
    matcher = compile_matcher(query, oracle.site)
    seconds = bucket_seconds(end - start)
    step = timedelta(seconds=seconds)
    starts = bucket_starts(start, end, seconds)
    buckets: Counter[datetime] = Counter()
    facets: dict[str, Counter[Any]] = {
        name: Counter() for name in ("source", "kind", "action", "vlan", "department")
    }
    for record in oracle.records:
        moment = record.message["time"]
        if not (start <= moment < end and matcher(record)):
            continue
        index = int((moment - starts[0]) // step)
        buckets[starts[index]] += 1
        facets["source"][record.message["source"]] += 1
        facets["kind"][kinds.kind_of(record.message["type"], record.message["subtype"])] += 1
        if record.table is not None and record.row is not None:
            if record.table in kinds.ACTION_TABLES and record.row.get("action") is not None:
                facets["action"][kinds.ascii_lower(str(record.row["action"]))] += 1
            client = kinds.CLIENT_IP.get(record.table)
            address = record.row.get(client) if client else None
            if address is not None:
                ip = ipaddress.ip_interface(str(address)).ip
                if ip.version == 4:
                    net = ipaddress.ip_network(f"{ip}/24", strict=False)
                    vlan = oracle.labels.vlan_of_net(net)
                    if vlan is not None:
                        facets["vlan"][vlan] += 1
                    department = oracle.labels.department_of_net(net)
                    if department is not None:
                        facets["department"][department] += 1
    return {
        "total": sum(buckets.values()),
        "buckets": [(bucket, buckets.get(bucket, 0)) for bucket in starts],
        "facets": {name: _facet(counter) for name, counter in facets.items()},
        "typed": matcher.typed,
    }


# -- asking the API -------------------------------------------------------------------------------


def run(oracle: Oracle, text_: str, start: datetime, end: datetime) -> tuple[int, Any]:
    response = oracle.client.post(
        API, json={"q": text_, "from": start.isoformat(), "to": end.isoformat(), "limit": PAGE}
    )
    return response.status_code, response.json()


def all_rows(oracle: Oracle, page: dict[str, Any]) -> list[Any]:
    keys = [item["key"] for item in page["items"]]
    while page["next_cursor"] is not None:
        response = oracle.client.post(
            f"{API}/page", json={"search_id": page["search_id"], "cursor": page["next_cursor"]}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        keys += [item["key"] for item in page["items"]]
    return keys


def as_keys(expected: list[Any]) -> list[str]:
    return [f"{table}:{utc_text(moment)}:{row_id}" for table, moment, _, row_id in expected]


def stats(oracle: Oracle, search_id: str) -> dict[str, Any]:
    response = oracle.client.post(f"{API}/stats", json={"search_id": search_id})
    assert response.status_code == 200, response.text
    return response.json()


def check_stats(answer: dict[str, Any], expected: dict[str, Any]) -> None:
    assert answer["complete"] is True
    assert answer["total"] == {"value": expected["total"], "exact": True}
    got = [(datetime.fromisoformat(b["start"]), b["count"]) for b in answer["histogram"]["buckets"]]
    assert got == expected["buckets"]
    for name, (values, other) in expected["facets"].items():
        facet = answer["facets"][name]
        if facet["status"] == "unavailable":
            assert answer["mode"] == "direct", name
            assert not expected["typed"], name
            assert name in ("action", "vlan", "department")
            continue
        assert facet["status"] == "ok", name
        assert [(v["value"], v["count"]) for v in facet["values"]] == values, name
        assert facet["other"] == other, name


# -- the strategy ---------------------------------------------------------------------------------


def _word(value: str) -> str:
    """The ASCII letters and digits of a value (at least 3: a free-text term or a pattern)."""
    word = "".join(char for char in value if char.isascii() and char.isalnum())
    return word if len(word) >= 3 else "example"


def _value(oracle: Oracle, name: str) -> st.SearchStrategy[str]:
    entry = FIELDS[name]
    form = entry.form
    seen = oracle.values.get(name, [])
    if form == "kind":
        return st.sampled_from(sorted(kinds.KINDS))
    if form == "vlan":
        return st.sampled_from([str(v) for v in sorted(oracle.site.vlans)])
    if form == "department":
        return st.sampled_from([d.name for d in oracle.site.departments]).map(
            lambda name: f'"{name}"' if " " in name else name
        )
    if form == "severity":
        return st.sampled_from(["err", "<=warning", "info", "2-5", ">=notice"])
    if form == "proto":
        return st.sampled_from(["tcp", "udp", "icmp", "6", "47"])
    if not seen:
        return st.just("*")
    sample = st.sampled_from(seen)
    if form == "ip":

        def network(value: str) -> str:
            ip = ipaddress.ip_interface(value).ip
            return f"{ip}" if ip.version == 6 else ".".join(str(ip).split(".")[:3]) + ".0/24"

        return st.one_of(
            sample.map(lambda v: str(ipaddress.ip_interface(v).ip)), sample.map(network)
        )
    if form in ("int", "port"):
        return st.one_of(sample, sample.map(lambda v: f">={v}"), sample.map(lambda v: f"<{v}"))
    if form == "mac":
        return st.one_of(sample, sample.map(lambda v: v[:8] + ":*"))
    if form == "domain":
        return st.one_of(
            sample,
            sample.map(lambda v: "*." + ".".join(v.split(".")[-2:])),
            sample.map(lambda v: ".".join(v.split(".")[-2:])),
        )
    if form in ("text", "source", "url"):
        quoted = sample.map(lambda v: '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"')
        if form == "source":
            return st.one_of(sample, sample.map(lambda v: v[:4] + "*"), st.just("ap"))
        if form == "url":
            return st.one_of(quoted, sample.map(lambda v: f'"{v[1:4]}"'))
        words = sample.map(_word)
        return st.one_of(
            quoted,
            words.map(lambda w: w[: len(w) // 2 + 1] + "*"),
            words.map(lambda w: "*" + w[len(w) // 2 :]),
            words.map(lambda w: "*" + w[1:-1] + "*"),
        )
    if form == "freetext":
        return sample.map(_word).map(lambda w: w.swapcase() if len(w) % 2 else w)
    if form == "error":
        return st.one_of(sample, sample.map(lambda v: v.split(".")[0] + ".*"), st.just("*"))
    if form == "policy":
        return st.sampled_from(["12", '"Internet"', "Inter*"])
    return sample.filter(lambda v: '"' not in v and "\\" not in v and "," not in v and " " not in v)


@st.composite
def queries(draw: st.DrawFn, oracle: Oracle) -> str:
    names = sorted(FIELDS)
    clauses = []
    for _ in range(draw(st.integers(0, 3))):
        name = draw(st.sampled_from(names))
        value = draw(_value(oracle, name))
        sign = draw(st.sampled_from(["", "", "-"]))
        clauses.append(f"{sign}{name}:{value}")
    return " ".join(clauses)


@st.composite
def ranges(draw: st.DrawFn) -> tuple[datetime, datetime]:
    """The whole day (summary table), or a part of it (minutes: direct)."""
    if draw(st.booleans()):
        return DAY_START, DAY_END
    first = draw(st.integers(0, 23 * 60))
    length = draw(st.sampled_from([5, 47, 90, 130, 600, 800]))
    start = DAY_START + timedelta(minutes=first)
    return start, min(DAY_END, start + timedelta(minutes=length))


# -- the properties -------------------------------------------------------------------------------


def check(oracle: Oracle, text_: str, start: datetime, end: datetime) -> None:
    status, answer = run(oracle, text_, start, end)
    event(f"status {status}")
    refused: SearchQueryError | None = None
    try:
        query = parse_query(text_)
        compile_matcher(query, oracle.site)
        plan_query(query, oracle.site)
    except SearchQueryError as exc:
        refused = exc
    if refused is not None:
        assert status == 422, (text_, answer)
        assert answer["error"]["code"] == refused.code
        return
    assert status == 200, (text_, answer)
    expected = expected_rows(oracle, text_, start, end)
    event("rows" if expected else "no rows")
    assert all_rows(oracle, answer) == as_keys(expected), text_
    check_stats(stats(oracle, answer["search_id"]), expected_stats(oracle, text_, start, end))


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(data=st.data())
def test_the_api_answers_what_the_matcher_says(oracle: Oracle, data: st.DataObject) -> None:
    text_ = data.draw(queries(oracle), label="q")
    start, end = data.draw(ranges(), label="range")
    check(oracle, text_, start, end)


#: Hand-picked queries that reach every driver, join and statistics mode.
EXAMPLES = (
    "",
    "type:web",
    "-type:heartbeat",
    "type:dhcp",
    "type:dhcp,arp mac:00:00:5e:*",
    "srcip:172.20.41.0/24",
    "srcip:172.20.41.0/24 type:web action:blocked",
    "action:blocked",
    "-action:accept",
    "domain:example",
    "domain:*.portal.example",
    "vlan:41,42",
    "department:Finans -action:passthrough",
    "source:core-r1",
    "source:ap",
    "sender:198.51.100.77",
    "srcip:172.20.60.0/24 youtube",
    "dhcp",
    "ip:100.64.12.9",
    "dstport:443 proto:tcp",
    "error:*",
    "severity:<=warning",
    "host:FIREWALL-01 -type:traffic",
    "qtype:a",
    "interface:*",
)


@pytest.mark.parametrize("hours", [24, 9])
def test_hand_picked_queries(oracle: Oracle, hours: int) -> None:
    start = DAY_START if hours == 24 else DAY_START + timedelta(hours=8, minutes=17)
    end = start + timedelta(hours=hours)
    for text_ in EXAMPLES:
        check(oracle, text_, start, end)


def test_the_summary_table_equals_direct_counting(oracle: Oracle) -> None:
    """Rollup (whole hours) and hybrid (partial edge hours) against the direct count."""
    eligible = [
        "",
        "type:web",
        "-type:heartbeat",
        "action:blocked",
        "vlan:41,43",
        "department:Planlama",
        "source:core-r1 -type:arp",
        "-action:accept type:traffic,web",
    ]
    for text_ in eligible:
        for start, end, mode in (
            (DAY_START, DAY_END, "rollup"),
            (DAY_START + timedelta(minutes=17), DAY_END - timedelta(minutes=29), "hybrid"),
        ):
            status, page = run(oracle, text_, start, end)
            assert status == 200, page
            assert page["plan"]["rollup_eligible"] is True, text_
            rolled = stats(oracle, page["search_id"])
            assert rolled["mode"] == mode, text_
            check_stats(rolled, expected_stats(oracle, text_, start, end))


@pytest.mark.skipif(
    "counts" not in loader.STEPS, reason="WP-A2: the ingest loader's counts step is not merged yet"
)
def test_the_loader_counts_equal_direct_counting(tmp_path: Path, db_engine: Engine) -> None:
    """Without any rebuild: the counts the loader wrote with the rows answer like the rows."""
    config = _config(tmp_path)
    _write_spool(config)
    assert IngestService(config, db_engine).run(once=True) == 0
    app = create_app(config, db_engine, audit_flush_seconds=None)
    state: AppState = app.state.arci
    factory = state.session_factory()
    with TestClient(app, base_url="http://testserver") as client:
        user = make_user(factory, "admin")
        login(client, factory, user)
        with factory() as session:
            accept_privacy(session, user.id, privacy_version(session), ip=None)
            session.commit()
        for text_ in ("", "type:web action:blocked", "vlan:41"):
            body = {"q": text_, "from": DAY_START.isoformat(), "to": DAY_END.isoformat()}
            page = client.post(API, json=body).json()
            rolled = client.post(f"{API}/stats", json={"search_id": page["search_id"]}).json()
            assert rolled["mode"] == "rollup"
            with factory() as session:
                from arcilogger_api.search.stats import run_stats

                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                plan = plan_query(parse_query(text_), SiteNetwork.load(session, config))
                direct = run_stats(
                    session,
                    plan,
                    DAY_START,
                    DAY_END,
                    labels=SiteLabels(SiteNetwork.load(session, config)),
                    budget_seconds=30,
                    sample_rows=1_000_000,
                    direct=True,
                )
                session.rollback()
            assert rolled["total"]["value"] == direct["total"]["value"]
            assert [b["count"] for b in rolled["histogram"]["buckets"]] == [
                b["count"] for b in direct["histogram"]["buckets"]
            ]


def test_vlan_mapping_of_a_net() -> None:
    """A /24 inside a VLAN is that VLAN; one holding smaller VLANs is several; public is none."""
    site = SiteNetwork(
        vlans={
            41: ipaddress.ip_network("172.20.41.0/24"),
            70: ipaddress.ip_network("172.20.70.0/25"),
            71: ipaddress.ip_network("172.20.70.128/25"),
            90: ipaddress.ip_network("172.20.0.0/16"),
        }
    )
    labels = SiteLabels(site, {41: "Finans"})
    assert labels.vlan_of_net("172.20.41.0/24") == 41
    assert labels.vlan_of_net("172.20.70.0/24") == MULTIPLE
    assert labels.vlan_of_net("172.20.99.0/24") == 90
    assert labels.vlan_of_net("203.0.113.0/24") is None
    assert labels.vlan_labels(41) == ("VLAN 41 (Finans)", "VLAN 41 (Finans)")
    assert labels.vlan_labels(MULTIPLE)[0] == "Birden çok VLAN"
