"""pysnmp stays out of every process that does not poll or probe SNMP (W5-N, Q4 on #202).

``arcilogger_core.snmp`` imports it only inside its methods: the configuration, the collector's
and ingest's modules and ``arcilogger_core.snmp`` itself load without it, so their start-up and
memory do not change.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    ["arcilogger_core.config", "arcilogger_core.snmp", "arcilogger_collector", "arcilogger_ingest"],
)
def test_importing_does_not_load_pysnmp(module: str) -> None:
    code = (
        f"import sys, {module}; "
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('pysnmp', 'pyasn1')); "
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
