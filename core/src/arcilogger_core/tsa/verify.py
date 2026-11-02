"""Independent verification of RFC 3161 time-stamp tokens (build specification section 6.3).

Verification never trusts the provider that produced the token. For a token and a file it checks:

1. the container: ``TimeStampResp`` with status granted, or a bare ``TimeStampToken``;
2. the CMS signature over the signed attributes, the ``content-type`` (id-ct-TSTInfo) and
   ``message-digest`` attributes;
3. the ESSCertID / ESSCertIDv2 binding between the signature and the TSA certificate;
4. the TSA certificate's extended key usage: only ``id-kp-timeStamping``, marked critical
   (``tsa.require_critical_eku``), and a key usage that allows signing;
5. the certificate chain up to one of the **pinned root certificates**, each certificate valid at
   the token's ``genTime``, each issuer a CA allowed to sign certificates within its
   pathLenConstraint;
6. that the token's message imprint equals the hash of the file, made with SHA-256 or stronger
   (also for the signature), with no signed attribute given twice;
7. when the caller passes revocation lists (``crls``, the verified lists of ``state/crl``,
   :mod:`arcilogger_core.crl`): every certificate of the chain below the root against them, with
   the codes and levels of the standalone verifier's ``check_revocation`` (Wave 5 D10). The
   result is ``details["crl"]``; the caller decides what a ``crl-revoked`` means for a day
   (:func:`arcilogger_core.chain.verify_day` fails it), as the standalone verifier does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

from arcilogger_core.tsa.asn1 import (
    DIGESTS,
    READABLE_DIGESTS,
    file_digest,
    parse_container,
    tst_info,
)
from arcilogger_core.tsa.base import TokenInfo, TsaError, VerifyResult

if TYPE_CHECKING:  # arcilogger_core.crl imports this module
    from arcilogger_core.crl import Crl

_HASHES: dict[str, type[hashes.HashAlgorithm]] = {
    "sha1": hashes.SHA1,
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
}
MAX_CHAIN_DEPTH = 8
CLOCK_SKEW = timedelta(minutes=5)


#: Policy arc and organization of ARCILOGGER's mock TSA (never a real Kamu SM stamp).
TEST_POLICY_PREFIX = "2.999."
TEST_ORGANIZATION = "ARCILOGGER TEST - NOT VALID"


def is_test_stamp(policy: str, root_subject: str) -> bool:
    """A stamp from ARCILOGGER's mock TSA: its test policy, or a chain to its test root."""
    return policy.startswith(TEST_POLICY_PREFIX) or TEST_ORGANIZATION in root_subject


def confirmed_legal_grade(
    claimed: bool, result: VerifyResult, legal_root_fingerprints: Collection[str]
) -> bool:
    """Whether a stamp counts as legal grade (shown so in the PDF, the calendar, packages).

    All of: the manifest (or the provider) says it came from the Kamu SM production server
    (``claimed``), the token verified, and its chain ends at a root whose SHA-256 fingerprint
    the site listed in ``tsa.legal_root_fingerprints``. A flag in a manifest alone never is.
    """
    root = result.details.get("root_sha256") or ""
    return bool(claimed and result.ok and root and root in set(legal_root_fingerprints))


def load_certificates(paths: Iterable[Path]) -> list[x509.Certificate]:
    """Load PEM or DER certificates (one or more per PEM file)."""
    certificates: list[x509.Certificate] = []
    for path in paths:
        data = path.read_bytes()
        if b"-----BEGIN CERTIFICATE-----" in data:
            certificates.extend(x509.load_pem_x509_certificates(data))
        else:
            certificates.append(x509.load_der_x509_certificate(data))
    return certificates


def load_roots_dir(directory: Path) -> list[x509.Certificate]:
    patterns = ("*.pem", "*.crt", "*.cer", "*.der")
    files = sorted({p for pattern in patterns for p in directory.glob(pattern)})
    return load_certificates(files)


