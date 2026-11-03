"""The legal-validity state readers of ``alerts.system`` (Wave 5 contract 2.3): the re-stamping
report ``state/renewal.json`` (2.1.2), the revocation list index ``state/crl/index.json`` (2.2)
and the revoked certificates of the scheduled verifications. ``GET /legal-status`` and the R20
checks read the files through them only.

The fixtures are written as the writers write them: the index exactly as the CrlFetch task of
W5-B does (key order, ``+00:00`` list times, ``+03:00`` fetch times, a ``null`` nextUpdate), the
report with the per-token fields of 2.1.2 only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, load_pem_private_key

from arcilogger_core.config import Config
from arcilogger_core.crl import Crl, parse_crl
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_core.tsa.mock import MockTsa
from arcilogger_core.tsa.verify import fingerprint, inspect_token
from arcilogger_worker.alerts.system import (
    CRL_INDEX_SCHEMA,
    CrlFailureEntry,
    CrlIndexState,
    CrlListEntry,
    CrlStatus,
    RevokedCertificate,
    StampRenewal,
    crl_index_path,
    crl_stale_issuers,
    crl_status,
    issuers_in_use,
    pinned_roots,
    read_crl_index,
    read_stamp_renewals,
    revoked_certificates,
    scheduled_verifications,
    shifted,
    stamp_in_use,
    stamp_renewal_path,
    stored_lists,
)
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

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
            {**token("2026-10-06", "due", "2027-03-01T00:00:00Z"), "status": ["due"]},
            {**token("2026-10-07", "due", "2027-03-01T00:00:00Z"), "status": {"code": "due"}},
            {**token("2026-10-08", "due", "2027-03-01T00:00:00Z"), "path": ["2026/10/08"]},
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
            # a fetch time beyond what site time can show is no fetch's
            {**good, "fetched_at": "9999-12-31T21:00:00+00:00"},
            {**good, "fetched_at": "0001-01-01T00:00:00+03:00"},
            {**good, "issuer_sha256": None},
            {**good, "issuer_sha256": ["a1"]},
            ["not", "an", "entry"],
            failures=(
                {"url": "http://198.51.100.7/ca.crl", "error": "unreachable"},
                {"url": "http://198.51.100.7/ca.crl", "at": "9999-12-31T21:30:00Z"},
            ),
        ),
    )
    state = read_crl_index(layout)
    assert state.readable
    assert len(state.lists) == 1
    assert state.failures == ()
    # the list's own times are only compared: a next update at the calendar's end is kept
    write(crl_index_path(layout), index({**good, "next_update": "9999-12-31T23:59:59Z"}))
    (kept,) = read_crl_index(layout).lists
    assert kept.next_update == datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)


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
    assert read_crl_index(layout) == CrlIndexState(readable=False)
    assert stored_lists(layout) is None  # W5-B's reader refuses it too: nothing counts


def stored(issuer: str, days_ago: int) -> Crl:
    """A stored list of ``issuer`` (a fingerprint) issued ``days_ago`` days before ``NOW``."""
    this_update = NOW - timedelta(days=days_ago)
    return Crl(b"", "CN=Örnek", x509.Name([]), this_update, None, {}, {}, issuer_sha256=issuer)


def test_crl_stale_issuers_by_this_update() -> None:
    def stale(*lists: Crl, in_use: tuple[str, ...] = (ROOT_SHA,)) -> list[str]:
        return crl_stale_issuers(lists, in_use, NOW, 7)

    assert stale(stored(ROOT_SHA, 6)) == []
    assert stale(stored(ROOT_SHA, 7)) == [ROOT_SHA]  # "newer than" seven days
    assert stale(stored(ROOT_SHA, 8)) == [ROOT_SHA]
    assert stale(stored(ROOT_SHA, 8), stored(ROOT_SHA, 2)) == []  # the issuer's newest counts
    assert stale(stored(SUB_SHA, 2)) == [ROOT_SHA]  # an issuer in use with no list at all
    assert stale(stored(ROOT_SHA, 9), stored(SUB_SHA, 1), in_use=(SUB_SHA, ROOT_SHA)) == [ROOT_SHA]
    assert stale(stored(ROOT_SHA, 9), in_use=()) == []  # nothing in use, nothing stale
    assert stale(stored(ROOT_SHA.upper(), 1)) == []  # a fingerprint is matched in any case


# -- the stamp in use and its lists ---------------------------------------------------------------


@pytest.fixture
def vault(layout: Layout) -> Iterator[SealedVault]:
    sealed = make_vault(layout.root, tsa_now=at_site(date(2026, 9, 29)))
    yield sealed
    sealed.unlock_all()


def root_list(tsa: MockTsa, this_update: datetime, revoked_at: datetime | None = None) -> bytes:
    key = load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(tsa.root_certificate.subject)
        .last_update(this_update)
        .next_update(this_update + timedelta(days=7))
    )
    if revoked_at is not None:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(tsa.tsa_certificate.serial_number)
            .revocation_date(revoked_at)
            .build()
        )
    return builder.sign(key, hashes.SHA256()).public_bytes(Encoding.DER)


def store(layout: Layout, tsa: MockTsa, *lists: bytes) -> list[str]:
    """The lists stored and indexed as ``crl fetch`` does (2.2); their stored names."""
    layout.crl_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for der in lists:
        crl = replace(parse_crl(der), issuer_sha256=fingerprint(tsa.root_certificate))
        (layout.crl_dir / crl.file_name).write_bytes(der)
        entries.append(
            crl_list(crl.issuer_sha256, crl.this_update, file=crl.file_name, sha256=crl.sha256)
        )
    write(crl_index_path(layout), index(*entries))
    return [entry["file"] for entry in entries]


def test_pinned_roots_read_only_and_the_mock_root_only_for_the_mock(
    layout: Layout, vault: SealedVault, tmp_path: Path
) -> None:
    mock = Config(data_dir=layout.root, secrets_dir=layout.root)
    root = vault.tsa.root_certificate
    assert pinned_roots(mock, layout) == [root]
    zamane = mock.model_copy(update={"tsa": mock.tsa.model_copy(update={"provider": "zamane"})})
    assert pinned_roots(zamane, layout) == []
    folder = tmp_path / "roots"
    folder.mkdir()
    (folder / "root.pem").write_bytes(root.public_bytes(Encoding.PEM))
    pinned = zamane.model_copy(update={"tsa": zamane.tsa.model_copy(update={"roots_dir": folder})})
    assert pinned_roots(pinned, layout) == [root]
    (folder / "broken.pem").write_text("-----BEGIN CERTIFICATE-----\nx\n", encoding="utf-8")
    assert pinned_roots(pinned, layout) == []  # unreadable: no root, and no failure


def test_stamp_in_use_is_the_newest_sealed_day(layout: Layout, vault: SealedVault) -> None:
    roots = [vault.tsa.root_certificate]
    assert stamp_in_use(layout, roots) is None  # nothing sealed yet
    assert issuers_in_use(None) == frozenset()
    d1, d2, d3 = date(2026, 10, 29), date(2026, 10, 30), date(2026, 10, 31)
    vault.seal_days([d1, d2])
    vault.write_day(d3)
    assert vault.fail_seal(d3).status == "pending"  # a failed attempt: no token
    stamp = stamp_in_use(layout, roots)
    assert stamp is not None
    assert stamp.day == d2
    assert stamp.chain == (vault.tsa.tsa_certificate, vault.tsa.root_certificate)
    assert stamp.gen_time == inspect_token(layout.token(d2).read_bytes()).gen_time
    # the root signs the TSA certificate: its lists must stay fresh, the TSA's are none
    assert issuers_in_use(stamp) == {fingerprint(vault.tsa.root_certificate)}
    assert stamp_in_use(layout, []) is None  # no pinned root: the day check says why
    vault.tamper(layout.token(d2), b"\x00")
    assert stamp_in_use(layout, roots) is None


def test_crl_status_judges_the_lists_that_count(layout: Layout, vault: SealedVault) -> None:
    vault.seal_days([date(2026, 10, 30)])
    stamp = stamp_in_use(layout, [vault.tsa.root_certificate])
    root = fingerprint(vault.tsa.root_certificate)
    assert crl_status(stored_lists(layout), stamp, NOW, 7) == CrlStatus(
        in_use=frozenset({root}), stale=(root,)
    )  # no list at all
    older, newest = store(
        layout,
        vault.tsa,
        root_list(vault.tsa, NOW - timedelta(days=10)),
        root_list(vault.tsa, NOW - timedelta(days=1)),
    )
    assert crl_status(stored_lists(layout), stamp, NOW, 7).stale == ()
    path = layout.crl_dir / newest
    path.write_bytes(path.read_bytes() + b"\x00")  # test: the stored file is changed
    status = crl_status(stored_lists(layout), stamp, NOW, 7)
    assert (status.stale, status.since, status.mismatched) == (
        (root,),
        NOW - timedelta(days=3),
        (newest,),
    )
    assert crl_status(stored_lists(layout), None, NOW, 7) == CrlStatus(mismatched=(newest,))
    write(crl_index_path(layout), {"schema": CRL_INDEX_SCHEMA, "lists": 3, "failures": []})
    assert crl_status(stored_lists(layout), stamp, NOW, 7) == CrlStatus(
        in_use=frozenset({root}), stale=(root,), readable=False
    )
    assert (layout.crl_dir / older).is_file()


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


def test_revoked_certificates_from_the_stored_lists(layout: Layout, vault: SealedVault) -> None:
    """The newest sealed day's chain against the stored lists: ``crl-revoked`` only (a
    revocation after the stamp, ``crl-revoked-later``, is a warning of the day)."""
    vault.seal_days([date(2026, 10, 30)])
    stamp = stamp_in_use(layout, [vault.tsa.root_certificate])
    assert stamp is not None
    subject = vault.tsa.tsa_certificate.subject.rfc4514_string()
    before = at_site(date(2026, 10, 30), 12, 0)
    store(layout, vault.tsa, root_list(vault.tsa, NOW, revoked_at=before))
    assert revoked_certificates(layout, stamp, stored_lists(layout)) == [
        RevokedCertificate(subject, before)
    ]
    assert revoked_certificates(layout) == []  # the scheduled verifications alone know nothing
    store(layout, vault.tsa, root_list(vault.tsa, NOW, revoked_at=stamp.gen_time + timedelta(1)))
    assert revoked_certificates(layout, stamp, stored_lists(layout)) == []
    # found by both: once, with the date the lists give
    verification(
        layout,
        "last-1",
        NOW,
        day_check(
            date(2026, 10, 30), REVOKED.replace("CN=Örnek Test TSA 1,O=Örnek A.Ş.,C=TR", subject)
        ),
    )
    store(layout, vault.tsa, root_list(vault.tsa, NOW, revoked_at=before))
    assert revoked_certificates(layout, stamp, stored_lists(layout)) == [
        RevokedCertificate(subject, before)
    ]


@pytest.mark.parametrize(
    "content",
    [
        {"at": "2026-11-02T09:00:00+03:00", "checks": 5},
        {"at": "2026-11-02T09:00:00+03:00", "checks": True},
        "[" * 100_000 + "]" * 100_000,
    ],
    ids=["number", "boolean", "nested"],
)
def test_scheduled_verifications_skip_a_damaged_file(layout: Layout, content: Any) -> None:
    path = layout.state / "verify" / "last-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), "utf-8")
    verification(layout, "last-2", NOW, day_check(date(2026, 10, 30), REVOKED))
    assert list(scheduled_verifications(layout)) == [date(2026, 10, 30)]


def test_stamp_renewals_leave_out_a_time_at_the_edge_of_the_calendar(layout: Layout) -> None:
    write(
        stamp_renewal_path(layout),
        report(
            token("2026-10-01", "due", "0001-01-01T00:00:00+03:00"),
            token("2026-10-02", "due", "2031-08-30T21:30:00Z"),
            token("2026-10-03", "uncovered", "9999-12-31T21:00:00+00:00"),  # 10000 in site time
            token("2026-10-04", "uncovered", "9999-12-31T23:59:59-05:00"),
            token("2026-10-05", "due", "9999-12-31T20:59:59+00:00"),  # the last one shown
            {
                **token("2026-10-06", "covered", "2031-08-30T21:30:00Z"),
                "renewed_at": "9999-12-31T22:00:00Z",
            },
        ),
    )
    found = read_stamp_renewals(layout) or ()
    assert [renewal.path[:10] for renewal in found] == ["2026/10/02", "2026/10/05", "2026/10/06"]
    assert found[1].cert_not_after == datetime(9999, 12, 31, 20, 59, 59, tzinfo=UTC)
    assert found[2].renewed_at is None  # an edge renewal time is not known
    assert shifted(found[1].cert_not_after, timedelta(seconds=1)) is None
    assert shifted(datetime(1, 1, 1, tzinfo=UTC), -timedelta(days=30)) is None
    assert shifted(NOW, timedelta(days=7)) == NOW + timedelta(days=7)
