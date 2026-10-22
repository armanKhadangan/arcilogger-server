"""Tampering with sealed evidence is detected."""

from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from typing import Any

from arcilogger_core.chain import verify_day, verify_range
from arcilogger_core.immutability import unlock


def _seal(world: Any, days: list[date]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert all(o.status == "sealed" for o in world.sealer().seal_due())


def _roots(world: Any) -> list[Any]:
    return [world.tsa.root_certificate]


def test_changed_raw_file_is_detected(world: Any, days: list[date]) -> None:
    _seal(world, days)
    path = world.layout.vault_day(days[1]) / "fortigate.log.gz"
    unlock(path)
    original = gzip.decompress(path.read_bytes())
    assert b"srcip=172.20.42.76 " in original  # a format-sample line: the content really changes
    lines = original.replace(b"srcip=172.20.42.76 ", b"srcip=172.20.42.77 ")
    path.write_bytes(gzip.compress(lines))
    checks = verify_range(world.layout, days, _roots(world))
    assert [c.ok for c in checks] == [True, False, True]
    assert any("SHA-256 mismatch" in error for error in checks[1].errors)


def test_recompressed_identical_content_is_still_flagged(world: Any, days: list[date]) -> None:
    """Even a re-compression with the same content changes the sealed bytes."""
    _seal(world, days[:1])
    path = world.layout.vault_day(days[0]) / "core-r1.log.gz"
    unlock(path)
    path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()), mtime=1))
    assert not verify_day(world.layout, days[0], _roots(world)).ok


def test_changed_manifest_is_detected(world: Any, days: list[date]) -> None:
    _seal(world, days)
    manifest_path = world.layout.manifest(days[0])
    unlock(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][0]["lines"] += 1
    manifest_path.write_bytes(json.dumps(manifest).encode())
    first = verify_day(world.layout, days[0], _roots(world))
    assert any("canonical" in error for error in first.errors)
    assert any("imprint" in error for error in first.errors)
    second = verify_day(world.layout, days[1], _roots(world))
    assert any("was changed" in error for error in second.errors)


def test_deleted_day_breaks_the_chain(world: Any, days: list[date]) -> None:
    _seal(world, days)
    for path in world.layout.vault_day(days[1]).iterdir():
        unlock(path)
        path.unlink()
    checks = verify_range(world.layout, [days[0], days[2]], _roots(world))
    by_day = {check.day: check for check in checks}
    assert any("missing from the chain" in e for e in by_day[days[1]].errors)
    assert any("is missing" in e for e in by_day[days[2]].errors)


def test_token_from_another_day_does_not_verify(world: Any, days: list[date]) -> None:
    _seal(world, days[:2])
    token_0, token_1 = world.layout.token(days[0]), world.layout.token(days[1])
    unlock(token_1)
    token_1.write_bytes(token_0.read_bytes())
    check = verify_day(world.layout, days[1], _roots(world))
    assert any("imprint" in error for error in check.errors)


def test_deep_check_reads_the_uncompressed_content(world: Any, days: list[date]) -> None:
    _seal(world, days[:1])
    assert verify_day(world.layout, days[0], _roots(world), deep=True).ok


def test_missing_manifest_or_token(world: Any, days: list[date]) -> None:
    _seal(world, days[:1])
    assert verify_day(world.layout, date(2026, 1, 1), _roots(world)).errors == ["manifest missing"]
    token = world.layout.token(days[0])
    unlock(token)
    token.unlink()
    assert "time-stamp token missing" in verify_day(world.layout, days[0], _roots(world)).errors


def test_result_serialisation(world: Any, days: list[date]) -> None:
    _seal(world, days[:1])
    data = verify_day(world.layout, days[0], _roots(world)).as_json()
    assert data["ok"] is True
    assert data["day"] == "2026-10-10"
    assert "Mock Root" in data["root"]