def _token_certificates(token: Any) -> list[tuple[Any, x509.Certificate]]:
    pairs = []
    for choice in token["content"]["certificates"] or []:
        if choice.name != "certificate":
            continue
        der = choice.chosen.dump()
        pairs.append((asn1_x509.Certificate.load(der), x509.load_der_x509_certificate(der)))
    return pairs


def _find_signer(token: Any, signer_info: Any) -> x509.Certificate | None:
    sid = signer_info["sid"]
    for asn1_cert, cert in _token_certificates(token):
        if sid.name == "issuer_and_serial_number":
            wanted = sid.chosen
            if (
                asn1_cert.serial_number == wanted["serial_number"].native
                and asn1_cert.issuer == wanted["issuer"]
            ):
                return cert
        elif sid.name == "subject_key_identifier":
            if asn1_cert.key_identifier == sid.chosen.native:
                return cert
    return None


def _attribute(signed_attrs: Any, name: str) -> Any:
    for attribute in signed_attrs:
        if attribute["type"].native == name:
            return attribute["values"][0]
    return None


def _weak(what: str, name: str) -> str:
    return f"weak digest algorithm {name} for {what} (SHA-256 or stronger is required)"


def _verify_signature(cert: x509.Certificate, signer_info: Any, data: bytes) -> None:
    algorithm = signer_info["signature_algorithm"]
    signature = signer_info["signature"].native
    hash_name = signer_info["digest_algorithm"]["algorithm"].native
    hash_alg = _HASHES[hash_name]()
    public_key = cert.public_key()
    kind = algorithm.signature_algo
    if isinstance(public_key, rsa.RSAPublicKey) and kind == "rsassa_pkcs1v15":
        public_key.verify(signature, data, padding.PKCS1v15(), hash_alg)
    elif isinstance(public_key, rsa.RSAPublicKey) and kind == "rsassa_pss":
        params = algorithm["parameters"]
        pss_hash = params["hash_algorithm"]["algorithm"].native
        if pss_hash not in DIGESTS:
            raise TsaError(_weak("the signature (RSASSA-PSS)", pss_hash))
        mgf_hash = _HASHES[params["mask_gen_algorithm"]["parameters"]["algorithm"].native]()
        pss = padding.PSS(padding.MGF1(mgf_hash), params["salt_length"].native)
        public_key.verify(signature, data, pss, _HASHES[pss_hash]())
    elif isinstance(public_key, ec.EllipticCurvePublicKey) and kind == "ecdsa":
        public_key.verify(signature, data, ec.ECDSA(hash_alg))
    else:
        raise TsaError(f"unsupported signature algorithm {kind}")


def _issuer_serial_problem(ess: Any, cert: x509.Certificate) -> str | None:
    """RFC 2634 / 5035: the optional issuerSerial of an ESSCertID must name the certificate."""
    issuer_serial = ess["issuer_serial"]
    if issuer_serial.native is None:
        return None
    if issuer_serial["serial_number"].native != cert.serial_number:
        return "ESSCertID issuerSerial does not match the TSA certificate"
    return None


def _check_ess_binding(signed_attrs: Any, cert: x509.Certificate) -> str | None:
    der = cert.public_bytes(Encoding.DER)
    v2 = _attribute(signed_attrs, "signing_certificate_v2")
    if v2 is not None:
        ess = v2["certs"][0]
        alg = ess["hash_algorithm"]["algorithm"].native or "sha256"
        if alg not in READABLE_DIGESTS:
            return f"ESSCertIDv2 uses an unsupported digest {alg}"
        if ess["cert_hash"].native != hashlib.new(READABLE_DIGESTS[alg], der).digest():
            return "ESSCertIDv2 does not match the TSA certificate"
        return _issuer_serial_problem(ess, cert)
    v1 = _attribute(signed_attrs, "signing_certificate")
    if v1 is not None:
        ess = v1["certs"][0]
        if ess["cert_hash"].native != hashlib.sha1(der).digest():  # noqa: S324 - ESSCertID is defined with SHA-1
            return "ESSCertID does not match the TSA certificate"
        return _issuer_serial_problem(ess, cert)
    return "no signing-certificate attribute (ESSCertID/ESSCertIDv2)"


