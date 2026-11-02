"""Requests and answers of ``/api/v1/search`` (contract 2.10; docs/MVP-CONTRACTS.md section 17).

Request bodies are strict (``extra="forbid"``). The answers are the shapes the web app is
checked against (``web/src/generated/api-schemas.json``, ``web/src/lib/search-types.ts``):
times are aware ISO-8601, ``from``/``to`` are named so on the wire (``from_`` in Python).
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from arcilogger_db.models import SAVED_SEARCH_NAME_MAX, SAVED_SEARCH_QUERY_MAX

#: The page size when a search names none (the web app's default).
DEFAULT_LIMIT = 100
#: Largest number of evidence references (the evidence extract's maximum, MAX_EXTRACT_LINES).
MAX_REFS = 10_000
#: Largest saved-search id and row id accepted (PostgreSQL ``bigint``).
MAX_ID = 2**63 - 1
#: A ``search_id`` is ``secrets.token_urlsafe(16)``; anything far longer is no search of ours.
SearchId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
QueryText = Annotated[str, StringConstraints(max_length=SAVED_SEARCH_QUERY_MAX)]
#: ``models.SAVED_SEARCH_PRESETS`` (a test keeps the two equal).
Preset = Literal["15m", "1h", "24h", "today", "yesterday", "7d", "30d"]
#: Not in a saved search's name: control (NUL breaks PostgreSQL text), format (bidi overrides
#: disguise a name) and lone surrogate characters (not UTF-8), as in ``auth.schemas``.
_FORBIDDEN: Final = frozenset({"Cc", "Cf", "Cs"})


def _single_line(value: str) -> str:
    if any(unicodedata.category(char) in _FORBIDDEN for char in value):
        raise ValueError("control characters are not allowed")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Answer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# -- requests -------------------------------------------------------------------------------------


class SearchRequest(StrictModel):
    """``POST /search``. With ``saved_id`` the saved search's query and range are used (its
    preset resolved against now); ``q``, ``from`` and ``to`` may then be left out."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    q: QueryText = ""
    from_: AwareDatetime | None = Field(default=None, alias="from")
    to: AwareDatetime | None = None
    #: 1 … ``search.page_size_max`` (checked by the endpoint, which reads the setting); without
    #: it 100, or the maximum when that is smaller.
    limit: int | None = Field(default=None, ge=1, le=1000)
    saved_id: int | None = Field(default=None, ge=1, le=MAX_ID)


class SearchPageRequest(StrictModel):
    search_id: SearchId
    cursor: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    limit: int | None = Field(default=None, ge=1, le=1000)


class SearchIdRequest(StrictModel):
    """``POST /search/stats``."""

    search_id: SearchId


class ParseRequest(StrictModel):
    q: Annotated[str, StringConstraints(max_length=10_000)]


class SearchRefsRequest(StrictModel):
    search_id: SearchId
    limit: int = Field(default=MAX_REFS, ge=1, le=MAX_REFS)


class SearchExportRequest(StrictModel):
    search_id: SearchId
    format: Literal["csv", "xlsx"]


class PresetRange(StrictModel):
    preset: Preset


class FixedRange(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: AwareDatetime = Field(alias="from")
    to: AwareDatetime


class SavedSearchIn(StrictModel):
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=SAVED_SEARCH_NAME_MAX),
        AfterValidator(_single_line),
    ]
    q: QueryText
    range: PresetRange | FixedRange


# -- shared parts ---------------------------------------------------------------------------------


class Notice(Answer):
    code: str
    message_tr: str
    message_en: str
    params: dict[str, Any]


class RawRef(Answer):
    """A raw line: line ``line`` of ``spool/<source>/<day>.log`` (or the sealed file), which
    starts at byte ``offset``."""

    source: str
    day: date
    line: int
    offset: int


class SearchClause(Answer):
    field: str
    #: ``match``, ``present`` (``field:*``) or ``text`` (free text).
    op: Literal["match", "present", "text"]
    values: list[str]
    negated: bool
    start: int
    end: int


class SearchQuery(Answer):
    normalized: str
    clauses: list[SearchClause]
    #: The free-text terms every row contains (for highlighting).
    text: list[str]


class SearchRange(Answer):
    from_: datetime = Field(alias="from")
    to: datetime


class SearchPlan(Answer):
    driver: Literal["messages", "typed"]
    tables: list[str]
    kinds: list[str]
    selective: bool
    rollup_eligible: bool


class SearchScan(Answer):
    complete: bool
    scanned_from: datetime
    scanned_to: datetime
    windows: int
    elapsed_ms: int


