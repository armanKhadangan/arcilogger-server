"""Statistics of a search (contract 2.7, 3.2): buckets aligned to site midnight, the three modes,
facets (sampled, unavailable, VLAN and department by the /24; a row's VLAN is the facet's, also
with overlapping subnets), the budget and the notice for counts still being prepared. The
equality of every mode with the matcher on real ingest is ``test_search_oracle.py``'s."""

from __future__ import annotations

import ipaddress
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers.search import state_of
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.stats import SiteLabels, bucket_seconds, bucket_starts
from arcilogger_api.search.testing import Lines, sign_in
from arcilogger_api.search.windows import vlan_of
from arcilogger_api.testing import LoggedIn
from arcilogger_core.timeutil import SITE_TZ, day_window
from arcilogger_db.models import IngestOffset, Vlan
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.settings import set_setting

API = "/api/v1/search"
DAY = date(2026, 10, 20)
DAY_START, DAY_END = day_window(DAY)


def setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="test")
        session.commit()


@pytest.fixture
def searcher(client: TestClient, app_session_factory: sessionmaker[Session]) -> LoggedIn:
    setting(app_session_factory, "search.runs_per_minute", 600)
    return sign_in(client, app_session_factory)


def stats(client: TestClient, q: str, start: datetime, end: datetime) -> dict[str, Any]:
    page = client.post(API, json={"q": q, "from": start.isoformat(), "to": end.isoformat()})
    assert page.status_code == 200, page.text
    response = client.post(f"{API}/stats", json={"search_id": page.json()["search_id"]})
    assert response.status_code == 200, response.text
    return dict(response.json())


def counted(factory: sessionmaker[Session], lines: Lines) -> None:
    """Fill ``search_counts`` for the files written (what ingest's counts step does)."""
    with factory() as session:
        for source, day in {(w.source, w.day) for w in lines.written}:
            rebuild_stream(session.connection(), source, day)
        session.commit()


# -- buckets --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("span", "seconds"),
    [
        (timedelta(minutes=5), 60),
        (timedelta(hours=2), 60),
        (timedelta(hours=2, seconds=1), 300),
        (timedelta(hours=12), 300),
        (timedelta(days=3), 3600),
        (timedelta(days=14), 10800),
        (timedelta(days=31), 21600),
    ],
)
def test_bucket_sizes(span: timedelta, seconds: int) -> None:
    assert bucket_seconds(span) == seconds


def test_buckets_are_aligned_to_site_midnight() -> None:
    start = datetime(2026, 10, 26, 10, 7, tzinfo=SITE_TZ)
    starts = bucket_starts(start, start + timedelta(minutes=12), 300)
    assert [s.astimezone(SITE_TZ).strftime("%H:%M") for s in starts] == ["10:05", "10:10", "10:15"]
    six = bucket_starts(DAY_START + timedelta(hours=1), DAY_START + timedelta(days=2), 21600)
    assert [s.astimezone(SITE_TZ).hour for s in six[:5]] == [0, 6, 12, 18, 0]


# -- modes ----------------------------------------------------------------------------------------


@pytest.fixture
def day(app_session_factory: sessionmaker[Session]) -> Lines:
    """A day of web, traffic and DHCP rows of three VLANs, and some lines without a typed row."""
    lines = Lines(app_session_factory)
    with app_session_factory() as session:
        for hour in range(24):
            at = DAY_START + timedelta(hours=hour, minutes=7)
            lines.write(
                session,
                at,
                "fg_web",
                srcip="172.20.41.5",
                action="blocked" if hour % 3 else "passthrough",
            )
            lines.write(
                session,
                at + timedelta(minutes=1),
                "fg_traffic",
                srcip="172.20.42.9",
                action="accept",
            )
            lines.write(
                session,
                at + timedelta(minutes=2),
                "mt_dhcp",
                source="core-r1",
                ip="172.20.60.20",
                action="assigned",
            )
            lines.write(
                session, at + timedelta(minutes=3), None, source="ap-172.20.10.50", text="syslog"
            )
        session.commit()
    return lines


