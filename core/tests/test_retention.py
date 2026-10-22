"""Retention runs that fail or stop half-way never leave a record that looks valid.

The deletion record and its token exist under their final names only after the token verified;
the remaining chain verifies after every run, also one that stopped while deleting; an
interrupted run is finished by the next one. Every failure mode of the mock time-stamp
authority is exercised.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core import retention
from arcilogger_core.alerts import read_alerts
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import sealed_days, verify_range
from arcilogger_core.immutability import is_locked
from arcilogger_core.retention import RetentionResult, recover, run_retention, unfinished_record
from arcilogger_core.tsa.mock import MockFailure, MockTsaProvider


def _seal(world: Any, days: list[date]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert all(o.status == "sealed" for o in world.sealer().seal_due())


def _old(world: Any, days: list[date], *, deleting: int = 1) -> None:
    """Seal ``days`` and move to the day on which the first ``deleting`` of them expire."""
    _seal(world, days)
    world.reconfigure(retention={"enabled": True, "days": 730})
    world.set_now(days[deleting] + timedelta(days=730))


def _run(world: Any) -> RetentionResult:
    return run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
    )


def _deletions(world: Any) -> list[str]:
    folder: Path = world.layout.deletions
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _retention_alerts(world: Any) -> list[dict[str, Any]]:
    return [a for a in read_alerts(world.layout.alerts) if a["rule"] == "R20-retention"]


def _chain_ok(world: Any, days: list[date]) -> None:
    checks = verify_range(world.layout, days, [world.tsa.root_certificate], deep=True)
    assert all(c.ok for c in checks), [(c.day, c.errors) for c in checks]


@pytest.mark.parametrize(
    ("failure", "stage", "severity"),
    [
        (MockFailure.UNREACHABLE, "stamp", "high"),
        (MockFailure.TIMEOUT, "stamp", "high"),
        (MockFailure.REJECTION, "stamp", "high"),
        (MockFailure.NO_CREDIT, "stamp", "high"),
        (MockFailure.WRONG_IMPRINT, "stamp", "high"),
        (MockFailure.EXPIRED_CERT, "verify", "critical"),
        (MockFailure.BAD_SIGNATURE, "verify", "critical"),
    ],
)
def test_a_failed_stamp_leaves_no_record_and_deletes_nothing(
    world: Any, days: list[date], failure: MockFailure, stage: str, severity: str
) -> None:
    _old(world, days)
    world.tsa.failure = failure
    result = _run(world)
    assert result.error, failure
    assert "nothing deleted" in result.error
    assert result.deleted == []
    assert result.record is None
    # Neither the record nor its token stays behind, under any name.
    assert _deletions(world) == []
    assert not world.layout.chain_anchor.exists()
    assert sealed_days(world.layout) == days
    _chain_ok(world, days)
    (alert,) = _retention_alerts(world)
    assert alert["severity"] == severity
    assert alert["details"]["stage"] == stage
    assert alert["details"]["provider"] == "mock"
    assert alert["message"] == result.error

    # The service is back: the next run deletes with a valid record under the plain name.
    world.tsa.failure = MockFailure.NONE
    again = _run(world)
    assert again.error is None, again.error
    assert again.deleted == [days[0]]
    name = f"{(days[1] + timedelta(days=730)).isoformat()}-imha.json"
    assert again.record == name
    assert _deletions(world) == [name, name + ".zd", "chain-anchor.json"]
    _chain_ok(world, days[1:])


def test_the_unreachable_service_is_named_in_the_alert(world: Any, days: list[date]) -> None:
    _old(world, days)
    world.tsa.failure = MockFailure.UNREACHABLE
    result = _run(world)
    assert result.error is not None
    assert "could not be time-stamped" in result.error
    assert "unreachable" in result.error
    assert _retention_alerts(world)[-1]["details"]["retryable"] is True


def test_the_next_sealing_does_not_seal_a_failed_record(world: Any, days: list[date]) -> None:
    """A failed run leaves nothing for the next manifest's ``deletions`` list."""
    # The next day is a fixture day that ended in the real past: the mock TSA stamps with the
    # real time, which must be after the end of the day it seals.
    day = days[2]
    _old(world, days[:2])
    world.tsa.failure = MockFailure.BAD_SIGNATURE
    assert _run(world).error
    world.tsa.failure = MockFailure.NONE
    world.busy_day(day)
    now = world.now
    world.set_now(day + timedelta(days=1))
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    manifest = json.loads(world.layout.manifest(day).read_bytes())
    assert manifest["deletions"] == []
    world.now = now


