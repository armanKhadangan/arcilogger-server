"""``arcilogger-db`` command line (the installer runs ``arcilogger-db upgrade``)::

    arcilogger-db upgrade    [--config PATH]   migrate to the newest schema, seed, partitions
    arcilogger-db current    [--config PATH]   current and newest revision
    arcilogger-db check      [--config PATH]   connectivity, encoding, schema, TimescaleDB,
                                               partitions
    arcilogger-db partitions [--config PATH]   create missing monthly partitions (plain
                                               PostgreSQL; the worker also does it daily)
    arcilogger-db settings list              [--config PATH]
    arcilogger-db settings get KEY           [--config PATH]
    arcilogger-db settings set KEY JSON_VALUE --by NAME [--config PATH]
                                             change a setting (validated, audited as
                                             settings.change; JSON_VALUE "-" reads stdin)
    arcilogger-db backup run     [--config PATH]   scheduled database backup (Backup task,
                                               ADR-0009): pg_dump, check, state, retention
    arcilogger-db backup list    [--json]          the scheduled dumps, newest first
    arcilogger-db backup verify  [FILE ...] [--all] [--deep]
                                               size and SHA-256 against the sidecar, pg_restore
    arcilogger-db backup prune   [--dry-run]       apply the retention ([backup] keep_*)
    arcilogger-db backup restore --file F --target-db NAME [--superuser postgres] [--sha256 HEX]
                                               into a NEW database; the superuser password is
                                               read from stdin (a prompt on a console);
                                               --sha256: the hash recorded off the server
    arcilogger-db license status       [--json]    the licence state (``GET /license`` document)
    arcilogger-db license request-code [--json]    this installation's request code (one line)
    arcilogger-db license verify FILE  [--json]    the checks of install, no write, no audit
    arcilogger-db license install FILE [--json]    install a licence file (audited)
    arcilogger-db license remove       [--yes]     remove the installed licence (audited)
    arcilogger-db version

``settings set`` is the supported way to change a database setting until the settings page
(M6): it validates the key and the value, writes ``updated_by = NAME`` (so ``upgrade`` never
puts the default back) and appends ``settings.change`` with the old and new value to the
hash-chained audit log (:mod:`arcilogger_db.settings_admin`). A string value may be given
without JSON quotes (``100.64.12.9``); Windows PowerShell 5.1 drops the inner double quotes of
native-command arguments, so pipe lists in: ``'["203.0.113.200"]' | arcilogger-db settings
set nat.public_ips - --by tolga``.

``license …`` (offline licensing, licensing contract 6.8) is the only command that loads the
licence code (:mod:`arcilogger_db.licensing`, imported inside its handler): ``upgrade``,
``backup`` and every other command never do. ``install``, ``remove`` and ``status`` write
``state/license-status.json`` for the installer; ``install``/``remove`` (and a refused
``install``) are licence events and audit entries with the actor ``os:<Windows user>``.

Exit codes: 0 success / up to date, 1 needs attention (not migrated, outdated, warnings; a
backup, check or prune that failed; a licence refused, none installed, or the licence code
unavailable), 2 usage or configuration error (a restore refused before any change), 3 the
database cannot be reached.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from arcilogger_core.canonical import CanonicalJsonError, canonical_json
from arcilogger_core.config import Config, default_config_path, load_config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.fmt import format_bytes, format_datetime
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db import __version__
from arcilogger_db.audit import append_audit
from arcilogger_db.backup import (
    BackupError,
    RestoreRefusedError,
    Verification,
    backup_dir,
    check_expected_sha256,
    check_restore_target,
    find_tool,
    last_state_path,
    list_backups,
    prune_backups,
    restore_backup,
    run_backup,
    verify_backup,
)
from arcilogger_db.engine import make_engine
from arcilogger_db.migrate import current_revisions, head_revisions, upgrade
from arcilogger_db.partitions import (
    add_months,
    ensure_partitions,
    month_of,
    partition_status,
)
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.settings_admin import change_setting, check_actor, list_settings

EXIT_OK = 0
EXIT_ATTENTION = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3


class _UnreachableError(Exception):
    pass


class _UsageError(Exception):
    pass


def _utf8_console() -> None:
    """Turkish text (ı, ş, ğ) must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _engine(config: Config) -> Engine:
    return make_engine(config, application="db-cli", pool_size=1)


def _describe(exc: SQLAlchemyError) -> str:
    """Driver message without SQL or parameters (they never contain the password anyway)."""
    original = getattr(exc, "orig", None)
    return str(original or exc).strip().splitlines()[0]


