"""The standalone verifier's own DER, X.509, CMS and signature code, checked against
``cryptography``, ``asn1crypto`` and ARCILOGGER's core verifier (which uses those libraries).

The standalone verifier is a clean-room implementation that must reach the same verdict as
``arcilogger_core.tsa.verify`` on every token; the agreement tests below are the proof.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from asn1crypto import algos, cms, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.canonical import canonical_json
from arcilogger_core.canonical import is_canonical as core_is_canonical
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.tsa.asn1 import TimeStampResp, build_request
from arcilogger_core.tsa.mock import MockFailure, MockTsa
from arcilogger_core.tsa.verify import verify_token

DER = serialization.Encoding.DER
DATA = b'{"day":"2026-10-12","schema":"arcilogger.manifest/1"}'
CURVES = {
    "1.2.840.10045.3.1.7": ec.SECP256R1(),
    "1.3.132.0.34": ec.SECP384R1(),
    "1.3.132.0.35": ec.SECP521R1(),
}
HASHES: dict[str, hashes.HashAlgorithm] = {
    "sha1": hashes.SHA1(),  # noqa: S303 - RFC 8017 still allows it; tested for completeness
    "sha256": hashes.SHA256(),
    "sha384": hashes.SHA384(),
    "sha512": hashes.SHA512(),
}
PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey


# -- certificates for the tests -------------------------------------------------------------------


def _name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def make_certificate(
    subject: str,
    key: PrivateKey,
    issuer: tuple[x509.Certificate, PrivateKey] | None = None,
    *,
    ca: bool | None = None,
    eku: list[x509.ObjectIdentifier] | None = None,
    eku_critical: bool = True,
    valid_from: datetime | None = None,
    valid_days: int = 365,
    ski: bool = True,
) -> x509.Certificate:
    start = valid_from or datetime.now(UTC) - timedelta(days=1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer[0].subject if issuer else _name(subject))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=valid_days))
    )
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), eku_critical)
    if ski:
        builder = builder.add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        )
    return builder.sign(issuer[1] if issuer else key, hashes.SHA256())


def standalone(certificate: x509.Certificate) -> sv.Certificate:
    return sv.parse_certificate(certificate.public_bytes(DER))


def root_key(tsa: MockTsa) -> PrivateKey:
    key = serialization.load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(key, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey)
    return key


def signer_for(tsa: MockTsa, key_type: str, **options: Any) -> tuple[x509.Certificate, PrivateKey]:
    key: PrivateKey = (
        ec.generate_private_key(ec.SECP256R1())
        if key_type == "ec"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    certificate = make_certificate(
        "Test TSA",
        key,
        (tsa.root_certificate, root_key(tsa)),
        ca=False,
        **options,
    )
    return certificate, key


@pytest.fixture(scope="module", params=["ec", "rsa"])
def tsa(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> MockTsa:
    return MockTsa(tmp_path_factory.mktemp(f"tsa-{request.param}"), key_type=request.param)


def stamp(tsa: MockTsa, data: bytes = DATA, *, cert_req: bool = True) -> bytes:
    request, _ = build_request(hashlib.sha256(data).digest(), "sha256")
    if not cert_req:
        parsed = tsp.TimeStampReq.load(request)
        parsed["cert_req"] = False
        request = parsed.dump(force=True)
    return tsa.respond(request)


def both(
    tsa: MockTsa, token: bytes, data: bytes = DATA, roots: list[x509.Certificate] | None = None
) -> sv.TimestampResult:
    """Verify with both verifiers; they must agree. Returns the standalone result."""
    trusted = roots if roots is not None else [tsa.root_certificate]
    core = verify_token(token, roots=trusted, digest=hashlib.sha256(data).digest())
    mine = sv.verify_timestamp(token, data, [standalone(c) for c in trusted])
    assert mine.ok == core.ok, (core.errors, [e.code for e in mine.errors])
    return mine


# =================================================================================================
# Verdict agreement with the core verifier
# =================================================================================================


def test_valid_tokens(tsa: MockTsa) -> None:
    result = both(tsa, stamp(tsa))
    assert result.ok
    assert result.container == "TimeStampResp"
    assert result.policy == "2.999.5651.1"
    assert result.imprint == hashlib.sha256(DATA).digest()
    assert result.accuracy == "1 s"
    assert result.nonce is not None
    assert "Mock TSA" in result.tsa_name
    assert result.signer is not None
    assert result.root is not None
    assert result.root.der == tsa.root_certificate.public_bytes(DER)
    assert result.chain[0].subject_text.startswith("CN=ARCILOGGER Mock TSA")
    assert result.signature.startswith("ECDSA P-256" if "ec" in tsa.state_dir.name else "RSA 2048")


def test_rsa_pss_tokens(tmp_path: Path) -> None:
    tsa = MockTsa(tmp_path, key_type="rsa")
    tsa.rsa_pss = True
    result = both(tsa, stamp(tsa))
    assert result.ok
    assert result.signature == "RSA 2048 bit PSS / sha256"


def test_bare_time_stamp_token_and_ber_encoding(tsa: MockTsa) -> None:
    response = stamp(tsa)
    bare = tsp.TimeStampResp.load(response)["time_stamp_token"].dump()
    result = both(tsa, bare)
    assert result.ok
    assert result.container == "TimeStampToken"
    # BER with indefinite lengths around the response and the token (some CMS producers do this)
    outer = sv.parse_single(response)
    status, token = outer.children()
    ber_token = b"\x30\x80" + token.value + b"\x00\x00"
    ber = b"\x30\x80" + status.raw + ber_token + b"\x00\x00"
    assert both(tsa, ber).ok


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("bad_signature", "token-signature"),
        ("expired_cert", "token-cert-validity"),
        ("other_data", "token-imprint"),
        ("untrusted_root", "token-chain"),
        ("no_certificate", "token-no-cert"),
        ("rejection", "token-status"),
        ("garbage", "token-format"),
        ("truncated", "token-format"),
    ],
)
def test_failures_agree(tsa: MockTsa, tmp_path: Path, change: str, code: str) -> None:
    data, roots = DATA, [tsa.root_certificate]
    if change == "bad_signature":
        tsa.failure = MockFailure.BAD_SIGNATURE
    elif change == "expired_cert":
        tsa.failure = MockFailure.EXPIRED_CERT
    elif change == "rejection":
        tsa.failure = MockFailure.REJECTION
    try:
        token = stamp(tsa, cert_req=change != "no_certificate")
    finally:
        tsa.failure = MockFailure.NONE
    if change == "other_data":
        data = DATA + b" "
    elif change == "untrusted_root":
        roots = [MockTsa(tmp_path / "other", key_type="ec").root_certificate]
    elif change == "garbage":
        token = b"not a time stamp"
    elif change == "truncated":
        token = token[: len(token) // 2]
    result = both(tsa, token, data, roots)
    assert not result.ok
    assert [error.code for error in result.errors] == [code]
    assert result.errors[0].tr
    assert result.errors[0].en


@pytest.mark.parametrize(
    ("options", "messages"),
    [
        ({"eku": None}, ["no extended key usage"]),
        ({"eku": [ExtendedKeyUsageOID.TIME_STAMPING], "eku_critical": False}, ["not critical"]),
        (
            {"eku": [ExtendedKeyUsageOID.TIME_STAMPING, ExtendedKeyUsageOID.SERVER_AUTH]},
            ["not exactly id-kp-timeStamping"],
        ),
        (
            {"eku": [ExtendedKeyUsageOID.SERVER_AUTH], "eku_critical": False},
            ["not exactly", "not critical"],
        ),
    ],
)
def test_time_stamping_key_purpose_is_required(
    tsa: MockTsa, options: dict[str, Any], messages: list[str]
) -> None:
    key_type = "ec" if "ec" in tsa.state_dir.name else "rsa"
    tsa.signer = signer_for(tsa, key_type, **options)
    try:
        token = stamp(tsa)
    finally:
        tsa.signer = None
    result = both(tsa, token)
    assert [error.code for error in result.errors] == ["token-eku"] * len(messages)
    for error, message in zip(result.errors, messages, strict=True):
        assert message in error.en


def test_future_gen_time(tmp_path: Path) -> None:
    ahead = MockTsa(tmp_path, key_type="ec", clock=lambda: datetime.now(UTC) + timedelta(days=1))
    result = both(ahead, stamp(ahead))
    assert [error.code for error in result.errors] == ["token-future"]
    later = sv.verify_timestamp(
        stamp(ahead),
        DATA,
        [standalone(ahead.root_certificate)],
        now=datetime.now(UTC) + timedelta(days=2),
    )
    assert later.ok


def test_a_certificate_valid_only_after_the_stamp_is_rejected(tmp_path: Path) -> None:
    tsa = MockTsa(tmp_path, key_type="ec")
    tsa.signer = signer_for(
        tsa,
        "ec",
        eku=[ExtendedKeyUsageOID.TIME_STAMPING],
        valid_from=datetime.now(UTC) + timedelta(hours=1),
    )
    result = both(tsa, stamp(tsa))
    assert [error.code for error in result.errors] == ["token-cert-validity"]


# =================================================================================================
# Signatures, compared with cryptography
# =================================================================================================


@pytest.mark.parametrize("curve_oid", list(CURVES))
def test_ecdsa_on_every_supported_curve(curve_oid: str) -> None:
    curve = sv.CURVES[curve_oid]
    key = ec.generate_private_key(CURVES[curve_oid])
    numbers = key.public_key().public_numbers()
    secret = key.private_numbers().private_value
    assert sv.on_curve(curve, curve.gx, curve.gy)
    assert sv.ec_multiply(curve, secret, curve.gx, curve.gy) == (numbers.x, numbers.y)
    assert sv.ec_multiply(curve, curve.n, curve.gx, curve.gy) is None  # n * G = infinity
    public = sv.EcKey(curve, numbers.x, numbers.y)
    for name in ("sha256", "sha384", "sha512"):
        signature = key.sign(b"message", ec.ECDSA(HASHES[name]))
        assert sv.ecdsa_verify(public, name, b"message", signature)
        assert not sv.ecdsa_verify(public, name, b"messagE", signature)
    compressed = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint
    )
    assert sv._decode_point(curve, compressed) == (numbers.x, numbers.y)
    uncompressed = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert sv._decode_point(curve, uncompressed) == (numbers.x, numbers.y)
    assert sv._decode_point(curve, b"\x04" + b"\x00" * (2 * curve.size)) is None  # not on the curve
    assert sv._decode_point(curve, b"\x05" + uncompressed[1:]) is None


def test_ecdsa_rejects_malformed_signatures() -> None:
    curve = sv.CURVES["1.2.840.10045.3.1.7"]
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    public = sv.EcKey(curve, numbers.x, numbers.y)
    assert not sv.ecdsa_verify(public, "sha256", b"m", b"\x30\x00")
    zero = sv.encode(0x30, sv.encode(sv.INTEGER, b"\x00") + sv.encode(sv.INTEGER, b"\x01"))
    assert not sv.ecdsa_verify(public, "sha256", b"m", zero)
    assert not sv.ecdsa_verify(public, "sha256", b"m", b"garbage")


@pytest.mark.parametrize("bits", [2048, 3072])
def test_rsa_pkcs1_v15_and_pss(bits: int) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    numbers = key.public_key().public_numbers()
    public = sv.RsaKey(numbers.n, numbers.e)
    for name, algorithm in HASHES.items():
        signature = key.sign(b"data", padding.PKCS1v15(), algorithm)
        assert sv.rsa_pkcs1v15_verify(public, name, b"data", signature)
        assert not sv.rsa_pkcs1v15_verify(public, name, b"dat4", signature)
        for salt in sorted({0, 20, 32, algorithm.digest_size}):
            signature = key.sign(b"data", padding.PSS(padding.MGF1(algorithm), salt), algorithm)
            assert sv.rsa_pss_verify(public, name, name, salt, b"data", signature)
            assert not sv.rsa_pss_verify(public, name, name, salt + 1, b"data", signature)
            assert not sv.rsa_pss_verify(public, name, name, salt, b"dat4", signature)
    assert not sv.rsa_pkcs1v15_verify(public, "sha256", b"data", b"\x01" * 10)  # wrong length
    too_big = (numbers.n + 1).to_bytes((bits + 7) // 8, "big")
    assert not sv.rsa_pkcs1v15_verify(public, "sha256", b"data", too_big)
    assert not sv.rsa_pss_verify(public, "sha256", "sha256", 32, b"data", too_big)
    assert not sv.rsa_pss_verify(public, "sha256", "sha256", 32, b"data", b"\x01")


def test_rsa_digest_info_without_null_parameters_is_accepted() -> None:
    """RFC 8017 allows the DigestInfo parameters to be absent; some signers omit them."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private, public = key.private_numbers(), key.public_key().public_numbers()
    info = sv._digest_info("sha256", hashlib.sha256(b"data").digest(), with_null=False)
    k = (public.n.bit_length() + 7) // 8
    encoded = b"\x00\x01" + b"\xff" * (k - len(info) - 3) + b"\x00" + info
    signature = pow(int.from_bytes(encoded, "big"), private.d, public.n).to_bytes(k, "big")
    assert sv.rsa_pkcs1v15_verify(sv.RsaKey(public.n, public.e), "sha256", b"data", signature)


