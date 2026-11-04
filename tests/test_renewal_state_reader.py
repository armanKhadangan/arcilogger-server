"""``state/renewal.json`` as W5-A writes it is what W5-C reads (contract 2.1.2, W5-C-1).

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
    write_state,
)
from arcilogger_worker.alerts.system import read_stamp_renewals

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
