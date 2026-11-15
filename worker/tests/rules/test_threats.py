"""Threat and health rules R05, R06, R07, R17, R18 (W6-D, contract §2.2.3, §3 T-R, T-A7, §0.4
V3, V4, V5, V7).

Every row is built from a log line through the real parsers (:mod:`arcilogger_ingest.parsers`)
and inserted with its raw reference, then the rule's ``evaluate`` runs on a slice. The lines are
the repository's fixtures (``tests/fixtures/format/``, ``tests/fixtures/synthetic/``) with their
times moved, or synthetic lines in the same FortiOS ``format default`` shape where no fixture
exists (marked below; every value fictional, "sahada doğrula"). When a parser stops producing
what a rule reads, the format tests fail with the id of the assumption (V3 …) in the message.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from ipaddress import ip_network
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import site_day
from arcilogger_db.models import Alert, FgEvent, FgIps, FgTraffic, FgVirus, MtEvent
from arcilogger_ingest.parsers import LinePosition, parse_line
from arcilogger_worker.rules.engine import run_rules
from arcilogger_worker.rules.model import Actions, Hit, RuleContext, RuleDef, RuleSettings
from arcilogger_worker.rules.texts import COMMON_FIELDS, TEXTS, title, title_fields
from arcilogger_worker.rules.threats import EVIDENCE_LIMIT, RULES

from .cases import CASES, SLICE_END, CaseRow, RuleCase

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
TR = timezone(timedelta(hours=3))
#: End of the slice under test; the slice is the minute before it.
END = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
START = END - timedelta(seconds=60)
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
MODELS: Mapping[str, Any] = {
    "fg_ips": FgIps,
    "fg_virus": FgVirus,
    "fg_event": FgEvent,
    "fg_traffic": FgTraffic,
    "mt_event": MtEvent,
}


def fixture_line(path: str, contains: str) -> bytes:
    """The first non-comment line of ``tests/fixtures/<path>`` that contains ``contains``."""
    for line in (FIXTURES / path).read_bytes().splitlines():
        if not line.startswith(b"#") and contains.encode() in line:
            return line
    raise AssertionError(f"no line with {contains!r} in {path}")


#: tests/fixtures/synthetic/fortigate-synthetic.log, the IPS line (severity high, attack 99001).
IPS = fixture_line("synthetic/fortigate-synthetic.log", 'subtype="ips"')
#: tests/fixtures/synthetic/fortigate-synthetic.log, the antivirus line (eicar, 99002).
VIRUS = fixture_line("synthetic/fortigate-synthetic.log", 'subtype="virus"')
#: tests/fixtures/format/fortigate-sample.log, the performance statistics (cpu=3 mem=31).
PERF = fixture_line("format/fortigate-sample.log", 'logid="0100040704"')
#: tests/fixtures/synthetic/mikrotik-wire-synthetic.log, the pool-empty line (dhcp60, pool60).
POOL_EMPTY = fixture_line("synthetic/mikrotik-wire-synthetic.log", "is empty")
#: tests/fixtures/format/fortigate-sample.log, a forward-traffic line (for R06's policy part).
TRAFFIC = fixture_line("format/fortigate-sample.log", 'type="traffic"')
#: Synthetic (no fixture yet, sahada doğrula, V4): a system event announcing conserve mode, in
#: the header of the format sample; logid and text are placeholders.
CONSERVE = (
    b'<185>date=2026-09-23 time=09:41:00 devname="FIREWALL-01" devid="FGT0000000000000" '
    b'eventtime=1790145660000000000 tz="+0300" logid="9900000060" type="event" '
    b'subtype="system" level="critical" vd="root" logdesc="Kernel enters memory conserve mode" '
    b'msg="Kernel enters memory conserve mode"'
)
#: Synthetic (sahada doğrula, V5): the FortiGate DHCP server announcing an exhausted pool.
FG_POOL = (
    b'<188>date=2026-09-23 time=09:41:00 devname="FIREWALL-01" devid="FGT0000000000000" '
    b'eventtime=1790145660000000000 tz="+0300" logid="9900000061" type="event" '
    b'subtype="system" level="warning" vd="root" logdesc="DHCP server ran out of IP addresses" '
    b'interface="port5" msg="DHCP server ran out of IP addresses on port5"'
)


def retime(line: bytes, when: datetime) -> bytes:
    """``line`` with its device time (``date``/``time``/``eventtime`` or the ISO header) at
    ``when``."""
    local = when.astimezone(TR)
    line = re.sub(rb"date=\S+", b"date=" + local.strftime("%Y-%m-%d").encode(), line, count=1)
    line = re.sub(rb" time=\S+", b" time=" + local.strftime("%H:%M:%S").encode(), line, count=1)
    line = re.sub(
        rb"eventtime=\d+", b"eventtime=%d" % (int(when.timestamp()) * 10**9), line, count=1
    )
    return re.sub(
        rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d",
        local.isoformat(timespec="seconds").encode(),
        line,
        count=1,
    )


def field(line: bytes, name: str, value: str) -> bytes:
    """``line`` with the FortiGate field ``name`` set to ``value`` (quoted)."""
    pattern = rb"\b" + name.encode() + rb'=("[^"]*"|\S+)'
    replacement = name.encode() + b'="' + value.encode() + b'"'
    if re.search(pattern, line):
        return re.sub(pattern, replacement, line, count=1)
    return line + b" " + replacement


class Rows:
    """Parses lines and inserts their typed rows, numbering raw lines per source."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.lines: dict[str, int] = {}

    def add(
        self,
        line: bytes,
        received: datetime,
        *,
        source: str = "fortigate",
        source_type: str = "fortigate",
        device: datetime | None = None,
    ) -> list[tuple[str, int, dict[str, Any]]]:
        line = retime(line, device or received - timedelta(seconds=2))
        number = self.lines[source] = self.lines.get(source, 0) + 1
        sender = FIREWALL if source_type == "fortigate" else ROUTER
        outcome = parse_line(
            RawRecord(received=received, sender=sender, message=line),
            LinePosition(source, source_type, site_day(received), number, number * 1000),  # type: ignore[arg-type]
        )
        assert not outcome.failed, (outcome.error, outcome.detail)
        inserted = []
        for row in outcome.rows:
            model = MODELS[row.table]
            row_id = self.session.execute(
                insert(model).values(**row.values).returning(model.id)
            ).scalar_one()
            inserted.append((row.table, row_id, dict(row.values)))
        self.session.commit()
        return inserted


