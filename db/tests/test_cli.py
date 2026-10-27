"""``arcilogger-db`` with a real TOML configuration and the encrypted secret store."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from arcilogger_core.config import load_config
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db import __version__
from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.cli import main
from arcilogger_db.engine import make_engine
from arcilogger_db.migrate import head_revisions
from arcilogger_db.models import AuditLog, IngestOffset, Message, Setting
from arcilogger_db.partitions import add_months, month_of, months_back_for, partition_name
from arcilogger_db.searchcounts import count_key, hour_of, upsert_counts
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import TestServer

CONFIG = """
data_dir = "{data}"

[database]
host = "{host}"
port = {port}
name = "{name}"
user = "{user}"
connect_timeout_seconds = 5
"""


@pytest.fixture
def setup(pg_server: TestServer, tmp_path: Path) -> Iterator[Path]:
    """Config file + secret store for a brand-new, empty database."""
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    url = pg_server.admin_url
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(),
            host=url.host or "127.0.0.1",
            port=url.port or 5432,
            name=name,
            user=url.username,
        ),
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    try:
        yield path
    finally:
        pg_server.drop_database(name)


def test_check_upgrade_current_on_an_empty_database(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    assert main(["check", "--config", config]) == 1  # reachable, but no schema yet
    report = json.loads(capsys.readouterr().out)
    assert report["encoding"] == "UTF8"
    assert report["revision"] == []
    assert any("arcilogger-db upgrade" in w for w in report["warnings"])

    (head,) = head_revisions()
    assert main(["upgrade", "--config", config]) == 0
    out = capsys.readouterr().out
    assert f"boş veritabanı → {head}" in out
    assert f"Başlangıç verisi: {len(DEFAULT_SETTINGS)} ayar eklendi, 0 ayar güncellendi\n" in out
    assert "VLAN" not in out  # no network is shipped (the site's administrator enters it)
    assert "TimescaleDB:" in out

    assert main(["--config", config, "upgrade"]) == 0  # option before the command, idempotent
    assert f"{head} → {head}" in capsys.readouterr().out

    assert main(["current", "--config", config]) == 0
    assert head in capsys.readouterr().out

    status = main(["check", "--config", config])
    report = json.loads(capsys.readouterr().out)
    assert report["up_to_date"] is True
    assert report["timescale"]["mode"] == "auto"
    assert status == (1 if report["warnings"] else 0)
    partitioned = report["timescale"]["storage"] == "partitions"
    if partitioned:
        assert report["partitions"]["tables"] == 16
        assert report["partitions"]["default_rows"] == []
        assert not any("bölüm" in w for w in report["warnings"])
    # Only these fields are reported; the password is not among them.
    assert set(report) - {"partitions"} == {
        "host",
        "port",
        "database",
        "user",
        "server_version",
        "encoding",
        "timescale",
        "timescale_telemetry",
        "revision",
        "head",
        "up_to_date",
        "warnings",
    }


def test_partitions_command_and_missing_horizon_warning(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    assert main(["upgrade", "--config", config]) == 0
    out = capsys.readouterr().out
    assert main(["partitions", "--config", config]) == 0
    second = capsys.readouterr().out
    if "Aylık bölümler" not in out:  # TimescaleDB: hypertables, nothing to partition
        assert "TimescaleDB hypertable" in second
        return
    back = months_back_for(site_day(utc_now()), 400)  # the db.retention_days window
    assert f"16 tablo, {16 * (back + 1 + 3)} yeni bölüm" in out  # … + current + 3 ahead
    assert "16 tablo, 0 yeni bölüm" in second  # idempotent
    # Without next month's partitions `check` asks for attention.
    engine = make_engine(load_config(setup))
    future = [add_months(month_of(site_day(utc_now())), n) for n in (1, 2, 3)]
    with engine.begin() as connection:
        for month in future:
            connection.execute(text(f'DROP TABLE "{partition_name("mt_dhcp", month)}"'))
    engine.dispose()
    assert main(["check", "--config", config]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("arcilogger-db partitions" in w for w in report["warnings"])
    assert main(["partitions", "--config", config]) == 0
    assert "16 tablo, 3 yeni bölüm" in capsys.readouterr().out


def test_check_warns_when_timescaledb_is_installed_but_unused(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.g. shared_preload_libraries was missing at the first upgrade (installer item)."""
    config = str(setup)
    engine = make_engine(load_config(setup))
    try:
        with engine.connect() as connection:
            available = connection.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            ).scalar()
        if available is None:
            pytest.skip("needs a server with TimescaleDB (CI Linux job)")
        assert main(["upgrade", "--config", config]) == 0
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE settings SET value = value || CAST(:patch AS jsonb) "
                    "WHERE key = 'db.timescale'"
                ),
                {"patch": '{"enabled": false, "reason": "CREATE EXTENSION timescaledb failed"}'},
            )
    finally:
        engine.dispose()
    capsys.readouterr()
    assert main(["check", "--config", config]) == 1
    warnings = json.loads(capsys.readouterr().out)["warnings"]
    assert any("TimescaleDB kurulu ama" in w for w in warnings)


