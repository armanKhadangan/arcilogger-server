"""The licence state (licensing contract section 3; tests A-T10 rules, A-T11 to A-T14).

Every case runs the real :func:`evaluate` on controlled inputs; licences are signed with the
deterministic test key.
"""

from __future__ import annotations

import pickle
import sys
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from arcilogger_core.license import keys, revoked
from arcilogger_core.license.errors import HTTP_STATUS, MESSAGES, LicenseError
from arcilogger_core.license.modules import CORE_MODULE, MODULES, module, module_keys
from arcilogger_core.license.state import (
    PRODUCT_EPOCH,
    SOURCE_ACTIVE_DAYS,
    STATUS_FILE_SCHEMA,
    TRIAL_CLOCK_TOLERANCE,
    TRIAL_DAYS,
    UNLICENSED_USERS,
    Installation,
    LicenseInputs,
    LicenseStatus,
    LimitUse,
    check_applicable,
    evaluate,
    status_override,
)
from arcilogger_core.license.testing import (
    all_modules_status,
    base_only_status,
    make_license,
    override_status,
    tamper,
    trusted_test_keys,
)
from arcilogger_core.license.trusted_time import TrustedTime, site_day_start
from arcilogger_core.timeutil import SITE_TZ

pytestmark = pytest.mark.license_live

SERVER = "XW68-7K1Z-5FGV-AGT8"
OTHER = "0000-1111-2222-3333"
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
RELEASE = date(2026, 10, 20)
PAID = ("usage_analytics", "usage_reports", "bulk_import")
ALL = frozenset(module_keys())


@pytest.fixture(autouse=True)
def _test_key() -> Iterator[None]:
    with trusted_test_keys():
        yield


def site(day: date, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm, ss), tzinfo=SITE_TZ).astimezone(UTC)


def trusted_at(
    system: datetime = NOW,
    *,
    tsa: datetime | None = None,
    vault_day_end: datetime | None = None,
    release: date = date(2026, 1, 1),
) -> TrustedTime:
    floor = site_day_start(release)
    now = max(value for value in (system, tsa, vault_day_end, floor) if value is not None)
    return TrustedTime(
        now=now,
        system=system,
        tsa=tsa,
        tsa_day=None,
        vault_day_end=vault_day_end,
        release_floor=floor,
        license_issued=None,
    )


def inputs(
    *,
    stored: bytes | None = None,
    server_id: str | None = SERVER,
    started: datetime = NOW,
    bounds: Mapping[str, datetime] | None = None,
    trusted: TrustedTime | None = None,
    users: int = 2,
    sources: int = 5,
    unconfigured: int = 1,
    release: date = RELEASE,
) -> LicenseInputs:
    return LicenseInputs(
        installation=Installation("3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4", started),
        stored=stored,
        server_id=server_id,
        server_id_error=None if server_id is not None else "FingerprintError: no MachineGuid",
        trusted=trusted or trusted_at(),
        lower_bounds=dict(bounds or {}),
        users_used=users,
        log_sources_used=sources,
        log_sources_unconfigured=unconfigured,
        version="0.1.0",
        release_date=release,
    )


def licence(**kwargs: Any) -> bytes:
    kwargs.setdefault("server_ids", [SERVER])
    kwargs.setdefault("modules", ("core_5651", *PAID))
    return make_license(**kwargs)


def state_of(**kwargs: Any) -> LicenseStatus:
    return evaluate(inputs(**kwargs))


# -- A-T11 states, precedence, modules, limits ----------------------------------------------------


def test_a_fresh_installation_is_in_trial_with_everything_on() -> None:
    status = state_of()
    assert status.state == "trial"
    assert status.problem is None
    assert status.document is None
    assert status.enabled == ALL
    assert all(status.module_enabled(key) for key in ALL)
    assert status.trial_days_left == TRIAL_DAYS
    assert status.trial_ends_at == NOW + timedelta(days=14)
    assert status.limits["users"] == LimitUse(None, 2)
    assert status.limits["log_sources"] == LimitUse(None, 5, 1)
    assert status.maintenance_until is None
    assert status.valid_until is None
    assert not status.maintenance_expired
    assert not status.maintenance_ends_soon


def test_after_the_trial_only_5651_core_is_on() -> None:
    status = state_of(started=NOW - timedelta(days=20))
    assert status.state == "trial_expired"
    assert status.problem is None
    assert status.enabled == {CORE_MODULE}
    assert status.trial_days_left == 0
    assert status.limits["users"].max == UNLICENSED_USERS == 3
    assert status.limits["log_sources"].max is None


