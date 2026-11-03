"""The renewal record format and the long-term status of a token (Wave 5 §2.1.1-§2.1.2, D2, D5).

Pure facts on a fixed clock: no time-stamp authority and no disk. The day's TSA certificate
expires at ``CNA``; each test moves ``now`` (and the renewals' genTimes and certificates)
relative to it, so the window and expiry boundaries are exact to the second.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from arcilogger_core import PRODUCT_NAME, __version__
from arcilogger_core.canonical import canonical_json
from arcilogger_core.renewal import (
    RENEWAL_SCHEMA,
    STATUSES,
    RecordEntry,
    RenewalFact,
    RenewalRecordError,
    Status,
    TokenFact,
    build_record,
    compute_statuses,
    protects,
    renew_before_days,
    system_block,
    tsa_block,
)
from arcilogger_core.tsa.mock import MockTsaProvider

T0 = datetime(2026, 10, 12, 21, 10, 5, tzinfo=UTC)
#: The day token's TSA certificate expires five years after the stamp.
CNA = T0 + timedelta(days=1825)
WINDOW = timedelta(days=180)


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


DAY_DATE = date(2026, 10, 12)
DAY = TokenFact("2026/10/12/manifest-2026-10-12.json.zd", "day", sha("day token"), CNA)


def renewal(
    name: str,
    *,
    gen_time: datetime,
    cert_not_after: datetime,
    lists: tuple[TokenFact, ...] = (DAY,),
    eligible: bool = True,
) -> RenewalFact:
    token = TokenFact(f"renewals/{name}.json.zd", "renewal", sha(name), cert_not_after)
    return RenewalFact(
        f"renewals/{name}.json",
        token,
        gen_time,
        eligible,
        frozenset(listed.token_sha256 for listed in lists),
    )


def statuses(
    tokens: tuple[TokenFact, ...], renewals: tuple[RenewalFact, ...], now: datetime
) -> dict[str, tuple[Status, str | None, datetime | None]]:
    report = compute_statuses(tokens, renewals, now=now, renew_before=WINDOW)
    return {
        token.path: (token.status, token.covered_by, token.renewed_at) for token in report.tokens
    }


# -- each status ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClockCase:
    status: Status
    now: datetime
    renewals: tuple[RenewalFact, ...] = ()


COVERING = renewal(
    "2030-07-04-1", gen_time=CNA - timedelta(days=100), cert_not_after=CNA + 5 * WINDOW
)
#: Stamped after the day's certificate had expired, by a certificate valid then.
LATE = renewal(
    "2031-10-22-1",
    gen_time=CNA + timedelta(days=10),
    cert_not_after=CNA + timedelta(days=1835),
)
CLOCK_CASES = (
    ClockCase("not_due", CNA - timedelta(days=181)),
    ClockCase("due", CNA - timedelta(days=179)),
    ClockCase("covered", CNA - timedelta(days=90), (COVERING,)),
    ClockCase("late", CNA + timedelta(days=20), (LATE,)),
    ClockCase("uncovered", CNA + timedelta(days=1)),
)


@pytest.mark.parametrize("case", CLOCK_CASES, ids=[case.status for case in CLOCK_CASES])
def test_each_status_from_a_clock_fixture(case: ClockCase) -> None:
    report = compute_statuses((DAY,), case.renewals, now=case.now, renew_before=WINDOW)
    (token,) = report.tokens
    assert (token.path, token.kind, token.cert_not_after) == (DAY.path, "day", CNA)
    assert token.status == case.status
    if case.renewals:
        assert token.covered_by == case.renewals[0].record_path
        assert token.renewed_at == case.renewals[0].gen_time
    else:
        assert (token.covered_by, token.renewed_at) == (None, None)
    assert report.totals == {status: int(status == case.status) for status in STATUSES}


def test_a_token_is_not_due_while_far_from_expiry_even_when_renewed() -> None:
    # §2.1.2's first rule decides before any renewal is looked at.
    early = renewal(
        "2030-01-15-1", gen_time=CNA - timedelta(days=300), cert_not_after=CNA + 5 * WINDOW
    )
    now = CNA - timedelta(days=200)
    assert statuses((DAY,), (early,), now)[DAY.path] == ("not_due", None, None)


def test_the_window_boundary_is_exclusive() -> None:
    assert statuses((DAY,), (), CNA - WINDOW)[DAY.path][0] == "due"
    assert statuses((DAY,), (), CNA - WINDOW - timedelta(seconds=1))[DAY.path][0] == "not_due"
    # The expiry boundary: the certificate's last second is still due, its notAfter uncovered.
    assert statuses((DAY,), (), CNA - timedelta(seconds=1))[DAY.path][0] == "due"
    assert statuses((DAY,), (), CNA)[DAY.path][0] == "uncovered"
    # A renewal stamped at the very notAfter is late, one a second earlier covers.
    for gen_time, status in ((CNA, "late"), (CNA - timedelta(seconds=1), "covered")):
        stamped = renewal("2031-10-12-1", gen_time=gen_time, cert_not_after=CNA + 5 * WINDOW)
        assert statuses((DAY,), (stamped,), CNA + timedelta(days=1))[DAY.path][0] == status
    # The window is the setting, not a constant.
    report = compute_statuses((DAY,), (), now=CNA - WINDOW, renew_before=timedelta(days=30))
    assert report.tokens[0].status == "not_due"


def test_a_renewal_whose_own_certificate_is_due_does_not_count() -> None:
    now = CNA - timedelta(days=90)
    tired = renewal("2030-07-04-1", gen_time=CNA - timedelta(days=100), cert_not_after=CNA)
    found = statuses((DAY, tired.token), (tired,), now)
    assert found[DAY.path] == ("due", None, None)
    assert found[tired.token.path] == ("due", None, None)
    # The same renewal with a fresh certificate counts.
    assert statuses((DAY,), (COVERING,), now)[DAY.path][0] == "covered"


def test_a_three_deep_chain_covers_the_day_through_each_link() -> None:
    first_expiry = CNA + timedelta(days=1000)
    second_expiry = first_expiry + timedelta(days=1000)
    r1 = renewal("2030-07-04-1", gen_time=CNA - timedelta(days=100), cert_not_after=first_expiry)
    r2 = renewal(
        "2033-03-31-1",
        gen_time=first_expiry - timedelta(days=100),
        cert_not_after=second_expiry,
        lists=(r1.token,),
    )
    r3 = renewal(
        "2035-12-26-1",
        gen_time=second_expiry - timedelta(days=100),
        cert_not_after=second_expiry + timedelta(days=1825),
        lists=(r2.token,),
    )
    tokens = (DAY, r1.token, r2.token, r3.token)
    # The day's and the first renewal's certificates have expired; the second is due.
    now = second_expiry - timedelta(days=90)
    found = statuses(tokens, (r1, r2, r3), now)
    assert found == {
        DAY.path: ("covered", r1.record_path, r1.gen_time),
        r1.token.path: ("covered", r2.record_path, r2.gen_time),
        r2.token.path: ("covered", r3.record_path, r3.gen_time),
        r3.token.path: ("not_due", None, None),
    }
    # Without the last link the second renewal is due, so it no longer counts for the first,
    # and the first no longer counts for the day.
    found = statuses(tokens[:3], (r1, r2), now)
    assert found[r2.token.path] == ("due", None, None)
    assert found[r1.token.path] == ("uncovered", None, None)
    assert found[DAY.path] == ("uncovered", None, None)
    # Without the middle link the third renewal covers nothing the day depends on.
    assert statuses((DAY,), (r1, r3), now)[DAY.path] == ("uncovered", None, None)
    # A link stamped after the certificate it renews had expired leaves that renewal late,
    # and a late renewal does not count for the tokens it lists (§2.1.2: own status not_due
    # or covered).
    tardy = renewal(
        "2033-07-19-1",
        gen_time=first_expiry + timedelta(days=10),
        cert_not_after=first_expiry + timedelta(days=1835),
        lists=(r1.token,),
    )
    found = statuses((DAY, r1.token), (r1, tardy), first_expiry + timedelta(days=20))
    assert found[r1.token.path] == ("late", tardy.record_path, tardy.gen_time)
    assert found[DAY.path] == ("uncovered", None, None)


@pytest.mark.parametrize("now", [CNA - timedelta(days=90), CNA + timedelta(days=20)])
def test_an_ineligible_renewal_never_counts(now: datetime) -> None:
    # A test provider's renewal (reason "test"), one of an unlisted root or a revoked one is
    # written but never counts (D5), however good its certificate and time.
    ignored = renewal(
        "2030-07-04-1",
        gen_time=CNA - timedelta(days=100),
        cert_not_after=CNA + 5 * WINDOW,
        eligible=False,
    )
    expected: Status = "due" if now < CNA else "uncovered"
    assert statuses((DAY,), (ignored,), now)[DAY.path] == (expected, None, None)
    if now > LATE.gen_time:
        # It is passed over, not taken as the best: a later counting renewal decides.
        found = statuses((DAY,), (ignored, LATE), now)
        assert found[DAY.path] == ("late", LATE.record_path, LATE.gen_time)


def test_a_renewal_counts_only_for_the_hash_it_lists() -> None:
    changed = TokenFact(DAY.path, "day", sha("a changed token"), CNA)
    assert statuses((changed,), (COVERING,), CNA - timedelta(days=90))[DAY.path][0] == "due"


def test_the_earliest_counting_renewal_decides() -> None:
    now = CNA - timedelta(days=50)
    early = renewal(
        "2030-07-04-2", gen_time=CNA - timedelta(days=100), cert_not_after=CNA + WINDOW * 5
    )
    same = renewal(
        "2030-07-04-1", gen_time=early.gen_time, cert_not_after=early.token.cert_not_after
    )
    later = renewal(
        "2030-08-23-1", gen_time=CNA - timedelta(days=60), cert_not_after=CNA + WINDOW * 5
    )
    found = statuses((DAY,), (later, early, same), now)
    assert found[DAY.path] == ("covered", same.record_path, same.gen_time)


def test_a_cycle_of_renewals_does_not_count() -> None:
    # Real hashes cannot form one; the guard keeps the recursion finite all the same.
    now = CNA - timedelta(days=50)
    first = TokenFact("renewals/2030-01-01-1.json.zd", "renewal", sha("first"), CNA)
    second = TokenFact("renewals/2030-01-01-2.json.zd", "renewal", sha("second"), CNA)
    renewals = (
        RenewalFact(
            "renewals/2030-01-01-1.json", first, T0, True, frozenset({second.token_sha256})
        ),
        RenewalFact(
            "renewals/2030-01-01-2.json", second, T0, True, frozenset({first.token_sha256})
        ),
    )
    found = statuses((first, second), renewals, now)
    assert {status for status, _, _ in found.values()} == {"due"}


# -- the record -----------------------------------------------------------------------------------


def day_entry(label: str = "day") -> RecordEntry:
    return RecordEntry(
        kind="day",
        path=DAY.path,
        token_sha256=sha(f"{label} token"),
        cert_not_after=CNA,
        day=DAY_DATE,
        manifest_sha256=sha(f"{label} manifest"),
    )


REPORT = RecordEntry(
    kind="report",
    path="exports/reports/ARCILOGGER-5651-rapor-7.pdf.zd",
    token_sha256=sha("report token"),
    cert_not_after=CNA,
    record_sha256=sha("report pdf"),
)
DELETION = RecordEntry(
    kind="deletion",
    path="deletions/2028-10-13-imha.json.zd",
    token_sha256=sha("deletion token"),
    cert_not_after=CNA,
    record_sha256=sha("deletion record"),
)
SYSTEM = {"name": PRODUCT_NAME, "version": __version__, "site": "Örnek A.Ş."}
TSA = {"provider": "zamane", "legal_grade": True, "digest": "sha256"}
CREATED = datetime(2031, 5, 4, 0, 10, tzinfo=UTC)


def record(*entries: RecordEntry, created: datetime = CREATED) -> dict[str, Any]:
    return build_record(entries, created_at=created, system=SYSTEM, tsa=TSA, reason="due")


def renewal_entry(name: str, older: dict[str, Any]) -> RecordEntry:
    return RecordEntry(
        kind="renewal",
        path=f"renewals/{name}.json.zd",
        token_sha256=sha(f"{name} token"),
        cert_not_after=CNA + timedelta(days=1000),
        record_sha256=hashlib.sha256(canonical_json(older)).hexdigest(),
        protects=tuple(protects(older)),
    )


def test_protects_repeats_the_original_hashes() -> None:
    r1 = record(day_entry(), REPORT)
    originals = {sha("day token"), sha("day manifest"), sha("report token"), sha("report pdf")}
    assert protects(r1) == sorted(originals)
    r2 = record(renewal_entry("2030-07-04-1", r1))
    first = r2["tokens"][0]
    assert protects(r2) == sorted(originals | {first["token_sha256"], first["record_sha256"]})
    r3 = record(renewal_entry("2033-03-31-1", r2))
    second = r3["tokens"][0]
    assert protects(r3) == sorted(
        originals
        | {first["token_sha256"], first["record_sha256"]}
        | {second["token_sha256"], second["record_sha256"]}
    )
    # The builder ships a renewal whose raw text holds a packaged day's manifest or token hash
    # (or the packaged report's token hash): the third link still does.
    text = canonical_json(r3).decode()
    assert all(
        value in text for value in (sha("day token"), sha("day manifest"), sha("report token"))
    )
    # A record that is not one protects nothing (it was read from disk).
    assert protects({}) == []
    assert protects({"tokens": [{"token_sha256": sha("x").upper()}, "not an entry"]}) == []


def test_record_shape_is_the_contract_shape(world: Any) -> None:
    r1 = record(day_entry("r1"))
    entries = (renewal_entry("2029-01-03-1", r1), REPORT, day_entry(), DELETION)
    built = build_record(entries, created_at=CREATED, system=SYSTEM, tsa=TSA, reason="test")
    assert set(built) == {"schema", "created_at", "system", "tsa", "reason", "tokens"}
    assert built["schema"] == RENEWAL_SCHEMA == "arcilogger.renewal/1"
    # Site time with milliseconds, like a manifest's.
    assert built["created_at"] == "2031-05-04T03:10:00.000+03:00"
    assert built["system"] == SYSTEM
    assert built["tsa"] == TSA
    assert built["reason"] == "test"
    paths = [entry["path"] for entry in built["tokens"]]
    assert (
        paths
        == sorted(paths)
        == [
            "2026/10/12/manifest-2026-10-12.json.zd",
            "deletions/2028-10-13-imha.json.zd",
            "exports/reports/ARCILOGGER-5651-rapor-7.pdf.zd",
            "renewals/2029-01-03-1.json.zd",
        ]
    )
    keys = {entry["kind"]: set(entry) for entry in built["tokens"]}
    common = {"kind", "path", "token_sha256", "cert_not_after"}
    assert keys == {
        "day": common | {"day", "manifest_sha256"},
        "deletion": common | {"record_sha256"},
        "report": common | {"record_sha256"},
        "renewal": common | {"record_sha256", "protects"},
    }
    for entry in built["tokens"]:
        # UTC, whole seconds, "Z".
        expected = "2034-07-07T21:10:05Z" if entry["kind"] == "renewal" else "2031-10-11T21:10:05Z"
        assert entry["cert_not_after"] == expected
        hashes = [
            entry[key]
            for key in ("token_sha256", "manifest_sha256", "record_sha256")
            if key in entry
        ]
        for value in [*hashes, *entry.get("protects", ())]:
            assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert built["tokens"][0]["day"] == "2026-10-12"
    assert built["tokens"][3]["protects"] == protects(r1)
    # Canonical JSON, read back unchanged.
    assert json.loads(canonical_json(built)) == built

    # The blocks come from the site's configuration and provider, as the manifest's do.
    world.reconfigure(site_name="Örnek A.Ş.")
    provider = MockTsaProvider(world.tsa)
    assert system_block(world.config) == SYSTEM
    assert tsa_block(provider, world.config) == {
        "provider": "mock",
        "legal_grade": False,
        "digest": "sha256",
    }
    world.reconfigure(tsa={"provider": "mock", "require_critical_eku": False})
    assert tsa_block(provider, world.config)["require_critical_eku"] is False


def broken(kind: str, path: str, **fields: Any) -> tuple[RecordEntry, ...]:
    return (RecordEntry(kind, path, sha("t"), CNA, **fields),)  # type: ignore[arg-type]


REFUSED = {
    "no token": (),
    "a path twice": (day_entry(), day_entry("again")),
    "an unknown kind": broken("seal", DAY.path, record_sha256=sha("r")),
    "upper-case hex": (RecordEntry("day", DAY.path, sha("t").upper(), CNA, DAY_DATE, sha("m")),),
    "a day without its manifest": broken("day", DAY.path, day=DAY_DATE),
    "a day without its date": broken("day", DAY.path, manifest_sha256=sha("m")),
    "a deletion with a manifest": broken("deletion", DELETION.path, manifest_sha256=sha("m")),
    "a report outside exports": broken(
        "report", "reports/ARCILOGGER-5651-rapor-7.pdf.zd", record_sha256=sha("p")
    ),
    "a vault token under exports": broken(
        "deletion", "exports/deletions/x-imha.json.zd", record_sha256=sha("r")
    ),
    "a path out of the vault": broken(
        "deletion", "../deletions/x-imha.json.zd", record_sha256=sha("r")
    ),
    "a record, not its token": broken("deletion", "deletions/x-imha.json", record_sha256=sha("r")),
    "a report that protects": broken(
        "report", REPORT.path, record_sha256=sha("p"), protects=(sha("x"),)
    ),
    "a protected value that is no hash": broken(
        "renewal", "renewals/x.json.zd", record_sha256=sha("r"), protects=("x",)
    ),
}


@pytest.mark.parametrize("entries", REFUSED.values(), ids=REFUSED.keys())
def test_a_record_that_breaks_the_format_is_refused(entries: tuple[RecordEntry, ...]) -> None:
    with pytest.raises(RenewalRecordError):
        build_record(entries, created_at=CREATED, system=SYSTEM, tsa=TSA, reason="due")


@pytest.mark.parametrize(
    ("system", "tsa", "reason"),
    [
        (SYSTEM, TSA, "later"),
        ({**SYSTEM, "host": "example"}, TSA, "due"),
        (SYSTEM, {"provider": "mock"}, "due"),
    ],
    ids=["an unknown reason", "a system with the host", "a tsa block without its claim"],
)
def test_the_blocks_and_the_reason_are_checked(
    system: dict[str, str], tsa: dict[str, Any], reason: Any
) -> None:
    with pytest.raises(RenewalRecordError):
        build_record((day_entry(),), created_at=CREATED, system=system, tsa=tsa, reason=reason)


def test_renew_before_days_reads_the_setting_or_the_default(world: Any) -> None:
    config = world.config
    assert renew_before_days(config) == 180
    # W5-T adds the key (§2.5); until then a test sets it without validation.
    longer = config.model_copy(
        update={"tsa": config.tsa.model_copy(update={"renew_before_days": 365})}
    )
    assert renew_before_days(longer) == 365
