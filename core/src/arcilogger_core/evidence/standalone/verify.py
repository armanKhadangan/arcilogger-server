#!/usr/bin/env python3
"""ARCILOGGER kanıt paketi doğrulayıcı — ARCILOGGER evidence package verifier.

Bu dosya tek başına çalışır: yalnızca Python 3 standart kütüphanesini kullanır (Python 3.9 veya
üstü), ağa bağlanmaz ve hiçbir dosyayı değiştirmez. Kullanım::

    python -I -S verify.py [PAKET] [--kokler KLASÖR] [--hizli] [--json]

PAKET, açılmış paket klasörü ya da şifresiz ZIP dosyasıdır; verilmezse bu dosyanın bulunduğu
klasör doğrulanır. ``-I -S`` Python'u yalıtır: kurulu ek paketler ve ortam değişkenleri
kullanılmaz, yani sonuç yalnızca bu dosyadaki koda dayanır.

(English) A single file that needs only the Python 3 standard library (Python 3.9 or newer). It
never connects to a network and never writes a file. What it checks, in this order (each step is
explained again where it is implemented):

0. Every file name: safe and distinct as Windows sees names (otherwise exit code 2), because
   what this program checks must be what an archive tool unpacks.
1. ``paket.json``, the package index: canonical JSON (RFC 8785) and the size and SHA-256 of every
   listed file; in an evidence package it must exist and list every file.
2. The root certificates in the package (or the ones given with ``--kokler``) and their
   fingerprints. This program cannot know whether a root really belongs to Kamu SM: compare the
   printed fingerprints with the values Kamu SM publishes.
3. Every sealed day: the manifest (canonical JSON), every file it lists (size and SHA-256;
   without ``--hizli`` also the uncompressed SHA-256 and the line count), the RFC 3161 time-stamp
   token over the manifest (SHA-256 or stronger, CMS signature, signed attributes,
   ESSCertID/ESSCertIDv2, the critical id-kp-timeStamping key purpose, key usage, the
   certificate chain up to a root with every certificate valid at the stamp time, certificate
   revocation lists contained in the package), when the day was stamped (computed from the
   token: more than 48 hours after the day is "late"), and the link to the previous day
   (including days missing from the package, and runs of empty days a manifest lists as
   ``unsealed_empty_days`` instead of stamping them one by one). ARCILOGGER's own audit log is
   left out of packages by default (``scope.audit_log``): its absence is reported, not failed.
4. Deletion records, the chain anchor and renewal stamps when the package contains them.
5. The extract: every extracted line equals, byte for byte, that line of the sealed file.
6. Nothing else: a file in a sealed day folder that no manifest lists was never sealed and is a
   failure; in an evidence package any file that is not a known part is a failure too.

A copy of the vault (for example the replica) can be verified as well: it has no ``paket.json``
and is recognised by its layout; then only the sealed days and records are checked.

Exit code: 0 everything verified, 1 at least one check failed, 2 the package cannot be read.
Warnings do not change the exit code but must be read.

Clean-room implementation written for ARCILOGGER from the public standards only: ITU-T X.690
(DER/BER), RFC 5280 (X.509 certificates and CRLs), RFC 5652 (CMS), RFC 3161 and RFC 5816 (time
stamps, ESSCertIDv2), RFC 2634 (ESSCertID), RFC 8017 (RSA PKCS #1 v1.5 and PSS), FIPS 186-4 /
SEC 1 (ECDSA on P-256, P-384 and P-521) and RFC 8785 (JSON canonicalization). Online revocation
checks (OCSP, downloading CRLs) are not done.
"""

# ruff: noqa: UP017
# UP017: ``datetime.UTC`` only exists from Python 3.11; this file must run on Python 3.9.

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import hmac
import json
import os
import re
import sys
import unicodedata
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any

VERIFIER_VERSION = "4"
INDEX_NAME = "paket.json"
INDEX_SCHEMA = "arcilogger.evidence/1"
#: The guides. The Turkish one has an ASCII name: ZIP tools that ignore the UTF-8 name flag
#: would turn "DOĞRULAMA" into something else, and the package would no longer match its index.
GUIDE_TR = "DOGRULAMA.txt"
GUIDE_EN = "VERIFY-EN.txt"
VERIFIER_NAMES = ("verify.py", "verify.ps1", "verify.sh")
UTC = timezone.utc
#: Türkiye has used UTC+03:00 all year since 2016, so no tz database is needed: for display, and
#: for the end of a sealed day (midnight Türkiye time) in the lateness check.
TURKEY = timezone(timedelta(hours=3))
CLOCK_SKEW = timedelta(minutes=5)
#: Build specification 6.8: a day stamped more than 48 hours after it ended was stamped late
#: (ARCILOGGER's default ``seal.late_after_hours``). The verifier works this out from the token
#: itself instead of trusting the ``stamped_late`` flag of the manifest.
LATE_AFTER = timedelta(hours=48)
#: ARCILOGGER writes the manifest's ``created_at`` (server clock) seconds before the TSA stamps
#: it (genTime, TSA clock). A larger difference means a wrong clock, or a manifest that was
#: written at one time and stamped much later.
CREATED_TOLERANCE = timedelta(hours=1)
MAX_CHAIN_DEPTH = 8
CHUNK = 1024 * 1024
MAX_LISTED = 20
#: Upper limits that keep a hostile package from exhausting the memory of the auditor's
#: machine: a metadata file (index, manifest, token, certificate, CRL, extract) read whole, and
#: one raw line of a sealed file.
MAX_READ = 64 * 1024 * 1024
MAX_LINE = 16 * 1024 * 1024


# =================================================================================================
# 1. DER / BER decoding (ITU-T X.690)
# =================================================================================================


class DerError(ValueError):
    """The bytes are not a valid DER (or BER) encoding."""


# Universal tag numbers (ITU-T X.680).
BOOLEAN, INTEGER, BIT_STRING, OCTET_STRING, NULL, OID = 1, 2, 3, 4, 5, 6
UTF8_STRING, SEQUENCE, SET = 12, 16, 17
UTC_TIME, GENERALIZED_TIME, UNIVERSAL_STRING, BMP_STRING = 23, 24, 28, 30
UNIVERSAL, CONTEXT = 0, 2
MAX_DEPTH = 64
#: Universal types that DER (X.690 10.2) and BER always encode in the primitive form.
_PRIMITIVE_ONLY = frozenset({BOOLEAN, INTEGER, NULL, OID, UTC_TIME, GENERALIZED_TIME})


@dataclass(frozen=True)
class Tlv:
    """One decoded element: its tag, its contents octets and its complete encoding."""

    cls: int
    constructed: bool
    tag: int
    raw: bytes
    value: bytes

    def has_tag(self, tag: int, cls: int = UNIVERSAL) -> bool:
        return self.cls == cls and self.tag == tag

    def children(self) -> list[Tlv]:
        if not self.constructed:
            raise DerError("primitive element has no children")
        return parse_all(self.value)


def read_tlv(data: bytes, offset: int = 0, depth: int = 0) -> tuple[Tlv, int]:
    """Decode the element starting at ``offset``; return it and the offset after it.

    BER indefinite lengths (``0x80`` … end-of-contents) are accepted for constructed elements
    because some CMS producers use them; everything that is signed is re-encoded as DER.
    """
    if depth > MAX_DEPTH:
        raise DerError("nesting too deep")
    start = offset
    if offset >= len(data):
        raise DerError("truncated (no tag)")
    first = data[offset]
    offset += 1
    cls, constructed, tag = first >> 6, bool(first & 0x20), first & 0x1F
    if tag == 0x1F:  # high tag number form
        tag = 0
        while True:
            if offset >= len(data) or tag > 1 << 28:
                raise DerError("bad tag")
            byte = data[offset]
            offset += 1
            tag = (tag << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
    if offset >= len(data):
        raise DerError("truncated (no length)")
    length_byte = data[offset]
    offset += 1
    if length_byte == 0x80:
        if not constructed:
            raise DerError("indefinite length on a primitive element")
        content_start = offset
        while data[offset : offset + 2] != b"\x00\x00":
            _, offset = read_tlv(data, offset, depth + 1)
        value = data[content_start:offset]
        return Tlv(cls, constructed, tag, data[start : offset + 2], value), offset + 2
    if length_byte < 0x80:
        length = length_byte
    else:
        count = length_byte & 0x7F
        if count > 8 or offset + count > len(data):
            raise DerError("bad length")
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise DerError("truncated (content)")
    return Tlv(cls, constructed, tag, data[start:end], data[offset:end]), end


def parse_all(data: bytes) -> list[Tlv]:
    items = []
    offset = 0
    while offset < len(data):
        item, offset = read_tlv(data, offset)
        items.append(item)
    return items


def parse_single(data: bytes) -> Tlv:
    """Decode exactly one element; trailing bytes are an error."""
    item, end = read_tlv(data)
    if end != len(data):
        raise DerError("trailing bytes after the element")
    return item


def expect(node: Tlv, tag: int, what: str, cls: int = UNIVERSAL) -> Tlv:
    if not node.has_tag(tag, cls):
        raise DerError(f"{what}: unexpected element (class {node.cls}, tag {node.tag})")
    if cls == UNIVERSAL and node.constructed and tag in _PRIMITIVE_ONLY:
        # A "constructed INTEGER" is not an encoding at all; OpenSSL rejects it too.
        raise DerError(f"{what}: constructed encoding of a primitive type")
    return node


def seq(node: Tlv, what: str, minimum: int = 0) -> list[Tlv]:
    """The elements of a SEQUENCE, with at least ``minimum`` of them."""
    items = expect(node, SEQUENCE, what).children()
    if len(items) < minimum:
        raise DerError(f"{what}: too few elements")
    return items


def to_int(node: Tlv) -> int:
    """An INTEGER in its one minimal encoding (X.690 8.3.2), as OpenSSL and Java require."""
    expect(node, INTEGER, "INTEGER")
    value = node.value
    if not value:
        raise DerError("empty INTEGER")
    if len(value) > 1 and (
        (value[0] == 0x00 and value[1] < 0x80) or (value[0] == 0xFF and value[1] >= 0x80)
    ):
        raise DerError("INTEGER is not minimally encoded")
    return int.from_bytes(value, "big", signed=True)


def to_oid(node: Tlv) -> str:
    """Object identifier as dotted text, e.g. ``1.2.840.113549.1.7.2``."""
    expect(node, OID, "OBJECT IDENTIFIER")
    if not node.value or node.value[-1] & 0x80:
        raise DerError("bad OBJECT IDENTIFIER")
    numbers = []
    current = 0
    for byte in node.value:
        current = (current << 7) | (byte & 0x7F)
        if not byte & 0x80:
            numbers.append(current)
            current = 0
    first = numbers[0]
    arcs = [0, first] if first < 40 else [1, first - 40] if first < 80 else [2, first - 80]
    return ".".join(str(arc) for arc in arcs + numbers[1:])


def to_octets(node: Tlv, depth: int = 0) -> bytes:
    """The bytes of an OCTET STRING (BER allows it to be split into a constructed form).

    The nesting of the constructed form is limited like every other nesting, so a hostile file
    cannot make this recursion exhaust the stack.
    """
    expect(node, OCTET_STRING, "OCTET STRING")
    if not node.constructed:
        return node.value
    if depth >= MAX_DEPTH:
        raise DerError("nesting too deep")
    return b"".join(to_octets(child, depth + 1) for child in node.children())


def to_bits(node: Tlv) -> bytes:
    """The bytes of a BIT STRING that holds whole bytes (keys, signatures)."""
    expect(node, BIT_STRING, "BIT STRING")
    if node.constructed or not node.value or node.value[0] != 0:
        raise DerError("BIT STRING with unused bits")
    return node.value[1:]


def to_bool(node: Tlv) -> bool:
    expect(node, BOOLEAN, "BOOLEAN")
    return node.value != b"\x00"


def to_time(node: Tlv) -> datetime:
    """UTCTime or GeneralizedTime (both always in UTC, ``Z``, as RFC 5280 requires)."""
    if node.constructed:
        raise DerError("constructed time")
    text = node.value.decode("ascii", "replace")
    micro = 0
    if node.has_tag(UTC_TIME):
        match = re.fullmatch(r"(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?Z", text)
        if match is None:
            raise DerError(f"bad UTCTime {text!r}")
        year = int(match.group(1))
        year += 1900 if year >= 50 else 2000  # RFC 5280 4.1.2.5.1
    elif node.has_tag(GENERALIZED_TIME):
        match = re.fullmatch(r"(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(?:[.,](\d+))?Z", text)
        if match is None:
            raise DerError(f"bad GeneralizedTime {text!r}")
        year = int(match.group(1))
        if match.group(7):
            micro = int(match.group(7)[:6].ljust(6, "0"))
    else:
        raise DerError("not a time")
    month, day, hour, minute = (int(match.group(i)) for i in (2, 3, 4, 5))
    second = int(match.group(6) or 0)
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
    except ValueError as exc:
        raise DerError(f"bad time {text!r}") from exc


def to_text(node: Tlv) -> str:
    """A directory string (UTF8String, PrintableString, BMPString, …) as text."""
    if node.has_tag(BMP_STRING):
        return node.value.decode("utf-16-be", "replace")
    if node.has_tag(UNIVERSAL_STRING):
        return node.value.decode("utf-32-be", "replace")
    try:
        return node.value.decode("utf-8")
    except UnicodeDecodeError:
        return node.value.decode("latin-1")


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def encode(tag_byte: int, value: bytes) -> bytes:
    """DER encoding of one element (the tag byte includes class and constructed bit)."""
    return bytes([tag_byte]) + encode_length(len(value)) + value


def encode_oid(dotted: str) -> bytes:
    arcs = [int(arc) for arc in dotted.split(".")]
    body = b""
    for number in [arcs[0] * 40 + arcs[1], *arcs[2:]]:
        chunk = [number & 0x7F]
        number >>= 7
        while number:
            chunk.append(0x80 | (number & 0x7F))
            number >>= 7
        body += bytes(reversed(chunk))
    return encode(OID, body)


# =================================================================================================
# 2. Object identifiers and algorithm names
# =================================================================================================

SIGNED_DATA = "1.2.840.113549.1.7.2"
TST_INFO = "1.2.840.113549.1.9.16.1.4"
ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
ATTR_SIGNING_CERTIFICATE = "1.2.840.113549.1.9.16.2.12"
ATTR_SIGNING_CERTIFICATE_V2 = "1.2.840.113549.1.9.16.2.47"
EXT_BASIC_CONSTRAINTS = "2.5.29.19"
EXT_KEY_USAGE = "2.5.29.15"
EXT_CRL_REASON = "2.5.29.21"
EXT_EXTENDED_KEY_USAGE = "2.5.29.37"
EXT_SUBJECT_KEY_IDENTIFIER = "2.5.29.14"
KP_TIME_STAMPING = "1.3.6.1.5.5.7.3.8"
RSA_ENCRYPTION = "1.2.840.113549.1.1.1"
RSASSA_PSS = "1.2.840.113549.1.1.10"
MGF1 = "1.2.840.113549.1.1.8"
EC_PUBLIC_KEY = "1.2.840.10045.2.1"
#: Policy OIDs under the "example" arc 2.999 are used by ARCILOGGER's mock (test) TSA only.
TEST_POLICY_PREFIX = "2.999."
#: Organization (O) in every certificate of ARCILOGGER's mock TSA.
TEST_ORGANIZATION = "ARCILOGGER TEST - NOT VALID"
#: A ``gaps`` entry of a manifest that stands for a run of empty days that were not stamped
#: one by one (ARCILOGGER ``seal.max_consecutive_empty_days``).
UNSEALED_DAYS = "unsealed_empty_days"

HASH_OIDS = {
    "1.3.14.3.2.26": "sha1",
    "2.16.840.1.101.3.4.2.4": "sha224",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512",
}
HASH_OID_BY_NAME = {name: oid for oid, name in HASH_OIDS.items()}
#: Hashes accepted for what a time stamp binds (the imprint and the signature over the signed
#: attributes). ARCILOGGER stamps with SHA-256 (or SHA-512); SHA-1 collisions are practical.
STRONG_DIGESTS = frozenset({"sha256", "sha384", "sha512"})
#: keyUsage bits (RFC 5280 4.2.1.3).
KU_DIGITAL_SIGNATURE, KU_NON_REPUDIATION, KU_KEY_CERT_SIGN = 0, 1, 5
#: CRLReason values (RFC 5280 5.3.1) that do not mean "revoked for good".
CRL_REASON_HOLD, CRL_REASON_REMOVE = 6, 8
RSA_PKCS1_WITH_HASH = {
    "1.2.840.113549.1.1.5": "sha1",
    "1.2.840.113549.1.1.14": "sha224",
    "1.2.840.113549.1.1.11": "sha256",
    "1.2.840.113549.1.1.12": "sha384",
    "1.2.840.113549.1.1.13": "sha512",
}
ECDSA_WITH_HASH = {
    "1.2.840.10045.4.1": "sha1",
    "1.2.840.10045.4.3.1": "sha224",
    "1.2.840.10045.4.3.2": "sha256",
    "1.2.840.10045.4.3.3": "sha384",
    "1.2.840.10045.4.3.4": "sha512",
}
NAME_ATTRIBUTES = {
    "2.5.4.3": "CN",
    "2.5.4.5": "serialNumber",
    "2.5.4.6": "C",
    "2.5.4.7": "L",
    "2.5.4.8": "ST",
    "2.5.4.10": "O",
    "2.5.4.11": "OU",
    "2.5.4.97": "organizationIdentifier",
    "1.2.840.113549.1.9.1": "E",
}


class CheckError(Exception):
    """A failed check: a stable code plus a Turkish and an English explanation."""

    def __init__(self, code: str, tr: str, en: str) -> None:
        super().__init__(en)
        self.code = code
        self.tr = tr
        self.en = en


@dataclass(frozen=True)
class Algorithm:
    """An AlgorithmIdentifier: OID plus the (optional) parameters element."""

    oid: str
    params: Tlv | None


def to_algorithm(node: Tlv) -> Algorithm:
    parts = seq(node, "AlgorithmIdentifier", 1)
    return Algorithm(to_oid(parts[0]), parts[1] if len(parts) > 1 else None)


def hash_name(algorithm: Algorithm) -> str:
    name = HASH_OIDS.get(algorithm.oid)
    if name is None:
        raise CheckError(
            "algorithm",
            f"Desteklenmeyen özet algoritması {algorithm.oid}",
            f"unsupported digest algorithm {algorithm.oid}",
        )
    return name


# =================================================================================================
# 3. Signature verification: RSA PKCS #1 v1.5 and PSS (RFC 8017), ECDSA (FIPS 186-4)
# =================================================================================================


@dataclass(frozen=True)
class Curve:
    """A NIST prime curve y² = x³ + ax + b (mod p) with generator (gx, gy) of order n."""

    name: str
    p: int
    a: int
    b: int
    gx: int
    gy: int
    n: int

    @property
    def size(self) -> int:
        return (self.p.bit_length() + 7) // 8


# Domain parameters from FIPS 186-4 appendix D.1.2 (a = p - 3 for all three curves).
_P256 = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_P384 = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFF",
    16,
)
_P521 = 2**521 - 1
CURVES = {
    "1.2.840.10045.3.1.7": Curve(
        "P-256",
        p=_P256,
        a=_P256 - 3,
        b=0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
        gx=0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
        gy=0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
        n=0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
    ),
    "1.3.132.0.34": Curve(
        "P-384",
        p=_P384,
        a=_P384 - 3,
        b=int(
            "B3312FA7E23EE7E4988E056BE3F82D19181D9C6EFE8141120314088F5013875A"
            "C656398D8A2ED19D2A85C8EDD3EC2AEF",
            16,
        ),
        gx=int(
            "AA87CA22BE8B05378EB1C71EF320AD746E1D3B628BA79B9859F741E082542A38"
            "5502F25DBF55296C3A545E3872760AB7",
            16,
        ),
        gy=int(
            "3617DE4A96262C6F5D9E98BF9292DC29F8F41DBD289A147CE9DA3113B5F0B8C0"
            "0A60B1CE1D7E819D7A431D7C90EA0E5F",
            16,
        ),
        n=int(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF"
            "581A0DB248B0A77AECEC196ACCC52973",
            16,
        ),
    ),
    "1.3.132.0.35": Curve(
        "P-521",
        p=_P521,
        a=_P521 - 3,
        b=int(
            "0051953EB9618E1C9A1F929A21A0B68540EEA2DA725B99B315F3B8B489918EF1"
            "09E156193951EC7E937B1652C0BD3BB1BF073573DF883D2C34F1EF451FD46B503F00",
            16,
        ),
        gx=int(
            "00C6858E06B70404E9CD9E3ECB662395B4429C648139053FB521F828AF606B4D"
            "3DBAA14B5E77EFE75928FE1DC127A2FFA8DE3348B3C1856A429BF97E7E31C2E5BD66",
            16,
        ),
        gy=int(
            "011839296A789A3BC0045C8A5FB42C7D1BD998F54449579B446817AFBD17273E"
            "662C97EE72995EF42640C550B9013FAD0761353C7086A272C24088BE94769FD16650",
            16,
        ),
        n=int(
            "01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFA"
            "51868783BF2F966B7FCC0148F709A5D03BB5C9B8899C47AEBB6FB71E91386409",
            16,
        ),
    ),
}