def test_signature_dispatch_by_algorithm_identifier() -> None:
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_public = sv.RsaKey(rsa_key.public_key().public_numbers().n, 65537)
    sha256_rsa = sv.Algorithm("1.2.840.113549.1.1.11", None)
    assert sv.verify_signature(
        rsa_public, sha256_rsa, b"x", rsa_key.sign(b"x", padding.PKCS1v15(), hashes.SHA256())
    ).startswith("RSA 2048")
    # plain rsaEncryption needs the digest from the CMS SignerInfo
    plain = sv.Algorithm(sv.RSA_ENCRYPTION, None)
    signature = rsa_key.sign(b"x", padding.PKCS1v15(), hashes.SHA384())
    assert "sha384" in sv.verify_signature(rsa_public, plain, b"x", signature, digest_hint="sha384")
    with pytest.raises(sv.CheckError) as missing:
        sv.verify_signature(rsa_public, plain, b"x", signature)
    assert missing.value.code == "signature"
    # PSS parameters, explicit and default (SHA-1, MGF1-SHA-1, salt 20)
    params = algos.RSASSAPSSParams(
        {
            "hash_algorithm": {"algorithm": "sha512"},
            "mask_gen_algorithm": {"algorithm": "mgf1", "parameters": {"algorithm": "sha512"}},
            "salt_length": 64,
        }
    ).dump()
    pss = sv.Algorithm(sv.RSASSA_PSS, sv.parse_single(params))
    signature = rsa_key.sign(b"x", padding.PSS(padding.MGF1(hashes.SHA512()), 64), hashes.SHA512())
    assert sv.verify_signature(rsa_public, pss, b"x", signature) == "RSA 2048 bit PSS / sha512"
    sha1 = hashes.SHA1()  # noqa: S303 - the RFC 4055 default parameters use SHA-1
    default = rsa_key.sign(b"x", padding.PSS(padding.MGF1(sha1), 20), sha1)
    assert sv.verify_signature(rsa_public, sv.Algorithm(sv.RSASSA_PSS, None), b"x", default)
    # ECDSA with the bare id-ecPublicKey OID uses the CMS digest too
    ec_key = ec.generate_private_key(ec.SECP384R1())
    point = ec_key.public_key().public_numbers()
    ec_public = sv.EcKey(sv.CURVES["1.3.132.0.34"], point.x, point.y)
    signature = ec_key.sign(b"x", ec.ECDSA(hashes.SHA256()))
    assert (
        sv.verify_signature(
            ec_public, sv.Algorithm(sv.EC_PUBLIC_KEY, None), b"x", signature, "sha256"
        )
        == "ECDSA P-384 / sha256"
    )
    # wrong key type, unknown algorithm, unsupported PSS options
    for key, algorithm in (
        (ec_public, sha256_rsa),
        (rsa_public, sv.Algorithm("1.2.840.10045.4.3.2", None)),
        (ec_public, pss),
    ):
        with pytest.raises(sv.CheckError) as wrong:
            sv.verify_signature(key, algorithm, b"x", signature)
        assert wrong.value.code == "signature"
    with pytest.raises(sv.CheckError) as unknown:
        sv.verify_signature(rsa_public, sv.Algorithm("1.2.3.4", None), b"x", b"")
    assert unknown.value.code == "algorithm"
    bad_mgf = algos.RSASSAPSSParams(
        {"mask_gen_algorithm": {"algorithm": "mgf1", "parameters": {"algorithm": "md5"}}}
    ).dump()
    with pytest.raises(sv.CheckError):
        sv.verify_signature(
            rsa_public, sv.Algorithm(sv.RSASSA_PSS, sv.parse_single(bad_mgf)), b"x", b""
        )
    bad_trailer = sv.encode(0x30, sv.encode(0xA3, sv.encode(sv.INTEGER, b"\x02")))
    with pytest.raises(sv.CheckError):
        sv.verify_signature(
            rsa_public, sv.Algorithm(sv.RSASSA_PSS, sv.parse_single(bad_trailer)), b"x", b""
        )