def test_leftovers_of_an_interrupted_stamp_are_removed(world: Any, days: list[date]) -> None:
    """Power loss while stamping: a ``.part`` record (with or without its ``.part.zd``) and an
    orphaned final token are never valid; the next run removes them and starts over."""
    _old(world, days)
    folder = world.layout.deletions
    folder.mkdir(parents=True)
    (folder / "2024-01-01-imha.json.part").write_bytes(b'{"days": []}')
    (folder / "2024-01-01-imha.json.part.zd").write_bytes(b"not a token")
    (folder / "2024-01-02-imha.json.part").write_bytes(b'{"days": []}')
    (folder / "2024-01-03-imha.json.zd").write_bytes(b"orphan")
    removed = recover(world.layout, [world.tsa.root_certificate])
    assert sorted(removed) == [
        "2024-01-01-imha.json.part",
        "2024-01-01-imha.json.part.zd",
        "2024-01-02-imha.json.part",
        "2024-01-03-imha.json.zd",
    ]
    assert _deletions(world) == []
    result = _run(world)
    assert result.deleted == [days[0]], result.error
    assert result.discarded == []


def test_a_promotion_interrupted_between_the_renames_is_completed(
    world: Any, days: list[date]
) -> None:
    """The token was renamed, the record was not: the token verifies against the part, so the
    next run completes the rename and finishes the deletion."""
    _old(world, days)
    provider = MockTsaProvider(world.tsa)
    folder = world.layout.deletions
    folder.mkdir(parents=True)
    part = folder / "2028-10-11-imha.json.part"
    entry = _entry(world, days[0])
    part.write_bytes(canonical_json({"schema": retention.DELETION_SCHEMA, "days": [entry]}))
    stamp = provider.stamp(part)
    stamp.token_path.replace(folder / "2028-10-11-imha.json.zd")
    # A token that does not belong to its part is not promoted.
    other = folder / "2028-10-12-imha.json.part"
    other.write_bytes(b'{"days": []}')
    (folder / "2028-10-12-imha.json.zd").write_bytes(
        (folder / "2028-10-11-imha.json.zd").read_bytes()
    )

    result = _run(world)
    assert result.error is None, result.error
    assert result.resumed == "2028-10-11-imha.json"
    assert result.deleted == [days[0]]
    assert sorted(result.discarded) == ["2028-10-12-imha.json.part", "2028-10-12-imha.json.zd"]
    assert _deletions(world) == [
        "2028-10-11-imha.json",
        "2028-10-11-imha.json.zd",
        "chain-anchor.json",
    ]
    anchor = json.loads(world.layout.chain_anchor.read_bytes())
    assert anchor["record"] == "2028-10-11-imha.json"
    assert is_locked(world.layout.deletions / "2028-10-11-imha.json")
    assert is_locked(world.layout.deletions / "2028-10-11-imha.json.zd")
    _chain_ok(world, days[1:])


def _entry(world: Any, day: date) -> dict[str, Any]:
    from arcilogger_core.seal import sha256_file

    layout = world.layout
    return {
        "day": day.isoformat(),
        "manifest_sha256": sha256_file(layout.manifest(day)),
        "token_sha256": sha256_file(layout.token(day)),
    }


