"""The one server function of the legal-validity status (Wave 5 contract 2.3, D11).

:func:`compute_legal_status` reads the configuration and the state files only: the sealing
records and the vault's tokens (:class:`~arcilogger_core.seal.Sealer`), the re-stamping report,
the stored revocation lists against the newest sealed day's chain and the scheduled
verifications, through the readers the R20 checks use
(:mod:`arcilogger_worker.alerts.system`). Nothing about log subjects is read, so the answer is the
same for every user.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from arcilogger_api.legalstatus.models import (
    LegalLevel,
    LegalLink,
    LegalReason,
    LegalReasonCode,
    LegalStatus,
)
from arcilogger_api.legalstatus.options import PLAIN_HTTP_PROVIDERS, LegalOptions, legal_options
from arcilogger_api.legalstatus.texts import certificate_name, provider_label, reason_text
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import day_window, isoformat_site
from arcilogger_worker.alerts.system import (
    CrlStatus,
    NoStamps,
    crl_stamp,
    crl_status,
    pinned_roots,
    read_crl_index,
    read_stamp_renewals,
    revoked_certificates,
    shifted,
    stamp_in_use,
    stored_lists,
)

log = logging.getLogger("arcilogger_api.legalstatus")

#: The reasons that make the stamps of the site unusable as evidence now (contract 2.3.1).
DANGER: frozenset[LegalReasonCode] = frozenset(
    {"provider_not_legal", "legal_root_missing", "unstamped_days", "certificate_revoked"}
)


def late_unstamped_days(layout: Layout, config: Config, now: datetime) -> list[date] | None:
    """The closed days without a time stamp longer than ``seal.late_after_hours`` after their
    end, oldest first: the Panel's blocking card and ``unstamped_days`` (D11). ``None``: unknown,
    when the spool or the seal states cannot be read (ADR-0007)."""
    sealer = Sealer(config, provider=NoStamps(), roots=[], clock=lambda: now)
    try:
        pending = sealer.closed_unsealed_days()
    except (OSError, RecursionError) as exc:  # RecursionError: a seal state nested too deeply
        log.info("unstamped days unknown (%s): %s", type(exc).__name__, exc)
        return None
    late = timedelta(hours=config.seal.late_after_hours)
    return [
        day
        for day in pending
        if not layout.token(day).is_file() and now > day_window(day)[1] + late
    ]


def _reason(
    code: LegalReasonCode,
    link: LegalLink | None,
    *,
    since: datetime | None = None,
    count: int | None = None,
    **values: object,
) -> LegalReason:
    message_tr, message_en = reason_text(code, count=count, **values)
    return LegalReason(
        code=code,
        level="danger" if code in DANGER else "warning",
        message_tr=message_tr,
        message_en=message_en,
        since=isoformat_site(since, "seconds") if since is not None else None,
        count=count,
        link=link,
    )


def _provider_reasons(config: Config) -> list[LegalReason]:
    tsa = config.tsa
    reasons = []
    if not tsa.legal_grade:
        reasons.append(_reason("provider_not_legal", "settings-tsa"))
    # The text states a fact: only a URL that is plain HTTP sends the password in clear text
    # (tsa.allow_plain_http alone, with an https:// URL, does not).
    if str(tsa.provider) in PLAIN_HTTP_PROVIDERS and urlparse(tsa.url).scheme == "http":
        reasons.append(_reason("credentials_in_clear", "settings-tsa"))
    if tsa.legal_grade and not tsa.legal_root_fingerprints:
        reasons.append(_reason("legal_root_missing", "settings-tsa"))
    return reasons


def _renewal_reasons(layout: Layout, options: LegalOptions) -> list[LegalReason]:
    renewals = read_stamp_renewals(layout) or ()
    reasons = []
    due = [renewal.cert_not_after for renewal in renewals if renewal.status == "due"]
    if due:
        before = timedelta(days=options.renew_before_days)
        reasons.append(
            _reason(
                "renewal_due",
                "sealing",
                since=shifted(min(due), -before),
                count=len(due),
                days=options.renew_before_days,
            )
        )
    uncovered = [renewal.cert_not_after for renewal in renewals if renewal.status == "uncovered"]
    if uncovered:
        reasons.append(
            _reason("renewal_uncovered", "sealing", since=min(uncovered), count=len(uncovered))
        )
    return reasons


def _crl_reasons(status: CrlStatus, options: LegalOptions) -> list[LegalReason]:
    """``crl_stale``: an issuer of the newest sealed day's chain without a fresh stored list
    (C22: fetched in the last ``tsa.crl_max_age_days`` days and not past its ``nextUpdate``;
    all of them when ``state/crl`` cannot be read). A chain to ARCILOGGER's test root has no
    issuer in use (``crl_stamp``, W5-C-13)."""
    if not status.stale:
        return []
    return [_reason("crl_stale", "settings-tsa", since=status.since, days=options.crl_max_age_days)]


def _sorted(reasons: Sequence[LegalReason]) -> list[LegalReason]:
    return sorted(reasons, key=lambda reason: (reason.level != "danger", reason.code))


def compute_legal_status(layout: Layout, config: Config, now: datetime) -> LegalStatus:
    """The legal-validity status at ``now``: every reason that holds, danger first."""
    options = legal_options(config)
    reasons = _provider_reasons(config)
    days = late_unstamped_days(layout, config, now)
    if days:
        late = timedelta(hours=options.late_after_hours)
        reasons.append(
            _reason(
                "unstamped_days",
                "sealing",
                since=day_window(days[0])[1] + late,
                count=len(days),
                hours=options.late_after_hours,
            )
        )
    reasons += _renewal_reasons(layout, options)
    # the newest sealed day's chain and the stored lists, read once for both reasons
    stamp = stamp_in_use(layout, pinned_roots(config, layout))
    lists = stored_lists(layout)
    revoked = revoked_certificates(layout, stamp, lists)
    if revoked:
        first = revoked[0]
        reasons.append(
            _reason(
                "certificate_revoked",
                "sealing",
                since=first.since,
                name=certificate_name(first.name),
            )
        )
    # ARCILOGGER's test root needs no list, as for R20-crl (W5-C-13, W5-E-5)
    index = read_crl_index(layout)
    status = crl_status(lists, index, crl_stamp(stamp), now, options.crl_max_age_days)
    reasons += _crl_reasons(status, options)
    ordered = _sorted(reasons)
    level: LegalLevel = ordered[0].level if ordered else "ok"
    return LegalStatus(
        level=level,
        provider=str(config.tsa.provider),
        provider_label=provider_label(str(config.tsa.provider), legal_grade=config.tsa.legal_grade),
        reasons=ordered,
        checked_at=isoformat_site(now, "seconds"),
    )
