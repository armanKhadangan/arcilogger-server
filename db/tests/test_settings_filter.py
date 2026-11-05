"""The content-filter settings of Uyum Durumu card 7 (Wave 4 contract 2.4): defaults, bounds,
texts and the refusal of a day outside 1-7."""

from __future__ import annotations

import pytest

from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    INTEGER_RANGES,
    SETTING_ERRORS,
    SettingValueError,
    validate_setting,
)
from arcilogger_db.settings_meta import group_settings, setting_meta

KEYS = ("alerts.filter_days", "alerts.filter_skip_holidays", "alerts.filter_quiet_hours")


def test_filter_settings_defaults_and_bounds() -> None:
    # Owner question Q10: every day of the week, the listed holidays not, 24 hours.
    assert DEFAULT_SETTINGS["alerts.filter_days"] == [1, 2, 3, 4, 5, 6, 7]
    assert DEFAULT_SETTINGS["alerts.filter_skip_holidays"] is True
    assert DEFAULT_SETTINGS["alerts.filter_quiet_hours"] == 24
    assert INTEGER_RANGES["alerts.filter_quiet_hours"] == (6, 168)

    # In the existing group "Uyarılar ve e-posta", after the other alert settings.
    alerts = [meta.key for meta in group_settings("alerts")]
    assert alerts[-3:] == list(KEYS)
    days, skip, hours = (setting_meta(key) for key in KEYS)
    assert (days.type, skip.type, hours.type) == ("integer_list", "boolean", "integer")
    assert (hours.minimum, hours.maximum) == (6, 168)
    assert (days.minimum, days.maximum) == (None, None)
    assert not any(meta.reauth or meta.locked for meta in (days, skip, hours))
    assert (days.label_tr, days.label_en) == (
        "İçerik filtresi kaydı beklenen günler",
        "Days on which content filter records are expected",
    )
    assert (skip.label_tr, skip.label_en) == (
        "Resmî tatillerde içerik filtresi kaydı bekleme",
        "Expect no content filter records on public holidays",
    )
    assert (hours.label_tr, hours.label_en) == (
        "İçerik filtresi kaydı olmadan geçebilecek süre (saat)",
        "Hours allowed without content filter records",
    )
    assert days.help_tr == (
        "ISO hafta günleri (1 = Pazartesi, 7 = Pazar). Bu günlerde aşağıdaki süre boyunca web ya "
        "da DNS filtresi kaydı gelmezse Uyum Durumu'nun 7. kartı kırmızı olur ve R20-filter "
        "uyarısı açılır. Varsayılan: haftanın her günü."
    )
    assert days.help_en == (
        "ISO weekdays (1 = Monday, 7 = Sunday). If no web or DNS filter record arrives for the "
        "time below on these days, card 7 of the Uyum Durumu page turns red and the R20-filter "
        "alert opens. Default: every day of the week."
    )
    assert skip.help_tr == (
        "Açıkken (varsayılan) Kullanım analizi'nin tatil listesindeki (usage.holidays) günler "
        "beklenmez. Kapalıyken resmî tatiller de beklenen gün sayılır."
    )
    assert skip.help_en == (
        "On (the default): the days in the holiday list of usage analytics (usage.holidays) are "
        "not expected. Off: public holidays count as expected days."
    )
    assert hours.help_tr == "Beklenen günlerde kayıtsız geçebilecek en uzun süre."
    assert hours.help_en == "The longest time without a record on expected days."

    # The days are stored sorted without duplicates, like usage.background_ports; no day at all
    # is allowed (no content-filter record is expected then).
    assert validate_setting("alerts.filter_days", [7, 1, 3, 1, 7]) == [1, 3, 7]
    assert validate_setting("alerts.filter_days", []) == []
    assert validate_setting("alerts.filter_skip_holidays", False) is False
    assert validate_setting("alerts.filter_quiet_hours", 6) == 6
    assert validate_setting("alerts.filter_quiet_hours", 168) == 168
    for refused in (5, 169, 0):
        with pytest.raises(SettingValueError) as caught:
            validate_setting("alerts.filter_quiet_hours", refused)
        assert caught.value.code == "setting_out_of_range"
        assert caught.value.message_tr == "6 ile 168 arasında olmalı."
        assert caught.value.message_en == "Must be between 6 and 168."
    for value in ("24", True):
        with pytest.raises(SettingValueError) as caught:
            validate_setting("alerts.filter_quiet_hours", value)
        assert caught.value.code == "setting_type"
    with pytest.raises(SettingValueError) as caught:
        validate_setting("alerts.filter_skip_holidays", 1)
    assert caught.value.code == "setting_type"


@pytest.mark.parametrize("value", [[0], [8], [1, 2, 9], [-1], [1, "2"], [True], [None], [[1]]])
def test_filter_days_refuse_values_outside_1_to_7(value: list[object]) -> None:
    with pytest.raises(SettingValueError) as caught:
        validate_setting("alerts.filter_days", value)
    refusal = caught.value
    assert (refusal.key, refusal.code) == ("alerts.filter_days", "setting_work_hours_days")
    assert (refusal.message_tr, refusal.message_en) == SETTING_ERRORS["setting_work_hours_days"]
    assert "1 (Pazartesi) - 7 (Pazar)" in str(refusal)
    # Not a list at all: the type refusal every list setting gives.
    with pytest.raises(SettingValueError) as caught:
        validate_setting("alerts.filter_days", "1-7")
    assert caught.value.code == "setting_type"