def test_a_valid_licence_enables_its_modules() -> None:
    status = state_of(stored=licence(log_sources=10, users=25))
    assert status.state == "licensed"
    assert status.problem is None
    assert status.document is not None
    assert status.enabled == ALL
    assert status.limits["users"] == LimitUse(25, 2)
    assert status.limits["log_sources"] == LimitUse(10, 5, 1)
    assert status.module_licensed("usage_analytics")
    assert status.maintenance_until == date(2099, 12, 31)
    assert not status.maintenance_expired


@pytest.mark.parametrize(
    ("listed", "enabled"),
    [
        (("core_5651",), {CORE_MODULE}),
        (("core_5651", "usage_analytics"), {CORE_MODULE, "usage_analytics"}),
        (("core_5651", "usage_reports"), {CORE_MODULE}),  # requires usage_analytics
        (("core_5651", "usage_reports", "usage_analytics"), {CORE_MODULE, *PAID[:2]}),
        (("core_5651", "bulk_import"), {CORE_MODULE, "bulk_import"}),
        (("core_5651", "rule_engine", "usage_analytics"), {CORE_MODULE, "usage_analytics"}),
    ],
)
def test_enabled_modules_follow_the_licence_and_requires(
    listed: tuple[str, ...], enabled: set[str]
) -> None:
    status = state_of(stored=licence(modules=listed))
    assert status.state == "licensed"
    assert status.enabled == enabled
    for key in ALL:
        assert status.module_enabled(key) is (key in enabled)
        assert status.module_licensed(key) is (key in listed)


def test_unknown_module_keys_are_ignored_and_not_answerable() -> None:
    status = state_of(stored=licence(modules=("core_5651", "rule_engine")))
    assert "rule_engine" not in status.enabled
    assert status.document is not None
    assert "rule_engine" in status.document.payload.modules
    with pytest.raises(ValueError, match="rule_engine"):
        status.module_enabled("rule_engine")
    with pytest.raises(ValueError, match="rule_engine"):
        status.module_licensed("rule_engine")


def _cases() -> dict[str, tuple[dict[str, Any], str, str | None]]:
    good = licence(users=25, log_sources=10)
    return {
        "trial": ({}, "trial", None),
        "trial_expired": ({"started": NOW - timedelta(days=15)}, "trial_expired", None),
        "inconsistent": (
            {"started": NOW + timedelta(days=3)},
            "trial_expired",
            "trial_clock_inconsistent",
        ),
        "licensed": ({"stored": good}, "licensed", None),
        "tampered": (
            {"stored": tamper(good, "limits.users", 9999)},
            "invalid",
            "license_signature_invalid",
        ),
        "malformed": ({"stored": b"{}"}, "invalid", "license_malformed"),
        "too large": ({"stored": good + b" " * 70_000}, "invalid", "license_too_large"),
        "other server": (
            {"stored": good, "server_id": OTHER},
            "wrong_server",
            "license_other_server",
        ),
        "no server id": (
            {"stored": good, "server_id": None},
            "wrong_server",
            "license_server_id_unavailable",
        ),
        "expired": (
            {"stored": licence(valid_until=date(2026, 10, 1), users=25, log_sources=10)},
            "expired",
            "license_expired",
        ),
        "not covered": (
            {"stored": licence(maintenance_until=date(2026, 10, 19), users=25, log_sources=10)},
            "version_not_covered",
            "license_version_not_covered",
        ),
    }


CASES = _cases()


@pytest.mark.parametrize("name", list(CASES))
def test_every_state_keeps_5651_core_and_has_its_limits(name: str) -> None:
    kwargs, state, problem = CASES[name]
    status = state_of(**kwargs)
    assert (status.state, status.problem) == (state, problem)
    assert CORE_MODULE in status.enabled
    assert status.module_enabled(CORE_MODULE)
    users, sources = status.limits["users"].max, status.limits["log_sources"].max
    if state == "trial":
        assert status.enabled == ALL
        assert (users, sources) == (None, None)
    elif state == "licensed":
        assert status.enabled == ALL
        assert (users, sources) == (25, 10)
    else:
        assert status.enabled == {CORE_MODULE}
        if state in ("trial_expired", "invalid"):
            assert (users, sources) == (UNLICENSED_USERS, None)
            assert status.document is None
        else:  # signature verified: the document's limits stay
            assert (users, sources) == (25, 10)
            assert status.document is not None
    assert status.limits["log_sources"].unconfigured == 1
    assert status.limits["users"].unconfigured == 0