class SearchSummary(Answer):
    """The typed fields of a row, for the "Özet" column (only those the row has)."""

    srcip: str | None = None
    srcport: int | None = None
    dstip: str | None = None
    dstport: int | None = None
    natip: str | None = None
    natport: int | None = None
    action: str | None = None
    domain: str | None = None
    url: str | None = None
    app: str | None = None
    category: str | None = None
    user: str | None = None
    mac: str | None = None
    proto: str | None = None
    policy: str | None = None
    interface: str | None = None
    vlan_id: int | None = None


class SearchRow(Answer):
    #: ``<table>:<time UTC µs>:<id>`` (``GET /search/event`` takes the three parts).
    key: str
    table: str
    time: datetime
    time_received: datetime
    source: str
    sender: str
    kind: str
    type: str | None
    subtype: str | None
    summary: SearchSummary
    #: ``messages.text``, at most 500 characters, display-safe.
    text: str | None
    raw: RawRef
    parse_error: str | None


class SearchPage(Answer):
    search_id: str
    expires_at: datetime
    query: SearchQuery
    range: SearchRange
    plan: SearchPlan
    items: list[SearchRow]
    next_cursor: str | None
    scan: SearchScan
    notices: list[Notice]


class SearchTotal(Answer):
    value: int
    #: ``false``: the count stopped at the time budget ("≥ value").
    exact: bool


class SearchBucket(Answer):
    start: datetime
    count: int


class SearchHistogram(Answer):
    bucket_seconds: int
    buckets: list[SearchBucket]


class FacetValue(Answer):
    value: str
    label_tr: str
    label_en: str
    count: int


class Facet(Answer):
    status: Literal["ok", "sampled", "unavailable"]
    values: list[FacetValue]
    other: int


class SearchFacets(Answer):
    source: Facet
    kind: Facet
    action: Facet
    vlan: Facet
    department: Facet


class SearchStats(Answer):
    search_id: str
    mode: Literal["rollup", "hybrid", "direct"]
    complete: bool
    covered_from: datetime
    covered_to: datetime
    total: SearchTotal
    histogram: SearchHistogram
    facets: SearchFacets
    sampled: bool
    sample_rows: int | None
    notices: list[Notice]
    elapsed_ms: int


class ParseErrorItem(Answer):
    code: str
    message_tr: str
    message_en: str
    start: int
    end: int


class ParseResult(Answer):
    normalized: str
    clauses: list[SearchClause]
    kinds: list[str]
    selective: bool
    errors: list[ParseErrorItem]


class CatalogueField(Answer):
    name: str
    aliases: list[str]
    #: The value form (``ip``, ``port``, ``domain``, ``text`` …; docs/ARAMA.md).
    kind: str
    label_tr: str
    label_en: str
    help_tr: str
    help_en: str
    tables: list[str]
    indexed: list[str]
    examples: list[str]


class CatalogueKind(Answer):
    key: str
    label_tr: str
    label_en: str


class CatalogueSource(Answer):
    name: str
    type: str
    #: A configured address range (its members are ``<name>-<ip>`` folders).
    network: bool


class CatalogueLimits(Answer):
    max_chars: int
    max_clauses: int
    max_values: int
    max_terms: int
    max_days: int
    page_size_max: int


class FieldCatalogue(Answer):
    fields: list[CatalogueField]
    kinds: list[CatalogueKind]
    sources: list[CatalogueSource]
    limits: CatalogueLimits


class SearchEventMessage(Answer):
    type: str
    subtype: str | None
    severity: int | None
    facility: int | None
    program: str | None
    host: str | None
    text: str
    parse_error: str | None


class SearchEvent(Answer):
    table: str
    kind: str
    #: Every column of the row but ``extra`` (addresses and times as text).
    fields: dict[str, Any]
    extra: dict[str, Any]
    message: SearchEventMessage | None
    raw: RawRef


class SearchRefs(Answer):
    refs: list[RawRef]
    #: The line limit cut the list (the search has more rows).
    truncated: bool
    #: The whole search was read (``false``: the time budget ran out first).
    complete: bool
    #: Days of the list whose file is not sealed yet (an evidence package refuses them).
    unsealed_days: list[date]
    #: SHA-256 of the list as canonical JSON (RFC 8785), as audited.
    sha256: str


class SavedSearch(Answer):
    id: int
    name: str
    q: str
    range: PresetRange | FixedRange
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None


class SavedSearchList(Answer):
    items: list[SavedSearch]