# =================================================================================================
# Certificates, chains, ESS binding, CRLs
# =================================================================================================


def test_certificate_fields_match_cryptography() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    turkish = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kamu Sertifikasyon Merkezi"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Zaman Damgası Kök Sertifikası Sağlayıcısı"),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(turkish)
        .issuer_name(turkish)
        .public_key(key.public_key())
        .serial_number(0x1234567890ABCDEF)
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2051, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .sign(key, hashes.SHA384())
    )
    parsed = standalone(certificate)
    assert parsed.serial == 0x1234567890ABCDEF
    assert (
        parsed.subject_text
        == "CN=Zaman Damgası Kök Sertifikası Sağlayıcısı, O=Kamu Sertifikasyon Merkezi, C=TR"
    )
    assert parsed.issuer == parsed.subject
    assert parsed.not_before == certificate.not_valid_before_utc
    assert parsed.not_after == certificate.not_valid_after_utc  # 2051: GeneralizedTime
    assert parsed.key_description == "EC P-256"
    assert parsed.is_ca() is True
    ski = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    assert parsed.key_identifier() == ski
    assert parsed.fingerprint() == certificate.fingerprint(hashes.SHA256()).hex().upper()
    assert parsed.algorithm.oid == "1.2.840.10045.4.3.3"
    assert (
        sv.verify_signature(parsed.key, parsed.algorithm, parsed.tbs, parsed.signature)
        == "ECDSA P-256 / sha384"
    )
    plain = standalone(make_certificate("leaf", key, ski=False))
    assert plain.is_ca() is None
    assert plain.key_identifier() is None
    loaded = sv.load_certificates(certificate.public_bytes(serialization.Encoding.PEM) * 2)
    assert [c.der for c in loaded] == [parsed.der] * 2
    assert sv.load_certificates(parsed.der)[0].der == parsed.der
    with pytest.raises(sv.DerError):
        sv.pem_blocks(
            b"-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n", "CERTIFICATE"
        )


def test_unsupported_keys_are_described() -> None:
    p224 = ec.generate_private_key(ec.SECP224R1())
    parsed = standalone(make_certificate("p224", p224))
    assert parsed.key is None
    assert "unsupported curve" in parsed.key_description