def rule(rule_id: str) -> RuleDef:
    return next(entry for entry in RULES if entry.id == rule_id)


def settings_of(
    entry: RuleDef, params: Mapping[str, object] | None = None, *, window_seconds: int | None = None
) -> RuleSettings:
    """The rule's defaults with ``params`` (lists as tuples, as the settings reader gives)."""
    values = {p.key: p.default for p in entry.params}
    for key, value in (params or {}).items():
        values[key] = tuple(value) if isinstance(value, list) else value
    return RuleSettings(
        enabled=True,
        severity=entry.severity,
        window_seconds=window_seconds or entry.window_seconds,
        cooldown_seconds=entry.cooldown_seconds,
        actions=Actions(email=True, webhook=True),
        params=values,
    )


def run(
    session: Session,
    rule_id: str,
    params: Mapping[str, object] | None = None,
    *,
    end: datetime = END,
    start: datetime = START,
    window_seconds: int | None = None,
) -> dict[str, Hit]:
    """``evaluate`` of the rule on the slice ``(start, end]`` with its defaults and ``params``."""
    entry = rule(rule_id)
    ctx = RuleContext(
        settings=settings_of(entry, params, window_seconds=window_seconds),
        start=start,
        end=end,
        max_device_skew=timedelta(seconds=3600),
        internal_networks=tuple(
            ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ),
        zone=ZoneInfo("Europe/Istanbul"),
    )
    hits = list(entry.evaluate(session, ctx))
    by_key = {hit.key: hit for hit in hits}
    assert len(by_key) == len(hits), "one hit per key"
    for hit in hits:
        missing = title_fields(TEXTS[rule_id]) - COMMON_FIELDS - set(hit.values)
        assert not missing, f"{rule_id}: the title needs {sorted(missing)}"
        title(rule_id, hit.key, hit.count, hit.values)
    return by_key


Ref = tuple[str, int | None, int | None, int | None]


def refs(inserted: Sequence[tuple[str, int, dict[str, Any]]]) -> list[Ref]:
    """``(table, row id, raw line, raw offset)`` of inserted rows (what ``/raw`` opens)."""
    return [
        (table, row_id, values["raw_line"], values["raw_offset"])
        for table, row_id, values in inserted
    ]


