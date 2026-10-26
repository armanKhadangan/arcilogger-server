"""The evidence path must run without the database stack (ADR-0001, docs/ARCHITECTURE.md)."""

from __future__ import annotations

import subprocess
import sys

CHECK = """
import sys
import arcilogger_core.cli, arcilogger_core.seal, arcilogger_core.chain, arcilogger_core.config
import arcilogger_core.retention, arcilogger_core.replica, arcilogger_core.secretstore
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in {"sqlalchemy", "psycopg", "alembic", "fastapi", "arcilogger_db"}
)
print(",".join(loaded))
"""


def test_core_imports_no_database_or_web_modules() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", CHECK], capture_output=True, check=True, timeout=120
    )
    assert completed.stdout.decode().strip() == ""


ADMIN_CHECK = """
import sys
import arcilogger_core.admin.contract, arcilogger_core.admin.envelope, arcilogger_core.admin.keys
import arcilogger_core.admin.messages, arcilogger_core.execpolicy, arcilogger_core.configwatch
import arcilogger_core.tsa.zamane_releases
import arcilogger_core.admin.rules, arcilogger_core.admin.tomledit, arcilogger_core.admin.policy
import arcilogger_core.admin.roots, arcilogger_core.admin.zamane, arcilogger_core.admin.store
import arcilogger_core.admin.apply, arcilogger_core.admin.agent, arcilogger_core.admin.dispatch
import arcilogger_core.admin.probe, arcilogger_core.admin.systemplan, arcilogger_core.admin.winapi
import arcilogger_core.admin.testing, arcilogger_core.admin.__main__
import arcilogger_core.tsa.preflight, arcilogger_core.smtp
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0]
    in {"sqlalchemy", "psycopg", "alembic", "fastapi", "starlette", "arcilogger_db",
        "arcilogger_api", "arcilogger_worker"}
    or name.startswith("arcilogger_core.license")
)
print(",".join(loaded))
"""


def test_the_administration_channel_imports_no_database_web_worker_or_licence_code() -> None:
    """The agent and the tasks run without them (ADR-0014; contract 2.1)."""
    completed = subprocess.run(
        [sys.executable, "-c", ADMIN_CHECK], capture_output=True, check=True, timeout=120
    )
    assert completed.stdout.decode().strip() == ""
