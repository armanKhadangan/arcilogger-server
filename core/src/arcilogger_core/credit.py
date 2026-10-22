"""Kamu SM credit (kontör) check: logged daily, alert below the threshold."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime

from arcilogger_core.alerts import raise_alert
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, utc_now
from arcilogger_core.tsa.base import TimestampProvider, TsaError


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
    try:
        credits = provider.credits()
    except TsaError as exc:
        entry.update(credits=None, error=str(exc))
        raise_alert(layout.alerts, "R20-credit", "medium", f"Kontör sorgulanamadı: {exc}", {})
        credits = None
    else:
        entry["credits"] = credits
        if credits is not None and credits < threshold:
            raise_alert(
                layout.alerts,
                "R20-credit",
                "high",
                f"Kamu SM kontörü azaldı: {credits} kaldı (eşik {threshold}). "
                "zdportal.kamusm.gov.tr üzerinden kontör alın.",
                {"credits": credits, "threshold": threshold},
            )
    layout.credit_log.parent.mkdir(parents=True, exist_ok=True)
    with layout.credit_log.open("ab") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return credits
