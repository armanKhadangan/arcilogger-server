"""The desktop status window's judgments (``arcilogger_core.desktop.status``; Wave 5 contract §5,
decisions I2 and I5) and its entry point. No Tk here: the window itself is
``test_desktop_window.py``."""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from arcilogger_core.desktop import __main__ as entry
from arcilogger_core.desktop import status, winapi
from arcilogger_core.desktop.status import Level, Probe, ServiceState, Site, Snapshot
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, isoformat_site
from arcilogger_core.tsa.mock import MockFailure

#: 15:00 site time.
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
YESTERDAY = "2026-11-01"
GB = 1024**3


def iso(value: datetime) -> str:
    return isoformat_site(value, "seconds")


def running() -> dict[str, ServiceState]:
    return {name: "running" for name, _, _, _ in status.SERVICES}


def probe(
    states: Mapping[str, ServiceState] | None = None,
    *,
    version: str | None = "1.0.0",
    disk: tuple[int, int] | None = (500 * GB, 1000 * GB),
    now: datetime = NOW,
) -> Probe:
    found = dict(running() if states is None else states)
    return Probe(
        services=lambda names: {name: found.get(name, "unknown") for name in names},
        health=lambda url: version,
        disk=lambda path: disk,
        now=lambda: now,
    )


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    """A healthy server: fresh status files, yesterday sealed, no alert."""
    layout = Layout(tmp_path / "data")
    write(
        layout.collector_status,
        {
            "updated_at": iso(NOW - timedelta(seconds=20)),
            "sources": {
                "fw-ornek": {"last_received": iso(NOW - timedelta(seconds=3))},
                "mt-ornek": {"last_received": iso(NOW - timedelta(seconds=40))},
            },
            "spool_write_error": None,
        },
    )
    write(
        layout.ingest_status,
        {
            "updated_at": iso(NOW - timedelta(seconds=5)),
            "database": {"ok": True, "last_error": None},
            "totals": {"lines_per_second": 812.4},
            "sources": {"fw-ornek": {"lag_seconds": 1.5}, "mt-ornek": {"lag_seconds": None}},
        },
    )
    write(layout.worker_status, {"updated_at": iso(NOW - timedelta(seconds=30))})
    write(
        layout.state / "seal" / f"{YESTERDAY}.json",
        {
            "status": "sealed",
            "sealed_at": "2026-11-02T00:12:40+03:00",
            "provider": "zamane",
            "legal_grade": True,
        },
    )
    return layout


def site(layout: Layout, **changes: Any) -> Site:
    values: dict[str, Any] = {"data_root": layout.root, "health_url": "http://127.0.0.1:9/"}
    values.update(changes)
    return Site(**values)


def look(layout: Layout, test_probe: Probe | None = None, **changes: Any) -> Snapshot:
    return status.collect(site(layout, **changes), test_probe or probe())


def row(snapshot: Snapshot, key: str) -> status.Row:
    return next(r for r in (*snapshot.services, *snapshot.checks) if r.key == key)


# -- the whole picture ---------------------------------------------------------------------------


def test_a_healthy_server_is_ok(layout: Layout) -> None:
    snapshot = look(layout)
    assert snapshot.level == "ok", status.render_text(snapshot)
    assert [r.key for r in snapshot.services] == [f"service:{n}" for n, *_ in status.SERVICES]
    assert [r.key for r in snapshot.checks] == ["flow", "database", "seal", "alerts", "disk"]
    assert row(snapshot, "service:ArciApi").text_tr == "çalışıyor; yanıt veriyor (sürüm 1.0.0)"
    assert row(snapshot, "flow").text_tr == "son kayıt 14:59:57 (3 sn önce); 2 kaynak"
    assert row(snapshot, "seal").text_tr == "son mühürlenen gün 2026-11-01, 00:12:40 (zamane)"
    assert row(snapshot, "alerts").text_en == "no alert in 24 hours"
    assert "500.0 GB boş / 1000.0 GB (%50)" in row(snapshot, "disk").text_tr
    text = status.render_text(snapshot)
    assert text.startswith("ARCILOGGER 15:00:00: Çalışıyor / Running")
    assert "[ok] Mühürleme / Sealing:" in text


def test_every_row_has_both_languages(layout: Layout) -> None:
    for r in (*look(layout).services, *look(layout).checks):
        assert all((r.title_tr, r.title_en, r.text_tr, r.text_en)), r


def test_unknown_counts_as_warn() -> None:
    assert status.overall(["ok", "unknown"]) == "warn"
    assert status.overall(["unknown", "critical"]) == "critical"
    assert status.overall([]) == "ok"


def test_window_title_and_flash_rule() -> None:
    assert status.window_title("critical") == "ARCILOGGER Durum — Sorun var / Problem"
    assert status.window_title("ok").startswith(status.TITLE)
    assert status.flash_due(None, "critical")
    assert status.flash_due("warn", "critical")
    assert not status.flash_due("critical", "critical")
    assert not status.flash_due("ok", "warn")