def test_chain_through_an_intermediate_and_its_failures() -> None:
    now = datetime.now(UTC)
    root_key_ = ec.generate_private_key(ec.SECP384R1())
    root = make_certificate("Root", root_key_, ca=True)
    middle_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    middle = make_certificate("Intermediate", middle_key, (root, root_key_), ca=True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = make_certificate("Leaf", leaf_key, (middle, middle_key), ca=False)
    chain, error = sv.build_chain(standalone(leaf), [standalone(middle)], [standalone(root)], now)
    assert error is None
    assert [c.subject_text for c in chain] == [
        "CN=Leaf, C=TR",
        "CN=Intermediate, C=TR",
        "CN=Root, C=TR",
    ]
    _, error = sv.build_chain(standalone(leaf), [], [standalone(root)], now)
    assert error is not None
    assert error.code == "token-chain"
    _, error = sv.build_chain(
        standalone(leaf), [standalone(middle)], [standalone(root)], now + timedelta(days=400)
    )
    assert error is not None
    assert error.code == "token-cert-validity"
    for ca in (None, False):  # an issuer that is not a CA
        not_ca = make_certificate("Intermediate", middle_key, (root, root_key_), ca=ca)
        _, error = sv.build_chain(standalone(leaf), [standalone(not_ca)], [standalone(root)], now)
        assert error is not None
        assert error.code == "token-not-ca"
    # two self-issued CA certificates with the same name and key issue each other for ever
    loop_key = ec.generate_private_key(ec.SECP256R1())
    first, second = (standalone(make_certificate("Loop", loop_key, ca=True)) for _ in range(2))
    _, error = sv.build_chain(first, [first, second], [standalone(root)], now)
    assert error is not None
    assert error.code == "token-chain"
    assert "too long" in error.en


def test_ess_certificate_identifiers() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = standalone(make_certificate("TSA", key))
    other = standalone(make_certificate("Other", key))

    def attribute(
        version: int, digest: bytes, *, algorithm: str | None = None, serial: int | None = None
    ) -> dict[str, list[sv.Tlv]]:
        cert_id: dict[str, object] = {"cert_hash": digest}
        if algorithm is not None:
            cert_id["hash_algorithm"] = {"algorithm": algorithm}
        if serial is not None:
            cert_id["issuer_serial"] = {"issuer": [], "serial_number": serial}
        value = (tsp.SigningCertificateV2 if version == 2 else tsp.SigningCertificate)(
            {"certs": [cert_id]}
        )
        oid = sv.ATTR_SIGNING_CERTIFICATE_V2 if version == 2 else sv.ATTR_SIGNING_CERTIFICATE
        return {oid: [sv.parse_single(value.dump())]}

    sha1 = hashlib.sha1(certificate.der).digest()  # noqa: S324 - ESSCertID is defined with SHA-1
    assert sv._check_ess(attribute(1, sha1), certificate) is None
    assert sv._check_ess(attribute(1, sha1, serial=certificate.serial), certificate) is None
    assert (
        sv._check_ess(attribute(2, hashlib.sha256(certificate.der).digest()), certificate) is None
    )
    sha512 = hashlib.sha512(certificate.der).digest()
    assert sv._check_ess(attribute(2, sha512, algorithm="sha512"), certificate) is None
    for wrong in (
        attribute(1, hashlib.sha1(other.der).digest()),  # noqa: S324
        attribute(2, sha512, algorithm="sha512", serial=certificate.serial + 1),
        {},
    ):
        error = sv._check_ess(wrong, certificate)
        assert error is not None
        assert error.code == "token-ess"


def test_signer_found_by_key_identifier() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = standalone(make_certificate("TSA", key))
    ski = certificate.key_identifier()
    assert ski is not None
    assert (
        sv._find_signer([certificate], sv.parse_single(b"\x80" + bytes([len(ski)]) + ski))
        is certificate
    )
    assert sv._find_signer([certificate], sv.parse_single(b"\x80\x01\x00")) is None


def test_crl_parsing_and_revocation_lookup() -> None:
    root_key_ = ec.generate_private_key(ec.SECP256R1())
    root = make_certificate("Root", root_key_, ca=True)
    leaf = make_certificate(
        "Leaf", ec.generate_private_key(ec.SECP256R1()), (root, root_key_), ca=False
    )
    now = datetime.now(UTC)
    revoked_at = now - timedelta(days=1)
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(root.subject)
        .last_update(now - timedelta(hours=1))
        .next_update(now + timedelta(days=1))
        .add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(leaf.serial_number)
            .revocation_date(revoked_at)
            .build()
        )
        .sign(root_key_, hashes.SHA256())
    )
    parsed = sv.parse_crl(crl.public_bytes(DER))
    assert parsed.issuer == standalone(root).subject
    assert parsed.next_update is not None
    assert parsed.revoked == {leaf.serial_number: revoked_at.replace(microsecond=0)}
    chain = [standalone(leaf), standalone(root)]
    assert [f[1] for f in sv.check_revocation(chain, [parsed], now)] == ["crl-revoked"]
    assert [f[1] for f in sv.check_revocation(chain, [parsed], now - timedelta(days=2))] == [
        "crl-revoked-later"
    ]
    assert [f[1] for f in sv.check_revocation(chain, [], now)] == ["crl-none"]
    empty = sv.parse_crl(
        x509.CertificateRevocationListBuilder()
        .issuer_name(root.subject)
        .last_update(now)
        .next_update(now + timedelta(days=1))
        .sign(root_key_, hashes.SHA256())
        .public_bytes(DER)
    )
    earlier = now - timedelta(minutes=1)
    assert [f[1] for f in sv.check_revocation(chain, [empty], earlier)] == ["crl-ok"]
    # a CRL issued before the stamp cannot know whether the certificate was revoked then
    assert [f[1] for f in sv.check_revocation(chain, [empty], now + timedelta(hours=1))] == [
        "crl-stale"
    ]
    # every usable CRL counts: a newer list that dropped the entry does not hide it
    assert [f[1] for f in sv.check_revocation(chain, [parsed, empty], earlier)] == ["crl-revoked"]


def _crl_with_reason(
    issuer: x509.Certificate, key: ec.EllipticCurvePrivateKey, serial: int, reason: x509.ReasonFlags
) -> sv.Crl:
    now = datetime.now(UTC)
    entry = (
        x509.RevokedCertificateBuilder()
        .serial_number(serial)
        .revocation_date(now - timedelta(days=1))
        .add_extension(x509.CRLReason(reason), critical=False)
        .build()
    )
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.subject)
        .last_update(now)
        .next_update(now + timedelta(days=1))
        .add_revoked_certificate(entry)
        .sign(key, hashes.SHA256())
    )
    return sv.parse_crl(crl.public_bytes(DER))