def test_precedence_of_the_licence_problems() -> None:
    everything_wrong = licence(valid_until=date(2026, 10, 1), maintenance_until=date(2026, 9, 1))
    assert state_of(stored=everything_wrong, server_id=OTHER).problem == "license_other_server"
    assert state_of(stored=everything_wrong, server_id=None).problem == (
        "license_server_id_unavailable"
    )
    assert state_of(stored=everything_wrong).problem == "license_expired"
    tampered = tamper(everything_wrong, "licensee", "X")
    assert state_of(stored=tampered, server_id=OTHER).problem == "license_signature_invalid"


def test_an_unusable_licence_never_falls_back_to_the_trial() -> None:
    status = state_of(stored=tamper(licence(), "licensee", "X"), started=NOW)
    assert status.state == "invalid"
    assert status.enabled == {CORE_MODULE}
    assert status.trial_days_left == TRIAL_DAYS  # computed, but not used: no fall-back
    status = state_of(stored=licence(), server_id=OTHER, started=NOW)
    assert status.state == "wrong_server"
    assert status.enabled == {CORE_MODULE}


def test_a_removed_key_or_a_revoked_id_makes_the_stored_licence_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = licence()
    assert state_of(stored=stored).state == "licensed"
    monkeypatch.setattr(keys, "TRUSTED_KEYS", {})
    assert (state_of(stored=stored).state, state_of(stored=stored).problem) == (
        "invalid",
        "license_unknown_key",
    )
    monkeypatch.undo()
    with trusted_test_keys():
        monkeypatch.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset({"ARCI-L-20261001-TEST0001"}))
        status = state_of(stored=stored)
    assert (status.state, status.problem) == ("invalid", "license_revoked")
    assert status.limits["users"].max == UNLICENSED_USERS


def test_limit_use() -> None:
    assert not LimitUse(None, 10_000).exceeded
    assert not LimitUse(None, 10_000).full
    assert LimitUse(3, 3).full
    assert not LimitUse(3, 3).exceeded
    assert LimitUse(3, 4).exceeded
    assert LimitUse(3, 4).full
    assert not LimitUse(3, 2).full
    status = state_of(stored=licence(users=3, log_sources=4), users=3, sources=5, unconfigured=2)
    assert status.limits["users"].full
    assert not status.limits["users"].exceeded
    assert status.limits["log_sources"].exceeded
    assert status.limits["log_sources"].unconfigured == 2


def test_the_licence_issue_time_raises_the_trusted_time() -> None:
    issued = NOW + timedelta(days=2)
    status = state_of(stored=licence(issued_at=issued))
    assert status.trusted.now == issued
    assert status.trusted.source == "license_issued"
    assert status.trusted.license_issued == issued
    assert state_of(stored=tamper(licence(issued_at=issued), "licensee", "X")).trusted.now == NOW


def test_status_file() -> None:
    status = state_of(stored=licence(modules=("core_5651", "usage_analytics")))
    written = datetime(2026, 10, 20, 10, 5, 0, 123, tzinfo=UTC)
    assert status.to_status_file(written_at=written) == {
        "schema": STATUS_FILE_SCHEMA,
        "written_at": "2026-10-20T10:05:00Z",
        "status": "licensed",
        "license_id": "ARCI-L-20261001-TEST0001",
        "licensee": "Örnek A.Ş.",
        "server_id": SERVER,
        "maintenance_until": "2099-12-31",
        "valid_until": None,
        "modules": ["core_5651", "usage_analytics"],
        "version": "0.1.0",
        "release_date": "2026-10-20",
    }
    trial = state_of().to_status_file(written_at=written)
    assert trial["status"] == "trial"
    assert trial["modules"] == list(module_keys())
    assert (trial["license_id"], trial["licensee"], trial["maintenance_until"]) == (None,) * 3
    assert STATUS_FILE_SCHEMA == "arcilogger.license-status/1"


def test_published_constants() -> None:
    assert TRIAL_DAYS == 14
    assert UNLICENSED_USERS == 3
    assert SOURCE_ACTIVE_DAYS == 30
    assert timedelta(minutes=10) == TRIAL_CLOCK_TOLERANCE
    assert datetime(2026, 1, 1, tzinfo=UTC) == PRODUCT_EPOCH