# -- services ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "state", "level", "text"),
    [
        ("ArciCollector", "stopped", "critical", "durdu"),
        ("ArciWeb", "missing", "critical", "kurulu değil"),
        ("ArciAdmin", "stopped", "warn", "durdu"),
        ("ArciIngest", "starting", "warn", "başlatılıyor"),
        ("ArciWorker", "paused", "warn", "duraklatıldı"),
        ("ArciWeb", "unknown", "unknown", "bilinmiyor"),
    ],
)
def test_service_states(
    layout: Layout, name: str, state: ServiceState, level: Level, text: str
) -> None:
    states = running()
    states[name] = state
    found = row(look(layout, probe(states)), f"service:{name}")
    assert (found.level, found.text_tr) == (level, text)


def test_a_stale_status_file_warns(layout: Layout) -> None:
    write(layout.worker_status, {"updated_at": iso(NOW - timedelta(minutes=5))})
    found = row(look(layout), "service:ArciWorker")
    assert found.level == "warn"
    assert found.text_tr == "çalışıyor; durum dosyası 5 dk eski"
    assert found.text_en == "running; status file 5 min old"


def test_a_missing_status_file_warns(layout: Layout) -> None:
    layout.ingest_status.unlink()
    assert row(look(layout), "service:ArciIngest").text_tr == "çalışıyor; durum dosyası yok"


def test_without_updated_at_the_file_time_counts(layout: Layout) -> None:
    write(layout.worker_status, {})
    old = (NOW - timedelta(minutes=10)).timestamp()
    os.utime(layout.worker_status, (old, old))
    assert row(look(layout), "service:ArciWorker").level == "warn"


def test_the_api_not_answering_warns(layout: Layout) -> None:
    found = row(look(layout, probe(version=None)), "service:ArciApi")
    assert (found.level, found.text_en) == ("warn", "running; not answering")


def test_without_the_service_manager_the_files_decide(layout: Layout) -> None:
    """Another platform (or the SCM could not be asked): a fresh file says running."""
    snapshot = look(layout, probe({}))
    assert row(snapshot, "service:ArciCollector").text_tr == "durum dosyası güncel"
    assert row(snapshot, "service:ArciCollector").level == "ok"
    assert row(snapshot, "service:ArciApi").level == "ok"
    assert row(snapshot, "service:ArciWeb").level == "unknown"
    layout.collector_status.unlink()
    assert row(look(layout, probe({})), "service:ArciCollector").level == "warn"


def test_a_failing_service_manager_is_unknown(layout: Layout) -> None:
    def refuse(names: Sequence[str]) -> Mapping[str, ServiceState]:
        raise OSError("RPC server unavailable")

    snapshot = status.collect(
        site(layout), Probe(services=refuse, health=lambda u: "1", now=lambda: NOW)
    )
    assert row(snapshot, "service:ArciWeb").level == "unknown"


# -- log flow and database -----------------------------------------------------------------------


def test_a_spool_write_error_is_critical(layout: Layout) -> None:
    data = json.loads(layout.collector_status.read_text("utf-8"))
    data["spool_write_error"] = {"sources": ["fw-ornek"], "error": "[Errno 28] No space left"}
    write(layout.collector_status, data)
    found = row(look(layout), "flow")
    assert found.level == "critical"
    assert found.text_tr == "ham kayıtlar diske yazılamıyor (fw-ornek): [Errno 28] No space left"
    assert look(layout).level == "critical"


def test_no_source_configured_warns(layout: Layout) -> None:
    write(layout.collector_status, {"updated_at": iso(NOW), "sources": {}})
    found = row(look(layout, configured=True), "flow")
    assert (found.level, found.text_en) == (
        "warn",
        "no expected log source is configured ([[sources]])",
    )
    # Without the configuration the window cannot tell "none configured" from "none sent".
    assert row(look(layout), "flow").text_en == "no message yet; 0 sources"


WATCHED = (status.Watched("fw-ornek"), status.Watched("ap-ornek", network=True))


def test_a_configured_source_that_never_sent_is_silent(layout: Layout) -> None:
    """The collector's file lists only what arrived since it started: after a restart, a source
    that sends nothing is missing from it, not listed as silent."""
    started = {"started_at": iso(NOW - timedelta(hours=2)), "updated_at": iso(NOW)}
    write(
        layout.collector_status,
        {**started, "sources": {"fw-ornek": {"last_received": iso(NOW - timedelta(seconds=3))}}},
    )
    found = row(look(layout, configured=True, sources=WATCHED), "flow")
    assert found.level == "warn"
    assert found.text_tr.endswith("2 kaynak, 1 sessiz: ap-ornek")
    # Shortly after a start nothing is silent yet.
    write(
        layout.collector_status,
        {
            "started_at": iso(NOW - timedelta(minutes=10)),
            "updated_at": iso(NOW),
            "sources": {"fw-ornek": {"last_received": iso(NOW - timedelta(seconds=3))}},
        },
    )
    assert row(look(layout, configured=True, sources=WATCHED), "flow").level == "ok"


