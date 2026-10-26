"""A narrow editor for ``arcilogger.toml`` (ADR-0014; contract 2.7.3).

The browser administration changes a few allow-listed keys of a file an administrator may also
edit by hand. Rendering the whole file anew would drop its bilingual comments, and a full TOML
library would be a new dependency on the evidence configuration path, so this editor changes
only what it must and proves the result:

* :class:`SetValue` replaces the value of an existing ``key = value`` line in place (its
  indentation and trailing comment stay), inserts a missing key at the end of its table (before
  the table's trailing blank and comment lines), appends a missing table at the end of the file,
  or removes the line for ``None`` (TOML has no null). Strings are written as basic strings with
  escapes, arrays of strings on one line;
* :class:`SetSources` writes every ``[[sources]]`` table as one **managed block** between two
  marker comments, at the place of the first existing ``[[sources]]`` table, or immediately before
  the first table header when there is none. The commented-out examples stay where they are.
  Comments inside the old source tables are lost (the version store keeps the previous file).

Inside an edited table the editor refuses what it cannot change safely (dotted keys, inline
tables, multi-line strings, a table given twice, a ``[[sources]]`` table outside the managed
block): :class:`TomlEditError` with ``config_unsupported_format``, and the change is made on the
console. Newline style (CRLF or LF) and encoding (UTF-8) are kept.

Postconditions, all checked before a result is returned (:func:`edit`):

* ``tomllib.loads(new) == expected(tomllib.loads(old), edits)`` exactly;
* the new file is a valid configuration (``config.parse_config``; else ``validation_failed``);
* no edits give the identical bytes;
* the installer's narrow TOML patterns (``Get-ArciTomlString`` for ``site_name``, ``data_dir``,
  ``[collector] bind``, ``[clock] ntp_server``; ``Get-ArciSourceTables`` for the ``address`` of the
  ``[[sources]]``) still read what ``tomllib`` reads, wherever they did before.

``secrets_dir`` and ``data_dir`` are never written (contract Day 0 note D0-2).
"""

from __future__ import annotations

import copy
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import JsonValue, ValidationError

from arcilogger_core.config import SourceConfig, parse_config

MANAGED_BEGIN: Final = (
    "# --- ARCILOGGER web arayüzü yönetir / managed by the ARCILOGGER web interface ---"
)
MANAGED_END: Final = "# --- yönetilen bölüm sonu / end of managed block ---"
#: Keys this editor never writes, whatever it is asked (contract Day 0 note D0-2).
NEVER_WRITTEN: Final = frozenset({"data_dir", "secrets_dir"})
#: The defaults of a source that are written only when they differ.
_SOURCE_DEFAULTS: Final[Mapping[str, Any]] = {
    "heartbeat_minutes": 15,
    "expected": True,
    "transport": "any",
    "enabled": True,
}
_BARE_KEY: Final = re.compile(r"[A-Za-z0-9_-]+")


class TomlEditError(ValueError):
    """The file cannot be edited safely, or the result would not be valid. ``code`` is
    ``config_unsupported_format`` or ``validation_failed``; ``reason`` explains it for the log
    (never a value of the file)."""

    def __init__(self, reason: str, *, code: str = "config_unsupported_format") -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class SetValue:
    """``path`` (``site_name`` or ``<table>.<key>``) to ``value``; None removes the key."""

    path: str
    value: JsonValue

    @property
    def table(self) -> str:
        """The table of the key ('' for the top level)."""
        return self.path.rpartition(".")[0]

    @property
    def key(self) -> str:
        return self.path.rpartition(".")[2]


@dataclass(frozen=True)
class SetSources:
    """Every ``[[sources]]`` table, in this order (an empty tuple leaves an empty block)."""

    sources: tuple[SourceConfig, ...]


Edit = SetValue | SetSources


# -- rendering -------------------------------------------------------------------------------------


