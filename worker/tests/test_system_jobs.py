"""``system.sample``: the daily disk samples of the Sistem page (Wave 4 contract 2.5.2, 3 W4-B)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from arcilogger_core.layout import Layout
from arcilogger_worker import system_jobs
from arcilogger_worker.service import JobContext
from arcilogger_worker.system_jobs import HISTORY_DAYS, job_system_sample, read_history

GIB = 1024**3


class Usage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.fixture
def disks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, int]:
    """Two drives, ``D:`` (data) and ``C:`` (system); the test sets the used bytes of each."""
    used = {"D:": 300 * GIB, "C:": 40 * GIB}
    paths = {"D:": tmp_path / "data", "C:": tmp_path / "system"}

    def fake_drives(data_dir: Path) -> list[tuple[str, Path]]:
        assert data_dir == tmp_path
        return list(paths.items())

    def fake_usage(path: Path) -> Usage:
        label = next(label for label, known in paths.items() if known == path)
        total = 1000 * GIB if label == "D:" else 100 * GIB
        return Usage(total, used[label], total - used[label])

    monkeypatch.setattr(system_jobs, "drives", fake_drives)
    monkeypatch.setattr(shutil, "disk_usage", fake_usage)
    return used


def context(layout: Layout, now: datetime) -> JobContext:
    engine = create_engine("sqlite://")  # the job never opens a session
    return JobContext(engine=engine, sessions=sessionmaker(engine), now=now, layout=layout)


def history(layout: Layout) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(layout.system_history.read_text(encoding="utf-8"))
    return data


def test_sample_writes_one_entry_per_site_day(tmp_path: Path, disks: dict[str, int]) -> None:
    layout = Layout(tmp_path)
    assert layout.system_history == tmp_path / "state" / "system-history.json"
    # 21:40 UTC is 00:40 of the next site day (Europe/Istanbul, UTC+3).
    first = datetime(2026, 10, 11, 21, 40, tzinfo=UTC)
    assert job_system_sample(context(layout, first)) == {"day": "2026-10-12", "drives": 2}
    assert history(layout) == {
        "schema": 1,
        "days": [
            {
                "day": "2026-10-12",
                "drives": [
                    {"path": "D:", "total": 1000 * GIB, "used": 300 * GIB},
                    {"path": "C:", "total": 100 * GIB, "used": 40 * GIB},
                ],
            }
        ],
    }
    # A second run of the same site day (a retry, a restart) replaces the day's entry.
    disks["D:"] = 310 * GIB
    job_system_sample(context(layout, first + timedelta(hours=20)))
    days = history(layout)["days"]
    assert [day["day"] for day in days] == ["2026-10-12"]
    assert days[0]["drives"][0]["used"] == 310 * GIB
    # The next site day gets an entry of its own, after the earlier one.
    job_system_sample(context(layout, first + timedelta(days=1)))
    assert [day["day"] for day in history(layout)["days"]] == ["2026-10-12", "2026-10-13"]
    assert read_history(layout.system_history) == history(layout)["days"]
    assert not list(layout.state.glob("*.tmp"))  # written through a temporary file, replaced


def test_history_keeps_120_days(tmp_path: Path, disks: dict[str, int]) -> None:
    layout = Layout(tmp_path)
    start = date(2026, 6, 1)
    layout.state.mkdir(parents=True)
    old = [
        {"day": (start + timedelta(days=i)).isoformat(), "drives": []}
        for i in range(HISTORY_DAYS + 5)
    ]
    layout.system_history.write_text(json.dumps({"schema": 1, "days": old[::-1]}), encoding="utf-8")
    now = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    job_system_sample(context(layout, now))
    days = [day["day"] for day in history(layout)["days"]]
    assert len(days) == HISTORY_DAYS == 120
    assert days == sorted(days)
    assert days[-1] == "2026-10-30"
    assert days[0] == (start + timedelta(days=HISTORY_DAYS + 5 - 119)).isoformat()


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        json.dumps({"schema": 2, "days": [{"day": "2026-10-01", "drives": []}]}).encode(),
        json.dumps({"schema": 1, "days": "none"}).encode(),
    ],
)
def test_an_unreadable_history_starts_again(
    tmp_path: Path, disks: dict[str, int], content: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    layout = Layout(tmp_path)
    layout.state.mkdir(parents=True)
    layout.system_history.write_bytes(content)
    job_system_sample(context(layout, datetime(2026, 10, 12, 9, 0, tzinfo=UTC)))
    assert [day["day"] for day in history(layout)["days"]] == ["2026-10-12"]
    assert content.startswith(b'{"schema": 1') or "system-history.json" in caplog.text


def test_entries_of_another_shape_are_left_out(tmp_path: Path) -> None:
    path = tmp_path / "system-history.json"
    good = {"day": "2026-10-02", "drives": [{"path": "D:", "total": 10, "used": 5}]}
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "days": [
                    good,
                    {"day": "2026-10-01", "drives": [{"path": "D:", "total": 1.5, "used": 1}]},
                    {"day": 20261003, "drives": []},
                    "2026-10-04",
                ],
            }
        ),
        encoding="utf-8",
    )
    assert read_history(path) == [good]
    assert read_history(tmp_path / "missing.json") == []


def test_a_drive_that_cannot_be_measured_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_usage(path: Path) -> Usage:
        if path.name == "gone":
            raise OSError("device not ready")
        return Usage(0, 0, 0)  # a drive that reports no size

    monkeypatch.setattr(
        system_jobs, "drives", lambda _: [("E:", tmp_path / "gone"), ("C:", tmp_path / "empty")]
    )
    monkeypatch.setattr(shutil, "disk_usage", fake_usage)
    layout = Layout(tmp_path)
    assert job_system_sample(context(layout, datetime(2026, 10, 12, 9, 0, tzinfo=UTC))) == {
        "day": "2026-10-12",
        "drives": 0,
    }