def _check_eku(
    cert: x509.Certificate, *, require_critical: bool = True
) -> tuple[list[str], list[str]]:
    """RFC 3161 2.3: the only key purpose is id-kp-timeStamping, and the extension is critical.

    ``require_critical`` false (``tsa.require_critical_eku``, K6 in docs/kamusm.md) turns a
    non-critical extension into a warning; a wrong or missing purpose is always an error.
    Returns ``(errors, warnings)``.
    """
    try:
        extension = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return ["TSA certificate has no extended key usage"], []
    errors: list[str] = []
    warnings: list[str] = []
    if list(extension.value) != [ExtendedKeyUsageOID.TIME_STAMPING]:
        errors.append("TSA certificate extended key usage is not exactly id-kp-timeStamping")
    if not extension.critical:
        text = "TSA certificate extended key usage is not critical (RFC 3161 2.3)"
        (errors if require_critical else warnings).append(text)
    return errors, warnings


def _check_key_usage(cert: x509.Certificate) -> list[str]:
    """RFC 5280 4.2.1.3: a keyUsage extension of the TSA certificate must allow signing."""
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return []
    if usage.digital_signature or usage.content_commitment:
        return []
    return ["TSA certificate key usage does not allow signing (digitalSignature/nonRepudiation)"]


def _valid_at(cert: x509.Certificate, moment: datetime) -> bool:
    return cert.not_valid_before_utc <= moment <= cert.not_valid_after_utc


def fingerprint(certificate: x509.Certificate) -> str:
    """SHA-256 fingerprint of a certificate (lower-case hex of its DER encoding)."""
    return hashlib.sha256(certificate.public_bytes(Encoding.DER)).hexdigest()


def _check_chain(
    signer: x509.Certificate,
    pool: Sequence[x509.Certificate],
    roots: Sequence[x509.Certificate],
    moment: datetime,
) -> tuple[list[str], list[x509.Certificate]]:
    """The errors, and the chain from the TSA certificate to the pinned root it reaches (empty
    when it reaches none)."""
    root_ders = {root.public_bytes(Encoding.DER) for root in roots}
    candidates = [*pool, *roots]
    chain = [signer]
    current = signer
    below = 0  # intermediate CA certificates between the issuer and the TSA certificate
    for _ in range(MAX_CHAIN_DEPTH):
        if not _valid_at(current, moment):
            return [f"certificate {current.subject.rfc4514_string()} not valid at genTime"], []
        if current.public_bytes(Encoding.DER) in root_ders:
            return [], chain
        issuer = None
        for candidate in candidates:
            if candidate.subject != current.issuer or candidate == current:
                continue
            try:
                current.verify_directly_issued_by(candidate)
            except (ValueError, TypeError, InvalidSignature):
                continue
            issuer = candidate
            break
        if issuer is None:
            return ["certificate chain does not reach a pinned root certificate"], []
        problem = _issuer_problem(issuer, below)
        if problem is not None:
            return [problem], []
        below += 1
        chain.append(issuer)
        current = issuer
    return ["certificate chain too long"], []


def _issuer_problem(issuer: x509.Certificate, below: int) -> str | None:
    """RFC 5280 6.1.4: an issuer is a CA (basicConstraints cA), may sign certificates (keyUsage
    keyCertSign, when it has keyUsage) and allows the ``below`` intermediate CA certificates
    between it and the TSA certificate (pathLenConstraint)."""
    name = issuer.subject.rfc4514_string()
    try:
        constraints = issuer.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return f"issuer {name} has no basic constraints"
    if not constraints.ca:
        return f"issuer {name} is not a CA"
    try:
        usage = issuer.extensions.get_extension_for_class(x509.KeyUsage).value
        if not usage.key_cert_sign:
            return f"issuer {name} has no keyCertSign key usage"
    except x509.ExtensionNotFound:
        pass
    if constraints.path_length is not None and below > constraints.path_length:
        return f"the chain exceeds the pathLenConstraint of {name}"
    return None


