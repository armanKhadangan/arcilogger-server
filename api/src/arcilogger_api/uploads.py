"""One file from a ``multipart/form-data`` request, read without an extra dependency.

FastAPI's ``UploadFile`` needs the ``python-multipart`` package; the API only ever receives one
small file (the CSV imports, at most :data:`arcilogger_api.csvfiles.MAX_BYTES`), so this module
reads the body itself — bounded **while it streams in** (a ``Content-Length`` above the limit is
refused before anything is read) — and takes the named part out of it (RFC 7578: parts separated
by ``CRLF--boundary``, headers up to an empty line). The file's bytes are returned exactly as
sent.

The refusal code of an oversized body is ``csv_too_large`` unless the caller names another one
(``too_large_code="license_too_large"`` for licence uploads, licensing contract 6.2, with the
licence error texts); the raised :class:`BodyTooLargeError` tells how many bytes were declared
or read, so the refusal can be recorded.
"""

from __future__ import annotations

import re
from typing import Final

from fastapi import Request

from arcilogger_api.csvfiles import csv_error
from arcilogger_api.errors import ApiError

#: Room for the multipart framing around the file (boundaries, part headers, other fields).
OVERHEAD: Final = 64 * 1024
_BOUNDARY: Final = re.compile(r'boundary=(?:"([^"]{1,200})"|([^;\s]{1,200}))', re.IGNORECASE)
_NAME: Final = re.compile(r'(?:^|;)\s*name="([^"]*)"', re.IGNORECASE)

MESSAGES: Final = {
    "upload_invalid": (
        'Dosya gönderilmedi: istek "file" alanında bir dosya içeren multipart/form-data olmalıdır.',
        "No file was sent: the request must be multipart/form-data with the file in the "
        'field "file".',
    ),
}


def _texts(code: str) -> tuple[str, str] | None:
    if code in MESSAGES:
        return MESSAGES[code]
    if code.startswith("license_"):
        try:  # the licence texts (imported here only: uploads never needs the licence code)
            from arcilogger_core.license.errors import MESSAGES as LICENSE_MESSAGES
        except ImportError:
            return None
        return LICENSE_MESSAGES.get(code)
    return None


def _error(status: int, code: str) -> ApiError:
    texts = _texts(code)
    if texts is not None:
        return ApiError(status, code, *texts)
    if code.startswith("license_"):
        return ApiError(status, code)
    return csv_error(status, code)


class BodyTooLargeError(ApiError):
    """413: the body exceeds the limit; ``size`` = the declared ``Content-Length`` or the bytes
    read so far."""

    def __init__(self, code: str, size: int) -> None:
        error = _error(413, code)
        super().__init__(413, code, error.message_tr, error.message_en)
        self.size = size


async def read_body(
    request: Request, limit: int, *, too_large_code: str = "csv_too_large"
) -> bytes:
    """The request body, refused with 413 ``too_large_code`` as soon as it exceeds ``limit``."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise BodyTooLargeError(too_large_code, int(declared))
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLargeError(too_large_code, size)
        chunks.append(chunk)
    return b"".join(chunks)


def multipart_file(content_type: str, body: bytes, field: str = "file") -> bytes:
    """The content of part ``field``; 422 ``upload_invalid`` when there is none."""
    if not content_type.lower().startswith("multipart/form-data"):
        raise _error(422, "upload_invalid")
    found = _BOUNDARY.search(content_type)
    if found is None:
        raise _error(422, "upload_invalid")
    boundary = (found.group(1) or found.group(2)).encode("latin-1")
    # Every delimiter is CRLF "--" boundary; the first one may start the body directly.
    parts = (b"\r\n" + body).split(b"\r\n--" + boundary)
    for part in parts[1:]:
        if part.startswith(b"--"):
            break  # closing delimiter
        head, separator, content = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        for line in head.decode("latin-1").split("\r\n"):
            name, _, value = line.partition(":")
            if name.strip().lower() != "content-disposition":
                continue
            match = _NAME.search(value)
            if match is not None and match.group(1) == field:
                return content
    raise _error(422, "upload_invalid")