def hit_refs(hit: Hit) -> list[Ref]:
    return [(ref.table, ref.row_id, ref.raw_line, ref.raw_offset) for ref in hit.evidence]


# =================================================================================================
# Registry entries
# =================================================================================================


def test_threat_rules_registered() -> None:
    assert [entry.id for entry in RULES] == ["R05", "R06", "R07", "R17", "R18"]
    kinds = {entry.id: entry.kind for entry in RULES}
    assert kinds == {
        "R05": "event",
        "R06": "event",
        "R07": "event",
        "R17": "condition",
        "R18": "event",
    }
    assert rule("R17").clear_seconds == 600
    assert all(entry.clear_seconds is None for entry in RULES if entry.kind == "event")
    unverified = {entry.id for entry in RULES if not entry.format_verified}
    assert unverified == {"R06", "R17"}, "§2.2.3: R06 and R17 ship 'sahada doğrula'"
    severities = {entry.id: entry.severity for entry in RULES}
    assert severities == {
        "R05": "high",
        "R06": "critical",
        "R07": "high",
        "R17": "high",
        "R18": "high",
    }
    assert rule("R05").cooldown_seconds == 3600
    assert rule("R18").cooldown_seconds == 3600
    for entry in RULES:
        assert entry.inputs
        assert not entry.needs_setup(settings_of(entry)), f"{entry.id} works with its defaults"
        for param in entry.params:
            assert param.label_tr
            assert param.label_en


# =================================================================================================
# R05 — IPS saldırısı
# =================================================================================================


def test_r05_high_severity_opens_each(db_session: Session) -> None:
    rows = Rows(db_session)
    inserted = rows.add(IPS, END - timedelta(seconds=20))
    hits = run(db_session, "R05")
    assert set(hits) == {"198.51.100.7:99001"}
    hit = hits["198.51.100.7:99001"]
    assert hit.count == 1
    assert hit.time == END - timedelta(seconds=20)
    assert hit_refs(hit) == refs(inserted)
    assert hit.values["attack"] == "Synthetic.Signature.Remote.Code.Execution"
    assert hit.values["severity"] == "high"


def test_r05_high_row_before_the_slice_is_not_counted_again(db_session: Session) -> None:
    Rows(db_session).add(IPS, START - timedelta(seconds=1))
    assert run(db_session, "R05") == {}


def test_r05_medium_threshold(db_session: Session) -> None:
    rows = Rows(db_session)
    medium = field(IPS, "severity", "medium")
    for minute in range(9):
        rows.add(medium, START - timedelta(minutes=50 - minute))
    assert run(db_session, "R05") == {}, "no row of the key in the slice"
    rows.add(medium, END - timedelta(seconds=5))
    hits = run(db_session, "R05")
    assert set(hits) == {"198.51.100.7:99001"}
    assert hits["198.51.100.7:99001"].count == 10


def test_r05_medium_just_below_the_threshold(db_session: Session) -> None:
    rows = Rows(db_session)
    medium = field(IPS, "severity", "medium")
    for minute in range(9):
        rows.add(medium, END - timedelta(minutes=50 - minute))
    # The tenth row is older than the window (3600 s).
    rows.add(medium, END - timedelta(seconds=3601))
    assert run(db_session, "R05") == {}


def test_r05_low_severity_is_ignored_and_keys_are_separate(db_session: Session) -> None:
    rows = Rows(db_session)
    rows.add(field(IPS, "severity", "low"), END - timedelta(seconds=10))
    rows.add(field(IPS, "attackid", "99003"), END - timedelta(seconds=9))
    rows.add(IPS.replace(b"srcip=198.51.100.7", b"srcip=198.51.100.8"), END - timedelta(seconds=8))
    assert set(run(db_session, "R05")) == {"198.51.100.7:99003", "198.51.100.8:99001"}


def test_r05_evidence_keeps_the_oldest_up_to_the_limit(db_session: Session) -> None:
    """At most ``EVIDENCE_LIMIT`` (the largest ``rules.evidence_max``) rows, oldest first; the
    count is every row."""
    rows = Rows(db_session)
    inserted = [
        rows.add(IPS, START + timedelta(milliseconds=500 * n))[0]
        for n in range(1, EVIDENCE_LIMIT + 3)
    ]
    hit = run(db_session, "R05")["198.51.100.7:99001"]
    assert hit.count == EVIDENCE_LIMIT + 2
    assert hit_refs(hit) == refs(inserted[:EVIDENCE_LIMIT])
    assert hit.time == START + timedelta(milliseconds=500 * (EVIDENCE_LIMIT + 2))