def toml_string(value: str) -> str:
    """``value`` as a TOML basic string: quote, backslash and control characters escaped (as
    ``ConvertTo-ArciTomlString`` of the installer does), everything else as it is."""
    out = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def toml_value(value: JsonValue) -> str:
    """A JSON value of an allow-listed key as TOML (no floats, no tables, no nulls)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, list):
        items = [item for item in value if isinstance(item, str)]
        if len(items) == len(value):
            return "[" + ", ".join(toml_string(item) for item in items) + "]"
    raise TomlEditError(f"a {type(value).__name__} cannot be written", code="validation_failed")


def source_table(source: SourceConfig) -> dict[str, Any]:
    """The keys of one ``[[sources]]`` table in model order; defaults are left out."""
    table: dict[str, Any] = {}
    for name, value in source.model_dump(mode="json").items():
        if name in _SOURCE_DEFAULTS and value == _SOURCE_DEFAULTS[name]:
            continue
        table[name] = value
    return table


def _render_sources(sources: Sequence[SourceConfig], newline: str) -> str:
    lines = [MANAGED_BEGIN]
    for index, source in enumerate(sources):
        if index:
            lines.append("")
        lines.append("[[sources]]")
        lines += [f"{key} = {toml_value(value)}" for key, value in source_table(source).items()]
    lines.append(MANAGED_END)
    return newline.join(lines) + newline


# -- the expected document -----------------------------------------------------------------------


def expected(document: Mapping[str, Any], edits: Sequence[Edit]) -> dict[str, Any]:
    """``document`` (``tomllib`` output) as the edits make it: the postcondition's model."""
    result = copy.deepcopy(dict(document))
    for item in edits:
        if isinstance(item, SetSources):
            tables = [source_table(source) for source in item.sources]
            if tables:
                result["sources"] = tables
            else:
                result.pop("sources", None)
            continue
        target: dict[str, Any] | None = result
        if item.table:
            if item.value is None:
                target = result.get(item.table)
            else:
                target = result.setdefault(item.table, {})
        if target is None:
            continue
        if item.value is None:
            target.pop(item.key, None)
        else:
            target[item.key] = copy.deepcopy(item.value)
    return result


# -- scanning --------------------------------------------------------------------------------------

StatementKind = Literal["blank", "comment", "table", "array", "key"]
ValueKind = Literal["string", "multiline_string", "array", "inline_table", "scalar"]


@dataclass
class _Statement:
    kind: StatementKind
    #: Offset of the first character of the line and offset after its newline (or the end).
    start: int
    end: int
    #: Table name or key, its parts joined with ".", and whether it has more than one part.
    name: str = ""
    dotted: bool = False
    value_start: int = 0
    value_end: int = 0
    value_kind: ValueKind = "scalar"
    text: str = field(default="", repr=False)


