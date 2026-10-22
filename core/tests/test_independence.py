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
