"""ARCILOGGER core.

The evidence path of ARCILOGGER: daily sealing (hash, manifest, chain, time stamp), verification,
retention, replica and the time-stamp provider interface. Everything in this package must work
on its own, started by Windows Task Scheduler, even when the database, the API or the web
interface are down.

Modules available so far:

* :mod:`arcilogger_core.timeutil` — the time rules (UTC inside, Europe/Istanbul for people,
  receive-time day partitions).
* :mod:`arcilogger_core.fmt` — Turkish / English display formatting shared by reports and
  e-mails (the web interface has a TypeScript twin tested against the same vectors).
* :mod:`arcilogger_core.cli` — the ``arcilogger-core`` command line.
* :mod:`arcilogger_core.license` — offline licensing (ADR-0013): licence files, request codes,
  the server id, trusted time and the licence state. The evidence path never imports it.
"""

from typing import Final

__version__ = "0.1.0"

#: Release day of this version, bumped together with ``__version__``. The licence's
#: ``maintenance_until`` is compared with it (a version released after the maintenance period
#: runs with 5651 Temel only); Build-ArciBundle reads this exact line into the bundle manifest.
RELEASE_DATE: Final = "2026-10-20"

PRODUCT_NAME = "ARCILOGGER"