class _Scanner:
    """Finds the statements of a valid TOML document (``tomllib`` read it first), with enough
    of the syntax to know where every value ends: strings of all four kinds, arrays across
    lines with comments in them, inline tables."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.size = len(text)

    def fail(self, reason: str, at: int) -> TomlEditError:
        line = self.text.count("\n", 0, at) + 1
        return TomlEditError(f"{reason} (line {line})")

    def spaces(self, at: int) -> int:
        while at < self.size and self.text[at] in " \t":
            at += 1
        return at

    def newline_after(self, at: int) -> int:
        """The offset after the end of the line at ``at`` (optional comment, newline or end)."""
        at = self.spaces(at)
        if at < self.size and self.text[at] == "#":
            found = self.text.find("\n", at)
            return self.size if found < 0 else found + 1
        if self.text.startswith("\r\n", at):
            return at + 2
        if at < self.size and self.text[at] == "\n":
            return at + 1
        if at >= self.size:
            return self.size
        raise self.fail("unexpected text after a value", at)

    def string(self, at: int) -> tuple[int, bool]:
        """The end of the string starting at ``at`` and whether it is a multi-line one."""
        text = self.text
        quote = text[at]
        if text.startswith(quote * 3, at):
            index = at + 3
            while index < self.size:
                if quote == '"' and text[index] == "\\":
                    index += 2
                    continue
                if text.startswith(quote * 3, index):
                    index += 3
                    extra = 0
                    while extra < 2 and index < self.size and text[index] == quote:
                        index += 1
                        extra += 1
                    return index, True
                index += 1
            raise self.fail("unterminated multi-line string", at)
        index = at + 1
        while index < self.size and text[index] not in "\r\n":
            if quote == '"' and text[index] == "\\":
                index += 2
                continue
            if text[index] == quote:
                return index + 1, False
            index += 1
        raise self.fail("unterminated string", at)

    def bracketed(self, at: int) -> int:
        """The end of the array or inline table starting at ``at``."""
        depth = 0
        index = at
        while index < self.size:
            char = self.text[index]
            if char in "[{":
                depth += 1
                index += 1
            elif char in "]}":
                depth -= 1
                index += 1
                if depth == 0:
                    return index
            elif char == "#":
                found = self.text.find("\n", index)
                index = self.size if found < 0 else found
            elif char in "\"'":
                index = self.string(index)[0]
            else:
                index += 1
        raise self.fail("unterminated array or inline table", at)

    def value(self, at: int) -> tuple[int, ValueKind]:
        char = self.text[at] if at < self.size else ""
        if char in "\"'":
            end, multiline = self.string(at)
            return end, "multiline_string" if multiline else "string"
        if char == "[":
            return self.bracketed(at), "array"
        if char == "{":
            return self.bracketed(at), "inline_table"
        index = at
        while index < self.size and self.text[index] not in "#\r\n":
            index += 1
        end = index
        while end > at and self.text[end - 1] in " \t":
            end -= 1
        if end == at:
            raise self.fail("a key without a value", at)
        return end, "scalar"

    def key(self, at: int, closing: str) -> tuple[list[str], int]:
        """The parts of a key (or table name) at ``at``, up to ``closing`` (``=`` or ``]``)."""
        parts: list[str] = []
        index = at
        while True:
            index = self.spaces(index)
            char = self.text[index] if index < self.size else ""
            if char in "\"'":
                end, multiline = self.string(index)
                if multiline:
                    raise self.fail("a multi-line string as a key", index)
                raw = self.text[index:end]
                parts.append(tomllib.loads(f"k = {raw}")["k"])
                index = end
            else:
                found = _BARE_KEY.match(self.text, index)
                if found is None:
                    raise self.fail("not a key", index)
                parts.append(found.group(0))
                index = found.end()
            index = self.spaces(index)
            char = self.text[index] if index < self.size else ""
            if char == ".":
                index += 1
                continue
            if not self.text.startswith(closing, index):
                raise self.fail(f"expected {closing!r}", index)
            return parts, index + len(closing)

    def statements(self) -> list[_Statement]:
        found: list[_Statement] = []
        index = 0
        while index < self.size:
            start = index
            at = self.spaces(index)
            char = self.text[at] if at < self.size else ""
            if char == "" or char == "\n" or self.text.startswith("\r\n", at):
                end = self.newline_after(at)
                found.append(_Statement("blank", start, end))
            elif char == "#":
                end = self.newline_after(at)
                found.append(
                    _Statement("comment", start, end, text=self.text[at:end].rstrip("\r\n"))
                )
            elif char == "[":
                array = self.text.startswith("[[", at)
                parts, after = self.key(at + (2 if array else 1), "]]" if array else "]")
                end = self.newline_after(after)
                found.append(
                    _Statement(
                        "array" if array else "table",
                        start,
                        end,
                        name=".".join(parts),
                        dotted=len(parts) > 1,
                    )
                )
            else:
                parts, after = self.key(at, "=")
                value_start = self.spaces(after)
                value_end, kind = self.value(value_start)
                end = self.newline_after(value_end)
                found.append(
                    _Statement(
                        "key",
                        start,
                        end,
                        name=".".join(parts),
                        dotted=len(parts) > 1,
                        value_start=value_start,
                        value_end=value_end,
                        value_kind=kind,
                    )
                )
            index = end
        return found


# -- regions ---------------------------------------------------------------------------------------


@dataclass
class _Region:
    """The statements of one table: from its header (None for the top level) to the next."""

    header: _Statement | None
    body: list[_Statement]


def _regions(statements: Sequence[_Statement]) -> list[_Region]:
    regions = [_Region(None, [])]
    for statement in statements:
        if statement.kind in ("table", "array"):
            regions.append(_Region(statement, []))
        else:
            regions[-1].body.append(statement)
    return regions


def _check_editable(region: _Region, label: str) -> None:
    for statement in region.body:
        if statement.kind != "key":
            continue
        if statement.dotted:
            raise TomlEditError(f"{label}: dotted key {statement.name!r}")
        if statement.value_kind == "inline_table":
            raise TomlEditError(f"{label}: inline table {statement.name!r}")
        if statement.value_kind == "multiline_string":
            raise TomlEditError(f"{label}: multi-line string {statement.name!r}")


def _table_region(regions: Sequence[_Region], table: str) -> _Region | None:
    """The one ``[table]`` region, refusing the forms the editor does not handle."""
    top = regions[0]
    for statement in top.body:
        if statement.kind == "key" and (
            statement.name == table or statement.name.startswith(f"{table}.")
        ):
            raise TomlEditError(f"[{table}] is given as a key or dotted keys of the top level")
    matches = []
    for region in regions[1:]:
        header = region.header
        if header is None:
            continue
        if header.name == table and header.kind == "table":
            matches.append(region)
        elif header.name == table or header.name.startswith(f"{table}."):
            raise TomlEditError(f"[{table}] has a sub-table or is an array of tables")
    if len(matches) > 1:
        raise TomlEditError(f"[{table}] appears twice")
    if matches:
        _check_editable(matches[0], f"[{table}]")
        return matches[0]
    return None


# -- editing ---------------------------------------------------------------------------------------


@dataclass(order=True)
class _Change:
    start: int
    end: int
    text: str = field(compare=False)


def _insertion_point(region: _Region, text: str) -> int:
    """After the region's last key; else after its header, or at the top of the file below its
    first comment lines."""
    keys = [s for s in region.body if s.kind == "key"]
    if keys:
        return keys[-1].end
    if region.header is not None:
        return region.header.end
    index = 0
    for statement in region.body:
        if statement.kind != "comment":
            break
        index = statement.end
    return index


def _line_break(text: str, at: int, newline: str) -> str:
    """A newline when ``at`` does not start a line (an insertion after a last line without one)."""
    return newline if at > 0 and text[at - 1] != "\n" else ""


def _key_changes(
    text: str, regions: Sequence[_Region], table: str, items: Sequence[SetValue], newline: str
) -> tuple[list[_Change], list[_Change]]:
    """The changes inside the table's region, and the table appended at the end of the file
    when it is missing (the caller applies all appended tables after everything else)."""
    if not items:
        return [], []
    region = regions[0] if not table else _table_region(regions, table)
    if region is not None and not table:
        _check_editable(region, "top level")
    changes: list[_Change] = []
    missing: list[SetValue] = []
    for item in items:
        if region is None:
            if item.value is not None:
                missing.append(item)
            continue
        lines = [s for s in region.body if s.kind == "key" and s.name == item.key]
        if len(lines) > 1:
            raise TomlEditError(f"{item.path} appears twice")
        if not lines:
            if item.value is not None:
                missing.append(item)
            continue
        line = lines[0]
        if item.value is None:
            changes.append(_Change(line.start, line.end, ""))
        else:
            changes.append(_Change(line.value_start, line.value_end, toml_value(item.value)))
    if not missing:
        return changes, []
    added = "".join(f"{item.key} = {toml_value(item.value)}{newline}" for item in missing)
    if region is not None:
        at = _insertion_point(region, text)
        changes.append(_Change(at, at, _line_break(text, at, newline) + added))
        return changes, []
    at = len(text)
    lead = _line_break(text, at, newline) + (newline if text.strip() else "")
    return changes, [_Change(at, at, f"{lead}[{table}]{newline}{added}")]


def _sources_change(
    text: str,
    statements: Sequence[_Statement],
    regions: Sequence[_Region],
    item: SetSources,
    newline: str,
) -> _Change:
    for statement in regions[0].body:
        if statement.kind == "key" and statement.name.split(".")[0] == "sources":
            raise TomlEditError("sources is given as a key of the top level")
    for region in regions[1:]:
        header = region.header
        if header is None:
            continue
        if header.name == "sources" and header.kind != "array":
            raise TomlEditError("[sources] is a table, not an array of tables")
        if header.name.startswith("sources."):
            raise TomlEditError("[[sources]] has a sub-table")
        if header.name == "sources":
            _check_editable(region, "[[sources]]")
    block = _render_sources(item.sources, newline)
    begins = [s for s in statements if s.kind == "comment" and s.text.strip() == MANAGED_BEGIN]
    ends = [s for s in statements if s.kind == "comment" and s.text.strip() == MANAGED_END]
    headers = [s for s in statements if s.kind in ("table", "array")]
    sources = [s for s in headers if s.name == "sources"]
    if begins or ends:
        if len(begins) != 1 or len(ends) != 1 or ends[0].start < begins[0].start:
            raise TomlEditError("the managed block of the sources is damaged")
        start, end = begins[0].start, ends[0].end
        for header in headers:
            inside = start <= header.start < end
            if inside != (header.name == "sources"):
                raise TomlEditError(
                    "a table other than [[sources]] in the managed block, or a [[sources]] "
                    "table outside it"
                )
        return _Change(start, end, _line_break(text, start, newline) + block)
    if sources:
        first, last = sources[0], sources[-1]
        between = [h for h in headers if first.start <= h.start <= last.start]
        if any(h.name != "sources" for h in between):
            raise TomlEditError("another table between two [[sources]] tables")
        last_region = next(r for r in regions if r.header is last)
        keys = [s for s in last_region.body if s.kind == "key"]
        end = keys[-1].end if keys else last.end
        return _Change(first.start, end, block)
    if headers:
        at = headers[0].start
        return _Change(at, at, block + newline)
    at = len(text)
    lead = _line_break(text, at, newline) + (newline if text.strip() else "")
    return _Change(at, at, lead + block)


def _apply(text: str, changes: Sequence[_Change]) -> str:
    """Apply ``changes`` from the end of the text backwards. Insertions at one place end up in
    the order of ``changes`` (the later one is applied first, the earlier one before it)."""
    indexed = sorted(
        enumerate(changes), key=lambda item: (item[1].start, item[1].end, item[0]), reverse=True
    )
    ordered = [change for _, change in indexed]
    for earlier, later in zip(ordered[1:], ordered, strict=False):
        if earlier.end > later.start:
            raise TomlEditError("two edits overlap")
    for change in ordered:
        text = text[: change.start] + change.text + text[change.end :]
    return text


# -- the installer's patterns (ArciInstall.psm1) -------------------------------------------------

_INSTALLER_KEYS: Final = (
    ("", "site_name"),
    ("", "data_dir"),
    ("collector", "bind"),
    ("clock", "ntp_server"),
)
_SOURCES_TABLE: Final = re.compile(
    r"(?ms)^[ \t]*\[\[[ \t]*sources[ \t]*\]\][ \t]*(?:#[^\r\n]*)?\r?$(?P<body>.*?)(?=^[ \t]*\[|\Z)"
)
_SOURCE_ADDRESS: Final = re.compile(
    r"(?m)^[ \t]*address[ \t]*=[ \t]*(?:\"(?P<basic>[^\"\r\n]*)\"|'(?P<literal>[^'\r\n]*)')"
    r"[ \t]*(?:#[^\r\n]*)?\r?$"
)


class _PatternError(Exception):
    pass


def _installer_unescape(raw: str) -> str:
    """``ConvertFrom-ArciTomlEscape``."""
    simple = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
    out = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        code = raw[index + 1 : index + 2]
        if code in simple:
            out.append(simple[code])
            index += 2
        elif code in ("u", "U"):
            width = 4 if code == "u" else 8
            out.append(chr(int(raw[index + 2 : index + 2 + width], 16)))
            index += 2 + width
        else:
            raise _PatternError(f"invalid escape \\{code}")
    return "".join(out)


def _installer_string(text: str, section: str, key: str) -> str | None:
    """``Get-ArciTomlString -Section section -Key key``: the value, None when absent;
    :class:`_PatternError` where the installer throws."""
    first = re.search(r"(?m)^[ \t]*\[", text)
    top_length = first.start() if first else len(text)
    if not section:
        body = text[:top_length]
    else:
        name = re.escape(section)
        top = text[:top_length]
        if re.search(rf"(?m)^[ \t]*[\"']?{name}[\"']?[ \t]*[.=]", top) or re.search(
            rf"(?m)^[ \t]*\[\[[ \t]*{name}[ \t]*\]\]", text
        ):
            raise _PatternError(f"[{section}] dotted, inline or an array of tables")
        tables = list(
            re.finditer(
                rf"(?ms)^[ \t]*\[[ \t]*{name}[ \t]*\][ \t]*(?:#[^\r\n]*)?\r?$(?P<body>.*?)"
                r"(?=^[ \t]*\[|\Z)",
                text,
            )
        )
        if len(tables) > 1:
            raise _PatternError(f"[{section}] twice")
        if not tables:
            return None
        body = tables[0].group("body")
    name = re.escape(key)
    lines = re.findall(rf"(?m)^[ \t]*[\"']?{name}[\"']?[ \t]*=", body)
    if not lines:
        return None
    if len(lines) > 1:
        raise _PatternError(f"{key} twice")
    simple = re.search(
        rf"(?m)^[ \t]*{name}[ \t]*=[ \t]*(?P<value>\"(?P<basic>(?:[^\"\\\r\n]|\\.)*)\""
        r"|'(?P<literal>[^'\r\n]*)')[ \t]*(?:#[^\r\n]*)?(?=\r?$)",
        body,
    )
    if simple is None:
        raise _PatternError(f"{key} is not a one-line string")
    if simple.group("basic") is not None:
        return _installer_unescape(simple.group("basic"))
    return simple.group("literal")


def _installer_sources(text: str) -> tuple[int, list[str]]:
    """``Get-ArciSourceTables``: the number of tables and the plain addresses, in file order."""
    tables = list(_SOURCES_TABLE.finditer(text))
    addresses = []
    for table in tables:
        found = _SOURCE_ADDRESS.search(table.group("body"))
        if found:
            value = found.group("basic")
            if value is None:
                value = found.group("literal")
            if value:
                addresses.append(value)
    return len(tables), addresses


def _installer_reads(text: str, document: Mapping[str, Any]) -> dict[str, bool]:
    """For each value the installer's patterns read: whether they read what ``tomllib`` reads."""
    result: dict[str, bool] = {}
    for section, key in _INSTALLER_KEYS:
        table = document.get(section, {}) if section else document
        wanted = table.get(key) if isinstance(table, Mapping) else None
        try:
            result[f"{section}.{key}"] = _installer_string(text, section, key) == (
                wanted if isinstance(wanted, str) else None
            )
        except (_PatternError, ValueError):
            result[f"{section}.{key}"] = False
    sources = document.get("sources", [])
    wanted_addresses = [s.get("address") for s in sources if isinstance(s, Mapping)]
    count, addresses = _installer_sources(text)
    result["sources"] = count == len(sources) and addresses == wanted_addresses
    return result