def test_a_network_source_counts_by_its_senders(layout: Layout) -> None:
    write(
        layout.collector_status,
        {
            "started_at": iso(NOW - timedelta(hours=5)),
            "updated_at": iso(NOW),
            "sources": {
                "fw-ornek": {"last_received": iso(NOW - timedelta(seconds=3))},
                "ap-ornek-172.20.10.5": {"last_received": iso(NOW - timedelta(minutes=2))},
                "ap-ornek-172.20.10.6": {"last_received": iso(NOW - timedelta(hours=3))},
                "old-ornek": {"last_received": iso(NOW - timedelta(hours=4))},
            },
        },
    )
    # One sender of the network is enough; a source no longer watched is not judged.
    found = row(look(layout, configured=True, sources=WATCHED), "flow")
    assert (found.level, found.text_en) == ("ok", "last message 14:59:57 (3 s ago); 2 sources")


def test_a_silent_source_warns(layout: Layout) -> None:
    write(
        layout.collector_status,
        {
            "updated_at": iso(NOW),
            "sources": {
                "fw-ornek": {"last_received": iso(NOW - timedelta(seconds=3))},
                "mt-ornek": {"last_received": iso(NOW - timedelta(minutes=61))},
                "sw-ornek": {"last_received": None},
            },
        },
    )
    found = row(look(layout), "flow")
    assert found.level == "warn"
    assert found.text_tr.endswith("3 kaynak, 2 sessiz: mt-ornek, sw-ornek")
    assert row(look(layout, gap_minutes=120), "flow").text_en.endswith(
        "3 sources, 1 silent: sw-ornek"
    )


def test_no_message_yet_and_old_information(layout: Layout) -> None:
    write(
        layout.collector_status,
        {"updated_at": iso(NOW - timedelta(minutes=7)), "sources": {"fw-ornek": {}}},
    )
    found = row(look(layout), "flow")
    assert found.text_tr == "henüz kayıt gelmedi; 1 kaynak, 1 sessiz: fw-ornek (bilgi 7 dk eski)"


def test_the_database_down_is_critical(layout: Layout) -> None:
    write(layout.ingest_status, {"database": {"ok": False, "last_error": "connection refused"}})
    found = row(look(layout), "database")
    assert (found.level, found.text_tr) == (
        "critical",
        "veritabanına yazılamıyor: connection refused",
    )


def test_old_loader_information_says_so(layout: Layout) -> None:
    """A stopped loader leaves its last file behind: the row says how old its numbers are."""
    write(
        layout.ingest_status,
        {"updated_at": iso(NOW - timedelta(minutes=20)), "database": {"ok": True}, "sources": {}},
    )
    assert row(look(layout), "database").text_tr == "güncel (gecikme 0 sn) (bilgi 20 dk eski)"


def test_a_loader_far_behind_warns(layout: Layout) -> None:
    write(layout.ingest_status, {"database": {"ok": True}, "sources": {"a": {"lag_seconds": 900}}})
    found = row(look(layout), "database")
    assert (found.level, found.text_tr) == ("warn", "15 dk geride")
    assert row(look(layout), "database").text_en == "15 min behind"


def test_no_status_file_yet(layout: Layout) -> None:
    layout.collector_status.unlink()
    layout.ingest_status.unlink()
    snapshot = look(layout)
    assert row(snapshot, "flow").level == "unknown"
    assert row(snapshot, "database").text_en == "no information yet"


# -- sealing -------------------------------------------------------------------------------------


def seal_state(layout: Layout, day: str, **data: Any) -> None:
    write(layout.state / "seal" / f"{day}.json", data)


def failed_attempt(error: str) -> dict[str, Any]:
    """What ``Sealer._failed`` writes for a failed attempt: still ``pending``, with the error
    (pinned against the real sealer in ``test_the_real_sealer_s_states``)."""
    return {
        "status": "pending",
        "attempts": 1,
        "last_error": error,
        "last_error_code": "tsa_unavailable",
        "next_attempt_at": "2026-11-02T15:05:00+03:00",
    }


def test_nothing_to_seal_yet_is_ok(layout: Layout) -> None:
    (layout.state / "seal" / f"{YESTERDAY}.json").unlink()
    found = row(look(layout), "seal")
    assert (found.level, found.text_en) == ("ok", "no closed day to seal yet")
    (layout.state / "seal").rmdir()
    assert row(look(layout), "seal").level == "ok"