def test_crl_reason_codes() -> None:
    """certificateHold is a warning to examine, removeFromCRL (delta CRLs) cancels an entry."""
    root_key_ = ec.generate_private_key(ec.SECP256R1())
    root = make_certificate("Root", root_key_, ca=True)
    leaf = make_certificate(
        "Leaf", ec.generate_private_key(ec.SECP256R1()), (root, root_key_), ca=False
    )
    chain = [standalone(leaf), standalone(root)]
    moment = datetime.now(UTC) - timedelta(minutes=1)
    for reason, code in (
        (x509.ReasonFlags.certificate_hold, "crl-hold"),
        (x509.ReasonFlags.remove_from_crl, "crl-ok"),
        (x509.ReasonFlags.key_compromise, "crl-revoked"),
    ):
        crl = _crl_with_reason(root, root_key_, leaf.serial_number, reason)
        assert crl.reasons[leaf.serial_number] in (1, 6, 8)
        assert [f[1] for f in sv.check_revocation(chain, [crl], moment)] == [code], reason
    # a CRL whose signature algorithm parameters are malformed is unusable, not a crash
    bad = sv.Crl(
        tbs=b"",
        algorithm=sv.Algorithm(sv.RSASSA_PSS, sv.parse_single(b"\x30\x02\xa0\x00")),
        signature=b"",
        issuer=standalone(leaf).issuer,
        this_update=moment,
        next_update=None,
        revoked={},
    )
    assert [f[1] for f in sv.check_revocation(chain, [bad], moment)] == ["crl-none"]


# =================================================================================================
# DER details, canonical JSON, reading packages
# =================================================================================================


def test_der_decoding_details() -> None:
    # long-form length and a high tag number
    long_value = b"x" * 300
    node = sv.parse_single(b"\x04\x82\x01\x2c" + long_value)
    assert sv.to_octets(node) == long_value
    high = sv.parse_single(b"\x9f\x81\x00\x01\xaa")
    assert (high.cls, high.tag, high.value) == (sv.CONTEXT, 128, b"\xaa")
    # BER: indefinite length and an OCTET STRING split into pieces
    ber = sv.parse_single(b"\x24\x80\x04\x02ab\x04\x01c\x00\x00")
    assert sv.to_octets(ber) == b"abc"
    nested = sv.parse_single(b"\x30\x80\x30\x80\x02\x01\x05\x00\x00\x00\x00")
    assert sv.to_int(nested.children()[0].children()[0]) == 5
    assert sv.to_int(sv.parse_single(b"\x02\x01\xff")) == -1
    assert sv.to_bool(sv.parse_single(b"\x01\x01\xff")) is True
    for bad in (
        b"",  # no tag
        b"\x30",  # no length
        b"\x04\x05abc",  # truncated
        b"\x04\x89" + b"\x01" * 9,  # length of 9 bytes
        b"\x04\x80abc",  # indefinite length on a primitive
        b"\x02\x01\x01\x00",  # trailing byte
        b"\x9f\xff\xff\xff\xff\xff\x7f\x00",  # absurd tag number
    ):
        with pytest.raises(sv.DerError):
            sv.parse_single(bad)
    deep = b"\x30\x80" * 70 + b"\x00\x00" * 70
    with pytest.raises(sv.DerError, match="deep"):
        sv.parse_single(deep)
    with pytest.raises(sv.DerError):
        sv.parse_single(b"\x02\x01\x01").children()
    with pytest.raises(sv.DerError):
        sv.to_int(sv.parse_single(b"\x02\x00"))
    with pytest.raises(sv.DerError):
        sv.to_bits(sv.parse_single(b"\x03\x02\x01\x80"))  # unused bits
    with pytest.raises(sv.DerError):
        sv.seq(sv.parse_single(b"\x30\x00"), "x", 1)
    with pytest.raises(sv.DerError):
        sv.to_oid(sv.parse_single(b"\x06\x01\x81"))


@pytest.mark.parametrize(
    "oid", ["2.999.5651.1", "1.2.840.113549.1.9.16.2.47", "0.9.2342", "1.3.6.1.5.5.7.3.8"]
)
def test_object_identifiers_round_trip(oid: str) -> None:
    assert sv.to_oid(sv.parse_single(sv.encode_oid(oid))) == oid


def test_times_and_strings() -> None:
    def time_of(tag: int, text: str) -> datetime:
        return sv.to_time(sv.parse_single(bytes([tag, len(text)]) + text.encode()))

    assert time_of(23, "491231235959Z") == datetime(2049, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert time_of(23, "500101000000Z") == datetime(1950, 1, 1, tzinfo=UTC)
    assert time_of(23, "5001010000Z") == datetime(1950, 1, 1, tzinfo=UTC)
    assert time_of(24, "20261012211005.123Z") == datetime(
        2026, 10, 12, 21, 10, 5, 123000, tzinfo=UTC
    )
    for tag, text in (
        (24, "20261312000000Z"),
        (24, "20261012000000+0300"),
        (23, "2610122110Z+"),
        (4, "x"),
    ):
        with pytest.raises(sv.DerError):
            time_of(tag, text)
    assert sv.to_text(sv.parse_single(b"\x1e\x04\x00\xc7\x00\x31")) == "Ç1"  # BMPString
    assert sv.to_text(sv.parse_single(b"\x1c\x04\x00\x00\x01\x5f")) == "ş"  # UniversalString
    assert sv.to_text(sv.parse_single(b"\x14\x01\xfc")) == "ü"  # T61String read as Latin-1
    assert "unreadable" in sv.name_text(b"\x30\x03\x31\x01\x00")


def test_canonical_form_agrees_with_core() -> None:
    documents = [
        {"b": 1, "a": [True, False, None], "ç": "ğüşıöç", "€": "€", "😀": "😀"},
        {"text": 'quote " backslash \\ control \u0001 tab \t', "n": -(2**53 - 1)},
        {"nested": {"z": {"y": []}}, "": ""},
    ]
    for document in documents:
        data = canonical_json(document)
        assert sv.is_canonical(data)
        assert core_is_canonical(data)
        loose = json.dumps(document, indent=1).encode()
        assert sv.is_canonical(loose) == core_is_canonical(loose) is False
    for odd in (b'{"a":1.5}', b'{"a":9007199254740993}', b"\xff", b'"\\ud800"', b"[1,2] ", b"{"):
        assert sv.is_canonical(odd) == core_is_canonical(odd) is False


def test_package_readers(tmp_path: Path) -> None:
    for name, safe in (
        ("vault/2026/10/12/x.log.gz", True),
        ("paket.json", True),
        ("../x", False),
        ("/etc/passwd", False),
        ("a\\b", False),
        ("C:x", False),
        ("a//b", False),
        ("./a", False),
        ("", False),
    ):
        assert sv.safe_name(name) is safe, name
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").write_bytes(b"hello")
    folder = sv.FolderPackage(tmp_path)
    with pytest.raises(OSError, match="invalid file name"):
        folder.open("../outside")
    assert folder.names() == ["a/b.txt"]
    assert folder.read("a/b.txt") == b"hello"
    with pytest.raises(sv.PackageError):
        sv.ZipPackage(tmp_path / "a" / "b.txt")


def test_json_output_of_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "paket.json").write_bytes(canonical_json({"schema": "other", "files": []}))
    assert sv.main([str(tmp_path / "x"), "--json"]) == 1  # no roots, no days: nothing verified
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is False
    assert report["mode"] == "package"
    fails = {f["code"] for f in report["findings"] if f["level"] == "fail"}
    assert fails == {"root-none", "nothing-verified"}
    assert {f["code"] for f in report["findings"] if f["level"] == "warn"} == {"index-schema"}
    assert report["counts"]["fail"] == 2


