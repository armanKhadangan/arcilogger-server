"""Trusted time and the trial's file lower bounds (licensing contract 3.3, 3.4; A-T9, A-T10).

The vault is sealed by the real core sealer with the mock TSA (the ``world`` fixture of
``conftest.py``); the mock's clock follows the world's clock so every day's genTime is known.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.license import trusted_time
from arcilogger_core.license.trusted_time import (
    TrustedTime,
    file_lower_bounds,
    read_only_roots,
    site_day_end,
    site_day_start,
    trusted_now,
)
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.verify import verify_token

pytestmark = pytest.mark.license_live

DAYS = [date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)]
EARLY_RELEASE = date(2026, 1, 1)


def site(day: date, hh: int = 0, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=SITE_TZ).astimezone(UTC)


def gen_time(day: date) -> datetime:
    """The genTime the mock gives ``day``: sealed at 00:30 of the next day."""
    return site(day + timedelta(days=1), 0, 30)


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    trusted_time.clear_cache()
    yield
    trusted_time.clear_cache()


@pytest.fixture
def sealed(world: Any) -> Any:
    """Three days sealed one by one, each stamped at 00:30 of the following day."""
    # A mock TSA whose certificates and genTimes follow the world's clock.
    shutil.rmtree(world.layout.state / "mock-tsa")
    world.now = site(DAYS[0])
    world.tsa = MockTsa(world.layout.state / "mock-tsa", key_type="ec", clock=world.clock)
    for day in DAYS:
        world.busy_day(day)
        world.set_now(day + timedelta(days=1), 0, 30)
        assert [outcome.status for outcome in world.sealer().seal_due()] == ["sealed"]
    return world


def now_at(world: Any, moment: datetime, **kwargs: Any) -> TrustedTime:
    kwargs.setdefault("release_date", EARLY_RELEASE)
    return trusted_now(world.layout, world.config, clock=lambda: moment, **kwargs)


def _writable(path: Path) -> Path:
    if path.exists() and is_locked(path):
        unlock(path)
    return path


# -- A-T9 trusted time --------------------------------------------------------------------------


def test_newest_verified_gen_time_is_the_tsa_floor(sealed: Any) -> None:
    system = site(date(2026, 10, 13), 9, 0)
    trusted = now_at(sealed, system)
    assert trusted.tsa == gen_time(DAYS[-1])
    assert trusted.tsa_day == DAYS[-1]
    assert trusted.vault_day_end == site_day_end(DAYS[-1])
    assert trusted.now == system
    assert trusted.source == "system"
    assert not trusted.clock_behind
    assert trusted.release_floor == site_day_start(EARLY_RELEASE)
    assert trusted.license_issued is None
    assert all(value.tzinfo == UTC for value in (trusted.now, trusted.system, trusted.tsa))


def test_a_clock_set_back_30_days_is_raised_to_the_tsa_time(sealed: Any) -> None:
    trusted = now_at(sealed, site(date(2026, 9, 13), 9, 0))
    assert trusted.now == gen_time(DAYS[-1])
    assert trusted.source == "tsa"
    assert trusted.clock_behind


def test_a_corrupted_newest_token_falls_back_to_the_previous_day(sealed: Any) -> None:
    _writable(sealed.layout.token(DAYS[-1])).write_bytes(b"not a token")
    trusted = now_at(sealed, site(date(2026, 9, 13)))
    assert trusted.tsa == gen_time(DAYS[-2])
    assert trusted.tsa_day == DAYS[-2]
    # The newest day still counts by its name: the vault-day floor wins.
    assert trusted.vault_day_end == site_day_end(DAYS[-1])
    assert trusted.now == site_day_end(DAYS[-1])
    assert trusted.source == "vault_day"


def test_a_changed_manifest_no_longer_verifies(sealed: Any) -> None:
    manifest = _writable(sealed.layout.manifest(DAYS[-1]))
    manifest.write_bytes(manifest.read_bytes() + b" ")
    assert now_at(sealed, site(date(2026, 9, 13))).tsa_day == DAYS[-2]


def test_an_empty_vault_has_no_vault_floors(world: Any) -> None:
    system = site(date(2026, 10, 13), 9, 0)
    trusted = now_at(world, system)
    assert trusted.tsa is None
    assert trusted.tsa_day is None
    assert trusted.vault_day_end is None
    assert trusted.now == system
    assert trusted.source == "system"


def test_without_roots_the_newest_vault_day_still_holds(sealed: Any, tmp_path: Path) -> None:
    empty = tmp_path / "roots"
    empty.mkdir()
    sealed.reconfigure(tsa={"provider": "rfc3161", "roots_dir": str(empty)})
    trusted = now_at(sealed, site(date(2026, 9, 13)))
    assert trusted.tsa is None
    assert trusted.now == site_day_end(DAYS[-1])
    assert trusted.now >= site_day_end(DAYS[-1])
    assert trusted.source == "vault_day"
    assert trusted.clock_behind


def test_the_mock_root_counts_only_while_the_provider_is_mock(sealed: Any) -> None:
    assert read_only_roots(sealed.config, sealed.layout)
    sealed.reconfigure(tsa={"provider": "rfc3161"})
    assert read_only_roots(sealed.config, sealed.layout) == []
    assert now_at(sealed, site(date(2026, 9, 13))).tsa is None
    assert read_only_roots(None, sealed.layout) == []


def test_junk_vault_days_remove_the_tsa_floor_but_keep_their_own_end(sealed: Any) -> None:
    junk = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]
    for day in junk:
        path = sealed.layout.manifest(day)
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
        sealed.layout.token(day).write_bytes(b"junk")
    trusted = now_at(sealed, site(date(2026, 9, 13)))
    assert trusted.tsa is None  # only the newest three days are scanned
    assert trusted.vault_day_end == site_day_end(junk[-1])
    assert trusted.now == site_day_end(junk[-1])
    assert trusted.source == "vault_day"


def test_names_that_are_not_days_are_ignored(sealed: Any) -> None:
    odd = sealed.layout.vault / "2026" / "10" / "31" / "manifest-2026-13-45.json"
    odd.parent.mkdir(parents=True)
    odd.write_text("{}", encoding="utf-8")
    (sealed.layout.vault / "notes").mkdir()
    (sealed.layout.vault / "notes" / "manifest-2030-01-01.json").write_text("{}", encoding="utf-8")
    assert now_at(sealed, site(date(2026, 9, 13))).vault_day_end == site_day_end(DAYS[-1])


def test_a_vault_day_name_without_an_end_is_ignored(sealed: Any) -> None:
    """``manifest-9999-12-31.json`` parses as a date, but its day has no end (the next day
    overflows): trusted_now must not raise for it (one file write would 503 the licence page
    and fail license.check), and it is no vault day."""
    last = sealed.layout.vault / "9999" / "12" / "31" / "manifest-9999-12-31.json"
    last.parent.mkdir(parents=True)
    last.write_text("{}", encoding="utf-8")
    assert trusted_time.vault_days(sealed.layout) == tuple(DAYS)
    trusted = now_at(sealed, site(date(2026, 9, 13)))
    assert trusted.vault_day_end == site_day_end(DAYS[-1])
    assert trusted.tsa_day == DAYS[-1]
    assert file_lower_bounds(sealed.layout) == {"vault": site_day_end(DAYS[0])}


def test_the_release_day_is_a_floor(world: Any) -> None:
    release = date(2026, 10, 20)
    trusted = now_at(world, site(date(2026, 10, 1), 12), release_date=release)
    assert trusted.release_floor == site(release)
    assert trusted.now == site(release)
    assert trusted.source == "release_date"
    assert trusted.clock_behind
    on_the_day = now_at(world, site(release, 0, 1), release_date=release)
    assert on_the_day.now == site(release, 0, 1)
    assert on_the_day.source == "system"


def test_the_default_release_date_is_the_published_one(world: Any) -> None:
    import arcilogger_core

    trusted = trusted_now(world.layout, world.config, clock=lambda: site(date(2026, 1, 2)))
    assert trusted.release_floor == site(date.fromisoformat(arcilogger_core.RELEASE_DATE))


def test_the_licence_issue_time_is_a_floor(world: Any) -> None:
    system = site(date(2026, 10, 1), 12)
    trusted = now_at(world, system)
    issued = site(date(2026, 10, 5), 15)
    raised = trusted.with_floor(issued, "license_issued")
    assert raised.now == issued
    assert raised.license_issued == issued
    assert raised.source == "license_issued"
    assert raised.clock_behind
    # Only raises: an older issue time changes nothing but is kept for display.
    kept = trusted.with_floor(site(date(2026, 9, 1)), "license_issued")
    assert kept.now == system
    assert kept.source == "system"
    with pytest.raises(ValueError, match="floor"):
        trusted.with_floor(issued, "tsa")  # type: ignore[arg-type]


def test_ties_go_to_the_earlier_source() -> None:
    moment = datetime(2026, 10, 20, 21, tzinfo=UTC)
    trusted = TrustedTime(
        now=moment,
        system=moment - timedelta(hours=1),
        tsa=moment,
        tsa_day=date(2026, 10, 20),
        vault_day_end=moment,
        release_floor=moment,
        license_issued=moment,
    )
    assert trusted.source == "tsa"
    assert trusted.clock_behind
    trusted = TrustedTime(
        now=moment,
        system=moment - timedelta(minutes=5),
        tsa=None,
        tsa_day=None,
        vault_day_end=moment,
        release_floor=moment - timedelta(days=1),
        license_issued=None,
    )
    assert trusted.source == "vault_day"
    assert not trusted.clock_behind  # exactly 5 minutes is not "behind"


def test_read_only_roots_writes_nothing_and_never_builds_the_mock(
    world: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("MockTsa constructed")

    monkeypatch.setattr(MockTsa, "__init__", forbidden)
    monkeypatch.setattr("arcilogger_core.tsa.trusted_roots", forbidden)
    fresh = Layout(tmp_path / "fresh-data")
    assert read_only_roots(world.config, fresh) == []
    assert not fresh.root.exists()
    trusted = trusted_now(fresh, world.config, clock=lambda: site(date(2026, 10, 13)))
    assert trusted.tsa is None
    assert not fresh.root.exists()
    # With an existing mock root (made before the patch would have fired): read, not created.
    state = world.layout.state
    monkeypatch.undo()
    MockTsa(state / "mock-tsa", key_type="ec")
    monkeypatch.setattr(MockTsa, "__init__", forbidden)
    before = sorted(path.relative_to(state) for path in state.rglob("*"))
    assert len(read_only_roots(world.config, world.layout)) == 1
    now_at(world, site(date(2026, 10, 13)))
    assert sorted(path.relative_to(state) for path in state.rglob("*")) == before


def test_trusted_now_never_raises(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(trusted_time, "_scan_vault_days", broken)
    trusted = now_at(world, site(date(2026, 10, 13)))
    assert trusted.vault_day_end is None
    monkeypatch.setattr(trusted_time, "read_only_roots", broken)
    assert now_at(world, site(date(2026, 10, 13))).tsa is None
    assert file_lower_bounds(world.layout) == {}


def test_the_token_result_is_cached_by_file_identity(
    sealed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original = verify_token

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(trusted_time, "verify_token", counting)
    moment = site(date(2026, 9, 13))
    assert now_at(sealed, moment).tsa_day == DAYS[-1]
    assert now_at(sealed, moment).tsa_day == DAYS[-1]
    assert len(calls) == 1
    _writable(sealed.layout.token(DAYS[-1])).write_bytes(b"replaced")
    assert now_at(sealed, moment).tsa_day == DAYS[-2]  # size/mtime changed: checked again


def test_the_vault_day_list_is_cached_for_a_minute(
    sealed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    moment = site(date(2026, 9, 13))
    clock = [1000.0]
    monkeypatch.setattr(trusted_time, "_monotonic", lambda: clock[0])
    assert now_at(sealed, moment).vault_day_end == site_day_end(DAYS[-1])
    later = date(2026, 10, 20)
    sealed.layout.manifest(later).parent.mkdir(parents=True)
    sealed.layout.manifest(later).write_text("{}", encoding="utf-8")
    assert now_at(sealed, moment).vault_day_end == site_day_end(DAYS[-1])
    clock[0] += 61
    assert now_at(sealed, moment).vault_day_end == site_day_end(later)


# -- A-T10 file lower bounds --------------------------------------------------------------------


def test_the_vault_bound_is_the_end_of_the_oldest_day_by_name(sealed: Any) -> None:
    bounds = file_lower_bounds(sealed.layout)
    assert bounds["vault"] == site_day_end(DAYS[0])


def test_a_re_stamped_oldest_day_does_not_move_the_vault_bound(sealed: Any) -> None:
    later = MockTsa(
        sealed.layout.state / "mock-tsa",
        key_type="ec",
        clock=lambda: datetime(2026, 12, 1, tzinfo=UTC),
    )
    token = _writable(sealed.layout.token(DAYS[0]))
    token.unlink()
    stamped = MockTsaProvider(later).stamp(sealed.layout.manifest(DAYS[0]))
    assert stamped.info.gen_time == datetime(2026, 12, 1, tzinfo=UTC)
    trusted_time.clear_cache()
    assert file_lower_bounds(sealed.layout)["vault"] == site_day_end(DAYS[0])


def test_the_chain_anchor_names_an_older_day(sealed: Any) -> None:
    anchor = sealed.layout.chain_anchor
    anchor.parent.mkdir(parents=True, exist_ok=True)
    _writable(anchor).write_text(json.dumps({"day": "2026-09-01"}), encoding="utf-8")
    assert file_lower_bounds(sealed.layout)["vault"] == site_day_end(date(2026, 9, 1))
    _writable(anchor).write_text(json.dumps({"day": "2026-10-11"}), encoding="utf-8")
    assert file_lower_bounds(sealed.layout)["vault"] == site_day_end(DAYS[0])


def test_a_chain_anchor_alone_is_a_vault_bound(world: Any) -> None:
    anchor = world.layout.chain_anchor
    anchor.parent.mkdir(parents=True)
    anchor.write_text(json.dumps({"day": "2026-08-01"}), encoding="utf-8")
    assert file_lower_bounds(world.layout) == {"vault": site_day_end(date(2026, 8, 1))}
    anchor.write_text("not json", encoding="utf-8")
    assert file_lower_bounds(world.layout) == {}


def test_the_spool_bound_is_the_end_of_the_oldest_spool_day(world: Any) -> None:
    assert file_lower_bounds(world.layout) == {}
    world.write_spool("fortigate", date(2026, 10, 14), [(time(9), b"<1>x")])
    world.write_spool("core-r1", date(2026, 10, 13), [(time(9), b"<1>x")])
    audit = world.layout.audit_spool(date(2026, 10, 1))
    audit.parent.mkdir(parents=True)
    audit.write_text("{}\n", encoding="utf-8")  # the audit spool is not a log source day
    assert file_lower_bounds(world.layout) == {"spool": site_day_end(date(2026, 10, 13))}


@pytest.mark.parametrize("planted", [date(2025, 12, 31), date(2025, 1, 1), date(1, 1, 1)])
def test_a_planted_day_before_the_epoch_does_not_hide_the_oldest_real_day(
    sealed: Any, planted: date
) -> None:
    """``evaluate`` drops bounds before PRODUCT_EPOCH; the bound must then be the oldest day
    at or after it, not nothing (a junk manifest would otherwise reopen the trial)."""
    path = sealed.layout.manifest(planted)
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    assert trusted_time.vault_days(sealed.layout)[0] == planted
    assert file_lower_bounds(sealed.layout) == {"vault": site_day_end(DAYS[0])}
    # The same for a chain anchor from before the epoch and for the spool.
    anchor = sealed.layout.chain_anchor
    anchor.parent.mkdir(parents=True, exist_ok=True)
    _writable(anchor).write_text(json.dumps({"day": planted.isoformat()}), encoding="utf-8")
    sealed.write_spool("core-r1", date(2026, 10, 13), [(time(9), b"<1>x")])
    sealed.write_spool("planted", planted, [(time(9), b"<1>x")])
    assert file_lower_bounds(sealed.layout) == {
        "vault": site_day_end(DAYS[0]),
        "spool": site_day_end(date(2026, 10, 13)),
    }


def test_only_planted_days_before_the_epoch_give_no_bound(world: Any) -> None:
    path = world.layout.manifest(date(2025, 12, 31))
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    world.write_spool("planted", date(2025, 12, 31), [(time(9), b"<1>x")])
    assert file_lower_bounds(world.layout) == {}
    # The first day that ends after the epoch counts (2026-01-01 ends at 21:00 UTC).
    world.write_spool("core-r1", date(2026, 1, 1), [(time(9), b"<1>x")])
    assert file_lower_bounds(world.layout) == {"spool": site_day_end(date(2026, 1, 1))}


def test_a_spool_that_cannot_be_listed_is_warned_about_once(
    world: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The worker must see the same spool bound as the API (5.2). If a missing folder right
    hides it, the process says so in its log instead of silently deciding differently."""
    world.write_spool("core-r1", date(2026, 10, 13), [(time(9), b"<1>x")])
    assert file_lower_bounds(world.layout) == {"spool": site_day_end(date(2026, 10, 13))}

    def denied(self: Layout) -> list[date]:
        raise PermissionError(13, "Access is denied", str(self.spool))

    monkeypatch.setattr(Layout, "spool_days", denied)
    with caplog.at_level("WARNING", logger="arcilogger.license"):
        assert file_lower_bounds(world.layout) == {}
        assert file_lower_bounds(world.layout) == {}
    warnings = [r for r in caplog.records if r.name == "arcilogger.license"]
    assert len(warnings) == 1
    assert warnings[0].levelname == "WARNING"
    assert str(world.layout.spool) in warnings[0].getMessage()
    assert "spool lower bound" in warnings[0].getMessage()


def test_file_lower_bounds_of_an_empty_data_folder(tmp_path: Path) -> None:
    assert file_lower_bounds(Layout(tmp_path / "nothing")) == {}