def test_a_seal_task_that_never_ran_is_late(layout: Layout) -> None:
    """No state at all, but the collector wrote closed days: Seal never ran."""
    (layout.state / "seal" / f"{YESTERDAY}.json").unlink()
    for day in ("2026-10-30", "2026-11-02"):
        path = layout.spool / "fw-ornek" / f"{day}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    (layout.spool / "_audit").mkdir()
    (layout.spool / "_audit" / "2026-10-29.jsonl").write_bytes(b"")
    found = row(look(layout), "seal")
    assert (found.level, found.text_en) == (
        "critical",
        "3 closed day(s) not sealed (last seal: none)",
    )
    # Spool days before [seal] first_day are never sealed.
    assert row(look(layout, first_day=date(2026, 11, 2)), "seal").level == "ok"


def test_a_trial_stamp_warns(layout: Layout) -> None:
    seal_state(layout, YESTERDAY, status="sealed", provider="mock", legal_grade=False)
    found = row(look(layout), "seal")
    assert found.level == "warn"
    assert found.text_tr.endswith("damga yasal nitelikli değil (deneme ya da test sağlayıcısı)")


def test_yesterday_not_sealed_before_and_after_three(layout: Layout) -> None:
    seal_state(layout, YESTERDAY, status="pending", attempts=0)
    seal_state(layout, "2026-10-31", status="sealed", provider="zamane", legal_grade=True)
    early = probe(now=datetime(2026, 11, 1, 23, 30, tzinfo=UTC))  # 02:30 site time
    found = row(look(layout, early), "seal")
    assert (found.level, found.text_en) == (
        "ok",
        "waiting for the nightly seal (last seal: 2026-10-31)",
    )
    found = row(look(layout), "seal")
    assert found.level == "warn"
    assert found.text_tr == "dün henüz mühürlenmedi (son mühür: 2026-10-31)"


def test_two_days_behind_warns_at_any_hour(layout: Layout) -> None:
    (layout.state / "seal" / f"{YESTERDAY}.json").unlink()
    seal_state(layout, "2026-10-30", status="sealed", provider="zamane", legal_grade=True)
    early = probe(now=datetime(2026, 11, 1, 23, 30, tzinfo=UTC))  # 02:30 site time, 26.5 h
    found = row(look(layout, early), "seal")
    assert (found.level, found.text_en) == (
        "warn",
        "2 closed days not sealed yet (last seal: 2026-10-30)",
    )


def test_a_failed_attempt_warns_with_its_error(layout: Layout) -> None:
    seal_state(layout, YESTERDAY, **failed_attempt("Zaman damgası sunucusuna ulaşılamadı"))
    seal_state(layout, "2026-10-31", status="sealed", provider="zamane", legal_grade=True)
    early = probe(now=datetime(2026, 11, 1, 22, 0, tzinfo=UTC))  # 01:00: before 03:00 too
    for when in (early, probe()):
        found = row(look(layout, when), "seal")
        assert found.level == "warn"
        assert found.text_tr == (
            "2026-11-01 mühürlenemedi, yeniden denenecek: Zaman damgası sunucusuna ulaşılamadı"
        )


def test_sealing_later_than_the_limit_is_critical(layout: Layout) -> None:
    (layout.state / "seal" / f"{YESTERDAY}.json").unlink()
    seal_state(layout, "2026-10-29", status="sealed", provider="zamane", legal_grade=True)
    seal_state(layout, "2026-10-30", **failed_attempt("no credit"))
    found = row(look(layout), "seal")
    # 2026-10-30 ended at 2026-10-31 00:00 site time, 63 hours before 15:00 on 11-02.
    assert found.level == "critical"
    assert found.text_tr == "3 kapanmış gün mühürlenmedi (son mühür: 2026-10-29)"
    assert row(look(layout, late_after_hours=72), "seal").level == "warn"


def test_a_first_day_failing_for_days_is_critical(layout: Layout) -> None:
    """No day sealed yet: the oldest open day decides, not "nothing sealed yet"."""
    (layout.state / "seal" / f"{YESTERDAY}.json").unlink()
    for day in ("2026-10-29", "2026-10-30", "2026-10-31", YESTERDAY):
        seal_state(layout, day, **failed_attempt("TSA unreachable"))
    found = row(look(layout), "seal")
    assert (found.level, found.text_en) == (
        "critical",
        "4 closed day(s) not sealed (last seal: none)",
    )
    seal_state(layout, "2026-10-29", status="skipped")
    seal_state(layout, "2026-10-30", status="skipped")
    seal_state(layout, "2026-10-31", status="skipped")
    found = row(look(layout), "seal")
    assert (found.level, found.text_en) == (
        "warn",
        "2026-11-01 could not be sealed and will be retried",
    )


