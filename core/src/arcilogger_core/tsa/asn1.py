"""RFC 3161 structures: requests, responses, token parsing (asn1crypto)."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

from asn1crypto import cms, core, tsp

from arcilogger_core.tsa.base import TsaRejectedError, TsaResponseError


class TimeStampResp(core.Sequence):  # type: ignore[misc]
    """RFC 3161 TimeStampResp. asn1crypto declares ``timeStampToken`` as required, but it is
    OPTIONAL in the RFC and absent from every rejection (e.g. "Not enough credit")."""

    _fields = [  # noqa: RUF012 - asn1crypto API
        ("status", tsp.PKIStatusInfo),
        ("time_stamp_token", cms.ContentInfo, {"optional": True}),
    ]


#: Our names ↔ hashlib names ↔ asn1crypto algorithm names.
DIGESTS = {"sha256": "sha256", "sha384": "sha384", "sha512": "sha512"}
#: Accepted when reading tokens produced by others (never used to request).
READABLE_DIGESTS = {**DIGESTS, "sha1": "sha1"}


def file_digest(path: Path, alg: str) -> bytes:
    hasher = hashlib.new(READABLE_DIGESTS[alg])
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.digest()


def build_request(digest: bytes, alg: str, *, nonce: int | None = None) -> tuple[bytes, int]:
    """DER ``TimeStampReq`` with certReq=TRUE and a random 64-bit nonce."""
    nonce = nonce if nonce is not None else secrets.randbits(63) | 1
    request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {
                "hash_algorithm": {"algorithm": DIGESTS[alg]},
                "hashed_message": digest,
            },
            "nonce": nonce,
            "cert_req": True,
        }
    )
    return request.dump(), nonce


def parse_container(data: bytes) -> tuple[Any, str]:
    """Return ``(ContentInfo, container)`` for a TimeStampResp or a bare TimeStampToken."""
    try:
        response = TimeStampResp.load(data, strict=True)
        info = response["status"]
        status = info["status"].native
        if status not in ("granted", "granted_with_mods"):
            texts = tuple(info["status_string"].native or ())
            # Bits RFC 3161 does not name come as numbers: TÜRKTRUST also sets bit 1 (RFC 4210's
            # badMessageCheck) when it refuses the user name or password.
            fail_info = tuple(
                sorted(
                    bit if isinstance(bit, str) else f"bit {bit}"
                    for bit in info["fail_info"].native or ()
                )
            )
            details = "; ".join(texts) or "no reason given"
            if fail_info:
                details += f" [failInfo: {', '.join(fail_info)}]"
            raise TsaRejectedError(
                f"time-stamp request rejected ({status}): {details}",
                status=str(status),
                texts=texts,
                fail_info=fail_info,
            )
        token = response["time_stamp_token"]
        if token.native is None:
            raise TsaResponseError("granted response without a token")
        return token, "TimeStampResp"
    except (ValueError, TypeError, KeyError):
        pass
    try:
        token = cms.ContentInfo.load(data, strict=True)
        if token["content_type"].native != "signed_data":
            raise TsaResponseError("not a CMS SignedData token")
        return token, "TimeStampToken"
    except (ValueError, TypeError, KeyError) as exc:
        raise TsaResponseError("neither a TimeStampResp nor a TimeStampToken") from exc


def tst_info(token: Any) -> Any:
    signed = token["content"]
    encap = signed["encap_content_info"]
    if encap["content_type"].native != "tst_info":
        raise TsaResponseError("token does not contain TSTInfo")
    content = encap["content"]
    raw = content.contents if hasattr(content, "contents") else bytes(content)
    return tsp.TSTInfo.load(raw), raw


def status_text(data: bytes) -> str:
    """Human readable status of a TimeStampResp (for error messages)."""
    try:
        response = TimeStampResp.load(data)
        texts = response["status"]["status_string"].native or []
        return f"{response['status']['status'].native}: {'; '.join(texts)}"
    except (ValueError, TypeError, KeyError):
        return "unparseable response"
