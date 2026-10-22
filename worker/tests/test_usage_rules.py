"""Usage rules without the database: registrable domains (bundled Public Suffix List, never
downloaded), background rules, work hours and holidays, pseudonyms."""

from __future__ import annotations

import socket
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_worker.testing import tr
from arcilogger_worker.usage.config import Holidays, UsageSettings, WorkHours
from arcilogger_worker.usage.domains import BackgroundRules, DomainNamer, normalize_hostname
from arcilogger_worker.usage.pseudonyms import pseudonym, token


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.youtube.com", "youtube.com"),
        ("youtube.com", "youtube.com"),
        ("news.bbc.co.uk", "bbc.co.uk"),
        ("www.turkiye.gov.tr", "turkiye.gov.tr"),
        ("d1234.cloudfront.net", "cloudfront.net"),  # private suffixes off by default
        ("portal.example", "portal.example"),  # unknown TLD: the label before it
        ("203.0.113.10", "203.0.113.10"),
        ("localhost", "localhost"),
    ],
)
def test_registrable_domains(host: str, expected: str) -> None:
    assert DomainNamer().registrable(host) == expected


def test_private_suffixes_on_request() -> None:
    assert DomainNamer(private_suffixes=True).registrable("d1234.cloudfront.net") == (
        "d1234.cloudfront.net"
    )


def test_the_suffix_list_is_read_locally_never_downloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_network(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the Public Suffix List must never be fetched")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    namer = DomainNamer()
    assert namer.source.startswith("bundled publicsuffixlist ")
    assert namer.registrable("www.example.co.uk") == "example.co.uk"
    assert "publicsuffixlist.update" not in sys.modules
    # An administrator's newer list; a broken one falls back to the bundled list, with a reason.
    good = tmp_path / "public_suffix_list.dat"
    good.write_text(
        "// ===BEGIN ICANN DOMAINS===\ncom\nyeni\n// ===END ICANN DOMAINS===\n", encoding="utf-8"
    )
    custom = DomainNamer(str(good))
    assert custom.warning == ""
    assert str(good) in custom.source
    assert custom.registrable("a.b.ornek.yeni") == "ornek.yeni"
    broken = tmp_path / "broken.dat"
    broken.write_text("not a list\n", encoding="utf-8")
    fallback = DomainNamer(str(broken))
    assert "no ICANN section" in fallback.warning
    assert fallback.source.startswith("bundled")
    assert "usage.psl_file not used" in DomainNamer(str(tmp_path / "missing.dat")).warning


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("WWW.Example.COM.", "www.example.com"),
        ("example.com:443", "example.com"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        ("  ", None),
        ("bad host", None),
        (None, None),
    ],
)
def test_host_names_are_normalised(raw: str | None, expected: str | None) -> None:
    assert normalize_hostname(raw) == expected


def test_background_rules() -> None:
    rules = BackgroundRules.create(
        tuple(DEFAULT_SETTINGS["usage.background_domains"]),
        tuple(DEFAULT_SETTINGS["usage.background_appcats"]),
        tuple(DEFAULT_SETTINGS["usage.background_ports"]),
    )
    assert rules.host("download.windowsupdate.com")
    assert rules.host("windowsupdate.com")  # "*.x" also matches "x"
    assert rules.host("ocsp.digicert.com")
    assert rules.host("crl3.digicert.com")
    assert not rules.host("www.microsoft.com")
    assert not rules.host("www.youtube.com")
    assert rules.appcat("update")
    assert rules.appcat("Network.Service")
    assert not rules.appcat("General.Interest")
    assert not rules.appcat(None)
    assert all(rules.port(port) for port in (53, 123, 853))
    assert not rules.port(443)
    assert not rules.port(None)


def test_work_hours_and_holidays() -> None:
    settings = UsageSettings()
    wednesday = date(2026, 10, 14)
    assert not settings.is_off_hours(tr(wednesday, 8))
    assert not settings.is_off_hours(tr(wednesday, 17, 59))
    assert settings.is_off_hours(tr(wednesday, 18))
    assert settings.is_off_hours(tr(wednesday, 7, 59))
    assert settings.is_off_hours(tr(date(2026, 10, 17), 10))  # Saturday
    assert settings.is_off_hours(tr(date(2026, 10, 29), 10))  # Cumhuriyet Bayramı (Thursday)
    minutes = settings.off_hours_minutes(wednesday)
    assert len(minutes) == 1440
    assert minutes.count(False) == 600  # 08:00-18:00
    # Only the fixed-date national holidays are shipped (religious ones are entered yearly).
    shipped = Holidays.parse(None)
    assert sorted(shipped.yearly) == [(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)]
    assert shipped.dates == frozenset()
    custom = UsageSettings.from_values(
        {
            "usage.work_hours": {"days": [1, 2, 3, 4, 5, 6], "start": "08:30", "end": "17:00"},
            "usage.holidays": ["2026-12-31", "01-01", "bad"],
        }
    )
    assert custom.work_hours == WorkHours(frozenset({1, 2, 3, 4, 5, 6}), 510, 1020)
    assert custom.is_off_hours(tr(date(2026, 12, 31), 10))
    assert not custom.is_off_hours(tr(date(2026, 10, 17), 10))  # Saturday is a work day now
    # A damaged value falls back to the default, it never stops the rollups.
    broken = UsageSettings.from_values(
        {"usage.work_hours": {"days": "x"}, "usage.dns_window_seconds": "x"}
    )
    assert broken.work_hours == UsageSettings().work_hours
    assert broken.dns_window_seconds == 600


def test_pseudonyms_are_stable_and_keyed() -> None:
    key = b"pseudonym-test-key-0123456789"  # gitleaks:allow (test-only key)
    first = pseudonym(key, "person:12")
    assert first == pseudonym(key, "person:12")
    assert first[0].startswith("p:")
    assert len(first[0]) == 18
    assert first[1] == f"Kullanıcı-{first[0][2:6].upper()}"
    assert pseudonym(key, "device:3c:52:82:00:10:02")[1].startswith("Cihaz-")
    assert pseudonym(key, "ip:172.20.41.77")[0].startswith("d:")
    assert token(b"another-key-0123456789", "person:12") != first[0][2:]