def test_the_real_sealer_s_states(world: Any, days: list[date]) -> None:
    """The row reads what ``Sealer`` really writes: a failed attempt, then the seal."""
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1), 1, 0)
    site = status.Site(data_root=world.config.data_dir, health_url="http://127.0.0.1:9/")
    world.tsa.failure = MockFailure.UNREACHABLE
    world.sealer().seal_day(days[0])
    state = json.loads(world.layout.seal_state(days[0]).read_text("utf-8"))
    assert state["status"] == "pending"
    assert state["last_error"]
    found = status.seal_row(world.layout, site, world.now)
    assert found.level == "warn"
    assert found.text_en == f"{days[0].isoformat()} could not be sealed and will be retried"
    world.now = day_window(days[0])[1] + timedelta(hours=49)
    assert status.seal_row(world.layout, site, world.now).level == "critical"
    world.tsa.failure = MockFailure.NONE
    world.set_now(days[0] + timedelta(days=1), 2, 0)
    world.sealer().seal_day(days[0])
    found = status.seal_row(world.layout, site, world.now)
    assert found.level in ("ok", "warn")  # warn only for the mock's trial stamp
    assert found.text_en.startswith(f"last sealed day {days[0].isoformat()} (mock)")


def test_only_the_newest_seal_states_are_read(layout: Layout) -> None:
    """One file per day: years of days are listed, only the newest ones are opened."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    for offset in range(40):
        day = (start + timedelta(days=offset)).date().isoformat()
        seal_state(layout, day, status="sealed", provider="zamane", legal_grade=True)
    (layout.state / "seal" / "notes.json").write_text("{}", encoding="utf-8")
    (layout.state / "seal" / "secret-changes.jsonl").write_text("", encoding="utf-8")
    opened: list[Path] = []
    original = status.read_json

    def counting(path: Path) -> tuple[dict[str, Any], datetime] | None:
        opened.append(path)
        return original(path)

    states = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(status, "read_json", counting)
        states = status._seal_states(layout)
    assert len(opened) == status.SEAL_STATES
    assert states[0][0].isoformat() == YESTERDAY


# -- alerts --------------------------------------------------------------------------------------


def alert(severity: str, ago: timedelta, message: str = "m") -> str:
    entry = {"time": iso(NOW - ago), "rule": "R20-x", "severity": severity, "message": message}
    return json.dumps(entry, ensure_ascii=False) + "\n"


def test_alerts_of_the_last_day(layout: Layout) -> None:
    layout.alerts.write_text(
        alert("critical", timedelta(hours=30), "too old")
        + alert("medium", timedelta(hours=5), "orta uyarı")
        + "{torn line without end\n"
        + "\x00\x00\n"
        + alert("high", timedelta(hours=2), "Log kaynağı sessiz: mt-ornek (172.20.0.2)")
        + "[1, 2]\n",
        encoding="utf-8",
    )
    found = row(look(layout), "alerts")
    assert found.level == "warn"
    assert found.text_tr == (
        "1 yüksek, 1 orta; en önemlisi (13:00:00): Log kaynağı sessiz: mt-ornek (172.20.0.2)"
    )
    assert found.text_en == "1 high, 1 medium; the most important at 13:00:00"


def test_a_critical_alert_is_critical(layout: Layout) -> None:
    layout.alerts.write_text(alert("critical", timedelta(minutes=5)), encoding="utf-8")
    assert row(look(layout), "alerts").level == "critical"
    assert look(layout).level == "critical"


def test_low_alerts_are_shown_but_ok(layout: Layout) -> None:
    layout.alerts.write_text(alert("info", timedelta(minutes=5)), encoding="utf-8")
    found = row(look(layout), "alerts")
    assert (found.level, found.text_en) == ("ok", "1 info; the most important at 14:55:00")


def test_only_the_tail_of_the_alert_file_is_read(layout: Layout) -> None:
    old = alert("critical", timedelta(hours=1), "x" * 200)
    new = alert("high", timedelta(minutes=1))
    layout.alerts.write_text(old + new, encoding="utf-8")
    tail = len(new.encode()) + 10  # starts inside the first line, which is skipped
    lines = status.alert_lines(layout.alerts, tail=tail)
    assert [entry["severity"] for entry in lines] == ["high"]
    assert len(status.alert_lines(layout.alerts)) == 2


# -- disk ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("free", "level"), [(40, "critical"), (100, "warn"), (149, "warn"), (150, "ok")]
)
def test_disk_levels(layout: Layout, free: int, level: Level) -> None:
    found = row(look(layout, probe(disk=(free * GB, 1000 * GB))), "disk")
    assert found.level == level


def test_disk_unreadable(layout: Layout) -> None:
    assert row(look(layout, probe(disk=None)), "disk").level == "unknown"
    assert row(look(layout, probe(disk=(0, 0))), "disk").level == "unknown"


def test_disk_space_of_a_real_folder(tmp_path: Path) -> None:
    space = status.disk_space(tmp_path)
    assert space is not None
    assert space[1] >= space[0] >= 0
    assert status.disk_space(tmp_path / "missing") is None


# -- files the window may not read ---------------------------------------------------------------


def test_without_administrator_rights_the_rows_say_why(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data folder is for administrators only (ArciInstall.psm1): a window started without
    elevation shows "unknown" with the reason, and still the services, the API and the disk."""
    real_open = Path.open
    real_read = status.read_shared

    def denied(self: Path, *args: Any, **kwargs: Any) -> Any:
        if layout.state in self.parents:
            raise PermissionError(13, "Access is denied")
        return real_open(self, *args, **kwargs)

    def denied_read(path: Path) -> bytes:
        if layout.state in path.parents:
            raise PermissionError(13, "Access is denied")
        return real_read(path)

    def scandir(path: Any) -> Any:
        raise PermissionError(13, "Access is denied")

    # The status files go through read_shared (CreateFileW on Windows), the alerts through open().
    monkeypatch.setattr(status, "read_shared", denied_read)
    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(os, "scandir", scandir)
    snapshot = look(layout)
    for key in ("flow", "database", "seal", "alerts"):
        assert (row(snapshot, key).level, row(snapshot, key).text_tr) == (
            "unknown",
            status.NO_ACCESS_TR,
        )
    assert row(snapshot, "service:ArciCollector").level == "ok"  # the SCM's word stays
    assert row(snapshot, "disk").level == "ok"
    assert snapshot.level == "warn"


