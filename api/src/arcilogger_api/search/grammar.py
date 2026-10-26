"""The query language of Arama and Canlı Akış: text → syntax tree (ADR-0015, contract 2.2).

One language for search, live tail, saved searches, export and the evidence extract::

    query    = ws* [ clause { ws+ clause } ] ws* ;
    clause   = [ "-" | "NOT" ws+ ] ( fielded | freetext ) ;
    fielded  = field ":" values ;
    field    = letter { letter | digit | "_" } ;   (* any case, Turkish letters folded; aliases *)
    values   = value { "," value } ;                (* OR inside one field *)
    value    = quoted | bare ;
    freetext = quoted | word ;                      (* a word or a "quoted phrase" *)
    quoted   = '"' { ? any char except '"' and '\\' ? | '\\' ( '"' | '\\' ) } '"' ;
    bare     = ? 1+ chars except whitespace, '"', ',', '(', ')' ? ;   (* may contain ':' *)
    word     = ? 1+ chars except whitespace, '"', '(', ')' and containing no ':' ? ;

Clauses are ANDed; ``-`` or ``NOT`` negates one; a bare ``AND`` is ignored. Parentheses and a
bare ``OR`` are refused (``search_or_unsupported``: OR is a comma inside one field). A word with
``:`` whose prefix is no field is refused (``search_unknown_field``), never taken as free text,
with a hint when it looks like a MAC address (``mac:…``) or an address or URL (quote it).
``field:*`` means the field has a value. Wildcards ``*`` are read in bare values only: a quoted
value is literal. Time is not part of the query (``from``/``to`` travel beside it).

:func:`parse_query` is hand-written, linear in the length of the text and bounded by
:data:`LIMITS` (1,000 characters, 16 clauses, 20 values per clause, 5 free-text terms, 256
characters per value). For any input it returns a :class:`Query` or raises
:class:`SearchQueryError` with the character span of the offending part — nothing else.
``Query.normalized`` (canonical field names and values, ``-`` for negation, single spaces) parses
again to the same tree; spans are not part of the tree.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import unicodedata
from dataclasses import dataclass
from typing import Final

from arcilogger_api.search.fields import (
    FIELDS,
    MAX_VALUE_CHARS,
    ContainsValue,
    Present,
    RawValue,
    Value,
    bare_or_quoted,
    lookup_field,
    mac_text,
    parse_value,
    quote,
)
from arcilogger_api.search.messages import SearchQueryError

MAX_CHARS: Final = 1000
MAX_CLAUSES: Final = 16
MAX_VALUES: Final = 20
MAX_TERMS: Final = 5
#: ``FieldCatalogue.limits`` (``max_days`` and ``page_size_max`` are settings).
LIMITS: Final = {
    "max_chars": MAX_CHARS,
    "max_clauses": MAX_CLAUSES,
    "max_values": MAX_VALUES,
    "max_terms": MAX_TERMS,
}
#: Characters that end a bare token (whitespace too); a value also ends at a comma.
_STOP: Final = frozenset('"()')
_VALUE_STOP: Final = _STOP | {","}
_KEYWORDS: Final = frozenset({"AND", "OR", "NOT"})


@dataclass(frozen=True, slots=True)
class Clause:
    """One condition: ``field`` (canonical name; ``text`` for free text) matches any of
    ``values``. ``free``: free text written without a field name."""

    field: str
    values: tuple[Value, ...]
    negated: bool = False
    free: bool = False
    #: 0-based half-open character span in the text it was parsed from (not part of the tree).
    start: int = dataclasses.field(default=0, compare=False)
    end: int = dataclasses.field(default=0, compare=False)

    @property
    def op(self) -> str:
        """``present`` (``field:*``), ``text`` (free text) or ``match``."""
        if self.values == (Present(),):
            return "present"
        return "text" if self.field == "text" else "match"

    def value_texts(self) -> list[str]:
        """The values as written in the normalised query."""
        return [value.render() for value in self.values]

    def render(self) -> str:
        sign = "-" if self.negated else ""
        if self.free:
            value = self.values[0]
            term = value.term if isinstance(value, ContainsValue) else value.render()
            return sign + _free_text(term)
        return f"{sign}{self.field}:{','.join(self.value_texts())}"


@dataclass(frozen=True, slots=True)
class Query:
    """A parsed query: its clauses (ANDed) and its normalised text."""

    clauses: tuple[Clause, ...]
    normalized: str = dataclasses.field(default="", compare=False)

    @property
    def empty(self) -> bool:
        """No condition: every record of the time range matches."""
        return not self.clauses

    @property
    def terms(self) -> tuple[str, ...]:
        """The free-text terms a matching record contains (positive clauses with one value)."""
        return tuple(
            value.term
            for clause in self.clauses
            if clause.field == "text" and not clause.negated and len(clause.values) == 1
            for value in clause.values
            if isinstance(value, ContainsValue)
        )


def _free_text(term: str) -> str:
    """A free-text term as written in a normalised query."""
    if ":" in term or term.startswith("-") or term in _KEYWORDS or "*" in term:
        return quote(term)
    return bare_or_quoted(term)


def _syntax(reason: str, start: int, end: int) -> SearchQueryError:
    return SearchQueryError("search_syntax", start, end, reason=reason, position=start + 1)


def _check_characters(text: str) -> None:
    for index, char in enumerate(text):
        category = unicodedata.category(char)
        if (category == "Cc" and not char.isspace()) or category == "Cs":
            raise _syntax("control_character", index, index + 1)


def _hint(word: str) -> dict[str, str]:
    """``hint``/``suggestion`` for a word that has a ``:`` but names no field."""
    mac = mac_text(word)
    if mac is not None:
        return {"hint": "mac", "suggestion": f"mac:{mac}"}
    looks_like_address = "://" in word or "::" in word
    if not looks_like_address:
        try:
            ipaddress.ip_address(word.strip("[]"))
            looks_like_address = True
        except ValueError:
            looks_like_address = False
    return {"hint": "quote", "suggestion": quote(word)} if looks_like_address else {}


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.pos = 0

    # -- scanning --------------------------------------------------------------------------------

    def _space(self, index: int) -> bool:
        return index < self.n and self.text[index].isspace()

    def _skip_space(self) -> None:
        while self._space(self.pos):
            self.pos += 1

    def _token_end(self, index: int, stop: frozenset[str] = _STOP) -> int:
        """End of the bare token at ``index`` (whitespace or a character of ``stop`` ends it)."""
        while index < self.n and not self.text[index].isspace() and self.text[index] not in stop:
            index += 1
        return index

    def _quoted(self, start: int) -> tuple[str, int]:
        """The unescaped text of the quoted string at ``start`` and the index after it."""
        chars: list[str] = []
        index = start + 1
        while index < self.n:
            char = self.text[index]
            if char == '"':
                return "".join(chars), index + 1
            if char == "\\":
                if index + 1 < self.n and self.text[index + 1] in '"\\':
                    chars.append(self.text[index + 1])
                    index += 2
                    continue
                raise _syntax("bad_escape", index, min(index + 2, self.n))
            chars.append(char)
            index += 1
        raise _syntax("unclosed_quote", start, self.n)

    def _after_token(self, end: int) -> None:
        """A token must be followed by whitespace or the end of the query."""
        if end >= self.n or self.text[end].isspace():
            return
        if self.text[end] in "()":
            raise SearchQueryError("search_or_unsupported", end, end + 1)
        raise _syntax("unexpected_quote", end, end + 1)

    # -- clauses ---------------------------------------------------------------------------------

    def parse(self) -> Query:
        clauses: list[Clause] = []
        terms = 0
        while True:
            self._skip_space()
            if self.pos >= self.n:
                break
            clause = self._clause()
            if clause is None:
                continue
            if len(clauses) == MAX_CLAUSES:
                raise SearchQueryError("search_too_complex", clause.start, clause.end)
            if clause.field == "text":
                terms += len(clause.values)
                if terms > MAX_TERMS:
                    raise SearchQueryError("search_too_complex", clause.start, clause.end)
            clauses.append(clause)
        return Query(tuple(clauses), " ".join(clause.render() for clause in clauses))

    def _clause(self) -> Clause | None:
        start = self.pos
        negated = False
        if self.text[self.pos] == "-":
            self.pos += 1
            if self.pos >= self.n or self._space(self.pos):
                raise _syntax("missing_clause", start, self.pos)
            negated = True
        else:
            end = self._token_end(self.pos)
            word = self.text[self.pos : end]
            if word == "AND" and (end >= self.n or self._space(end)):
                self.pos = end
                return None
            if word == "NOT" and (end >= self.n or self._space(end)):
                self.pos = end
                self._skip_space()
                if self.pos >= self.n:
                    raise _syntax("missing_clause", start, end)
                negated = True
        return self._condition(start, negated)

    def _condition(self, start: int, negated: bool) -> Clause:
        char = self.text[self.pos]
        if char in "()":
            raise SearchQueryError("search_or_unsupported", self.pos, self.pos + 1)
        if char == '"':
            term, end = self._quoted(self.pos)
            self._after_token(end)
            return self._free(term, True, start, self.pos, end, negated)
        name_end = self.pos
        while name_end < self.n and (self.text[name_end].isalnum() or self.text[name_end] == "_"):
            name_end += 1
        word_end = self._token_end(self.pos)
        if name_end < self.n and self.text[name_end] == ":" and self.text[self.pos].isalpha():
            entry = lookup_field(self.text[self.pos : name_end])
            if entry is not None:
                values = self._values(entry.name, name_end + 1)
                return Clause(entry.name, values, negated, False, start, self.pos)
        word = self.text[self.pos : word_end]
        if ":" in word:
            prefix = word.partition(":")[0]
            hint = _hint(word)
            if not prefix and not hint:
                raise _syntax("missing_field", self.pos, self.pos + 1)
            raise SearchQueryError(
                "search_unknown_field", self.pos, word_end, field=prefix or word, **hint
            )
        if word == "OR":
            raise SearchQueryError("search_or_unsupported", self.pos, word_end)
        self._after_token(word_end)
        return self._free(word, False, start, self.pos, word_end, negated)

    def _free(
        self, term: str, quoted: bool, start: int, value_start: int, end: int, negated: bool
    ) -> Clause:
        if len(term) > MAX_VALUE_CHARS:
            raise SearchQueryError("search_too_complex", value_start, end)
        value = parse_value(FIELDS["text"], RawValue(term, quoted, value_start, end))
        self.pos = end
        return Clause("text", (value,), negated, True, start, end)

    def _values(self, name: str, index: int) -> tuple[Value, ...]:
        entry = FIELDS[name]
        values: list[Value] = []
        while True:
            value_start = index
            if index >= self.n or self.text[index].isspace() or self.text[index] == ",":
                raise _syntax("empty_value", max(index - 1, 0), index)
            char = self.text[index]
            if char in "()":
                raise SearchQueryError("search_or_unsupported", index, index + 1)
            if char == '"':
                text, index = self._quoted(index)
                quoted = True
                if not text:
                    raise _syntax("empty_value", value_start, index)
            else:
                index = self._token_end(index, _VALUE_STOP)
                text, quoted = self.text[value_start:index], False
            if len(text) > MAX_VALUE_CHARS or len(values) == MAX_VALUES:
                raise SearchQueryError("search_too_complex", value_start, index)
            values.append(parse_value(entry, RawValue(text, quoted, value_start, index)))
            if index < self.n and self.text[index] == ",":
                index += 1
                continue
            self._after_token(index)
            break
        if len(values) > 1 and any(isinstance(value, Present) for value in values):
            raise _syntax("star_alone", self.pos, index)
        self.pos = index
        return tuple(values)


def parse_query(text: str, *, max_chars: int | None = MAX_CHARS) -> Query:
    """The syntax tree of ``text``; :class:`SearchQueryError` for anything it refuses.

    ``max_chars`` limits the input (``None``: no limit, for a text this module normalised: a
    canonical value can be longer than the one typed, e.g. an international domain in punycode).
    """
    if max_chars is not None and len(text) > max_chars:
        raise SearchQueryError("search_too_complex", 0, len(text))
    _check_characters(text)
    return _Parser(text).parse()