def cmd_upgrade(config: Config) -> int:
    engine = _engine(config)
    try:
        try:
            result = upgrade(engine, timescale=config.database.timescale)
        except OperationalError as exc:
            raise _UnreachableError(_describe(exc)) from exc
        with engine.connect() as connection:
            timescale = connection.execute(
                text("SELECT value FROM settings WHERE key = 'db.timescale'")
            ).scalar_one_or_none()
    finally:
        engine.dispose()
    before = ", ".join(result.before) or "boş veritabanı"
    print(f"Şema: {before} → {', '.join(result.after)}")
    seed = result.seed
    print(
        f"Başlangıç verisi: {len(seed.settings_inserted)} ayar eklendi, "
        f"{len(seed.settings_refreshed)} ayar güncellendi"
    )
    print(f"TimescaleDB: {_timescale_text(timescale)}")
    if result.partitions.tables:
        print(
            f"Aylık bölümler: {len(result.partitions.tables)} tablo, "
            f"{len(result.partitions.created)} yeni bölüm"
        )
    return EXIT_OK


def cmd_partitions(config: Config) -> int:
    engine = _engine(config)
    try:
        with engine.begin() as connection:
            report = ensure_partitions(connection)
            status = partition_status(connection)
    except OperationalError as exc:
        raise _UnreachableError(_describe(exc)) from exc
    finally:
        engine.dispose()
    if not report.tables:
        print("Bölümlenmiş tablo yok (TimescaleDB hypertable kullanılıyor).")
        return EXIT_OK
    print(f"Aylık bölümler: {len(report.tables)} tablo, {len(report.created)} yeni bölüm")
    for name, rows in sorted(report.moved.items()):
        print(f"  {name}: varsayılan bölümden {rows} satır taşındı")
    if status.covered_until is not None:
        print(f"Kapsanan son ay: {status.covered_until:%Y-%m}")
    return EXIT_OK


def _timescale_text(status: Any) -> str:
    if not isinstance(status, dict):
        return "bilinmiyor"
    if status.get("enabled"):
        compression = "sıkıştırma açık" if status.get("compression") else "sıkıştırma yok"
        return f"açık (sürüm {status.get('version')}, {compression})"
    storage = "aylık bölümler" if status.get("storage") == "partitions" else "düz tablolar"
    return f"kapalı ({status.get('reason') or '-'}; {storage})"


def cmd_current(config: Config) -> int:
    engine = _engine(config)
    try:
        with engine.connect() as connection:
            current = current_revisions(connection)
    except OperationalError as exc:
        raise _UnreachableError(_describe(exc)) from exc
    finally:
        engine.dispose()
    heads = head_revisions()
    print(f"Veritabanı: {', '.join(current) or '(şema yok)'}")
    print(f"En yeni   : {', '.join(heads)}")
    return EXIT_OK if current == heads else EXIT_ATTENTION


def cmd_check(config: Config) -> int:
    engine = _engine(config)
    report: dict[str, Any] = {
        "host": config.database.host,
        "port": config.database.port,
        "database": config.database.name,
        "user": config.database.user,
    }
    warnings: list[str] = []
    try:
        with engine.connect() as connection:
            report["server_version"] = connection.execute(text("SHOW server_version")).scalar()
            encoding = connection.execute(
                text(
                    "SELECT pg_encoding_to_char(encoding) FROM pg_database "
                    "WHERE datname = current_database()"
                )
            ).scalar()
            report["encoding"] = encoding
            if encoding != "UTF8":
                warnings.append(f"veritabanı kodlaması {encoding}, UTF8 olmalı")
            current = current_revisions(connection)
            report["timescale"] = None
            if current:
                report["timescale"] = connection.execute(
                    text("SELECT value FROM settings WHERE key = 'db.timescale'")
                ).scalar_one_or_none()
            telemetry = connection.execute(
                text("SELECT setting FROM pg_settings WHERE name = 'timescaledb.telemetry_level'")
            ).scalar_one_or_none()
            report["timescale_telemetry"] = telemetry
            if telemetry not in (None, "off"):
                warnings.append(
                    "TimescaleDB telemetrisi açık: postgresql.conf içinde "
                    "timescaledb.telemetry_level = off ayarlayın"
                )
            available = connection.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            ).scalar_one_or_none()
            timescale = report["timescale"]
            if (
                available
                and isinstance(timescale, dict)
                and not timescale.get("enabled")
                and timescale.get("mode") != "off"
            ):
                warnings.append(
                    "TimescaleDB kurulu ama bu veritabanı onu kullanmıyor "
                    f"({timescale.get('reason') or '-'}); shared_preload_libraries ayarını "
                    "ilk kurulumdan önce yapın veya [database] timescale = off yazın"
                )
            if current:
                partitions = partition_status(connection)
                if partitions.tables:
                    report["partitions"] = {
                        "tables": partitions.tables,
                        "covered_until": (
                            f"{partitions.covered_until:%Y-%m}"
                            if partitions.covered_until
                            else None
                        ),
                        "default_rows": sorted(
                            t for t, rows in partitions.default_rows.items() if rows
                        ),
                    }
                    next_month = add_months(month_of(site_day(utc_now())), 1)
                    if partitions.covered_until is None or partitions.covered_until < next_month:
                        warnings.append(
                            "gelecek ayın bölümleri yok: arcilogger-db partitions çalıştırın "
                            "(worker bunu her gün yapar)"
                        )
    except OperationalError as exc:
        raise _UnreachableError(_describe(exc)) from exc
    finally:
        engine.dispose()
    heads = head_revisions()
    report["revision"] = list(current)
    report["head"] = list(heads)
    report["up_to_date"] = current == heads
    if not current:
        warnings.append("şema yok: arcilogger-db upgrade çalıştırın")
    elif current != heads:
        warnings.append("şema güncel değil: arcilogger-db upgrade çalıştırın")
    report["warnings"] = warnings
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return EXIT_ATTENTION if warnings else EXIT_OK


