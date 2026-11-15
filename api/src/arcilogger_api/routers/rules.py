"""``/api/v1/rules`` — the security rules R01-R19 and their editor (Wave 6 contract §2.3, §2.6.1).

* ``GET /rules`` (``alerts.read``) — every registered rule: its texts, kind, inputs, flags, data
  status (``ok``, ``no_data``, ``never``), last run and error, skipped backlog, the registry
  defaults, the effective settings, its parameters and their bounds, and its advice. Readable
  without the licence module (the editor shows the lock: ``module_enabled``).
* ``PUT /rules/{id}`` (``settings.manage``, module ``security_rules``) — body in the
  ``rules.<ID>`` shape; stored is only what differs from the registry defaults, audited as
  ``rule.update`` with the full effective settings before and after (no person data).
* ``POST /rules/{id}/dry-run`` (``settings.manage``, module) — "Son 24 saatte dene": how many
  alerts the candidate settings would have opened in the last 24 hours
  (:func:`arcilogger_worker.rules.dryrun.dry_run`); one at a time per API process.

Errors: 404 ``rule_not_found``, 422 ``rule_param_invalid`` (``details.field``), 409
``rule_dry_run_busy``, 504 ``rule_dry_run_timeout``; without the module 403
``module_not_licensed`` (the licence code of every paid route).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Body, Depends, Path, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.deps import DbSession, SessionFactoryDep
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import guarded_status, require_module
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import RuleState
from arcilogger_db.settings import get_setting, set_setting
from arcilogger_worker.rules import registry
from arcilogger_worker.rules.dryrun import DryRunTimeoutError, dry_run
from arcilogger_worker.rules.engine import data_status
from arcilogger_worker.rules.model import RuleDef
from arcilogger_worker.rules.settings import (
    RuleSettingsError,
    as_json,
    defaults,
    effective,
    engine_settings,
    setting_key,
    validate_overrides,
)
from arcilogger_worker.rules.texts import STARTING_VALUE, TEXTS

router = APIRouter(prefix="/rules", tags=["rules"])

#: Wave 6 decision D1 (``arcilogger_core.license.modules``; the key, so the API never loads the
#: licence code at import).
SECURITY_RULES_MODULE: Final = "security_rules"
ReaderDep = Annotated[CurrentUser, Depends(require("alerts.read"))]
EditorDep = Annotated[CurrentUser, Depends(require("settings.manage"))]
RuleId = Annotated[str, Path(pattern=r"^R[0-9]{2}$")]
#: The module of the rules' writes (decision D1); the list stays readable without it.
LICENSED: Final = [require_module(SECURITY_RULES_MODULE)]

MESSAGES: Final = {
    "rule_not_found": ("Böyle bir güvenlik kuralı yok.", "No such security rule."),
    "rule_param_invalid": ("Kural ayarı geçersiz: {field}.", "Invalid rule setting: {field}."),
    "rule_dry_run_busy": (
        "Başka bir deneme sürüyor; bitince tekrar deneyin.",
        "Another trial run is in progress; try again when it has finished.",
    ),
    "rule_dry_run_timeout": (
        "Deneme {n} sn içinde bitmedi; pencereyi ya da listeleri küçültün.",
        "The trial run did not finish within {n} s; shorten the window or the lists.",
    ),
}
#: One dry run at a time per API process (contract §2.1.6).
_DRY_RUN: Final = threading.Lock()


def _error(status: int, code: str, *, details: Any = None, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(
        status, code, text_tr.format(**params), text_en.format(**params), details=details
    )


# -- models ---------------------------------------------------------------------------------------


class ParamOut(BaseModel):
    key: str
    kind: Literal["int", "bytes", "seconds", "percent", "strings", "networks", "ints", "bool"]
    min: int | None
    max: int | None
    label_tr: str
    label_en: str


class RuleOut(BaseModel):
    id: str
    name_tr: str
    name_en: str
    kind: Literal["event", "condition"]
    inputs: list[str]
    person_level: bool
    #: ``false``: "Sahada doğrula" (the rule reads a log format without a sample).
    format_verified: bool
    #: "Ayar gerekli": the rule stays idle with its current settings.
    needs_setup: bool
    data_status: Literal["ok", "no_data", "never"]
    last_input_at: datetime | None
    last_run_at: datetime | None
    #: The class of the last failure (no text, no value), or null.
    last_error: str | None
    #: A backlog was skipped up to this moment.
    skipped_until: datetime | None
    #: The registry defaults and the effective settings, both in the ``rules.<ID>`` shape.
    defaults: dict[str, Any]
    settings: dict[str, Any]
    params: list[ParamOut]
    advice_tr: str
    advice_en: str
    #: "Başlangıç değeri; ağınıza göre ayarlayın."
    note_tr: str
    note_en: str


class RulesOut(BaseModel):
    module_enabled: bool
    rules: list[RuleOut]


class DryRunKey(BaseModel):
    key: str
    count: int
    first_time: datetime
    last_time: datetime


class DryRunOut(BaseModel):
    would_open: int
    keys: list[DryRunKey]
    truncated: bool
    duration_ms: int


# -- helpers --------------------------------------------------------------------------------------


def _rule(rule_id: str) -> RuleDef:
    try:
        return registry.rule(rule_id)
    except KeyError:
        raise _error(404, "rule_not_found") from None


def rule_out(db: Session, rule: RuleDef, state: RuleState | None, now: datetime) -> RuleOut:
    texts = TEXTS[rule.id]
    settings = effective(rule, get_setting(db, setting_key(rule.id)))
    no_data = engine_settings(db).no_data
    last_input = state.last_input_at if state is not None else None
    return RuleOut(
        id=rule.id,
        name_tr=texts.name_tr,
        name_en=texts.name_en,
        kind=rule.kind,
        inputs=list(rule.inputs),
        person_level=rule.person_level,
        format_verified=rule.format_verified,
        needs_setup=rule.needs_setup(settings),
        data_status=data_status(last_input, now, no_data),
        last_input_at=last_input,
        last_run_at=state.last_run_at if state is not None else None,
        last_error=state.last_error if state is not None else None,
        skipped_until=state.skipped_until if state is not None else None,
        defaults=as_json(defaults(rule)),
        settings=as_json(settings),
        params=[
            ParamOut(
                key=param.key,
                kind=param.kind,
                min=param.min,
                max=param.max,
                label_tr=param.label_tr,
                label_en=param.label_en,
            )
            for param in rule.params
        ],
        advice_tr=texts.advice_tr,
        advice_en=texts.advice_en,
        note_tr=STARTING_VALUE[0],
        note_en=STARTING_VALUE[1],
    )


def _states(db: Session) -> dict[str, RuleState]:
    return {state.rule: state for state in db.scalars(select(RuleState))}


def _differences(rule: RuleDef, stored: Mapping[str, Any]) -> dict[str, Any]:
    """Only what differs from the registry defaults (contract §2.1.4)."""
    base = as_json(defaults(rule))
    kept: dict[str, Any] = {}
    for key, value in stored.items():
        if key in ("params", "actions"):
            entries = {
                name: item
                for name, item in value.items()
                if as_json(effective(rule, {key: {name: item}}, strict=True))[key][name]
                != base[key][name]
            }
            if entries:
                kept[key] = entries
        elif value != base[key]:
            kept[key] = value
    return kept


def _validated(rule: RuleDef, body: object) -> dict[str, Any]:
    try:
        return validate_overrides(rule, body)
    except RuleSettingsError as exc:
        raise _error(
            422, "rule_param_invalid", details={"field": exc.field}, field=exc.field
        ) from None


# -- routes ---------------------------------------------------------------------------------------


@router.get("")
def list_rules(request: Request, db: DbSession, user: ReaderDep) -> RulesOut:
    """Every registered rule with its settings and state (the editor's table)."""
    enabled = guarded_status(request, db).module_enabled(SECURITY_RULES_MODULE)
    states = _states(db)
    now = utc_now()
    return RulesOut(
        module_enabled=enabled,
        rules=[rule_out(db, rule, states.get(rule.id), now) for rule in registry.RULES],
    )


@router.put("/{rule_id}", dependencies=LICENSED)
def update_rule(
    rule_id: RuleId,
    request: Request,
    db: DbSession,
    user: EditorDep,
    body: Annotated[dict[str, Any], Body()],
) -> RuleOut:
    """Save the settings of one rule (``rules.<ID>``), audited as ``rule.update``."""
    rule = _rule(rule_id)
    stored = _differences(rule, _validated(rule, body))
    key = setting_key(rule.id)
    before = as_json(effective(rule, get_setting(db, key)))
    set_setting(db, key, stored, updated_by=user.username)
    after = as_json(effective(rule, stored, strict=True))
    record_audit(
        db,
        actor=user,
        action="rule.update",
        target=f"rule:{rule.id}",
        details={"rule": rule.id, "before": before, "after": after},
        ip=client_ip(request),
    )
    db.commit()
    return rule_out(db, rule, _states(db).get(rule.id), utc_now())


@router.post("/{rule_id}/dry-run", dependencies=LICENSED)
def dry_run_rule(
    rule_id: RuleId,
    factory: SessionFactoryDep,
    user: EditorDep,
    body: Annotated[dict[str, Any], Body()],
) -> DryRunOut:
    """ "Son 24 saatte dene": what these settings would have raised in the last 24 hours (writes
    nothing)."""
    rule = _rule(rule_id)
    _validated(rule, body)
    if not _DRY_RUN.acquire(blocking=False):
        raise _error(409, "rule_dry_run_busy")
    try:
        with factory() as session:
            result = dry_run(session, rule, body, utc_now())
    except DryRunTimeoutError as exc:
        raise _error(
            504, "rule_dry_run_timeout", details={"seconds": exc.seconds}, n=exc.seconds
        ) from None
    finally:
        _DRY_RUN.release()
    return DryRunOut.model_validate(result.as_json())