def test_an_interrupted_deletion_keeps_the_anchor_consistent_and_is_finished(
    world: Any, days: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the second of two days fails at its first file (a file in use on Windows): the
    first day is gone and the anchor names it, the second day is intact — the remaining chain
    verifies as it is (no false "chain broken" until the next run); the next run finishes."""
    _old(world, days, deleting=2)
    real = retention._delete_data
    calls: list[date] = []

    def in_use(layout: Any, day: date) -> None:
        calls.append(day)
        if day == days[1]:
            raise PermissionError(13, "The process cannot access the file", "fortigate.log.gz")
        real(layout, day)

    monkeypatch.setattr(retention, "_delete_data", in_use)
    first = _run(world)
    assert calls == days[:2]
    assert first.deleted == [days[0]]
    assert first.error is not None
    assert "next run finishes" in first.error
    assert first.error.startswith(f"{days[1]} gününün silinmesi yarıda kaldı")
    record = first.record
    assert record is not None
    (alert,) = [a for a in _retention_alerts(world) if a["severity"] == "critical"]
    assert alert["details"]["day"] == days[1].isoformat()
    assert sealed_days(world.layout) == days[1:]
    anchor = json.loads(world.layout.chain_anchor.read_bytes())
    # The anchor names the last day that is gone; the untouched day still links to it.
    assert anchor["day"] == days[0].isoformat()
    assert anchor["record"] == record
    _chain_ok(world, sealed_days(world.layout))
    assert unfinished_record(world.layout, [world.tsa.root_certificate]) is not None

    monkeypatch.setattr(retention, "_delete_data", real)
    second = _run(world)
    assert second.error is None, second.error
    assert second.resumed == record
    assert second.deleted == [days[1]]
    assert sealed_days(world.layout) == days[2:]
    assert unfinished_record(world.layout, [world.tsa.root_certificate]) is None
    assert json.loads(world.layout.chain_anchor.read_bytes())["day"] == days[1].isoformat()
    _chain_ok(world, days[2:])
    assert [a["severity"] for a in _retention_alerts(world)] == ["critical", "info"]
    for day in days[:2]:
        assert world.sealer().state(day)["deleted_by"] == record
    assert is_locked(world.layout.deletions / record)


def test_a_deletion_that_stopped_inside_a_day_leaves_only_that_day_incomplete(
    world: Any, days: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day's data files are gone, its manifest and token not yet (power loss): that day is
    reported incomplete, the days after it still verify (they link to its manifest), and the
    next run finishes it and moves the anchor."""
    _old(world, days)
    real = retention._write_anchor

    def power_loss(layout: Any, record: Path, last: dict[str, Any]) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(retention, "_write_anchor", power_loss)
    first = _run(world)
    assert first.error is not None
    assert first.deleted == []
    assert sealed_days(world.layout) == days
    assert not world.layout.chain_anchor.exists()
    broken = verify_range(world.layout, days, [world.tsa.root_certificate])
    assert [c.day for c in broken if not c.ok] == [days[0]]
    assert all("missing" in error for error in broken[0].errors), broken[0].errors
    _chain_ok(world, days[1:])

    monkeypatch.setattr(retention, "_write_anchor", real)
    second = _run(world)
    assert second.error is None, second.error
    assert second.resumed == first.record
    assert second.deleted == [days[0]]
    assert json.loads(world.layout.chain_anchor.read_bytes())["day"] == days[0].isoformat()
    _chain_ok(world, days[1:])


def test_a_day_changed_after_the_record_is_not_deleted(
    world: Any, days: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    _old(world, days, deleting=2)
    real = retention._delete_data

    def stop_at_second(layout: Any, day: date) -> None:
        if day == days[1]:
            raise OSError("disk error")
        real(layout, day)

    monkeypatch.setattr(retention, "_delete_data", stop_at_second)
    assert _run(world).error
    monkeypatch.setattr(retention, "_delete_data", real)
    token = world.layout.token(days[1])
    from arcilogger_core.immutability import unlock

    unlock(token)
    token.write_bytes(token.read_bytes() + b"\x00")
    result = _run(world)
    assert result.error is not None
    assert "changed after" in result.error
    assert days[1] in sealed_days(world.layout)


def test_a_record_without_a_valid_stamp_never_authorises_a_deletion(
    world: Any, days: list[date]
) -> None:
    """An unstamped record (left by a version before the ``.part`` naming) lists days that are
    still in the vault: it is not "finished"; a new, stamped record is written instead."""
    _old(world, days)
    folder = world.layout.deletions
    folder.mkdir(parents=True)
    today = (days[1] + timedelta(days=730)).isoformat()
    legacy = folder / f"{today}-imha.json"
    legacy.write_bytes(canonical_json({"days": [_entry(world, days[0])]}))
    assert unfinished_record(world.layout, [world.tsa.root_certificate]) is None
    result = _run(world)
    assert result.error is None, result.error
    assert result.resumed is None
    assert result.record == f"{today}-2-imha.json"
    assert result.deleted == [days[0]]
    assert legacy.exists()  # never deleted: an earlier manifest may have sealed it