def test_the_summary_table_answers_a_whole_day(
    client: TestClient, searcher: LoggedIn, day: Lines, app_session_factory: sessionmaker[Session]
) -> None:
    counted(app_session_factory, day)
    rolled = stats(client, "", DAY_START, DAY_END)
    assert rolled["mode"] == "rollup"
    assert rolled["complete"] is True
    assert rolled["total"] == {"value": 96, "exact": True}
    assert rolled["histogram"]["bucket_seconds"] == 3600
    assert {b["count"] for b in rolled["histogram"]["buckets"]} == {4}
    kinds = {v["value"]: v["count"] for v in rolled["facets"]["kind"]["values"]}
    assert kinds == {"web": 24, "traffic": 24, "dhcp": 24, "syslog": 24}
    actions = {v["value"]: v["count"] for v in rolled["facets"]["action"]["values"]}
    assert actions == {"blocked": 16, "passthrough": 8, "accept": 24, "assigned": 24}
    vlans = {v["value"]: (v["count"], v["label_tr"]) for v in rolled["facets"]["vlan"]["values"]}
    assert vlans["41"] == (24, "VLAN 41 (Finans)")
    departments = {v["value"]: v["count"] for v in rolled["facets"]["department"]["values"]}
    assert departments["Finans"] == 24
    blocked = stats(client, "type:web action:blocked", DAY_START, DAY_END)
    assert blocked["mode"] == "rollup"
    assert blocked["total"]["value"] == 16


def test_partial_edge_hours_are_counted_directly(
    client: TestClient, searcher: LoggedIn, day: Lines, app_session_factory: sessionmaker[Session]
) -> None:
    counted(app_session_factory, day)
    start, end = DAY_START + timedelta(minutes=8), DAY_END - timedelta(minutes=50)
    hybrid = stats(client, "vlan:41", start, end)
    assert hybrid["mode"] == "hybrid"
    # the first web row (00:07) is before the range; the last (23:07) is inside it
    assert hybrid["total"]["value"] == 23
    assert stats(client, "", start, end)["facets"]["action"]["status"] == "ok"


def test_counts_still_being_prepared_are_counted_directly(
    client: TestClient, searcher: LoggedIn, day: Lines, app_session_factory: sessionmaker[Session]
) -> None:
    counted(app_session_factory, day)
    with app_session_factory() as session:
        session.execute(
            insert(IngestOffset).values(
                source="fortigate", day=DAY, byte_offset=10, line_number=48, counts_from_line=49
            )
        )
        session.commit()
    direct = stats(client, "type:web", DAY_START, DAY_END)
    assert direct["mode"] == "direct"
    assert "counts_incomplete" in [n["code"] for n in direct["notices"]]
    assert direct["total"]["value"] == 24
    # a messages-driven count knows only the source and the kind
    assert direct["facets"]["action"] == {"status": "unavailable", "values": [], "other": 0}
    assert direct["facets"]["kind"]["status"] == "ok"


def test_short_ranges_are_counted_directly(
    client: TestClient, searcher: LoggedIn, day: Lines
) -> None:
    direct = stats(client, "srcip:172.20.41.5", DAY_START, DAY_START + timedelta(hours=3))
    assert direct["mode"] == "direct"
    assert direct["histogram"]["bucket_seconds"] == 300
    assert direct["total"]["value"] == 3
    assert direct["facets"]["vlan"]["values"][0]["value"] == "41"
    assert direct["facets"]["department"]["values"][0]["value"] == "Finans"


