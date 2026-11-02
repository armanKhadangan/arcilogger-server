"""Arama: the query language and its field catalogue (ADR-0015, contract 2.2-2.4).

One language for search, live tail, saved searches, export and the evidence extract:

* :func:`parse_query` (``grammar``) — text → :class:`Query` (clauses with character spans and a
  normalised text), or :class:`SearchQueryError` with the span of what it refuses;
* :data:`FIELDS` (``fields``) — the field catalogue: names, aliases, value forms, the columns
  of every table, the index-backed tables, Turkish and English labels; :func:`branch_tables`
  and :func:`allowed_kinds` choose the tables a query reads; :class:`SiteNetwork` binds VLANs,
  departments and network sources;
* :func:`compile_matcher` (``matcher``) — the query as a Python predicate over one record
  (Canlı Akış, and the oracle the SQL of the planner is tested against);
* ``messages`` — every error code and notice, Turkish first.

The planner, the SQL, the endpoints and the live tail build on these modules.
"""

from arcilogger_api.search.fields import (
    FIELDS,
    Field,
    SiteNetwork,
    allowed_kinds,
    branch_tables,
    lookup_field,
)
from arcilogger_api.search.grammar import LIMITS, Clause, Query, parse_query
from arcilogger_api.search.matcher import Matcher, Record, compile_matcher
from arcilogger_api.search.messages import SearchQueryError

__all__ = [
    "FIELDS",
    "LIMITS",
    "Clause",
    "Field",
    "Matcher",
    "Query",
    "Record",
    "SearchQueryError",
    "SiteNetwork",
    "allowed_kinds",
    "branch_tables",
    "compile_matcher",
    "lookup_field",
    "parse_query",
]
