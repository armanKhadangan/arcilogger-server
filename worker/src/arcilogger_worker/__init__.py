"""ARCILOGGER worker (Windows service ``ArciWorker``).

Scheduled application jobs and the 5651 lookup engine:

* :mod:`.correlation` — lease timeline (IP ↔ MAC over time, spec 9.1), devices, IP conflicts;
* :mod:`.lookup` — the 5651 lookup: types (contract) and engine (spec 9.3-9.5);
* :mod:`.service` — the job loop (incremental leases every minute, nightly rebuild, partitions);
* :mod:`.reports` — the scheduled usage reports (HTML e-mail, PDF, Excel; ADR-0012) and
  :mod:`.pdf`, the PDF building blocks it shares with the 5651 report of the API;
* :mod:`.cli` — ``arcilogger-worker run | rebuild --day | version``.

Usage rollups (M4) and security detections (M5) come later. Evidence jobs
(sealing, verification, retention, replica) are **not** here — they belong to
``arcilogger-core`` and run from Windows Task Scheduler without the database.
"""

__version__ = "0.1.0"