#: Points in Jacobian coordinates (X, Y, Z) stand for the affine point (X/Z², Y/Z³);
#: Z = 0 is the point at infinity.
INFINITY = (0, 1, 0)


def _double(curve: Curve, point: tuple[int, int, int]) -> tuple[int, int, int]:
    x, y, z = point
    p = curve.p
    if z == 0 or y == 0:
        return INFINITY
    ysq = y * y % p
    s = 4 * x * ysq % p
    m = (3 * x * x + curve.a * pow(z, 4, p)) % p
    nx = (m * m - 2 * s) % p
    ny = (m * (s - nx) - 8 * ysq * ysq) % p
    return nx, ny, 2 * y * z % p


def _add(
    curve: Curve, first: tuple[int, int, int], second: tuple[int, int, int]
) -> tuple[int, int, int]:
    if first[2] == 0:
        return second
    if second[2] == 0:
        return first
    p = curve.p
    x1, y1, z1 = first
    x2, y2, z2 = second
    z1sq, z2sq = z1 * z1 % p, z2 * z2 % p
    u1, u2 = x1 * z2sq % p, x2 * z1sq % p
    s1, s2 = y1 * z2sq * z2 % p, y2 * z1sq * z1 % p
    if u1 == u2:
        return _double(curve, first) if s1 == s2 else INFINITY
    h, r = (u2 - u1) % p, (s2 - s1) % p
    hsq = h * h % p
    hcu = hsq * h % p
    nx = (r * r - hcu - 2 * u1 * hsq) % p
    ny = (r * (u1 * hsq - nx) - s1 * hcu) % p
    return nx, ny, h * z1 * z2 % p


def _multiply(curve: Curve, scalar: int, point: tuple[int, int, int]) -> tuple[int, int, int]:
    """Double-and-add (verification handles only public values, so timing does not matter)."""
    result = INFINITY
    for bit in bin(scalar)[2:]:
        result = _double(curve, result)
        if bit == "1":
            result = _add(curve, result, point)
    return result


def ec_multiply(curve: Curve, scalar: int, x: int, y: int) -> tuple[int, int] | None:
    """``scalar`` times (x, y) in affine coordinates, None for the point at infinity."""
    point = _multiply(curve, scalar, (x, y, 1))
    if point[2] == 0:
        return None
    z_inv = pow(point[2], -1, curve.p)
    return point[0] * z_inv * z_inv % curve.p, point[1] * pow(z_inv, 3, curve.p) % curve.p


def on_curve(curve: Curve, x: int, y: int) -> bool:
    p = curve.p
    return 0 <= x < p and 0 <= y < p and (y * y - (x * x * x + curve.a * x + curve.b)) % p == 0


@dataclass(frozen=True)
class RsaKey:
    n: int
    e: int


@dataclass(frozen=True)
class EcKey:
    curve: Curve
    x: int
    y: int


def ecdsa_verify(key: EcKey, digest_name: str, message: bytes, signature: bytes) -> bool:
    """ECDSA verification (FIPS 186-4 section 6.4.2 / SEC 1 section 4.1.4)."""
    try:
        parts = seq(parse_single(signature), "ECDSA-Sig-Value", 2)
        r, s = to_int(parts[0]), to_int(parts[1])
    except DerError:
        return False
    curve = key.curve
    n = curve.n
    if not (0 < r < n and 0 < s < n):
        return False
    # The signature is not covered by itself: accept only the one DER encoding of (r, s), as
    # OpenSSL and cryptography do, so that every verifier gives the same answer.
    if len(parts) != 2 or encode(0x30, _der_integer(r) + _der_integer(s)) != signature:
        return False
    digest = hashlib.new(digest_name, message).digest()
    e = int.from_bytes(digest, "big")
    excess = len(digest) * 8 - n.bit_length()
    if excess > 0:
        e >>= excess  # use the leftmost bits of the hash, as many as the order has
    w = pow(s, -1, n)
    point = _add(
        curve,
        _multiply(curve, e * w % n, (curve.gx, curve.gy, 1)),
        _multiply(curve, r * w % n, (key.x, key.y, 1)),
    )
    if point[2] == 0:
        return False
    z_inv = pow(point[2], -1, curve.p)
    return point[0] * z_inv * z_inv % curve.p % n == r