def test_unreachable_database_exit_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(), host="127.0.0.1", port=1, name="x", user="x"
        ),
        encoding="utf-8",
    )
    for command in ("upgrade", "current", "check", "partitions"):
        assert main([command, "--config", str(path)]) == 3
        assert "Veritabanına bağlanılamadı" in capsys.readouterr().err


def test_configuration_errors_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "--config", str(tmp_path / "missing.toml")]) == 2
    bad = tmp_path / "bad.toml"
    bad.write_text('data_dir = "x"\n[database]\nport = 0\n', encoding="utf-8")
    assert main(["check", "--config", str(bad)]) == 2
    assert "Yapılandırma okunamadı" in capsys.readouterr().err
    assert main([]) == 2


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"ARCILOGGER db {__version__}"


def test_module_entry_point_runs() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "arcilogger_db", "version"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0
    assert completed.stdout.decode("utf-8").strip() == f"ARCILOGGER db {__version__}"


# -- settings: the supported, audited way to change a database setting ----------------------------


def _settings_state(path: Path, key: str) -> tuple[Any, str, list[AuditLog]]:
    engine = make_engine(load_config(path))
    try:
        with Session(engine) as session:
            row = session.get(Setting, key)
            assert row is not None
            audit = list(
                session.scalars(
                    select(AuditLog)
                    .where(AuditLog.action == "settings.change")
                    .order_by(AuditLog.id)
                )
            )
            assert verify_audit_chain(session).ok
            return row.value, row.updated_by, audit
    finally:
        engine.dispose()


