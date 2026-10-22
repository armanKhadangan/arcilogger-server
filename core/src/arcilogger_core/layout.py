"""Where ARCILOGGER keeps its files under the data directory (``D:\\ARCILOGGER`` on the server).

::

    spool/<source>/<YYYY-MM-DD>.log            today's raw files (collector, append-only)
    vault/<YYYY>/<MM>/<DD>/<source>.log.gz     sealed raw files
    vault/<YYYY>/<MM>/<DD>/audit.jsonl.gz      ARCILOGGER audit log of the day
    vault/<YYYY>/<MM>/<DD>/manifest-<day>.json and manifest-<day>.json.zd
    vault/deletions/                           sealed deletion records and the chain anchor
    exports/                                   evidence packages
    state/                                     job state, collector status, clock samples
    logs/                                      service logs
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

SPOOL_SUFFIX = ".log"
AUDIT_SOURCE = "_audit"


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def spool(self) -> Path:
        return self.root / "spool"

    @property
    def vault(self) -> Path:
        return self.root / "vault"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def deletions(self) -> Path:
        return self.vault / "deletions"

    def spool_file(self, source: str, day: date) -> Path:
        return self.spool / source / f"{day.isoformat()}{SPOOL_SUFFIX}"

    def audit_spool(self, day: date) -> Path:
        """Audit entries of a day, written by the API (milestone M6) before sealing."""
        return self.spool / AUDIT_SOURCE / f"{day.isoformat()}.jsonl"

    def vault_day(self, day: date) -> Path:
        return self.vault / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"

    def manifest(self, day: date) -> Path:
        return self.vault_day(day) / f"manifest-{day.isoformat()}.json"

    def token(self, day: date) -> Path:
        return self.vault_day(day) / f"manifest-{day.isoformat()}.json.zd"

    def seal_state(self, day: date) -> Path:
        return self.state / "seal" / f"{day.isoformat()}.json"

    def replica_state(self, day: date) -> Path:
        return self.state / "replica" / f"{day.isoformat()}.json"

    def clock_samples(self, day: date) -> Path:
        return self.state / "clock" / f"{day.isoformat()}.jsonl"

    @property
    def collector_status(self) -> Path:
        return self.state / "collector-status.json"

    @property
    def alerts(self) -> Path:
        return self.state / "alerts.jsonl"

    @property
    def credit_log(self) -> Path:
        return self.state / "credit.jsonl"

    @property
    def secret_changes(self) -> Path:
        """Who changed which stored credential, when (``arcilogger-core secret set|delete``;
        never a value). In ``state\\seal``, which only administrators and the evidence tasks may
        write (ADR-0007); the worker reads it into the audit log (``settings.secret_change``)."""
        return self.state / "seal" / "secret-changes.jsonl"

    @property
    def chain_anchor(self) -> Path:
        return self.deletions / "chain-anchor.json"

    def spool_days(self) -> list[date]:
        """All days that have at least one spool file, oldest first."""
        days: set[date] = set()
        if self.spool.is_dir():
            for source_dir in self.spool.iterdir():
                # The name first: spool\_audit is protected, a process that may only list the
                # spool (the worker, ADR-0007) need not even look at it.
                if source_dir.name == AUDIT_SOURCE or not source_dir.is_dir():
                    continue
                for file in source_dir.glob(f"*{SPOOL_SUFFIX}"):
                    try:
                        days.add(date.fromisoformat(file.stem))
                    except ValueError:
                        continue
        return sorted(days)

    def spool_files_for(self, day: date) -> list[tuple[str, Path]]:
        """``(source, path)`` for every spool file of ``day``, sorted by source name."""
        if not self.spool.is_dir():
            return []
        found = []
        for source_dir in sorted(self.spool.iterdir()):
            if not source_dir.is_dir() or source_dir.name == AUDIT_SOURCE:
                continue
            candidate = source_dir / f"{day.isoformat()}{SPOOL_SUFFIX}"
            if candidate.is_file():
                found.append((source_dir.name, candidate))
        return found
