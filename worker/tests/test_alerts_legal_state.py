"""The legal-validity state readers of ``alerts.system`` (Wave 5 contract 2.3): the re-stamping
report ``state/renewal.json`` (2.1.2), the revocation list index ``state/crl/index.json`` (2.2)
and the revoked certificates of the scheduled verifications. ``GET /legal-status`` and the R20
checks read the files through them only.

The fixtures are written as the writers write them: the index exactly as the CrlFetch task of
W5-B does (key order, ``+00:00`` list times, ``+03:00`` fetch times, a ``null`` nextUpdate), the
report with the per-token fields of 2.1.2 only."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_worker.alerts.system import (
    CRL_INDEX_SCHEMA,
    CrlFailureEntry,
    CrlIndexState,
    CrlListEntry,
    RevokedCertificate,
    StampRenewal,
    crl_index_path,
    crl_stale_issuers,
    issuers_in_use,
    read_crl_index,
    read_stamp_renewals,
    revoked_certificates,
    scheduled_verifications,
    stamp_renewal_path,
)
from arcilogger_worker.vaulttesting import at_site

NOW = at_site(date(2026, 11, 2), 9, 0)
ROOT_SHA = "a1" * 32
SUB_SHA = "b2" * 32
REVOKED = (
    "A time-stamp certificate is on a revocation list: CN=Örnek Test TSA 1,O=Örnek A.Ş.,C=TR. "
    "Contact the provider; legal review is recommended."
)


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report(*tokens: Any, **totals: int) -> dict[str, Any]:
    counts = {"not_due": 0, "due": 0, "covered": 0, "late": 0, "uncovered": 0, **totals}
    return {"tokens": list(tokens), "totals": counts}


def token(day: str, status: str, not_after: str, covered_by: str | None = None) -> dict[str, Any]:
    year, month, _ = day.split("-")
    return {
        "path": f"{year}/{month}/{day[-2:]}/manifest-{day}.json.zd",
        "kind": "day",
        "cert_not_after": not_after,
        "status": status,
        "covered_by": covered_by,
    }


def crl_list(issuer_sha: str, this_update: datetime, **extra: Any) -> dict[str, Any]:
    entry = {
        "file": f"{issuer_sha[:12]}-{this_update:%Y%m%dT%H%M%S}Z-89abcdef.crl",
        "issuer": "CN=Örnek Test TSA Kök,O=Örnek A.Ş.,C=TR",
        "issuer_sha256": issuer_sha,
        "this_update": this_update.astimezone(UTC).isoformat(),
        "next_update": (this_update + timedelta(days=7)).astimezone(UTC).isoformat(),
        "sha256": "c3" * 32,
        "url": "http://192.0.2.10/crl/root.crl",
        "fetched_at": isoformat_site(NOW - timedelta(hours=6), "seconds"),
    }
    return {**entry, **extra}


def index(*lists: Any, failures: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {"schema": CRL_INDEX_SCHEMA, "lists": list(lists), "failures": list(failures)}


# -- state/renewal.json ---------------------------------------------------------------------------


def test_stamp_renewals_are_read_from_the_report(layout: Layout) -> None:
    write(
        stamp_renewal_path(layout),
        report(
            token("2026-10-01", "due", "2027-03-01T00:00:00Z"),
            token("2026-09-01", "covered", "2026-12-01T00:00:00Z", "renewals/2026-10-20-1.json"),
            due=1,
            covered=1,
        ),
    )
    assert stamp_renewal_path(layout) == layout.state / "renewal.json"
    assert read_stamp_renewals(layout) == (
        StampRenewal(
            path="2026/10/01/manifest-2026-10-01.json.zd",
            kind="day",
            cert_not_after=datetime(2027, 3, 1, tzinfo=UTC),
            status="due",
            covered_by=None,
            renewed_at=None,
        ),
        StampRenewal(
            path="2026/09/01/manifest-2026-09-01.json.zd",
            kind="day",
            cert_not_after=datetime(2026, 12, 1, tzinfo=UTC),
            status="covered",
            covered_by="renewals/2026-10-20-1.json",
            renewed_at=None,
        ),
    )


def test_stamp_renewals_take_renewed_at_and_a_plain_list(layout: Layout) -> None:
    """The report's top level is not fixed by the contract: a plain token list reads too, and
    a ``renewed_at`` of an entry is taken when the writer gives one."""
    entry = token("2026-09-01", "late", "2026-10-01T00:00:00Z", "renewals/2026-10-20-1.json")
    write(stamp_renewal_path(layout), [{**entry, "renewed_at": "2026-10-20T03:15:00+03:00"}])
    (renewal,) = read_stamp_renewals(layout) or ()
    assert renewal.status == "late"
    assert renewal.renewed_at == datetime(2026, 10, 20, 0, 15, tzinfo=UTC)


def test_stamp_renewals_missing_file_is_empty_and_corrupt_is_unknown(layout: Layout) -> None:
    assert read_stamp_renewals(layout) == ()  # the Renew task has not run yet
    path = stamp_renewal_path(layout)
    path.parent.mkdir(parents=True)
    path.write_text('{"tokens": [', encoding="utf-8")
    assert read_stamp_renewals(layout) is None
    write(path, {"tokens": {"not": "a list"}})
    assert read_stamp_renewals(layout) is None
    write(path, "text")
    assert read_stamp_renewals(layout) is None


def test_stamp_renewals_skip_unknown_status_and_bad_times(layout: Layout) -> None:
    write(
        stamp_renewal_path(layout),
        report(
            token("2026-10-01", "overdue", "2027-03-01T00:00:00Z"),  # not a status of 2.1.2
            token("2026-10-02", "due", "next spring"),
            token("2026-10-03", "due", "2027-03-01T00:00:00"),  # no time zone
            {**token("2026-10-04", "due", "2027-03-01T00:00:00Z"), "path": None},
            "not an entry",
            {**token("2026-10-05", "uncovered", "2026-10-30T00:00:00Z"), "seq": 7},
        ),
    )
    (kept,) = read_stamp_renewals(layout) or ()
    assert kept.path == "2026/10/05/manifest-2026-10-05.json.zd"
    assert kept.status == "uncovered"


def test_stamp_renewals_count_from_the_tokens_not_the_totals(layout: Layout) -> None:
    write(
        stamp_renewal_path(layout),
        report(
            token("2026-10-01", "due", "2027-03-01T00:00:00Z"),
            token("2026-10-02", "uncovered", "2026-10-30T00:00:00Z"),
            due=9,
            uncovered=0,
        ),
    )
    statuses = [renewal.status for renewal in read_stamp_renewals(layout) or ()]
    assert statuses == ["due", "uncovered"]


# -- state/crl/index.json -------------------------------------------------------------------------


def test_crl_index_is_read_with_the_writers_shape(layout: Layout) -> None:
    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    older = crl_list(SUB_SHA, this_update - timedelta(days=3), next_update=None)
    older["fetched_at"] = isoformat_site(NOW - timedelta(days=3), "seconds")
    failure = {
        "url": "http://198.51.100.7/ca.crl",
        "at": isoformat_site(NOW - timedelta(hours=6), "seconds"),
        "error": "unreachable",
    }
    data = index(crl_list(ROOT_SHA, this_update, note="unknown key"), older, failures=(failure,))
    write(crl_index_path(layout), data)
    (layout.state / "crl" / ".fetch.lock").write_bytes(b"")
    assert crl_index_path(layout) == layout.state / "crl" / "index.json"
    assert '"this_update": "2026-11-01T00:00:00+00:00"' in crl_index_path(layout).read_text("utf-8")

    state = read_crl_index(layout)
    assert state.readable
    assert state.lists == (
        CrlListEntry(
            issuer="CN=Örnek Test TSA Kök,O=Örnek A.Ş.,C=TR",
            issuer_sha256=ROOT_SHA,
            this_update=this_update,
            next_update=this_update + timedelta(days=7),
            file=f"{ROOT_SHA[:12]}-20261101T000000Z-89abcdef.crl",
            fetched_at=NOW - timedelta(hours=6),
        ),
        CrlListEntry(
            issuer="CN=Örnek Test TSA Kök,O=Örnek A.Ş.,C=TR",
            issuer_sha256=SUB_SHA,
            this_update=this_update - timedelta(days=3),
            next_update=None,
            file=f"{SUB_SHA[:12]}-20261029T000000Z-89abcdef.crl",
            fetched_at=NOW - timedelta(days=3),
        ),
    )
    assert state.failures == (
        CrlFailureEntry(
            url="http://198.51.100.7/ca.crl", at=NOW - timedelta(hours=6), error="unreachable"
        ),
    )
    assert state.newest_fetched_at == NOW - timedelta(hours=6)
    assert issuers_in_use(state) == {ROOT_SHA, SUB_SHA}


def test_crl_index_missing_is_empty_and_bad_entries_are_left_out(layout: Layout) -> None:
    assert read_crl_index(layout) == CrlIndexState()
    good = crl_list(ROOT_SHA, datetime(2026, 11, 1, tzinfo=UTC))
    write(
        crl_index_path(layout),
        index(
            good,
            {**good, "this_update": "yesterday"},
            {**good, "next_update": "soon"},
            {**good, "fetched_at": "2026-11-02T03:10:00"},  # no time zone
            {**good, "issuer_sha256": None},
            ["not", "an", "entry"],
            failures=({"url": "http://198.51.100.7/ca.crl", "error": "unreachable"},),
        ),
    )
    state = read_crl_index(layout)
    assert state.readable
    assert len(state.lists) == 1
    assert state.failures == ()


@pytest.mark.parametrize(
    "content",
    [
        '{"schema": "arcilogger.crl-index/1", "lists": [',  # torn
        json.dumps({"schema": "arcilogger.crl-index/2", "lists": [], "failures": []}),
        json.dumps({"schema": CRL_INDEX_SCHEMA, "lists": {}, "failures": []}),
        json.dumps({"schema": CRL_INDEX_SCHEMA, "lists": []}),
        json.dumps([]),
    ],
)
def test_crl_index_wrong_schema_is_unreadable_not_an_error(layout: Layout, content: str) -> None:
    path = crl_index_path(layout)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    state = read_crl_index(layout)
    assert state == CrlIndexState(readable=False)
    assert issuers_in_use(state) == frozenset()


def test_crl_stale_issuers_by_this_update() -> None:
    def fresh(days: int, issuer: str = ROOT_SHA) -> CrlListEntry:
        moment = NOW - timedelta(days=days)
        return CrlListEntry("CN=Örnek", issuer, moment, None, "x.crl", NOW)

    def stale(*lists: CrlListEntry, in_use: tuple[str, ...] = (ROOT_SHA,)) -> list[str]:
        return crl_stale_issuers(CrlIndexState(lists=lists), in_use, NOW, 7)

    assert stale(fresh(6)) == []
    assert stale(fresh(7)) == [ROOT_SHA]  # "newer than" seven days
    assert stale(fresh(8)) == [ROOT_SHA]
    assert stale(fresh(8), fresh(2)) == []  # the newest list of the issuer counts
    assert stale(fresh(2, SUB_SHA)) == [ROOT_SHA]  # an issuer in use with no list at all
    assert stale(fresh(9), fresh(1, SUB_SHA), in_use=(SUB_SHA, ROOT_SHA, ROOT_SHA)) == [ROOT_SHA]
    assert stale(fresh(9), in_use=()) == []  # nothing in use, nothing stale


# -- revoked certificates -------------------------------------------------------------------------


def verification(layout: Layout, name: str, at: datetime, *checks: dict[str, Any]) -> None:
    write(
        layout.state / "verify" / f"{name}.json",
        {"at": isoformat_site(at, "seconds"), "deep": False, "checks": list(checks)},
    )


def day_check(day: date, *errors: str) -> dict[str, Any]:
    return {"day": day.isoformat(), "ok": not errors, "errors": list(errors), "warnings": []}


def test_revoked_certificates_from_the_newest_scheduled_verification(layout: Layout) -> None:
    d1, d2, d3, d4 = (date(2026, 10, day) for day in (28, 29, 30, 31))
    other = REVOKED.replace("Örnek Test TSA 1", "Örnek Test TSA 2")
    assert revoked_certificates(layout) == []
    # Monthly: d1 and d2 revoked. Nightly, later: d2 verifies again (its token is fine now),
    # d3 and d4 revoked by the same certificate, d4 also with another error.
    verification(layout, "last-full", NOW - timedelta(days=3), day_check(d1, REVOKED))
    verification(
        layout,
        "last-3",
        NOW - timedelta(hours=5),
        day_check(d2),
        day_check(d3, other),
        day_check(d4, "token: message imprint does not match", REVOKED),
    )
    verification(layout, "last-day", NOW - timedelta(days=5), day_check(d2, REVOKED))
    assert scheduled_verifications(layout)[d2].ok
    assert revoked_certificates(layout) == [
        RevokedCertificate("CN=Örnek Test TSA 1,O=Örnek A.Ş.,C=TR", None),
        RevokedCertificate("CN=Örnek Test TSA 2,O=Örnek A.Ş.,C=TR", None),
    ]

    # A deleted day (retention) no longer has a certificate in use.
    write(layout.seal_state(d1), {"status": "deleted"})
    write(layout.seal_state(d4), {"status": "deleted"})
    assert revoked_certificates(layout) == [
        RevokedCertificate("CN=Örnek Test TSA 2,O=Örnek A.Ş.,C=TR", None)
    ]


def test_revoked_certificates_ignore_other_texts_and_unreadable_files(layout: Layout) -> None:
    day = date(2026, 10, 30)
    verification(
        layout,
        "last-1",
        NOW,
        day_check(day, REVOKED.removesuffix("."), "revocation not checked: index unreadable"),
    )
    (layout.state / "verify" / "last-2.json").write_text("{", encoding="utf-8")
    assert revoked_certificates(layout) == []