# =================================================================================================
# R06 — Botnet / tehdit IP
# =================================================================================================


def test_r06_formats(db_session: Session) -> None:
    """V7: botnet detections are ``fg_ips`` rows with ``eventtype`` ``botnet``."""
    rows = Rows(db_session)
    inserted = rows.add(field(IPS, "eventtype", "botnet"), END - timedelta(seconds=10))
    assert [table for table, _, _ in inserted] == ["fg_ips"], (
        "V7: a FortiGate IPS line no longer produces an fg_ips row"
    )
    assert inserted[0][2]["eventtype"] == "botnet", "V7: the IPS eventtype is no longer parsed"
    hits = run(db_session, "R06")
    assert set(hits) == {"198.51.100.7"}, "V7: R06 does not match the parsed botnet row"
    assert hits["198.51.100.7"].values["srcip"] == "198.51.100.7"
    assert hit_refs(hits["198.51.100.7"]) == refs(inserted)


def test_r06_signature_is_not_botnet(db_session: Session) -> None:
    Rows(db_session).add(IPS, END - timedelta(seconds=10))
    assert run(db_session, "R06") == {}


def test_r06_threat_policy(db_session: Session) -> None:
    rows = Rows(db_session)
    traffic = rows.add(TRAFFIC, END - timedelta(seconds=10))
    assert traffic[0][0] == "fg_traffic"
    srcip = str(traffic[0][2]["srcip"])
    policy = str(traffic[0][2]["policyname"])
    assert run(db_session, "R06") == {}, "the policy part is off while the list is empty"
    hits = run(db_session, "R06", {"threat_policy_names": [policy]})
    assert set(hits) == {srcip}
    assert hit_refs(hits[srcip]) == refs(traffic)
    assert run(db_session, "R06", {"threat_policy_names": [policy + "x"]}) == {}


def test_r06_both_parts_add_up_per_address(db_session: Session) -> None:
    rows = Rows(db_session)
    traffic = rows.add(TRAFFIC, END - timedelta(seconds=10))
    srcip = str(traffic[0][2]["srcip"])
    botnet = field(IPS, "eventtype", "botnet").replace(
        b"srcip=198.51.100.7", f"srcip={srcip}".encode()
    )
    rows.add(botnet, END - timedelta(seconds=5))
    hits = run(db_session, "R06", {"threat_policy_names": [str(traffic[0][2]["policyname"])]})
    assert hits[srcip].count == 2
    assert [ref.table for ref in hits[srcip].evidence] == ["fg_traffic", "fg_ips"]


# =================================================================================================
# R07 — Zararlı yazılım
# =================================================================================================


def test_r07_each_row_and_title_values(db_session: Session) -> None:
    rows = Rows(db_session)
    inserted = rows.add(VIRUS, END - timedelta(seconds=10))
    hits = run(db_session, "R07")
    assert set(hits) == {"203.0.113.30:99002"}
    hit = hits["203.0.113.30:99002"]
    assert hit.values["virus"] == "Synthetic.Test.File"
    assert hit.values["srcip"] == "203.0.113.30"
    assert hit_refs(hit) == refs(inserted)
    shown = repr(hit.values)
    for secret in ("eicar.com", "downloads.example.net", "http://"):
        assert secret not in shown, "§2.2.3: the file name and URL stay in the evidence"


def test_r07_ignored_actions(db_session: Session) -> None:
    rows = Rows(db_session)
    rows.add(VIRUS, END - timedelta(seconds=10))  # action="blocked"
    assert run(db_session, "R07", {"ignore_actions": ["blocked"]}) == {}
    assert set(run(db_session, "R07", {"ignore_actions": ["monitored"]})) == {"203.0.113.30:99002"}
    # A row without an action is never ignored.
    without = re.sub(rb' action="[^"]*"', b"", VIRUS).replace(b"virusid=99002", b"virusid=99004")
    rows.add(without, END - timedelta(seconds=5))
    assert set(run(db_session, "R07", {"ignore_actions": ["blocked"]})) == {"203.0.113.30:99004"}