# -- A-T10 rules and A-T12 trial reset and clock attempts ------------------------------------------


def test_a_new_row_with_old_vault_days_is_not_a_new_trial() -> None:
    status = state_of(started=NOW, bounds={"vault": NOW - timedelta(days=20)})
    assert status.state == "trial_expired"
    assert status.trial_started_at == NOW - timedelta(days=20)
    assert status.trial_days_left == 0


@pytest.mark.parametrize("name", ["audit", "trial_start_audit", "license_events", "spool"])
def test_a_new_row_with_old_database_or_spool_traces_is_not_a_new_trial(name: str) -> None:
    status = state_of(started=NOW, bounds={name: NOW - timedelta(days=20)})
    assert status.state == "trial_expired"
    assert status.problem is None


def test_trial_boundaries() -> None:
    start = NOW
    assert state_of(started=start).trial_days_left == 14
    almost = state_of(started=start - timedelta(days=13, hours=23))
    assert (almost.state, almost.trial_days_left) == ("trial", 1)
    one_day = state_of(started=start - timedelta(days=13))
    assert (one_day.state, one_day.trial_days_left) == ("trial", 1)
    second = state_of(started=start - timedelta(days=12, hours=23))
    assert second.trial_days_left == 2
    over = state_of(started=start - timedelta(days=14))
    assert (over.state, over.trial_days_left) == ("trial_expired", 0)
    last_second = state_of(started=start - timedelta(days=14) + timedelta(microseconds=1))
    assert (last_second.state, last_second.trial_days_left) == ("trial", 1)


def test_a_clock_set_back_inside_the_trial_does_not_extend_it() -> None:
    start = NOW - timedelta(days=20)
    system = start + timedelta(days=5)  # the clock was set back into the trial
    tsa = start + timedelta(days=15)  # but the last time stamp is after its end
    status = state_of(started=start, trusted=trusted_at(system, tsa=tsa))
    assert status.state == "trial_expired"
    status = state_of(started=start, trusted=trusted_at(system, vault_day_end=tsa))
    assert status.state == "trial_expired"
    assert state_of(started=start, trusted=trusted_at(system)).state == "trial"


def test_a_trial_started_with_the_clock_forward_is_unavailable() -> None:
    status = state_of(started=NOW + timedelta(days=3 * 365))
    assert status.state == "trial_expired"
    assert status.problem == "trial_clock_inconsistent"
    assert status.trial_inconsistent
    assert status.trial_days_left == 0
    assert status.enabled == {CORE_MODULE}
    assert status.limits["users"].max == UNLICENSED_USERS


def test_a_row_a_few_minutes_ahead_is_tolerated() -> None:
    status = state_of(started=NOW + timedelta(minutes=9))
    assert status.state == "trial"
    assert not status.trial_inconsistent
    assert status.trial_days_left == 14
    assert state_of(started=NOW + TRIAL_CLOCK_TOLERANCE).state == "trial"
    later = state_of(started=NOW + TRIAL_CLOCK_TOLERANCE + timedelta(seconds=1))
    assert later.problem == "trial_clock_inconsistent"


@pytest.mark.parametrize("name", ["audit", "trial_start_audit", "license_events"])
def test_a_database_trace_after_the_trusted_time_is_inconsistent(name: str) -> None:
    status = state_of(bounds={name: NOW + timedelta(hours=1)})
    assert status.state == "trial_expired"
    assert status.problem == "trial_clock_inconsistent"
    assert state_of(bounds={name: NOW + timedelta(minutes=9)}).state == "trial"


@pytest.mark.parametrize("name", ["vault", "spool"])
def test_day_name_bounds_are_clipped_to_the_trusted_time(name: str) -> None:
    # Today's spool day ends tonight: never a clock inconsistency, never a later start.
    status = state_of(started=NOW - timedelta(days=2), bounds={name: NOW + timedelta(hours=15)})
    assert status.state == "trial"
    assert not status.trial_inconsistent
    assert status.trial_started_at == NOW - timedelta(days=2)
    clipped = state_of(started=NOW + timedelta(minutes=5), bounds={name: NOW + timedelta(days=9)})
    assert clipped.trial_started_at == NOW