# =================================================================================================
# Forged tokens: what only the holder of a TSA key could produce — both verifiers must agree
# =================================================================================================


def _sign(key: Any, data: bytes, digest: str) -> bytes:
    if isinstance(key, rsa.RSAPrivateKey):
        return key.sign(data, padding.PKCS1v15(), HASHES[digest])
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return key.sign(data, ec.ECDSA(HASHES[digest]))


def resign(
    token: bytes,
    key: Any,
    edit: Callable[[Any], None],
    *,
    digest: str = "sha256",
    fix_digest: bool = True,
) -> bytes:
    """Change a token's SignedData with ``edit``, then sign it again with ``key``."""
    response = TimeStampResp.load(token)
    signed = response["time_stamp_token"]["content"]
    edit(signed)
    info = signed["signer_infos"][0]
    tst_der = signed["encap_content_info"]["content"].contents
    if info["signed_attrs"].native is None:
        to_sign = tst_der  # CMS without signed attributes signs the content itself
    else:
        if fix_digest:
            for attribute in info["signed_attrs"]:
                if attribute["type"].native == "message_digest":
                    attribute["values"] = [hashlib.new(digest, tst_der).digest()]
        to_sign = b"\x31" + info["signed_attrs"].dump(force=True)[1:]
    info["signature"] = _sign(key, to_sign, digest)
    return bytes(response.dump(force=True))


def tsa_key(tsa: MockTsa) -> Any:
    return serialization.load_pem_private_key((tsa.state_dir / "tsa.key").read_bytes(), None)


def _attribute(signed: Any, name: str) -> Any:
    return next(a for a in signed["signer_infos"][0]["signed_attrs"] if a["type"].native == name)


def test_forged_signed_attributes(tsa: MockTsa) -> None:
    token, key = stamp(tsa), tsa_key(tsa)
    assert both(tsa, resign(token, key, lambda signed: None)).ok  # re-signing alone is harmless

    def other_content_type(signed: Any) -> None:
        _attribute(signed, "content_type")["values"] = ["data"]

    def wrong_digest(signed: Any) -> None:
        _attribute(signed, "message_digest")["values"] = [b"\x00" * 32]

    def wrong_ess(signed: Any) -> None:
        certs = _attribute(signed, "signing_certificate_v2")["values"][0]["certs"]
        certs[0]["cert_hash"] = b"\x01" * 32

    def no_signed_attributes(signed: Any) -> None:
        signed["signer_infos"][0]["signed_attrs"] = None

    def not_tst_info(signed: Any) -> None:
        signed["encap_content_info"]["content_type"] = "data"

    def two_signers(signed: Any) -> None:
        signed["signer_infos"] = [signed["signer_infos"][0], signed["signer_infos"][0]]

    for edit, code, fix in (
        (other_content_type, "token-content-type", True),
        (wrong_digest, "token-message-digest", False),
        (wrong_ess, "token-ess", True),
        (no_signed_attributes, "token-signed-attrs", True),
        (not_tst_info, "token-format", True),
        (two_signers, "token-signers", True),
    ):
        result = both(tsa, resign(token, key, edit, fix_digest=fix))
        assert [error.code for error in result.errors] == [code], edit.__name__


def test_forged_but_valid_variants_are_accepted_by_both(tsa: MockTsa) -> None:
    """ESSCertID (v1, SHA-1), a SHA-512 message digest, millisecond accuracy: all valid."""
    key = tsa_key(tsa)
    certificate_der = tsa.tsa_certificate.public_bytes(DER)

    def ess_v1(signed: Any) -> None:
        sha1 = hashlib.sha1(certificate_der).digest()  # noqa: S324 - ESSCertID is SHA-1
        signed["signer_infos"][0]["signed_attrs"][2] = cms.CMSAttribute(
            {"type": "signing_certificate", "values": [{"certs": [{"cert_hash": sha1}]}]}
        )

    assert both(tsa, resign(stamp(tsa), key, ess_v1)).ok

    def sha512_digest(signed: Any) -> None:
        signed["signer_infos"][0]["digest_algorithm"] = {"algorithm": "sha512"}

    assert both(tsa, resign(stamp(tsa), key, sha512_digest, digest="sha512")).ok
    # the CMS digestAlgorithm decides the signature hash in both verifiers; a signature made
    # with the hash of the signature algorithm OID instead does not verify in either
    inconsistent = resign(stamp(tsa), key, sha512_digest, digest="sha512")
    response = TimeStampResp.load(inconsistent)
    info = response["time_stamp_token"]["content"]["signer_infos"][0]
    info["signature"] = _sign(key, b"\x31" + info["signed_attrs"].dump()[1:], "sha256")
    assert [e.code for e in both(tsa, response.dump(force=True)).errors] == ["token-signature"]

    def precise(signed: Any) -> None:
        tst = tsp.TSTInfo.load(signed["encap_content_info"]["content"].contents)
        tst["accuracy"] = {"seconds": 1, "millis": 500, "micros": 20}
        signed["encap_content_info"]["content"] = cms.ParsableOctetString(tst.dump(force=True))

    result = both(tsa, resign(stamp(tsa), key, precise))
    assert result.ok
    assert result.accuracy == "1 s 500 ms 20 µs"


def test_signer_identified_by_subject_key_identifier(tsa: MockTsa) -> None:
    key_type = "ec" if "ec" in tsa.state_dir.name else "rsa"
    certificate, key = signer_for(tsa, key_type, eku=[ExtendedKeyUsageOID.TIME_STAMPING])
    tsa.signer = (certificate, key)
    try:
        token = stamp(tsa)
    finally:
        tsa.signer = None
    ski = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest

    def by_key_identifier(signed: Any) -> None:
        signed["signer_infos"][0]["sid"] = cms.SignerIdentifier({"subject_key_identifier": ski})
        signed["signer_infos"][0]["version"] = "v3"

    result = both(tsa, resign(token, key, by_key_identifier))
    assert result.ok
    assert result.signer is not None
    assert result.signer.key_identifier() == ski


