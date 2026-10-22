"""Constants of the licensing package, re-exported by :mod:`arcilogger_core.license`.

A separate module so that every submodule can import them without importing the package
``__init__`` (which re-exports the submodules).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final

import arcilogger_core

#: Length of the trial (every module on, no limits), counted from the installation's effective
#: trial start (licensing contract 3.4).
TRIAL_DAYS: Final = 14

#: Times before this instant are never a trial lower bound (no ARCILOGGER data is older).
PRODUCT_EPOCH: Final = datetime(2026, 1, 1, tzinfo=UTC)

#: The licensor: the rights holder who signs licence files (shown on the licence page).
LICENSOR_NAME: Final = "Arman Khadangan"
LICENSOR_EMAIL: Final = "arman@khadangan.com"

#: ``product`` of every licence payload.
PRODUCT: Final = "ARCILOGGER"


def release_date() -> date:
    """The release day of this version (``arcilogger_core.RELEASE_DATE``)."""
    return date.fromisoformat(arcilogger_core.RELEASE_DATE)
