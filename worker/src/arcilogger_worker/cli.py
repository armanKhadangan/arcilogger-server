"""``arcilogger-worker`` command line (run by the Windows service ``ArciWorker``)::

    arcilogger-worker run [--config PATH]
        scheduled application jobs until stopped (Ctrl+C, service stop, SIGTERM)
    arcilogger-worker rebuild --day YYYY-MM-DD [--to YYYY-MM-DD] [--config PATH]
        manual repair: rebuild the lease timeline of a site day (or a range, in order)
    arcilogger-worker alerts test-email --to ADDRESS [--config PATH]
        send one test e-mail through [smtp]; prints success or the SMTP error (no password)
    arcilogger-worker alerts list [--open] [--limit N] [--config PATH]
        the newest alerts (``--open``: open and acknowledged ones only)
    arcilogger-worker usage rebuild --from YYYY-MM-DD [--to YYYY-MM-DD] [--config PATH]
        rebuild the usage rollups of a range of site days (e.g. after linking devices to
        persons, or after changing a ``usage.*`` setting), one transaction per day
    arcilogger-worker version

Exit codes (as ``arcilogger-db``): 0 ok, 1 a job failed, 2 configuration or usage error (also
lease settings the timeline cannot work with), 3 database unreachable. A failed day is
recorded as a ``failed`` ``leases.rebuild`` job run with the error, never left ``running``; the
usage rebuild records one ``usage.rebuild`` job run for the range.

``run`` refuses (exit 2) a configuration that moves the installation's folders: ``secrets_dir``
must be the folder of the ``--config`` file and ``data_dir`` the data root the installer
recorded in ``data-root.txt`` next to it (ADR-0014, contract Day 0 notes D0-2, WP2-1): the
administration agent may write ``arcilogger.toml``, and the worker imports what it finds below
``data_dir`` into the audit log.

Licence (contract 1.4): only ``usage rebuild`` is gated — refused with exit 1 and the
``module_not_licensed`` message while ``usage_analytics`` is off. ``run``, ``rebuild`` (the lease
timeline, the 5651 attribution repair), ``alerts …`` and ``version`` work in every licence state.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from types import FrameType

from pydantic import ValidationError
from sqlalchemy import Engine, text, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config, default_config_path, load_config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.engine import make_engine, session_factory
from arcilogger_db.license_guard import module_not_licensed_texts, safe_status
from arcilogger_db.models import JobRun
from arcilogger_worker import __version__
from arcilogger_worker.alerts.commands import list_alerts, send_test_email
from arcilogger_worker.correlation.rebuild import rebuild_lease_day
from arcilogger_worker.service import Worker, default_jobs
from arcilogger_worker.usage.jobs import rebuild_range

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_DATABASE = 3
#: The module of ``usage rebuild`` (the only gated command).
USAGE_MODULE = "usage_analytics"


def _utf8_console() -> None:
    """Turkish text (ı, ş, ğ) must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _setup_logging(log_dir: Path) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "worker.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a day (YYYY-MM-DD): {value!r}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arcilogger-worker", description="ARCILOGGER worker")
    parser.add_argument("--version", action="version", version=f"ARCILOGGER worker {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("version", help="print the version")
    run = commands.add_parser("run", help="run the scheduled application jobs until stopped")
    run.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    rebuild = commands.add_parser(
        "rebuild", help="rebuild the lease timeline of a site day (manual repair)"
    )
    rebuild.add_argument("--day", type=_day, required=True, help="site day YYYY-MM-DD")
    rebuild.add_argument("--to", type=_day, default=None, help="last day of a range (inclusive)")
    rebuild.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    alerts = commands.add_parser("alerts", help="alerts: list them, test the e-mail")
    alert_commands = alerts.add_subparsers(dest="alerts_command", metavar="<command>")
    alert_commands.required = True
    test = alert_commands.add_parser("test-email", help="send one test e-mail through [smtp]")
    test.add_argument("--to", required=True, metavar="ADDRESS", help="recipient address")
    test.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    listing = alert_commands.add_parser("list", help="the newest alerts")
    listing.add_argument("--open", action="store_true", help="only open/acknowledged alerts")
    listing.add_argument("--limit", type=int, default=50, metavar="N")
    listing.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    usage = commands.add_parser("usage", help="usage analytics: rebuild the rollups")
    usage_commands = usage.add_subparsers(dest="usage_command", metavar="<command>")
    usage_commands.required = True
    usage_rebuild = usage_commands.add_parser(
        "rebuild", help="rebuild the usage rollups of site days (after linking devices)"
    )
    usage_rebuild.add_argument(
        "--from", dest="first", type=_day, required=True, help="first site day YYYY-MM-DD"
    )
    usage_rebuild.add_argument(
        "--to", dest="last", type=_day, default=None, help="last site day (inclusive)"
    )
    usage_rebuild.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    return parser


def _load(config_path: Path | None) -> Config | None:
    try:
        return load_config(config_path)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Yapılandırma okunamadı / cannot read the configuration: {exc}", file=sys.stderr)
        return None


#: The installer's record of the data root, next to the configuration file (contract WP2-1).
DATA_ROOT_RECORD = "data-root.txt"


def _lexical(path: Path) -> str:
    """``path`` made absolute without following a link (Windows: case-insensitive)."""
    return os.path.normcase(os.path.abspath(path))  # noqa: PTH100 - lexical on purpose


def _location_problem(config: Config, config_path: Path) -> str | None:
    """Why the worker must not use ``config``, or ``None`` (ADR-0014, contract Day 0 notes D0-2,
    WP2-1, WP3-5): ``secrets_dir`` is not the folder of the fixed ``config_path`` (it chooses
    the SMTP and database passwords), or ``data_dir`` is not the data root recorded in
    :data:`DATA_ROOT_RECORD` (no record: not checked; a record that cannot be used refuses the
    configuration). The semantics of WP2's ``config.check_fixed_location``, which replaces this
    at the merge."""
    if _lexical(config.secrets_dir) != _lexical(config_path.parent):
        return (
            f"secrets_dir ({config.secrets_dir}) yapılandırma dosyasının klasörü değil / is "
            f"not the folder of the configuration file ({config_path.parent})"
        )
    record = config_path.parent / DATA_ROOT_RECORD
    try:
        text = record.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        return f"{record} okunamadı / cannot be read ({type(exc).__name__})"
    lines = text.strip().splitlines()
    if len(lines) != 1 or not Path(lines[0].strip()).is_absolute():
        return f"{record}: tek satırda mutlak bir yol değil / not one line with an absolute path"
    if _lexical(config.data_dir) != _lexical(Path(lines[0].strip())):
        return (
            f"data_dir ({config.data_dir}) kurulumun veri klasörü değil / is not the data root "
            f"the installer recorded ({lines[0].strip()})"
        )
    return None


def _check_database(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        print(
            f"Veritabanına bağlanılamadı / database unreachable: {type(exc.orig).__name__}",
            file=sys.stderr,
        )
        return False
    return True


def run(config_path: Path | None, *, stop: threading.Event | None = None) -> int:
    """Service entry point: the job loop until ``stop`` (or a signal) ends it."""
    config = _load(config_path)
    if config is None:
        return EXIT_CONFIG
    fixed_path = config_path or default_config_path()
    problem = _location_problem(config, fixed_path)
    if problem is not None:  # nothing is written below a data_dir that may not be ours
        print(
            f"Yapılandırma kullanılmadı / the configuration was not used: {problem}",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    _setup_logging(config.data_dir / "logs")
    engine = make_engine(config, application="worker")
    stop = stop or threading.Event()

    def _request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)
        if sys.platform == "win32":
            # The service wrapper may send Ctrl+Break instead of Ctrl+C.
            signal.signal(signal.SIGBREAK, _request_stop)
    worker = Worker(
        engine=engine,
        layout=Layout(config.data_dir),
        jobs=default_jobs(config.worker.interval_seconds),
        config=config,
        config_path=fixed_path,  # live reload, the admin folder
    )
    try:
        worker.run(stop)
    finally:
        engine.dispose()
    return EXIT_OK


def _rebuild_one(sessions: sessionmaker[Session], day: date, *, lookahead: bool) -> int:
    """Rebuild one day (:func:`.correlation.rebuild.rebuild_lease_day`); returns the exit
    code."""
    outcome = rebuild_lease_day(sessions, day, lookahead=lookahead)
    report = outcome.report
    if report is None:
        kind = "ayar hatası / settings error" if outcome.settings_error else "failed"
        print(f"{day}: başarısız / {kind}: {outcome.error_text}", file=sys.stderr)
        return EXIT_CONFIG if outcome.settings_error else EXIT_FAILED
    print(
        f"{day}: {report.inserted} kira yazıldı, {report.updated} güncellendi, "
        f"{report.deleted} silindi, {report.alerts} yeni uyarı / {report.inserted} "
        f"written, {report.updated} updated, {report.deleted} deleted, "
        f"{report.alerts} new alerts"
    )
    return EXIT_OK


def rebuild(config_path: Path | None, first: date, last: date | None) -> int:
    """``arcilogger-worker rebuild``: days in order, look-ahead only after the last one."""
    last = last or first
    if last < first:
        print("--to must not be before --day", file=sys.stderr)
        return EXIT_CONFIG
    config = _load(config_path)
    if config is None:
        return EXIT_CONFIG
    engine = make_engine(config, application="worker-rebuild")
    try:
        if not _check_database(engine):
            return EXIT_DATABASE
        sessions = session_factory(engine)
        day = first
        while day <= last:
            code = _rebuild_one(sessions, day, lookahead=day == last)
            if code != EXIT_OK:
                return code
            day += timedelta(days=1)
    except SQLAlchemyError as exc:  # e.g. the database went away between two days
        print(f"Veritabanı hatası / database error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_DATABASE
    finally:
        engine.dispose()
    return EXIT_OK


def alerts(config_path: Path | None, args: argparse.Namespace) -> int:
    """``arcilogger-worker alerts test-email | list``."""
    config = _load(config_path)
    if config is None:
        return EXIT_CONFIG
    engine = make_engine(config, application="worker-alerts")
    try:
        if args.alerts_command == "test-email":
            return send_test_email(config, args.to, engine=engine)
        if not _check_database(engine):
            return EXIT_DATABASE
        return list_alerts(engine, only_open=args.open, limit=args.limit)
    finally:
        engine.dispose()


def usage_rebuild(config_path: Path | None, first: date, last: date | None) -> int:
    """``arcilogger-worker usage rebuild``: the usage rollups of ``first`` … ``last``."""
    last = last or first
    if last < first:
        print("--to must not be before --from / --to, --from gününden önce olamaz", file=sys.stderr)
        return EXIT_CONFIG
    if last > site_day(utc_now()):
        print("--to must not be in the future / gelecekteki bir gün olamaz", file=sys.stderr)
        return EXIT_CONFIG
    config = _load(config_path)
    if config is None:
        return EXIT_CONFIG
    engine = make_engine(config, application="worker-usage")
    try:
        if not _check_database(engine):
            return EXIT_DATABASE
        sessions = session_factory(engine)
        with sessions() as session:
            guarded = safe_status(session, config=config, layout=Layout(config.data_dir))
            session.commit()
        if not guarded.module_enabled(USAGE_MODULE):
            text_tr, text_en = module_not_licensed_texts(USAGE_MODULE)
            print(f"{text_tr}\n[{text_en}]", file=sys.stderr)
            return EXIT_FAILED
        with sessions() as session:
            run = JobRun(
                job="usage.rebuild",
                started_at=utc_now(),
                status="running",
                details={"from": first.isoformat(), "to": last.isoformat(), "manual": True},
            )
            session.add(run)
            session.commit()
            run_id = run.id
        days: list[dict[str, object]] = []

        def progress(day: date, details: dict[str, object]) -> None:
            days.append(details)
            rows = details.get("rows")
            count = sum(rows.values()) if isinstance(rows, dict) else 0
            print(
                f"{day}: {details.get('status')} — {count} satır, {details.get('duration_ms')} ms "
                f"/ {count} rows"
            )

        code = EXIT_OK
        error: dict[str, object] = {}
        try:
            rebuild_range(sessions, first, last, now=utc_now(), progress=progress)
        except SQLAlchemyError as exc:
            print(f"Veritabanı hatası / database error: {type(exc).__name__}", file=sys.stderr)
            code = EXIT_DATABASE
            error = {"error": type(exc).__name__}
        except Exception as exc:  # recorded and reported; the job run never stays "running"
            print(f"başarısız / failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            code = EXIT_FAILED
            error = {"error": type(exc).__name__, "message": str(exc)[:500]}
        with sessions() as session:
            session.execute(
                update(JobRun)
                .where(JobRun.id == run_id)
                .values(
                    status="ok" if code == EXIT_OK else "failed",
                    finished_at=utc_now(),
                    details={
                        "from": first.isoformat(),
                        "to": last.isoformat(),
                        "manual": True,
                        "days": len(days),
                        **error,
                    },
                )
            )
            session.commit()
        return code
    except SQLAlchemyError as exc:
        print(f"Veritabanı hatası / database error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_DATABASE
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else EXIT_CONFIG
    if args.command == "version":
        print(f"ARCILOGGER worker {__version__}")
        return EXIT_OK
    if args.command == "run":
        return run(args.config)
    if args.command == "rebuild":
        return rebuild(args.config, args.day, args.to)
    if args.command == "alerts":
        return alerts(args.config, args)
    if args.command == "usage":
        return usage_rebuild(args.config, args.first, args.last)
    parser.print_help()
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
