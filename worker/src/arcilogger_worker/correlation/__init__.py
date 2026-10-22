"""Identity correlation (spec 9.1, 9.2): lease timeline, devices, IP conflicts.

* :mod:`.timeline` — the lease rules as a pure computation (no database).
* :mod:`.leases` — the database jobs: full day rebuild and the incremental (every minute) mode.
* :mod:`.watermark` — how far the incremental job got (setting ``worker.leases_watermark``).
* :mod:`.devices` / :mod:`.oui` — device inventory and the optional vendor lookup.
* :mod:`.conflicts` — IP conflict detection and its alerts.

Nothing is imported here on purpose: :mod:`arcilogger_worker.lookup` imports its engine eagerly
(contract), so a package-level import here could close an import cycle.
"""
