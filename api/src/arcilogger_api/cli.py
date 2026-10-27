"""``arcilogger-api`` command line (run by the Windows service ``ArciApi``)::

    arcilogger-api serve [--config PATH] [--host H] [--port P]
    arcilogger-api setup-token [--config PATH]    one-time token for the first administrator
    arcilogger-api create-admin [--deactivate USERNAME] [--config PATH]
                                                  interactive: create an administrator (recovery);
                                                  --deactivate disables an active user in the same
                                                  transaction (a full licence users limit)
    arcilogger-api version

Serves the API with Uvicorn on ``[api] host:port`` (default 127.0.0.1:8000) — never with
auto-reload. ``X-Forwarded-For`` is trusted only from ``[api] forwarded_allow_ips`` (Caddy). A stop
waits at most 5 s for open connections (``timeout_graceful_shutdown``): an open Canlı Akış stream
never ends by itself, and Uvicorn waits for open connections before the application shuts down.
``setup-token`` and ``create-admin`` are run on the server console
(:mod:`arcilogger_api.auth.admin_cli`, docs/SECURITY.md).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import ValidationError

from arcilogger_api import __version__
from arcilogger_api.app import create_app
from arcilogger_api.auth import admin_cli
from arcilogger_core.config import check_fixed_location, default_config_path, load_config


def _utf8_console() -> None:
    """Turkish text (ı, ş, ğ) must not crash on a cp1252 Windows console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _run_server(app: Any, **options: Any) -> None:
    """Indirection so tests can check the options without starting a server."""
    uvicorn.run(app, **options)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arcilogger-api", description="ARCILOGGER HTTP API")
    parser.add_argument("--version", action="version", version=f"ARCILOGGER api {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to arcilogger.toml")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("version", help="print the version")
    serve = commands.add_parser("serve", help="serve the API (Uvicorn)")
    serve.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    serve.add_argument("--host", default=None, help="override [api] host")
    serve.add_argument("--port", type=int, default=None, help="override [api] port")
    for name, text in (
        ("setup-token", "print a one-time token for creating the first administrator (24 h)"),
        ("create-admin", "create an administrator interactively (recovery)"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("--config", type=Path, default=argparse.SUPPRESS)
        if name == "create-admin":
            command.add_argument(
                "--deactivate",
                metavar="USERNAME",
                default=None,
                help="disable this active user in the same transaction (licence users limit)",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"ARCILOGGER api {__version__}")
        return 0
    if args.command not in ("serve", "setup-token", "create-admin"):
        parser.print_help()
        return 2
    path = args.config or default_config_path()
    try:
        config = load_config(path)
        # Nothing the browser can change may move the secret store or the data root (ADR-0014).
        check_fixed_location(config, path)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Yapılandırma okunamadı: {exc}", file=sys.stderr)
        return 2
    if args.command == "setup-token":
        return admin_cli.setup_token(config)
    if args.command == "create-admin":
        if args.deactivate is None:
            return admin_cli.create_admin(config)
        return admin_cli.create_admin(config, deactivate=args.deactivate)
    _run_server(
        create_app(config, config_path=path),
        host=args.host or config.api.host,
        port=args.port or config.api.port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips=config.api.forwarded_allow_ips,
        server_header=False,
        log_level="info",
        # An open Canlı Akış stream must not hold a service stop: after 5 s Uvicorn cancels it.
        timeout_graceful_shutdown=5,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