def test_bounds_before_the_product_epoch_are_ignored() -> None:
    old = PRODUCT_EPOCH - timedelta(seconds=1)
    for name in ("vault", "spool", "audit", "trial_start_audit", "license_events"):
        status = state_of(bounds={name: old})
        assert status.state == "trial", name
        assert status.trial_started_at == NOW
    assert state_of(bounds={"audit": PRODUCT_EPOCH}).state == "trial_expired"


def test_the_earliest_bound_wins() -> None:
    bounds = {
        "vault": NOW - timedelta(days=3),
        "spool": NOW - timedelta(days=5),
        "audit": NOW - timedelta(days=4),
    }
    status = state_of(bounds=bounds)
    assert status.trial_started_at == NOW - timedelta(days=5)
    assert status.trial_days_left == 9


def test_an_unknown_bound_name_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="lower bound"):
        state_of(bounds={"vault_genTime": NOW})


# -- A-T13 valid_until and maintenance boundaries in site time -------------------------------------


def test_valid_until_ends_at_midnight_site_time() -> None:
    stored = licence(valid_until=date(2027, 1, 31))
    last_second = trusted_at(site(date(2027, 1, 31), 23, 59, 59))
    assert state_of(stored=stored, trusted=last_second).state == "licensed"
    midnight = trusted_at(site(date(2027, 2, 1)))
    status = state_of(stored=stored, trusted=midnight)
    assert (status.state, status.problem) == ("expired", "license_expired")
    assert status.valid_until == date(2027, 1, 31)
    # 23:30 UTC on 31.01 is already 01.02 in Istanbul.
    assert state_of(
        stored=stored, trusted=trusted_at(datetime(2027, 1, 31, 21, tzinfo=UTC))
    ).state == ("expired")


def test_valid_days_left_counts_site_days_from_the_trusted_time() -> None:
    stored = licence(valid_until=date(2027, 1, 31))
    early = state_of(stored=stored, trusted=trusted_at(site(date(2027, 1, 17), 12)))
    assert (early.state, early.valid_days_left) == ("licensed", 14)
    last = state_of(stored=stored, trusted=trusted_at(site(date(2027, 1, 31), 23, 59, 59)))
    assert (last.state, last.valid_days_left) == ("licensed", 0)
    after = state_of(stored=stored, trusted=trusted_at(site(date(2027, 2, 1))))
    assert (after.state, after.valid_days_left) == ("expired", -1)
    # A clock set back counts nothing back: the newest vault day is the floor.
    behind = trusted_at(site(date(2027, 1, 1)), vault_day_end=site(date(2027, 1, 30)))
    assert state_of(stored=stored, trusted=behind).valid_days_left == 1
    assert state_of(stored=licence()).valid_days_left is None  # no time limit
    assert state_of(stored=tamper(stored, "licensee", "X")).valid_days_left is None  # invalid


def test_the_release_day_against_maintenance() -> None:
    assert state_of(stored=licence(maintenance_until=RELEASE)).state == "licensed"
    status = state_of(stored=licence(maintenance_until=RELEASE - timedelta(days=1)))
    assert (status.state, status.problem) == ("version_not_covered", "license_version_not_covered")
    assert status.enabled == {CORE_MODULE}


def test_maintenance_flags() -> None:
    until = date(2027, 10, 20)
    stored = licence(maintenance_until=until)

    def at(moment: datetime) -> LicenseStatus:
        return state_of(stored=stored, trusted=trusted_at(moment), release=date(2026, 10, 20))

    last = at(site(until, 23, 59, 59))
    assert (last.maintenance_days_left, last.maintenance_expired, last.maintenance_ends_soon) == (
        0,
        False,
        True,
    )
    after = at(site(until + timedelta(days=1)))
    assert (
        after.maintenance_days_left,
        after.maintenance_expired,
        after.maintenance_ends_soon,
    ) == (
        -1,
        True,
        False,
    )
    assert after.state == "licensed"  # information only: nothing locks
    assert at(site(until - timedelta(days=30), 12)).maintenance_ends_soon
    assert not at(site(until - timedelta(days=31), 12)).maintenance_ends_soon
    assert at(site(until - timedelta(days=31), 12)).maintenance_days_left == 31


def test_maintenance_flags_exist_for_every_verified_document() -> None:
    stored = licence(maintenance_until=date(2026, 9, 1))
    for status in (
        state_of(stored=stored),
        state_of(stored=stored, server_id=OTHER),
    ):
        assert status.maintenance_until == date(2026, 9, 1)
        assert status.maintenance_expired
    assert state_of(stored=tamper(stored, "licensee", "X")).maintenance_until is None


