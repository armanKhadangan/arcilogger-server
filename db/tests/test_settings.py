"""Reading and writing the settings table."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arcilogger_core.canonical import canonical_json
from arcilogger_db.models import Setting, User
from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    MIGRATION_AUTHOR,
    SEED_AUTHOR,
    SYSTEM_AUTHOR,
    get_setting,
    get_settings,
    set_setting,
    validate_setting,
)


def test_get_setting_falls_back_to_default_and_returns_copies(db_session: Session) -> None:
    db_session.execute(delete(Setting).where(Setting.key == "lease.server_seconds"))
    value = get_setting(db_session, "lease.server_seconds")
    assert value == {}
    value["dhcp999"] = 1  # a caller mutating its copy must not change the defaults
    assert DEFAULT_SETTINGS["lease.server_seconds"] == {}
    assert get_setting(db_session, "lease.server_seconds") == {}
    assert get_setting(db_session, "no.such.key") is None
    assert get_setting(db_session, "no.such.key", 7) == 7


def test_set_setting_upserts_and_records_the_author(db_session: Session) -> None:
    set_setting(db_session, "nat.public_ips", ["203.0.113.7"], updated_by="tolga")
    set_setting(db_session, "custom.flag", True, updated_by="tolga")
    row = db_session.scalars(select(Setting).where(Setting.key == "nat.public_ips")).one()
    db_session.refresh(row)
    assert row.value == ["203.0.113.7"]
    assert row.updated_by == "tolga"
    assert get_setting(db_session, "custom.flag") is True


def test_get_settings_by_prefix_and_keys(db_session: Session) -> None:
    set_setting(db_session, "auth.idle_minutes", 5, updated_by="tolga")
    auth = get_settings(db_session, prefix="auth.")
    assert set(auth) == {k for k in DEFAULT_SETTINGS if k.startswith("auth.")}
    assert auth["auth.idle_minutes"] == 5
    assert auth["auth.absolute_hours"] == 12
    picked = get_settings(db_session, keys=["nat.public_ips", "auth.idle_minutes"])
    assert picked == {"nat.public_ips": [], "auth.idle_minutes": 5}


def test_every_default_is_canonical_json_so_changes_can_be_audited() -> None:
    """Spec 8.5 audits settings changes; the audit chain only takes canonical JSON (no floats)."""
    for key, value in DEFAULT_SETTINGS.items():
        assert canonical_json({"old": value, "new": value}), key


@pytest.mark.parametrize("value", [1.1, {"factor": 0.5}, [float("nan")], 2**80])
def test_set_setting_refuses_values_that_cannot_be_audited(
    db_session: Session, value: object
) -> None:
    with pytest.raises(ValueError, match="cannot be audited exactly"):
        set_setting(db_session, "lease.timeout_percent", value, updated_by="tolga")
    assert get_setting(db_session, "lease.timeout_percent") == 110


@pytest.mark.parametrize("author", [SEED_AUTHOR, SYSTEM_AUTHOR, MIGRATION_AUTHOR])
def test_internal_authors_can_never_be_user_names(db_session: Session, author: str) -> None:
    """The seed recognises its own rows by ``updated_by``; no admin may share that name."""
    assert author.startswith(":")
    db_session.add(User(username=author, display_name="x", role="admin", password_hash="x"))
    with pytest.raises(IntegrityError, match="ck_users_username"):
        db_session.flush()


def test_prefix_is_literal_not_a_like_pattern(db_session: Session) -> None:
    set_setting(db_session, "a_b.x", 1, updated_by="t")
    set_setting(db_session, "axb.y", 2, updated_by="t")
    assert get_settings(db_session, prefix="a_b.") == {"a_b.x": 1}


@pytest.mark.parametrize(
    ("key", "value", "stored"),
    [
        (
            "nat.public_ips",
            ["2001:DB8::1", "203.0.113.7", "203.0.113.7"],
            ["2001:db8::1", "203.0.113.7"],
        ),
        ("nat.wan_transip", " 100.64.12.9 ", "100.64.12.9"),
        ("nat.wan_transip", "", ""),
        ("devices.infrastructure_macs", ["0000.5E00.5332"], ["00:00:5e:00:53:32"]),
        (
            "auth.totp_required_roles",
            ["admin", "hr", "auditor", "admin"],
            ["admin", "hr", "auditor"],
        ),
        ("auth.idle_minutes", 120, 120),
        ("auth.lockout_attempts", 20, 20),
        ("lease.server_seconds", {"dhcp61": 3600}, {"dhcp61": 3600}),
        ("dhcp.server_vlan_regex", r"^dhcp-(\d+)$", r"^dhcp-(\d+)$"),
        ("lookup.report_timestamp", True, True),
        ("arp.ignore_after_release_seconds", 0, 0),
    ],
)
def test_valid_setting_values_are_normalised(key: str, value: object, stored: object) -> None:
    assert validate_setting(key, value) == stored


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("no.such.key", 1, "bilinmeyen ayar"),
        ("lease.server_seconds", {"dhcp61": 5}, "en az 60 saniye"),
        ("lease.server_seconds", {"dhcp61": True}, "en az 60 saniye"),
        ("privacy.notice_version", " ", "boş olamaz"),
        ("nat.public_ips", [7], "IP adresi bekleniyor"),
        ("dhcp.server_vlan_regex", "(", "geçersiz düzenli ifade"),
        ("lookup.max_candidates", 0, "en az 1"),
        ("evidence.keep_days", None, "tam sayı bekleniyor"),
        # Spec 13 protections cannot be switched off through the settings command.
        ("auth.totp_required_roles", [], "admin, auditor rolü için"),
        ("auth.totp_required_roles", ["auditor"], "admin rolü için"),
        ("auth.totp_required_roles", ["admin", "hr"], "auditor rolü için"),
        ("auth.lockout_attempts", 1_000_000, "en çok 20"),
        ("auth.idle_minutes", 525_600, "en çok 120"),
        ("auth.absolute_hours", 25, "en çok 24"),
        ("auth.lockout_minutes", 0, "en az 1"),
        ("auth.password_min_length", 8, "en az 12"),
    ],
)
def test_invalid_setting_values_are_refused(key: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_setting(key, value)


def test_every_shipped_default_passes_its_own_validation() -> None:
    for key, value in DEFAULT_SETTINGS.items():
        assert validate_setting(key, value) == value, key


# -- area E: alert settings -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "stored"),
    [
        (
            "alerts.email_recipients",
            [" Bilgi-Islem@Ornek.EXAMPLE ", "nobet@example.com", "Bilgi-Islem@ornek.example"],
            ["Bilgi-Islem@ornek.example", "nobet@example.com"],
        ),
        ("alerts.email_recipients", [], []),
        ("alerts.email_min_severity", "critical", "critical"),
        ("alerts.web_base_url", " https://arcilogger.internal/ ", "https://arcilogger.internal"),
        ("alerts.web_base_url", "https://172.20.20.15:8443", "https://172.20.20.15:8443"),
        ("alerts.web_base_url", "", ""),
        ("alerts.email_digest_seconds", 0, 0),
        ("alerts.r15_startup_grace_minutes", 0, 0),
        ("alerts.disk_used_percent", 90, 90),
        ("alerts.email_resolved", False, False),
    ],
)
def test_valid_alert_settings(key: str, value: object, stored: object) -> None:
    assert validate_setting(key, value) == stored


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("alerts.email_recipients", ["not an address"], "geçersiz e-posta adresi"),
        ("alerts.email_recipients", ["Ayşe <ayse@example.com>"], "geçersiz e-posta adresi"),
        ("alerts.email_recipients", [5], "geçersiz e-posta adresi"),
        ("alerts.email_recipients", [f"u{n}@example.com" for n in range(21)], "en çok 20"),
        ("alerts.email_recipients", "a@example.com", "liste bekleniyor"),
        ("alerts.email_min_severity", "urgent", "değerlerinden biri"),
        ("alerts.web_base_url", "http://172.20.20.15", "https://"),
        ("alerts.web_base_url", "https://evil.example/<script>", "https://"),
        ("alerts.email_max_per_hour", 0, "en az 1"),
        ("alerts.email_digest_seconds", 7200, "en çok 3600"),
        ("alerts.disk_used_percent", 100, "en çok 99"),
        ("alerts.disk_used_percent", 10, "en az 50"),
        ("alerts.seal_overdue_hour", 0, "en az 1"),
        ("alerts.parse_failure_percent", 0, "en az 1"),
        ("alerts.job_failures", 0, "en az 1"),
        ("alerts.r15_never_seen_minutes", 0, "en az 1"),
        ("alerts.backup_stale_hours", 0, "en az 1"),
    ],
)
def test_invalid_alert_settings_are_refused(key: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_setting(key, value)
