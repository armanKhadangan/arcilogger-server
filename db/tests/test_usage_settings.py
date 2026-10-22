"""Area U: validation of the ``usage.*`` settings (an administrator's change is normalised or
refused with a Turkish message)."""

from __future__ import annotations

import pytest

from arcilogger_db.settings import DEFAULT_SETTINGS, validate_setting


@pytest.mark.parametrize(
    ("key", "value", "stored"),
    [
        ("usage.background_domains", [" *.Example.COM. ", "*.example.com"], ["*.example.com"]),
        ("usage.background_appcats", ["Update", " Update "], ["Update"]),
        ("usage.background_ports", [853, 53, 53], [53, 853]),
        (
            "usage.work_hours",
            {"days": [5, 1, 1], "start": "08:30", "end": "17:00"},
            {"days": [1, 5], "start": "08:30", "end": "17:00"},
        ),
        ("usage.holidays", ["10-29", "2027-03-20", "02-29"], ["02-29", "10-29", "2027-03-20"]),
        (
            "usage.psl_file",
            "  C:\\ProgramData\\ARCILOGGER\\psl.dat ",
            "C:\\ProgramData\\ARCILOGGER\\psl.dat",
        ),
        ("usage.active_bytes_threshold", 4096, 4096),
        ("usage.auto_rebuild_days", 0, 0),
        ("usage.pseudonymize_anonymous_role", True, True),
    ],
)
def test_valid_usage_settings(key: str, value: object, stored: object) -> None:
    assert validate_setting(key, value) == stored


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("usage.background_domains", ["*.*"], "geçersiz alan adı kalıbı"),
        ("usage.background_domains", ["bad host.com"], "geçersiz alan adı kalıbı"),
        ("usage.background_appcats", [""], "geçersiz uygulama kategorisi"),
        ("usage.background_ports", [0], "1-65535"),
        ("usage.background_ports", [True], "1-65535"),
        ("usage.work_hours", {"days": [1], "start": "18:00", "end": "08:00"}, "gece vardiyası"),
        ("usage.work_hours", {"days": [8], "start": "08:00", "end": "18:00"}, r"1 \(Pazartesi\)"),
        ("usage.work_hours", {"days": [1], "start": "8:00", "end": "18:00"}, "SS:DD"),
        ("usage.work_hours", {"days": [1]}, "days, start ve end"),
        ("usage.holidays", ["2026-02-30"], "geçersiz tarih"),
        ("usage.holidays", ["29.10"], "geçersiz tarih"),
        ("usage.active_bytes_threshold", 0, "en az 1"),
        ("usage.auto_rebuild_days", 63, "en çok 62"),
        ("usage.retention_days", 0, "en az 1"),
        ("usage.change_min_percent", 0, "en az 1"),
    ],
)
def test_invalid_usage_settings_are_refused(key: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_setting(key, value)


def test_only_the_fixed_date_national_holidays_are_shipped() -> None:
    """Religious holidays move every year: never invented, the administrator enters them."""
    assert DEFAULT_SETTINGS["usage.holidays"] == [
        "01-01",
        "04-23",
        "05-01",
        "05-19",
        "07-15",
        "08-30",
        "10-29",
    ]