# -- A-T14 check_applicable and the messages -------------------------------------------------------


def _document(**kwargs: Any) -> Any:
    from arcilogger_core.license.document import load_license

    return load_license(licence(**kwargs))


def _applicable(document: Any, **kwargs: Any) -> LicenseError:
    kwargs.setdefault("server_id", SERVER)
    kwargs.setdefault("server_id_error", None)
    kwargs.setdefault("trusted", trusted_at())
    kwargs.setdefault("release_date", RELEASE)
    with pytest.raises(LicenseError) as caught:
        check_applicable(document, **kwargs)
    return caught.value


def test_check_applicable_accepts_a_good_licence() -> None:
    check_applicable(
        _document(),
        server_id=SERVER,
        server_id_error=None,
        trusted=trusted_at(),
        release_date=RELEASE,
    )


def test_check_applicable_order_and_codes() -> None:
    bad = _document(valid_until=date(2026, 10, 1), maintenance_until=date(2026, 9, 1))
    assert _applicable(bad, server_id=None, server_id_error="x").code == (
        "license_server_id_unavailable"
    )
    assert _applicable(bad, server_id=OTHER).code == "license_other_server"
    assert _applicable(bad).code == "license_expired"
    assert _applicable(_document(maintenance_until=date(2026, 9, 1))).code == (
        "license_version_not_covered"
    )


def test_check_applicable_messages_in_both_languages() -> None:
    error = _applicable(_document(server_ids=[SERVER, "AAAA-BBBB-CCCC-DDDD"]), server_id=OTHER)
    assert error.message_tr == (
        f"Lisans bu sunucu için düzenlenmemiş (bu sunucu: {OTHER}). Yeni lisans için istek "
        "kodunu gönderin."
    )
    assert error.message_en == (
        f"The licence was not issued for this server (this server: {OTHER}). Send the request "
        "code for a new licence."
    )
    assert error.details == {"server_id": OTHER, "allowed": [SERVER, "AAAA-BBBB-CCCC-DDDD"]}
    assert error.http_status == 409
    assert error.license_id == "ARCI-L-20261001-TEST0001"

    error = _applicable(_document(valid_until=date(2026, 10, 1)))
    assert error.message_tr == "Lisansın geçerlilik süresi 01.10.2026 tarihinde doldu."
    assert error.message_en == "The licence expired on 2026-10-01."
    assert error.details == {"valid_until": "2026-10-01"}

    error = _applicable(_document(maintenance_until=date(2026, 10, 19)))
    assert error.message_tr == (
        "Bu sürüm (0.1.0, yayın tarihi 20.10.2026) lisansın bakım süresinden (19.10.2026 "
        "tarihine kadar) sonra yayımlandı."
    )
    assert error.message_en == (
        "This version (0.1.0, released 2026-10-20) was released after the licence's maintenance "
        "period (until 2026-10-19)."
    )
    assert error.details == {
        "version": "0.1.0",
        "release_date": "2026-10-20",
        "maintenance_until": "2026-10-19",
    }
    error = _applicable(_document(), server_id=None, server_id_error="no MachineGuid")
    assert error.message_tr.startswith("Sunucu kimliği okunamadı")
    assert error.reason == "no MachineGuid"


def test_check_applicable_uses_the_issue_time_as_a_floor() -> None:
    document = _document(valid_until=date(2026, 10, 5), issued_at=site(date(2026, 10, 6), 12))
    early = trusted_at(site(date(2026, 10, 3)))
    assert _applicable(document, trusted=early).code == "license_expired"


# -- errors ---------------------------------------------------------------------------------------


def test_every_published_code_has_both_texts() -> None:
    assert set(MESSAGES) == {
        "license_too_large",
        "license_malformed",
        "license_unknown_key",
        "license_signature_invalid",
        "license_revoked",
        "license_server_id_unavailable",
        "license_other_server",
        "license_expired",
        "license_version_not_covered",
        "license_not_installed",
        "license_unavailable",
        "trial_clock_inconsistent",
        "module_not_licensed",
        "license_limit",
        "request_malformed",
        "request_checksum",
    }
    for tr, en in MESSAGES.values():
        assert tr
        assert en
        assert tr != en
    assert set(HTTP_STATUS) == set(MESSAGES) - {
        "trial_clock_inconsistent",
        "request_malformed",
        "request_checksum",
    }
    assert HTTP_STATUS["license_too_large"] == 413
    assert HTTP_STATUS["module_not_licensed"] == 403
    assert HTTP_STATUS["license_not_installed"] == 404
    assert HTTP_STATUS["license_unavailable"] == 503
    assert HTTP_STATUS["license_limit"] == 409
    assert "arman@khadangan.com" in MESSAGES["trial_clock_inconsistent"][0]
    assert "arman@khadangan.com" in MESSAGES["trial_clock_inconsistent"][1]