def _json(value: Any) -> str:
    return canonical_json(value).decode("utf-8")


def _parse_value(key: str, raw: str) -> Any:
    """JSON_VALUE (``-``: read from stdin); a bare word is accepted for a string setting."""
    text_value = sys.stdin.read().strip() if raw == "-" else raw
    try:
        return json.loads(text_value)
    except json.JSONDecodeError as exc:
        if isinstance(DEFAULT_SETTINGS.get(key), str):
            return text_value
        raise _UsageError(f"{key}: değer geçerli JSON değil ({exc.msg}): {text_value!r}") from exc


def _known(key: str) -> str:
    if key not in DEFAULT_SETTINGS:
        raise _UsageError(f"bilinmeyen ayar: {key} (liste: arcilogger-db settings list)")
    return key


def _settings_list(session: Session, args: argparse.Namespace) -> list[str]:
    lines = []
    for row in list_settings(session):
        who = "varsayılan" if row.updated_by is None else row.updated_by
        lines.append(f"{row.key} = {_json(row.value)}  ({who})")
    return lines


def _settings_get(session: Session, args: argparse.Namespace) -> list[str]:
    key = _known(args.key)
    (row,) = [row for row in list_settings(session) if row.key == key]
    return [_json(row.value)]


def _settings_set(session: Session, args: argparse.Namespace) -> list[str]:
    key = _known(args.key)
    value = _parse_value(key, args.value)
    try:
        change = change_setting(
            session,
            key,
            value,
            actor=args.by,
            details={"via": "arcilogger-db", "os_user": getpass.getuser()},
        )
    except (ValueError, CanonicalJsonError) as exc:
        raise _UsageError(str(exc)) from exc
    lines = [
        f"Ayar değiştirildi: {change.key}",
        f"  eski: {_json(change.old)}",
        f"  yeni: {_json(change.new)}",
        f"  denetim kaydı: #{change.audit_id} (settings.change, {args.by.strip()})",
    ]
    if not change.changed:
        lines.append("  (değer aynıydı; değişiklik yine de denetim kaydına yazıldı)")
    return lines


SETTINGS_ACTIONS = {"list": _settings_list, "get": _settings_get, "set": _settings_set}


def cmd_settings(config: Config, args: argparse.Namespace) -> int:
    action = SETTINGS_ACTIONS[args.settings_command]
    if args.settings_command == "set":  # usage errors before connecting
        _known(args.key)
        try:
            check_actor(args.by)
        except ValueError as exc:
            raise _UsageError(str(exc)) from exc
    engine = _engine(config)
    try:
        # One READ COMMITTED transaction: the setting and its audit entry commit together.
        with engine.begin() as connection, Session(bind=connection) as session:
            lines = action(session, args)
            session.flush()
    except OperationalError as exc:
        raise _UnreachableError(_describe(exc)) from exc
    finally:
        engine.dispose()
    print("\n".join(lines))
    return EXIT_OK


# -- backup (ADR-0009) ----------------------------------------------------------------------------


def _backup_log(config: Config) -> logging.Handler | None:
    """``logs/backup.log``: one line per step (no password ever reaches a log message)."""
    try:
        logs = Layout(config.data_dir).logs
        logs.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(logs / "backup.log", encoding="utf-8")
    except OSError:
        return None  # e.g. an operator without write access to logs: the console is enough
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("arcilogger.backup")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False  # the console gets the Turkish/English lines printed below
    return handler


def _backup_run(config: Config, args: argparse.Namespace) -> int:
    if not config.backup.enabled:
        print(
            "Zamanlanmış veritabanı yedeği kapalı ([backup] enabled = false) "
            "[scheduled database backup is disabled]."
        )
        return EXIT_OK
    outcome = run_backup(config, anchors=TrustAnchors.of(args.config or default_config_path()))
    if not outcome.ok or outcome.file is None or outcome.size is None:
        print(
            f"Veritabanı yedeği alınamadı [database backup failed]: {outcome.error}",
            file=sys.stderr,
        )
        return EXIT_ATTENTION
    meta = outcome.sidecar
    print(f"Yedek [backup]: {outcome.file}")
    print(f"  Boyut [size]: {format_bytes(outcome.size)}, SHA-256 {outcome.sha256}")
    print(
        f"  pg_dump {meta.get('pg_dump_version')}, sunucu [server] {meta.get('server_version')}, "
        f"şema [schema] {meta.get('alembic_revision') or '-'}, "
        f"TimescaleDB {meta.get('timescaledb_version') or '-'}"
    )
    print(f"  Durum [state]: {last_state_path(Layout(config.data_dir))}")
    if outcome.pruned is not None:
        print(
            f"Saklama [retention]: {len(outcome.pruned.kept)} yedek tutuldu [kept], "
            f"{len(outcome.pruned.deleted)} silindi [deleted]"
        )
    if outcome.prune_error:
        print(
            f"Eski yedekler silinemedi [retention failed]: {outcome.prune_error}", file=sys.stderr
        )
        return EXIT_ATTENTION
    return EXIT_OK


