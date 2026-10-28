"""Content-filter evidence of the FortiGate sources (Wave 4 contract 2.4): card 7 of Uyum Durumu
and, from W4-F on, the ``R20-filter`` check. Imports nothing from ``arcilogger_api``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FilterEvidence:
    applies: bool  # an enabled, non-unknown source of type "fortigate" exists
    last_at: datetime | None  # newest `time` of fg_web or fg_dns within 8 days
    today: Mapping[str, int]  # {"fg_web": n, "fg_dns": n}, the site day so far
    days: Sequence[tuple[date, int, int]]  # last 7 site days: (day, web, dns), newest first
    web_profiles: Sequence[str]  # distinct profiles of the newest 10 000 fg_web rows of 24 h
    window_hours: int  # alerts.filter_quiet_hours
    window_expected: bool  # every site day touched by [now - window, now] is expected
    alert_due: bool  # applies and no record in the window and window_expected
    silent_kinds: Sequence[str]  # "web"/"dns" seen in the 7 days but not in the window


def filter_evidence(session: Session, *, now: datetime) -> FilterEvidence:
    raise NotImplementedError  # W4-A: being written
