"""What a security rule is, what it finds and what it is given (Wave 6 contract §2.1.2).

A rule (:class:`RuleDef`) is a function over the database with fixed defaults: its kind, severity,
window, cooldown and typed parameters (:class:`Param`). The engine evaluates it on one slice of
receive time with the validated settings (:class:`RuleSettings`) in a :class:`RuleContext`; the
rule answers its hits (:class:`Hit`), each with the rows behind it (:class:`EvidenceRef`).

**Every query of a rule**

* bounds ``time`` (device time) to the pruning bounds (:attr:`RuleContext.time_low`,
  :attr:`RuleContext.time_high`) **and** ``time_received`` to the window
  (:attr:`RuleContext.window_start`, :attr:`RuleContext.end`];
* selects only the group keys that have at least one row in the slice
  (:attr:`RuleContext.start`, :attr:`RuleContext.end`];
* passes every parameter as a bound parameter (lists as arrays, networks as ``cidr[]`` with
  ``<<=``), never inside the SQL text;
* returns evidence rows with ``id, time, time_received, source, raw_day, raw_line, raw_offset``.

Windows run on receive time (decision D8): a device clock that is minutes off does not hide or
split a burst; device time is kept in the evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from ipaddress import IPv4Network, IPv6Network
from typing import Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from arcilogger_db.models import ALERT_SEVERITIES, RULE_EVIDENCE_TABLES

ParamKind = Literal["int", "bytes", "seconds", "percent", "strings", "networks", "ints", "bool"]
RuleKind = Literal["event", "condition"]

#: The kinds of a parameter: one integer (a count, a size in bytes, a duration in seconds, a
#: percentage), a list (of strings, of CIDR networks, of integers) or a switch.
PARAM_KINDS: Final[tuple[ParamKind, ...]] = (
    "int",
    "bytes",
    "seconds",
    "percent",
    "strings",
    "networks",
    "ints",
    "bool",
)
#: The parameter kinds that hold one integer; ``min``/``max`` bound the value.
INTEGER_KINDS: Final = frozenset({"int", "bytes", "seconds", "percent"})
#: The parameter kinds that hold a list; ``min``/``max`` bound its length.
LIST_KINDS: Final = frozenset({"strings", "networks", "ints"})
#: ``event``: an alert per key, closed by a person; ``condition``: the rule resolves its alert
#: when the condition has been absent for ``clear_seconds`` (decision D9).
RULE_KINDS: Final[tuple[RuleKind, ...]] = ("event", "condition")
#: Longest list of a parameter and longest string in one (contract §2.1.4).
LIST_MAX: Final = 200
STRING_MAX: Final = 200
#: Bounds of a rule's window and cooldown (contract §2.1.4).
WINDOW_MIN: Final = 60
WINDOW_MAX: Final = 7 * 86_400
COOLDOWN_MAX: Final = 7 * 86_400

Network = IPv4Network | IPv6Network


@dataclass(frozen=True)
class Param:
    """One parameter of a rule an administrator may change (rule editor, ``rules.<ID>``)."""

    #: e.g. ``threshold_ports``.
    key: str
    kind: ParamKind
    #: The registry default; a list default is a tuple (immutable).
    default: object
    #: ``int``/``bytes``/``seconds``/``percent``: the value; lists: their length.
    min: int | None = None
    max: int | None = None
    label_tr: str = ""
    label_en: str = ""


@dataclass(frozen=True)
class Actions:
    """What a new alert of the rule sets off besides the Security page."""

    #: Through ``alerts.notify`` when the severity also reaches ``alerts.email_min_severity``.
    email: bool
    #: Through the webhooks (W6-H) that take this rule.
    webhook: bool


@dataclass(frozen=True)
class RuleSettings:
    """The effective settings of one rule: the registry defaults with the valid overrides of
    ``rules.<ID>`` (``arcilogger_worker.rules.settings``)."""

    enabled: bool
    severity: str
    window_seconds: int
    cooldown_seconds: int
    actions: Actions
    #: Every parameter of the rule by key: ``int`` for the integer kinds, ``bool``, a tuple of
    #: strings or integers, a tuple of networks.
    params: Mapping[str, object]

    def param(self, key: str) -> object:
        """The value of parameter ``key`` (``KeyError`` for a key the rule does not have)."""
        return self.params[key]


@dataclass(frozen=True)
class RuleContext:
    """What the engine gives a rule's ``evaluate`` for one slice."""

    settings: RuleSettings
    #: The slice: rows received in ``(start, end]`` are new.
    start: datetime
    end: datetime
    #: ``rules.max_device_skew_seconds``: device time is at most this far from receive time.
    max_device_skew: timedelta
    #: ``rules.internal_networks`` ("iç adres" for every rule).
    internal_networks: tuple[Network, ...]
    #: The site time zone (work hours, nights, site days).
    zone: ZoneInfo

    @property
    def window_start(self) -> datetime:
        """Start of the window that ends with the slice: rows received in
        ``(window_start, end]`` count."""
        return self.end - timedelta(seconds=self.settings.window_seconds)

    @property
    def time_low(self) -> datetime:
        """Lower device-time bound of every query (partition pruning): the earlier of the slice
        and the window start, less the skew, so a row of the window received before the slice
        (a window longer than the slice) is still read (deviation W6-A-1)."""
        return min(self.start, self.window_start) - self.max_device_skew

    @property
    def time_high(self) -> datetime:
        """Upper device-time bound of every query (partition pruning)."""
        return self.end + self.max_device_skew


