"""RFC 8785 JSON Canonicalization Scheme (JCS) for manifests and audit entries.

Hashes and time stamps cover the exact bytes of a manifest, so the bytes must be reproducible by
any independent verifier: keys sorted by UTF-16 code units, no insignificant whitespace, strings
escaped like ECMAScript ``JSON.stringify``, UTF-8 output.

ARCILOGGER manifests contain no floating-point numbers (times are strings, offsets are integer
microseconds), so floats are rejected instead of implementing ECMAScript number formatting.
Integers must stay within +/-(2^53 - 1) so every JSON implementation reads them exactly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalJsonError(TypeError):
    """The value cannot be represented in an ARCILOGGER canonical document."""


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def _serialize(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError(f"integer out of the safe range: {value}")
        out.append(str(value))
    elif isinstance(value, float):
        raise CanonicalJsonError("floating-point numbers are not allowed in canonical documents")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalJsonError("object keys must be strings")
        out.append("{")
        for index, key in enumerate(sorted(value, key=_utf16_key)):
            if index:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _serialize(value[key], out)
        out.append("}")
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _serialize(item, out)
        out.append("]")
    else:
        raise CanonicalJsonError(f"unsupported type: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """Serialize ``value`` to canonical (RFC 8785) UTF-8 bytes."""
    out: list[str] = []
    _serialize(value, out)
    try:
        return "".join(out).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalJsonError("strings must be valid Unicode (no lone surrogates)") from exc


def is_canonical(data: bytes) -> bool:
    """True if ``data`` is exactly the canonical form of the JSON it contains."""
    try:
        return canonical_json(json.loads(data.decode("utf-8"))) == data
    except (UnicodeDecodeError, json.JSONDecodeError, CanonicalJsonError):
        return False