def _der_integer(value: int) -> bytes:
    """DER of a non-negative INTEGER (a leading zero byte when the high bit is set)."""
    return encode(INTEGER, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def _digest_info(digest_name: str, digest: bytes, with_null: bool) -> bytes:
    """DER DigestInfo (RFC 8017 9.2). Parameters are NULL; some signers omit them."""
    algorithm = encode_oid(HASH_OID_BY_NAME[digest_name]) + (
        encode(NULL, b"") if with_null else b""
    )
    return encode(0x30, encode(0x30, algorithm) + encode(OCTET_STRING, digest))


def rsa_pkcs1v15_verify(key: RsaKey, digest_name: str, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 verification (RFC 8017 8.2.2): rebuild the encoded message, compare."""
    k = (key.n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= key.n:
        return False
    em = pow(s, key.e, key.n).to_bytes(k, "big")
    digest = hashlib.new(digest_name, message).digest()
    for with_null in (True, False):
        info = _digest_info(digest_name, digest, with_null)
        padding = k - len(info) - 3
        expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + info
        if padding >= 8 and hmac.compare_digest(em, expected):
            return True
    return False


def _mgf1(seed: bytes, length: int, digest_name: str) -> bytes:
    output = b""
    counter = 0
    while len(output) < length:
        output += hashlib.new(digest_name, seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return output[:length]


def rsa_pss_verify(
    key: RsaKey, digest_name: str, mgf_digest: str, salt_length: int, message: bytes, sig: bytes
) -> bool:
    """RSASSA-PSS verification (RFC 8017 8.1.2 and EMSA-PSS-VERIFY 9.1.2)."""
    mod_bits = key.n.bit_length()
    if len(sig) != (mod_bits + 7) // 8:
        return False
    s = int.from_bytes(sig, "big")
    if s >= key.n:
        return False
    em_bits = mod_bits - 1
    em_len = (em_bits + 7) // 8
    m = pow(s, key.e, key.n)
    if m.bit_length() > em_len * 8:
        return False
    em = m.to_bytes(em_len, "big")
    m_hash = hashlib.new(digest_name, message).digest()
    h_len = len(m_hash)
    if em_len < h_len + salt_length + 2 or em[-1] != 0xBC:
        return False
    masked_db, h = em[: em_len - h_len - 1], em[em_len - h_len - 1 : -1]
    zero_bits = 8 * em_len - em_bits
    if zero_bits and masked_db[0] >> (8 - zero_bits):
        return False
    mask = _mgf1(h, len(masked_db), mgf_digest)
    db_value = int.from_bytes(masked_db, "big") ^ int.from_bytes(mask, "big")
    db_value &= (1 << (len(masked_db) * 8 - zero_bits)) - 1
    db = db_value.to_bytes(len(masked_db), "big")
    pad = em_len - h_len - salt_length - 2
    if db[:pad] != b"\x00" * pad or db[pad] != 0x01:
        return False
    salt = db[len(db) - salt_length :] if salt_length else b""
    return hmac.compare_digest(h, hashlib.new(digest_name, b"\x00" * 8 + m_hash + salt).digest())


def _pss_parameters(params: Tlv | None) -> tuple[str, str, int]:
    """RSASSA-PSS-params (RFC 4055): hash, MGF1 hash and salt length, with their defaults."""
    digest, mgf_digest, salt = "sha1", "sha1", 20
    if params is None or params.has_tag(NULL):
        return digest, mgf_digest, salt
    for item in seq(params, "RSASSA-PSS-params"):
        inner = item.children()[0]
        if item.has_tag(0, CONTEXT):
            digest = hash_name(to_algorithm(inner))
        elif item.has_tag(1, CONTEXT):
            mgf = to_algorithm(inner)
            if mgf.oid != MGF1 or mgf.params is None:
                raise CheckError("algorithm", "Desteklenmeyen PSS maske işlevi", "unsupported MGF")
            mgf_digest = hash_name(to_algorithm(mgf.params))
        elif item.has_tag(2, CONTEXT):
            salt = to_int(inner)
        elif item.has_tag(3, CONTEXT) and to_int(inner) != 1:
            raise CheckError("algorithm", "Desteklenmeyen PSS trailer", "unsupported PSS trailer")
    return digest, mgf_digest, salt


def verify_signature(
    key: RsaKey | EcKey | None,
    algorithm: Algorithm,
    data: bytes,
    signature: bytes,
    digest_hint: str | None = None,
) -> str:
    """Check ``signature`` over ``data``; return a description such as ``ECDSA P-256 / sha256``.

    ``digest_hint`` is the CMS digestAlgorithm of the SignerInfo. When given it decides the hash
    for RSA PKCS #1 v1.5 and ECDSA, as in ARCILOGGER's core verifier (and needed anyway for plain
    ``rsaEncryption`` / ``id-ecPublicKey``, which name no hash); certificates and CRLs have no
    hint and use the hash their signature algorithm names. Raises :class:`CheckError`.
    """
    oid = algorithm.oid
    valid = False
    if oid in RSA_PKCS1_WITH_HASH or oid == RSA_ENCRYPTION:
        digest = digest_hint or RSA_PKCS1_WITH_HASH.get(oid)
        if isinstance(key, RsaKey) and digest:
            valid = rsa_pkcs1v15_verify(key, digest, data, signature)
            description = f"RSA {key.n.bit_length()} bit PKCS#1 v1.5 / {digest}"
        else:
            description = "RSA"
    elif oid == RSASSA_PSS:
        digest, mgf_digest, salt = _pss_parameters(algorithm.params)
        if isinstance(key, RsaKey):
            valid = rsa_pss_verify(key, digest, mgf_digest, salt, data, signature)
            description = f"RSA {key.n.bit_length()} bit PSS / {digest}"
        else:
            description = "RSA-PSS"
    elif oid in ECDSA_WITH_HASH or oid == EC_PUBLIC_KEY:
        digest = digest_hint or ECDSA_WITH_HASH.get(oid)
        if isinstance(key, EcKey) and digest:
            valid = ecdsa_verify(key, digest, data, signature)
            description = f"ECDSA {key.curve.name} / {digest}"
        else:
            description = "ECDSA"
    else:
        raise CheckError(
            "algorithm",
            f"Desteklenmeyen imza algoritması {oid}",
            f"unsupported signature algorithm {oid}",
        )
    if not valid:
        raise CheckError(
            "signature",
            f"İmza geçersiz ({description})",
            f"signature invalid ({description})",
        )
    return description


# =================================================================================================
# 4. X.509 certificates and CRLs (RFC 5280)
# =================================================================================================


@dataclass(frozen=True)
class Extension:
    critical: bool
    value: bytes


@dataclass(frozen=True)
class Certificate:
    """The parts of an X.509 certificate this program needs."""

    der: bytes
    tbs: bytes
    algorithm: Algorithm
    signature: bytes
    serial: int
    issuer: bytes  # DER of the issuer Name
    subject: bytes  # DER of the subject Name
    not_before: datetime
    not_after: datetime
    key: RsaKey | EcKey | None
    key_description: str
    extensions: dict[str, Extension] = field(hash=False, compare=False)

    @property
    def subject_text(self) -> str:
        return name_text(self.subject)

    @property
    def issuer_text(self) -> str:
        return name_text(self.issuer)

    def fingerprint(self, digest_name: str = "sha256") -> str:
        """Upper-case hex of the hash of the certificate; :func:`colon_hex` gives ``AB:CD:…``,
        the form certificate viewers and CA web pages show."""
        return hashlib.new(digest_name, self.der).hexdigest().upper()

    def valid_at(self, moment: datetime) -> bool:
        return self.not_before <= moment <= self.not_after

    def is_ca(self) -> bool | None:
        """basicConstraints cA, or None when the extension is absent."""
        extension = self.extensions.get(EXT_BASIC_CONSTRAINTS)
        if extension is None:
            return None
        parts = seq(parse_single(extension.value), "BasicConstraints")
        return bool(parts) and parts[0].has_tag(BOOLEAN) and to_bool(parts[0])

    def path_length(self) -> int | None:
        """basicConstraints pathLenConstraint, or None when there is no limit."""
        extension = self.extensions.get(EXT_BASIC_CONSTRAINTS)
        if extension is None:
            return None
        for part in seq(parse_single(extension.value), "BasicConstraints"):
            if part.has_tag(INTEGER):
                return to_int(part)
        return None

    def key_usage(self) -> set[int] | None:
        """The keyUsage bits that are set (``KU_*``), or None when the extension is absent."""
        extension = self.extensions.get(EXT_KEY_USAGE)
        if extension is None:
            return None
        node = expect(parse_single(extension.value), BIT_STRING, "KeyUsage")
        if node.constructed or not node.value or node.value[0] > 7:
            raise DerError("bad KeyUsage")
        bits = node.value[1:]
        return {i for i in range(len(bits) * 8) if bits[i // 8] & (0x80 >> (i % 8))}

    def key_identifier(self) -> bytes | None:
        extension = self.extensions.get(EXT_SUBJECT_KEY_IDENTIFIER)
        return to_octets(parse_single(extension.value)) if extension else None


def colon_hex(hex_text: str) -> str:
    return ":".join(hex_text[i : i + 2] for i in range(0, len(hex_text), 2))


def name_text(raw: bytes) -> str:
    """A distinguished name as ``CN=…, O=…, C=TR`` (most specific first, like RFC 4514)."""
    try:
        parts = []
        for rdn in seq(parse_single(raw), "Name"):
            for pair in expect(rdn, SET, "RelativeDistinguishedName").children():
                type_node, value_node = seq(pair, "AttributeTypeAndValue", 2)[:2]
                oid = to_oid(type_node)
                parts.append(f"{NAME_ATTRIBUTES.get(oid, oid)}={to_text(value_node)}")
        return ", ".join(reversed(parts))
    except DerError:
        return "(okunamayan ad / unreadable name)"


def _public_key(spki: Tlv) -> tuple[RsaKey | EcKey | None, str]:
    parts = seq(spki, "SubjectPublicKeyInfo", 2)
    algorithm = to_algorithm(parts[0])
    bits = to_bits(parts[1])
    if algorithm.oid in (RSA_ENCRYPTION, RSASSA_PSS):
        numbers = seq(parse_single(bits), "RSAPublicKey", 2)
        key = RsaKey(to_int(numbers[0]), to_int(numbers[1]))
        return key, f"RSA {key.n.bit_length()} bit"
    if algorithm.oid == EC_PUBLIC_KEY:
        curve_oid = to_oid(algorithm.params) if algorithm.params is not None else ""
        curve = CURVES.get(curve_oid)
        if curve is None:
            return None, f"EC (desteklenmeyen eğri / unsupported curve {curve_oid})"
        point = _decode_point(curve, bits)
        if point is None:
            return None, f"EC {curve.name} (geçersiz nokta / invalid point)"
        return EcKey(curve, point[0], point[1]), f"EC {curve.name}"
    return None, f"desteklenmeyen anahtar / unsupported key {algorithm.oid}"


def _decode_point(curve: Curve, data: bytes) -> tuple[int, int] | None:
    """SEC 1 2.3.4: uncompressed (04 X Y) or compressed (02/03 X) point on the curve."""
    size = curve.size
    if len(data) == 1 + 2 * size and data[0] == 4:
        x = int.from_bytes(data[1 : 1 + size], "big")
        y = int.from_bytes(data[1 + size :], "big")
    elif len(data) == 1 + size and data[0] in (2, 3):
        x = int.from_bytes(data[1:], "big")
        # All three NIST primes are 3 mod 4, so a square root is a single exponentiation.
        y = pow((x * x * x + curve.a * x + curve.b) % curve.p, (curve.p + 1) // 4, curve.p)
        if y % 2 != data[0] % 2:
            y = curve.p - y
    else:
        return None
    return (x, y) if on_curve(curve, x, y) else None


def parse_certificate(der: bytes) -> Certificate:
    certificate = seq(parse_single(der), "Certificate", 3)
    fields = seq(certificate[0], "TBSCertificate", 6)
    index = 1 if fields[0].has_tag(0, CONTEXT) else 0  # [0] EXPLICIT version
    serial = to_int(fields[index])
    issuer = expect(fields[index + 2], SEQUENCE, "issuer").raw
    validity = seq(fields[index + 3], "Validity", 2)
    subject = expect(fields[index + 4], SEQUENCE, "subject").raw
    key, key_description = _public_key(fields[index + 5])
    extensions: dict[str, Extension] = {}
    for item in fields[index + 6 :]:
        if item.has_tag(3, CONTEXT):
            for extension in seq(item.children()[0], "Extensions"):
                parts = seq(extension, "Extension", 2)
                critical = len(parts) == 3 and to_bool(parts[1])
                extensions[to_oid(parts[0])] = Extension(critical, to_octets(parts[-1]))
    return Certificate(
        der=der,
        tbs=certificate[0].raw,
        algorithm=to_algorithm(certificate[1]),
        signature=to_bits(certificate[2]),
        serial=serial,
        issuer=issuer,
        subject=subject,
        not_before=to_time(validity[0]),
        not_after=to_time(validity[1]),
        key=key,
        key_description=key_description,
        extensions=extensions,
    )


_PEM = re.compile(rb"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.DOTALL)


def pem_blocks(data: bytes, label: str) -> list[bytes]:
    """DER blocks of a PEM file with the given label, or ``[data]`` for a DER file."""
    if b"-----BEGIN " not in data:
        return [data]
    blocks = []
    for match in _PEM.finditer(data):
        if match.group(1).decode("ascii") == label:
            try:
                blocks.append(base64.b64decode(re.sub(rb"\s+", b"", match.group(2)), validate=True))
            except binascii.Error as exc:
                raise DerError("bad PEM base64") from exc
    return blocks


def load_certificates(data: bytes) -> list[Certificate]:
    return [parse_certificate(block) for block in pem_blocks(data, "CERTIFICATE")]


@dataclass(frozen=True)
class Crl:
    """A certificate revocation list (SİL) as retrieved from the CA."""

    tbs: bytes
    algorithm: Algorithm
    signature: bytes
    issuer: bytes
    this_update: datetime
    next_update: datetime | None
    revoked: dict[int, datetime] = field(hash=False, compare=False)
    #: CRLReason of the entries that carry one (RFC 5280 5.3.1).
    reasons: dict[int, int] = field(default_factory=dict, hash=False, compare=False)


def parse_crl(der: bytes) -> Crl:
    certificate_list = seq(parse_single(der), "CertificateList", 3)
    fields = seq(certificate_list[0], "TBSCertList", 3)
    index = 1 if fields[0].has_tag(INTEGER) else 0  # optional version
    issuer = expect(fields[index + 1], SEQUENCE, "issuer").raw
    this_update = to_time(fields[index + 2])
    rest = fields[index + 3 :]
    next_update = None
    if rest and (rest[0].has_tag(UTC_TIME) or rest[0].has_tag(GENERALIZED_TIME)):
        next_update = to_time(rest[0])
        rest = rest[1:]
    revoked: dict[int, datetime] = {}
    reasons: dict[int, int] = {}
    if rest and rest[0].has_tag(SEQUENCE):
        for entry in rest[0].children():
            parts = seq(entry, "revokedCertificate", 2)
            serial = to_int(parts[0])
            revoked[serial] = to_time(parts[1])
            for extension in seq(parts[2], "crlEntryExtensions") if len(parts) > 2 else []:
                ext = seq(extension, "Extension", 2)
                if to_oid(ext[0]) == EXT_CRL_REASON:
                    reason = expect(parse_single(to_octets(ext[-1])), 10, "CRLReason")
                    reasons[serial] = int.from_bytes(reason.value, "big")
    return Crl(
        tbs=certificate_list[0].raw,
        algorithm=to_algorithm(certificate_list[1]),
        signature=to_bits(certificate_list[2]),
        issuer=issuer,
        this_update=this_update,
        next_update=next_update,
        revoked=revoked,
        reasons=reasons,
    )


# =================================================================================================
# 5. RFC 3161 time-stamp tokens (CMS SignedData with TSTInfo)
# =================================================================================================


@dataclass
class TimestampResult:
    """Everything a token says, the chain that was found and every failed check."""

    errors: list[CheckError] = field(default_factory=list)
    #: Deviations the site accepted and sealed (a non-critical EKU, ``require_critical_eku``).
    warnings: list[CheckError] = field(default_factory=list)
    container: str = ""
    gen_time: datetime | None = None
    serial: int | None = None
    policy: str = ""
    imprint_digest: str = ""
    imprint: bytes = b""
    nonce: int | None = None
    accuracy: str = ""
    tsa_name: str = ""
    signer: Certificate | None = None
    signature: str = ""
    chain: list[Certificate] = field(default_factory=list)
    certificates: list[Certificate] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def serial_hex(self) -> str:
        return format(self.serial, "X") if self.serial is not None else ""

    @property
    def root(self) -> Certificate | None:
        return self.chain[-1] if self.chain and not self.errors else None

    def fail(self, code: str, tr: str, en: str) -> None:
        self.errors.append(CheckError(code, tr, en))


def _token_content(token: bytes) -> tuple[Tlv, str]:
    """The ContentInfo of a ``.zd`` file, which is a TimeStampResp or a bare TimeStampToken."""
    top = parse_single(token)
    parts = seq(top, "TimeStampResp / ContentInfo", 1)
    if not parts[0].has_tag(SEQUENCE):  # a ContentInfo starts with its content type OID
        return top, "TimeStampToken"
    status_info = seq(parts[0], "PKIStatusInfo", 1)  # TimeStampResp starts with the status
    status = to_int(status_info[0])
    if status not in (0, 1):  # granted, grantedWithMods
        texts = []
        if len(status_info) > 1 and status_info[1].has_tag(SEQUENCE):
            texts = [to_text(text) for text in status_info[1].children()]
        detail = "; ".join(texts)
        raise CheckError(
            "token-status",
            f"Zaman damgası sunucusu isteği reddetmiş (durum {status}): {detail}",
            f"the time-stamp server rejected the request (status {status}): {detail}",
        )
    if len(parts) < 2:
        raise DerError("granted response without a token")
    return parts[1], "TimeStampResp"


def _attributes(signed_attrs: Tlv) -> dict[str, list[Tlv]]:
    attributes: dict[str, list[Tlv]] = {}
    for attribute in signed_attrs.children():
        parts = seq(attribute, "Attribute", 2)
        oid = to_oid(parts[0])
        if oid in attributes:
            raise DerError(f"signed attribute {oid} appears twice")
        attributes[oid] = expect(parts[1], SET, "AttributeValues").children()
    return attributes


def _find_signer(certificates: Sequence[Certificate], sid: Tlv) -> Certificate | None:
    """The certificate named by the SignerIdentifier (issuer + serial, or key identifier)."""
    if sid.has_tag(SEQUENCE):
        issuer_node, serial_node = seq(sid, "IssuerAndSerialNumber", 2)[:2]
        serial = to_int(serial_node)
        for certificate in certificates:
            if certificate.issuer == issuer_node.raw and certificate.serial == serial:
                return certificate
    elif sid.has_tag(0, CONTEXT):
        for certificate in certificates:
            if certificate.key_identifier() == sid.value:
                return certificate
    return None


def _check_ess(attributes: dict[str, list[Tlv]], signer: Certificate) -> CheckError | None:
    """ESSCertIDv2 (RFC 5816) or ESSCertID (RFC 2634) must name the signing certificate.

    This binds the signature to one certificate, so the certificate cannot be swapped for
    another one with the same key.
    """
    for oid, default_digest in (
        (ATTR_SIGNING_CERTIFICATE_V2, "sha256"),
        (ATTR_SIGNING_CERTIFICATE, "sha1"),
    ):
        values = attributes.get(oid)
        if not values:
            continue
        certs = seq(seq(values[0], "SigningCertificate", 1)[0], "certs", 1)
        parts = seq(certs[0], "ESSCertID", 1)
        digest, index = default_digest, 0
        if oid == ATTR_SIGNING_CERTIFICATE_V2 and parts[0].has_tag(SEQUENCE):
            digest, index = hash_name(to_algorithm(parts[0])), 1
        if to_octets(parts[index]) != hashlib.new(digest, signer.der).digest():
            return CheckError(
                "token-ess",
                "İmza özniteliğindeki sertifika özeti (ESSCertID) TSA sertifikasıyla uyuşmuyor",
                "ESSCertID/ESSCertIDv2 does not match the TSA certificate",
            )
        if len(parts) > index + 1:  # optional IssuerSerial
            issuer_serial = seq(parts[index + 1], "IssuerSerial", 2)
            if to_int(issuer_serial[1]) != signer.serial:
                return CheckError(
                    "token-ess",
                    "ESSCertID içindeki seri numarası TSA sertifikasıyla uyuşmuyor",
                    "ESSCertID issuerSerial does not match the TSA certificate",
                )
        return None
    return CheckError(
        "token-ess",
        "İmzalı özniteliklerde sertifika bağı (ESSCertID/ESSCertIDv2) yok",
        "no signing-certificate attribute (ESSCertID/ESSCertIDv2)",
    )


def _check_eku(signer: Certificate, require_critical: bool = True) -> list[CheckError]:
    """RFC 3161 2.3: the TSA certificate's only key purpose is id-kp-timeStamping, critical.

    With ``require_critical`` false a non-critical extension is returned with the code
    ``token-eku-not-critical`` (the caller reports it as a warning).
    """
    extension = signer.extensions.get(EXT_EXTENDED_KEY_USAGE)
    if extension is None:
        return [
            CheckError(
                "token-eku",
                "TSA sertifikasında genişletilmiş anahtar kullanımı (EKU) yok",
                "the TSA certificate has no extended key usage",
            )
        ]
    errors = []
    purposes = [to_oid(node) for node in seq(parse_single(extension.value), "ExtKeyUsage")]
    if purposes != [KP_TIME_STAMPING]:
        errors.append(
            CheckError(
                "token-eku",
                "TSA sertifikasının anahtar kullanımı yalnızca zaman damgası (id-kp-timeStamping) "
                "değil",
                "the TSA certificate's extended key usage is not exactly id-kp-timeStamping",
            )
        )
    if not extension.critical:
        errors.append(
            CheckError(
                "token-eku" if require_critical else "token-eku-not-critical",
                "TSA sertifikasının anahtar kullanımı uzantısı kritik değil (RFC 3161 2.3)",
                "the TSA certificate's extended key usage is not critical (RFC 3161 2.3)",
            )
        )
    return errors


def _format_time(moment: datetime) -> str:
    """``2026-10-12 21:10:05 UTC (13.10.2026 00:10:05 TSİ)`` — TSİ is Turkish time, UTC+3."""
    utc = moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    local = moment.astimezone(TURKEY).strftime("%d.%m.%Y %H:%M:%S")
    return f"{utc} ({local} TSİ)"


def build_chain(
    signer: Certificate,
    pool: Sequence[Certificate],
    roots: Sequence[Certificate],
    moment: datetime,
) -> tuple[list[Certificate], CheckError | None]:
    """Walk from the TSA certificate to one of ``roots``.

    Each step: the certificate must be valid at the stamp time (``moment``); an issuer is a
    certificate whose subject is this certificate's issuer and whose key verifies its signature;
    the issuer must be a CA that may sign certificates (:func:`_issuer_problem`). The walk ends
    at a certificate that is byte-for-byte one of the roots.
    """
    root_ders = {root.der for root in roots}
    candidates = [*pool, *roots]
    chain = [signer]
    current = signer
    for _ in range(MAX_CHAIN_DEPTH):
        if not current.valid_at(moment):
            return chain, CheckError(
                "token-cert-validity",
                f"Sertifika zaman damgası anında geçerli değil: {current.subject_text} "
                f"(geçerlilik {_format_time(current.not_before)} - "
                f"{_format_time(current.not_after)})",
                f"certificate not valid at genTime: {current.subject_text}",
            )
        if current.der in root_ders:
            return chain, None
        issuer = None
        for candidate in candidates:
            if candidate.subject != current.issuer or candidate.der == current.der:
                continue
            try:
                verify_signature(candidate.key, current.algorithm, current.tbs, current.signature)
            except (CheckError, ValueError, IndexError):  # DerError is a ValueError
                continue
            issuer = candidate
            break
        if issuer is None:
            return chain, CheckError(
                "token-chain",
                f"Sertifika zinciri güvenilen kök sertifikalardan hiçbirine ulaşmıyor "
                f"(son sertifika: {current.subject_text}; veren: {current.issuer_text})",
                "the certificate chain does not reach a trusted root certificate",
            )
        problem = _issuer_problem(issuer, len(chain) - 1)
        if problem is not None:
            return chain, problem
        chain.append(issuer)
        current = issuer
    return chain, CheckError(
        "token-chain", "Sertifika zinciri çok uzun", "certificate chain too long"
    )


def _issuer_problem(issuer: Certificate, below: int) -> CheckError | None:
    """RFC 5280 6.1.4: an issuer is a CA (basicConstraints cA=TRUE), may sign certificates
    (keyUsage keyCertSign, when it has a keyUsage extension) and allows the ``below``
    intermediate CA certificates between it and the TSA certificate (pathLenConstraint)."""
    name = issuer.subject_text
    try:
        is_ca, usage, limit = issuer.is_ca(), issuer.key_usage(), issuer.path_length()
    except (ValueError, IndexError):
        is_ca, usage, limit = None, None, None
    if is_ca is not True:
        return CheckError(
            "token-not-ca",
            f"Zincirdeki veren sertifika bir sertifika otoritesi (CA) değil: {name}",
            f"issuer {name} is not a CA",
        )
    if usage is not None and KU_KEY_CERT_SIGN not in usage:
        return CheckError(
            "token-key-usage",
            f"Zincirdeki veren sertifikanın anahtar kullanımı sertifika imzalamaya izin vermiyor "
            f"(keyCertSign yok): {name}",
            f"issuer {name} has no keyCertSign key usage",
        )
    if limit is not None and below > limit:
        return CheckError(
            "token-path-length",
            f"Zincir, {name} sertifikasının izin verdiğinden (pathLenConstraint {limit}) daha "
            f"fazla ara sertifika içeriyor ({below})",
            f"the chain exceeds the pathLenConstraint of {name}",
        )
    return None


def _check_key_usage(signer: Certificate) -> list[CheckError]:
    """RFC 5280 4.2.1.3: when the TSA certificate has a keyUsage extension, it must allow
    signing (digitalSignature and/or nonRepudiation)."""
    try:
        usage = signer.key_usage()
    except (ValueError, IndexError):
        usage = set()
    if usage is None or usage & {KU_DIGITAL_SIGNATURE, KU_NON_REPUDIATION}:
        return []
    return [
        CheckError(
            "token-key-usage",
            "TSA sertifikasının anahtar kullanımı imzalamaya izin vermiyor "
            "(digitalSignature / nonRepudiation yok)",
            "the TSA certificate's key usage does not allow signing",
        )
    ]


@dataclass(frozen=True)
class _SignedToken:
    """The parts of the CMS SignedData that the checks need."""

    tst_der: bytes
    sid: Tlv
    digest_algorithm: Algorithm
    signed_attrs: Tlv | None
    signature_algorithm: Algorithm
    signature: bytes


def _parse_token(token: bytes, result: TimestampResult) -> _SignedToken:
    """Decode the token into ``result`` (what it says) and return what is to be checked."""
    content_info, result.container = _token_content(token)
    content = seq(content_info, "ContentInfo", 2)
    if to_oid(content[0]) != SIGNED_DATA:
        raise DerError("not a CMS SignedData")
    signed = seq(expect(content[1], 0, "content", CONTEXT).children()[0], "SignedData", 4)
    encapsulated = seq(signed[2], "EncapsulatedContentInfo", 2)
    if to_oid(encapsulated[0]) != TST_INFO:
        raise DerError("the token does not contain a TSTInfo")
    tst_der = to_octets(expect(encapsulated[1], 0, "eContent", CONTEXT).children()[0])
    _read_tst_info(tst_der, result)
    for item in signed[3:-1]:
        if item.has_tag(0, CONTEXT):  # certificates [0] IMPLICIT CertificateSet
            result.certificates = [
                parse_certificate(choice.raw)
                for choice in item.children()
                if choice.has_tag(SEQUENCE)
            ]
    signer_infos = expect(signed[-1], SET, "SignerInfos").children()
    if len(signer_infos) != 1:
        raise CheckError(
            "token-signers",
            f"Zaman damgasında tam olarak bir imzacı olmalı, {len(signer_infos)} var",
            "the token must have exactly one signer",
        )
    info = seq(signer_infos[0], "SignerInfo", 5)
    signed_attrs = info[3] if info[3].has_tag(0, CONTEXT) else None
    rest = info[4:] if signed_attrs is not None else info[3:]
    return _SignedToken(
        tst_der=tst_der,
        sid=info[1],
        digest_algorithm=to_algorithm(info[2]),
        signed_attrs=signed_attrs,
        signature_algorithm=to_algorithm(rest[0]),
        signature=to_octets(rest[1]),
    )


def verify_timestamp(
    token: bytes,
    data: bytes,
    roots: Sequence[Certificate],
    *,
    now: datetime | None = None,
    require_critical_eku: bool = True,
) -> TimestampResult:
    """Verify an RFC 3161 token over ``data`` (the exact bytes that were stamped).

    The same rules as ARCILOGGER's own verifier (``arcilogger_core.tsa.verify``), so both
    reach the same verdict on the same token. ``require_critical_eku`` false — only when the
    sealed manifest says the site accepted Kamu SM's profile (``tsa.require_critical_eku``) —
    turns a non-critical EKU extension into a warning (``result.warnings``).
    """
    result = TimestampResult()
    try:
        parts = _parse_token(token, result)
        signer = _find_signer(result.certificates, parts.sid)
    except CheckError as exc:
        result.errors.append(exc)
        return result
    except (ValueError, IndexError, RecursionError) as exc:  # DerError is a ValueError
        result.fail(
            "token-format",
            f"Zaman damgası dosyası çözümlenemedi (bozuk ya da zaman damgası değil): {exc}",
            f"cannot parse the time-stamp token: {exc}",
        )
        return result
    gen_time = result.gen_time or datetime.now(UTC)  # always set once the token parsed
    result.signer = signer

    # 1. The imprint: the hash in the token must be the hash of the stamped file, made with a
    #    hash function that is still collision resistant.
    if result.imprint_digest not in HASH_OID_BY_NAME:
        result.fail(
            "algorithm",
            f"Desteklenmeyen özet algoritması {result.imprint_digest}",
            f"unsupported imprint algorithm {result.imprint_digest}",
        )
    elif result.imprint_digest not in STRONG_DIGESTS:
        result.fail(*_weak_digest("damgalanan özet (messageImprint)", result.imprint_digest))
    elif hashlib.new(result.imprint_digest, data).digest() != result.imprint:
        result.fail(
            "token-imprint",
            "Zaman damgasındaki özet, damgalanan dosyanın özetiyle uyuşmuyor "
            "(dosya değiştirilmiş ya da damga başka bir dosyaya ait)",
            "the message imprint does not match the file (file changed or wrong token)",
        )
    if signer is None:
        result.fail(
            "token-no-cert",
            "TSA sertifikası zaman damgasının içinde yok (certReq)",
            "the TSA certificate is not included in the token (certReq)",
        )
        return result

    try:
        # 2. Signed attributes: content type, digest of the TSTInfo, signature, ESS binding.
        _check_signed_attributes(result, signer, parts)
        # 3. The TSA certificate's purpose and its chain to a trusted root at genTime.
        for problem in _check_eku(signer, require_critical_eku):
            target = result.warnings if problem.code == "token-eku-not-critical" else result.errors
            target.append(problem)
        result.errors.extend(_check_key_usage(signer))
        result.chain, chain_error = build_chain(signer, result.certificates, roots, gen_time)
        if chain_error is not None:
            result.errors.append(chain_error)
    except (ValueError, IndexError, RecursionError) as exc:
        result.fail(
            "token-format",
            f"Zaman damgasının imza bölümü çözümlenemedi: {exc}",
            f"cannot parse the signature part of the token: {exc}",
        )

    # 4. A time stamp from the future means a wrong clock or a forged token.
    if gen_time > (now or datetime.now(UTC)) + CLOCK_SKEW:
        result.fail(
            "token-future",
            f"Zaman damgasının zamanı gelecekte: {_format_time(gen_time)}",
            "genTime is in the future",
        )
    return result


def _weak_digest(what: str, name: str) -> tuple[str, str, str]:
    return (
        "token-weak-digest",
        f"Zaman damgasında {what} için zayıf özet algoritması kullanılmış: {name} (en az SHA-256 "
        "gerekir)",
        f"weak digest algorithm {name} for {what} (SHA-256 or stronger is required)",
    )


def _read_tst_info(tst_der: bytes, result: TimestampResult) -> None:
    """TSTInfo (RFC 3161 2.4.2): policy, imprint, serial number, genTime and the optional rest."""
    fields = seq(parse_single(tst_der), "TSTInfo", 5)
    result.policy = to_oid(fields[1])
    imprint = seq(fields[2], "MessageImprint", 2)
    imprint_oid = to_algorithm(imprint[0]).oid
    result.imprint_digest = HASH_OIDS.get(imprint_oid, imprint_oid)
    result.imprint = to_octets(imprint[1])
    result.serial = to_int(fields[3])
    result.gen_time = to_time(expect(fields[4], GENERALIZED_TIME, "genTime"))
    for item in fields[5:]:
        if item.has_tag(SEQUENCE):  # accuracy: seconds, [0] millis, [1] micros
            amounts = []
            for part in item.children():
                if part.has_tag(INTEGER):
                    amounts.append(f"{to_int(part)} s")
                else:
                    unit = "ms" if part.has_tag(0, CONTEXT) else "µs"
                    number = int.from_bytes(part.value, "big")
                    amounts.append(f"{number} {unit}")
            result.accuracy = " ".join(amounts)
        elif item.has_tag(INTEGER):
            result.nonce = to_int(item)
        elif item.has_tag(0, CONTEXT):  # tsa [0] GeneralName; directoryName is [4]
            name = item.children()[0]
            if name.has_tag(4, CONTEXT):
                result.tsa_name = name_text(name.children()[0].raw)


def _check_signed_attributes(
    result: TimestampResult, signer: Certificate, parts: _SignedToken
) -> None:
    signed_attrs = parts.signed_attrs
    if signed_attrs is None:
        result.fail("token-signed-attrs", "İmzalı öznitelikler yok", "signed attributes missing")
        return
    attributes = _attributes(signed_attrs)
    content_type = attributes.get(ATTR_CONTENT_TYPE)
    if not content_type or to_oid(content_type[0]) != TST_INFO:
        result.fail(
            "token-content-type",
            "content-type özniteliği id-ct-TSTInfo değil",
            "content-type attribute is not id-ct-TSTInfo",
        )
    try:
        digest = hash_name(parts.digest_algorithm)
        signature_digest = (
            _pss_parameters(parts.signature_algorithm.params)[0]
            if parts.signature_algorithm.oid == RSASSA_PSS
            else digest
        )
    except CheckError as exc:
        result.errors.append(exc)
        return
    for name in sorted({digest, signature_digest} - STRONG_DIGESTS):
        result.fail(*_weak_digest("imza (signerInfo)", name))
    message_digest = attributes.get(ATTR_MESSAGE_DIGEST)
    expected = hashlib.new(digest, parts.tst_der).digest()
    if not message_digest or to_octets(message_digest[0]) != expected:
        result.fail(
            "token-message-digest",
            "message-digest özniteliği TSTInfo özetiyle uyuşmuyor",
            "message-digest attribute does not match the TSTInfo",
        )
    # RFC 5652 5.4: the signature covers the DER encoding of the attributes as a SET OF.
    try:
        result.signature = verify_signature(
            signer.key,
            parts.signature_algorithm,
            encode(0x31, signed_attrs.value),
            parts.signature,
            digest_hint=digest,
        )
    except CheckError as exc:
        result.errors.append(CheckError("token-signature", exc.tr, exc.en))
    ess_error = _check_ess(attributes, signer)
    if ess_error is not None:
        result.errors.append(ess_error)


def check_revocation(
    chain: Sequence[Certificate], crls: Sequence[Crl], moment: datetime
) -> list[tuple[str, str, str, str]]:
    """Look up each certificate of the chain (except the root) in the package's CRLs.

    Returns ``(level, code, tr, en)`` tuples. A CRL is used only if its signature verifies with
    the key of the certificate's issuer from the chain. Every usable CRL is consulted (a CA may
    drop an expired certificate from later lists): an entry dated at or before the stamp time
    (``moment``) is a failure, a later one a warning, an entry on hold (certificateHold) a
    warning. "Not revoked" is only reported when a usable CRL was issued after the stamp time;
    an older one cannot know about revocations between its date and the stamp. Online checking
    is not done.
    """
    findings = []
    for position, certificate in enumerate(chain[:-1]):
        issuer = chain[position + 1]
        usable = []
        for crl in crls:
            if crl.issuer != certificate.issuer:
                continue
            try:
                verify_signature(issuer.key, crl.algorithm, crl.tbs, crl.signature)
            except (CheckError, ValueError, IndexError):  # DerError is a ValueError
                continue
            usable.append(crl)
        name = certificate.subject_text
        if not usable:
            findings.append(
                (
                    "info",
                    "crl-none",
                    f"{name}: bu sertifikayı kapsayan SİL pakette yok; iptal durumu bilinmiyor",
                    f"{name}: no CRL for this certificate in the package; revocation unknown",
                )
            )
            continue
        entries = sorted(
            (crl.revoked[certificate.serial], crl.reasons.get(certificate.serial))
            for crl in usable
            if certificate.serial in crl.revoked
            and crl.reasons.get(certificate.serial) != CRL_REASON_REMOVE
        )
        latest = max(usable, key=lambda crl: crl.this_update)
        if entries:
            findings.append(_revocation_finding(name, *entries[0], moment))
        elif latest.this_update >= moment:
            findings.append(
                (
                    "ok",
                    "crl-ok",
                    f"{name}: SİL'de iptal kaydı yok "
                    f"(SİL tarihi {_format_time(latest.this_update)})",
                    f"{name}: not revoked according to the CRL of {latest.this_update.isoformat()}",
                )
            )
        else:
            findings.append(
                (
                    "info",
                    "crl-stale",
                    f"{name}: paketteki en yeni SİL ({_format_time(latest.this_update)}) zaman "
                    "damgasından önce yayımlanmış; damga anındaki iptal durumu bilinmiyor",
                    f"{name}: the newest CRL predates the time stamp; revocation then unknown",
                )
            )
    return findings


def _revocation_finding(
    name: str, revoked_at: datetime, reason: int | None, moment: datetime
) -> tuple[str, str, str, str]:
    if reason == CRL_REASON_HOLD:
        return (
            "warn",
            "crl-hold",
            f"{name}: sertifika SİL'de askıya alınmış olarak görünüyor ({_format_time(revoked_at)}"
            "); askının zaman damgası anını kapsayıp kapsamadığı incelenmeli",
            f"{name}: certificate on hold since {revoked_at.isoformat()}",
        )
    if revoked_at <= moment:
        return (
            "fail",
            "crl-revoked",
            f"{name}: sertifika zaman damgasından ÖNCE iptal edilmiş ({_format_time(revoked_at)})",
            f"{name}: revoked before the time stamp ({revoked_at.isoformat()})",
        )
    return (
        "warn",
        "crl-revoked-later",
        f"{name}: sertifika zaman damgasından sonra iptal edilmiş "
        f"({_format_time(revoked_at)}); iptal nedeninin incelenmesi gerekir",
        f"{name}: revoked after the time stamp ({revoked_at.isoformat()})",
    )


# =================================================================================================
# 6. JSON canonical form (RFC 8785) — paket.json and the manifests are written this way
# =================================================================================================


def _canonical(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True or value is False:
        out.append("true" if value else "false")
    elif isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValueError("integer outside the safe range")
        out.append(str(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, dict):
        out.append("{")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be", "surrogatepass"))
        for position, key in enumerate(keys):
            out.append(("," if position else "") + json.dumps(key, ensure_ascii=False) + ":")
            _canonical(value[key], out)
        out.append("}")
    elif isinstance(value, list):
        out.append("[")
        for position, item in enumerate(value):
            if position:
                out.append(",")
            _canonical(item, out)
        out.append("]")
    else:
        raise ValueError("not representable (ARCILOGGER documents contain no floats)")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"the key {key!r} appears twice in one object")
        result[key] = value
    return result


def load_json(data: bytes) -> Any:
    """Parse a JSON document. A key that appears twice in one object is an error: ``json.loads``
    keeps the last one and another program might keep the first, so the two would read the same
    signed bytes differently. Raises ValueError (or RecursionError for absurd nesting)."""
    return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)


def is_canonical(data: bytes) -> bool:
    """True if ``data`` is exactly the RFC 8785 form of the JSON it contains."""
    try:
        out: list[str] = []
        _canonical(load_json(data), out)
        return "".join(out).encode("utf-8") == data
    except (ValueError, RecursionError):  # UnicodeError is a ValueError
        return False


# =================================================================================================
# 7. Reading the package (a folder or an unencrypted ZIP file) — never writing
# =================================================================================================


class TooLargeError(Exception):
    """A file (or a raw line) is larger than this program is willing to hold in memory."""


#: What reading a file can raise: OS errors, a bad ZIP entry (CRC), a corrupt gzip stream, a
#: file above the size limits.
READ_ERRORS = (OSError, EOFError, zipfile.BadZipFile, zlib.error, TooLargeError)


class PackageError(Exception):
    """The package cannot be read at all (exit code 2)."""

    def __init__(self, tr: str, en: str) -> None:
        super().__init__(en)
        self.tr = tr
        self.en = en


def safe_name(name: str) -> bool:
    """A relative POSIX path that stays inside the package (no ``..``, no drive, no ``\\``)."""
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


#: Names Windows cannot create, with any extension: an archive tool renames or skips them.
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }
)
_RESERVED_CHARACTERS = frozenset('<>:"|?*\\')


def name_problem(name: str) -> str | None:
    """Why a file name in a package is unsafe, or None.

    Production and most auditors use Windows. A name that Windows cannot store as it is (``..``,
    a drive, a trailing dot or space, a device name such as ``CON``, a control character) would
    be changed or skipped when the package is unpacked there, so the files a reader sees would
    not be the files this program checked.
    """
    if not safe_name(name):
        return "not a relative path inside the package"
    for part in name.split("/"):
        if any(ord(char) < 0x20 or char == "\x7f" or char in _RESERVED_CHARACTERS for char in part):
            return "control or reserved character"
        if part.endswith((".", " ")):
            return "a name ending in a dot or a space"
        if part.split(".")[0].rstrip(" ").upper() in _WINDOWS_RESERVED:
            return "a reserved device name on Windows"
    return None


def name_key(name: str) -> str:
    """The name as Windows compares it: case-insensitive and Unicode-normalised (NFC)."""
    return unicodedata.normalize("NFC", name).casefold()


def check_names(names: Sequence[str], folder_entries: Sequence[str] = ()) -> None:
    """Every name must be safe (:func:`name_problem`) and distinct as Windows sees names.

    Two entries that differ only in case (``extract/x.txt`` and ``EXTRACT/X.TXT``) are one file
    on Windows: unpacking the package there would overwrite the checked file with the other one.
    ``folder_entries`` are the folder entries of a ZIP (names only checked for safety). Raises
    :class:`PackageError` (exit code 2): such a package cannot be verified safely.
    """
    seen: dict[str, str] = {}
    folders: set[str] = set()
    for name in [*folder_entries, *names]:
        problem = name_problem(name)
        if problem is not None:
            raise PackageError(
                f"Pakette güvenli olmayan dosya adı var: {name!r} ({problem}); paket "
                "değiştirilmiş olabilir",
                f"unsafe file name in the package: {name!r} ({problem})",
            )
        if name in folder_entries:
            continue
        key = name_key(name)
        if key in seen:
            raise PackageError(
                f"Pakette aynı adla (büyük/küçük harf farkı dahil) birden çok dosya var: "
                f"{seen[key]!r} ve {name!r}; paket değiştirilmiş olabilir",
                f"two files with the same name (ignoring case): {seen[key]!r} and {name!r}",
            )
        seen[key] = name
        parts = key.split("/")
        folders.update("/".join(parts[:end]) for end in range(1, len(parts)))
    for key in sorted(set(seen) & folders):
        raise PackageError(
            f"Pakette {seen[key]!r} hem dosya hem klasör adı olarak geçiyor",
            f"{seen[key]!r} is both a file and a folder",
        )


class Package:
    """Read-only access to the files of a package by their relative POSIX names."""

    label = ""

    def names(self) -> list[str]:
        raise NotImplementedError

    def open(self, name: str) -> IO[bytes]:
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        """A whole (small) file: index, manifest, token, certificate, CRL, extract, record."""
        with self.open(name) as handle:
            data = handle.read(MAX_READ + 1)
        if len(data) > MAX_READ:
            raise TooLargeError(f"larger than {MAX_READ} bytes")
        return data

    def close(self) -> None:
        return None

    def __enter__(self) -> Package:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FolderPackage(Package):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.label = str(root)
        found = []
        for folder, _, files in os.walk(self.root):
            for file in files:
                found.append((Path(folder) / file).relative_to(self.root).as_posix())
        check_names(found)
        self._names = sorted(found)

    def names(self) -> list[str]:
        return list(self._names)

    def open(self, name: str) -> IO[bytes]:
        if not safe_name(name):
            raise OSError(f"invalid file name: {name!r}")
        return self.root.joinpath(*name.split("/")).open("rb")


class ZipPackage(Package):
    def __init__(self, path: Path) -> None:
        self.label = str(path)
        try:
            self.archive = zipfile.ZipFile(path)
        except READ_ERRORS as exc:
            raise PackageError(f"ZIP açılamadı: {exc}", f"cannot open the ZIP: {exc}") from exc
        infos = [info for info in self.archive.infolist() if not info.is_dir()]
        try:
            if any(info.flag_bits & 0x1 for info in infos):
                raise PackageError(
                    "Paket parolayla şifrelenmiş (AES-256). Önce parolayı girerek açın (örneğin "
                    "7-Zip ile), sonra açılan klasörü doğrulayın: python -I -S verify.py KLASÖR",
                    "the package is encrypted (AES-256): extract it with its password first "
                    "(e.g. with 7-Zip), then verify the extracted folder",
                )
            # Every entry name, folders included, is checked before anything is read: what
            # this program verifies must be what an archive tool unpacks.
            check_names(
                [info.filename for info in infos],
                [info.filename.rstrip("/") for info in self.archive.infolist() if info.is_dir()],
            )
        except PackageError:
            self.archive.close()
            raise
        self._infos = {info.filename: info for info in infos}

    def names(self) -> list[str]:
        return sorted(self._infos)

    def open(self, name: str) -> IO[bytes]:
        return self.archive.open(self._infos[name])

    def close(self) -> None:
        self.archive.close()


def open_package(target: Path) -> Package:
    if target.is_dir():
        return FolderPackage(target)
    if target.is_file() and zipfile.is_zipfile(target):
        return ZipPackage(target)
    raise PackageError(
        f"Paket bulunamadı (klasör ya da ZIP olmalı): {target}",
        f"no package here (expected a folder or a ZIP file): {target}",
    )


def hash_stream(handle: IO[bytes]) -> tuple[str, int]:
    """SHA-256 (hex) and size of everything ``handle`` yields, read in 1 MiB chunks."""
    hasher = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(CHUNK), b""):
        hasher.update(chunk)
        size += len(chunk)
    return hasher.hexdigest(), size


def gunzip_digest(handle: IO[bytes]) -> tuple[str, int]:
    """SHA-256 and line count of the uncompressed content of a gzip stream.

    Every ``\\n`` ends a line; a non-empty last line without ``\\n`` (a write torn by a crash
    or a full disk, sealed as it was and marked ``unterminated_last_line`` in the manifest)
    counts as a line too — the rule ARCILOGGER uses when it seals.
    """
    hasher = hashlib.sha256()
    lines = 0
    last = b""
    with gzip.GzipFile(fileobj=handle, mode="rb") as unzipped:
        for chunk in iter(lambda: unzipped.read(CHUNK), b""):
            hasher.update(chunk)
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    if last not in (b"", b"\n"):
        lines += 1
    return hasher.hexdigest(), lines


# =================================================================================================
# 8. Findings and the report
# =================================================================================================

LEVEL_LABELS = {"ok": "TAMAM", "info": "BİLGİ", "warn": "UYARI", "fail": "HATA"}


@dataclass(frozen=True)
class Finding:
    level: str  # "ok" | "info" | "warn" | "fail"
    code: str
    where: str
    tr: str
    en: str

    def as_json(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "where": self.where,
            "tr": self.tr,
            "en": self.en,
        }


class Report:
    """Collects findings; ``echo`` prints each one as soon as it is found."""

    def __init__(self, echo: Callable[[str], None] | None = None) -> None:
        self.findings: list[Finding] = []
        self.echo = echo
        self.days: list[dict[str, Any]] = []
        self.roots: list[dict[str, str]] = []
        self.index_sha256: str | None = None
        self.mode = ""

    def section(self, tr: str, en: str) -> None:
        if self.echo is not None:
            self.echo("")
            self.echo(f"== {tr} [{en}] ==")

    def add(self, level: str, code: str, where: str, tr: str, en: str) -> None:
        self.findings.append(Finding(level, code, where, tr, en))
        if self.echo is not None:
            label = "[" + LEVEL_LABELS[level] + "]"
            self.echo(f"  {label:8} {tr} [{en}]")

    def ok(self, code: str, where: str, tr: str, en: str) -> None:
        self.add("ok", code, where, tr, en)

    def info(self, code: str, where: str, tr: str, en: str) -> None:
        self.add("info", code, where, tr, en)

    def warn(self, code: str, where: str, tr: str, en: str) -> None:
        self.add("warn", code, where, tr, en)

    def fail(self, code: str, where: str, tr: str, en: str) -> None:
        self.add("fail", code, where, tr, en)

    def failed(self, error: CheckError, where: str, prefix: str = "") -> None:
        self.fail(error.code, where, prefix + error.tr, prefix + error.en)

    def count(self, level: str) -> int:
        return sum(1 for finding in self.findings if finding.level == level)

    @property
    def passed(self) -> bool:
        return self.count("fail") == 0

    def codes(self, level: str = "fail") -> list[str]:
        return [finding.code for finding in self.findings if finding.level == level]

    def as_json(self, label: str) -> dict[str, Any]:
        return {
            "verifier": f"ARCILOGGER verify.py {VERIFIER_VERSION}",
            "package": label,
            "passed": self.passed,
            "mode": self.mode,
            "counts": {level: self.count(level) for level in LEVEL_LABELS},
            "index_sha256": self.index_sha256,
            "roots": self.roots,
            "days": self.days,
            "findings": [finding.as_json() for finding in self.findings],
        }


# =================================================================================================
# 9. The checks
# =================================================================================================

_MANIFEST = re.compile(
    r"^(?P<prefix>(?:vault/)?)(?P<y>\d{4})/(?P<m>\d\d)/(?P<d>\d\d)/"
    r"manifest-(?P<day>\d{4}-\d\d-\d\d)\.json$"
)
_EXTRACT = re.compile(r"^extract/(?P<day>\d{4}-\d\d-\d\d)/(?P<source>[a-z0-9][a-z0-9.-]*)\.txt$")
_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
#: A file directly in a sealed day folder (relative to the vault).
_DAY_FILE = re.compile(r"^\d{4}/\d\d/\d\d/[^/]+$")
#: What else an ARCILOGGER package contains besides the vault files and the extract.
_PACKAGE_FILE = re.compile(
    r"^(?:paket\.json|verify\.py|verify\.ps1|verify\.sh|DOGRULAMA\.txt|VERIFY-EN\.txt"
    r"|sertifikalar/kokler\.pem|sertifikalar/(?:kokler|tsa)/[A-Za-z0-9][A-Za-z0-9._-]*\.pem"
    r"|sil/[A-Za-z0-9][A-Za-z0-9._-]*|rapor/[A-Za-z0-9][A-Za-z0-9._-]*\.pdf(?:\.zd)?)$"
)
#: Records outside the day folders of a vault copy that need no manifest of their own.
_VAULT_RECORD = re.compile(r"^(?:deletions|renewals)/[^/]+\.json(?:\.zd)?$")
#: Files the operating system adds to a folder when someone opens or copies it (Finder's
#: .DS_Store, AppleDouble "._" files on USB sticks and network shares, Explorer's Thumbs.db and
#: desktop.ini). They are listed, never read, and do not make a package fail.
_OS_METADATA_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def os_metadata(name: str) -> bool:
    base = name.rpartition("/")[2]
    return (
        base.casefold() in _OS_METADATA_NAMES
        or base.startswith("._")
        or name.startswith("__MACOSX/")
    )


@dataclass
class DayState:
    """What the per-day checks learned, for the chain and extract checks that follow."""

    day: str
    manifest_name: str
    data: bytes | None = None
    manifest: dict[str, Any] | None = None
    manifest_sha256: str = ""
    token_sha256: str = ""
    verified_files: set[str] = field(default_factory=set)
    gen_time: datetime | None = None  # only when the token verified


def _entries(value: Any) -> list[dict[str, Any]]:
    """The objects of a JSON list (anything else in it is ignored)."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _object(value: Any) -> dict[str, Any]:
    """A JSON object, or an empty one for anything else."""
    return value if isinstance(value, dict) else {}