def test_messages_never_claim_legal_compliance() -> None:
    text = " ".join(" ".join(pair) for pair in MESSAGES.values()).lower()
    for word in ("yasal olarak uyumlu", "legally compliant", "kopyalanamaz", "tamper-proof"):
        assert word not in text


def test_message_templates_are_filled() -> None:
    usage = module("usage_analytics")
    error = LicenseError(
        "module_not_licensed", {"module_tr": usage.name_tr, "module_en": usage.name_en}
    )
    assert error.message_tr == (
        "Bu özellik lisansınızda yok: Kullanım analizi. Ayrıntılar Ayarlar → Lisans sayfasında."
    )
    assert error.message_en == (
        "This feature is not part of your licence: Usage analytics. See Settings → Licence."
    )
    assert error.http_status == 403
    limit = LicenseError("license_limit", {"used": 3, "max": 3})
    assert limit.message_tr.startswith("Kullanıcı sayısı lisans sınırında (3/3)")
    assert limit.message_en.startswith("The number of users is at the licence limit (3/3)")
    assert limit.params == {"used": "3", "max": "3"}
    missing = LicenseError("license_unknown_key")
    assert "{key_id}" in missing.message_en  # a missing value stays visible
    assert str(missing).startswith("license_unknown_key: ")
    assert LicenseError("trial_clock_inconsistent").http_status == 422
    with pytest.raises(ValueError, match="unknown licence error code"):
        LicenseError("license_bogus")


def test_licence_errors_survive_pickling() -> None:
    error = LicenseError(
        "license_expired",
        {"date": date(2026, 10, 1)},
        details={"valid_until": "2026-10-01"},
        license_id="ARCI-L-20261001-TEST0001",
        key_id="test-key-1",
        reason="r",
    )
    copy = pickle.loads(pickle.dumps(error))  # noqa: S301 - our own object
    assert (copy.code, copy.message_tr, copy.message_en) == (
        error.code,
        error.message_tr,
        error.message_en,
    )
    assert (copy.details, copy.license_id, copy.key_id, copy.reason) == (
        error.details,
        error.license_id,
        error.key_id,
        error.reason,
    )


def test_module_registry() -> None:
    assert [item.key for item in MODULES] == [
        "core_5651",
        "usage_analytics",
        "usage_reports",
        "bulk_import",
    ]
    assert module("core_5651").always
    assert module("core_5651").name_tr == "5651 Temel"
    assert module("usage_analytics").name_tr == "Kullanım analizi"
    assert module("usage_reports").requires == ("usage_analytics",)
    assert module("bulk_import").name_en == "Bulk import (CSV)"
    assert not any(item.always for item in MODULES[1:])
    for item in MODULES:
        assert item.description_tr
        assert item.description_en
        assert all(required in module_keys() for required in item.requires)
    with pytest.raises(KeyError):
        module("rule_engine")


# -- test helpers ---------------------------------------------------------------------------------


def test_helper_statuses() -> None:
    trial = all_modules_status(now=NOW)
    assert trial.state == "trial"
    assert trial.enabled == ALL
    assert trial.limits["users"].max is None
    assert trial.server_id == SERVER
    base = base_only_status(now=NOW)
    assert base.state == "trial_expired"
    assert base.enabled == {CORE_MODULE}
    assert base.limits["users"].max == UNLICENSED_USERS
    assert all_modules_status().state == "trial"


def test_status_override_is_inert_without_the_testing_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert status_override() is None
    status = base_only_status(now=NOW)
    with override_status(status):
        assert status_override() is status
        with override_status(None):
            assert status_override() is None
        assert status_override() is status
        monkeypatch.delitem(sys.modules, "arcilogger_core.license.testing")
        assert status_override() is None
        monkeypatch.undo()
        assert status_override() is status
    assert status_override() is None