def _read_last_state(config: Config) -> Any:
    try:
        return json.loads(last_state_path(Layout(config.data_dir)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _backup_list(config: Config, args: argparse.Namespace) -> int:
    folder = backup_dir(config)
    backups = list_backups(folder)
    last = _read_last_state(config)
    if args.json:
        rows = [
            {
                "file": backup.path.name,
                "taken": backup.taken.isoformat(),
                "size": backup.size,
                "sha256": (backup.sidecar or {}).get("sha256"),
                "alembic_revision": (backup.sidecar or {}).get("alembic_revision"),
                "sidecar": backup.sidecar is not None,
            }
            for backup in backups
        ]
        print(json.dumps({"dir": str(folder), "backups": rows, "last": last}, indent=2))
        return EXIT_OK
    print(f"Klasör [folder]: {folder}")
    for backup in backups:
        meta = backup.sidecar
        detail = (
            f"şema [schema] {meta.get('alembic_revision') or '-'}, "
            f"SHA-256 {str(meta.get('sha256'))[:16]}…"
            if meta
            else "yan dosya yok [no sidecar]"
        )
        print(
            f"  {backup.path.name}  {format_datetime(backup.taken)}  "
            f"{format_bytes(backup.size):>9}  {detail}"
        )
    total = sum(backup.size for backup in backups)
    print(f"{len(backups)} yedek [backups], toplam [total] {format_bytes(total)}")
    if isinstance(last, dict):
        state = "başarılı [ok]" if last.get("ok") else f"BAŞARISIZ [failed]: {last.get('error')}"
        print(f"Son deneme [last attempt]: {last.get('finished')} {state}")
    else:
        print("Son deneme yok [no attempt recorded] (state/backup/last.json)")
    return EXIT_OK


def _print_verification(check: Verification) -> None:
    state = "OK  " if check.ok else "HATA"
    entries = f" ({check.entries} nesne [objects])" if check.entries is not None else ""
    print(f"{state}  {check.path.name}{entries}")
    for problem in check.problems:
        print(f"      hata [problem]: {problem}")
    for note in check.notes:
        print(f"      not [note]: {note}")


def _backup_verify(config: Config, args: argparse.Namespace) -> int:
    files: list[Path] = list(args.files)
    if not files:
        backups = list_backups(backup_dir(config))
        files = [b.path for b in (backups if args.all else backups[:1])]
    if not files:
        print("Doğrulanacak yedek yok [no backup to verify].", file=sys.stderr)
        return EXIT_ATTENTION
    anchors = TrustAnchors.of(args.config or default_config_path())
    tool = find_tool("pg_restore", config.backup.pg_restore, anchors=anchors)
    checks = [verify_backup(path, tool, deep=args.deep) for path in files]
    for check in checks:
        _print_verification(check)
    return EXIT_OK if all(check.ok for check in checks) else EXIT_ATTENTION


def _backup_prune(config: Config, args: argparse.Namespace) -> int:
    folder = backup_dir(config)
    # As after a run, the last good run's dump stays whatever its name (a clock set back).
    last = _read_last_state(config)
    protect: set[Path] = set()
    if isinstance(last, dict) and last.get("ok") is True and isinstance(last.get("file"), str):
        protect.add(config.data_dir / last["file"])
    try:
        result = prune_backups(folder, config.backup, dry_run=args.dry_run, protect=protect)
    except OSError as exc:
        print(f"Eski yedekler silinemedi [retention failed]: {exc}", file=sys.stderr)
        return EXIT_ATTENTION
    verb = "silinecek [would delete]" if args.dry_run else "silindi [deleted]"
    for path in result.deleted + result.cleaned:
        print(f"  {verb}: {path.name}")
    print(
        f"Saklama [retention] (günlük/haftalık/aylık [daily/weekly/monthly] "
        f"{config.backup.keep_daily}/{config.backup.keep_weekly}/{config.backup.keep_monthly}): "
        f"{len(result.kept)} yedek tutuldu [kept], {len(result.deleted)} {verb}"
    )
    return EXIT_OK


def _read_password(user: str) -> str | None:
    """The superuser password: a prompt on a console, otherwise the first line of stdin."""
    if sys.stdin.isatty():
        value = getpass.getpass(f"PostgreSQL {user} parolası [password]: ")
    else:
        value = sys.stdin.readline()
    # A pipe from Windows PowerShell 5.1 may start with a UTF-8 BOM; never part of a password.
    return value.removeprefix("﻿").rstrip("\r\n") or None


def _backup_restore(config: Config, args: argparse.Namespace) -> int:
    try:  # before asking for the password
        name = check_restore_target(config, args.target_db)
        sha256 = check_expected_sha256(args.sha256)
    except RestoreRefusedError as exc:
        print(f"Geri yükleme reddedildi [restore refused]: {exc}", file=sys.stderr)
        return EXIT_USAGE
    password = _read_password(args.superuser)
    try:
        outcome = restore_backup(
            config,
            args.file,
            name,
            superuser=args.superuser,
            password=password,
            sha256=sha256,
            anchors=TrustAnchors.of(args.config or default_config_path()),
        )
    except RestoreRefusedError as exc:
        print(
            "Geri yükleme reddedildi, hiçbir şey değiştirilmedi [restore refused, nothing "
            f"changed]: {exc}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    except OperationalError as exc:
        raise _UnreachableError(_describe(exc)) from exc
    except BackupError as exc:
        print(f"Geri yükleme başarısız [restore failed]: {exc}", file=sys.stderr)
        return EXIT_ATTENTION
    config_path = args.config or default_config_path()
    timescale = ", TimescaleDB" if outcome.timescaledb else ""
    print(f"Geri yükleme tamam [restore finished]: {args.file.name} -> {outcome.database}")
    print(f"  {outcome.tables} tablo [tables], şema [schema] {outcome.revision or '-'}{timescale}")
    if outcome.sha256_given:
        checked = "--sha256 ile aynı [matches --sha256]"
    elif outcome.sidecar:
        checked = (
            "--sha256 verilmedi; yalnızca yan dosyayla, kazara bozulmaya karşı denetlendi [no "
            "--sha256: checked against the sidecar only, for accidental damage]"
        )
    else:
        checked = "--sha256 ve yan dosya yok, denetlenmedi [no --sha256, no sidecar: not checked]"
    print(f"  SHA-256 {outcome.sha256} ({checked})")
    if outcome.expected_revision and outcome.expected_revision != outcome.revision:
        print(f"  UYARI [warning]: yan dosyadaki şema [sidecar schema] {outcome.expected_revision}")
    print(
        f"  Canlı veritabanı [live database] {config.database.name} değiştirilmedi "
        "[was not changed]."
    )
    print("ARCILOGGER'ı bu veritabanına geçirmek için [to switch ARCILOGGER to it]:")
    print("  1. Stop-Service ArciWorker, ArciApi, ArciIngest")
    print(f'  2. {config_path}: [database] name = "{outcome.database}"')
    step = 3
    if (outcome.revision or "") != ",".join(head_revisions()):
        print(f'  {step}. & $db upgrade --config "{config_path}"   (şema eski [older schema])')
        step += 1
    print(f"  {step}. Start-Service ArciIngest, ArciApi, ArciWorker")
    print(
        "  Ingest, yedekten sonraki logları spool ve kasadan yeniden yükler [ingest loads the "
        "logs written after the backup again from the spool and the vault]: docs/YEDEKLEME.md "
        "bölüm 5."
    )
    return EXIT_OK


BACKUP_ACTIONS = {
    "run": _backup_run,
    "list": _backup_list,
    "verify": _backup_verify,
    "prune": _backup_prune,
    "restore": _backup_restore,
}


def cmd_backup(config: Config, args: argparse.Namespace) -> int:
    handler = _backup_log(config) if args.backup_command != "list" else None
    try:
        return BACKUP_ACTIONS[args.backup_command](config, args)
    except (BackupError, OSError) as exc:  # a tool or the folder (run records its own failures)
        print(f"Yedekleme hatası [backup error]: {exc}", file=sys.stderr)
        return EXIT_ATTENTION
    finally:
        if handler is not None:
            logger = logging.getLogger("arcilogger.backup")
            logger.removeHandler(handler)
            logger.propagate = True
            handler.close()


# -- license (offline licensing, contract 6.8) ---------------------------------------------------

#: A licence file larger than this is not read at all (the licence limit is 64 KiB).
LICENSE_READ_LIMIT = 16 * 1024 * 1024
STATE_NAMES = {
    "trial": ("Deneme sürümü", "trial"),
    "trial_expired": ("Lisanssız (deneme süresi doldu)", "unlicensed (trial over)"),
    "licensed": ("Lisanslı", "licensed"),
    "invalid": ("Lisans doğrulanamadı", "licence invalid"),
    "wrong_server": ("Lisans bu sunucu için değil", "licence not for this server"),
    "expired": ("Lisansın süresi doldu", "licence expired"),
    "version_not_covered": ("Bu sürüm bakım süresinin dışında", "version not covered"),
}


def _os_actor() -> tuple[str, str]:
    """(``os:<user>``, ``<user>``): who ran the command (event actor, audit ``os_user``)."""
    try:
        user = getpass.getuser()
    except Exception:  # no USERNAME/LOGNAME and no account database entry
        user = "unknown"
    return f"os:{user}", user


def _print_error(code: str, text_tr: str, text_en: str, details: Any, as_json: bool) -> int:
    if as_json:
        error = {
            "ok": False,
            "code": code,
            "message_tr": text_tr,
            "message_en": text_en,
            "details": details,
        }
        print(json.dumps(error, ensure_ascii=False))
    else:
        print(f"{text_tr}\n[{text_en}]", file=sys.stderr)
    return EXIT_ATTENTION


def _license_unavailable(as_json: bool) -> int:
    from arcilogger_db.license_guard import license_unavailable_texts

    text_tr, text_en = license_unavailable_texts()
    return _print_error("license_unavailable", text_tr, text_en, None, as_json)


def _refused(error: Any, as_json: bool) -> int:
    details = dict(error.details) or None
    return _print_error(error.code, error.message_tr, error.message_en, details, as_json)


def _read_license_file(path: Path) -> tuple[bytes | None, int]:
    """The file's bytes (``None`` above :data:`LICENSE_READ_LIMIT`) and its size."""
    size = path.stat().st_size
    if size > LICENSE_READ_LIMIT:
        return None, size
    data = path.read_bytes()
    return data, len(data)


def _status_lines(document: dict[str, Any]) -> list[str]:
    state = document["status"]
    state_tr, state_en = STATE_NAMES.get(state, (state, state))
    lines = [f"Lisans durumu [licence state]: {state_tr} [{state_en}]"]
    if document.get("problem"):
        lines.append(f"  Sorun [problem]: {document['problem']}")
    trial = document.get("trial")
    if trial is not None:
        lines.append(
            f"  Deneme [trial]: {trial['started_at']} → {trial['ends_at']}, "
            f"{trial['days_left']} gün kaldı [days left]"
        )
    if document.get("licensee"):
        lines.append(f"  Lisans sahibi [licensee]: {document['licensee']}")
    details = document.get("details") or {}
    if details.get("license_id"):
        lines.append(
            f"  Lisans [licence]: {details['license_id']} (anahtar [key] {details.get('key_id')})"
        )
    maintenance = document.get("maintenance")
    if maintenance is not None:
        ended = " — süresi doldu [ended]" if maintenance["expired"] else ""
        lines.append(
            f"  Bakım [maintenance]: {maintenance['until']} "
            f"({maintenance['days_left']} gün [days]){ended}"
        )
    if document.get("valid_until"):
        lines.append(f"  Geçerlilik sonu [valid until]: {document['valid_until']}")
    modules = ", ".join(
        f"{item['name_tr']} [{item['name_en']}]: "
        + ("açık [on]" if item["enabled"] else "kapalı [off]")
        for item in document["modules"]
    )
    lines.append(f"  Modüller [modules]: {modules}")
    for name, label in (
        ("log_sources", "Log kaynağı [log sources]"),
        ("users", "Kullanıcı [users]"),
    ):
        use = document["limits"][name]
        limit = "sınırsız [unlimited]" if use["max"] is None else str(use["max"])
        lines.append(f"  {label}: {use['used']} / {limit}")
    if details:
        lines.append(f"  Sunucu kimliği [server id]: {details.get('server_id') or '-'}")
        if details.get("server_id_error"):
            lines.append(f"    okunamadı [unreadable]: {details['server_id_error']}")
        lines.append(f"  Kurulum kimliği [installation id]: {details.get('installation_id')}")
    product = document["product"]
    lines.append(
        f"  Sürüm [version]: {product['version']} "
        f"(yayın tarihi [released] {product['release_date']})"
    )
    return lines


def _write_status(licensing: Any, layout: Layout, status: Any) -> None:
    try:
        licensing.write_status_file(layout, status)
    except OSError as exc:
        print(f"Durum dosyası yazılamadı [status file not written]: {exc}", file=sys.stderr)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _confirm_remove() -> bool:
    answer = input("Kurulu lisans kaldırılsın mı? [remove the installed licence?] (e/h) ")
    return answer.strip().lower() in ("e", "evet", "y", "yes")


class _LicenseRun:
    """One ``arcilogger-db license`` command in one READ COMMITTED transaction (the licence
    event and its audit entry commit together)."""

    def __init__(self, licensing: Any, error_type: Any, config: Config, args: Any) -> None:
        self.licensing = licensing
        self.error_type = error_type
        self.config = config
        self.args = args
        self.as_json = bool(getattr(args, "json", False))
        self.layout = Layout(config.data_dir)
        self.actor, self.os_user = _os_actor()
        self.lines: list[str] = []

    def audit_for(self, session: Session) -> Any:
        def audit(action: str, target: str | None, details: Any) -> None:
            append_audit(
                session,
                action=action,
                actor=self.actor,
                target=target,
                details={**details, "os_user": self.os_user},
            )

        return audit

    def status(self, session: Session) -> Any:
        return self.licensing.load_status(session, config=self.config, layout=self.layout)

    def document(self, session: Session, status: Any) -> dict[str, Any]:
        result: dict[str, Any] = self.licensing.status_json(
            status, details=True, session=session, config=self.config
        )
        return result

    def run_status(self, session: Session) -> int:
        status = self.status(session)
        document = self.document(session, status)
        _write_status(self.licensing, self.layout, status)
        self.lines = [_dump(document)] if self.as_json else _status_lines(document)
        return EXIT_OK

    def run_request_code(self, session: Session) -> int:
        info = self.licensing.request_info(self.status(session), self.config)
        if not info["organization"]:
            print(
                "Uyarı: Kuruluş adı ayarlanmamış [warning: organisation name not set] (site_name).",
                file=sys.stderr,
            )
        if self.as_json:
            self.lines = [_dump(info)]
            return EXIT_OK
        if info["request_code"] is None:
            return _refused(self.error_type("license_server_id_unavailable"), False)
        self.lines = [info["request_code"]]
        return EXIT_OK

    def run_verify(self, session: Session, data: bytes | None, size: int) -> int:
        try:
            if data is None:
                raise self.error_type("license_too_large", reason=f"{size} bytes")
            checked = self.licensing.verify_license_file(
                session, data, config=self.config, layout=self.layout
            )
        except self.error_type as exc:
            return _refused(exc, self.as_json)
        if self.as_json:
            document = self.document(session, self.status(session))
            self.lines = [_dump({"ok": True, "status": document})]
        else:
            self.lines = [
                "Lisans dosyası geçerli ve bu sunucuya uygun; hiçbir şey değiştirilmedi [the "
                "licence file is valid for this server; nothing changed]: "
                f"{checked.payload.license_id} ({checked.payload.licensee})"
            ]
        return EXIT_OK

    def run_install(self, session: Session, data: bytes | None, size: int) -> int:
        audit = self.audit_for(session)
        try:
            if data is None:
                raise self.error_type("license_too_large", reason=f"{size} bytes")
            status = self.licensing.install_license(
                session,
                data,
                config=self.config,
                layout=self.layout,
                actor=self.actor,
                source="cli",
                audit=audit,
            )
        except self.error_type as exc:
            self.licensing.record_refusal(
                session, exc, data, actor=self.actor, source="cli", audit=audit, size=size
            )
            return _refused(exc, self.as_json)
        _write_status(self.licensing, self.layout, status)
        document = self.document(session, status)
        if self.as_json:
            self.lines = [_dump({"ok": True, "status": document})]
        else:
            self.lines = ["Lisans yüklendi [licence installed].", *_status_lines(document)]
        return EXIT_OK

    def run_remove(self, session: Session) -> int:
        try:
            self.licensing.remove_license(
                session, actor=self.actor, source="cli", audit=self.audit_for(session)
            )
        except self.error_type as exc:
            return _refused(exc, False)
        status = self.status(session)
        _write_status(self.licensing, self.layout, status)
        document = self.document(session, status)
        self.lines = ["Lisans kaldırıldı [licence removed].", *_status_lines(document)]
        return EXIT_OK


def cmd_license(config: Config, args: argparse.Namespace) -> int:
    """``arcilogger-db license status | request-code | verify | install | remove``."""
    command = args.license_command
    try:  # the only place that loads the licence code (contract 5.2)
        from arcilogger_core.license import LicenseError
        from arcilogger_db import licensing
    except ImportError:
        return _license_unavailable(bool(getattr(args, "json", False)))
    run = _LicenseRun(licensing, LicenseError, config, args)
    data: bytes | None = None
    size = 0
    if command in ("verify", "install"):
        try:
            data, size = _read_license_file(args.file)
        except OSError as exc:
            print(f"Lisans dosyası okunamadı [licence file unreadable]: {exc}", file=sys.stderr)
            return EXIT_USAGE
    if command == "remove" and not args.yes:
        if not sys.stdin.isatty():
            print("Onay için --yes gerekli [--yes is required].", file=sys.stderr)
            return EXIT_USAGE
        if not _confirm_remove():
            print("Vazgeçildi; hiçbir şey değiştirilmedi [cancelled; nothing changed].")
            return EXIT_USAGE
    engine = _engine(config)
    try:
        with engine.begin() as connection, Session(bind=connection) as session:
            if command == "status":
                code = run.run_status(session)
            elif command == "request-code":
                code = run.run_request_code(session)
            elif command == "verify":
                code = run.run_verify(session, data, size)
            elif command == "install":
                code = run.run_install(session, data, size)
            else:
                code = run.run_remove(session)
            session.flush()
    except OperationalError as exc:
        raise _UnreachableError(_describe(exc)) from exc
    finally:
        engine.dispose()
    if run.lines:
        print("\n".join(run.lines))
    return code


HANDLERS = {
    "upgrade": cmd_upgrade,
    "current": cmd_current,
    "check": cmd_check,
    "partitions": cmd_partitions,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcilogger-db", description="ARCILOGGER database: migrations and checks."
    )
    parser.add_argument("--version", action="version", version=f"ARCILOGGER db {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("version", help="print the version")
    for name, help_text in (
        ("upgrade", "migrate to the newest schema and seed the defaults"),
        ("current", "show the current and the newest schema revision"),
        ("check", "connectivity, encoding, schema state, TimescaleDB and partitions"),
        ("partitions", "create the missing monthly partitions (plain PostgreSQL)"),
    ):
        sub = commands.add_parser(name, help=help_text)
        # Accepted after the command too: `arcilogger-db upgrade --config X`.
        sub.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    settings = commands.add_parser(
        "settings", help="list, show or change database settings (validated and audited)"
    )
    actions = settings.add_subparsers(dest="settings_command", metavar="<action>")
    listing = actions.add_parser("list", help="every setting with its value and who set it")
    show = actions.add_parser("get", help="the value of one setting (canonical JSON)")
    show.add_argument("key")
    change = actions.add_parser(
        "set", help="change a setting: validated, audited (settings.change), kept by upgrades"
    )
    change.add_argument("key")
    change.add_argument("value", metavar="JSON_VALUE", help='JSON value; "-" reads it from stdin')
    change.add_argument("--by", required=True, help="name of the person making the change")
    for sub in (settings, listing, show, change):
        sub.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    backup = commands.add_parser(
        "backup", help="scheduled database backups: run, list, verify, prune, restore"
    )
    backup_actions = backup.add_subparsers(dest="backup_command", metavar="<action>")
    run = backup_actions.add_parser(
        "run", help="dump, check, record state/backup/last.json, apply the retention"
    )
    backups = backup_actions.add_parser("list", help="the scheduled dumps, newest first")
    backups.add_argument("--json", action="store_true")
    verify = backup_actions.add_parser(
        "verify", help="size and SHA-256 against the sidecar, pg_restore --list (default: newest)"
    )
    verify.add_argument("files", nargs="*", type=Path, metavar="FILE")
    verify.add_argument("--all", action="store_true", help="every scheduled dump")
    verify.add_argument(
        "--deep", action="store_true", help="also read all data (takes about as long as a restore)"
    )
    prune = backup_actions.add_parser("prune", help="apply the retention ([backup] keep_*)")
    prune.add_argument("--dry-run", action="store_true", help="only list what would be deleted")
    restore = backup_actions.add_parser(
        "restore",
        help="restore a dump into a NEW database (superuser password read from stdin)",
    )
    restore.add_argument("--file", type=Path, required=True, help="the .dump file")
    restore.add_argument(
        "--target-db", required=True, metavar="NAME", help="name of the new database"
    )
    restore.add_argument(
        "--superuser", default="postgres", metavar="NAME", help="PostgreSQL superuser"
    )
    restore.add_argument(
        "--sha256",
        default=None,
        metavar="HEX",
        help="the dump's SHA-256 recorded off the server; refused before any change if it differs",
    )
    for sub in (backup, run, backups, verify, prune, restore):
        sub.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    licence = commands.add_parser(
        "license", help="offline licence: state, request code, verify, install, remove"
    )
    licence_actions = licence.add_subparsers(dest="license_command", metavar="<action>")
    state = licence_actions.add_parser("status", help="the licence state (writes the status file)")
    state.add_argument("--json", action="store_true", help="the GET /license admin document")
    request_code = licence_actions.add_parser(
        "request-code", help="this installation's request code (e-mail it to the licensor)"
    )
    request_code.add_argument("--json", action="store_true")
    check_file = licence_actions.add_parser(
        "verify", help="the checks of install for a .lic file, without installing it"
    )
    check_file.add_argument("file", type=Path, metavar="FILE")
    check_file.add_argument("--json", action="store_true")
    install_file = licence_actions.add_parser("install", help="install a .lic file (audited)")
    install_file.add_argument("file", type=Path, metavar="FILE")
    install_file.add_argument("--json", action="store_true")
    remove = licence_actions.add_parser("remove", help="remove the installed licence (audited)")
    remove.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    for sub in (licence, state, request_code, check_file, install_file, remove):
        sub.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"ARCILOGGER db {__version__}")
        return EXIT_OK
    handler = HANDLERS.get(args.command or "")
    if args.command == "settings" and getattr(args, "settings_command", None) is not None:
        handler = _settings_handler(args)
    if args.command == "backup" and getattr(args, "backup_command", None) is not None:
        handler = _backup_handler(args)
    if args.command == "license" and getattr(args, "license_command", None) is not None:
        handler = _license_handler(args)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        config = load_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Yapılandırma okunamadı: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return handler(config)
    except SecretStoreError as exc:
        print(f"Gizli bilgi deposu okunamadı: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except _UnreachableError as exc:
        print(f"Veritabanına bağlanılamadı: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except _UsageError as exc:
        print(f"Ayar değiştirilmedi: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (SQLAlchemyError, RuntimeError) as exc:
        message = _describe(exc) if isinstance(exc, SQLAlchemyError) else str(exc)
        print(f"Veritabanı hatası: {message}", file=sys.stderr)
        return EXIT_ATTENTION


def _settings_handler(args: argparse.Namespace) -> Any:
    return lambda config: cmd_settings(config, args)


def _backup_handler(args: argparse.Namespace) -> Any:
    return lambda config: cmd_backup(config, args)


def _license_handler(args: argparse.Namespace) -> Any:
    return lambda config: cmd_license(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
