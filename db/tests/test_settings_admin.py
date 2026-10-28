"""Settings changes for the web interface (ADR-0014, contract 2.13): ``SettingValueError`` with a
code and Turkish and English texts for every refusal (the console text unchanged), the
re-authentication and console-only keys, ``change_setting`` with the user and the address."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.egress import CHANGES_KEY, fingerprint
from arcilogger_db.egress import ERROR_CODES as EGRESS_ERROR_CODES
from arcilogger_db.models import ROLES, AuditLog, Setting, User
from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    LOCKED_KEYS,
    REAUTH_KEYS,
    SETTING_ERRORS,
    SettingValueError,
    email_addresses,
    get_setting,
    validate_setting,
)
from arcilogger_db.settings_admin import change_setting, reset_setting

#: (key, value, code, the console's text exactly as before ADR-0014): every refusal of
#: ``validate_setting``.
REFUSALS: list[tuple[str, Any, str, str]] = [
    ("no.such.key", 1, "setting_unknown", "bilinmeyen ayar: no.such.key"),
    (
        "evidence.keep_days",
        None,
        "setting_type",
        "evidence.keep_days: tam sayı bekleniyor (varsayılan 30), verilen: null",
    ),
    (
        "evidence.keep_days",
        True,
        "setting_type",
        "evidence.keep_days: tam sayı bekleniyor (varsayılan 30), verilen: true/false",
    ),
    (
        "usage.background_ports",
        [1.5],
        "setting_not_canonical",
        "setting usage.background_ports: value cannot be audited exactly: floating-point numbers "
        "are not allowed in canonical documents",
    ),
    (
        "lookup.max_session_seconds",
        59,
        "setting_below_minimum",
        "lookup.max_session_seconds: en az 60 olmalı",
    ),
    (
        "lookup.max_candidates",
        0,
        "setting_out_of_range",
        "lookup.max_candidates: en az 1, en çok 10000 olmalı",
    ),
    (
        "auth.idle_minutes",
        525_600,
        "setting_out_of_range",
        "auth.idle_minutes: en az 1, en çok 120 olmalı",
    ),
    (
        "nat.public_ips",
        [7],
        "setting_ip_invalid",
        "nat.public_ips: IP adresi bekleniyor, verilen 7",
    ),
    (
        "nat.public_ips",
        ["999.1.1.1"],
        "setting_ip_invalid",
        "nat.public_ips: geçersiz IP adresi '999.1.1.1'",
    ),
    (
        "nat.wan_transip",
        "not-an-ip",
        "setting_ip_invalid",
        "nat.wan_transip: geçersiz IP adresi 'not-an-ip'",
    ),
    (
        "devices.infrastructure_macs",
        ["00:00:5e:00:53"],
        "setting_mac_invalid",
        "devices.infrastructure_macs: geçersiz MAC adresi '00:00:5e:00:53'",
    ),
    (
        "auth.totp_required_roles",
        ["admin", "auditor", "root"],
        "setting_role_unknown",
        "auth.totp_required_roles: bilinmeyen rol ['root'] (geçerli: admin, auditor, hr, viewer)",
    ),
    (
        "auth.totp_required_roles",
        ["auditor"],
        "setting_totp_required",
        "auth.totp_required_roles: admin rolü için iki adımlı doğrulama (TOTP) zorunludur "
        "(şartname 13); listeye rol eklenebilir, bu roller çıkarılamaz",
    ),
    (
        "dhcp.server_vlan_regex",
        "(",
        "setting_regex_invalid",
        "dhcp.server_vlan_regex: geçersiz düzenli ifade: missing ), unterminated subpattern at "
        "position 0",
    ),
    (
        "dhcp.server_vlan_regex",
        "^dhcp$",
        "setting_regex_group",
        "dhcp.server_vlan_regex: ilk grup VLAN numarasını yakalamalı, örn. ^dhcp(\\d+)$",
    ),
    (
        "lease.server_seconds",
        {"dhcp61": 5},
        "setting_lease_seconds",
        "lease.server_seconds: 'dhcp61' için en az 60 saniye gerekir",
    ),
    ("privacy.notice_version", " ", "setting_empty", "privacy.notice_version: boş olamaz"),
    (
        "alerts.email_recipients",
        ["not an address"],
        "setting_email_invalid",
        "alerts.email_recipients: geçersiz e-posta adresi 'not an address'",
    ),
    (
        "alerts.email_recipients",
        [f"u{n}@ornek.example" for n in range(21)],
        "setting_too_many",
        "alerts.email_recipients: en çok 20 adres",
    ),
    (
        "alerts.email_min_severity",
        "urgent",
        "setting_severity_invalid",
        "alerts.email_min_severity: info, low, medium, high, critical değerlerinden biri olmalı",
    ),
    (
        "alerts.web_base_url",
        "http://172.20.20.15",
        "setting_url_invalid",
        "alerts.web_base_url: https:// ile başlayan sunucu adresi bekleniyor, ör. "
        "https://10.0.0.10 (boş: yapılandırmadan)",
    ),
    (
        "usage.background_domains",
        ["*"],
        "setting_domain_pattern_invalid",
        "usage.background_domains: geçersiz alan adı kalıbı '*' (ör. *.example.com)",
    ),
    (
        "usage.background_appcats",
        [""],
        "setting_appcat_invalid",
        "usage.background_appcats: geçersiz uygulama kategorisi ''",
    ),
    (
        "usage.background_ports",
        [0],
        "setting_ports_invalid",
        "usage.background_ports: 1-65535 arası port numaraları bekleniyor",
    ),
    (
        "usage.background_ports",
        list(range(1, 502)),
        "setting_too_many",
        "usage.background_ports: en çok 500 öğe",
    ),
    (
        "usage.work_hours",
        {"days": [1]},
        "setting_work_hours_fields",
        "usage.work_hours: days, start ve end alanları bekleniyor",
    ),
    (
        "usage.work_hours",
        {"days": [8], "start": "08:00", "end": "18:00"},
        "setting_work_hours_days",
        "usage.work_hours: days 1 (Pazartesi) - 7 (Pazar) arası gün numaraları olmalı",
    ),
    (
        "usage.work_hours",
        {"days": [1], "start": 8, "end": "18:00"},
        "setting_work_hours_time",
        "usage.work_hours: start ve end SS:DD biçiminde olmalı",
    ),
    (
        "usage.work_hours",
        {"days": [1], "start": "8:00", "end": "18:00"},
        "setting_work_hours_time",
        "usage.work_hours: start ve end SS:DD biçiminde olmalı (ör. 08:00)",
    ),
    (
        "usage.work_hours",
        {"days": [1], "start": "18:00", "end": "08:00"},
        "setting_work_hours_order",
        "usage.work_hours: mesai başlangıcı bitişten önce olmalı (gece vardiyası yok)",
    ),
    (
        "usage.holidays",
        ["02-30"],
        "setting_holiday_invalid",
        "usage.holidays: geçersiz tarih '02-30' (AA-GG ya da YYYY-AA-GG)",
    ),
    # ADR-0016: FortiGate DHCP evidence, srcmac and the internet exits.
    (
        "dhcp.fortigate_logids",
        {"26001": "assigned"},
        "setting_logid_invalid",
        "dhcp.fortigate_logids: logid 10 rakam, anlamı assigned ya da deassigned olmalı (en çok "
        "16 kayıt)",
    ),
    (
        "dhcp.fortigate_dhcp_msgs",
        {"0100026001": []},
        "setting_dhcp_msg_invalid",
        "dhcp.fortigate_dhcp_msgs: her logid 10 rakam, değeri 1-8 ileti türünden (her biri 1-32 "
        "karakter) oluşan bir liste olmalı (en çok 16 kayıt)",
    ),
    (
        "lookup.srcmac_interfaces",
        [" vlan42"],
        "setting_interface_invalid",
        "lookup.srcmac_interfaces: geçersiz arayüz adı ' vlan42'",
    ),
    (
        "dhcp.fortigate_interfaces_excluded",
        [f"ipsec{n}" for n in range(33)],
        "setting_too_many",
        "dhcp.fortigate_interfaces_excluded: en çok 32 arayüz",
    ),
    (
        "nat.snat_trandisp",
        [],
        "setting_trandisp_invalid",
        "nat.snat_trandisp: 1-8 değer, her biri küçük harfle başlayan en çok 16 karakter (ör. "
        "snat)",
    ),
    (
        "nat.egresses",
        [],
        "setting_type",
        "nat.egresses: nesne bekleniyor (varsayılan {}), verilen: liste",
    ),
]


@pytest.mark.parametrize(("key", "value", "code", "console"), REFUSALS)
def test_every_refusal_has_a_code_and_both_texts(
    key: str, value: Any, code: str, console: str
) -> None:
    with pytest.raises(SettingValueError) as caught:
        validate_setting(key, value)
    refusal = caught.value
    assert isinstance(refusal, ValueError)  # the console's ``except ValueError`` still holds
    assert str(refusal) == console  # arcilogger-db settings set prints the same as before
    assert (refusal.key, refusal.code) == (key, code)
    assert refusal.message_tr
    assert refusal.message_en
    assert refusal.message_tr != refusal.message_en
    assert "{" not in refusal.message_tr + refusal.message_en  # every fact filled in
    # The web interface's texts never repeat what was submitted, only the rule's own words
    # (field names, roles, the example pattern).
    rules = " ".join(text for texts in SETTING_ERRORS.values() for text in texts) + " ".join(ROLES)
    for submitted in value if isinstance(value, list | dict) else [value]:
        if isinstance(submitted, str) and submitted.strip() and submitted not in rules:
            assert submitted not in refusal.message_tr + refusal.message_en


def test_every_code_of_the_catalogue_is_a_refusal() -> None:
    # The codes of the exit rules of nat.egresses (arcilogger_db.egress) are refusals of
    # validate_egresses, which the settings module wraps; they are produced by its own tests.
    assert set(SETTING_ERRORS) >= EGRESS_ERROR_CODES
    assert {code for _, _, code, _ in REFUSALS} | EGRESS_ERROR_CODES == set(SETTING_ERRORS)
    for message_tr, message_en in SETTING_ERRORS.values():
        assert message_tr.endswith(".")
        assert message_en.endswith(".")


def test_the_bounds_are_named_in_both_languages() -> None:
    with pytest.raises(SettingValueError) as caught:
        validate_setting("auth.lockout_attempts", 21)
    assert caught.value.message_tr == "1 ile 20 arasında olmalı."
    assert caught.value.message_en == "Must be between 1 and 20."
    with pytest.raises(SettingValueError) as caught:
        validate_setting("lease.default_seconds", 30)
    assert caught.value.message_tr == "En az 60 olmalı."
    assert caught.value.message_en == "Must be at least 60."
    with pytest.raises(SettingValueError) as caught:
        validate_setting("lookup.port_hint", "yes")
    assert caught.value.message_en == "Wrong type of value: true/false expected."


def test_report_recipients_share_the_address_refusal() -> None:
    """Report schedules (area R) validate their recipients with the same function."""
    with pytest.raises(SettingValueError) as caught:
        email_addresses("recipients", ["bt@ornek.example", "nope"])
    assert (caught.value.key, caught.value.code) == ("recipients", "setting_email_invalid")


def test_reauth_and_locked_keys() -> None:
    assert set(DEFAULT_SETTINGS) >= REAUTH_KEYS
    assert set(DEFAULT_SETTINGS) >= LOCKED_KEYS
    assert {key for key in DEFAULT_SETTINGS if key.startswith(("auth.", "privacy."))} <= REAUTH_KEYS
    assert {
        "db.retention_days",
        "usage.retention_days",
        "reports.keep_days",
        "evidence.keep_days",
        "usage.pseudonymize_anonymous_role",
        "alerts.email_recipients",
        "alerts.email_min_severity",
    } <= REAUTH_KEYS
    # A stolen session cannot silence the alerts, but ordinary thresholds need no password.
    assert "alerts.email_digest_seconds" not in REAUTH_KEYS
    assert "lookup.tolerance_seconds" not in REAUTH_KEYS
    assert {"devices.oui_file", "usage.psl_file"} == LOCKED_KEYS
    assert not LOCKED_KEYS & REAUTH_KEYS
    # ADR-0016 (owner point O8): the NAT, FortiGate DHCP and srcmac settings need no password.
    wave3 = {key for key in DEFAULT_SETTINGS if key.startswith(("nat.", "dhcp.", "lookup.srcmac"))}
    assert not wave3 & (REAUTH_KEYS | LOCKED_KEYS)


def _entries(session: Session) -> list[AuditLog]:
    return list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == "settings.change").order_by(AuditLog.id)
        )
    )


def test_change_setting_records_the_user_and_the_address(db_session: Session) -> None:
    user = User(username="ayse", display_name="Ayşe", role="admin", password_hash="!")
    db_session.add(user)
    db_session.flush()
    change = change_setting(
        db_session,
        "lookup.tolerance_seconds",
        90,
        actor="ayse",
        actor_id=user.id,
        ip="172.20.10.37",
    )
    db_session.commit()
    (entry,) = _entries(db_session)
    assert entry.id == change.audit_id
    assert (entry.actor_id, entry.actor_username, entry.ip) == (user.id, "ayse", "172.20.10.37")
    assert entry.target == "setting:lookup.tolerance_seconds"
    assert entry.details == {"old": 60, "new": 90}
    row = db_session.get(Setting, "lookup.tolerance_seconds")
    assert row is not None
    assert (row.value, row.updated_by) == (90, "ayse")
    assert verify_audit_chain(db_session).ok


def test_the_console_change_has_no_user_id_or_address(db_session: Session) -> None:
    change_setting(db_session, "lookup.tolerance_seconds", 30, actor="Tolga Yılmaz")
    db_session.commit()
    (entry,) = _entries(db_session)
    assert (entry.actor_id, entry.actor_username, entry.ip) == (None, "Tolga Yılmaz", None)


def test_reset_puts_the_shipped_default_back_and_is_audited(db_session: Session) -> None:
    change_setting(db_session, "usage.holidays", ["01-01"], actor="ayse")
    change = reset_setting(db_session, "usage.holidays", actor="ayse", ip="172.20.10.37")
    db_session.commit()
    assert (change.old, change.new) == (["01-01"], DEFAULT_SETTINGS["usage.holidays"])
    assert get_setting(db_session, "usage.holidays") == DEFAULT_SETTINGS["usage.holidays"]
    first, second = _entries(db_session)
    assert "reset" not in first.details
    assert second.details == {
        "reset": True,
        "old": ["01-01"],
        "new": DEFAULT_SETTINGS["usage.holidays"],
    }
    row = db_session.get(Setting, "usage.holidays")
    assert row is not None
    assert row.updated_by == "ayse"  # a person's choice: the seed never takes it over
    with pytest.raises(SettingValueError) as caught:
        reset_setting(db_session, "no.such.key", actor="ayse")
    assert caught.value.code == "setting_unknown"


# -- the change record of the internet exits (ADR-0016 D32) ---------------------------------------

HAT_1 = {"label": "Ana hat", "mode": "direct", "public": ["203.0.113.200"], "interfaces": ["wan1"]}
HAT_2 = {
    "label": "Yedek hat",
    "mode": "isp_nat",
    "public": ["198.51.100.77"],
    "transips": ["100.64.13.9"],
    "interfaces": ["wan2"],
}


def _changes(session: Session) -> dict[str, Any]:
    value = get_setting(session, CHANGES_KEY)
    return dict(value) if isinstance(value, dict) else {}


def test_a_change_of_the_exits_is_recorded_per_exit(db_session: Session) -> None:
    first = change_setting(db_session, "nat.egresses", {"hat-1": HAT_1}, actor="ayse")
    entry = db_session.get(AuditLog, first.audit_id)
    assert entry is not None
    record = _changes(db_session)
    assert record == {
        "hat-1": {
            "fingerprint": fingerprint(first.new["hat-1"]),
            "changed_at": entry.time.isoformat(),
            "changed_by": "ayse",
            "deleted": False,
        }
    }
    row = db_session.get(Setting, CHANGES_KEY)
    assert row is not None
    assert row.updated_by == ":system"  # a status row, never an editable setting
    assert CHANGES_KEY not in DEFAULT_SETTINGS
    # A new exit is recorded; the unchanged one keeps its entry.
    change_setting(db_session, "nat.egresses", {"hat-1": HAT_1, "hat-2": HAT_2}, actor="tolga")
    after = _changes(db_session)
    assert after["hat-1"] == record["hat-1"]
    assert after["hat-2"]["changed_by"] == "tolga"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "Yedek hat (eski)"),
        ("interfaces", ["wan2", "wan3"]),
        ("port_preservation", "preserved"),
    ],
)
def test_an_edit_that_decides_no_address_is_not_recorded(
    db_session: Session, field: str, value: Any
) -> None:
    """A label, the interfaces or port preservation only narrow or reorder a search (D32)."""
    change_setting(db_session, "nat.egresses", {"hat-2": HAT_2}, actor="ayse")
    record = _changes(db_session)
    change_setting(db_session, "nat.egresses", {"hat-2": {**HAT_2, field: value}}, actor="tolga")
    assert _changes(db_session) == record


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "direct"),
        ("public", ["198.51.100.78"]),
        ("transips", ["100.64.13.10"]),
        ("public_dynamic", True),
    ],
)
def test_an_edit_of_the_content_is_recorded(db_session: Session, field: str, value: Any) -> None:
    change_setting(db_session, "nat.egresses", {"hat-1": HAT_1, "hat-2": HAT_2}, actor="ayse")
    before = _changes(db_session)
    edited = {**HAT_2, field: value}
    if field == "mode":  # a direct exit takes no NAT address
        edited.pop("transips")
    change = change_setting(
        db_session, "nat.egresses", {"hat-1": HAT_1, "hat-2": edited}, actor="tolga"
    )
    after = _changes(db_session)
    assert after["hat-1"] == before["hat-1"]
    assert after["hat-2"]["fingerprint"] == fingerprint(change.new["hat-2"])
    assert after["hat-2"]["fingerprint"] != before["hat-2"]["fingerprint"]
    assert (after["hat-2"]["changed_by"], after["hat-2"]["deleted"]) == ("tolga", False)


def test_a_deleted_exit_is_recorded_and_its_return_too(db_session: Session) -> None:
    change_setting(db_session, "nat.egresses", {"hat-1": HAT_1, "hat-2": HAT_2}, actor="ayse")
    before = _changes(db_session)
    change_setting(db_session, "nat.egresses", {"hat-1": HAT_1}, actor="tolga")
    deleted = _changes(db_session)["hat-2"]
    assert (deleted["deleted"], deleted["changed_by"]) == (True, "tolga")
    assert deleted["fingerprint"] == before["hat-2"]["fingerprint"]  # what it held, by content
    reset_setting(db_session, "nat.egresses", actor="ayse")  # {}: every exit deleted
    assert {name: entry["deleted"] for name, entry in _changes(db_session).items()} == {
        "hat-1": True,
        "hat-2": True,  # already deleted: its entry stays as it was
    }
    assert _changes(db_session)["hat-2"] == deleted
    change_setting(db_session, "nat.egresses", {"hat-2": HAT_2}, actor="ayse")
    assert _changes(db_session)["hat-2"]["deleted"] is False


def test_the_change_record_is_in_the_audited_transaction(db_session: Session) -> None:
    change_setting(db_session, "nat.egresses", {"hat-1": HAT_1}, actor="ayse")
    db_session.rollback()
    assert _changes(db_session) == {}
    assert _entries(db_session) == []
    change_setting(db_session, "nat.egresses", {"hat-1": HAT_1}, actor="ayse")
    db_session.commit()
    assert sorted(_changes(db_session)) == ["hat-1"]
    assert len(_entries(db_session)) == 1
    assert verify_audit_chain(db_session).ok


def test_other_settings_leave_the_change_record_alone(db_session: Session) -> None:
    change_setting(db_session, "nat.snat_trandisp", ["snat", "dnat"], actor="ayse")
    assert db_session.get(Setting, CHANGES_KEY) is None
