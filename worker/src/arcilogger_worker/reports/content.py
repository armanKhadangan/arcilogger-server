"""The content of one usage report: the engine's report data plus what the report adds itself.

:func:`build_document` returns one JSON object (``schema`` 1) that the three renderers
(:mod:`.html`, :mod:`.pdf`, :mod:`.xlsx`) turn into files — the same object for all three, so the
e-mail, the PDF and the workbook never disagree::

    {"schema": 1,
     "report": {"name", "kind", "run_id", "schedule_id", "site_name", "web_url",
                "generated_at", "version", "preview"},
     "data":   <build_report_data(...)>,      # group 2 contract section 3 (area U)
     "alerts": {"total", "listed": [...], "by_severity": {...}},
     "seal":   {"day", "status", "sealed_at", "serial", "provider", "test_stamp"}}

* **Alerts of the period** come from the ``alerts`` table — time, rule, severity, state and the
  rule's name and one sentence from the **catalogue** (:mod:`arcilogger_worker.alerts.texts`,
  whitelisted facts only), never an alert's stored title or details: those may carry error
  texts or, for later rules, log fields (ADR-0010). No raw log line reaches a report.
* **Sealing status of the previous day** (the day before the run's site day) is read from the
  files the Seal task writes, like the sealing page does: manifest and time stamp in the vault
  and ``state/seal/<day>.json``. The worker may read both (ADR-0007); it may not list the spool,
  so a day without any state is "not sealed yet", never "no data".
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day
from arcilogger_db.models import Alert
from arcilogger_worker import __version__
from arcilogger_worker.alerts.store import SEVERITY_RANK
from arcilogger_worker.alerts.texts import describe, rule_text
from arcilogger_worker.usage import report_data

DOCUMENT_SCHEMA: Final = 1
#: Most alerts listed one by one (the rest are counted).
MAX_ALERTS: Final = 200

ReportBuilder = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ReportMeta:
    """Who and what a report is (the ``report`` part of the document)."""

    name: str
    kind: str
    site_name: str
    web_url: str
    generated_at: datetime
    run_id: int | None = None
    schedule_id: int | None = None
    #: Rendered for ``GET /reports/preview``: never stored, never sent.
    preview: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "run_id": self.run_id,
            "schedule_id": self.schedule_id,
            "site_name": self.site_name,
            "web_url": self.web_url,
            "generated_at": self.generated_at.isoformat(),
            "version": __version__,
            "preview": self.preview,
        }


# -- alerts of the period -------------------------------------------------------------------------


def period_alerts(session: Session, start: datetime, end: datetime) -> dict[str, Any]:
    """Alerts raised in ``[start, end)``: most severe first, then newest; catalogue texts."""
    rows = list(
        session.scalars(
            select(Alert).where(Alert.time >= start, Alert.time < end).order_by(Alert.time.desc())
        )
    )
    rows.sort(key=lambda row: -SEVERITY_RANK.get(row.severity, 0))
    listed = []
    for row in rows[:MAX_ALERTS]:
        text = rule_text(row.rule)
        sentence_tr, sentence_en = describe(row.rule, dict(row.details or {}))
        listed.append(
            {
                "time": row.time.isoformat(),
                "rule": row.rule,
                "severity": row.severity,
                "state": row.state,
                "name_tr": text.name_tr,
                "name_en": text.name_en,
                "text_tr": sentence_tr,
                "text_en": sentence_en,
            }
        )
    return {
        "total": len(rows),
        "listed": listed,
        "by_severity": dict(Counter(row.severity for row in rows)),
    }


# -- sealing status of the previous day -----------------------------------------------------------


def _read_state(layout: Layout, day: date) -> dict[str, Any] | None:
    try:
        data = json.loads(layout.seal_state(day).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"status": "unreadable"}
    return data if isinstance(data, dict) else {"status": "unreadable"}


def seal_status(layout: Layout, day: date) -> dict[str, Any]:
    """``sealed`` | ``pending`` | ``failed`` | ``skipped`` of ``day`` (see the module text)."""
    state = _read_state(layout, day)
    status = (state or {}).get("status")
    in_vault = layout.manifest(day).is_file() and layout.token(day).is_file()
    if status == "skipped":
        result = "skipped"
    elif in_vault and status in (None, "sealed", "deleted"):
        result = "sealed"
    elif state is not None and (status == "unreadable" or state.get("last_error")):
        result = "failed"
    else:
        result = "pending"
    sealed = result == "sealed" and state is not None
    provider = (state or {}).get("provider") if sealed else None
    return {
        "day": day.isoformat(),
        "status": result,
        "sealed_at": (state or {}).get("sealed_at") if sealed else None,
        "serial": (state or {}).get("serial") if sealed else None,
        "provider": provider if isinstance(provider, str) else None,
        "test_stamp": provider == "mock",
    }


# -- the document ---------------------------------------------------------------------------------


def assemble(
    data: Mapping[str, Any],
    *,
    meta: ReportMeta,
    alerts: Mapping[str, Any],
    seal: Mapping[str, Any],
) -> dict[str, Any]:
    """The document of the renderers (a JSON-safe copy of everything)."""
    document = {
        "schema": DOCUMENT_SCHEMA,
        "report": meta.as_json(),
        "data": data,
        "alerts": alerts,
        "seal": seal,
    }
    copied: dict[str, Any] = json.loads(
        json.dumps(document, ensure_ascii=False, default=_json_value)
    )
    return copied


def _json_value(value: Any) -> Any:
    """Times as ISO-8601 text (the engine's contract asks for text; this keeps a ``datetime``
    from failing a report)."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"not JSON: {type(value).__name__}")


def build_document(
    session: Session,
    layout: Layout,
    *,
    meta: ReportMeta,
    start: datetime,
    end: datetime,
    person_level: bool,
    pseudonymize: bool,
    builder: ReportBuilder | None = None,
    pseudonym_key: bytes | None = None,
) -> dict[str, Any]:
    """Everything one report shows (read-only; the caller's session is not written to).

    ``pseudonym_key``: the secret ``usage.pseudonym_key`` of the caller's secret store, for a
    report with pseudonyms (without it the engine reads the default configuration's).
    """
    key: dict[str, Any] = {} if pseudonym_key is None else {"pseudonym_key": pseudonym_key}
    # Looked up at call time: the engine's module (area U) may be replaced in tests.
    data = (builder or report_data.build_report_data)(
        session.connection(),
        start=start,
        end=end,
        kind=meta.kind,
        person_level=person_level,
        pseudonymize=pseudonymize,
        **key,
    )
    alerts = period_alerts(session, start, end)
    seal = seal_status(layout, site_day(meta.generated_at) - timedelta(days=1))
    return assemble(data, meta=meta, alerts=alerts, seal=seal)