RevocationLevel = Literal["fail", "warn", "info", "ok"]

#: The revocation codes and their levels, as the standalone verifier's ``check_revocation``
#: reports them (Wave 5 D10, ADR-0006: two verifiers, one verdict).
CRL_LEVELS: dict[str, RevocationLevel] = {
    "crl-revoked": "fail",
    "crl-revoked-later": "warn",
    "crl-hold": "warn",
    "crl-none": "info",
    "crl-stale": "info",
    "crl-ok": "ok",
}
_SEVERITY: tuple[RevocationLevel, ...] = ("ok", "info", "warn", "fail")
#: CRLReason codes (RFC 5280 5.3.1) the revocation check treats specially: certificateHold and
#: removeFromCRL (an entry that lifts a hold, in a delta list).
CRL_REASON_HOLD, CRL_REASON_REMOVE = 6, 8


@dataclass(frozen=True)
class Revocation:
    """What the revocation lists say about one certificate of a token's chain."""

    code: str
    #: The certificate's subject (RFC 4514).
    subject: str
    #: The revocation date (``crl-revoked``, ``crl-revoked-later``, ``crl-hold``) or the newest
    #: list's thisUpdate (``crl-ok``, ``crl-stale``); None for ``crl-none``.
    at: datetime | None = None

    @property
    def level(self) -> RevocationLevel:
        return CRL_LEVELS[self.code]


def check_revocation(
    chain: Sequence[x509.Certificate], crls: Sequence[Crl], moment: datetime
) -> list[Revocation]:
    """Look up each certificate of the chain (except the root) in ``crls``.

    The rules of the standalone verifier's ``check_revocation``: a list counts for a certificate
    when it names the certificate's issuer and the key of the issuer from the chain verifies its
    signature. Every such list is consulted (a CA may drop an expired certificate from later
    lists; removeFromCRL entries do not count). A revocation (any reason but certificateHold)
    dated at or before ``moment`` (the stamp time) is ``crl-revoked``, also when an earlier hold
    precedes it (C6). Otherwise the earliest entry decides: certificateHold is ``crl-hold``, a
    revocation after ``moment`` ``crl-revoked-later``. Without an entry, ``crl-ok`` needs a list
    issued at or after the stamp time (an older one cannot know about revocations in between:
    ``crl-stale``); no usable list is ``crl-none``.
    """
    findings = []
    for position, certificate in enumerate(chain[:-1]):
        issuer = chain[position + 1]
        name = certificate.issuer.public_bytes()
        usable = [
            crl for crl in crls if crl.issuer_name.public_bytes() == name and crl.signed_by(issuer)
        ]
        subject = certificate.subject.rfc4514_string()
        if not usable:
            findings.append(Revocation("crl-none", subject))
            continue
        serial = certificate.serial_number
        entries = sorted(
            (
                (crl.revoked[serial], crl.reasons.get(serial))
                for crl in usable
                if serial in crl.revoked and crl.reasons.get(serial) != CRL_REASON_REMOVE
            ),
            # Of two entries of the same date, one without a reason comes first, then by reason
            # code. Only entries after the stamp time still depend on this order (C6 decides
            # those at or before it): a revocation without a reason, or with a code below 6, comes
            # before a hold of its date.
            key=lambda entry: (entry[0], -1 if entry[1] is None else entry[1]),
        )
        latest = max(usable, key=lambda crl: crl.this_update)
        # C6: a revocation at or before the stamp time outranks an earlier hold.
        before = [entry for entry in entries if entry[1] != CRL_REASON_HOLD and entry[0] <= moment]
        if entries:
            revoked_at, reason = (before or entries)[0]
            if reason == CRL_REASON_HOLD:
                findings.append(Revocation("crl-hold", subject, revoked_at))
            elif revoked_at <= moment:
                findings.append(Revocation("crl-revoked", subject, revoked_at))
            else:
                findings.append(Revocation("crl-revoked-later", subject, revoked_at))
        elif latest.this_update >= moment:
            findings.append(Revocation("crl-ok", subject, latest.this_update))
        else:
            findings.append(Revocation("crl-stale", subject, latest.this_update))
    return findings