class Verifier:
    """Runs the checks of one package in order and writes findings into the report."""

    def __init__(
        self,
        package: Package,
        report: Report,
        *,
        roots_dir: Path | None,
        deep: bool,
        now: datetime,
    ) -> None:
        self.package = package
        self.report = report
        self.roots_dir = roots_dir
        self.deep = deep
        self.now = now
        names = package.names()
        #: Operating-system metadata files: reported, otherwise ignored (see os_metadata).
        self.os_files = [name for name in names if os_metadata(name)]
        self.names = [name for name in names if not os_metadata(name)]
        self.name_set = set(self.names)
        self.prefix = "vault/" if any(name.startswith("vault/") for name in self.names) else ""
        # An evidence package (a ZIP, a vault/ folder, an index or the verifier next to it) must
        # be complete and contain nothing else; a plain copy of the vault (e.g. the replica)
        # has no index, and only its sealed days are verified.
        self.package_mode = (
            isinstance(package, ZipPackage)
            or self.prefix == "vault/"
            or any(name in self.name_set for name in (INDEX_NAME, GUIDE_TR, *VERIFIER_NAMES))
        )
        report.mode = "package" if self.package_mode else "vault"
        #: Files a check has accounted for (sealed, a record, a stamp); see :meth:`check_contents`.
        self.covered: set[str] = set()
        #: Folders whose manifest (or chain anchor) could not be read: that failure is reported
        #: already, so their files are not reported once more as unexpected.
        self.broken_folders: set[str] = set()
        self.index: dict[str, Any] = {}
        self.roots: list[Certificate] = []
        self.crls: list[Crl] = []
        self.days: dict[str, DayState] = {}
        self.test_days: list[str] = []
        self._digests: dict[str, tuple[str, int]] = {}

    # -- reading helpers -----------------------------------------------------------------------

    def _read(self, name: str, where: str) -> bytes | None:
        """The file's bytes, or None after reporting why it could not be read."""
        if name not in self.name_set:
            self.report.fail("file-missing", where, f"Dosya yok: {name}", f"file missing: {name}")
            return None
        try:
            return self.package.read(name)
        except READ_ERRORS as exc:
            self.report.fail(
                "file-unreadable", where, f"{name}: okunamadı ({exc})", f"{name}: unreadable"
            )
            return None

    def _digest(self, name: str, where: str) -> tuple[str, int] | None:
        """SHA-256 and size of a package file, computed once (big log files are read once)."""
        if name not in self._digests:
            try:
                with self.package.open(name) as handle:
                    self._digests[name] = hash_stream(handle)
            except READ_ERRORS as exc:
                self.report.fail(
                    "file-unreadable", where, f"{name}: okunamadı ({exc})", f"{name}: unreadable"
                )
                return None
        return self._digests[name]

    # -- 1. the package index ------------------------------------------------------------------

    def check_index(self) -> None:
        """paket.json lists every file with size and SHA-256; it is canonical JSON (RFC 8785)."""
        report = self.report
        report.section("1. Paket dizini (paket.json)", "package index")
        if INDEX_NAME not in self.name_set:
            if self.package_mode:
                report.fail(
                    "index-missing",
                    "paket",
                    "paket.json yok: bir kanıt paketinde her zaman bulunur, silinmiş ya da paket "
                    "eksik. (Kasanın bir kopyasını doğruluyorsanız doğrudan kasa klasörünü verin.)",
                    "paket.json missing from an evidence package",
                )
            else:
                report.info(
                    "vault-copy",
                    "paket",
                    "Paket dizini yok: bu bir kasa kopyası olarak doğrulanıyor; yalnızca mühürlü "
                    "günler ve kayıtlar kontrol edilir",
                    "no index: verified as a copy of the vault (sealed days and records only)",
                )
                report.warn(
                    "index-missing",
                    "paket",
                    "paket.json yok: dosya listesi kontrol edilemiyor; yalnızca mühürlü günler "
                    "doğrulanıyor",
                    "paket.json missing: only the sealed days are verified",
                )
            return
        self.covered.add(INDEX_NAME)
        data = self._read(INDEX_NAME, "paket")
        if data is None:
            return
        report.index_sha256 = hashlib.sha256(data).hexdigest()
        try:
            index = load_json(data)
            if not isinstance(index, dict) or not isinstance(index.get("files"), list):
                raise ValueError("not an evidence package index")
        except (ValueError, RecursionError) as exc:
            report.fail(
                "index-invalid", "paket", f"paket.json okunamadı: {exc}", f"bad paket.json: {exc}"
            )
            return
        self.index = index
        if not is_canonical(data):
            report.fail(
                "index-not-canonical",
                "paket",
                "paket.json kanonik JSON değil (yazıldıktan sonra değiştirilmiş)",
                "paket.json is not canonical JSON (changed after it was written)",
            )
        if index.get("schema") != INDEX_SCHEMA:
            report.warn(
                "index-schema",
                "paket",
                f"Bilinmeyen paket biçimi: {index.get('schema')!r}",
                "unknown package schema",
            )
        report.info(
            "index-hash",
            "paket",
            f"paket.json SHA-256: {report.index_sha256} (ARCILOGGER denetim kaydındaki değerle "
            "karşılaştırılabilir)",
            "compare with the value in ARCILOGGER's audit log",
        )
        listed = {INDEX_NAME}
        good = 0
        for entry in index["files"]:
            name = str(entry.get("path", "")) if isinstance(entry, dict) else ""
            if not safe_name(name) or name in listed:
                report.fail(
                    "index-path-invalid", "paket", f"Geçersiz yol: {name!r}", "invalid path"
                )
                continue
            listed.add(name)
            if self._check_listed_file(name, entry):
                good += 1
        if good:
            report.ok(
                "index-files",
                "paket",
                f"{good} dosyanın boyutu ve SHA-256 değeri paket dizinindekiyle aynı",
                f"size and SHA-256 of {good} files match the index",
            )
        extra = [name for name in self.names if name not in listed]
        level = "fail" if self.package_mode else "warn"
        for name in extra[:MAX_LISTED]:
            report.add(
                level,
                "index-unlisted-file",
                "paket",
                f"Paket dizininde olmayan dosya (sonradan eklenmiş): {name}",
                f"file not listed in the index (added later): {name}",
            )
        if len(extra) > MAX_LISTED:
            report.add(
                level,
                "index-unlisted-file",
                "paket",
                f"… ve paket dizininde olmayan {len(extra) - MAX_LISTED} dosya daha",
                f"… and {len(extra) - MAX_LISTED} more unlisted files",
            )
        scope = _object(index.get("scope"))
        days = ", ".join(str(day) for day in scope.get("days") or [])
        sources = ", ".join(str(source) for source in scope.get("sources") or []) or "tümü"
        report.info(
            "index-scope",
            "paket",
            f"Paket {index.get('package_id')}: oluşturan {index.get('created_by') or '-'}, "
            f"zaman {index.get('created_at')}, günler {days}, kaynaklar {sources}, sorgu "
            f"{scope.get('lookup_id') or '-'}",
            "package scope as recorded by ARCILOGGER",
        )

    def _check_listed_file(self, name: str, entry: dict[str, Any]) -> bool:
        report = self.report
        if name not in self.name_set:
            report.fail(
                "index-file-missing", "paket", f"Dosya eksik: {name}", f"file missing: {name}"
            )
            return False
        digest = self._digest(name, "paket")
        if digest is None:
            return False
        if digest[1] != entry.get("bytes"):
            report.fail(
                "index-size-mismatch",
                "paket",
                f"{name}: boyut farklı (dizinde {entry.get('bytes')}, dosya {digest[1]} bayt)",
                f"{name}: size differs from the index",
            )
            return False
        if digest[0] != entry.get("sha256"):
            report.fail(
                "index-hash-mismatch",
                "paket",
                f"{name}: SHA-256 farklı (dizinde {entry.get('sha256')}, dosya {digest[0]})",
                f"{name}: SHA-256 differs from the index",
            )
            return False
        return True

    # -- 2. root certificates and CRLs ---------------------------------------------------------

    def load_roots(self) -> None:
        report = self.report
        report.section("2. Kök sertifikalar", "root certificates")
        sources: list[tuple[str, bytes]] = []
        if self.roots_dir is not None:
            report.info(
                "roots-external",
                "kökler",
                f"Yalnızca komut satırında verilen klasördeki köklere güveniliyor: "
                f"{self.roots_dir} (paketteki kökler kullanılmıyor)",
                "trusting only the roots from the given folder, not the package's",
            )
            for path in sorted(self.roots_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in (".pem", ".crt", ".cer", ".der"):
                    sources.append((str(path), path.read_bytes()))
        else:
            listed = [str(entry.get("path")) for entry in _entries(self.index.get("roots"))]
            names = listed or [
                name
                for name in self.names
                if name.startswith("sertifikalar/kokler/") and name.endswith(".pem")
            ]
            for name in names:
                data = self._read(name, "kökler")
                if data is not None:
                    sources.append((name, data))
        seen: set[bytes] = set()
        for label, data in sources:
            try:
                certificates = load_certificates(data)
            except (ValueError, IndexError, RecursionError) as exc:
                report.fail(
                    "root-invalid", "kökler", f"{label}: okunamadı ({exc})", f"{label}: unreadable"
                )
                continue
            for certificate in certificates:
                if certificate.der not in seen:
                    seen.add(certificate.der)
                    self._add_root(label, certificate)
        if not self.roots:
            report.fail(
                "root-none",
                "kökler",
                "Güvenilecek kök sertifika yok; zaman damgaları doğrulanamaz",
                "no trusted root certificate; time stamps cannot be verified",
            )
            return
        report.info(
            "root-trust",
            "kökler",
            "Bu program bir kökün gerçekten Kamu SM'ye ait olduğunu kanıtlayamaz: yukarıdaki "
            "parmak izlerini Kamu SM'nin yayımladığı değerlerle (sertifikalar.kamusm.gov.tr) "
            "karşılaştırın ya da kendi indirdiğiniz kökleri --kokler ile verin",
            "compare these fingerprints with Kamu SM's published values",
        )

    def _add_root(self, label: str, certificate: Certificate) -> None:
        self.roots.append(certificate)
        sha256, sha1 = certificate.fingerprint("sha256"), certificate.fingerprint("sha1")
        self.report.roots.append(
            {"path": label, "subject": certificate.subject_text, "sha256": sha256, "sha1": sha1}
        )
        self.report.info(
            "root",
            "kökler",
            f"{label}: {certificate.subject_text}; geçerlilik "
            f"{_format_time(certificate.not_before)} - {_format_time(certificate.not_after)}; "
            f"SHA-256 parmak izi {colon_hex(sha256)}; SHA-1 parmak izi {colon_hex(sha1)}",
            "root certificate and its fingerprints",
        )

    def load_crls(self) -> None:
        report = self.report
        names = [name for name in self.names if name.startswith("sil/")]
        if not names:
            note = str(self.index.get("crl_note") or "")
            report.info(
                "crl-absent",
                "kökler",
                f"Pakette SİL (sertifika iptal listesi) yok; iptal durumu kontrol edilmeyecek. "
                f"{note}".strip(),
                "no CRLs in the package; revocation is not checked",
            )
            return
        for name in names:
            data = self._read(name, "kökler")
            try:
                for block in pem_blocks(data or b"", "X509 CRL") if data else []:
                    self.crls.append(parse_crl(block))
            except (ValueError, IndexError, RecursionError) as exc:
                report.warn("crl-invalid", "kökler", f"{name}: SİL okunamadı ({exc})", "bad CRL")
        report.info("crl-loaded", "kökler", f"{len(self.crls)} SİL yüklendi", "CRLs loaded")

    # -- 3. sealed days ------------------------------------------------------------------------

    def find_days(self) -> list[str]:
        for name in self.names:
            match = _MANIFEST.match(name)
            if match is None or match.group("prefix") != self.prefix:
                continue
            day = match.group("day")
            self.covered.add(name)
            if day.replace("-", "") != match.group("y") + match.group("m") + match.group("d"):
                self.report.fail(
                    "manifest-misplaced", day, f"{name}: yanlış klasörde", f"{name}: wrong folder"
                )
                self.broken_folders.add(name.rsplit("/", 1)[0])
                continue
            state = DayState(day, name, data=self._read(name, day))
            if state.data is not None:
                state.manifest_sha256 = hashlib.sha256(state.data).hexdigest()
            self.days[day] = state
        # Every day the index names (in its day list or its scope) must be in the package.
        listed = {str(entry.get("day")) for entry in _entries(self.index.get("days"))}
        scope_days = _object(self.index.get("scope")).get("days")
        if isinstance(scope_days, list):
            listed |= {str(day) for day in scope_days}
        for day in sorted(listed - set(self.days)):
            self.report.fail(
                "manifest-missing",
                day,
                f"{day}: paket dizininde olan günün manifesti pakette yok",
                f"{day}: the manifest of a listed day is missing",
            )
        return sorted(self.days)

    def check_day(self, state: DayState) -> None:
        report = self.report
        day = state.day
        report.section(f"3. Gün {day}", f"sealed day {day}")
        summary: dict[str, Any] = {"day": day, "ok": False}
        report.days.append(summary)
        if state.data is None:
            self.broken_folders.add(state.manifest_name.rsplit("/", 1)[0])
            return
        failures_before = report.count("fail")
        try:
            manifest = load_json(state.data)
            if not isinstance(manifest, dict):
                raise ValueError("not an object")
        except (ValueError, RecursionError) as exc:
            report.fail(
                "manifest-invalid", day, f"Manifest okunamadı: {exc}", f"bad manifest: {exc}"
            )
            self.broken_folders.add(state.manifest_name.rsplit("/", 1)[0])
            return
        state.manifest = manifest
        report.info(
            "manifest",
            day,
            f"Manifest {state.manifest_name}, SHA-256 {state.manifest_sha256}",
            "manifest file and hash",
        )
        if not is_canonical(state.data):
            # ARCILOGGER always writes manifests in the canonical form, so another form means
            # another program wrote it (and two JSON parsers might not read it the same way).
            report.fail(
                "manifest-not-canonical",
                day,
                "Manifest kanonik JSON değil; ARCILOGGER manifestleri her zaman kanonik yazılır "
                "(RFC 8785), bu manifesti başka bir program yazmış",
                "the manifest is not canonical JSON (ARCILOGGER always writes canonical JSON)",
            )
        if manifest.get("day") != day:
            report.fail(
                "manifest-day-mismatch",
                day,
                f"Manifest başka bir güne ait: {manifest.get('day')}",
                "the manifest is for another day",
            )
        self._check_token(state, state.data, summary)
        self._check_day_files(state, manifest)
        self._check_deletions(state, manifest)
        self._check_timing(state, manifest, summary)
        self._day_notes(day, manifest)
        tsa = _object(manifest.get("tsa"))
        summary["ok"] = report.count("fail") == failures_before
        summary["legal_grade"] = bool(tsa.get("legal_grade"))
        summary["provider"] = tsa.get("provider")

    def _day_notes(self, day: str, manifest: dict[str, Any]) -> None:
        """What the manifest itself says about legal validity and gaps."""
        report = self.report
        tsa = _object(manifest.get("tsa"))
        if not tsa.get("legal_grade"):
            self.test_days.append(day)
            report.warn(
                "legal-test-stamp",
                day,
                f"Bu gün yasal geçerliliği olmayan bir test/deneme zaman damgasıyla "
                f"(sağlayıcı: {tsa.get('provider', '?')}) mühürlenmiş",
                "sealed with a test time stamp that has no legal validity",
            )
        gaps = _entries(manifest.get("gaps"))
        if gaps:
            sources = ", ".join(sorted({str(gap.get("source")) for gap in gaps}))
            report.info(
                "gaps",
                day,
                f"Manifestte {len(gaps)} kesinti kaydı var (kaynak: {sources}); bunlar log "
                "gelmeyen süreleri gösterir",
                f"{len(gaps)} silence gaps are recorded in the manifest",
            )

    def _check_timing(
        self, state: DayState, manifest: dict[str, Any], summary: dict[str, Any]
    ) -> None:
        """When the day was stamped, worked out from the verified token, not from the manifest.

        A day that is changed after it ended has to be sealed and stamped again; the only trace
        of that is a stamp time long after the day. So: a stamp more than 48 hours after the end
        of the day (Türkiye time) is "late" whatever the manifest says; a stamp before the end
        of the day, or far from the time the manifest says it was written (``created_at``),
        means a wrong clock or a manifest that was not stamped when it was written.
        """
        report = self.report
        day = state.day
        flagged = bool(manifest.get("stamped_late"))
        reason = str(manifest.get("late_reason") or "-")
        gen_time = state.gen_time
        if gen_time is None:  # the token did not verify; only the manifest's own statement
            if flagged:
                report.warn("stamped-late", day, f"Gün geç mühürlenmiş: {reason}", "stamped late")
            return
        start = date_from_iso(day) + timedelta(days=1)
        day_end = datetime(start.year, start.month, start.day, tzinfo=TURKEY)
        delay = gen_time - day_end
        hours = delay.total_seconds() / 3600
        summary["stamp_delay_hours"] = round(hours, 1)
        if delay > LATE_AFTER and not flagged:
            report.warn(
                "stamped-late-computed",
                day,
                f"Gün, bitiminden {hours:.1f} saat sonra damgalanmış (sınır 48 saat), ama "
                "manifest bunu geç mühürleme olarak göstermiyor. Gün damgalanmadan önce "
                "değiştirilmiş ya da sonradan yeniden mühürlenmiş olabilir: sunucunun mühürleme "
                "ve denetim kayıtlarıyla karşılaştırın",
                f"stamped {hours:.1f} hours after the end of the day (limit 48 h) although the "
                "manifest does not say it was late: the day may have been re-sealed",
            )
        elif flagged or delay > LATE_AFTER:
            report.warn(
                "stamped-late",
                day,
                f"Gün geç mühürlenmiş: damga, gün bitiminden {hours:.1f} saat sonra (sınır 48 "
                f"saat); manifestteki gerekçe: {reason}",
                f"stamped late: {hours:.1f} hours after the end of the day",
            )
        if delay < -CLOCK_SKEW:
            report.warn(
                "stamped-before-day-end",
                day,
                f"Damga zamanı ({_format_time(gen_time)}) günün bitiminden önce: manifest bütün "
                "günü kapsayamaz; sunucu saati ileride olabilir (manifestin clock bölümüne bakın)",
                "stamped before the day ended (server clock ahead?)",
            )
        created = _parse_time(manifest.get("created_at"))
        if created is not None:
            difference = gen_time - created
            if difference < -CLOCK_SKEW or difference > CREATED_TOLERANCE:
                minutes = difference.total_seconds() / 60
                report.warn(
                    "stamp-time-differs",
                    day,
                    f"Manifestin yazılma zamanı ({_format_time(created)}) ile damga zamanı "
                    f"({_format_time(gen_time)}) arasında {minutes:.0f} dakika fark var; sunucu "
                    "saati yanlış olabilir ya da manifest yazıldıktan çok sonra damgalanmış",
                    f"created_at and genTime differ by {minutes:.0f} minutes",
                )

    def _check_token(self, state: DayState, manifest_bytes: bytes, summary: dict[str, Any]) -> None:
        report = self.report
        day = state.day
        self.covered.add(state.manifest_name + ".zd")
        token = self._read(state.manifest_name + ".zd", day)
        if token is None:
            return
        state.token_sha256 = hashlib.sha256(token).hexdigest()
        tsa = _object(_object(state.manifest).get("tsa"))
        result = verify_timestamp(
            token,
            manifest_bytes,
            self.roots,
            now=self.now,
            require_critical_eku=tsa.get("require_critical_eku") is not False,
        )
        for error in result.errors:
            report.failed(self._explain(result, error), day, "Zaman damgası: ")
        self._eku_warnings(result, day)
        if result.gen_time is not None:
            summary["gen_time"] = result.gen_time.isoformat()
            summary["serial"] = result.serial_hex
        signer, root = result.signer, result.root
        if signer is not None:
            summary["tsa"] = signer.subject_text
        if result.ok and result.gen_time is not None and signer is not None and root is not None:
            state.gen_time = result.gen_time
            summary["root_sha256"] = root.fingerprint()
            report.ok(
                "token-ok",
                day,
                f"Zaman damgası geçerli: zaman {_format_time(result.gen_time)}, seri no "
                f"{result.serial_hex}, TSA {signer.subject_text}, imza {result.signature}, kök "
                f"{root.subject_text} (SHA-256 {colon_hex(root.fingerprint())}), politika "
                f"{result.policy}, biçim {result.container}",
                "time-stamp token valid: imprint, signature, ESSCertID, EKU and chain",
            )
            claims_legal = tsa.get("legal_grade")
            test_root = any(TEST_ORGANIZATION in cert.subject_text for cert in result.chain)
            if claims_legal and (result.policy.startswith(TEST_POLICY_PREFIX) or test_root):
                report.fail(
                    "legal-claim-mismatch",
                    day,
                    "Manifest Kamu SM üretim sunucusundan alınmış bir damga olduğunu söylüyor, ama "
                    f"damga ARCILOGGER'ın test (mock) TSA'sına ait (politika {result.policy}, kök "
                    f"{root.subject_text})",
                    "the manifest claims a Kamu SM production stamp but the token is from "
                    "ARCILOGGER's test (mock) TSA",
                )
            for level, code, tr, en in check_revocation(result.chain, self.crls, result.gen_time):
                if self.crls:
                    report.add(level, code, day, tr, en)
        self._cross_check_index(day, result)

    def _eku_warnings(self, result: TimestampResult, where: str) -> None:
        for warning in result.warnings:
            self.report.warn(
                warning.code,
                where,
                f"Zaman damgası: {warning.tr}. Site, Kamu SM'nin sertifika profilini kabul edip "
                "bu kuralı gevşetmiş ve bunu mühürlü manifestte belirtmiştir "
                '(tsa.require_critical_eku = false); OpenSSL "ts -verify" böyle bir damgayı '
                '"unsuitable certificate purpose" diyerek reddeder',
                f"{warning.en}; the site relaxed this rule and sealed that choice in the "
                "manifest (OpenSSL ts -verify rejects such a token)",
            )

    def _relaxed_eku(self) -> bool:
        """Whether a sealed manifest of the package says the site accepts a non-critical EKU
        (then the stamps of its records — deletions, renewals — are judged the same way)."""
        for state in self.days.values():
            try:
                manifest = load_json(state.data or b"")
            except (ValueError, RecursionError):
                continue
            tsa = _object(_object(manifest).get("tsa"))
            if tsa.get("require_critical_eku") is False:
                return True
        return False

    def _explain(self, result: TimestampResult, error: CheckError) -> CheckError:
        """A token whose imprint is another manifest of this package was swapped: say which."""
        if error.code != "token-imprint" or result.imprint_digest != "sha256":
            return error
        imprint = result.imprint.hex()
        for other in self.days.values():
            if other.manifest_sha256 == imprint:
                return CheckError(
                    "token-swapped",
                    f"Bu zaman damgası başka bir güne ait ({other.day} manifestini damgalıyor); "
                    "damga dosyaları yer değiştirmiş",
                    f"this token stamps the manifest of {other.day}: tokens were swapped",
                )
        return error

    def _cross_check_index(self, day: str, result: TimestampResult) -> None:
        entries = [e for e in _entries(self.index.get("days")) if e.get("day") == day]
        if not entries or result.gen_time is None:
            return
        listed_time = entries[0].get("gen_time")
        try:
            time_differs = datetime.fromisoformat(str(listed_time)) != result.gen_time
        except ValueError:
            time_differs = True
        if entries[0].get("serial") != result.serial_hex or time_differs:
            self.report.fail(
                "token-index-mismatch",
                day,
                "paket.json'daki zaman damgası bilgisi (seri no / zaman) damga dosyasıyla "
                "uyuşmuyor",
                "serial or genTime in paket.json differs from the token",
            )

    def _excluded(self, day: str) -> set[str]:
        for entry in _entries(self.index.get("days")):
            if entry.get("day") == day:
                return {str(item.get("path")) for item in _entries(entry.get("excluded"))}
        return set()

    def _check_day_files(self, state: DayState, manifest: dict[str, Any]) -> None:
        """Every file of the manifest: present (unless left out on purpose), size, SHA-256."""
        report = self.report
        day = state.day
        excluded = self._excluded(day)
        audit = manifest.get("audit_log")
        for entry in [*_entries(manifest.get("files")), *_entries([audit])]:
            is_audit = entry is audit
            path = str(entry.get("path", ""))
            if not safe_name(path):
                report.fail("file-path-invalid", day, f"Geçersiz yol: {path!r}", "invalid path")
                continue
            name = self.prefix + path
            if name in self.name_set:
                self.covered.add(name)
                if self._check_day_file(day, name, path, entry):
                    state.verified_files.add(name)
            elif is_audit and self.package_mode and not self.index:
                # Without a readable paket.json (already a failure) the package's scope is
                # unknown; ARCILOGGER leaves the audit log out by default, so its absence alone
                # is not reported as one more failure.
                report.info(
                    "audit-log-absent",
                    day,
                    f"{path}: denetim kaydı pakette yok (paket dizini okunamadığı için kapsam "
                    "bilinmiyor)",
                    f"{path}: the audit log is not in the package (scope unknown: no index)",
                )
            elif path in excluded and is_audit and not self._in_scope(entry, audit=True):
                report.info(
                    "audit-log-excluded",
                    day,
                    f"{path}: ARCILOGGER'ın denetim kaydı bu pakete alınmadı (varsayılan; başka "
                    "sorguları ve personel bilgilerini içerir). Bu bir hata değildir; dosyanın "
                    f"manifestteki SHA-256 değeri {entry.get('sha256')}",
                    f"{path}: ARCILOGGER's audit log is not included in this package (the "
                    "default: it holds other requests and staff data); not a failure",
                )
            elif path in excluded and not is_audit and not self._in_scope(entry):
                report.info(
                    "file-excluded",
                    day,
                    f"{path}: kapsam dışı, pakete alınmadı (doğrulanmadı; manifestteki SHA-256 "
                    f"{entry.get('sha256')})",
                    f"{path}: left out of this package on purpose (not verified)",
                )
            elif path in excluded:
                report.fail(
                    "file-excluded-in-scope",
                    day,
                    f"{path}: paket.json bu dosyayı kapsam dışı sayıyor, ama "
                    + (
                        "paket denetim kaydını içerdiğini söylüyor"
                        if is_audit
                        else f"kaynağı ({entry.get('source')}) paketin kapsamında"
                    )
                    + " — dosya eksik",
                    f"{path}: marked as left out although it is in the package's scope",
                )
            else:
                report.fail("file-missing", day, f"{path}: dosya eksik", f"{path}: file missing")

    def _in_scope(self, entry: dict[str, Any], *, audit: bool = False) -> bool:
        """Whether a manifest file belongs to what the package claims to contain: the chosen
        sources, and the audit log only when ``scope.audit_log`` is true (a package without that
        key is from a version that always included it)."""
        scope = _object(self.index.get("scope"))
        if audit:
            return scope.get("audit_log", True) is not False
        sources = scope.get("sources")
        return sources is None or entry.get("source") in sources

    def _check_day_file(self, day: str, name: str, path: str, entry: dict[str, Any]) -> bool:
        report = self.report
        digest = self._digest(name, day)
        if digest is None:
            return False
        sha256, size = digest
        if "bytes" in entry and size != entry["bytes"]:
            report.fail(
                "file-size-mismatch",
                day,
                f"{path}: boyut manifesttekinden farklı ({entry['bytes']} yerine {size} bayt)",
                f"{path}: size differs from the manifest",
            )
            return False
        if sha256 != entry.get("sha256"):
            report.fail(
                "file-hash-mismatch",
                day,
                f"{path}: SHA-256 değeri manifestteki değerle aynı değil — dosya mühürlendikten "
                "sonra değiştirilmiş",
                f"{path}: SHA-256 differs from the manifest (changed after sealing)",
            )
            return False
        detail = ""
        if self.deep and "sha256_uncompressed" in entry:
            try:
                with self.package.open(name) as handle:
                    raw_sha256, lines = gunzip_digest(handle)
            except READ_ERRORS as exc:
                report.fail(
                    "file-content-mismatch", day, f"{path}: açılamadı ({exc})", f"{path}: bad gzip"
                )
                return False
            if raw_sha256 != entry["sha256_uncompressed"] or lines != entry.get("lines"):
                report.fail(
                    "file-content-mismatch",
                    day,
                    f"{path}: açılmış içerik manifestle uyuşmuyor",
                    f"{path}: uncompressed content differs from the manifest",
                )
                return False
            detail = f"; açılmış hali {lines} satır, SHA-256 aynı"
        report.ok(
            "file-ok",
            day,
            f"{path}: {size} bayt, SHA-256 manifestteki değerle aynı{detail}",
            f"{path}: matches the manifest",
        )
        if entry.get("unterminated_last_line") is True:
            report.info(
                "file-torn-last-line",
                day,
                f"{path}: son satır yarım kalmış (satır sonu yok; çökme ya da dolu disk sırasında "
                "kesilen bir yazma). ARCILOGGER baytları olduğu gibi mühürlemiştir; bu satır "
                "eksik olabilir",
                f"{path}: the last line is incomplete (a write torn by a crash or a full disk); "
                "it was sealed as it was",
            )
        return True

    def _check_deletions(self, state: DayState, manifest: dict[str, Any]) -> None:
        """Deletion records (imha tutanağı) sealed into this day's manifest by retention."""
        for entry in _entries(manifest.get("deletions")):
            path = str(entry.get("path", ""))
            name = self.prefix + path
            if not safe_name(path) or name not in self.name_set:
                self.report.info(
                    "deletion-absent",
                    state.day,
                    f"Manifestteki imha tutanağı pakette yok: {path}",
                    "the deletion record named in the manifest is not in the package",
                )
                continue
            self.covered.update((name, name + ".zd"))
            record = self._read(name, state.day)
            if record is None:
                continue
            if hashlib.sha256(record).hexdigest() != entry.get("sha256"):
                self.report.fail(
                    "deletion-hash-mismatch",
                    state.day,
                    f"İmha tutanağı manifestteki özetle uyuşmuyor: {path}",
                    "the deletion record differs from the manifest",
                )
                continue
            self._check_stamped_record(
                name, record, state.day, "deletion", entry.get("token_sha256")
            )

    def _check_stamped_record(
        self, name: str, record: bytes, where: str, kind: str, token_sha256: Any = None
    ) -> TimestampResult | None:
        """A deletion or renewal record and its own time-stamp token (``<name>.zd``)."""
        report = self.report
        token = self._read(name + ".zd", where)
        if token is None:
            return None
        if token_sha256 and hashlib.sha256(token).hexdigest() != token_sha256:
            report.fail(
                f"{kind}-token-mismatch",
                where,
                f"{name}.zd: özet manifestteki değerle uyuşmuyor",
                f"{name}.zd: differs from the manifest",
            )
        result = verify_timestamp(
            token, record, self.roots, now=self.now, require_critical_eku=not self._relaxed_eku()
        )
        for error in result.errors:
            report.failed(error, where, f"{name}: ")
        self._eku_warnings(result, where)
        if result.ok and result.gen_time is not None:
            report.ok(
                f"{kind}-ok",
                where,
                f"{name}: zaman damgası geçerli ({_format_time(result.gen_time)}, seri no "
                f"{result.serial_hex})",
                f"{name}: time stamp valid",
            )
        return result

    # -- 4. the chain between days -------------------------------------------------------------

    def check_chain(self) -> None:
        """Each manifest names the SHA-256 of the previous day's manifest and token."""
        report = self.report
        report.section("4. Günler arası zincir", "chain between days")
        anchor = self._anchor()
        days = sorted(self.days)
        for position, day in enumerate(days):
            manifest = self.days[day].manifest
            if manifest is None:
                continue
            previous = manifest.get("previous")
            before = date_from_iso(day)
            skipped = self._skipped_days(day, manifest)
            if skipped is not None:
                before = date_from_iso(skipped[0])
            expected_day = (before - timedelta(days=1)).isoformat()
            earlier = days[position - 1] if position else None
            if previous is None and earlier is not None:
                report.warn(
                    "chain-restart",
                    day,
                    f"{day}: manifest zincirin yeniden başladığını söylüyor (önceki gün bağlantısı "
                    f"yok), oysa pakette daha önceki bir gün ({earlier}) var; aradaki zincir "
                    "kontrol edilemez, nedeni sorulmalı",
                    "the chain restarts here although an earlier day is in the package",
                )
            elif previous is None:
                report.info(
                    "chain-first",
                    day,
                    f"{day}: ARCILOGGER zincirinin ilk günü (önceki gün bağlantısı yok)",
                    "first day of the chain",
                )
            elif not isinstance(previous, dict) or previous.get("day") != expected_day:
                report.fail(
                    "chain-not-previous-day",
                    day,
                    f"{day}: zincir bağlantısı bir önceki günü ({expected_day}) göstermiyor",
                    "the chain link does not point to the day before",
                )
            elif expected_day in self.days:
                self._check_link(day, previous, self.days[expected_day])
            elif anchor is not None and anchor.get("day") == expected_day:
                self._check_anchor_link(day, previous, anchor)
            elif earlier is not None:
                report.warn(
                    "chain-gap",
                    day,
                    f"{day}: önceki gün ({expected_day}) pakette yok, oysa daha önceki bir gün "
                    f"({earlier}) var: aradaki gün(ler) pakete alınmamış ya da çıkarılmış; "
                    f"{earlier} ile {day} arasındaki zincir kontrol edilemez",
                    f"the package skips day(s) between {earlier} and {day}; the chain between "
                    "them cannot be checked",
                )
            else:
                report.info(
                    "chain-start",
                    day,
                    f"{day}: zincir bu pakette başlıyor; önceki gün ({expected_day}) pakette "
                    f"yok. Beklenen önceki manifest SHA-256 {previous.get('manifest_sha256')}, "
                    f"damga SHA-256 {previous.get('token_sha256')}",
                    "chain start in this package: the previous day is not included",
                )

    def _skipped_days(self, day: str, manifest: dict[str, Any]) -> tuple[str, str] | None:
        """The run of empty days this manifest stands for (``unsealed_empty_days`` in its gaps).

        ARCILOGGER does not stamp every day of a long run without any log (collector stopped,
        server off, clock jumped forward): the next sealed day lists the run and links to the
        last sealed day before it. The run must end the day before this day.
        """
        for gap in _entries(manifest.get("gaps")):
            if gap.get("kind") != UNSEALED_DAYS:
                continue
            first, last = str(gap.get("first_day")), str(gap.get("last_day"))
            try:
                start, end = date_from_iso(first), date_from_iso(last)
                valid = start <= end and end == date_from_iso(day) - timedelta(days=1)
            except ValueError:
                valid = False
            if not valid:
                self.report.fail(
                    "chain-skip-invalid",
                    day,
                    f"{day}: manifestteki boş gün aralığı ({first} … {last}) geçersiz ya da bu "
                    "günden hemen önce bitmiyor",
                    "the run of empty days in the manifest is invalid or does not end the day "
                    "before",
                )
                return None
            count = (end - start).days + 1
            self.report.info(
                "chain-skipped-days",
                day,
                f"{day}: önceki {count} gün ({first} … {last}) hiç log içermediği için tek tek "
                "damgalanmamış; bu manifest onları boşluk olarak listeliyor ve zincir, ondan "
                "önceki mühürlü güne bağlanıyor",
                f"the {count} empty days {first} … {last} were not stamped one by one; this "
                "manifest lists them and links to the sealed day before them",
            )
            return first, last
        return None

    def _check_link(self, day: str, previous: dict[str, Any], linked: DayState) -> None:
        if (
            previous.get("manifest_sha256") == linked.manifest_sha256
            and previous.get("token_sha256") == linked.token_sha256
        ):
            self.report.ok(
                "chain-ok",
                day,
                f"{day} → {linked.day}: zincir bağlantısı doğru (önceki günün manifest ve damga "
                "özetleri aynı)",
                "chain link matches the previous day's manifest and token",
            )
        else:
            self.report.fail(
                "chain-broken",
                day,
                f"{day} → {linked.day}: zincir kırık — önceki günün manifesti ya da zaman "
                "damgası, bu gün mühürlendikten sonra değiştirilmiş",
                "chain broken: the previous day's manifest or token was changed",
            )

    def _anchor(self) -> dict[str, Any] | None:
        name = self.prefix + "deletions/chain-anchor.json"
        if name not in self.name_set:
            return None
        data = self._read(name, "zincir")
        try:
            anchor = load_json(data or b"")
        except (ValueError, RecursionError):
            anchor = None
        if not isinstance(anchor, dict):
            self.report.fail(
                "anchor-invalid", "zincir", "Zincir çapası okunamadı", "chain anchor unreadable"
            )
            self.broken_folders.add(self.prefix + "deletions")
            return None
        return anchor

    def _check_anchor_link(
        self, day: str, previous: dict[str, Any], anchor: dict[str, Any]
    ) -> None:
        """The previous day was deleted after its retention period; the chain anchor and the
        time-stamped deletion record keep its hashes."""
        matches = previous.get("manifest_sha256") == anchor.get("manifest_sha256") and previous.get(
            "token_sha256"
        ) == anchor.get("token_sha256")
        record_name = self.prefix + "deletions/" + str(anchor.get("record", ""))
        self.covered.update(
            (self.prefix + "deletions/chain-anchor.json", record_name, record_name + ".zd")
        )
        record = self._read(record_name, day) if safe_name(record_name) else None
        record_ok = False
        if record is not None:
            result = self._check_stamped_record(record_name, record, day, "deletion")
            try:
                entries = _entries(load_json(record).get("days"))
            except (ValueError, AttributeError, RecursionError):
                entries = []
            record_ok = bool(result and result.ok) and any(
                entry.get("day") == anchor.get("day")
                and entry.get("manifest_sha256") == previous.get("manifest_sha256")
                and entry.get("token_sha256") == previous.get("token_sha256")
                for entry in entries
            )
        if matches and record_ok:
            self.report.ok(
                "chain-anchor",
                day,
                f"{day}: önceki gün ({anchor.get('day')}) saklama süresi dolduğu için imha "
                "edilmiş; bağlantı zincir çapası ve zaman damgalı imha tutanağıyla eşleşiyor",
                "previous day deleted by retention; the link matches the chain anchor and the "
                "time-stamped deletion record",
            )
        else:
            self.report.fail(
                "chain-broken",
                day,
                f"{day}: zincir kırık — önceki gün bağlantısı zincir çapası ya da imha "
                "tutanağıyla uyuşmuyor",
                "chain broken: the link does not match the chain anchor / deletion record",
            )

    # -- 5. renewals ---------------------------------------------------------------------------

    def check_renewals(self) -> None:
        """Renewal records re-stamp older tokens before the TSA certificate expires."""
        folder = self.prefix + "renewals/"
        names = [n for n in self.names if n.startswith(folder) and n.endswith(".json")]
        if not names:
            return
        self.report.section("5. Yenileme damgaları", "renewal stamps")
        known = {}
        for state in self.days.values():
            known[state.manifest_sha256] = f"{state.day} manifest"
            known[state.token_sha256] = f"{state.day} damga"
        for name in names:
            self.covered.update((name, name + ".zd"))
            record = self._read(name, "yenileme")
            if record is None:
                continue
            result = self._check_stamped_record(name, record, "yenileme", "renewal")
            text = record.decode("utf-8", "replace")
            covered = sorted({known[h] for h in _HEX64.findall(text) if h in known})
            if result is not None and result.ok:
                self.report.info(
                    "renewal-covers",
                    "yenileme",
                    f"{name} şunları yeniden damgalıyor: "
                    f"{', '.join(covered) or '(bu paketteki hiçbir gün)'}",
                    "what the renewal stamp covers",
                )

    # -- 5b. the official report ---------------------------------------------------------------

    def check_report(self) -> None:
        """The 5651 report (``rapor/<name>.pdf``) and, when it was time-stamped, its token.

        The PDF's size and SHA-256 were compared with paket.json in step 1; here its hash is
        shown for comparison with ARCILOGGER's lookup record and audit log (the PDF cannot
        contain its own hash), and the token ``rapor/<name>.pdf.zd`` is verified against the
        PDF and the package's root certificates like a deletion record's. paket.json names the
        report (``report``) and its token (``report_token``): a named file that is missing, or a
        report or token it does not name, fails — a package cannot lose its report (or gain one)
        unnoticed. ``report_note`` says why a report has no token (shown as a warning).
        """
        names = [name for name in self.names if name.startswith("rapor/")]
        listed: dict[str, str] = {}
        note = ""
        if self.package_mode:
            for key in ("report", "report_token"):
                value = self.index.get(key)
                if isinstance(value, str) and value:
                    listed[key] = value
                elif value is not None:
                    listed[key] = repr(value)
            raw_note = self.index.get("report_note")
            note = raw_note if isinstance(raw_note, str) else ""
        if not names and not listed and not note:
            return
        report = self.report
        report.section("5b. Resmi rapor", "official report")
        for key, name in sorted(listed.items()):
            if name not in self.name_set:
                what = ("raporu", "report") if key == "report" else ("rapor damgasını", "token")
                report.fail(
                    f"{key.replace('_', '-')}-missing",
                    "rapor",
                    f"paket.json {key} olarak {name} dosyasını gösteriyor, fakat pakette yok "
                    f"(paketin {what[0]} çıkarılmış)",
                    f"paket.json names {name} as the package's {what[1]}, but it is missing",
                )
        for name in names:
            if name.endswith(".zd"):
                self.covered.add(name)
                if name.removesuffix(".zd") not in self.name_set:
                    report.fail(
                        "report-token-orphan",
                        "rapor",
                        f"{name}: zaman damgasının ait olduğu rapor pakette yok",
                        f"{name}: the report this time stamp belongs to is missing",
                    )
                elif self.package_mode and listed.get("report_token") != name:
                    report.fail(
                        "report-token-unlisted",
                        "rapor",
                        f"{name}: paket.json bu dosyayı raporun zaman damgası olarak göstermiyor",
                        f"{name}: paket.json does not name this file as the report's time stamp",
                    )
                continue
            self.covered.add(name)
            if self.package_mode and listed.get("report") != name:
                report.fail(
                    "report-unlisted",
                    "rapor",
                    f"{name}: paket.json bu dosyayı paketin raporu olarak göstermiyor",
                    f"{name}: paket.json does not name this file as the package's report",
                )
            data = self._read(name, "rapor")
            if data is None:
                continue
            report.info(
                "report-hash",
                "rapor",
                f"Rapor {name}: SHA-256 {hashlib.sha256(data).hexdigest()} — ARCILOGGER'daki "
                "sorgu kaydında ve denetim kaydında (lookup.report) aynı değer yazılı olmalı",
                "compare the report's SHA-256 with ARCILOGGER's lookup record and audit log",
            )
            if name + ".zd" in self.name_set:
                self._check_stamped_record(name, data, "rapor", "report")
            elif note:
                report.warn(
                    "report-token-omitted",
                    "rapor",
                    f"{name}: raporun zaman damgası pakette yok — {note}",
                    "the report's time stamp is not in the package (see report_note in paket.json)",
                )
            else:
                report.info(
                    "report-not-stamped",
                    "rapor",
                    f"{name}: rapora ayrıca zaman damgası alınmamış (isteğe bağlı ayar)",
                    "the report itself was not time-stamped (optional setting)",
                )

    # -- 6. the extract ------------------------------------------------------------------------

    def check_extracts(self) -> None:
        """Each extract line is ``<line number>\\t<the raw line>``; compare with the sealed file."""
        names = [name for name in self.names if name.startswith("extract/")]
        if not names:
            return
        report = self.report
        report.section("6. Alıntılanan satırlar", "extracted lines")
        listed = {str(entry.get("path")): entry for entry in _entries(self.index.get("extract"))}
        for name in names:
            match = _EXTRACT.match(name)
            if match is None:
                report.add(
                    "fail" if self.package_mode else "warn",
                    "extract-unknown",
                    "alıntı",
                    f"Alıntı klasöründe beklenmeyen dosya: {name}",
                    f"unexpected file in extract/: {name}",
                )
            self.covered.add(name)
            if match is None:
                continue
            day, source = match.group("day"), match.group("source")
            sealed = f"{self.prefix}{day[:4]}/{day[5:7]}/{day[8:10]}/{source}.log.gz"
            data = self._read(name, "alıntı")
            if data is None:
                continue
            try:
                wanted = _parse_extract(data)
            except ValueError as exc:
                report.fail(
                    "extract-format",
                    "alıntı",
                    f"{name}: biçim hatalı ({exc})",
                    f"{name}: bad format ({exc})",
                )
                continue
            if name in listed and listed[name].get("lines") != sorted(wanted):
                report.fail(
                    "extract-index-mismatch",
                    "alıntı",
                    f"{name}: satır numaraları paket dizinindekilerle aynı değil",
                    f"{name}: line numbers differ from the index",
                )
            if sealed not in self.name_set:
                report.fail(
                    "extract-no-source",
                    "alıntı",
                    f"{name}: alıntının mühürlü kaynak dosyası pakette yok ({sealed})",
                    f"{name}: the sealed file it was taken from is missing",
                )
                continue
            state = self.days.get(day)
            if state is None or sealed not in state.verified_files:
                report.fail(
                    "extract-source-unverified",
                    "alıntı",
                    f"{name}: kaynak dosya ({sealed}) doğrulanamadığı için alıntıya da güvenilemez",
                    f"{name}: the sealed source file did not verify",
                )
                continue  # an unverified file is not read any further
            self._compare_extract(name, sealed, wanted)

    def _compare_extract(self, name: str, sealed: str, wanted: dict[int, bytes]) -> None:
        report = self.report
        found: dict[int, bytes] = {}
        last = max(wanted) if wanted else 0
        try:
            with self.package.open(sealed) as handle, gzip.GzipFile(fileobj=handle) as raw:
                for number in range(1, last + 1):
                    line = raw.readline(MAX_LINE + 1)
                    if not line:
                        break
                    if len(line) > MAX_LINE:
                        raise TooLargeError(f"line {number} is longer than {MAX_LINE} bytes")
                    if number in wanted:
                        found[number] = line[:-1] if line.endswith(b"\n") else line
        except READ_ERRORS as exc:
            report.fail(
                "extract-mismatch", "alıntı", f"{sealed}: açılamadı ({exc})", f"{sealed}: bad gzip"
            )
            return
        bad = sorted(number for number, line in wanted.items() if found.get(number) != line)
        if bad:
            shown = ", ".join(str(number) for number in bad[:MAX_LISTED])
            report.fail(
                "extract-mismatch",
                "alıntı",
                f"{name}: {len(bad)} satır mühürlü dosyadakiyle aynı değil (satır {shown})",
                f"{name}: {len(bad)} lines differ from the sealed file",
            )
        else:
            report.ok(
                "extract-ok",
                "alıntı",
                f"{name}: {len(wanted)} satırın tamamı {sealed} dosyasındaki aynı numaralı "
                "satırlarla bayt bayt aynı",
                f"{name}: all {len(wanted)} lines equal the sealed file byte for byte",
            )

    # -- summary -------------------------------------------------------------------------------

    def check_contents(self) -> None:
        """Nothing in the package that no check accounted for.

        A file in a sealed day folder that the day's manifest does not list was never sealed:
        it proves nothing and must not look as if it did (a failure, also in a vault copy). In
        an evidence package every other file must be one of the known parts too; in a copy of
        the vault, unknown files outside the day folders are only reported.
        """
        report = self.report
        report.section("7. Paketin içeriği", "package contents")
        if self.os_files:
            shown = ", ".join(self.os_files[:MAX_LISTED])
            report.info(
                "os-metadata",
                "içerik",
                f"İşletim sisteminin eklediği {len(self.os_files)} dosya yok sayıldı (okunmadı, "
                f"kanıt değildir): {shown}",
                "files added by the operating system were ignored",
            )
        prefix, problems, overflow = self.prefix, 0, "warn"
        for name in self.names:
            if name in self.covered or name.rpartition("/")[0] in self.broken_folders:
                continue
            relative = name[len(prefix) :] if name.startswith(prefix) else None
            if relative is not None and _DAY_FILE.match(relative):
                level, code = "fail", "file-not-sealed"
                tr = (
                    f"{name}: bu dosya günün manifestinde yok, yani hiç mühürlenmemiş; kanıt "
                    "değildir (pakete sonradan eklenmiş olabilir)"
                )
                en = f"{name}: not listed in the day's manifest (never sealed)"
            elif self._known_part(name):
                continue
            else:
                level, code = ("fail" if self.package_mode else "warn"), "file-unexpected"
                tr = f"{name}: ARCILOGGER'ın bu yerde üretmediği, beklenmeyen dosya"
                en = f"{name}: unexpected file"
            problems += 1
            if problems <= MAX_LISTED:
                report.add(level, code, "içerik", tr, en)
            elif level == "fail":
                overflow = "fail"
        if problems > MAX_LISTED:
            report.add(
                overflow,
                "file-unexpected",
                "içerik",
                f"… ve {problems - MAX_LISTED} beklenmeyen dosya daha",
                f"… and {problems - MAX_LISTED} more unexpected files",
            )
        if not problems:
            report.ok(
                "contents-ok",
                "içerik",
                "Paketteki her dosya mühürlü bir güne, bir kayda ya da paketin bilinen bir "
                "parçasına ait",
                "every file belongs to a sealed day, a record or a known part of the package",
            )

    def _known_part(self, name: str) -> bool:
        if self.package_mode:
            return bool(_PACKAGE_FILE.match(name) or _EXTRACT.match(name))
        return bool(_VAULT_RECORD.match(name))

    def summary(self) -> None:
        report = self.report
        report.section("Özet", "summary")
        days = sorted(self.days)
        if days:
            report.info(
                "summary-days",
                "özet",
                f"İncelenen gün: {len(days)} ({days[0]} … {days[-1]})",
                f"{len(days)} days examined",
            )
        else:
            report.fail(
                "nothing-verified",
                "özet",
                "Pakette mühürlü gün yok: doğrulanan bir şey yok",
                "no sealed day in the package: nothing was verified",
            )
        used = sorted(
            {str(day.get("root_sha256")) for day in report.days if day.get("root_sha256")}
        )
        for fingerprint in used:
            report.info(
                "summary-root",
                "özet",
                f"Damgalar şu köke bağlanıyor: SHA-256 {colon_hex(fingerprint)} — Kamu SM'nin "
                "yayımladığı parmak iziyle karşılaştırın",
                "the time stamps chain to this root: compare it with Kamu SM's published value",
            )
        if len(used) > 1:
            report.warn(
                "summary-several-roots",
                "özet",
                f"Bu paketteki günlerin damgaları {len(used)} farklı köke bağlanıyor; her birinin "
                "parmak izini Kamu SM'nin yayımladığı değerlerle ayrı ayrı karşılaştırın",
                f"the days of this package chain to {len(used)} different roots: compare each",
            )
        if self.test_days:
            report.warn(
                "summary-test-days",
                "özet",
                f"{len(self.test_days)} gün yasal geçerliliği olmayan test damgası taşıyor: "
                f"{', '.join(self.test_days)}",
                "some days carry test time stamps without legal validity",
            )


def date_from_iso(text: str) -> date:
    return date(int(text[:4]), int(text[5:7]), int(text[8:10]))


def _parse_time(value: Any) -> datetime | None:
    """An ISO 8601 time with a UTC offset (as ARCILOGGER writes them), or None."""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _parse_extract(data: bytes) -> dict[int, bytes]:
    """``<number>\\t<raw line>\\n`` per line → {number: raw line}.

    Raw spool lines never contain a line feed (the collector escapes control bytes as ``#ooo``),
    so one extract line is exactly one raw line.
    """
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    wanted: dict[int, bytes] = {}
    previous = 0
    for line in lines:
        number_text, tab, raw = line.partition(b"\t")
        if not tab or not number_text.isdigit():
            raise ValueError("expected '<line number>TAB<raw line>'")
        number = int(number_text)
        if number <= previous:
            raise ValueError("line numbers must increase")
        wanted[number] = raw
        previous = number
    return wanted


def verify_package(
    package: Package,
    *,
    roots_dir: Path | None = None,
    deep: bool = True,
    now: datetime | None = None,
    echo: Callable[[str], None] | None = None,
) -> Report:
    """Run every check on ``package``; the report's ``passed`` is the verdict."""
    report = Report(echo)
    verifier = Verifier(
        package, report, roots_dir=roots_dir, deep=deep, now=now or datetime.now(UTC)
    )
    verifier.check_index()
    verifier.load_roots()
    verifier.load_crls()
    days = verifier.find_days()
    if not days and not verifier.index:
        raise PackageError(
            "Burada kanıt paketi ya da mühürlü gün bulunamadı",
            "no evidence package or sealed day found here",
        )
    for day in days:
        verifier.check_day(verifier.days[day])
    verifier.check_chain()
    verifier.check_renewals()
    verifier.check_report()
    verifier.check_extracts()
    verifier.check_contents()
    verifier.summary()
    return report


def _utf8_output() -> None:
    """Turkish letters must not break a Windows console or a redirected output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_output()
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="ARCILOGGER kanıt paketi doğrulayıcı [evidence package verifier]",
    )
    parser.add_argument(
        "paket",
        nargs="?",
        help="paket klasörü ya da şifresiz ZIP (varsayılan: bu dosyanın klasörü) "
        "[package folder or unencrypted ZIP; default: this file's folder]",
    )
    parser.add_argument(
        "--kokler",
        "--roots",
        dest="roots",
        type=Path,
        help="yalnızca bu klasördeki kök sertifikalara güven (örneğin Kamu SM'den kendi "
        "indirdikleriniz) [trust only the root certificates in this folder]",
    )
    parser.add_argument(
        "--hizli",
        "--quick",
        dest="quick",
        action="store_true",
        help="sıkıştırılmış dosyaları açıp içeriklerini ayrıca kontrol etme [skip decompression]",
    )
    parser.add_argument("--json", action="store_true", help="sonucu JSON olarak yaz [JSON output]")
    args = parser.parse_args(argv)
    target = Path(args.paket) if args.paket else Path(__file__).resolve().parent
    if args.roots is not None and not args.roots.is_dir():
        print(f"HATA: kök klasörü yok: {args.roots} [roots folder not found]", file=sys.stderr)
        return 2
    echo = None if args.json else print
    if echo is not None:
        echo(f"ARCILOGGER kanıt paketi doğrulayıcı {VERIFIER_VERSION} [evidence package verifier]")
        echo(f"Paket [package]: {target}")
        echo(f"Python {sys.version.split()[0]}")
    try:
        with open_package(target) as package:
            report = verify_package(package, roots_dir=args.roots, deep=not args.quick, echo=echo)
    except PackageError as exc:
        print(f"HATA: {exc.tr} [{exc.en}]", file=sys.stderr)
        return 2
    except Exception as exc:  # a bug or an input no check foresaw: never a pass
        print(
            f"HATA: doğrulama beklenmeyen bir hatayla durdu; paket doğrulanamadı: {exc!r} "
            "[verification stopped by an unexpected error; the package is NOT verified]",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps(report.as_json(str(target)), ensure_ascii=False, indent=2))
        return 0 if report.passed else 1
    failures, warnings = report.count("fail"), report.count("warn")
    print(f"  Hata [failures]: {failures}   Uyarı [warnings]: {warnings}")
    if report.passed and warnings:
        print(
            f"SONUÇ: DOĞRULANDI — {warnings} uyarıyı okuyun [RESULT: VERIFIED — read the "
            f"{warnings} warnings]"
        )
    elif report.passed:
        print("SONUÇ: DOĞRULANDI [RESULT: VERIFIED]")
    else:
        print(
            f"SONUÇ: DOĞRULANAMADI — {failures} hata [RESULT: NOT VERIFIED — {failures} failures]"
        )
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