@dataclass(frozen=True)
class EvidenceRef:
    """One row behind a hit: a reference, never the row's content (an ``alert_evidence`` row)."""

    #: A table of :data:`~arcilogger_db.models.RULE_EVIDENCE_TABLES`.
    table: str
    #: The row's ``id``; ``None`` for ``devices`` (keyed by MAC).
    row_id: int | None
    #: Device time (``devices``: ``first_seen``).
    time: datetime
    time_received: datetime | None = None
    source: str | None = None
    #: The raw reference ``GET /api/v1/raw`` opens; ``None`` for ``devices``.
    raw_day: date | None = None
    raw_line: int | None = None
    raw_offset: int | None = None

    def __post_init__(self) -> None:
        if self.table not in RULE_EVIDENCE_TABLES:
            raise ValueError(f"evidence table {self.table!r} is not a rule evidence table")
        raw = (
            self.row_id,
            self.time_received,
            self.source,
            self.raw_day,
            self.raw_line,
            self.raw_offset,
        )
        if self.table != "devices" and any(item is None for item in raw):
            raise ValueError(f"evidence of {self.table} needs its row id and raw reference")


@dataclass(frozen=True)
class Hit:
    """What a rule found for one key in one evaluation."""

    #: The group value, e.g. ``172.20.41.17`` (dedup key ``<ID>:<key>``).
    key: str
    #: Receive time of the newest row of the hit.
    time: datetime
    #: Rows behind the hit in its window.
    count: int
    #: The numbers and names the title and the details show (the title's fields, :mod:`.texts`).
    values: Mapping[str, object]
    #: At most ``rules.evidence_max`` rows, oldest first.
    evidence: Sequence[EvidenceRef] = ()
    #: Person-level rules (R09, R13, R14) only: the person of the address, never a name.
    person_id: int | None = None


Evaluate = Callable[[Session, RuleContext], Iterable[Hit]]


def _no_setup(_: RuleSettings) -> bool:
    return False


@dataclass(frozen=True, kw_only=True)
class RuleDef:
    """One security rule of the registry (:mod:`.registry`)."""

    #: ``R01`` … ``R19`` (not ``R15``, a 5651 Temel check of :mod:`arcilogger_worker.alerts`).
    id: str
    kind: RuleKind
    #: Default severity (:data:`~arcilogger_db.models.ALERT_SEVERITIES`).
    severity: str
    window_seconds: int
    cooldown_seconds: int
    params: tuple[Param, ...]
    #: The tables it reads, e.g. ``("fg_traffic",)`` (data status, ``rule_state.last_input_at``).
    inputs: tuple[str, ...]
    evaluate: Evaluate
    #: R09, R13, R14: the hit names a person (``Hit.person_id``).
    person_level: bool = False
    #: ``False``: the rule reads a log format the repository has no sample of ("sahada doğrula",
    #: contract §0.4).
    format_verified: bool = True
    #: ``True`` with the given settings: the rule stays idle until configured ("Ayar gerekli").
    needs_setup: Callable[[RuleSettings], bool] = field(default=_no_setup)
    #: Condition rules only: the alert resolves itself after this long without a hit.
    clear_seconds: int | None = None

    def __post_init__(self) -> None:
        problems = []
        if self.kind not in RULE_KINDS:
            problems.append(f"kind {self.kind!r}")
        if self.severity not in ALERT_SEVERITIES:
            problems.append(f"severity {self.severity!r}")
        if not WINDOW_MIN <= self.window_seconds <= WINDOW_MAX:
            problems.append(f"window_seconds {self.window_seconds}")
        if not 0 <= self.cooldown_seconds <= COOLDOWN_MAX:
            problems.append(f"cooldown_seconds {self.cooldown_seconds}")
        if (self.kind == "condition") != (self.clear_seconds is not None):
            problems.append("clear_seconds is for condition rules, and they need it")
        if not self.inputs:
            problems.append("no inputs")
        keys = [param.key for param in self.params]
        if len(set(keys)) != len(keys):
            problems.append("duplicate parameter keys")
        problems.extend(problem for problem in map(_param_problem, self.params) if problem)
        if problems:
            raise ValueError(f"rule {self.id}: " + "; ".join(problems))


def _param_problem(param: Param) -> str | None:
    """Why ``param``'s declaration cannot be used, or ``None``."""
    if param.kind not in PARAM_KINDS:
        return f"parameter {param.key}: kind {param.kind!r}"
    if not (param.label_tr and param.label_en):
        return f"parameter {param.key}: Turkish and English labels"
    default = param.default
    if param.kind == "bool":
        return None if type(default) is bool else f"parameter {param.key}: default not a bool"
    if param.kind in INTEGER_KINDS:
        if type(default) is not int:
            return f"parameter {param.key}: default not an integer"
        size = default
    elif not isinstance(default, tuple):
        return f"parameter {param.key}: a list default must be a tuple"
    else:
        size = len(default)
        if size > LIST_MAX:
            return f"parameter {param.key}: default longer than {LIST_MAX}"
    if param.min is not None and size < param.min:
        return f"parameter {param.key}: default below its minimum"
    if param.max is not None and size > param.max:
        return f"parameter {param.key}: default above its maximum"
    return None