def test_r07_row_before_the_slice(db_session: Session) -> None:
    Rows(db_session).add(VIRUS, START)
    assert run(db_session, "R07") == {}


# =================================================================================================
# R17 — Güvenlik duvarı sağlığı (condition)
# =================================================================================================


def perf(cpu: int, mem: int) -> bytes:
    return PERF.replace(b"cpu=3 ", b"cpu=%d " % cpu).replace(b"mem=31 ", b"mem=%d " % mem)


def samples(rows: Rows, values: Sequence[tuple[int, int]], *, every: int = 60) -> list[Any]:
    """Performance samples every ``every`` seconds, the last one in the slice."""
    inserted = []
    newest = END - timedelta(seconds=10)
    for index, (cpu, mem) in enumerate(values):
        received = newest - timedelta(seconds=every * (len(values) - 1 - index))
        inserted.extend(rows.add(perf(cpu, mem), received))
    return inserted


def test_r17_formats(db_session: Session) -> None:
    """V3: performance statistics are ``fg_event`` rows with logid ``0100040704`` and ``cpu``,
    ``mem`` in ``extra``; V4: conserve mode is an ``fg_event`` row whose ``logdesc`` contains
    ``conserve``."""
    rows = Rows(db_session)
    sample = rows.add(PERF, END - timedelta(seconds=10))
    assert [table for table, _, _ in sample] == ["fg_event"], "V3: perf line is not an fg_event row"
    values = sample[0][2]
    assert values["logid"] == "0100040704", "V3: the performance logid is no longer parsed"
    assert values["extra"].get("cpu") == "3", "V3: cpu is no longer in extra"
    assert values["extra"].get("mem") == "31", "V3: mem is no longer in extra"
    conserve = rows.add(CONSERVE, END - timedelta(seconds=5))
    assert [table for table, _, _ in conserve] == ["fg_event"], "V4: conserve line is not fg_event"
    assert "conserve" in str(conserve[0][2]["logdesc"]).lower(), "V4: logdesc is no longer parsed"
    hits = run(db_session, "R17")
    assert set(hits) == {"fortigate"}, "V4: R17 does not match the conserve-mode row"
    assert hits["fortigate"].values["conserve"] is True
    assert hit_refs(hits["fortigate"]) == refs(conserve)


def test_r17_sustained_cpu(db_session: Session) -> None:
    rows = Rows(db_session)
    inserted = samples(rows, [(20, 30), *[(95, 30)] * 11])
    hits = run(db_session, "R17")
    assert set(hits) == {"fortigate"}
    hit = hits["fortigate"]
    assert hit.count == 11
    assert hit.values["cpu"] == 95
    assert hit.values["mem"] == 30
    assert hit.values["conserve"] is False
    assert hit_refs(hit) == refs(inserted[1:])
    assert hit.time == END - timedelta(seconds=10)


def test_r17_sustained_memory(db_session: Session) -> None:
    rows = Rows(db_session)
    samples(rows, [(10, 90)] * 11)
    assert set(run(db_session, "R17")) == {"fortigate"}


def test_r17_just_below(db_session: Session) -> None:
    rows = Rows(db_session)
    # Ten minutes minus one sample interval above the limit: not sustained yet.
    samples(rows, [(20, 30), *[(95, 30)] * 10])
    assert run(db_session, "R17") == {}


def test_r17_at_the_limit_is_not_above(db_session: Session) -> None:
    samples(Rows(db_session), [(80, 85)] * 12)
    assert run(db_session, "R17") == {}
    assert set(run(db_session, "R17", {"cpu_percent": 79})) == {"fortigate"}


def test_r17_one_good_sample_breaks_the_run(db_session: Session) -> None:
    rows = Rows(db_session)
    samples(rows, [*[(95, 30)] * 8, (40, 30), *[(95, 30)] * 4])
    assert run(db_session, "R17") == {}


def test_r17_cpu_and_memory_are_judged_separately(db_session: Session) -> None:
    rows = Rows(db_session)
    # High CPU, then high memory: neither measure is high in every sample for ten minutes.
    samples(rows, [*[(95, 30)] * 6, *[(30, 95)] * 6])
    assert run(db_session, "R17") == {}


def test_r17_resolved_condition_gives_no_hit(db_session: Session) -> None:
    """The condition is over when the newest sample is below the limits (the engine resolves
    the alert after ``clear_seconds`` without a hit)."""
    rows = Rows(db_session)
    samples(rows, [*[(95, 30)] * 12, (20, 30)])
    assert run(db_session, "R17") == {}


