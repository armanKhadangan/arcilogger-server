"""``arcilogger-collector`` command line (run by the Windows service ``ArciCollector``)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from arcilogger_collector import __version__
from arcilogger_collector.service import run
from arcilogger_core.config import default_config_path, load_config


def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_dir / "collector.log", encoding="utf-8"),
        ],
    )


async def _main(config_path: Path | None) -> None:
    path = config_path or default_config_path()
    config = load_config(path)
    _setup_logging(config.data_dir / "logs")
    stop = asyncio.Event()
    if sys.platform != "win32":  # Windows: the service wrapper sends Ctrl+C (KeyboardInterrupt)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    await run(config, stop, config_path=path)  # [[sources]] are reloaded from the same file


def _utf8_console() -> None:
    """Turkish text (ı, ş, ğ) must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arcilogger-collector", description="ARCILOGGER syslog collector"
    )
    parser.add_argument(
        "--version", action="version", version=f"ARCILOGGER collector {__version__}"
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    run_cmd = commands.add_parser("run", help="receive syslog until stopped")
    run_cmd.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 2
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