def test_read_shared(tmp_path: Path) -> None:
    """Windows: CreateFileW with delete sharing; elsewhere a plain read. Both map a missing
    file to FileNotFoundError, which the rows read as "no information yet"."""
    path = tmp_path / "worker-status.json"
    path.write_bytes(b'{"updated_at": null}')
    assert status.read_shared(path) == b'{"updated_at": null}'
    with pytest.raises(FileNotFoundError):
        status.read_shared(tmp_path / "missing.json")


def test_damaged_files_are_unknown(layout: Layout) -> None:
    layout.collector_status.write_text("{not json", encoding="utf-8")
    layout.ingest_status.write_text("[1, 2]", encoding="utf-8")
    snapshot = look(layout)
    assert row(snapshot, "flow").text_en == "unknown: unreadable file"
    assert row(snapshot, "database").level == "unknown"
    assert row(snapshot, "service:ArciCollector").level == "ok"


def test_a_damaged_seal_state_is_skipped(layout: Layout) -> None:
    (layout.state / "seal" / "2026-11-02.json").write_text("{", encoding="utf-8")
    assert row(look(layout), "seal").level == "ok"


def test_unlistable_or_unreadable_folders(layout: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Any) -> Any:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(os, "scandir", broken)
    assert row(look(layout), "seal").text_en == "unknown: unreadable file"
    monkeypatch.undo()
    layout.alerts.mkdir()
    assert row(look(layout), "alerts").level == "unknown"


def test_no_data_folder(tmp_path: Path) -> None:
    snapshot = status.collect(Site(data_root=None, health_url="http://x/"), probe())
    assert [r.key for r in snapshot.checks] == ["data"]
    assert snapshot.level == "warn"


def test_a_configuration_problem_is_shown(layout: Layout) -> None:
    snapshot = look(layout, problem="data_dir is not the installation's folder")
    assert row(snapshot, "config").text_tr == "okunamadı: data_dir is not the installation's folder"
    assert snapshot.level == "warn"


# -- configuration and the API -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "url"),
    [
        ("127.0.0.1", "http://127.0.0.1:8000/api/v1/health"),
        ("0.0.0.0", "http://127.0.0.1:8000/api/v1/health"),  # noqa: S104 - a text, never bound
        ("::", "http://127.0.0.1:8000/api/v1/health"),
        ("::1", "http://[::1]:8000/api/v1/health"),
        ("localhost", "http://localhost:8000/api/v1/health"),
    ],
)
def test_health_url(host: str, url: str) -> None:
    assert status.health_url(host, 8000) == url


def test_load_site(tmp_path: Path) -> None:
    data = tmp_path / "data"
    config = tmp_path / "arcilogger.toml"
    config.write_text(
        f'data_dir = "{data.as_posix()}"\n[api]\nport = 8123\n[seal]\ngap_minutes = 30\n',
        encoding="utf-8",
    )
    found = status.load_site(config)
    assert found.data_root == data
    assert found.health_url == "http://127.0.0.1:8123/api/v1/health"
    assert (found.gap_minutes, found.late_after_hours, found.problem) == (30, 48, None)