def token_chain(data: bytes, roots: Sequence[x509.Certificate]) -> list[x509.Certificate]:
    """The chain :func:`verify_token` checks a token's revocation along: its TSA certificate,
    the intermediates from the token and the pinned root it reaches at genTime (empty when the
    token cannot be read or reaches no root)."""
    try:
        token, container = parse_container(data)
        signer_infos = token["content"]["signer_infos"]
        signer = _find_signer(token, signer_infos[0]) if len(signer_infos) == 1 else None
        if signer is None:
            return []
        moment = _info(token, container, signer).gen_time
    except TsaError:
        return []
    pool = [cert for _, cert in _token_certificates(token)]
    return _check_chain(signer, pool, roots, moment)[1]


def _revocation_details(findings: Sequence[Revocation]) -> dict[str, str]:
    """``details["crl"]`` and its companions: the most severe finding (the first one of the
    chain among equals), or ``"not checked"`` when the chain has nothing below its root."""
    if not findings:
        return {"crl": "not checked"}
    worst = max(findings, key=lambda finding: _SEVERITY.index(finding.level))
    return {
        "crl": worst.code,
        "crl_level": worst.level,
        "crl_subject": worst.subject,
        "crl_at": worst.at.isoformat() if worst.at is not None else "",
    }


def _info(token: Any, container: str, signer: x509.Certificate | None) -> TokenInfo:
    tst, _ = tst_info(token)
    imprint = tst["message_imprint"]
    return TokenInfo(
        gen_time=tst["gen_time"].native.astimezone(UTC),
        serial=format(tst["serial_number"].native, "X"),
        policy=tst["policy"].dotted,
        tsa_name=signer.subject.rfc4514_string() if signer else "",
        tsa_cert_not_after=signer.not_valid_after_utc if signer else None,
        message_imprint_hex=imprint["hashed_message"].native.hex(),
        digest_alg=imprint["hash_algorithm"]["algorithm"].native,
        nonce=tst["nonce"].native,
        container=container,
    )


def inspect_token(data: bytes) -> TokenInfo:
    """Read what a token says, without any trust decision."""
    token, container = parse_container(data)
    signer_infos = token["content"]["signer_infos"]
    signer = _find_signer(token, signer_infos[0]) if len(signer_infos) else None
    return _info(token, container, signer)