def test_r17_no_sample_in_the_slice(db_session: Session) -> None:
    rows = Rows(db_session)
    for minute in range(12):
        rows.add(perf(95, 30), START - timedelta(minutes=12 - minute))
    assert run(db_session, "R17") == {}


def test_r17_fields_are_parameters(db_session: Session) -> None:
    rows = Rows(db_session)
    line = perf(95, 30).replace(b"cpu=", b"cpu_usage=")
    for index in range(11):
        rows.add(line, END - timedelta(seconds=10 + 60 * (10 - index)))
    assert run(db_session, "R17") == {}
    assert set(run(db_session, "R17", {"cpu_field": ["cpu", "cpu_usage"]})) == {"fortigate"}


def test_r17_text_values_do_not_break_the_slice(db_session: Session) -> None:
    rows = Rows(db_session)
    rows.add(PERF.replace(b"cpu=3 ", b'cpu="n/a" '), END - timedelta(seconds=20))
    rows.add(CONSERVE, END - timedelta(seconds=10))
    assert set(run(db_session, "R17")) == {"fortigate"}


def test_r17_through_the_engine_resolves_itself(
    db_session_factory: sessionmaker[Session],
) -> None:
    """D9: R17 is a condition rule. Its alert stays open while the overload goes on and within
    ``clear_seconds`` (600) after the last hit; the engine then resolves it
    (``condition_cleared``)."""

    def runs(now: datetime) -> dict[str, Any]:
        return run_rules(db_session_factory, now=now, rules=(rule("R17"),)).details()

    first = END - timedelta(minutes=20)
    assert runs(first)["started"] == ["R17"]
    with db_session_factory() as session:
        rows = Rows(session)
        last = first + timedelta(minutes=2)
        for minute in range(13):  # 12 minutes above 80 % CPU, the newest at ``last``
            rows.add(perf(95, 30), last - timedelta(minutes=12 - minute))

    def alert() -> Alert:
        with db_session_factory() as session:
            (found,) = session.scalars(select(Alert).where(Alert.rule == "R17")).all()
            session.expunge(found)
            return found

    assert runs(last + timedelta(minutes=3))["opened"] == 1
    assert alert().state == "open"
    assert alert().details["rule_kind"] == "condition"
    with db_session_factory() as session:  # the overload is over
        Rows(session).add(perf(20, 30), last + timedelta(minutes=4))
    runs(last + timedelta(minutes=9))  # 7 minutes after the last hit: within clear_seconds
    assert alert().state == "open"
    assert runs(last + timedelta(minutes=15))["resolved"] == 1
    resolved = alert()
    assert resolved.state == "resolved"
    assert resolved.details["auto_resolved"]["reason"] == "condition_cleared"


# =================================================================================================
# R18 — DHCP havuzu doldu
# =================================================================================================


def test_r18_formats(db_session: Session) -> None:
    """V5: the FortiGate announces an exhausted pool with an ``fg_event`` whose ``logdesc``
    contains ``ran out of``; MikroTik's ``dhcp_pool_empty`` is a parser family."""
    rows = Rows(db_session)
    mikrotik = rows.add(
        POOL_EMPTY, END - timedelta(seconds=20), source="core-r1", source_type="mikrotik"
    )
    assert [table for table, _, _ in mikrotik] == ["mt_event"]
    assert mikrotik[0][2]["event"] == "dhcp_pool_empty"
    fortigate = rows.add(FG_POOL, END - timedelta(seconds=10))
    assert [table for table, _, _ in fortigate] == ["fg_event"], "V5: pool line is not fg_event"
    assert "ran out of" in str(fortigate[0][2]["logdesc"]), "V5: logdesc is no longer parsed"
    hits = run(db_session, "R18")
    assert set(hits) == {"core-r1:dhcp60", "fortigate:port5"}, "V5: R18 does not match"
    assert hits["core-r1:dhcp60"].values["pool"] == "pool60"
    assert hits["core-r1:dhcp60"].values["server"] == "dhcp60"
    assert hit_refs(hits["core-r1:dhcp60"]) == refs(mikrotik)
    assert hit_refs(hits["fortigate:port5"]) == refs(fortigate)


