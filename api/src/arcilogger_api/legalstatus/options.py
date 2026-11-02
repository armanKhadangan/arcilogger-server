"""The settings the legal-validity status reads (Wave 5 contract 2.5).

The ``tsa`` keys of Wave 5 are added by the configuration package (W5-T); until they are in
:class:`~arcilogger_core.config.TsaConfig` they are read here with the contract's defaults, in
this one place (``crl_max_age_days`` in the worker's alert checks, which judge it too).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from arcilogger_core.config import Config
from arcilogger_worker.alerts.system import crl_max_age_days

#: Providers whose credentials cross the network with every request (``tsa.allow_plain_http``).
PLAIN_HTTP_PROVIDERS: Final[frozenset[str]] = frozenset({"turktrust"})


@dataclass(frozen=True)
class LegalOptions:
    #: A token is due for re-stamping this many days before its certificate expires.
    renew_before_days: int
    #: A revocation list issued longer ago than this is stale.
    crl_max_age_days: int
    allow_plain_http: bool
    #: A closed day unstamped for longer than this blocks (``seal.late_after_hours``).
    late_after_hours: int


def legal_options(config: Config) -> LegalOptions:
    tsa = config.tsa
    return LegalOptions(
        renew_before_days=int(getattr(tsa, "renew_before_days", 180)),
        crl_max_age_days=crl_max_age_days(config),  # shared with the R20-crl check
        allow_plain_http=bool(getattr(tsa, "allow_plain_http", False)),
        late_after_hours=config.seal.late_after_hours,
    )