def test_duplicate_singleton_attributes_are_rejected(tsa: MockTsa) -> None:
    """RFC 5652 11.1/11.2: content-type and message-digest appear once. Here the standalone
    verifier is stricter than the core verifier (which reads the first instance)."""

    def duplicate(signed: Any) -> None:
        attributes = signed["signer_infos"][0]["signed_attrs"]
        attributes.append(attributes[0])

    token = resign(stamp(tsa), tsa_key(tsa), duplicate)
    result = sv.verify_timestamp(token, DATA, [standalone(tsa.root_certificate)])
    assert [error.code for error in result.errors] == ["token-format"]
    assert "appears twice" in result.errors[0].en


def test_unusual_responses(tsa: MockTsa) -> None:
    granted_without_token = TimeStampResp({"status": {"status": "granted"}}).dump()
    assert [e.code for e in both(tsa, granted_without_token).errors] == ["token-format"]
    not_signed_data = cms.ContentInfo({"content_type": "data", "content": b"x"}).dump()
    assert [e.code for e in both(tsa, not_signed_data).errors] == ["token-format"]
    md5_request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {
                "hash_algorithm": {"algorithm": "md5"},
                "hashed_message": b"\x00" * 16,
            },
            "cert_req": True,
        }
    ).dump()
    assert "algorithm" in [e.code for e in both(tsa, tsa.respond(md5_request)).errors]


def test_more_key_and_algorithm_corner_cases() -> None:
    ed25519 = Ed25519PrivateKey.generate()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name("ed"))
        .issuer_name(_name("ed"))
        .public_key(ed25519.public_key())
        .serial_number(1)
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 1, 1, tzinfo=UTC))
        .sign(ed25519, None)
    )
    parsed = standalone(certificate)
    assert parsed.key is None
    assert "unsupported key" in parsed.key_description
    with pytest.raises(sv.CheckError):  # Ed25519 is not used by time-stamp tokens
        sv.verify_signature(parsed.key, parsed.algorithm, parsed.tbs, parsed.signature)
    other_mgf = sv.encode(0x30, sv.encode(0xA1, sv.encode(0x30, sv.encode_oid("1.2.3.4"))))
    pss = sv.Algorithm(sv.RSASSA_PSS, sv.parse_single(other_mgf))
    with pytest.raises(sv.CheckError, match="MGF"):
        sv.verify_signature(sv.RsaKey(3, 3), pss, b"", b"")


# =================================================================================================
# Strictness added after review: DER, ECDSA encodings, weak hashes, key usage and path length
# =================================================================================================


def test_der_is_strict_where_encodings_could_differ() -> None:
    with pytest.raises(sv.DerError, match="constructed"):
        sv.to_int(sv.parse_single(b"\x22\x03\x02\x01\x05"))  # a "constructed INTEGER"
    with pytest.raises(sv.DerError, match="constructed"):
        sv.to_oid(sv.parse_single(b"\x26\x03\x06\x01\x2a"))
    with pytest.raises(sv.DerError, match="constructed"):
        sv.to_time(sv.parse_single(b"\x38\x00"))  # a constructed GeneralizedTime
    for padded in (b"\x02\x02\x00\x05", b"\x02\x02\xff\x85"):
        with pytest.raises(sv.DerError, match="minimally"):
            sv.to_int(sv.parse_single(padded))
    assert sv.to_int(sv.parse_single(b"\x02\x02\x00\x85")) == 0x85  # the zero is needed here
    assert sv.to_int(sv.parse_single(b"\x02\x02\xff\x05")) == -251
    nested = b"\x04\x01A"
    for _ in range(sv.MAX_DEPTH + 1):
        nested = b"\x24" + sv.encode_length(len(nested)) + nested
    with pytest.raises(sv.DerError, match="too deep"):
        sv.to_octets(sv.parse_single(nested))


def test_ecdsa_accepts_only_the_der_encoding_of_a_signature() -> None:
    """The signature value is not signed itself: a re-encoded (r, s) must not verify, as in
    OpenSSL and ``cryptography`` (one of the review's differential cases)."""
    key = ec.generate_private_key(ec.SECP521R1())
    public = standalone(make_certificate("k", key)).key
    assert isinstance(public, sv.EcKey)
    signature = key.sign(DATA, ec.ECDSA(hashes.SHA512()))
    assert sv.ecdsa_verify(public, "sha512", DATA, signature)
    r_node, s_node = sv.seq(sv.parse_single(signature), "sig", 2)
    body = r_node.raw + s_node.raw
    variants = {
        "constructed INTEGER": sv.encode(0x30, bytes([0x22]) + r_node.raw[1:] + s_node.raw),
        "long-form length": b"\x30\x82" + len(body).to_bytes(2, "big") + body,
        "padded INTEGER": sv.encode(0x30, sv.encode(0x02, b"\x00" + r_node.value) + s_node.raw),
        "extra element": sv.encode(0x30, body + b"\x05\x00"),
    }
    for name, variant in variants.items():
        assert not sv.ecdsa_verify(public, "sha512", DATA, variant), name


def test_weak_digests_are_refused(tsa: MockTsa) -> None:
    """ARCILOGGER stamps with SHA-256 or SHA-512; a SHA-1 imprint or signature is a failure in
    the standalone verifier and in the core verifier (see the agreement tests)."""
    roots = [standalone(tsa.root_certificate)]
    request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {
                "hash_algorithm": {"algorithm": "sha1"},
                "hashed_message": hashlib.sha1(DATA).digest(),  # noqa: S324 - the weak case
            },
            "cert_req": True,
        }
    ).dump()
    result = sv.verify_timestamp(tsa.respond(request), DATA, roots)
    assert [error.code for error in result.errors] == ["token-weak-digest"]
    assert "messageImprint" in result.errors[0].tr

    def sha1_signature(signed: Any) -> None:
        signed["signer_infos"][0]["digest_algorithm"] = {"algorithm": "sha1"}

    token = resign(stamp(tsa), tsa_key(tsa), sha1_signature, digest="sha1")
    result = sv.verify_timestamp(token, DATA, roots)
    assert [error.code for error in result.errors] == ["token-weak-digest"]
    assert "signerInfo" in result.errors[0].tr


def _ca(
    subject: str,
    key: PrivateKey,
    issuer: tuple[x509.Certificate, PrivateKey] | None = None,
    *,
    path_length: int | None = None,
    cert_sign: bool = True,
) -> x509.Certificate:
    start = datetime.now(UTC) - timedelta(days=1)
    usage = x509.KeyUsage(
        digital_signature=not cert_sign,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=cert_sign,
        crl_sign=cert_sign,
        encipher_only=False,
        decipher_only=False,
    )
    return (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer[0].subject if issuer else _name(subject))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), True)
        .add_extension(usage, True)
        .sign(issuer[1] if issuer else key, hashes.SHA256())
    )


