"""``key=value`` tokenizer for FortiGate logs (FortiOS ``format default``, spec 2.5 and 7.2).

Grammar (one log line, after the syslog PRI and any prefix):

* Tokens are separated by ASCII whitespace (FortiOS uses single spaces).
* ``key=value``: the key runs up to the first ``=`` and contains no whitespace, ``=`` or ``"``.
* An **unquoted** value ends at the next whitespace; it may contain ``=``, ``"``, ``,``, ``;``.
* A **quoted** value (``key="…"``) may contain anything, including whitespace, ``=``, ``,``,
  ``;``, ``\\"`` (an escaped quote) and ``\\\\`` (an escaped backslash). Every other backslash is
  kept literally, so Windows paths survive. Whether FortiOS escapes backslashes at all is a
  "verify on site" item (docs/PARSERS.md): with ``escapes=False`` (reference only) every
  backslash is an ordinary character — the FortiGate parser retries a line that way when the
  escape-aware reading leaves a quote open or stray tokens (``"C:\\dir\\" next=1``).
* A quoted value without a closing quote (a datagram cut short) takes the rest of the line and
  is reported in :attr:`Tokens.unterminated`.
* ``key=`` and ``key=""`` are both the empty string.
* A key seen more than once keeps its **first** value in :attr:`Tokens.fields`; every value
  (first included) is listed in :attr:`Tokens.duplicates`.
* Anything that is not ``key=value`` is kept in :attr:`Tokens.strays` — nothing is dropped.

:func:`tokenize_reference` is the specification as a plain character loop. :func:`tokenize`
gives the same result (a property test compares them on arbitrary text) but answers ordinary
lines with a few C-level string operations — about five times faster (12 µs instead of 62 µs
for a real line on the development VM) — and falls back to the reference whenever a line has
backslashes, control characters, unbalanced quotes or anything else unusual.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

#: Token separators (ASCII whitespace).
_SEPARATORS: Final = frozenset(" \t\n\r\f\v")
#: Characters that end a key.
_KEY_END: Final = frozenset(' \t\n\r\f\v="')
#: Stands for a quoted value in the fast path (NUL never occurs in decoded text).
_MARKER: Final = "\x00"
_MARKED_VALUE: Final = "=" + _MARKER
#: Lines containing any of these always take the reference path (``in`` checks are ~20 times
#: faster than one regular-expression search over a 1,300-character line).
_UNUSUAL: Final = ("\\", "\t", "\n", "\r", "\f", "\v", _MARKER)


@dataclass(frozen=True, slots=True)
class Tokens:
    """Result of tokenizing one line."""

    #: First value of every key. Key order is not part of the contract (JSONB does not keep
    #: it either); "first" refers to the order in the line.
    fields: dict[str, str]
    #: Keys that occurred more than once → all their values in line order.
    duplicates: dict[str, list[str]]
    #: Tokens that are not ``key=value`` (e.g. trailing garbage), in line order.
    strays: tuple[str, ...] = ()
    #: Key whose quoted value had no closing quote (took the rest of the line).
    unterminated: str | None = None


def _add(fields: dict[str, str], duplicates: dict[str, list[str]], key: str, value: str) -> None:
    if key not in fields:
        fields[key] = value
    elif key in duplicates:
        duplicates[key].append(value)
    else:
        duplicates[key] = [fields[key], value]


def tokenize_reference(text: str, *, escapes: bool = True) -> Tokens:
    """The tokenizer specification as a straightforward character loop.

    ``escapes=False`` reads every backslash literally (``\\"`` is a backslash, then the closing
    quote).
    """
    fields: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    strays: list[str] = []
    unterminated: str | None = None
    length = len(text)
    i = 0
    while i < length:
        if text[i] in _SEPARATORS:
            i += 1
            continue
        j = i
        while j < length and text[j] not in _KEY_END:
            j += 1
        if j in (i, length) or text[j] != "=":
            # Not key=value: a stray token up to the next separator.
            k = j
            while k < length and text[k] not in _SEPARATORS:
                k += 1
            strays.append(text[i:k])
            i = k
            continue
        key = text[i:j]
        j += 1  # past "="
        if j < length and text[j] == '"':
            buffer: list[str] = []
            k = j + 1
            closed = False
            while k < length:
                char = text[k]
                if escapes and char == "\\" and k + 1 < length and text[k + 1] in '"\\':
                    buffer.append(text[k + 1])
                    k += 2
                elif char == '"':
                    closed = True
                    break
                else:
                    buffer.append(char)
                    k += 1
            if not closed and unterminated is None:
                unterminated = key
            _add(fields, duplicates, key, "".join(buffer))
            i = k + 1
        else:
            k = j
            while k < length and text[k] not in _SEPARATORS:
                k += 1
            _add(fields, duplicates, key, text[j:k])
            i = k
    return Tokens(fields, duplicates, tuple(strays), unterminated)


def _tokenize_fast(text: str) -> Tokens | None:
    """Ordinary lines in a few C-level string operations; ``None`` = let the reference decide.

    Without backslashes a quoted value cannot contain ``"``, so splitting at every ``"`` gives
    unquoted text and quoted values alternately. The unquoted parts are joined with a marker
    (NUL, which never occurs in decoded text) in place of each quoted value, split at spaces
    and the markers replaced by the quoted values in order. The checks make sure every marker
    is a complete value right after ``key=`` and followed by a space or the end. Anything
    unusual — an odd number of quotes, text glued to a closing quote, ``a=b="c"``, a stray
    token, an empty key, a duplicate key — returns ``None``.
    """
    for char in _UNUSUAL:
        if char in text:
            return None
    parts = text.split('"')
    if not len(parts) & 1:
        return None  # odd number of quotes: an unterminated value
    quoted = parts[1::2]
    joined = _MARKER.join(parts[0::2])
    if joined.count(_MARKED_VALUE + " ") + joined.endswith(_MARKED_VALUE) != len(quoted):
        return None
    pairs = [token.partition("=") for token in joined.split(" ") if token]
    values = iter(quoted)
    fields = {
        key: (next(values) if value == _MARKER else value)
        for key, separator, value in pairs
        if separator and key
    }
    if len(fields) != len(pairs) or next(values, None) is not None:
        return None  # stray token, empty or duplicate key, or a marker inside a value
    return Tokens(fields, {})


def tokenize(text: str) -> Tokens:
    """Tokenize one line (same result as :func:`tokenize_reference`, faster)."""
    fast = _tokenize_fast(text)
    return tokenize_reference(text) if fast is None else fast


def quote(value: str) -> str:
    """``value`` as a quoted FortiGate value (``\\`` and ``"`` escaped)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """Inverse of :func:`tokenize` for valid keys: every value quoted (fixtures, tests)."""
    return " ".join(f"{key}={quote(value)}" for key, value in pairs)