def verify_token(
    data: bytes,
    *,
    roots: Sequence[x509.Certificate],
    file_path: Path | None = None,
    digest: bytes | None = None,
    now: datetime | None = None,
    require_critical_eku: bool = True,
    crls: Sequence[Crl] | None = None,
) -> VerifyResult:
    """Verify a token against a file (or a precomputed digest of it) and the pinned roots.

    The same rules as the standalone verifier that goes into evidence packages (ADR-0006: two
    independent implementations, one verdict): a token accepted here is never refused there.
    ``require_critical_eku`` false accepts a non-critical EKU extension with a warning.

    ``crls``: the revocation lists to check the chain against (:func:`check_revocation`). A
    token that otherwise verifies gets the most severe code in ``details["crl"]``, its level in
    ``details["crl_level"]``, the certificate in ``details["crl_subject"]`` and the date in
    ``details["crl_at"]``; an empty sequence gives ``crl-none``. Revocation never changes
    ``ok``, the errors or the root (legal grade stays the three-part test); with ``crls`` None,
    or for a token that does not verify, ``details["crl"]`` is ``"not checked"``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    try:
        token, container = parse_container(data)
        _, tst_raw = tst_info(token)
    except TsaError as exc:
        return VerifyResult(ok=False, errors=(str(exc),))

    signed = token["content"]
    if len(signed["signer_infos"]) != 1:
        return VerifyResult(ok=False, errors=("token must have exactly one signer",))
    signer_info = signed["signer_infos"][0]
    signer = _find_signer(token, signer_info)
    info = _info(token, container, signer)
    if signer is None:
        return VerifyResult(ok=False, errors=("TSA certificate not included (certReq)",), info=info)

    # 1. imprint ↔ file, with a collision-resistant hash
    alg = info.digest_alg
    if alg not in READABLE_DIGESTS:
        errors.append(f"unsupported imprint algorithm {alg}")
    elif alg not in DIGESTS:
        errors.append(_weak("the stamped digest (messageImprint)", alg))
    else:
        expected = (
            digest if digest is not None else file_digest(file_path, alg) if file_path else None
        )
        if expected is None:
            errors.append("no file or digest given to compare with the imprint")
        elif expected.hex() != info.message_imprint_hex:
            errors.append("message imprint does not match the file (file changed or wrong token)")

    # 2. signed attributes and signature
    signed_attrs = signer_info["signed_attrs"]
    if not len(signed_attrs):
        errors.append("signed attributes missing")
    else:
        names = [attribute["type"].dotted for attribute in signed_attrs]
        errors += [
            f"signed attribute {name} appears twice"
            for name in sorted({name for name in names if names.count(name) > 1})
        ]
        if _attribute(signed_attrs, "content_type") is None or (
            _attribute(signed_attrs, "content_type").native != "tst_info"
        ):
            errors.append("content-type attribute is not id-ct-TSTInfo")
        message_digest = _attribute(signed_attrs, "message_digest")
        hash_name = signer_info["digest_algorithm"]["algorithm"].native
        if hash_name not in READABLE_DIGESTS:
            errors.append(f"unsupported digest algorithm {hash_name}")
        elif hash_name not in DIGESTS:
            errors.append(_weak("the signature (signerInfo)", hash_name))
        elif message_digest is None or (
            message_digest.native != hashlib.new(READABLE_DIGESTS[hash_name], tst_raw).digest()
        ):
            errors.append("message-digest attribute does not match TSTInfo")
        to_verify = b"\x31" + signed_attrs.dump()[1:]
        try:
            _verify_signature(signer, signer_info, to_verify)
        except (InvalidSignature, TsaError, KeyError, ValueError) as exc:
            errors.append(f"signature invalid: {exc.__class__.__name__}")
        binding_error = _check_ess_binding(signed_attrs, signer)
        if binding_error:
            errors.append(binding_error)

    # 3. certificate purpose and chain
    eku_errors, eku_warnings = _check_eku(signer, require_critical=require_critical_eku)
    errors += eku_errors + _check_key_usage(signer)
    warnings += eku_warnings
    pool = [cert for _, cert in _token_certificates(token)]
    chain_errors, chain = _check_chain(signer, pool, roots, info.gen_time)
    errors += chain_errors
    anchor = chain[-1] if chain else None

    # 4. time sanity
    if info.gen_time > (now or datetime.now(UTC)) + CLOCK_SKEW:
        errors.append("genTime is in the future")

    details = {
        "container": container,
        "root": anchor.subject.rfc4514_string() if anchor is not None else "",
        # Which pinned root the chain reached: legal grade is decided from this fingerprint
        # (tsa.legal_root_fingerprints), never from what a manifest says about itself.
        "root_sha256": fingerprint(anchor) if anchor is not None and not errors else "",
        "crl": "not checked",
    }
    # 5. revocation, of a token that verifies (as the standalone verifier does)
    if crls is not None and chain and not errors:
        details.update(_revocation_details(check_revocation(chain, crls, info.gen_time)))
    return VerifyResult(
        ok=not errors,
        errors=tuple(errors),
        info=info,
        details=details,
        warnings=tuple(warnings),
    )
