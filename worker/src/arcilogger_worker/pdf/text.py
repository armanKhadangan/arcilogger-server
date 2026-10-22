"""Minimal text extraction for the PDFs ARCILOGGER renders with reportlab.

No PDF library is a dependency of ARCILOGGER, but the tests must prove that a report's Turkish
text survives in the file (searchable, copyable) — not only that the glyphs were drawn. This
module reads exactly what reportlab writes: objects ``N 0 obj … endobj``, streams with
``/FlateDecode`` and/or ``/ASCII85Decode``, TrueType subset fonts with a ``/ToUnicode`` CMap
(``bfchar`` / ``bfrange``), and text shown with ``(…) Tj`` / ``[…] TJ`` after ``/Fx size Tf``.
Each code is mapped through the font's ``ToUnicode`` map — the same map a PDF viewer uses for
search and copy — so a missing or wrong map fails the tests.

It is not a general PDF parser (no object streams, no cross-reference parsing, no encryption).

**Test support only**: no service imports it. It lives in the worker package (like
:mod:`arcilogger_worker.testing`) so that the tests of every package that renders a PDF — the
5651 report and the evidence package of the API, the usage reports of the worker, end-to-end —
can read the text (``arcilogger_api.pdftext`` re-exports it under its old name).
"""

from __future__ import annotations

import base64
import re
import zlib
from typing import Final

_OBJECT: Final = re.compile(rb"(\d+)\s+0\s+obj\b(.*?)\bendobj", re.S)
_FONT_REF: Final = re.compile(rb"/([A-Za-z0-9+_.-]+)\s+(\d+)\s+0\s+R")
_TO_UNICODE: Final = re.compile(rb"/ToUnicode\s+(\d+)\s+0\s+R")
_CONTENTS: Final = re.compile(rb"/Contents\s+(?:(\d+)\s+0\s+R|\[([^\]]*)\])")
_REF: Final = re.compile(rb"(\d+)\s+0\s+R")
_BFCHAR: Final = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE: Final = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_HEX: Final = re.compile(rb"<([0-9A-Fa-f]+)>")
_TEXT_OPS: Final = re.compile(
    rb"/([A-Za-z0-9+_.-]+)\s+[-\d.]+\s+Tf"
    rb"|\(((?:\\.|[^\\()])*)\)\s*Tj"
    rb"|\[((?:\\.|[^\\\]])*)\]\s*TJ"
    rb"|(T\*|ET\b|Td\b|TD\b|Tm\b)",
    re.S,
)
_STRING: Final = re.compile(rb"\(((?:\\.|[^\\()])*)\)", re.S)
_ESCAPES: Final = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _objects(data: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): m.group(2) for m in _OBJECT.finditer(data)}


def _split_stream(body: bytes) -> tuple[bytes, bytes | None]:
    """``(dictionary, decoded stream or None)``."""
    index = body.find(b"stream")
    if index < 0:
        return body, None
    head = body[:index]
    raw = body[index + len(b"stream") :]
    raw = raw[2:] if raw.startswith(b"\r\n") else raw[1:]
    raw = raw[: raw.rfind(b"endstream")]
    filters = re.findall(rb"/(ASCII85Decode|FlateDecode)", head)
    for name in filters:
        if name == b"ASCII85Decode":
            encoded = raw.strip().removeprefix(b"<~").removesuffix(b"~>")
            raw = base64.a85decode(encoded)
        else:
            raw = zlib.decompress(raw)
    return head, raw


def _unescape(literal: bytes) -> bytes:
    out = bytearray()
    index = 0
    while index < len(literal):
        byte = literal[index : index + 1]
        if byte != b"\\":
            out += byte
            index += 1
            continue
        following = literal[index + 1 : index + 2]
        octal = re.match(rb"[0-7]{1,3}", literal[index + 1 : index + 4])
        if octal:
            out.append(int(octal.group(), 8) & 0xFF)
            index += 1 + len(octal.group())
        elif following in _ESCAPES:
            out += _ESCAPES[following]
            index += 2
        elif following in (b"\r", b"\n"):
            index += 2  # line continuation
        else:
            out += following
            index += 2
    return bytes(out)


def _cmap(stream: bytes) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for block in _BFCHAR.findall(stream):
        values = _HEX.findall(block)
        for source, target in zip(values[0::2], values[1::2], strict=False):
            mapping[int(source, 16)] = bytes.fromhex(target.decode()).decode("utf-16-be")
    for block in _BFRANGE.findall(stream):
        values = _HEX.findall(block)
        for low, high, target in zip(values[0::3], values[1::3], values[2::3], strict=False):
            first = int(target, 16)
            for offset, code in enumerate(range(int(low, 16), int(high, 16) + 1)):
                mapping[code] = chr(first + offset)
    return mapping


def pdf_text(data: bytes) -> str:
    """The text of every page, one line per text line, pages in file order."""
    objects = _objects(data)
    streams: dict[int, tuple[bytes, bytes | None]] = {
        number: _split_stream(body) for number, body in objects.items()
    }
    cmaps: dict[int, dict[int, str]] = {}
    for number, (head, _) in streams.items():
        match = _TO_UNICODE.search(head)
        if match is not None:
            target = streams.get(int(match.group(1)))
            if target is not None and target[1] is not None:
                cmaps[number] = _cmap(target[1])
    fonts: dict[str, dict[int, str]] = {}
    for head, stream in streams.values():
        if stream is None:
            for name, number in _FONT_REF.findall(head):
                if int(number) in cmaps:
                    fonts[name.decode()] = cmaps[int(number)]
    contents: list[int] = []
    for _, (head, stream) in sorted(streams.items()):
        if stream is not None or b"/Page" not in head:
            continue
        for single, array in _CONTENTS.findall(head):
            contents += [int(single)] if single else [int(n) for n in _REF.findall(array)]
    lines: list[str] = []
    for number in contents:
        stream = streams.get(number, (b"", None))[1]
        if stream is None:
            continue
        current: dict[int, str] = {}
        line = ""
        for match in _TEXT_OPS.finditer(stream):
            font, shown, array, operator = match.groups()
            if font is not None:
                current = fonts.get(font.decode(), {})
            elif shown is not None:
                line += "".join(current.get(b, chr(b)) for b in _unescape(shown))
            elif array is not None:
                for part in _STRING.findall(array):
                    line += "".join(current.get(b, chr(b)) for b in _unescape(part))
            elif operator is not None and line:
                lines.append(line)
                line = ""
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalized(text: str) -> str:
    """Lines joined with spaces, runs of white space collapsed (phrases that wrapped match)."""
    return " ".join(text.split())