def test_settings_set_is_validated_audited_and_kept_by_upgrades(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    assert main(["upgrade", "--config", config]) == 0
    capsys.readouterr()
    key = "devices.infrastructure_macs"
    assert main(["settings", "get", key, "--config", config]) == 0
    assert capsys.readouterr().out.strip() == "[]"

    args = ["settings", "set", key, '["00-00-5E-00-53-32"]', "--by", "tolga", "--config", config]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "eski: []" in out
    assert 'yeni: ["00:00:5e:00:53:32"]' in out  # normalised
    value, author, audit = _settings_state(setup, key)
    assert (value, author) == (["00:00:5e:00:53:32"], "tolga")
    (entry,) = audit
    assert (entry.actor_username, entry.actor_id, entry.target) == ("tolga", None, f"setting:{key}")
    assert entry.details["old"] == []
    assert entry.details["new"] == ["00:00:5e:00:53:32"]
    assert entry.details["via"] == "arcilogger-db"

    # The next upgrade (install, repair, update) keeps the administrator's value.
    assert main(["upgrade", "--config", config]) == 0
    assert "0 ayar güncellendi" in capsys.readouterr().out
    assert _settings_state(setup, key)[:2] == (["00:00:5e:00:53:32"], "tolga")

    assert main(["settings", "list", "--config", config]) == 0
    listing = capsys.readouterr().out
    assert f'{key} = ["00:00:5e:00:53:32"]  (tolga)' in listing
    assert "nat.public_ips = []  (:seed)" in listing
    assert "audit.spool_watermark" not in listing  # internal keys are not settings


def test_settings_set_accepts_a_bare_string_and_stdin(
    setup: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = str(setup)
    assert main(["upgrade", "--config", config]) == 0
    # Windows PowerShell 5.1 drops the JSON quotes of a native argument: 100.64.12.9 is a string.
    assert main(["settings", "set", "nat.wan_transip", "100.64.12.9", "--by", "tolga",
                 "--config", config]) == 0  # fmt: skip
    assert _settings_state(setup, "nat.wan_transip")[0] == "100.64.12.9"
    monkeypatch.setattr("sys.stdin", io.StringIO('["203.0.113.200"]\n'))
    assert main(["settings", "set", "nat.public_ips", "-", "--by", "tolga", "--config",
                 config]) == 0  # fmt: skip
    value, _author, audit = _settings_state(setup, "nat.public_ips")
    assert value == ["203.0.113.200"]
    assert [e.details["new"] for e in audit] == ["100.64.12.9", ["203.0.113.200"]]


def test_settings_set_takes_the_fortigate_and_nat_keys_normalised(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0016: the new keys go through the same validation, normalisation and audit."""
    config = str(setup)
    assert main(["upgrade", "--config", config]) == 0
    capsys.readouterr()
    changes = {
        "dhcp.fortigate_dhcp_msgs": (
            '{"0100026001": ["Ack", "ACK"], "0100026002": ["Release"]}',
            {"0100026001": ["Ack"], "0100026002": ["Release"]},
        ),
        "dhcp.fortigate_logids": ("{}", {}),
        "lookup.srcmac_interfaces": ('["vlan42", "vlan42", "port 1"]', ["vlan42", "port 1"]),
        "lookup.srcmac_max_ips": ("2", 2),
        "nat.snat_trandisp": ('["snat", "snat"]', ["snat"]),
    }
    for key, (given, _stored) in changes.items():
        assert main(["settings", "set", key, given, "--by", "tolga", "--config", config]) == 0
    for key, (_text, stored) in changes.items():
        value, author, audit = _settings_state(setup, key)
        assert (value, author) == (stored, "tolga")
        assert [e.details["new"] for e in audit if e.target == f"setting:{key}"] == [stored]
    assert main(["settings", "list", "--config", config]) == 0
    listing = capsys.readouterr().out
    assert "dhcp.fortigate_logids = {}  (tolga)" in listing
    assert "dhcp.fortigate_format_verified = false  (:seed)" in listing
    assert "nat.egresses = {}  (:seed)" in listing


@pytest.mark.parametrize(
    ("key", "value", "by", "message"),
    [
        ("no.such.key", "1", "tolga", "bilinmeyen ayar"),
        ("seed.network_version", "2", "tolga", "bilinmeyen ayar"),  # internal key
        ("audit.spool_watermark", "0", "tolga", "bilinmeyen ayar"),
        ("lease.timeout_percent", "1.5", "tolga", "tam sayı bekleniyor"),  # float
        ("devices.infrastructure_macs", '"x"', "tolga", "liste bekleniyor"),
        ("devices.infrastructure_macs", '["not-a-mac"]', "tolga", "geçersiz MAC"),
        ("nat.public_ips", "[1.2.3.4]", "tolga", "geçerli JSON değil"),
        ("nat.public_ips", '["999.1.1.1"]', "tolga", "geçersiz IP"),
        ("lookup.port_hint", "1", "tolga", "true/false bekleniyor"),
        ("lookup.tolerance_seconds", "true", "tolga", "tam sayı bekleniyor"),
        ("lookup.tolerance_seconds", "7200", "tolga", "en çok 3600"),
        ("auth.totp_required_roles", '["root"]', "tolga", "bilinmeyen rol"),
        ("auth.totp_required_roles", "[]", "tolga", "zorunludur"),
        ("auth.totp_required_roles", '["auditor"]', "tolga", "admin rolü için"),
        ("auth.lockout_attempts", "1000000", "tolga", "en çok 20"),
        ("auth.idle_minutes", "525600", "tolga", "en çok 120"),
        ("dhcp.server_vlan_regex", '"^dhcp$"', "tolga", "ilk grup"),
        # ADR-0016 keys (FortiGate DHCP evidence, srcmac, internet exits).
        ("dhcp.fortigate_logids", '{"26001": "assigned"}', "tolga", "logid 10 rakam"),
        ("dhcp.fortigate_dhcp_msgs", '{"0100026001": []}', "tolga", "ileti türünden"),
        ("dhcp.fortigate_format_verified", '"yes"', "tolga", "true/false bekleniyor"),
        ("dhcp.fortigate_interfaces_excluded", '["ipsec "]', "tolga", "geçersiz arayüz adı"),
        ("lookup.srcmac_interfaces", '"vlan42"', "tolga", "liste bekleniyor"),
        ("lookup.srcmac_max_ips", "17", "tolga", "en çok 16"),
        ("nat.snat_trandisp", "[]", "tolga", "küçük harfle başlayan"),
        ("nat.egresses", "[]", "tolga", "nesne bekleniyor"),
        ("auth.idle_minutes", "5", ":seed", "--by"),
        ("auth.idle_minutes", "5", " ", "--by"),
    ],
)
def test_settings_set_refuses_bad_input_and_changes_nothing(
    setup: Path,
    capsys: pytest.CaptureFixture[str],
    key: str,
    value: str,
    by: str,
    message: str,
) -> None:
    config = str(setup)
    assert main(["upgrade", "--config", config]) == 0
    before = _settings_state(setup, "auth.idle_minutes")
    capsys.readouterr()
    assert main(["settings", "set", key, value, "--by", by, "--config", config]) == 2
    err = capsys.readouterr().err
    assert "Ayar değiştirilmedi" in err
    assert message in err
    assert _settings_state(setup, "auth.idle_minutes") == before
    assert before[2] == []  # no audit entry was written


def test_settings_commands_need_a_reachable_database_and_a_known_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(), host="127.0.0.1", port=1, name="x", user="x"
        ),
        encoding="utf-8",
    )
    assert main(["settings", "list", "--config", str(path)]) == 3
    assert main(["settings", "get", "no.such.key", "--config", str(path)]) == 3
    assert main(["settings", "set", "no.such.key", "1", "--by", "a", "--config", str(path)]) == 2
    assert main(["settings", "--config", str(path)]) == 2  # no action: help
    capsys.readouterr()


# -- search-counts: the search summary table (ADR-0015) -------------------------------------------


def _half_counted_file(path: Path, source: str, day: date, lines: int) -> None:
    """A file as an upgrade leaves it while ingest goes on: ``lines`` rows of the previous full
    hour, its ``ingest_offsets`` row with ``counts_from_line = 3`` and the counts of lines 3 on."""
    hour = hour_of(utc_now()) - timedelta(hours=1)
    rows = [
        {
            "time": hour + timedelta(minutes=5 * number),
            "time_received": hour + timedelta(minutes=5 * number),
            "source": source,
            "sender": "172.31.255.1",
            "raw_day": day,
            "raw_line": number,
            "raw_offset": 100 * (number - 1),
            "type": "traffic",
            "subtype": "forward",
            "text": f"line {number}",
        }
        for number in range(1, lines + 1)
    ]
    engine = make_engine(load_config(path))
    try:
        with engine.begin() as connection:
            connection.execute(insert(Message), rows)
            connection.execute(
                insert(IngestOffset).values(
                    source=source,
                    day=day,
                    byte_offset=100 * len(rows),
                    line_number=len(rows),
                    counts_from_line=3,
                )
            )
            upsert_counts(connection, Counter(count_key(row, None, None) for row in rows[2:]))
    finally:
        engine.dispose()


def _sql(path: Path, statement: str) -> Any:
    engine = make_engine(load_config(path))
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement))
            return result.scalar() if result.returns_rows else None
    finally:
        engine.dispose()


def test_search_counts_status_rebuild_and_verify(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    assert main(["upgrade", "--config", config]) == 0
    day = site_day(utc_now())
    _half_counted_file(setup, "fortigate", day, 4)
    capsys.readouterr()

    assert main(["search-counts", "status", "--config", config]) == 0
    out = capsys.readouterr().out
    assert "1 anahtar [keys], 2 satır [lines]" in out
    assert "Sayılmayı bekleyen dosyalar [files waiting to be counted]: 1 (0 bitmiş" in out
    assert f"fortigate {day}: ilk 2 satır sayılmadı [lines not counted], yükleniyor" in out

    # The file waiting for its recount is left out of the comparison.
    assert main(["search-counts", "verify", "--config", config]) == 0
    out = capsys.readouterr().out
    assert "Henüz sayılmamış 1 dosya karşılaştırılmadı [files not counted yet were left out]" in out
    assert f"left out]: fortigate {day}\n" in out
    assert "fg_dns.qname 0, fg_web.hostname 0" in out

    args = ["search-counts", "rebuild", "--source", "fortigate", "--day", day.isoformat()]
    assert main([*args, "--config", config]) == 0
    out = capsys.readouterr().out
    assert f"fortigate {day}: 4 satır yeniden sayıldı [lines recounted], 1 anahtar [keys]" in out
    assert "Yükseltmeden önce yüklenen satırlar da sayıldı" in out
    assert _sql(setup, "SELECT counts_from_line FROM ingest_offsets") is None
    assert _sql(setup, "SELECT sum(n) FROM search_counts") == 4

    assert main(["search-counts", "status", "--config", config]) == 0
    assert "Sayılmayı bekleyen dosya yok" in capsys.readouterr().out
    assert main(["search-counts", "verify", "--hours", "2", "--config", config]) == 0
    out = capsys.readouterr().out
    assert "1 anahtar [keys], 4 satır [lines]" in out
    assert "Özet tablosu satırlarla aynı [the summary table matches the rows]." in out

    _sql(setup, "UPDATE search_counts SET n = n + 1")
    assert main(["search-counts", "verify", "--config", config]) == 1
    captured = capsys.readouterr()
    assert "FARK [difference]: 1 anahtar" in captured.err
    assert "satırlar [rows] 4, tablo [table] 5" in captured.err
    assert f"arcilogger-db search-counts rebuild --source fortigate --day {day}" in captured.err
    assert main([*args, "--config", config]) == 0  # the repair it names
    assert "Yükseltmeden" not in capsys.readouterr().out
    assert main(["search-counts", "verify", "--config", config]) == 0


def test_search_counts_refuses_bad_input_and_needs_the_database(
    setup: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    bad_day = ["search-counts", "rebuild", "--source", "fortigate", "--day", "2026-13-01"]
    assert main([*bad_day, "--config", config]) == 2
    assert "Geçersiz gün [invalid day]: 2026-13-01" in capsys.readouterr().err
    for hours in ("-1", "745"):
        assert main(["search-counts", "verify", "--hours", hours, "--config", config]) == 2
        assert "--hours 0 ile 744 arasında olmalı" in capsys.readouterr().err
    assert main(["search-counts", "--config", config]) == 2  # no action: help
    path = tmp_path / "unreachable.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(), host="127.0.0.1", port=1, name="x", user="x"
        ),
        encoding="utf-8",
    )
    day = ["--source", "fortigate", "--day", "2026-10-25"]
    for action in (["status"], ["rebuild", *day], ["verify"]):
        assert main(["search-counts", *action, "--config", str(path)]) == 3
        assert "Veritabanına bağlanılamadı" in capsys.readouterr().err
