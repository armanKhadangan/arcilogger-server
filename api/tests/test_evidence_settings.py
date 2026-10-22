"""The evidence package limits are known settings with the defaults the API applies."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.evidence_limits import DEFAULTS, Limits
from arcilogger_core.canonical import canonical_json
from arcilogger_db.models import Setting
from arcilogger_db.settings import DEFAULT_SETTINGS, SEED_AUTHOR


def test_every_limit_is_a_known_setting_with_the_same_default() -> None:
    for key, (default, smallest) in DEFAULTS.items():
        assert key in DEFAULT_SETTINGS, key
        value = DEFAULT_SETTINGS[key]
        # Canonical JSON (no floats), so a change can be audited as {"old", "new"}.
        assert type(value) is int, key
        assert canonical_json(value)
        assert value == default, key
        assert value >= smallest, key


def test_the_limits_are_seeded_and_loaded(app_session_factory: sessionmaker[Session]) -> None:
    with app_session_factory() as session:
        rows = session.execute(
            select(Setting.key, Setting.value).where(
                Setting.key.in_(sorted(DEFAULTS)), Setting.updated_by == SEED_AUTHOR
            )
        )
        seeded: dict[str, object] = dict(rows.all())
        assert seeded == {key: DEFAULT_SETTINGS[key] for key in DEFAULTS}
        limits = Limits.load(session)
    assert limits.max_unfinished_per_user == 2
    assert limits.max_unfinished_total == 5
    assert limits.max_requests_per_hour == 10
    assert limits.min_free_percent == 10
    assert limits.min_free_bytes == 20 * 1024**3
    assert limits.exports_max_bytes == 100 * 1024**3
