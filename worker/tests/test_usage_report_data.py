"""``build_report_data`` on the golden usage site (contract section 3, schema 1).

The figures are the hand-computed ones of test_usage_golden.py (14.10.2026): company active
1020 s, online 1440 s, download 734500 bytes, upload 11560, 14 sessions, 3 blocked attempts,
180 s active off hours; departments Finans 540 s (Ayşe 360 s, Can 120 s and the bare address
of VLAN 41, 60 s), İdari İşler (PC-02, by its VLAN) 300 s, Planlama (Burak) 180 s.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_db.models import UsageDaily, UsageDay
from arcilogger_db.settings import set_setting
from arcilogger_worker.testing import tr
from arcilogger_worker.usage import report_data
from arcilogger_worker.usage.jobs import rebuild_day
from arcilogger_worker.usage.pseudonyms import PseudonymKeyMissingError, pseudonym
from arcilogger_worker.usage.report_data import build_report_data, sample_report_data
from arcilogger_worker.usage.testing import (
    DAY,
    NEXT,
    PC1_MAC,
    PC2_MAC,
    GoldenSite,
    build_golden_site,
)

NOW = tr(NEXT + timedelta(days=1), 9)
GENERATED = tr(NEXT, 7)
KEY = b"report-data-test-key-0123456789"  # gitleaks:allow (test-only key)


@pytest.fixture
def site(db_session: Session, layout: Layout) -> GoldenSite:
    golden = build_golden_site(db_session, layout)
    rebuild_day(db_session, DAY, now=NOW)
    rebuild_day(db_session, NEXT, now=NOW)
    return golden


def _full_day(session: Session, **options: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {"person_level": True, "pseudonymize": False}
    arguments.update(options)
    return build_report_data(
        session,
        start=tr(DAY, 0),
        end=tr(NEXT, 0),
        kind="full_day",
        generated_at=GENERATED,
        **arguments,
    )


def test_full_day_report_matches_the_golden_day(db_session: Session, site: GoldenSite) -> None:
    data = _full_day(db_session)
    assert json.loads(json.dumps(data)) == data  # JSON-serialisable as it is
    assert data["schema"] == 1
    assert data["period"] == {
        "from": "2026-10-14T00:00:00+03:00",
        "to": "2026-10-15T00:00:00+03:00",
    }
    assert data["generated_at"] == "2026-10-15T07:00:00+03:00"
    assert data["computed_until"] == "2026-10-15T00:00:00+03:00"
    company = data["company"]
    assert {k: v for k, v in company.items() if not k.startswith("top_")} == {
        "active_users": 5,
        "online_seconds": 1440,
        "active_seconds": 1020,
        "download_bytes": 734500,
        "upload_bytes": 11560,
        "sessions": 14,
        "blocked": 3,
        "off_hours_active_seconds": 180,
    }
    assert [(s["domain"], s["seconds"]) for s in company["top_sites"]] == [
        ("portal.example", 300),
        ("bulut.example", 240),
        ("video.example", 210),
        ("haber.example", 120),
        ("203.0.113.110", 60),
        ("muzik.example", 60),
        ("posta.example", 30),
    ]
    assert company["top_sites"][0]["share"] == round(300 / 1020, 4)
    assert company["top_categories"][0] == {
        "category": "İş Uygulamaları",
        "seconds": 300,
        "share": round(300 / 1020, 4),
    }
    assert data["departments"] == [
        {"name": "Finans", "active_users": 3, "active_seconds": 540,
         "download_bytes": 699500, "upload_bytes": 11560, "blocked": 3},
        {"name": "İdari İşler", "active_users": 1, "active_seconds": 300,
         "download_bytes": 19000, "upload_bytes": 0, "blocked": 0},
        {"name": "Planlama", "active_users": 1, "active_seconds": 180,
         "download_bytes": 16000, "upload_bytes": 0, "blocked": 0},
    ]  # fmt: skip
    assert [p["label"] for p in data["persons"]] == ["Ayşe Yılmaz", "Burak Demir", "Can Öztürk"]
    assert data["persons"][0] == {
        "label": "Ayşe Yılmaz",
        "subject": f"person:{site.ayse}",
        "department": "Finans",
        "first_activity": "2026-10-14T09:00:00+03:00",
        "last_activity": "2026-10-14T19:32:00+03:00",
        "online_seconds": 660,
        "active_seconds": 360,
        "download_bytes": 687500,
        "upload_bytes": 11560,
        "sessions": 7,
        "top_sites": [
            {"domain": "video.example", "seconds": 210},
            {"domain": "haber.example", "seconds": 120},
            {"domain": "posta.example", "seconds": 30},
        ],
        "top_categories": [
            {"category": "Video", "seconds": 210},
            {"category": "Haber", "seconds": 120},
            {"category": "Bilinmiyor", "seconds": 30},
        ],
        "blocked": 3,
        "off_hours_active_seconds": 120,
    }
    assert data["changes"] == []  # no rolled-up day before the 14th
    assert data["data_quality"]["unattributed_seconds"] == 60
    assert data["data_quality"]["notes_tr"][1] == (
        "Aktif sürenin %6 kadarı bir cihaza bağlanamadı (yalnızca IP adresi)."
    )
    assert len(data["person_sites"]) == 9  # 7 domains of Ayşe, portal.example of Burak and Can
    # Every detail row names its subject: the renderers match rows to persons by it.
    keys = {p["label"]: p["subject"] for p in data["persons"]}
    assert all(row["subject"] == keys[row["label"]] for row in data["person_sites"])
    assert [
        (h["label"], h["hour"], h["active_seconds"], h["download_bytes"])
        for h in data["person_hours"]
    ] == [
        ("Ayşe Yılmaz", "2026-10-14T09:00:00+03:00", 240, 621600),
        ("Ayşe Yılmaz", "2026-10-14T10:00:00+03:00", 0, 55900),
        ("Ayşe Yılmaz", "2026-10-14T19:00:00+03:00", 120, 10000),
        ("Burak Demir", "2026-10-14T11:00:00+03:00", 180, 16000),
        ("Can Öztürk", "2026-10-14T12:00:00+03:00", 60, 4000),
        ("Can Öztürk", "2026-10-14T13:00:00+03:00", 60, 3000),
    ]
    assert [(b["time"], b["label"], b["source"]) for b in data["blocked"]] == [
        ("2026-10-14T11:15:00+03:00", "Ayşe Yılmaz", "web"),
        ("2026-10-14T11:15:01+03:00", "Ayşe Yılmaz", "dns"),
        ("2026-10-14T11:20:00+03:00", "Ayşe Yılmaz", "app"),
    ]
    assert {b["subject"] for b in data["blocked"]} == {f"person:{site.ayse}"}
    assert data["blocked_total"] == 3
    assert [(d["label"], d["mac"], d["person"], d["active_seconds"]) for d in data["devices"]] == [
        ("Ayşe'nin bilgisayarı", PC1_MAC, "Ayşe Yılmaz", 360),
        ("Planlama bilgisayarı", "3c:52:82:00:10:03", "Burak Demir, Can Öztürk", 300),
        ("PC-02", PC2_MAC, None, 300),
        ("172.20.41.77", None, None, 60),
    ]
    # Deterministic for the same data.
    assert _full_day(db_session) == data


def test_company_only_and_pseudonymised_reports(
    db_session: Session, site: GoldenSite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = _full_day(db_session, person_level=False)
    for name in ("persons", "person_sites", "person_hours", "blocked", "devices"):
        assert plain[name] == []
    assert plain["company"]["active_seconds"] == 1020
    # Without a key in the secret store there are no pseudonyms (never a weaker fallback).
    config = Config(data_dir=tmp_path / "data", secrets_dir=tmp_path / "secrets")
    monkeypatch.setattr(report_data, "load_config", lambda _path: config)
    with pytest.raises(PseudonymKeyMissingError):
        _full_day(db_session, pseudonymize=True)
    hidden = _full_day(db_session, pseudonymize=True, pseudonym_key=KEY)
    assert hidden["pseudonymized"] is True
    labels = [p["label"] for p in hidden["persons"]]
    assert pseudonym(KEY, f"person:{site.ayse}")[1] in labels
    assert all(label.startswith("Kullanıcı-") for label in labels)
    assert all(d["mac"] is None and d["ip"] is None for d in hidden["devices"])
    assert {d["label"][:6] for d in hidden["devices"]} == {"Cihaz-"}
    assert {b["label"] for b in hidden["blocked"]} == {pseudonym(KEY, f"person:{site.ayse}")[1]}
    token = pseudonym(KEY, f"person:{site.ayse}")[0]
    assert {b["subject"] for b in hidden["blocked"]} == {token}  # never the real key
    assert token in {p["subject"] for p in hidden["persons"]}


def test_a_cut_list_of_blocked_attempts_is_counted_and_noted(
    db_session: Session, site: GoldenSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_data, "BLOCKED_MAX", 2)
    data = _full_day(db_session)
    assert [b["time"] for b in data["blocked"]] == [
        "2026-10-14T11:15:00+03:00",
        "2026-10-14T11:15:01+03:00",
    ]  # the oldest
    assert data["blocked_total"] == 3
    assert data["persons"][0]["blocked"] == 3  # the count stays complete
    assert data["data_quality"]["notes_tr"][-1] == (
        "Engellenen deneme listesi 2 kayıtta kesildi (dönemde 3 kayıt; en eskiler listelendi); "
        "kişi ve departman sayıları listelenmeyenleri de sayar."
    )
    assert data["data_quality"]["notes_en"][-1].startswith(
        "The list of blocked attempts was cut at 2 rows (3 in the period"
    )
    # A person left out of the person-level lists does not use up the list either.
    db_session.execute(
        text("UPDATE persons SET usage_excluded = true WHERE id = :id"), {"id": site.ayse}
    )
    db_session.commit()
    hidden = _full_day(db_session)
    assert (hidden["blocked"], hidden["blocked_total"]) == ([], 0)


def test_end_of_day_window_counts_the_hours_before_the_end(
    db_session: Session, site: GoldenSite
) -> None:
    data = build_report_data(
        db_session,
        start=tr(DAY, 0),
        end=tr(DAY, 18, 30),
        kind="end_of_day",
        person_level=True,
        pseudonymize=False,
        generated_at=tr(DAY, 18, 30),
    )
    # Without Ayşe's evening (120 s) and PC-02's 23:59 (60 s).
    assert data["company"]["active_seconds"] == 840
    assert data["company"]["off_hours_active_seconds"] == 0
    assert data["company"]["blocked"] == 3
    assert any("saatleri kapsar" in note for note in data["data_quality"]["notes_tr"])
    assert [h["hour"] for h in data["person_hours"] if h["label"] == "Ayşe Yılmaz"] == [
        "2026-10-14T09:00:00+03:00",
        "2026-10-14T10:00:00+03:00",
    ]


def test_part_day_windows_take_first_and_last_activity_from_the_window(
    db_session: Session, site: GoldenSite
) -> None:
    """The end-of-day report run late (catch-up at 21:00: the evening is already rolled up) and
    a custom 12:30-18:00 window: the activity times are active minutes inside the window."""

    def persons(start: datetime, end: datetime) -> dict[str, tuple[str | None, str | None]]:
        data = build_report_data(
            db_session,
            start=start,
            end=end,
            kind="custom",
            person_level=True,
            pseudonymize=False,
            generated_at=GENERATED,
        )
        return {p["label"]: (p["first_activity"], p["last_activity"]) for p in data["persons"]}

    # Ayşe: 09:00-09:04 and 19:30-19:32 — not "18:30", the end of the window.
    assert persons(tr(DAY, 0), tr(DAY, 18, 30))["Ayşe Yılmaz"] == (
        "2026-10-14T09:00:00+03:00",
        "2026-10-14T09:04:00+03:00",
    )
    # Can: 12:00 and 13:00 — the hour of 12:00 counts, its minute before 12:30 does not.
    assert persons(tr(DAY, 12, 30), tr(DAY, 18))["Can Öztürk"] == (
        "2026-10-14T13:00:00+03:00",
        "2026-10-14T13:01:00+03:00",
    )
    # Burak: of his minutes 11:00, 11:01 and 11:59, the first starts before 11:00:30; the last
    # starts inside the window and ends after it (cut at the window's end).
    assert persons(tr(DAY, 11, 0, 30), tr(DAY, 11, 59, 30))["Burak Demir"] == (
        "2026-10-14T11:01:00+03:00",
        "2026-10-14T11:59:30+03:00",
    )


def _limit_noted(data: dict[str, Any]) -> bool:
    return any("blocked_per_day" in note for note in data["data_quality"]["notes_tr"])


def test_the_daily_limit_of_stored_blocked_attempts_is_noted(
    db_session: Session, site: GoldenSite
) -> None:
    """The engine stores at most ``usage.blocked_per_day`` attempts per subject and day (the
    newest) and counts all of them: a whole-day report counts every attempt but lists the stored
    ones, a part-day report can only count the stored ones."""
    set_setting(db_session, "usage.blocked_per_day", 2, updated_by="test")
    db_session.commit()
    rebuild_day(db_session, DAY, now=NOW)
    whole = _full_day(db_session)
    assert whole["company"]["blocked"] == whole["persons"][0]["blocked"] == 3
    assert [b["time"] for b in whole["blocked"]] == [
        "2026-10-14T11:15:01+03:00",
        "2026-10-14T11:20:00+03:00",
    ]  # the two newest
    assert whole["blocked_total"] == 2  # no 50 000 cut: no note of that
    assert not any("kesildi" in note for note in whole["data_quality"]["notes_tr"])
    assert whole["data_quality"]["notes_tr"][-2:] == [
        "1 kişi, cihaz ya da IP adresinin engellenen denemeleri en az bir gün günlük kayıt "
        "sınırını (usage.blocked_per_day) aştı: listeler o günlerin yalnızca en yeni kayıtlarını "
        "içerir; sayılar hepsini sayar.",
        "Aktif sürenin %6 kadarı bir cihaza bağlanamadı (yalnızca IP adresi).",
    ]
    assert whole["data_quality"]["notes_en"][-2].startswith(
        "The blocked attempts of 1 person, device or IP address exceeded the daily row limit"
    )
    # Without the lists nothing is missing (nor below when the person is left out of them).
    assert not _limit_noted(_full_day(db_session, person_level=False))
    part = build_report_data(
        db_session,
        start=tr(DAY, 0),
        end=tr(DAY, 18, 30),
        kind="end_of_day",
        person_level=False,
        pseudonymize=False,
        generated_at=tr(DAY, 18, 30),
    )
    assert part["company"]["blocked"] == 2  # the stored attempts of the window
    assert (
        "1 kişi, cihaz ya da IP adresinin engellenen denemeleri en az bir gün günlük kayıt "
        "sınırını (usage.blocked_per_day) aştı: rapor günün bir bölümünü kapsadığından sayılar ve "
        "listeler yalnızca saklanan kayıtları içerir; gerçek sayı daha yüksek olabilir."
    ) in part["data_quality"]["notes_tr"]
    assert any(
        note.startswith("The blocked attempts of 1 person, device or IP address exceeded")
        and note.endswith("the real number may be higher.")
        for note in part["data_quality"]["notes_en"]
    )
    db_session.execute(
        text("UPDATE persons SET usage_excluded = true WHERE id = :id"), {"id": site.ayse}
    )
    db_session.commit()
    assert not _limit_noted(_full_day(db_session))


def test_notable_changes_against_the_seven_day_average(
    db_session: Session, site: GoldenSite
) -> None:
    before = DAY - timedelta(days=1)
    db_session.add(
        UsageDay(
            day=before,
            status="final",
            computed_at=NOW,
            computed_until=tr(DAY, 0),
            inputs_hash="0" * 64,
            attribution_hash="0" * 64,
            ips=[],
            details={},
        )
    )
    db_session.add(
        UsageDaily(
            day=before,
            subject=f"person:{site.ayse}",
            off_hours=False,
            kind="person",
            person_id=site.ayse,
            vlan=43,
            online_seconds=60,
            active_seconds=60,
            download_bytes=0,
            upload_bytes=0,
            sessions=1,
            blocked=0,
            timeline=[],
        )
    )
    set_setting(db_session, "usage.change_min_seconds", 60, updated_by="test")
    db_session.commit()
    changes = _full_day(db_session)["changes"]
    assert [(c["scope"], c["metric"], c["value"], c["baseline_7d"], c["change_percent"])
            for c in changes] == [
        ("company", "active_seconds", 1020, 60, 1600.0),
        ("department:Finans", "active_seconds", 540, 60, 800.0),
    ]  # fmt: skip
    assert changes[0]["text_tr"] == (
        "Şirket: aktif kullanım süresi 7 günlük ortalamaya göre %1600 arttı "
        "(17 dk; ortalama 1 dk, tahmini)"
    )
    assert changes[0]["text_en"].startswith("Company: active time up 1600 %")


def _keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_keys(value[0])] if value else []
    return type(value).__name__ if value is not None else None


def test_sample_report_data_has_the_schema_of_the_real_one(
    db_session: Session, site: GoldenSite
) -> None:
    sample = sample_report_data()
    real = _full_day(db_session)
    assert sample == sample_report_data()
    assert json.loads(json.dumps(sample)) == sample
    assert set(sample) == set(real)
    for name in ("company", "data_quality"):
        assert set(sample[name]) == set(real[name])
    for name in ("departments", "persons", "person_sites", "person_hours", "blocked", "devices"):
        assert set(sample[name][0]) == set(real[name][0]), name
    assert set(sample["company"]["top_sites"][0]) == set(real["company"]["top_sites"][0])
    assert set(sample["changes"][0]) == {
        "scope",
        "metric",
        "value",
        "baseline_7d",
        "change_percent",
        "text_tr",
        "text_en",
    }
