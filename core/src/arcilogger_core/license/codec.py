"""Text encodings of the licensing formats.

* Crockford base32 (request codes, server ids, licence ids): alphabet
  ``0123456789ABCDEFGHJKMNPQRSTVWXYZ``, big-endian bit order, the last character padded with zero
  bits, no ``=`` padding. Decoding is strict: it accepts exactly the texts the encoder writes.
* base64url without padding (RFC 4648 section 5) for signatures and public keys, decoded
  strictly: the text must re-encode to itself.
"""

from __future__ import annotations

import base64
import re
from typing import Final

CROCKFORD_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_VALUES: Final = {char: index for index, char in enumerate(CROCKFORD_ALPHABET)}
_B64URL: Final = re.compile(r"[A-Za-z0-9_-]*")


def crockford_encode(data: bytes) -> str:
    """Crockford base32 of ``data`` (big-endian bits, zero-padded last character)."""
    if not data:
        return ""
    bits = len(data) * 8
    chars = -(-bits // 5)
    number = int.from_bytes(data, "big") << (chars * 5 - bits)
    return "".join(
        CROCKFORD_ALPHABET[(number >> shift) & 0x1F] for shift in range((chars - 1) * 5, -1, -5)
    )


def crockford_decode(text: str) -> bytes:
    """The bytes of a Crockford base32 text written by :func:`crockford_encode`.

    Raises ``ValueError`` for a character outside the (upper-case) alphabet, for non-zero
    padding bits and for a length no encoding has (a last character of padding bits only).
    """
    if not text:
        return b""
    number = 0
    for char in text:
        value = _CROCKFORD_VALUES.get(char)
        if value is None:
            raise ValueError(f"character {char!r} is not in the Crockford base32 alphabet")
        number = (number << 5) | value
    bits = len(text) * 5
    size = bits // 8
    padding = bits - size * 8
    if padding >= 5:
        raise ValueError("Crockford base32 text has a superfluous last character")
    if number & ((1 << padding) - 1):
        raise ValueError("non-zero padding bits in Crockford base32 text")
    return (number >> padding).to_bytes(size, "big")


def b64url_encode(data: bytes) -> str:
    """base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode_strict(text: str, length: int) -> bytes:
    """Decode base64url without padding that must encode exactly ``length`` bytes.

    ``+``, ``/``, ``=``, whitespace, a wrong length or non-zero trailing bits raise
    ``ValueError``.
    """
    expected = -(-length * 4 // 3)
    if not isinstance(text, str) or not _B64URL.fullmatch(text):
        raise ValueError("not base64url without padding")
    if len(text) != expected:
        raise ValueError(f"base64url text of {length} bytes must have {expected} characters")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if len(data) != length or b64url_encode(data) != text:
        raise ValueError("non-canonical base64url text")
    return data


def group(text: str, size: int, sep: str = "-") -> str:
    """``text`` split into groups of ``size`` characters joined with ``sep``."""
    if size < 1:
        raise ValueError("group size must be positive")
    return sep.join(text[index : index + size] for index in range(0, len(text), size))
