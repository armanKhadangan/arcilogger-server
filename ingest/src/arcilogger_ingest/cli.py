"""``arcilogger-ingest`` command line (run by the Windows service ``ArciIngest``)::

    arcilogger-ingest run [--config PATH] [--once] [--workers N]
                                          tail the spool and load the database
    arcilogger-ingest status [--config PATH]   print state/ingest-status.json
    arcilogger-ingest version

``--workers N`` prepares lines (parsing, row checks) in N worker processes while the service
process reads files and loads the database (default 0: everything in one process). See
docs/INGEST.md for measured throughput; 2 is a sensible value on the 4-core server once the
FortiGate/MikroTik parsers produce typed rows.

Exit codes of ``run``:

== ===============================================================================================
0  stopped on request (Ctrl+C, Ctrl+Break, SIGTERM, console close, system shutdown); with
   ``--once``: everything readable was loaded
1  unexpected internal error (logged with its traceback; the service wrapper restarts the service)
2  configuration error: ``arcilogger.toml`` unreadable or invalid, secret store unusable
3  ``--once`` only: the database is unreachable (the service mode waits for it instead)
4  ``--once`` only: another ingest instance is running on the same database (the service mode
   waits until it stops, checking every 5 s, and writes no status file meanwhile)
== ===============================================================================================

``status`` exits 0 when the file exists, 1 when it does not (service never started). Logging goes
to the console (stderr); the service wrapper captures it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from arcilogger_core.config import Config, default_config_path, load_config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_db.engine import make_engine
from arcilogger_ingest import __version__
from arcilogger_ingest.service import (
    EXIT_CONFIG,
    EXIT_DB_UNAVAILABLE,
    EXIT_ERROR,
    EXIT_LOCKED,
    EXIT_OK,
    IngestService,
)
from arcilogger_ingest.status import read_status
from arcilogger_ingest.stopping import install_stop_handlers

__all__ = [
    "EXIT_CONFIG",
    "EXIT_DB_UNAVAILABLE",
    "EXIT_ERROR",
    "EXIT_LOCKED",
    "EXIT_OK",
    "main",
    "run",
]

log = logging.getLogger("arcilogger.ingest")


def _utf8_console() -> None:
    """Turkish text (ı, ş, ğ) must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arcilogger-ingest", description="ARCILOGGER ingest")
    parser.add_argument("--version", action="version", version=f"ARCILOGGER ingest {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("version", help="print the version")
    run_cmd = commands.add_parser("run", help="tail the raw spool and load the database")
    run_cmd.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    run_cmd.add_argument(
        "--once", action="store_true", help="load what is there now, then exit (no waiting)"
    )
    run_cmd.add_argument(
        "--workers",
        type=int,
        default=None,
        choices=range(17),
        metavar="N",
        help="parse worker processes (0-16; default [ingest] workers, 0 = in the service process)",
    )
    status = commands.add_parser("status", help="print the ingest status file")
    status.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    return parser


def _load(config_path: Path | None) -> Config | None:
    try:
        return load_config(config_path)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Yapılandırma okunamadı / configuration error: {exc}", file=sys.stderr)
        return None


def run(config_path: Path | None, *, once: bool = False, workers: int | None = None) -> int:
    """Service entry point: tail the spool and load the database until stopped."""
    config = _load(config_path)
    if config is None:
        return EXIT_CONFIG
    _setup_logging()
    try:
        # One connection holds the single-instance lock for the life of the service, one loads.
        engine = make_engine(config, application="ingest", pool_size=2, max_overflow=1)
    except (OSError, ValueError, SecretStoreError) as exc:  # missing/corrupt/wrong key
        log.error("database settings unusable (secret store): %s", exc)
        return EXIT_CONFIG
    service = IngestService(
        config,
        engine,
        workers=config.ingest.workers if workers is None else workers,
        config_path=config_path or default_config_path(),  # [[sources]] reloaded from it
    )
    uninstall = install_stop_handlers(service.stop, service.stopped)
    try:
        return service.run(once=once)
    except Exception:
        log.exception("ingest failed")
        return EXIT_ERROR
    finally:
        uninstall()
        engine.dispose()


def status(config_path: Path | None) -> int:
    config = _load(config_path)
    if config is None:
        return EXIT_CONFIG
    path = Layout(config.data_dir).ingest_status
    data = read_status(path)
    if data is None:
        print(f"{path}: yok / not found (ingest never ran?)", file=sys.stderr)
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"ARCILOGGER ingest {__version__}")
        return 0
    if args.command == "run":
        return run(args.config, once=args.once, workers=args.workers)
    if args.command == "status":
        return status(args.config)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
