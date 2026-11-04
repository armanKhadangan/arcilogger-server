"""``state/renewal/renewal.json`` as W5-A writes it is what W5-C reads (contract 2.1.2, W5-C-1,
W5-E-4).

The writer is ``arcilogger_core.renewal.write_state`` (the ``Renew`` task's command line); the
readers are the one W5-C wrote for the alerts and ``/legal-status``
(``arcilogger_worker.alerts.system.read_stamp_renewals``) and the day page's
(``arcilogger_api.sealinfo.VaultView``). A change on either side that breaks the other fails here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from arcilogger_api.sealinfo import VaultView
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.renewal import (
    LastRun,
    RenewalFact,
    TokenFact,
    compute_statuses,
    state_path,
    write_state,
)
from arcilogger_worker.alerts.system import (
    RenewRuns,
    read_renew_runs,
    read_stamp_renewals,
    stamp_renewal_path,
)

NOW = datetime(2031, 6, 23, 0, 0, tzinfo=UTC)
WINDOW = timedelta(days=180)


def _hash(number: int) -> str:
    return f"{number:064x}"


def _day(day: date) -> str:
    return f"{day:%Y/%m/%d}/manifest-{day.isoformat()}.json.zd"


def test_w5c_reader_reads_every_status(tmp_path: Path) -> None:
    """Every status and kind, ``covered_by`` and ``renewed_at``, a report path under
    ``exports/``: each entry W5-A writes is read back unchanged."""
    days = [date(2026, 10, d) for d in (10, 11, 12, 13, 14)]
    tokens = [
        TokenFact(_day(days[0]), "day", _hash(1), NOW + timedelta(days=400)),  # not_due
        TokenFact(_day(days[1]), "day", _hash(2), NOW + timedelta(days=100)),  # due
        TokenFact(_day(days[2]), "day", _hash(3), NOW + timedelta(days=100)),  # covered
        TokenFact(_day(days[3]), "day", _hash(4), NOW - timedelta(days=30)),  # late
        TokenFact(_day(days[4]), "day", _hash(5), NOW - timedelta(days=1)),  # uncovered
        TokenFact("deletions/2026-10-20-imha.json.zd", "deletion", _hash(6), NOW),
        TokenFact("exports/reports/ARCILOGGER-5651-rapor-7.pdf.zd", "report", _hash(7), NOW),
    ]
    own = TokenFact("renewals/2031-06-01-1.json.zd", "renewal", _hash(8), NOW + 5 * WINDOW)
    tokens.append(own)
    renewal = RenewalFact(
        "renewals/2031-06-01-1.json",
        own,
        datetime(2031, 6, 1, 0, 10, 5, tzinfo=UTC),
        eligible=True,
        lists=frozenset({_hash(3), _hash(4), _hash(7)}),
    )
    report = compute_statuses(tokens, [renewal], now=NOW, renew_before=WINDOW)
    expected = {
        _day(days[0]): "not_due",
        _day(days[1]): "due",
        _day(days[2]): "covered",
        _day(days[3]): "late",
        _day(days[4]): "uncovered",
        "deletions/2026-10-20-imha.json.zd": "uncovered",
        "exports/reports/ARCILOGGER-5651-rapor-7.pdf.zd": "covered",
        "renewals/2031-06-01-1.json.zd": "not_due",
    }
    assert {token.path: token.status for token in report.tokens} == expected

    layout = Layout(tmp_path / "data")
    write_state(
        layout,
        report,
        at=NOW,
        renew_before=180,
        last_run=LastRun(NOW, "renewed", 1, record="renewals/2031-06-23-1.json"),
    )
    read = read_stamp_renewals(layout)
    assert read is not None
    assert len(read) == len(report.tokens)
    for written, back in zip(report.tokens, read, strict=True):
        assert (back.path, back.kind, back.status) == (written.path, written.kind, written.status)
        assert back.cert_not_after == written.cert_not_after
        assert back.covered_by == written.covered_by
        assert back.renewed_at == written.renewed_at
    by_path = {entry.path: entry for entry in read}
    for path in (_day(days[2]), _day(days[3]), "exports/reports/ARCILOGGER-5651-rapor-7.pdf.zd"):
        assert by_path[path].covered_by == "renewals/2031-06-01-1.json"
        assert by_path[path].renewed_at == datetime(2031, 6, 1, 0, 10, 5, tzinfo=UTC)

    # the day page looks a day up by its token path and takes renewed_at from the file
    config = Config.model_validate({"data_dir": str(layout.root), "secrets_dir": str(tmp_path)})
    view = VaultView(layout, config, now=NOW)
    assert view.renewal(days[3]) == {
        "status": "late",
        "cert_not_after": NOW - timedelta(days=30),
        "covered_by": "renewals/2031-06-01-1.json",
        "renewed_at": datetime(2031, 6, 1, 0, 10, 5, tzinfo=UTC),
    }
    assert view.renewal(days[1]) == {
        "status": "due",
        "cert_not_after": NOW + timedelta(days=100),
        "covered_by": None,
        "renewed_at": None,
    }
    assert view.problems == []


def test_a_state_without_statuses_reads_as_unknown(tmp_path: Path) -> None:
    """A refused run with no pinned root and no earlier file writes no token list: the reader
    says "unknown" (None), never "nothing is due" (an empty tuple)."""
    layout = Layout(tmp_path / "data")
    write_state(
        layout, None, at=NOW, renew_before=180, last_run=LastRun(NOW, "refused", 2, error="roots")
    )
    assert read_stamp_renewals(layout) is None


def test_the_writer_and_the_readers_meet_in_the_protected_folder(tmp_path: Path) -> None:
    """The report is written to ``state/renewal/renewal.json`` only, and every reader (the
    alerts, the Sistem page's ``read_renew_runs``, ``/legal-status`` and the day page) reads it
    there (W5-E-4). An earlier release's ``state/renewal.json`` is carried over by the first
    write and never written; after that it decides nothing."""
    layout = Layout(tmp_path / "data")
    earlier = layout.state / "renewal.json"
    earlier.parent.mkdir(parents=True)
    earlier.write_text('{"tokens": [], "failures_in_a_row": 1}\n', encoding="utf-8")
    due = TokenFact(_day(date(2026, 10, 12)), "day", _hash(1), NOW + timedelta(days=100))
    report = compute_statuses([due], [], now=NOW, renew_before=WINDOW)
    failed = LastRun(NOW, "failed", 3, error="tsa_unavailable")
    write_state(layout, report, at=NOW, renew_before=180, last_run=failed)
    path = layout.state / "renewal" / "renewal.json"
    assert stamp_renewal_path(layout) == state_path(layout) == path
    assert path.is_file()
    assert earlier.read_text(encoding="utf-8") == '{"tokens": [], "failures_in_a_row": 1}\n'
    expected_runs = RenewRuns(last_run_at=NOW, outcome="failed", failures_in_a_row=2)
    assert read_renew_runs(layout) == expected_runs
    # a service writing the earlier file afterwards changes nothing any reader sees
    earlier.write_text('{"tokens": [], "failures_in_a_row": 0}\n', encoding="utf-8")
    assert [(entry.path, entry.status) for entry in read_stamp_renewals(layout) or ()] == [
        (due.path, "due")
    ]
    assert read_renew_runs(layout) == expected_runs
    config = Config.model_validate({"data_dir": str(layout.root), "secrets_dir": str(tmp_path)})
    view = VaultView(layout, config, now=NOW)
    assert view.renewal(date(2026, 10, 12)) == {
        "status": "due",
        "cert_not_after": NOW + timedelta(days=100),
        "covered_by": None,
        "renewed_at": None,
    }
    assert view.problems == []