def test_r18_other_events_and_old_rows(db_session: Session) -> None:
    rows = Rows(db_session)
    rows.add(POOL_EMPTY, START, source="core-r1", source_type="mikrotik")
    rows.add(field(FG_POOL, "logdesc", "DHCP server started"), END - timedelta(seconds=10))
    assert run(db_session, "R18") == {}
    assert set(run(db_session, "R18", {"fortigate_phrases": ["started"]})) == {"fortigate:port5"}


# =================================================================================================
# T-A7 — bound parameters
# =================================================================================================

HOSTILE = ["x'); DELETE FROM alerts; --", "%", "_", "a;b", '"', "\\"]


def test_list_parameters_are_bound(db_session: Session) -> None:
    """Quotes, semicolons and ``%`` in every list parameter change nothing but the match."""
    rows = Rows(db_session)
    rows.add(IPS, END - timedelta(seconds=30))
    rows.add(field(IPS, "severity", "medium"), END - timedelta(seconds=29))
    rows.add(VIRUS, END - timedelta(seconds=28))
    rows.add(FG_POOL, END - timedelta(seconds=27))
    rows.add(CONSERVE, END - timedelta(seconds=26))
    db_session.add(
        Alert(
            time=END,
            rule="R15",
            severity="high",
            state="open",
            title="t",
            details={},
            dedup_key="k",
        )
    )
    db_session.commit()
    for rule_id, entry in (
        ("R05", rule("R05")),
        ("R06", rule("R06")),
        ("R07", rule("R07")),
        ("R17", rule("R17")),
        ("R18", rule("R18")),
    ):
        for param in entry.params:
            if param.kind != "strings":
                continue
            hits = run(db_session, rule_id, {param.key: HOSTILE})
            assert all(isinstance(hit, Hit) for hit in hits.values())
    # "%" is a character, not a pattern: it does not match every logdesc.
    assert run(db_session, "R18", {"fortigate_phrases": ["%"]}) == {}
    assert run(db_session, "R17", {"conserve_phrases": ["%", "_"]}) == {}
    assert run(db_session, "R05", {"high_severities": ["%"], "medium_severities": ["%"]}) == {}
    assert db_session.scalar(select(func.count()).select_from(Alert)) == 1


# =================================================================================================
# The W6-D block of cases.py (T-R), run here until the engine tests run every case
# =================================================================================================

W6D_CASES = [case for case in CASES if case.rule in {entry.id for entry in RULES}]


def write_case_rows(session: Session, rows: Sequence[CaseRow]) -> None:
    """The case rows with the common columns (source, sender, raw reference) filled in."""
    line = 0
    for row in rows:
        values = dict(row.values)
        extra = values.pop("extra", {})
        source = str(
            values.pop("source", "fortigate" if row.table.startswith("fg_") else "core-r1")
        )
        for n in range(row.repeat):
            line += 1
            received = SLICE_END + row.received + timedelta(seconds=n)
            session.execute(
                insert(MODELS[row.table]).values(
                    time=received + row.device_offset,
                    time_received=received,
                    source=source,
                    sender=FIREWALL if row.table.startswith("fg_") else ROUTER,
                    extra=extra,
                    raw_day=site_day(received),
                    raw_line=line,
                    raw_offset=line * 1000,
                    **values,
                )
            )
    session.commit()


@pytest.mark.parametrize("case", W6D_CASES, ids=lambda case: f"{case.rule}-{case.name}")
def test_w6d_cases(db_session: Session, case: RuleCase) -> None:
    write_case_rows(db_session, case.rows)
    params = case.overrides.get("params", {})
    assert isinstance(params, Mapping)
    hits = run(
        db_session,
        case.rule,
        params,
        end=SLICE_END,
        start=SLICE_END - timedelta(seconds=case.slice_seconds or 600),
    )
    assert set(hits) == {expected.key for expected in case.expected}
    for expected in case.expected:
        hit = hits[expected.key]
        assert hit.count == expected.count
        for name, value in expected.values.items():
            assert hit.values[name] == value, name
        if expected.evidence is not None:
            assert len(hit.evidence) == expected.evidence


def test_every_threat_rule_has_positive_and_negative_cases() -> None:
    for entry in RULES:
        cases = [case for case in W6D_CASES if case.rule == entry.id]
        assert any(case.positive for case in cases), entry.id
        assert any(not case.positive for case in cases), entry.id