def test_facets_of_a_large_count_are_sampled(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    lines = Lines(app_session_factory)
    start = DAY_START + timedelta(hours=1)
    lines.many(start, 300, timedelta(seconds=10), "fg_web", source="fw-old", srcip="172.20.41.5")
    lines.many(
        start + timedelta(hours=1),
        1000,
        timedelta(seconds=2),
        "fg_web",
        source="fw-new",
        srcip="172.20.41.5",
    )
    setting(app_session_factory, "search.facet_sample_rows", 1000)
    sampled = stats(client, "srcip:172.20.41.5", DAY_START, DAY_START + timedelta(hours=6))
    assert sampled["total"]["value"] == 1300  # the histogram counts everything
    assert sampled["sampled"] is True
    assert sampled["sample_rows"] == 1000
    source = sampled["facets"]["source"]
    assert source["status"] == "sampled"
    assert [(v["value"], v["count"]) for v in source["values"]] == [("fw-new", 1000)]


def test_the_top_ten_and_other(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    lines = Lines(app_session_factory)
    with app_session_factory() as session:
        for index in range(12):
            for _ in range(12 - index):
                lines.write(
                    session,
                    DAY_START + timedelta(minutes=index),
                    "fg_web",
                    srcip="172.20.41.5",
                    action=f"a{index:02d}",
                )
        session.commit()
    facet = stats(client, "srcip:172.20.41.5", DAY_START, DAY_START + timedelta(hours=1))["facets"][
        "action"
    ]
    assert [v["value"] for v in facet["values"]] == [f"a{index:02d}" for index in range(10)]
    assert facet["other"] == 2 + 1


class FakeClock:
    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_a_count_the_budget_cuts_says_how_far_it_got(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    day: Lines,
    app_session_factory: sessionmaker[Session],
) -> None:
    setting(app_session_factory, "search.stats_budget_ms", 1000)
    page = client.post(
        API, json={"q": "srcip:*", "from": DAY_START.isoformat(), "to": DAY_END.isoformat()}
    ).json()
    state_of(app).clock = FakeClock(0.15)
    partial = client.post(f"{API}/stats", json={"search_id": page["search_id"]}).json()
    assert partial["mode"] == "direct"
    assert partial["complete"] is False
    assert partial["total"]["exact"] is False
    covered = datetime.fromisoformat(partial["covered_from"])
    assert DAY_START < covered < DAY_END
    assert datetime.fromisoformat(partial["covered_to"]) == DAY_END
    assert partial["total"]["value"] == sum(b["count"] for b in partial["histogram"]["buckets"])


def test_a_vlan_facet_of_a_net_with_smaller_vlans(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        session.add_all(
            [
                Vlan(subnet="172.20.70.0/25", vlan_id=70, name_tr="Lab A"),
                Vlan(subnet="172.20.70.128/25", vlan_id=71, name_tr="Lab B"),
            ]
        )
        session.commit()
    Lines(app_session_factory).add(DAY_START + timedelta(minutes=1), "fg_web", srcip="172.20.70.5")
    facet = stats(client, "srcip:172.20.70.0/24", DAY_START, DAY_START + timedelta(hours=1))[
        "facets"
    ]["vlan"]
    assert [(v["value"], v["label_tr"], v["label_en"]) for v in facet["values"]] == [
        ("multiple", "Birden çok VLAN", "Several VLANs")
    ]
    with app_session_factory() as session:
        assert session.scalar(select(Vlan.name_tr).where(Vlan.vlan_id == 70)) == "Lab A"


def test_a_row_names_the_most_specific_vlan_as_the_facet_does() -> None:
    # The wider subnet first, as an unordered SELECT of the VLANs may give it.
    site = SiteNetwork(
        vlans={
            90: ipaddress.ip_network("172.20.0.0/16"),
            41: ipaddress.ip_network("172.20.41.0/24"),
            42: ipaddress.ip_network("172.20.42.0/24"),
        }
    )
    labels = SiteLabels(site)
    assert vlan_of(site, "172.20.41.5") == 41 == labels.vlan_of_net("172.20.41.0/24")
    assert vlan_of(site, "172.20.99.5") == 90 == labels.vlan_of_net("172.20.99.0/24")
    assert vlan_of(site, "203.0.113.5") is None
    assert vlan_of(site, None) is None


def test_a_row_and_the_vlan_facet_agree_on_overlapping_subnets(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        session.add(Vlan(subnet="172.20.0.0/16", vlan_id=90, name_tr="Kampüs"))
        session.commit()
    Lines(app_session_factory).add(DAY_START + timedelta(minutes=1), "fg_web", srcip="172.20.41.5")
    span = {"from": DAY_START.isoformat(), "to": (DAY_START + timedelta(hours=1)).isoformat()}
    page = client.post(API, json={"q": "srcip:172.20.41.5", **span}).json()
    (row,) = page["items"]
    facet = client.post(f"{API}/stats", json={"search_id": page["search_id"]}).json()["facets"]
    assert row["summary"]["vlan_id"] == 41
    assert [value["value"] for value in facet["vlan"]["values"]] == ["41"]
