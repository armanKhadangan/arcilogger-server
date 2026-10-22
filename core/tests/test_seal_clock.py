"""Server clock steps, stray spool files and long runs of empty days never corrupt the chain.

* A clock that jumped backwards creates spool files of old days: they never move the chain
  start back (no mass stamping), are never sealed or deleted, and raise one critical alert.
* A clock that jumped forward would seal a day that has not ended: the time-stamp authority's
  time (genTime) must be after the end of the day, otherwise the stamp is discarded and the day
  is tried again later.
* More than ``seal.max_consecutive_empty_days`` days without any log are not stamped one by
  one; the next sealed day lists them in its gaps and links to the last sealed day before.
"""

from __future__ import annotations

import json
import os
import time as systime
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.alerts import read_alerts
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import sealed_days, verify_day, verify_range
from arcilogger_core.evidence import build_package, package_days
from arcilogger_core.evidence.archive import ArchivePackage
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import unlock
from arcilogger_core.retention import run_retention
from arcilogger_core.seal import UNSEALED_DAYS, Sealer, sha256_file, unsealed_days
from arcilogger_core.timeutil import day_window
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider


def _seal_alerts(world: Any) -> list[dict[str, Any]]:
    rules = ("R20-seal", "R20-clock", "R20-seal-skip")
    return [alert for alert in read_alerts(world.layout.alerts) if alert["rule"] in rules]


def _manifest(world: Any, day: date) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(world.layout.manifest(day).read_bytes())
    return data


def _seal(world: Any, days: list[date]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"] * len(days)


# -- a clock that jumped backwards ---------------------------------------------------------------


def test_an_old_spool_file_never_moves_the_chain_start_back(world: Any, days: list[date]) -> None:
    """CMOS reset before time sync: one message filed under a day three weeks back."""
    _seal(world, days)
    credits = world.tsa.credits
    stray = world.write_spool("fortigate", date(2026, 9, 20), [(time(9, 0), b"<1>early")])
    world.now += timedelta(minutes=30)
    sealer = world.sealer()
    assert sealer.chain_start() == days[0]
    assert sealer.seal_due() == []
    assert world.tsa.credits == credits  # nothing was stamped
    assert stray.exists()  # raw evidence is never deleted
    assert sealed_days(world.layout) == days
    checks = verify_range(world.layout, days, [world.tsa.root_certificate], deep=True)
    assert all(check.ok for check in checks), [check.errors for check in checks]
    alerts = _seal_alerts(world)
    assert [a["severity"] for a in alerts] == ["critical"]
    assert alerts[0]["details"]["why"] == "before-chain"
    assert alerts[0]["rule"] == "R20-clock"  # not a failed sealing attempt (R20-seal)
    assert "2026-09-20" in alerts[0]["details"]["day"]
    world.sealer().seal_due()
    assert len(_seal_alerts(world)) == 1  # one alert per problem, not one per run


def test_a_recreated_spool_file_of_a_sealed_day_is_reported_and_kept(
    world: Any, days: list[date]
) -> None:
    _seal(world, days)
    again = world.write_spool("core-r1", days[1], [(time(23, 50), b"<30>late line")])
    audit = world.layout.audit_spool(days[2])
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_bytes(b'{"action":"login","hash":"aa"}\n')
    sealer = world.sealer()
    assert sealer.seal_due() == []
    assert sealer.seal_day(days[1]).message == "already sealed"
    assert again.exists()
    assert audit.exists()
    whys = sorted((a["details"]["source"], a["details"]["why"]) for a in _seal_alerts(world))
    assert whys == [("_audit", "sealed"), ("core-r1", "sealed")]


def test_a_sealed_day_after_today_means_the_clock_went_back(world: Any, days: list[date]) -> None:
    _seal(world, days)
    world.set_now(days[1], 12, 0)  # the clock jumped back into a sealed day
    world.write_spool("fortigate", days[2] + timedelta(days=3), [(time(1, 0), b"<1>x")])
    assert world.sealer().seal_due() == []
    keys = {a["details"]["key"] for a in _seal_alerts(world)}
    assert "clock-behind:sealed:2026-10-12" in keys
    assert "stray:future:fortigate:2026-10-15" in keys
    assert any(key.startswith("clock-behind:spool:") for key in keys)


def test_first_day_cannot_move_the_chain_back_nor_reseal_a_deleted_day(
    world: Any, days: list[date]
) -> None:
    world.reconfigure(seal={"quiet_seconds": 0, "gzip_level": 6, "first_day": days[0]})
    _seal(world, days[:2])
    world.reconfigure(
        seal={"quiet_seconds": 0, "gzip_level": 6, "first_day": "2026-09-01"},
        retention={"enabled": True, "days": 730},
    )
    assert world.sealer().chain_start() == days[0]
    world.set_now(days[1] + timedelta(days=730))
    result = run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
    )
    assert result.deleted == [days[0]], result.error
    world.set_now(days[1] + timedelta(days=1))
    sealer = world.sealer()
    assert sealer.chain_start() == days[1]  # the day after the chain anchor
    assert sealer.seal_due() == []
    assert not world.layout.vault_day(days[0]).exists()


