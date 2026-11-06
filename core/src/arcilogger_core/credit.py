"""Kamu SM credit (kontör) check: logged daily, alert below the threshold.

A provider without a credit query (:func:`credit_query`: ``rfc3161``, ``turktrust``) is logged as
``{"supported": false}`` and never raises ``R20-credit`` (Wave 5 contract §2.4, C21); ``GET
/credits`` then reports ``supported: false``, and Uyum Durumu card 5 leaves the credit out
(#220)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from typing import Final

from arcilogger_core.alerts import raise_alert
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, utc_now
from arcilogger_core.tsa.base import TimestampProvider, TsaError

#: The providers without a credit query: their :meth:`credits` answers None by design (C21).
NO_CREDIT_QUERY: Final = frozenset({"rfc3161", "turktrust"})


def credit_query(provider: str) -> bool:
    """Whether ARCILOGGER queries the credit of ``provider`` (``tsa.provider``, a provider's
    ``name``): Zamane and the mock TSA, not ``rfc3161`` or ``turktrust`` (C21)."""
    return provider not in NO_CREDIT_QUERY


def check_credit(
    provider: TimestampProvider,
    layout: Layout,
    threshold: int,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> int | None:
    entry: dict[str, object] = {
        "time": isoformat_site(clock(), "seconds"),
        "provider": provider.name,
    }
    if not credit_query(provider.name):
        # No query is sent (C21): the entry says so, and nothing is raised.
        entry.update(credits=None, supported=False)
        credits = None
    else:
        credits = _query(provider, layout, threshold, entry)
    layout.credit_log.parent.mkdir(parents=True, exist_ok=True)
    with layout.credit_log.open("ab") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return credits


def _query(
    provider: TimestampProvider, layout: Layout, threshold: int, entry: dict[str, object]
) -> int | None:
    """The credit of a provider with a credit query, ``R20-credit`` when it fails or is low."""
    try:
        credits = provider.credits()
    except TsaError as exc:
        entry.update(credits=None, error=str(exc))
        raise_alert(layout.alerts, "R20-credit", "medium", f"Kontör sorgulanamadı: {exc}", {})
        credits = None
    else:
        entry["credits"] = credits
        if credits is None:
            entry["supported"] = False
        elif credits < threshold:
            raise_alert(
                layout.alerts,
                "R20-credit",
                "high",
                f"Kamu SM kontörü azaldı: {credits} kaldı (eşik {threshold}). "
                "zdportal.kamusm.gov.tr üzerinden kontör alın.",
                {"credits": credits, "threshold": threshold},
            )
    return credits