def test_issuers_must_be_allowed_to_sign_certificates_and_respect_path_length() -> None:
    now = datetime.now(UTC)
    root_key_ = ec.generate_private_key(ec.SECP256R1())
    middle_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    for root_limit, middle_sign, expected in (
        (1, True, None),
        (None, True, None),
        (0, True, "token-path-length"),  # the root allows no intermediate CA below it
        (1, False, "token-key-usage"),  # the intermediate may not sign certificates
    ):
        root = _ca("Root", root_key_, path_length=root_limit)
        middle = _ca("Intermediate", middle_key, (root, root_key_), cert_sign=middle_sign)
        leaf = make_certificate("Leaf", leaf_key, (middle, middle_key), ca=False)
        chain, error = sv.build_chain(
            standalone(leaf), [standalone(middle)], [standalone(root)], now
        )
        assert (error.code if error else None) == expected, (root_limit, middle_sign)
        if expected is None:
            assert len(chain) == 3
    assert standalone(_ca("x", root_key_, path_length=2)).path_length() == 2
    assert standalone(make_certificate("y", leaf_key)).path_length() is None


def test_the_tsa_certificate_must_be_allowed_to_sign() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    assert sv._check_key_usage(standalone(make_certificate("no extension", key))) == []
    signer = standalone(_ca("signs", key, cert_sign=False))  # digitalSignature
    assert signer.key_usage() == {sv.KU_DIGITAL_SIGNATURE}
    assert sv._check_key_usage(signer) == []
    cert_signer = standalone(_ca("cert signer only", key))  # keyCertSign + cRLSign
    assert [e.code for e in sv._check_key_usage(cert_signer)] == ["token-key-usage"]


def test_times_written_by_arcilogger_are_read_with_their_offset() -> None:
    moment = sv._parse_time("2026-09-13T00:30:00.000+03:00")
    assert moment == datetime(2026, 9, 12, 21, 30, tzinfo=UTC)
    assert sv._parse_time("2026-09-13T00:30:00") is None  # no offset: not comparable
    assert sv._parse_time("yesterday") is None
    assert sv._parse_time(None) is None


# =================================================================================================
# One verdict: tokens the standalone verifier refuses are refused at sealing time too
# =================================================================================================


def _tsa_certificate(
    key: PrivateKey,
    issuer: tuple[x509.Certificate, PrivateKey],
    *,
    key_encipherment_only: bool = False,
) -> x509.Certificate:
    start = datetime.now(UTC) - timedelta(days=1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name("Parity TSA"))
        .issuer_name(issuer[0].subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), True)
    )
    if key_encipherment_only:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
    return builder.sign(issuer[1], hashes.SHA256())


def _parity_case(case: str, tmp_path: Path) -> tuple[bytes, list[x509.Certificate]]:
    """A token for ``case`` and the roots to check it against."""
    tsa = MockTsa(tmp_path / case, key_type="ec")
    key = ec.generate_private_key(ec.SECP256R1())
    if case in ("valid", "tsa-key-encipherment", "ess-issuer-serial", "sha1-signature"):
        certificate = _tsa_certificate(
            key,
            (tsa.root_certificate, root_key(tsa)),
            key_encipherment_only=case == "tsa-key-encipherment",
        )
        tsa.signer = (certificate, key)
        token = stamp(tsa)
        if case == "ess-issuer-serial":

            def wrong_serial(signed: Any) -> None:
                issuer = asn1_x509.Certificate.load(certificate.public_bytes(DER)).issuer
                ess = _attribute(signed, "signing_certificate_v2")["values"][0]["certs"][0]
                ess["issuer_serial"] = {
                    "issuer": [asn1_x509.GeneralName({"directory_name": issuer})],
                    "serial_number": certificate.serial_number + 1,
                }

            token = resign(token, key, wrong_serial)
        elif case == "sha1-signature":

            def sha1_signature(signed: Any) -> None:
                signed["signer_infos"][0]["digest_algorithm"] = {"algorithm": "sha1"}

            token = resign(token, key, sha1_signature, digest="sha1")
        return token, [tsa.root_certificate]
    # Through an intermediate CA: the root allows none (pathLen 0), or it may not sign.
    root_key_ = ec.generate_private_key(ec.SECP256R1())
    middle_key = ec.generate_private_key(ec.SECP256R1())
    root = _ca("Parity Root", root_key_, path_length=0 if case == "path-length" else None)
    middle = _ca(
        "Parity Intermediate",
        middle_key,
        (root, root_key_),
        cert_sign=case != "intermediate-cannot-sign",
    )
    tsa.signer = (_tsa_certificate(key, (middle, middle_key)), key)
    middle_asn1 = asn1_x509.Certificate.load(middle.public_bytes(DER))

    def with_intermediate(signed: Any) -> None:
        signed["certificates"] = [*signed["certificates"], middle_asn1]

    return resign(stamp(tsa), key, with_intermediate), [root]


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("valid", None),
        ("tsa-key-encipherment", "token-key-usage"),
        ("path-length", "token-path-length"),
        ("intermediate-cannot-sign", "token-key-usage"),
        ("ess-issuer-serial", "token-ess"),
        ("sha1-signature", "token-weak-digest"),
    ],
)
def test_core_and_standalone_verifiers_agree(case: str, code: str | None, tmp_path: Path) -> None:
    token, roots = _parity_case(case, tmp_path)
    core = verify_token(token, roots=roots, digest=hashlib.sha256(DATA).digest())
    mine = sv.verify_timestamp(token, DATA, [standalone(root) for root in roots])
    assert core.ok == mine.ok == (code is None), (case, core.errors, mine.errors)
    assert [error.code for error in mine.errors] == ([code] if code else [])
    if code is not None:
        assert len(core.errors) == 1, core.errors


def test_duplicate_signed_attributes_are_refused_by_both(tsa: MockTsa) -> None:
    def twice(signed: Any) -> None:
        attributes = signed["signer_infos"][0]["signed_attrs"]
        signed["signer_infos"][0]["signed_attrs"] = [*attributes, attributes[0]]

    token = resign(stamp(tsa), tsa_key(tsa), twice)
    core = verify_token(token, roots=[tsa.root_certificate], digest=hashlib.sha256(DATA).digest())
    mine = sv.verify_timestamp(token, DATA, [standalone(tsa.root_certificate)])
    assert not core.ok
    assert not mine.ok
    assert any("appears twice" in error for error in core.errors)


def test_a_weak_imprint_is_refused_by_both(tsa: MockTsa) -> None:
    weak = hashlib.sha1(DATA).digest()  # noqa: S324 - the weak case
    request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {"hash_algorithm": {"algorithm": "sha1"}, "hashed_message": weak},
            "cert_req": True,
        }
    ).dump()
    core = verify_token(tsa.respond(request), roots=[tsa.root_certificate], digest=weak)
    assert not core.ok
    assert any("weak digest algorithm sha1" in error for error in core.errors)