# -- a clock that jumped forward -----------------------------------------------------------------


@dataclass
class TsaClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


def test_a_day_is_not_sealed_before_the_tsa_says_it_ended(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    """The server clock jumped from 23:45 to 00:15; Kamu SM's clock still says 23:46."""
    day = days[0]
    world.busy_day(day)
    end = day_window(day)[1]
    tsa_clock = TsaClock(end - timedelta(minutes=14))
    tsa = MockTsa(tmp_path / "tsa", key_type="ec", clock=tsa_clock)
    world.now = end + timedelta(minutes=15)

    def sealer() -> Sealer:
        return Sealer(
            world.config,
            provider=MockTsaProvider(tsa),
            roots=[tsa.root_certificate],
            clock=world.clock,
            clock_status=lambda: "test",
        )

    outcomes = sealer().seal_due()
    assert [o.status for o in outcomes] == ["pending"]
    assert "server clock is ahead" in outcomes[0].message
    assert not world.layout.token(day).exists()
    assert not world.layout.manifest(day).exists()
    assert world.layout.spool_files_for(day)  # the raw data stays in the spool
    assert sealer().state(day)["status"] == "pending"
    assert _seal_alerts(world)[-1]["severity"] == "critical"

    # After the correction the day goes on until the real midnight; then it is sealed whole.
    world.write_spool("fortigate", day, [(time(23, 55), b"<1>after the correction")])
    tsa_clock.now = end + timedelta(minutes=20)
    world.now = end + timedelta(minutes=40)
    assert [o.status for o in sealer().seal_due()] == ["sealed"]
    files = {f["source"]: f for f in _manifest(world, day)["files"]}
    assert files["fortigate"]["lines"] == 145
    assert files["fortigate"]["last_received"] == "2026-10-10T23:55:00.000+03:00"


def test_a_day_is_sealed_only_after_the_quiet_time_past_midnight(
    world: Any, days: list[date]
) -> None:
    world.reconfigure(seal={"quiet_seconds": 600, "gzip_level": 6})
    world.busy_day(days[0])
    old = systime.time() - 3600
    for _, path in world.layout.spool_files_for(days[0]):
        os.utime(path, (old, old))
    world.now = day_window(days[0])[1] + timedelta(minutes=5)
    outcome = world.sealer().seal_day(days[0])
    assert outcome.status == "pending"
    assert "ended less than 600 s ago" in outcome.message
    world.now += timedelta(minutes=10)
    assert world.sealer().seal_day(days[0]).status == "sealed"


# -- long runs of empty days ---------------------------------------------------------------------


def test_a_long_run_of_empty_days_is_listed_not_stamped(world: Any, tmp_path: Path) -> None:
    """Server off for a week: the days are not stamped one by one (limit 2)."""
    world.reconfigure(seal={"quiet_seconds": 0, "gzip_level": 6, "max_consecutive_empty_days": 2})
    first, last = date(2026, 10, 1), date(2026, 10, 9)
    world.busy_day(first)
    world.busy_day(last)
    world.set_now(last + timedelta(days=1))
    credits = world.tsa.credits
    outcomes = world.sealer().seal_due()
    assert [(o.day.day, o.status) for o in outcomes] == [
        (1, "sealed"),
        *((n, "skipped") for n in range(2, 9)),
        (9, "sealed"),
    ]
    assert credits - world.tsa.credits == 2
    assert sealed_days(world.layout) == [first, last]
    manifest = _manifest(world, last)
    assert manifest["previous"]["day"] == "2026-10-01"
    assert unsealed_days(manifest) == (date(2026, 10, 2), date(2026, 10, 8))
    (gap,) = [g for g in manifest["gaps"] if g.get("kind") == UNSEALED_DAYS]
    assert gap["source"] == "*"
    assert gap["from"] == "2026-10-02T00:00:00+03:00"
    assert gap["to"] == "2026-10-09T00:00:00+03:00"
    checks = verify_range(world.layout, [first, last], [world.tsa.root_certificate], deep=True)
    assert all(check.ok for check in checks), [check.errors for check in checks]
    assert checks[-1].skipped[0] == date(2026, 10, 2)
    assert world.sealer().state(date(2026, 10, 5))["covered_by"] == "2026-10-09"
    alerts = _seal_alerts(world)
    assert [a["details"]["key"] for a in alerts] == ["empty-run:2026-10-02"]
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["rule"] == "R20-seal-skip"

    # An evidence package of both days: the standalone verifier follows the link.
    info = build_package(
        world.layout,
        package_days(first, last),
        roots=[world.tsa.root_certificate],
        out_dir=tmp_path / "out",
    )
    assert info.days == (first, last)
    with ArchivePackage(info.path, None) as package:
        report = sv.verify_package(package)
    assert report.passed, [(f.code, f.en) for f in report.findings if f.level == "fail"]
    assert "chain-skipped-days" in report.codes("info")
    assert "chain-ok" in report.codes("ok")


def test_empty_days_are_stamped_one_by_one_up_to_the_limit(world: Any) -> None:
    """The collector stops: two empty days are stamped as they close, the third is not."""
    world.reconfigure(seal={"quiet_seconds": 0, "gzip_level": 6, "max_consecutive_empty_days": 2})
    start = date(2026, 10, 1)
    world.busy_day(start)
    statuses = []
    for offset in range(1, 6):
        world.set_now(start + timedelta(days=offset))
        statuses += [(o.day.day, o.status) for o in world.sealer().seal_due()]
    assert statuses == [(1, "sealed"), (2, "sealed"), (3, "sealed"), (4, "skipped"), (5, "skipped")]
    resumed = start + timedelta(days=5)
    world.busy_day(resumed)
    world.set_now(resumed + timedelta(days=1))
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    manifest = _manifest(world, resumed)
    assert manifest["previous"]["day"] == "2026-10-03"
    assert unsealed_days(manifest) == (date(2026, 10, 4), date(2026, 10, 5))
    days = sealed_days(world.layout)
    checks = verify_range(world.layout, days, [world.tsa.root_certificate])
    assert all(check.ok for check in checks), [check.errors for check in checks]


def _restamp(world: Any, day: date, manifest: dict[str, Any]) -> None:
    """Rewrite a sealed manifest and stamp it again with the trusted mock TSA."""
    path = world.layout.manifest(day)
    for target in (path, world.layout.token(day)):
        unlock(target)
        target.unlink()
    path.write_bytes(canonical_json(manifest))
    MockTsaProvider(world.tsa).stamp(path)


def test_a_link_may_skip_days_only_as_the_manifest_lists_them(world: Any, days: list[date]) -> None:
    _seal(world, days)
    roots = [world.tsa.root_certificate]
    manifest = _manifest(world, days[2])
    manifest["previous"] = _manifest(world, days[1])["previous"] | {
        "manifest_sha256": sha256_file(world.layout.manifest(days[0])),
        "token_sha256": sha256_file(world.layout.token(days[0])),
    }
    _restamp(world, days[2], manifest)
    check = verify_day(world.layout, days[2], roots)
    assert check.errors == ["chain broken: link does not point to the day before"]

    skip = {"kind": UNSEALED_DAYS, "first_day": "2026-10-11", "last_day": "2026-10-11"}
    _restamp(world, days[2], manifest | {"gaps": [*manifest["gaps"], skip]})
    check = verify_day(world.layout, days[2], roots)
    assert check.ok, check.errors
    assert check.skipped == [days[1]]

    wrong = skip | {"first_day": "2026-10-10", "last_day": "2026-10-10"}
    _restamp(world, days[2], manifest | {"gaps": [*manifest["gaps"], wrong]})
    check = verify_day(world.layout, days[2], roots)
    assert "chain broken: the skipped days do not end the day before" in check.errors

    broken = skip | {"last_day": "not a day"}
    _restamp(world, days[2], manifest | {"gaps": [*manifest["gaps"], broken]})
    assert any(e.startswith("gaps:") for e in verify_day(world.layout, days[2], roots).errors)


def test_an_invalid_skip_entry_is_refused() -> None:
    with pytest.raises(ValueError, match="Invalid isoformat"):
        unsealed_days({"gaps": [{"kind": UNSEALED_DAYS, "first_day": "2026-10-05"}]})
    backwards = {"kind": UNSEALED_DAYS, "first_day": "2026-10-05", "last_day": "2026-10-01"}
    with pytest.raises(ValueError, match="before"):
        unsealed_days({"gaps": [backwards]})
    assert unsealed_days({"gaps": [{"source": "switch-1"}]}) is None


# -- a skip is provisional until a later day's stamp covers it -----------------------------------


def _jump_sealer(world: Any, tsa: MockTsa) -> Sealer:
    return Sealer(
        world.config,
        provider=MockTsaProvider(tsa),
        roots=[tsa.root_certificate],
        clock=world.clock,
        clock_status=lambda: "test",
    )


def test_a_forward_jump_never_leaves_real_days_unsealed(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    """At 23:45 the server clock jumps ten days ahead and stays there past the real midnight;
    the days it skipped as empty get their real data after the correction and are sealed."""
    day = days[0]  # 10.10
    world.busy_day(day - timedelta(days=1))
    world.busy_day(day)
    end = day_window(day)[1]
    tsa_clock = TsaClock(day_window(day)[0] + timedelta(hours=1))
    tsa = MockTsa(tmp_path / "tsa", key_type="ec", clock=tsa_clock)
    world.now = tsa_clock.now
    assert [o.status for o in _jump_sealer(world, tsa).seal_due()] == ["sealed"]

    jumped = day + timedelta(days=10)
    world.write_spool("fortigate", jumped, [(time(0, 16), b"<1>after the jump")])
    tsa_clock.now = end - timedelta(minutes=14)
    world.now = day_window(jumped)[0] + timedelta(minutes=16)
    assert [o.status for o in _jump_sealer(world, tsa).seal_due()] == ["pending"]  # genTime

    # Past the real midnight, the clock still ahead: the day is sealed, the next nine skipped.
    tsa_clock.now = end + timedelta(minutes=10)
    world.now += timedelta(minutes=6)
    outcomes = _jump_sealer(world, tsa).seal_due()
    assert [o.status for o in outcomes] == ["sealed"] + ["skipped"] * 9
    skipped = [day + timedelta(days=n) for n in range(1, 10)]

    # The clock is corrected at 14.10 01:00; the real data of 11.10 and 13.10 arrives (12.10
    # really had no log at all).
    for offset in (1, 3):
        world.write_spool("fortigate", day + timedelta(days=offset), [(time(10, 0), b"<1>real")])
    world.set_now(day + timedelta(days=4), 1, 0)
    tsa_clock.now = world.now
    sealer = _jump_sealer(world, tsa)
    outcomes = sealer.seal_due()
    assert [(o.day.day, o.status) for o in outcomes] == [(n, "sealed") for n in (11, 12, 13)]
    withdrawn = {
        a["details"]["day"]: (a["details"]["why"], a["severity"])
        for a in _seal_alerts(world)
        if a["details"].get("key", "").startswith("unskip:")
    }
    assert withdrawn == {
        "2026-10-11": ("data", "high"),
        "2026-10-12": ("short-run", "info"),
        "2026-10-13": ("data", "high"),
        **{d.isoformat(): ("not-ended", "high") for d in skipped[3:]},
    }
    assert all(sealer.state(d)["status"] == "pending" for d in skipped[3:])
    assert _manifest(world, skipped[0])["previous"]["day"] == "2026-10-10"
    assert _manifest(world, skipped[1])["files"] == []  # stamped as an empty day, not listed

    # A week later every real day is sealed in an unbroken chain; nothing is listed as empty.
    for offset in range(4, 11):
        world.write_spool("fortigate", day + timedelta(days=offset), [(time(10, 0), b"<1>real")])
    world.set_now(day + timedelta(days=11), 1, 0)
    tsa_clock.now = world.now
    assert [o.status for o in _jump_sealer(world, tsa).seal_due()] == ["sealed"] * 7
    sealed = sealed_days(world.layout)
    assert sealed == [day - timedelta(days=1) + timedelta(days=n) for n in range(12)]
    assert not any(world.layout.spool_files_for(d) for d in sealed)
    assert all(unsealed_days(_manifest(world, d)) is None for d in sealed)
    checks = verify_range(
        world.layout, sealed, [tsa.root_certificate], deep=True, now=world.now + timedelta(days=1)
    )
    assert all(check.ok for check in checks), [check.errors for check in checks]


def _skip_three(world: Any) -> tuple[date, list[date]]:
    """10.01 sealed with data, 10.02-10.04 skipped (limit 2), 10.05 with data and closed."""
    world.reconfigure(seal={"quiet_seconds": 0, "gzip_level": 6, "max_consecutive_empty_days": 2})
    first = date(2026, 10, 1)
    world.busy_day(first)
    world.set_now(first + timedelta(days=4))
    outcomes = world.sealer().seal_due()
    assert [o.status for o in outcomes] == ["sealed", "skipped", "skipped", "skipped"]
    resumed = first + timedelta(days=4)
    world.busy_day(resumed)
    world.set_now(resumed + timedelta(days=1))
    return resumed, [first + timedelta(days=n) for n in (1, 2, 3)]


def test_a_skipped_day_with_data_is_never_listed_as_empty(world: Any) -> None:
    resumed, skipped = _skip_three(world)
    world.write_spool("core-r1", skipped[1], [(time(8, 0), b"<30>late but real")])
    credits = world.tsa.credits
    outcome = world.sealer().seal_day(resumed)  # straight to the day after the run
    assert outcome.status == "pending"
    assert "skipped as empty but now with data: 2026-10-03" in outcome.message
    assert credits == world.tsa.credits
    assert not world.layout.manifest(resumed).exists()
    assert world.sealer().state(skipped[1])["skip_withdrawn"]["why"] == "data"

    world.now += timedelta(minutes=6)
    outcomes = world.sealer().seal_due()
    assert [(o.day.day, o.status) for o in outcomes] == [(n, "sealed") for n in (2, 3, 4, 5)]
    assert unsealed_days(_manifest(world, resumed)) is None
    assert _manifest(world, skipped[1])["files"][0]["lines"] == 1


class _DataDuringStamp(MockTsaProvider):
    """Writes a raw line for a skipped day while the next day is being stamped."""

    def __init__(self, tsa: MockTsa, world: Any, day: date) -> None:
        super().__init__(tsa)
        self.world, self.day = world, day

    def stamp(self, file_path: Path) -> Any:
        self.world.write_spool("core-r1", self.day, [(time(8, 0), b"<30>arrived meanwhile")])
        return super().stamp(file_path)


def test_data_that_arrives_while_the_next_day_is_stamped_discards_the_stamp(world: Any) -> None:
    resumed, skipped = _skip_three(world)
    sealer = Sealer(
        world.config,
        provider=_DataDuringStamp(world.tsa, world, skipped[2]),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
        clock_status=lambda: "test",
    )
    outcomes = sealer.seal_due()
    assert [(o.day, o.status) for o in outcomes] == [(resumed, "pending")]
    assert "skipped as empty but now with data: 2026-10-04" in outcomes[0].message
    assert not world.layout.manifest(resumed).exists()
    assert not world.layout.token(resumed).exists()
    assert sealer.state(skipped[2])["status"] == "pending"


def test_a_skip_covered_by_a_later_stamp_is_final(world: Any) -> None:
    resumed, skipped = _skip_three(world)
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    assert unsealed_days(_manifest(world, resumed)) == (skipped[0], skipped[-1])
    late = world.write_spool("core-r1", skipped[1], [(time(8, 0), b"<30>after the seal")])
    world.now += timedelta(hours=1)
    assert world.sealer().review_skips() == []
    assert world.sealer().seal_due() == []
    assert world.sealer().state(skipped[1])["status"] == "skipped"
    assert late.exists()  # raw evidence stays for a person to examine
    (alert,) = [a for a in _seal_alerts(world) if a["details"].get("key", "").startswith("stray:")]
    assert (alert["details"]["why"], alert["severity"]) == ("sealed", "critical")


def test_a_stamp_found_after_a_crash_still_covers_its_skipped_days(world: Any) -> None:
    resumed, skipped = _skip_three(world)
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    # The Seal task stopped after the stamp, before the day was recorded as sealed.
    world.layout.seal_state(resumed).write_text('{"status": "pending"}', encoding="utf-8")
    for day in skipped:
        state = world.sealer().state(day)
        del state["covered_by"]
        world.layout.seal_state(day).write_text(json.dumps(state), encoding="utf-8")
    world.write_spool("core-r1", skipped[0], [(time(8, 0), b"<30>after the stamp")])
    world.now += timedelta(minutes=10)
    sealer = world.sealer()
    assert sealer.review_skips() == []  # the stamp in the vault covers them
    assert [o.status for o in sealer.seal_due()] == ["sealed"]
    assert [sealer.state(day).get("covered_by") for day in skipped] == ["2026-10-05"] * 3
    assert sealer.state(skipped[0])["status"] == "skipped"


# -- waiting for the quiet time, test messages before the first day -----------------------------


def test_the_run_at_midnight_waits_without_a_failed_attempt(world: Any, days: list[date]) -> None:
    """The Seal task repeats every 5 minutes and so runs at 00:00 too (default quiet 30 s)."""
    world.reconfigure(seal={"quiet_seconds": 30, "gzip_level": 6})
    world.busy_day(days[0])
    old = systime.time() - 3600
    for _, path in world.layout.spool_files_for(days[0]):
        os.utime(path, (old, old))
    world.now = day_window(days[0])[1] + timedelta(seconds=5)
    outcomes = world.sealer().seal_due()
    assert [(o.status, o.message) for o in outcomes] == [
        ("waiting", "2026-10-10 ended less than 30 s ago")
    ]
    assert _seal_alerts(world) == []
    assert world.sealer().state(days[0])["attempts"] == 0
    world.now += timedelta(seconds=30)
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]


def test_test_messages_before_the_first_day_raise_only_an_info_alert(
    world: Any, days: list[date]
) -> None:
    """Installed on 11.10 with seal.first_day = 11.10; the devices were tested on 10.10."""
    world.reconfigure(seal={"quiet_seconds": 0, "gzip_level": 6, "first_day": days[1]})
    trial = world.write_spool("fortigate", days[0], [(time(15, 0), b"<1>test message")])
    world.busy_day(days[1])
    world.set_now(days[2])
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    world.now += timedelta(hours=1)
    assert world.sealer().seal_due() == []  # the chain is established now: still no new alert
    (alert,) = _seal_alerts(world)
    assert (alert["details"]["why"], alert["severity"]) == ("before-first-day", "info")
    assert "seal.first_day = 11.10.2026" in alert["message"]
    assert trial.exists()

    # first_day moved forward past a day that has data: that day is not sealed (high alert).
    world.busy_day(days[2])
    world.reconfigure(
        seal={"quiet_seconds": 0, "gzip_level": 6, "first_day": days[2] + timedelta(days=1)}
    )
    world.set_now(days[2] + timedelta(days=1))
    assert world.sealer().seal_due() == []
    alert = _seal_alerts(world)[-1]
    assert (alert["details"]["why"], alert["severity"]) == ("before-first-day", "high")
    assert alert["details"]["day"] == "2026-10-12"
