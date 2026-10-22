"""Alerts raised by the evidence path (collector, sealing, replica, retention, clock, credit).

They are appended to ``state/alerts.jsonl`` so they exist even when the database is down; the
worker imports every line once into the ``alerts`` table (job ``alerts.import``) and e-mails the
high and critical ones (``alerts.notify``; docs/ALERTS.md, ADR-0010). Rule ids follow the
specification (R15 log source silent, R20 ARCILOGGER system).

The import keeps one active alert per dedup key, which it derives from ``details``
(``arcilogger_worker.alerts.importer.import_key``): ``R20-seal`` by ``day``, ``R20-seal-config``
by ``cause``; every other rule by a ``key`` (``<rule>:<key>``), and without one ``R20-clock``,
``R20-credit``, ``R20-verify``, ``R20-replica`` and ``R20-backup`` keep one alert each,
``new-log-source`` and ``wrong-transport`` one per ``address``. Every other line is an alert of
its own: put a ``key`` into ``details`` when repeats should count on one alert.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from arcilogger_core.timeutil import isoformat_site, utc_now

Severity = Literal["info", "low", "medium", "high", "critical"]


def raise_alert(
    path: Path,
    rule: str,
    severity: Severity,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Append one alert (one JSON object per line) and flush it to disk.

    A power cut can leave the file ending in a torn line or NUL bytes without a newline; the
    alert then starts on a fresh line, so the fragment stays a line of its own (the import skips
    it) and the alert stays a complete line.
    """
    entry = {
        "time": isoformat_site(utc_now(), "seconds"),
        "rule": rule,
        "severity": severity,
        "message": message,
        "details": details or {},
    }
    data = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:  # every write goes to the end; reading finds the last byte
        size = handle.seek(0, os.SEEK_END)
        if size:
            handle.seek(size - 1)
            if handle.read(1) != b"\n":
                data = b"\n" + data
            handle.seek(0, os.SEEK_END)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def read_alerts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