# -- the editor ------------------------------------------------------------------------------------


def edit(old: bytes, edits: Sequence[Edit], *, path: Path) -> bytes:
    """``old`` with ``edits`` applied, after every postcondition holds (:class:`TomlEditError`).
    ``path`` is where the file lives (``secrets_dir`` defaults to its folder)."""
    if not edits:
        return old
    try:
        text = old.decode("utf-8")
        before = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise TomlEditError(f"the current file is not valid TOML: {exc}") from exc
    for item in edits:
        if isinstance(item, SetValue) and (
            item.path.count(".") > 1 or not item.key or item.path in NEVER_WRITTEN
        ):
            raise TomlEditError(f"{item.path} is not a key this editor writes")
    newline = "\r\n" if "\r\n" in text else "\n"
    statements = _Scanner(text).statements()
    regions = _regions(statements)
    # The order matters where insertions meet (the end of the top level, the end of the file):
    # top-level keys first, then keys of the existing tables, then the sources block, then the
    # tables appended at the end.
    tables: dict[str, list[SetValue]] = {"": []}
    sources: list[SetSources] = []
    for item in edits:
        if isinstance(item, SetSources):
            sources.append(item)
        else:
            tables.setdefault(item.table, []).append(item)
    changes: list[_Change] = []
    appended: list[_Change] = []
    for table, items in tables.items():
        inside, new_tables = _key_changes(text, regions, table, items, newline)
        changes += inside
        appended += new_tables
    changes += [_sources_change(text, statements, regions, item, newline) for item in sources]
    new_text = _apply(text, changes + appended)
    want = expected(before, edits)
    try:
        after = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(f"the edited file is not valid TOML: {exc}") from exc
    if after != want:
        raise TomlEditError("the edited file does not read as the expected document")
    try:
        new = new_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TomlEditError("a value cannot be written as UTF-8", code="validation_failed") from exc
    reads_before = _installer_reads(text, before)
    reads_after = _installer_reads(new_text, after)
    broken = sorted(key for key, ok in reads_before.items() if ok and not reads_after[key])
    if broken:
        raise TomlEditError(f"the installer could no longer read {', '.join(broken)}")
    try:
        parse_config(new, path)
    except (ValidationError, ValueError) as exc:
        raise TomlEditError(
            f"the edited file is not a valid configuration: {_one_line(exc)}",
            code="validation_failed",
        ) from exc
    return new


def _one_line(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(file)'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
    return f"{type(exc).__name__}: {exc}".splitlines()[0]