def test_load_site_watches_the_enabled_expected_sources(tmp_path: Path) -> None:
    config = tmp_path / "arcilogger.toml"
    config.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        "[seal]\nfirst_day = 2026-10-01\n"
        '[[sources]]\nname = "fw-ornek"\ntype = "fortigate"\naddress = "172.20.0.1"\n'
        '[[sources]]\nname = "ap-ornek"\ntype = "generic"\naddress = "172.20.10.0/24"\n'
        '[[sources]]\nname = "off-ornek"\ntype = "generic"\naddress = "172.20.0.9"\n'
        "enabled = false\n"
        '[[sources]]\nname = "lab-ornek"\ntype = "generic"\naddress = "172.20.0.8"\n'
        "expected = false\n",
        encoding="utf-8",
    )
    found = status.load_site(config)
    assert found.configured
    assert found.first_day == date(2026, 10, 1)
    assert found.sources == (status.Watched("fw-ornek"), status.Watched("ap-ornek", True))
    assert status.Watched("ap-ornek", True).matches("ap-ornek-172.20.10.5")
    assert not status.Watched("fw-ornek").matches("fw-ornek-172.20.0.1")


def test_load_site_without_rights_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(path: Path | None = None) -> Any:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(status, "load_config", denied)
    found = status.load_site(tmp_path / "arcilogger.toml")
    assert found.problem == status.NO_ACCESS_TR
    assert not found.configured


def test_load_site_without_a_usable_configuration(tmp_path: Path) -> None:
    config = tmp_path / "arcilogger.toml"
    config.write_text("data_dir = [", encoding="utf-8")
    (tmp_path / "data-root.txt").write_text(str(tmp_path / "data") + "\n", encoding="utf-8")
    found = status.load_site(config)
    assert found.data_root == tmp_path / "data"
    assert found.health_url.endswith(":8000/api/v1/health")
    assert found.problem
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert status.load_site(elsewhere / "arcilogger.toml").data_root is None


def test_load_site_refuses_a_moved_data_folder(tmp_path: Path) -> None:
    config = tmp_path / "arcilogger.toml"
    config.write_text(f'data_dir = "{(tmp_path / "elsewhere").as_posix()}"\n', encoding="utf-8")
    (tmp_path / "data-root.txt").write_text(str(tmp_path / "data") + "\n", encoding="utf-8")
    found = status.load_site(config)
    assert found.data_root == tmp_path / "data"
    assert found.problem is not None
    assert "data_dir" in found.problem


class _Health(http.server.BaseHTTPRequestHandler):
    body = b""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, format: str, *args: Any) -> None:
        return None


@pytest.fixture
def health_server() -> Iterator[tuple[str, type[_Health]]]:
    handler = type("Handler", (_Health,), {})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1/health", handler
    finally:
        server.shutdown()
        server.server_close()


def test_http_health(health_server: tuple[str, type[_Health]]) -> None:
    url, handler = health_server
    handler.body = json.dumps({"status": "ok", "version": "1.2.3"}).encode()
    assert status.http_health(url) == "1.2.3"
    handler.body = json.dumps({"status": "starting"}).encode()
    assert status.http_health(url) is None
    handler.body = b"not json"
    assert status.http_health(url) is None


def test_http_health_unreachable() -> None:
    assert status.http_health("http://127.0.0.1:9/api/v1/health") is None


# -- formatting ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "tr", "en"),
    [
        (-5, "0 sn", "0 s"),
        (89, "89 sn", "89 s"),
        (600, "10 dk", "10 min"),
        (3 * 3600, "3 sa", "3 h"),
        (86400 * 3, "3 gün", "3 days"),
        (86400 * 2, "2 gün", "2 days"),
    ],
)
def test_ago(seconds: int, tr: str, en: str) -> None:
    assert status.ago(timedelta(seconds=seconds)) == (tr, en)


def test_size() -> None:
    assert status.size(512) == "512 B"
    assert status.size(1536) == "1.5 KB"
    assert status.size(412 * GB) == "412.0 GB"
    assert status.size(3 * 1024 * GB) == "3.0 TB"


# -- the entry point and the non-Windows calls ---------------------------------------------------


def test_print(tmp_path: Path, layout: Layout, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "arcilogger.toml"
    config.write_text(f'data_dir = "{layout.root.as_posix()}"\n[api]\nport = 9\n', encoding="utf-8")
    assert entry.main(["--print", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ARCILOGGER ")
    assert "API · ArciApi / API:" in out


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-ins of other platforms")
def test_other_platforms_ask_nothing() -> None:
    assert winapi.service_states(["ArciApi"]) == {"ArciApi": "unknown"}
    assert winapi.is_elevated()
    assert not winapi.relaunch_elevated(["-I"])
    guard = winapi.single_instance(entry.MUTEX)
    assert guard is not None
    winapi.release(guard)
    assert not winapi.bring_to_front(status.TITLE)
    winapi.flash(1)
    winapi.dpi_aware()


def _entry_patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    elevated: bool,
    relaunched: bool = True,
    instances: Sequence[object | None] = (1,),
) -> SimpleNamespace:
    calls = SimpleNamespace(relaunch=[], front=[], released=[], run=[])
    queue = list(instances)
    monkeypatch.setattr(winapi, "single_instance", lambda name: queue.pop(0))
    monkeypatch.setattr(winapi, "is_elevated", lambda: elevated)
    monkeypatch.setattr(winapi, "release", calls.released.append)
    monkeypatch.setattr(winapi, "bring_to_front", lambda title: calls.front.append(title) or True)
    monkeypatch.setattr(winapi, "dpi_aware", lambda: None)

    def relaunch(arguments: Sequence[str]) -> bool:
        calls.relaunch.append(list(arguments))
        return relaunched

    monkeypatch.setattr(winapi, "relaunch_elevated", relaunch)
    fake_window = SimpleNamespace(run=lambda refresh: calls.run.append(refresh) or 0)
    import arcilogger_core.desktop as package

    monkeypatch.setitem(sys.modules, "arcilogger_core.desktop.window", fake_window)
    monkeypatch.setattr(package, "window", fake_window, raising=False)
    return calls


def test_a_second_start_shows_the_first_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _entry_patch(monkeypatch, elevated=True, instances=(None,))
    assert entry.main(["--config", str(tmp_path / "none.toml")]) == 0
    assert calls.front == [status.TITLE]
    assert calls.run == []


def test_a_second_start_without_rights_shows_the_window_through_an_elevated_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _entry_patch(monkeypatch, elevated=False, instances=(None,))
    assert entry.main(["--config", str(tmp_path / "none.toml")]) == 0
    assert len(calls.relaunch) == 1
    assert calls.front == []
    calls = _entry_patch(monkeypatch, elevated=False, relaunched=False, instances=(None,))
    assert entry.main(["--config", str(tmp_path / "none.toml")]) == 0
    assert calls.front == [status.TITLE]  # UAC declined: try anyway


def test_without_rights_it_starts_again_elevated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _entry_patch(monkeypatch, elevated=False, instances=(7,))
    config = tmp_path / "none.toml"
    assert entry.main(["--config", str(config)]) == 0
    assert calls.released == [7]
    assert calls.relaunch == [
        ["-I", "-m", "arcilogger_core.desktop", "--no-elevate", "--config", str(config)]
    ]
    assert calls.run == []


def test_declined_elevation_goes_on_with_less(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _entry_patch(monkeypatch, elevated=False, relaunched=False, instances=(7, 8))
    assert entry.main(["--config", str(tmp_path / "none.toml")]) == 0
    assert len(calls.run) == 1
    snapshot = calls.run[0]()
    assert isinstance(snapshot, Snapshot)
    calls = _entry_patch(monkeypatch, elevated=False, relaunched=False, instances=(7, None))
    assert entry.main(["--config", str(tmp_path / "none.toml")]) == 0
    assert calls.run == []


def test_no_elevate_and_elevated_open_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _entry_patch(monkeypatch, elevated=False)
    assert entry.main(["--no-elevate", "--config", str(tmp_path / "none.toml")]) == 0
    assert calls.relaunch == []
    assert len(calls.run) == 1
    calls = _entry_patch(monkeypatch, elevated=True)
    assert entry.main(["--config", str(tmp_path / "none.toml")]) == 0
    assert calls.relaunch == []
    assert len(calls.run) == 1


def test_self_test_flag_runs_the_window_check(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_window = SimpleNamespace(self_test=lambda: 0)
    import arcilogger_core.desktop as package

    monkeypatch.setitem(sys.modules, "arcilogger_core.desktop.window", fake_window)
    monkeypatch.setattr(package, "window", fake_window, raising=False)
    assert entry.main(["--self-test"]) == 0


def test_tk_start_is_tried_again_only_for_unreadable_library_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy Windows runner sometimes fails Tk's start-up on one of its own files (another file
    each time); a missing display or any other start-up error is never tried again."""
    tkinter = pytest.importorskip("tkinter", reason="this Python has no tkinter")
    from arcilogger_core.desktop import window

    unreadable = 'couldn\'t read file "C:/Python/tcl/tk8.6/icons.tcl": no such file or directory'
    calls: list[int] = []
    ROOT = object()  # noqa: N806 - stands for the Tk root

    def flaky() -> object:
        calls.append(1)
        if len(calls) < 3:
            raise tkinter.TclError(unreadable)
        return ROOT

    monkeypatch.setattr(tkinter, "Tk", flaky)
    monkeypatch.setattr("arcilogger_core.desktop.window.time.sleep", lambda seconds: None)
    assert window.new_root() is ROOT
    assert len(calls) == 3

    calls.clear()

    def never() -> object:
        calls.append(1)
        raise tkinter.TclError(unreadable)

    monkeypatch.setattr(tkinter, "Tk", never)
    with pytest.raises(tkinter.TclError):
        window.new_root()
    assert len(calls) == window.TK_ATTEMPTS

    calls.clear()

    def no_display() -> object:
        calls.append(1)
        raise tkinter.TclError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(tkinter, "Tk", no_display)
    with pytest.raises(tkinter.TclError):
        window.new_root()
    assert len(calls) == 1
